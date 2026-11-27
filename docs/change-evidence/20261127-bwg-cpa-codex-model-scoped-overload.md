# 2026-11-27 bwg CPA `chatgpt-oauth` 模型级 `server_is_overloaded` 与内嵌 `retry-after` 隔离修复

## 1. 背景与故障根因（18:30 CST / `2026-11-27T10:30:39Z` 线程 `01a21c8e-99dc-7f70-ba28-eb0dd6162a96`）

用户在 ChatGPT Desktop / Codex App 线程 `01a21c8e-99dc-7f70-ba28-eb0dd6162a96`（主模型配置为 `gpt-6.1-sol`，请求哈希前缀 `15f268058d97`）发送消息时遭遇断流报错：

```text
stream disconnected before completion: Shared upstream lane chatgpt-oauth is cooling down or busy; retry after 314s.
```

按毫秒级日志对齐（本机 `~/.codex/sessions/2026/11/27/rollout-2026-11-27T18-30-37-01a21c8e-99dc-7f70-ba28-eb0dd6162a96.jsonl`、远端 `journalctl -u cpa-admission.service`、`docker logs cli-proxy-api`）还原完整因果链：

1. **Codex Desktop 后台硬编码并发抢槽**：
   Codex Desktop（`app.asar` 模块 `80096`）在 Statsig 默认配置中将后台标题/摘要/建议生成固定在 `gpt-6-luna`（`codex_ephemeral_generation_model_slug`、`codex_ambient_suggestions_model_slug`、`codex_ambient_suggestions_safety_model_slug`）。用户在 `10:30:38Z` 提交消息后，客户端在 `10:30:39.293Z`（`8fd86031`）与 `10:30:39.314Z`（`37b99f12`）率先发出 2 个 `gpt-6-luna` 请求，占满 `chatgpt-oauth` 的 `max_inflight=2` 槽位；`10:30:39.387Z` 第 3 个 `gpt-6-luna` 请求（`d3ac43e3`）进入 FIFO 队首，而用户真正的主请求 `504f99be`（`model=gpt-6.1-sol`）在 `10:30:40.726Z` 排在 `d3ac43e3` 之后。
2. **上游 `gpt-6-luna` 流内 `server_is_overloaded` 与内嵌 `retry-after`**：
   `10:30:47.799Z`，`8fd86031`（`gpt-6-luna`）在 HTTP 200 SSE 第 4 个事件收到 `response.failed`，错误码为 `server_is_overloaded`，并在 JSON 载荷内携带 `{"headers":{"retry-after":"317","retry-after-ms":"317000"}}`。
3. **CPA 核心与 `cpa-admission` 的两层语义错位**：
   - 在 `cli-proxy-api`（`v8.1.7`）内部，`codexTerminalFailureErrWithCooling`（`codex_executor_terminal.go:180-181`，上游 issue `#7600`）解析了 JSON 内嵌的 `headers.retry-after=317s`，并由 `MarkResult`（`conductor_cooldown.go:2093-2095, 2423-2429`）**仅冷却该凭据下的 `gpt-6-luna` 模型分片 317 秒**（`credentialScoped=false`），同凭据的 `gpt-6.1-sol` 与 `gpt-6.2-sol` 分片在 CPA 核心内完全可用。
   - 然而 `cpa-admission` 此前存在两处缺口：
     1. `chatgpt-oauth.model_capacity_markers` 未包含 `"server_is_overloaded"`（仅含 `"model_capacity"` / `"Selected model is at capacity"`），导致 `server_is_overloaded` 被归为 `scope="lane"`（全车道级）；
     2. HTTP 200 流内错误没有顶层 HTTP `Retry-After` 响应头，`cpa-admission` 未提取 SSE JSON 内嵌的 `response.error.headers.retry-after`，因而在首次失败（`failure_streak=1 < threshold=2`）时完全未开启 `gpt-6-luna` 的模型冷却，立即把队首的 `d3ac43e3`（`gpt-6-luna`）放行进 CPA 核心。
   - `d3ac43e3`（`gpt-6-luna`）在 7ms 内撞上 CPA 核心的 `gpt-6-luna` 模型分片冷却，CPA 核心返回 **HTTP 503 + 顶层 `Retry-After: 317`**（`auth_unavailable`）。
   - `cpa-admission` 收到这笔带 `Retry-After: 317` 的 503 后，因 `scope="lane"` 直接在**整条 `chatgpt-oauth` 车道**上武装了 `_server_not_before = now + 317` 与 `open_until = now + 317`。
   - 5ms 后（`10:30:47.814Z`），排队中的用户主请求 `504f99be`（`gpt-6.1-sol`）及其后两次客户端流重试（`84d581f7`、`26f99b11`）全部被 `cpa-admission` 的全车道冷却以 `reason=cooldown retry_after=317/316/314` 秒拒，造成 `gpt-6-luna` 单模型容量过载连坐封杀 `gpt-6.1-sol` 整整 5 分钟。

