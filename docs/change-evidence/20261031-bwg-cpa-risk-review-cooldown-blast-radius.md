# BWG CPA 全面风险审查 + 冷却/爆炸半径收尾（2026-10-31）

## 目标与证据分层

用户要求对 bwg VPS 上 CPA 的多账号（OpenAI/GPT OAuth、Antigravity/Gemini OAuth、
WorkBuddy CN/INTL、Claude/Anthropic OAuth）与多 API key 渠道做封号/限流/降智
风险审查，并优化吐字慢与缓存命中；必要时修代码。本轮证据层级：

- `repo_verified`：`scripts/remote/cpa_policy.py`、`cpa-guardrail-apply.sh`、
  `cpa-guardrail-doctor.sh`、`cpa-admission.py/json`、路由清单与 runbook。
- `host_loaded`：strict doctor `DOCTOR_CONTRACT_OK`、实时 `config.yaml`、
  `cpa-admission` healthz、容器 `v8.0.48`。
- `controlled_live`：24h 自然流量审计（`cpa_natural_traffic_audit.py`，
  聚合口径，无 prompt/凭据）。
- `reference_only`：上游 `CLIProxyAPI` checkout `dbb70621`（源码核对，未改）。

## 现场读数（审查基线）

- 容器 `eceasy/cli-proxy-api:v8.0.48`，doctor 契约全绿，3 条 OAuth lane OPEN，
  `claude_lane=RETIRED`（0 凭据，历史退役）。
- `routing.retry.request-retry=0`、`max-retry-credentials=0`、
  `session-affinity=true/1h/rebind-on=quota/subagents=false`、
  `stream-bootstrap-buffering=false`；`fill-first`。
- 24h 自然流量：`chatgpt-oauth/gpt-6-luna` n=1020 peak_60s=18、
  `workbuddy-oauth/deepseek-v4.1-flash` n=2114 peak_60s=17；两者均贴着上游
  ~13–19/min 的社区画像。header p50：antigravity 3.4s、workbuddy 3.5s、
  glm 7.0s、codex luna 1.1s。`tiny_sse_completed=0`。
- 24h 容量事件 30（chatgpt 19 / workbuddy 11）；codex 单凭据，`server_is_overloaded`
  + `Retry-After` 6–323s 会整段冷却成 `503 auth_unavailable`。
- 缓存：`session-affinity` 本身即缓存复用主机制；`support-prompt-cache-key`
  各 provider 保持 `false`（非 OAuth 实测 ≥0.95，OAuth 按纪律禁止 canary）。

## 本轮变更（两处，最小可逆）

1. `routing.cooldown.transient-error-cooldown-seconds`：`60 → 30`。
   Codex 单凭据把一次瞬时 overload 拖成整段零出网 `503`；上游 `Retry-After`
   短至 6s 时常被 60s 下限拖长。30s 保留真实冷却，显式 `Retry-After` 仍优先。
   这是请求放大/扩散旋钮，不清配额冷却、不轮换凭据。
2. `upstream.codex.model-level-cooling`：`true`（源码
   `CodexConfig.ModelLevelCooling`；`config_v8` 映射
   `oauth.providers.codex.model-level-cooling`）。sol/luna/terra/gpt-reserve
   共用一张 Plus 账号，单模型 `usage_limit_reached` 不再整张凭据全黑。

未改：凭据/账密、模型路由、并发（`ADMISSION_MAX_INFLIGHT`）、`request-retry`、
`max-retry-credentials`、Nginx、`sensitive-words`、WorkBuddy 调度、Claude lane。

## 风控结论（为什么这些改动是安全的）

- 不新增账号、不换 IP/UA/设备/时区、不制造 OAuth 失败流量、不清冷却、不缩短重试。
- 两项都只缩小**容量信号的影响范围/时长**，不改变上游配额计量，也不绕过限流。
- 账号扩散控制仍在：`fill-first` 初始选择 + `session-affinity` 粘性 +
  `rebind-on=quota`（瞬态失败不换号，保留缓存局部性）+ 入口 lane 限流 +
  调用端 `request_max_retries=0/stream_max_retries=0`。

## 验证与回滚

- 仓内：`tests/test_cpa_policy_runtime.py`、`tests/test_cpa_guardrails_script.py`
  与门禁（见提交信息判据行）。
