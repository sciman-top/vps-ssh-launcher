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
  `workbuddy-oauth=18`；缺省即不限，保持历史语义（新增字段可空、向后兼容）。
- 语义：滚动 60s 只统计**普通准入**请求数，达上限时对普通请求直接
  `admission_reason=rate_limited` + 正 `Retry-After`（指向最早槽位过期），不排队；
  恢复探针不计入且绕过。取值高于 24h 自然峰值（18/17/10/5），只削并行标题/摘要
  突发。**不是账号配额、不识别 token、不规避上游限流**。
- 契约：`load_config` 校验（正整数、≤600，`bool` 拒绝）；`apply`/`doctor`
  在 admission lane 契约中钉定四值；`/healthz` 暴露 `requests_per_minute` 与
  `state.rpm_window_used`；新增单测（拒绝+Retry-After、缺省不限、非法值拒绝）。
- 回滚：恢复 `cpa-admission.json` 删除该键即回到不限；`.py`/apply/doctor 改动
  可 Git 回退，但要重新 commit→apply 才能改变已部署体。