此外，过去 4 天（11-24 至 11-27）的 `chatgpt-oauth` 历史故障由三类独立原因构成：
- **11-24 至 11-25**：`56011ac` 曾将 `gpt-6-luna`/`gpt-6.1-sol`/`gpt-6.2-sol` 映射到 `gpt-6-astra`，而 ChatGPT Plus 账号上游不支持 `gpt-6-astra`（400 `The 'gpt-6-astra' model is not supported when using Codex with a ChatGPT account`），已于 11-25 `6c2276a` 恢复直连并禁用 `gpt-6-astra`。
- **11-26**：Codex `cloaking` 注入假 `user_id` 触发上游 `403 Forbidden`，已于 11-26 禁用 `cloaking` 并对齐官方 Codex CLI 指纹。
- **11-26 至 11-27**：`gpt-6-luna` 高频触发上游 `server_is_overloaded`（内嵌 `retry-after: 256s~348s`），经上述机制放大为整条 `chatgpt-oauth` 车道 5 分钟级别的雪崩断流（本次彻底根治）。

## 2. 修复内容

1. **模型级容量标记对齐**（`scripts/remote/cpa-admission.json`、`scripts/remote/cpa_policy.py`、`scripts/cpa_admission_risk_audit.py`）：
   - 将 `"server_is_overloaded"` 纳入 `chatgpt-oauth.model_capacity_markers`（与 `"model_capacity"`、`"Selected model is at capacity"` 并列）。
2. **流内/响应体内嵌 `headers.retry-after` 提取**（`scripts/remote/cpa-admission.py`）：
   - 新增 `codex_overload_retry_after()`：对 `chatgpt-oauth` 且 `capacity_scope(...) == "model"` 的 JSON / SSE `data:` 帧，递归定位含模型容量标记的嵌套错误字典，解析 `headers.retry-after`（秒）或 `headers.retry-after-ms`（毫秒向上取整为秒），夹逼在 `[1, MAX_SERVER_RETRY_AFTER_SECONDS]`。
   - 在 `StreamErrorProbe._event()` 与 `Handler._proxy_unlocked()` 中将该值写入 `model_retry_after`（不污染车道级 `server_retry_after`），使 `LaneState.release()` 在首笔 HTTP 200 流内 `server_is_overloaded` 结束时即刻仅对故障模型（如 `gpt-6-luna`）武装 `_model_server_not_before[model]` 与 `_model_open_until[model]`，保持 `open_until = 0.0`（车道敞开）。
   - 已在队列中等待槽位的同模型请求（如 `d3ac43e3`）在唤醒后立即在 `acquire()` 顶部以 `reason="model_cooldown"` 快速拒绝，不再打穿到 CPA 核心制造 503 连坐；异模型主请求（如 `gpt-6.1-sol` / `gpt-6.2-sol`）直接获槽放行。

## 3. 部署、验证与回滚

- **本地门禁**：`pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1 -RunParallel` 全绿通过（`1899 passed, 30 skipped, 903 subtests passed`）。
- **远端原子切换**（`outputs/deploy-admission-codex-model-overload-20261127.ps1`）：
  - 旧 `cpa-admission.py` pin：`b99cfd2ff24c251a17eb5839f63af438991f692fadc6db5bf3a2f5091ff0a4a6`
  - 新 `cpa-admission.py` pin：`769b3b6d087c80ca1ae606107f2daf03747c687e0a51012f34906ba97ad7f343`
  - 旧 `cpa-admission.json` sha256：`ca018e92af69edb3c02600268ecee7f6d13a08082685b3245bbf13857b8fe0b9`
  - 新 `cpa-admission.json` sha256：`0e976f752571cf510e73ebd5b95d60001173ca9abd055bc9cd5ec6e40a44acb8`
  - 远端原子备份目录：`/opt/cliproxyapi/backups/20261127T105835.266045974Z-admission-codex-model-overload-20261127`