- 投影：`pwsh -NoProfile -File scripts/cpa_bwg_guardrails.ps1 -Profile bwg -Apply`；
  成功判据为 stdout `GUARDRAILS_APPLIED` + 复跑 doctor `DOCTOR_CONTRACT_OK`。
- 回滚：只恢复本次 `config.yaml`；远端备份目录见投影回执；不得覆盖 auth JSON，
  不得用 Git 回滚替代远端恢复。
- 边界：投影后仅证 `host_loaded`；不代表长期质量、上游合规或封号豁免。

## 后续叠加：per-lane 请求速率护栏（2026-10-31 第二轮，用户授权连续修复）

用户授权按推荐连续修复。"账号级 RPM/TPM 预算"补齐为 **admission 层 per-lane
`requests_per_minute`**（CPA 核心无此键，落到 admission 前代理最省、可回滚）。

- 配置：`chatgpt-oauth=24`、`zhipu-coding-plan=30`、`antigravity-oauth=18`、
  `workbuddy-oauth=12`；缺省即不限，保持历史语义（新增字段可空、向后兼容）。
- 语义：滚动 60s 只统计**普通准入**请求数，达上限时对普通请求直接
  `admission_reason=rate_limited` + 正 `Retry-After`（指向最早槽位过期），不排队；
  恢复探针不计入且绕过。初始取值高于 24h 自然峰值（18/17/10/5）；2026-10-31
  晚把 WorkBuddy 从 18 收紧到 12，只削并行标题/摘要突发。
  **不是账号配额、不识别 token、不规避上游限流**。
- 契约：`load_config` 校验（正整数、≤600，`bool` 拒绝）；`apply`/`doctor`
  在 admission lane 契约中钉定四值；`/healthz` 暴露 `requests_per_minute` 与
  `state.rpm_window_used`；新增单测（拒绝+Retry-After、缺省不限、非法值拒绝）。
- 回滚：恢复 `cpa-admission.json` 删除该键即回到不限；`.py`/apply/doctor 改动
  可 Git 回退，但要重新 commit→apply 才能改变已部署体。

## 投影与受控验收（rpm guard）

| 层 | 证据 |
|---|---|
| `repo_verified` | 单测：拒绝+Retry-After、缺省不限、非法值拒绝；端到端（真实 Handler/Proxy/Server + 假上游，cap=2）验证 200/200/429 且**第 3 发上游零触达**；时钟推进 61s 后恢复准入。 |
| `filesystem_projected` | 部署 `cpa-admission.py` sha256 = integrity pin = 仓库 HEAD 版 `3764a4e3…`；`cpa-admission.json` sha256 两侧一致 `7f53e6c7…`；doctor `projection-drift` 全 MATCH。 |
| `host_loaded`（历史轮次） | `/healthz` 四条 lane 均带 `requests_per_minute`(24/30/18/18)；strict doctor `DOCTOR_CONTRACT_OK`、`admission-health=OK`、`admission-integrity=OK`、`semantic-policy=OK`。 |
| `controlled_live`（部分） | GLM 非 OAuth lane 连发 3 次全 200、`rpm_window_used` 0→3 递增，证明不误伤且计数正确。 |
| 未做：真实上游拒绝路径 | 未在生产泳道把某 lane 打到上限触发真实 429：为验证本地护栏而消耗真实配额、或临时改生产配置重启 admission，收益不抵风险；拒绝路径已由"部署字节 == 被测字节"的端到端测试覆盖。 |

**2026-10-31 第三轮调参（待低峰投影）**：一小时自然流量里
`workbuddy-oauth/deepseek-v4.1-flash` 的 `peak_60s=15`，达到社区 `14003`
突发区间（13–19 rpm）的告警阈；该小时 378 个结果仍无 capacity 错，但 18 rpm
上限允许更高突发。因此将其本地请求速率上限从 18 收紧到 12，低于告警阈；
保持每凭据 `inflight=1`、`max_pending=4`、30 秒瞬态冷却和原有失败回退，不轮换
账号、出口或设备。12 是本地突发边界，不是上游许可额度；投影后才算
`host_loaded`，尚待自然流量确认在途排队仍可接受。

