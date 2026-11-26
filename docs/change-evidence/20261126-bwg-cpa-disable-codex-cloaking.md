# BWG CPA 关闭 `codex-cloaking` 缓解 `chatgpt-oauth` 持续过载波（2026-11-26）

**目标**：针对 2026-11-24 起持续超过 48 小时的 `chatgpt-oauth` 车道 `server_is_overloaded` 过载波及由此引发的客户端 `Shared upstream lane chatgpt-oauth is cooling down or busy; retry after <N>s.` 拒流，经用户明确授权在 `bwg` 上关闭 CPA `upstream.codex.disable-codex-cloaking`（设为 `true`），使官方 Codex Desktop 请求透传自身真实且自洽的 `Originator` / `User-Agent` 头，不再被无条件改写为被上游降优先级的 `Originator: codex-tui`。`zz` 与 `aliyun` 未触碰；未向 OAuth 车道发送任何合成探测请求，未改动任何用户 OAuth 凭据文件。

## 现象与现场只读诊断（变更前）

- **用户侧报错**：ChatGPT / Codex Desktop 调用 GPT 模型时频繁出现 `stream disconnected before completion: Shared upstream lane chatgpt-oauth is cooling down or busy; retry after 194s.`。
- **现场只读日志（`bwg`，2026-11-26）**：
  - `scripts/cpa_status.py --profile bwg`：`chatgpt-oauth` 4 个模型（`gpt-6-luna`、`gpt-6.1-sol`、`gpt-6.1-terra`、`gpt-6.2-sol`）状态正常，唯一 Plus 凭据 `status=active`，`/var/lib/cpa-admission/chatgpt-risk.json` 为 `hold_reason=null, not_before=0`（无封号/人工锁存）。
  - `cli-proxy-api` 容器日志录得 2026-11-26 当日 **23 次** `502 server_is_overloaded`（`gpt-6.1-sol`: 13，`gpt-6.1-terra`: 6，`gpt-6-luna`: 4），上游附带 `retry-after` 为 **306–374s（中位数 331s）**；另有多次 HTTP 200 流内第 4 帧 `response.failed`（`capacity=true`）。
  - 精准对应时间线：`12:17:50Z`（北京时间 `20:17:50`）`gpt-6-luna` 触发 `502 server_is_overloaded`（`retry-after=306s`），整条单凭据 `chatgpt-oauth` 车道进入冷却；`12:19:40Z–12:19:43Z` 客户端 `gpt-6.1-sol` 请求依次命中 `queue_timeout`（`waited_ms=120000`, `retry_after=197`）与 2 次 `cooldown` 快拒（`retry_after=195`、`retry_after=194`）。
  - 另观测到来自 `aliyun` 周期性后台 watchdog 的 `/v1/chat/completions` 小请求（`usage_in=53, usage_out=16`）与桌面端共享 `chatgpt-oauth` 车道，其撞上过载后同样会触发整条车道 5–6 分钟冷却。
- **根因依据**：结合 [上游台账](../runbooks/cpa-observed-baselines-and-upstream-log.md) 2026-11-25 实测与上游 `CLIProxyAPI#4679`，开启 `codex-cloaking` 会无条件将官方桌面客户端请求改写为 `Originator: codex-tui` + macOS iTerm UA，同时透传 Windows 桌面端专属头（`x-codex-window-id` 等），既造成复合指纹矛盾，又落入 OpenAI 高负载下优先削峰的 `codex-tui` 分流路径。本栈 `chatgpt-oauth` 的 agent 流量全部来自官方客户端，关闭 cloaking 可让客户端透传真实且自洽的官方头。

## 仓库提交与改动摘要

