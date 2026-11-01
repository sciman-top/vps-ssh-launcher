# BWG CPA 多 Provider 封号/限流/降智 + 吞吐/缓存深度审查收口（2026-11-01）

## 目标与结论先行

用户要求对 bwg 上 CPA（CLIProxyAPI）的多 OAuth/API-key 渠道做封号、限流、
降智风险审查，并优化"吐字慢"与缓存命中；必要时改代码。

**结论**：现役风控姿态已是逐项正确的最小化配置，本轮**未发现需要改动运行
配置或 admission/doctor 代码的缺陷**；缓存命中率接近理论上限，延迟尾部由
“单账号容量 + 逐凭据并发闸 + 上游生成时间”构成，均属安全机制的固有代价而
非可安全消除的 bug。唯一实质改进是上游 `v8.0.49` 的 Antigravity/Gemini
质量与性能修复，但尚在 72h 成熟期外、且 bwg 是插件宿主须走手工联合 canary，
本轮**不强行升级**。本文按证据层记录，不替代 live 验收。

## 证据层

- `repo_verified`：`scripts/remote/cpa-admission.py`、`cpa-guardrails-*`、
  `cpa-admission.json`（四 lane）、`cpa_policy.py`、runbook 台账。
- `host_loaded`：fresh strict doctor `DOCTOR_CONTRACT_OK`；容器
  `eceasy/cli-proxy-api:v8.0.48@sha256:c198886a…`；`config-version: 8`。
- `controlled_live`：24h admission journal 聚合（仅计数，无正文/凭据）。
- `reference_only`：上游 checkout `D:\CODE\external\...\CLIProxyAPI`
  `dbb70621..250d5981`（只读核对，未改）。

## 现场基线（fresh，24h）

策略（`host_loaded`，config-version 8）：

- `routing.retry.request-retry=0`、`max-retry-credentials=0`
- `routing.cooldown.transient-error-cooldown-seconds=30`、`save-cooldown-status=false`
- `routing.strategy=fill-first`；`session-affinity=true`/`1h`/`rebind-on=quota`/`subagents=false`
- `upstream` codex：`stream-bootstrap-buffering=false`、`stream-bootstrap-timeout=0`、`model-level-cooling=true`
- admission per-lane `requests_per_minute`：chatgpt=24、zhipu=30、antigravity=18、workbuddy=18
- 凭据：chatgpt **1**、antigravity 2、workbuddy 3（1 CN `eb624ce3` + 2 INTL `0faaff85`/`ca64800b`）、claude 0（退役墓碑 inert）

24h 事件（`controlled_live`）：

- `upstream_results≈5257`；`admission_lane_rejects≈35`
  （`rate_limited:10`、`operator_review_required:8`、`downstream_gone:6`、
  `stale_write_ahead_cooldown:6`、`queue_timeout:4`、`busy:1`）。
- 容量事件 14 条，**全部在 chatgpt-oauth**（gpt-6-luna 7、sol 3、terra 2、
  5.6-luna 2），表现为 HTTP200 流内 `response.failed`/`capacity` 或个别
  429/503/502；`admission_upstream_429_503_details` 仅 2 条硬 429/503。
- workbuddy：04:46 一次 admission 重启后的 04:48–04:52 出现 4 次瞬时
  `status=401 upstream_error`（`upstream_header_ms≈1–6ms`，即时拒绝），
  04:52:13 自行恢复 200；**未 `lane_hold_latched`**，doctor 现 `workbuddy=OPEN`。
  这是单凭据转瞬认证抖动，未污染整 lane（符合“单个 401 不 park 全 lane”语义）。
- `cooldown_state=none`；三活跃 OAuth lane `OPEN`；claude `RETIRED/HELD(inert)`。

## 延迟“吐字慢”分解（24h，protocol_result=completed）

