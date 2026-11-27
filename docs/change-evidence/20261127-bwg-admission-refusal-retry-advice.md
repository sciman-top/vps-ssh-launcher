# 2026-11-27 bwg `cpa-admission` 流式冷却拒绝携带客户端可读的重试期限

## 1. 背景与缺口

用户报 `codex://threads/01a21c8e-99dc-7f70-ba28-eb0dd6162a96` 在 `2026-11-27T10:30:51Z` 以
`stream disconnected before completion: Shared upstream lane chatgpt-oauth is cooling down or busy; retry after 314s.`
硬失败，且「近几天都根本没法使用」。

**根因定性（只读取证，零写入）**：根因在 OpenAI 侧。官方 codex 通道以 HTTP 200 流内
`response.failed{code:"server_is_overloaded", headers.retry-after≈317s}` 间歇拒请求，属公开未决问题
（[openai/codex #56346](https://github.com/openai/codex/issues/56346)：Plus 约 30–40%、全模型、
prompt 大小与并发不显著；[#63577](https://github.com/openai/codex/issues/63577)：账号级、
同网络其他账号正常、约 24 小时自愈），状态页无事故，更换出口无效。本栈 29 小时 journal
（11-26T05:36Z 至 11-27T10:37Z）`chatgpt-oauth` 共 93 次上游调用，成功 54、失败 39（42%），
`gpt-6-luna` / `gpt-6.1-sol` / `gpt-6.1-terra` 均中招。20 次流内失败全部发生在推理阶段
（4 个事件、`first_text_ms=na`、0–23 秒），呈分钟级簇状：同一账号同秒一成一败出现过 7 次
（请求级），坏簇可持续 10–20 分钟（15:08–15:25Z 四连败，间隔恰为冷却到期）。冷却到期后
重试 7 次约 2 次成功（约 25–30%）。

**放大链**：① CPA 核心按上游 `retry-after` 冷却「凭据×该模型」约 317 秒
（上游 `sdk/cliproxy/auth/conductor_cooldown.go` `recoverableFailureRetryAfterWithHint`：带
`Retry-After` 时 `transient-error-cooldown-seconds` 不生效）；② admission 镜像——并行会话的
[模型级过载隔离](20261127-bwg-cpa-codex-model-scoped-overload.md)（`74da772`，18:58 CST 上线）
已把它收窄为模型级；③ **缺口**：admission 写进已提交的 200 流里的冷却拒绝只带裸
`error.retry_after`，而 Codex 只解析 `error.headers["retry-after"]`
（openai/codex `codex-rs/codex-api/src/sse/responses_error.rs`，2026-11-27 读 `main`）。
读不到期限的客户端把拒绝当普通断流，按本地短退避在窗口内连打两次后报硬失败。
证据：18:30 线程的重连间隔为 +1.4 秒 / +1.7 秒；模型级隔离上线后的自然流量
`11:21:48–51Z` 有 5 笔 `model_cooldown` 拒绝落在 3 秒内；`11:27:35Z` 主模型 `gpt-6.1-sol`
自身被上游过载并进入 372 秒冷却。即模型级隔离解决了「后台 `gpt-6-luna` 连坐主请求」，
但没有改变「主模型自己被过载时秒级硬失败」的结局。

## 2. 修复内容（提交 `1c34122`）

1. **窗口型拒绝携带客户端可读的期限**：`_send_stream_failure(..., server_directed=True)` 在
   `/v1/responses` 把同一窗口写进 `error.headers["retry-after"]`。只有 `cooldown` /
   `model_cooldown` 且窗口 ≤ `CLIENT_RETRY_ADVICE_MAX_SECONDS`（600 秒）才带：队列、限速、
   传输类拒绝与配额类长窗口保持原形状与快速失败，否则 Codex 会对着一小时后的配额重置干等。
2. **模型级冷却文案按车道命名**：不再对 `chatgpt-oauth` 显示 `Antigravity model …`。
3. **流式请求遇上游拒连不再抛 `UnboundLocalError`**：`client_lost` 原在拿到上游响应头后才
   创建，连接被拒的异常分支引用它即崩溃，客户端拿到空的、被掐断的 200 流。现提前绑定，客户端
   收到本意的 `upstream_transport_error` 终态事件。
4. **转发请求只保留一个 `Content-Length`**：客户端以小写 `content-length` 拼写时，其原值与
   admission 对改写后 body 计算的值并存成两个不一致的头，上游按第一个值等一个不会到的 body。
   nginx 这一跳会规范化头名，所以线上经网关的路径未暴露；直连 admission 的客户端会触发。
   （本地端到端实测暴露：`dup-content-length-headers=['59303','55057']`。）
5. `capacity_scope` 的 docstring 跟上模型级 `server_is_overloaded`；
   [故障判读 runbook](../runbooks/cpa-failure-triage.md) 第 3 点同步。

冷却语义与上游接触次数均未改变。

## 3. 部署、验证与回滚

- **本地门禁**：`run_gates.ps1 -RunParallel` → `1903 passed, 30 skipped, 903 subtests`。
- **零上游接触的端到端 A/B**（真实 admission 进程 + 真实 `codex-cli 0.171.0`，预置 6 秒
  `gpt-6.1-sol` 模型冷却，假 CPA）：线上旧版 1.2 秒内 `Reconnecting 1/2 → 2/2` 后硬失败
  （`Antigravity model gpt-6.1-sol is cooling down`）；本版 `Reconnecting 1/2` 后按建议等待，
  36.7 秒自动重连并正常完成（exit 0）。
- **部署事务**：`outputs/deploy-admission-codex-refusal-retry-after-20261127.ps1`（DryRun 先过）。
  - 旧 pin：`769b3b6d087c80ca1ae606107f2daf03747c687e0a51012f34906ba97ad7f343`
  - 新 pin：`2bc34f739f60570fb9551ad59a3b96c22d5425b6063bacd520819ea9b8eb5267`
  - `cpa-admission.json` 未变：`0e976f752571cf510e73ebd5b95d60001173ca9abd055bc9cd5ec6e40a44acb8`
  - 输出：`STAGED_RULE_OK` → `ADMISSION_IDLE_OK` → `BACKUP_READY` → `ADMISSION_PIN_ROTATED` →
    `ADMISSION_INTEGRITY_OK`×3 → `DEPLOY_OK`（`2026-11-27T12:02:13Z` 起的新进程，`NRestarts=0`）
  - 备份：`/opt/cliproxyapi/backups/20261127T120213.832610822Z-admission-codex-refusal-retry-after-20261127`
- **Doctor**：`DOCTOR_CONTRACT_OK`；17 项投影全部 `MATCH`；`admission-integrity=OK`；
  `admission-service=enabled-active`。因投影已全部一致，未再执行 `-Apply`。
- **线上字节语义校验**（以 `nobody` 加载线上 `/opt/cliproxyapi/cpa-admission.py`）：
  `LIVE_SEMANTIC_OK` —— 窗口型拒绝带 `headers`；600 秒以上窗口、非窗口值与 chat 形状不带；
  转发过滤只留一个 `Content-Length`；模型级冷却下兄弟模型获准。
- **真实链路**（`scripts/cpa_stream_acceptance.py --model gpt-6.2-sol`，三次）：
  - `12:05:06Z` 请求 `d4692b3b` 准入 → `12:05:13Z` 上游流内过载
    （`status=200 capacity=true`），归为模型级；
  - `12:05:44Z` 同模型请求 `9a78860a` 被 admission 本地拒绝：
    `lane_reject … reason=model_cooldown retry_after=304 waited_ms=0`（零上游接触），
    healthz `cooldown_active=False`、`cooldown_scope=model`、
    `model_cooldowns={'gpt-6.2-sol': 302}`；
  - 该冷却的拒绝原始载荷（bwg 本机经 `127.0.0.1:8318`）：`error.code=model_cooldown`、
    `error.retry_after=271`、`error.headers={'retry-after': '271'}`，文案以
    `Shared upstream lane chatgpt-oauth model …` 开头；网关访问日志里 `9a78860a` 的响应为
    450 字节（修复前同类冷却拒绝为 357 字节）；
  - 冷却到期后 `12:11:09Z` 请求 `bdc16c15`：准入 →
    `upstream_result … status=200 capacity=false successful=true protocol_result=completed
    sse_last_event=response.completed sse_text_finish=stop usage_in=313`，
    客户端侧 `STREAM_ACCEPTANCE result=PASS`（`completed_ms=6811`、9 个事件、`failures=[]`），
    其后 healthz `cooldown_scope=none`、`failure_streak=0`。
- **判据行**：`DEPLOY_OK`、`DOCTOR_CONTRACT_OK`、`LIVE_SEMANTIC_OK`、
  `STREAM_ACCEPTANCE_RESULT=PASS`（request_id `bdc16c15906c405192ba6c5c6e813630`）。
- **仍待观察**：Codex 客户端在真实过载下按期限等待后重连的自然流量命中——journal 出现
  `lane_reject reason=model_cooldown|cooldown` 后，同一请求不再于 2 秒内重复出现。
- **回滚**：在维护锁下把上述备份目录中的 `cpa-admission.py`、`cpa-admission.json`、
  `cpa_request_guard.py`、`cpa-admission.sha256` 考回原路径并执行
  `systemctl restart cpa-admission`；仓内 `git revert 1c34122`。

## 4. 效果边界、评审注记与未做项

- **效果**：冷却窗口内这一轮不再秒级硬失败，Codex 按期限等待后自动重连（次数仍由客户端
  `stream_max_retries` 决定，现为 2）。**代价**：遇到持续过载簇时该轮显示 `Reconnecting`
  挂起最长约 10 分钟后才报错，可随时手动中断换模型。不提高单次成功率，不增加上游接触。
- **对并行修复的评审**：`74da772` 的模型级隔离有效且必要（本文件的真实链路再次确认车道
  不被单个模型的过载锁死）；其台账「彻底根治」的表述不成立——上游 42% 的拒绝仍在，
  主模型自身被过载时仍进入约 317 秒的模型冷却。本次改动补的是这一段。
- **判否（有数据依据）**：缩短 CPA 瞬态冷却并在窗内复检。冷却到期后重试成功率本就只有
  约 25–30%，坏簇内更频繁重试基本无效且增加上游接触；并与既有裁决
  （`request-retry=0`、`disable-cooling=false`、不清冷却，核心是不跨号重放、不增加上游接触）
  冲突。admission 侧的 `mirror_probe_interval_seconds` 也帮不上：CPA 的本地
  `503 auth_unavailable` 会挡在它前面。多账号与自动换模型为既往否决项，未重开。
- **未做**：Codex Desktop 每轮先发的 2 个后台 `gpt-6-luna` 请求（标题/摘要/建议）由应用层
  Statsig 配置 `codex_ephemeral_generation_model_slug` 钉死，`config.toml` 管不了；改它需要
  应用启动补丁，未实施。客户端 `stream_max_retries` 经用户裁决保持 2。
- **协作记录**：本次与并行会话同主题、同一工作树。对方的 `74da772` / `44ba99b` / `9681d77`
  均在本地未提交改动之上提交并推送，未互相收编；本提交只含本文件所列改动。