1. **`dfb018f`** — `fix(cpa): disable codex cloaking so official desktop headers pass verbatim`
   - `scripts/remote/cpa_policy.py`：在 `EXPECTED_CODEX` 中钉定 `"disable-codex-cloaking": True`；`_codex_cloaking_issues()` 改为拒绝旧路径（`oauth.providers.codex.disable-codex-cloaking`、`codex.disable-codex-cloaking`）显式设 `False`，防止残余覆写静默重开 `codex-tui` cloaking。
   - `scripts/remote/cpa-guardrail-apply.sh`：在 `upstream.codex` 投影块写入 `"disable-codex-cloaking": True`。
   - `scripts/remote/cpa-guardrail-doctor.sh`：在 `==cpa-policy==` 结构回读中纳入 `disable-codex-cloaking:`。
   - `tests/script_validation_support.py`、`tests/test_cpa_policy_runtime.py`：同步更新夹具与策略单测（`test_cpa_policy_requires_codex_cloaking_disabled`）。

## 远端投影与回滚备份（仅 `bwg`）

- **守卫投影**：运行 `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/cpa_bwg_guardrails.ps1 -Profile bwg -Apply`（基于干净提交 `dfb018f`）：
  - 回执：`GUARDRAILS_APPLIED`、`READY_STATUS=200`、`HEALTH_OK`、17/17 项 `PROJECTION_HASH_VERIFIED`。
  - 投影后校验和：
    - `/opt/cliproxyapi/config.yaml`: `f2ff55cf12c9eaa60e465be1afff57c573f71bd1f2b59662c2809d6c4f24db5e`
    - `/opt/cliproxyapi/cpa_policy.py`: `3dc0f06f1bad49d214b0b9f77aa90fd738ac8c0e502de844f54324843a5866a9`
- **远端备份与回滚入口**：
  - 备份目录：`/root/cpa-guardrails-backup-20261126T142818.694754085Z`。
  - 若需回滚：撤销 `dfb018f` 并重跑 `scripts/cpa_bwg_guardrails.ps1 -Profile bwg -Apply`，或直接从上述备份目录恢复原 `/opt/cliproxyapi/config.yaml` 与投影文件后重启 `cli-proxy-api`。

## 分层验证证据

| 层级 | 执行内容 | 结论与关键回执 |
| --- | --- | --- |
| `repo_verified` | `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1 -RunParallel` | `CATALOG_CHECK=PASS`、`bandit` / `bandit-scripts` PASS、`ruff check` / `ruff format --check` PASS、`mypy`（165 source files, 0 issues）、`pytest` `1894 passed, 30 skipped, 899 subtests passed in 120.13s` |
| `filesystem_projected` + `host_loaded` | 1) `scripts/cpa_bwg_guardrails.ps1 -Profile bwg -Apply`<br>2) strict doctor（`scripts/cpa_bwg_guardrails.ps1 -Profile bwg`）<br>3) `scripts/cpa_status.py --profile bwg` | 1) `GUARDRAILS_APPLIED`、`READY_STATUS=200`、`HEALTH_OK`、17/17 `PROJECTION_HASH_VERIFIED`<br>2) `DOCTOR_CONTRACT_OK`、`POLICY_OK`、`semantic-policy=OK`、`admission-integrity=OK`、`nginx-syntax=OK`、`lane-hold-chatgpt-oauth=OPEN`、`oauth_monitor=OK`（`oauth_days_left=6`）、`credential_ban_signals_7d=0`、`upstream.codex.disable-codex-cloaking: true` 已加载<br>3) 全部在役 OAuth 车道（含 `chatgpt-oauth` 4 模型）均为 `OK` |

## 未验证边界与后续观察

- 遵守外部 API 探测纪律，本次未向 `chatgpt-oauth` 发送额外合成请求；关闭 `codex-cloaking` 对 `server_is_overloaded` 频次的实际改善需通过接下来的自然桌面流量与 `cpa-admission` 日志观察验证。
- 若关闭 cloaking 后官方桌面端自然流量仍高频复现 `server_is_overloaded`，则说明该波过载来自 OpenAI 侧全局容量或单账号软限流，届时按 L2 runbook 等待恢复或临时切换非 OAuth 渠道（如 `gpt-6.2-astra` API key 路由）。
- `aliyun` 上的 `qq-codex-bot-provider-watchdog` 属于跨仓 `qq-astrbot-stack`，本次未跨仓修改；若希望彻底消除其周期性探测触发共享 `chatgpt-oauth` 冷却的噪音，可在 `qq-astrbot-stack` 仓单独降低其探测频次或停用对 OAuth 车道的探活。