| lane | n | admission waited p50/p90/p99 | upstream_header(≈TTFT) p50/p90 |
|---|---|---|---|
| chatgpt-oauth | 2219 | 2 / 7274 / 49835 ms（max 105437） | 1154 / 2092 ms |
| workbuddy-oauth | 2659 | 672 / 9270 / 25473 ms | 3169 / 6398 ms |
| antigravity-oauth | 12 | 2 / 3 / 3 ms | 5226 / 8869 ms |
| zhipu-coding-plan | 69 | 0 / 0 / 0 ms | 5790 / 7917 ms |
| passthrough | 170 | 0 / 0 / 0 ms | 4608 / 8123 ms |

解读：

- **chatgpt/workbuddy 的慢是排队**（`waited_ms` 尾部 7–105s），provider 本身
  TTFT 很快（1–3s）。根因是“单账号/单凭据 + `max_inflight`=2/1 + per-lane
  RPM”这一组安全闸在突发时串行化；56 个 chatgpt 客户端里 13.5% 等待 >5s，
  属群体突发而非单个坏客户端。
- **antigravity/zhipu/passthrough 的慢是上游生成时间**（TTFT 5–8s，队列≈0）。
- 传输层已最优：入口 `http2 on`、gateway `proxy_buffering off`、`sendfile on`、
  `stream-bootstrap-buffering=false`；不存在本地人为缓冲。

**为何不改并发/队列**：`workbuddy account_max_concurrent=1`（上游 #60：并行大上下文
是模型级限流最快路径）、chatgpt 单账号 `max_inflight=2` 都是为了压账号级风险；
提高会直接抬高容量事件与封号画像。`credits` 粘性虽让 WorkBuddy 压在单一热凭据
上（另 2 凭据闲置），但这正是保缓存局部性与“不把压力搬到所有账号”的设计
（2026-10-25 教训）。故延迟尾部是**安全换来的**，本轮不动。

## 缓存命中率（24h，token 遥测，观测口径）

| lane/model | reports | hit_ratio |
|---|---|---|
| workbuddy/deepseek-v4.1-flash | 1401 | **0.991** |
| chatgpt/gpt-6-luna | 945 | 0.973 |
| chatgpt/gpt-6.1-sol | 276 | 0.967 |
| chatgpt/gpt-6.1-terra | 56 | 0.922 |
| chatgpt/gpt-5.6-luna | 355 | 0.853 |
| zhipu/glm-5.3-flash | 25 | 0.972 |
| antigravity/gemini-3.8-flash-high | 12 | 0.893 |

缓存复用已接近上限，主机制就是 `session-affinity`；`support-prompt-cache-key`
各 provider 维持 `false`（OAuth lane 无 canary，非 OAuth 亦无缺口）。**缓存无需优化**；
个别 <0.95 的项是会话短/上游计数口径差异，非配置缺陷。

## 逐 Provider 风险矩阵

| Lane | 账号/渠道 | 封号风险 | 限流风险 | 降智风险 | 现状控制 |
|---|---|---|---|---|---|
| chatgpt-oauth | 1×Plus OAuth | 高（消费级 OAuth 条款；单账号 5 别名） | 高（24h 14 容量事件全在此） | 中（model-level-cooling 已按模型隔离） | 单账号集中是**最大风险面**；inflight=2/RPM24/冷却阶梯/Retry-After 吸收 |
| antigravity-oauth | 2×OAuth | 高（官方 FAQ 明确第三方违规） | 中 | 中（`gemini-3.8-flash-n <== -high` 名称变体 67 次/7d） | inflight2/RPM18/strict_backoff；`v8.0.49` 有 Gemini 质量+性能修复 |
| workbuddy-oauth | 3（1CN+2INTL） | 高（逆向插件 AUP/ToS 限制） | 中（6004 模型级、14003 出口画像） | 中（6004 模型级冷却、credits 粘性） | inflight1/RPM18/account_max_concurrent=1/identity_switch_off |
| zhipu-coding-plan | API key（GLM Coding Plan） | 中（付费正版） | 低-中 | 低 | RPM30/inflight3 |
| claude-oauth | 0（退役） | — | — | — | 墓碑 inert；不复活 |
| API-key/OpenCode 兜底 | 付费/独立 | 低 | 低 | 低 | 混合回退（`max-retry-credentials=0`，`request-retry=0`） |