- **Guardrails 投影与 Doctor 验收**：
  - `scripts/cpa_bwg_guardrails.ps1 -Profile bwg -Apply`：17/17 `PROJECTION_HASH_VERIFIED`，`HEALTH_OK`，`READY_STATUS=200`，备份 `/root/cpa-guardrails-backup-20261127T105844.119533839Z`。
  - `scripts/cpa_bwg_guardrails.ps1 -Profile bwg`：`DOCTOR_CONTRACT_OK`。
  - `scripts/cpa_status.py --profile bwg`：`gpt-6-luna`、`gpt-6.1-sol`、`gpt-6.2-sol` 状态均为 `OK`。
- **三层模拟与实战验收（`live_accepted`）**：
  1. **远端真源模块 18:30 并发队列重放模拟**（直接加载 `bwg` `/opt/cliproxyapi/cpa-admission.py` + `/opt/cliproxyapi/cpa-admission.json`）：
     - 构造 2 笔在途 `gpt-6-luna` 占满 `max_inflight=2`，第 3 笔 `gpt-6-luna` 与用户主请求 `gpt-6.1-sol` 依次排队；
     - 首笔 `gpt-6-luna` 收到 HTTP 200 SSE `response.failed`（`server_is_overloaded` + 内嵌 `retry-after: 317`）释放槽位后：
       - 队首第 3 笔 `gpt-6-luna` 即刻以 `Lease(admitted=False, reason='model_cooldown', retry_after=317)` 拒绝，零穿透 CPA 核心；
       - 排在后面的 `gpt-6.1-sol` 主请求即刻以 `Lease(admitted=True, reason='admitted', retry_after=0)` 获槽放行；
       - 车道快照保持 `cooldown_active=False`、`cooldown_scope='model'`、`model_cooldowns={'gpt-6-luna': 317}`。
  2. **部署后真实自然流量命中验证（`11:20:29Z` – `11:25:58Z`）**：
     - `11:20:29.424Z`：自然流量 `4b198a60`（`model=gpt-6-luna`）在 HTTP 200 SSE 第 4 帧触发上游 `server_is_overloaded`（内嵌 `retry-after: 316`），`cpa-admission` 立即将 `gpt-6-luna` 单独置入 316s `model_cooldown`（零 503 级联）；
     - `11:21:48Z` – `11:21:51Z`：后续 5 笔后台 `gpt-6-luna` 请求（`003495d5`、`508f09d5`、`c1346253`、`8b3aedbe`、`38acd18b`）均在 `waited_ms=0` 被 `reason=model_cooldown retry_after=237..234` 拦截，车道保持 `cooldown_active=false`；
     - `11:25:46.575Z`（316s 窗口到期后 1 秒）：`80102273`（`model=gpt-6-luna`）准入并成功完成（`status=200 protocol_result=completed usage_in=34195 usage_cached=34048 usage_out=146`）。
  3. **受控实战端到端流式验收（`scripts/cpa_stream_acceptance.py`）**：
     - 当 `gpt-6-luna`（剩余 229s）与 `gpt-6.1-sol`（`11:27:35Z` 命中上游 `server_is_overloaded` 内嵌 `retry-after: 372`，剩余 324s）同时处于各自独立的 `model_cooldowns` 时，`chatgpt-oauth` 车道仍保持 `cooldown_active=false`；
     - 执行 `./.venv/Scripts/python.exe scripts/cpa_stream_acceptance.py --model gpt-6.2-sol --expect-response-model gpt-6.2-sol`，请求 `7e7c894db3fb4e60a9742d36eefdea0f` 在 `waited_ms=11` 直接准入并通过公网网关端到端流式返回 `pong`：
       `STREAM_ACCEPTANCE={"attempt": 1, "capacity_marker": false, "completed_ms": 7310.23, "data_event_count": 9, "failures": [], "http_status": 200, "model": "gpt-6.2-sol", "request_id": "7e7c894db3fb4e60a9742d36eefdea0f", "result": "PASS", "text_matches": true, "total_ms": 7318.993}`。
- **回滚命令**：
  如需回滚远端 `cpa-admission`，在维护锁下将 `/opt/cliproxyapi/backups/20261127T105835.266045974Z-admission-codex-model-overload-20261127` 中的 `cpa-admission.py`、`cpa-admission.json`、`cpa_request_guard.py`、`cpa-admission.sha256` 考回原路径并执行 `systemctl restart cpa-admission`。