结论：`rate_limited` 拒绝是**本地决策、不触达上游**，其正确性由端到端测试证明；生产面已确认同一字节在跑且未误伤。若必须取得生产 429 硬证，需单列一次"临时把某非 OAuth lane 的 rpm 降到 1 → apply → 两发观测 1×200+1×429 → 恢复值 apply"的受控演练（会短暂重启 admission 并耗极少量配额），需单独确认后再做。

## 后续叠加：真实流量 token/cache 遥测（2026-10-31 第三轮）

原 runbook 结论是"真实流量 token/cache 聚合不可行"，缓存命中率只能靠 canary 采样。
本轮在 admission 侧补上**只读遥测**（不新增任何准入门禁）：

- `upstream_result` journal 行追加 `usage_in/usage_cached/usage_out`。取值只来自
  数字型白名单字段（`input_tokens`/`prompt_tokens`/`promptTokenCount`、
  `output_tokens`/`completion_tokens`/`candidatesTokenCount`、
  `*_details.cached_tokens`/`cachedContentTokenCount`/`cache_read_input_tokens`），
  `bool` 与负数不算计数，**正文永不进入日志**；未上报时输出 `na`。
- SSE 侧在多事件间按分量合并（input 常在前一事件、output 在终态事件），非流式
  从 JSON 体提取；截断体解析失败按"未报告"处理，不抛错。
- `cpa_natural_traffic_audit.py` 按 lane/model 汇总并输出
  `tokens reports=N in=… cached=… out=… cache_hit_ratio=…`；`reports` 是分母口径，
  未上报的请求既不进分子也不进分母，避免把"未报告"渲染成"零命中"。
- 边界：这是**观测**，不是预算/门禁；token 级限流仍未做（admission 无法在准入前
  得知输出 token，滞后计数做限流会误伤），需要时另立设计。

## 投影与受控验收（2026-10-31 第二轮）

- 仓内提交：`4b16b4f`（admission per-lane RPM 实现、配置、apply/doctor 契约、完整性 pin、单测）、`b413036`/`085f1a2`（台账与故障分类文档）、`abeb6c8`/`ff48b56`（reviewed deploy 事务及其将 JSON 与 Python 原子收敛的修复）、`32dea63`（单发 SSE 验收脚本）。
- Full gate：`911 passed, 8 skipped, 450 subtests passed`；Bandit、Ruff、format、mypy 全通过；真实 SSH integration 按契约跳过。
- `outputs/deploy-admission-rpm-20261031.ps1 -DryRun`：远端 live admission/pin 基线为 `07c8dca4…`，候选 admission 源码为 `3764a4e3…`，healthz 空闲检查通过。
- 首次仅轮换 Python 的事务在健康复验发现远端 JSON 仍为旧配置，事务自动回滚；未留下新代码。随后事务扩展为同一备份/原子替换/回滚中的 `cpa-admission.py`、`cpa-admission.json` 与 pin。
- 最终 reviewed deploy：`ADMISSION_IDLE_OK`、`ADMISSION_INTEGRITY_OK`、`HEALTH_OK lanes=4 rpm=24,30,18,18 holds=null`、`DEPLOY_OK`；备份目录为 `/opt/cliproxyapi/backups/20261031T114933.770245321Z-admission-rpm-20261031`。
- 投影后 strict doctor：`DOCTOR_CONTRACT_OK`；`admission-health=OK`、`admission-integrity=OK`、四条已配置 OAuth lane 为 `OPEN`、`lane-hold=OK`、13 项 `projection-drift` 全部 `MATCH`。
- 一发真实 DeepSeek SSE（BWG `127.0.0.1:8318`，`deepseek-v4.1-flash`，无重试）：`LIVE_PROBE_GATE=OPEN`、`LIVE_PROBE_RPM=18 window_used=0`、HTTP `200`、`1412 ms`、19 个事件、`response.completed=true`、文本匹配 `pong`、`LIVE_PROBE_RESULT=PASS`。client key 只在远端进程内读取，未回显；request-id 只记录哈希前缀 `f04012a9a0c8`。
- 受控请求后的 strict doctor 仍为 `DOCTOR_CONTRACT_OK`，WorkBuddy lane 无 hold/cooldown；24h 聚合中该 lane 仍可见既有上游容量/不完整流样本，单发通过不代表长期上游容量或自然流量质量已关闭。