**核心风险集中项**：ChatGPT 单账号承载 5 个模型别名 + 24h 全部 14 次容量事件。
缓解已在位（model-level-cooling、per-model 冷却、single-credential 不扩散）；
进一步降低需要**加账号容量**，属用户决策，agent 不擅自新增/迁移账号。

## 上游 v8.0.49 评估（reference_only）

- 当前运行 `v8.0.48`（digest 锁定）；上游 `v8.0.49` tag=`2026-10-31 21:09 CST`，
  **<72h 成熟期**，updater 于 `2026-11-01T04:04Z` 显示 `target=v8.0.48`、
  “no newer mature release”。
- `v8.0.49` 含与本审查直接相关的修复：
  `4c46f0aa`/`f42f5871` 剥离 `systemInstruction.parts` 空 `{}`（防上游 protobuf
  校验拒绝/回落，利于 Gemini 请求正确性）、`234217d0` 每轮丢弃多余
  `functionResponse`、`250d5981` Antigravity `selectGroupResponses` O(N+M)
  去重（性能/延迟）、`a8dbebab` pluginhost 允许注册多 OAuth 登录项（插件 ABI）。
- **部署路径**：bwg 是 WorkBuddy 原生插件宿主，updater 遇新候选一律
  `exit 77 / deferred / PLUGIN_CANARY_REQUIRED`，必须走**手工联合 canary**
  （钉 CPA digest+插件 SHA、隔离加载、drain 事务、重启后断言插件注册行、
  lane-hold 差分）。**成熟后**（≈2026-11-03 21:00 CST 之后）再按该流程评估；
  本轮不升级、不改 digest。

## 本轮动作与验证

- 运行配置：**未改**（无缺陷、无授权范围内的安全增益）。
- admission/doctor 代码：**未改**（现有 401 不污染整 lane、容量按 model 隔离、
  per-lane RPM+队列+冷却语义均经 24h 自然流量与既有单测覆盖）。
- 仓内验证（simulated）：`tests/test_cpa_policy_runtime.py`、
  `tests/test_cpa_guardrails_script.py` 在 `v8.0.48` 现状下
  `62 passed, 59 subtests passed`（含 admission rate-guard 的
  真实 Handler/Proxy/Server + 假上游端到端）。
- fresh strict doctor（`host_loaded`）：`DOCTOR_CONTRACT_OK`；`cooldown_state=none`；
  `admission-integrity=OK`；三 OAuth lane `OPEN hold_reason=none`；
  `==projection-drift==` 全部 11 个运行产物 + `cpa-admission.json` 逐项 `MATCH`
  （repo == host，无待投影项）。
- 受控实战（`controlled_live`）：一次 loopback（`127.0.0.1:8318` `/v1/responses`，
  `deepseek-v4.1-flash`，`stream:true`，无重试；client key 仅在远端进程内从
  `config.yaml access.api-keys[0]` 读取，未回显）→ `PROBE_STATUS=200`、
  首个事件 1087 ms、10 个事件、`response.completed=true`、文本匹配 `pong`、
  `RESULT=PASS`。
- 参考仓 `CLIProxyAPI` 仅 `reference_only` 只读核对（fetch/log/show，无写/无 build）。

## 回滚与边界

- 本轮无远端**配置/代码**写入（仅文档 + 一次只读单发 SSE 探测），无回滚对象；
  若后续单独执行 `v8.0.49` canary，按 updater/
  `outputs/cpa-guarded-restart-*` 的备份优先+drain 差分路径，Git 回滚不替代远端恢复。
- 边界：本文是 repo/projection/host_loaded + 24h 自然流量观测；**不证明**
  上游配额、长期 OAuth 账号安全、China 网络自然验收。无本地阈值可保证不封号；
  不得以换号/换 IP/清冷却/连点重试/并发压测去验证风控边界。
