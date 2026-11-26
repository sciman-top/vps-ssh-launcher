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
| `simulated`（隔离 `unshare --mount --net --fork`，零生产凭据、零出网） | 使用生产容器同版真二进制 `CLIProxyAPI`（`v8.1.4` fork）+ 私有 `/etc/hosts` + 自签 `chatgpt.com` mock 上游做 `disable-codex-cloaking` A/B | `cloaking_on`（`false`）`http_status=200` 实测出站 `Originator=codex-tui`、`User-Agent=codex-tui/0.154.0 (Mac OS 26.5.2; arm64) iTerm.app/3.6.11 ...`、`x-codex-window-id=win-sim-1234`；<br>`cloaking_off`（`true`）`http_status=200` 实测出站 `Originator=Codex Desktop`、`User-Agent=Codex Desktop/0.171.0-alpha.13.2 (Windows 10.0.26200; x86_64)`、`x-codex-window-id=win-sim-1234`（临时目录已自动清理） |
| `filesystem_projected` + `host_loaded` | 1) `scripts/cpa_bwg_guardrails.ps1 -Profile bwg -Apply`<br>2) strict doctor（`scripts/cpa_bwg_guardrails.ps1 -Profile bwg`）<br>3) `scripts/cpa_status.py --profile bwg` | 1) `GUARDRAILS_APPLIED`、`READY_STATUS=200`、`HEALTH_OK`、17/17 `PROJECTION_HASH_VERIFIED`<br>2) `DOCTOR_CONTRACT_OK`、`POLICY_OK`、`semantic-policy=OK`、`admission-integrity=OK`、`nginx-syntax=OK`、`lane-hold-chatgpt-oauth=OPEN`、`oauth_monitor=OK`（`oauth_days_left=6`）、`credential_ban_signals_7d=0`、`upstream.codex.disable-codex-cloaking: true` 已加载<br>3) 全部在役 OAuth 车道（含 `chatgpt-oauth` 4 模型）均为 `OK` |
| `controlled_live`（仅非 OAuth 车道） | `python3 /opt/cliproxyapi/cpa-health.py cache-canary glm-5.3-flash chat` | sample 1 `4114 in / 0 cached (0.0000)` -> sample 2 `4114 in / 4096 cached (0.9956 = 99.56%)`、`HEALTH_OK` |

## 未验证边界与后续观察

- 遵守外部 API 探测纪律，本次未向 `chatgpt-oauth` 发送额外合成请求；关闭 `codex-cloaking` 对 `server_is_overloaded` 频次的实际改善需通过接下来的自然桌面流量与 `cpa-admission` 日志观察验证。
- 若关闭 cloaking 后官方桌面端自然流量仍高频复现 `server_is_overloaded`，则说明该波过载来自 OpenAI 侧全局容量或单账号软限流，届时按 L2 runbook 等待恢复或临时切换非 OAuth 渠道（如 `gpt-6.2-astra` API key 路由）。
- `aliyun` 上的 `qq-codex-bot-provider-watchdog` 属于跨仓 `qq-astrbot-stack`，本次未跨仓修改；若希望彻底消除其周期性探测触发共享 `chatgpt-oauth` 冷却的噪音，可在 `qq-astrbot-stack` 仓单独降低其探测频次或停用对 OAuth 车道的探活。

## 后续增补：修复 `cpa-admission` 早探时序错配与排队快拒（2026-11-26 晚）

### 触发依据（上线后 14 分钟自然流量实证）

- `disable-codex-cloaking: true` 上线后，22:36:51（`14:36:51Z`）官方桌面端 `gpt-6-luna` 仍触发上游 `502 server_is_overloaded`（`retry-after=354s`），说明本波过载来自 OpenAI 上游侧而非 cloaking 单因。
- 同时捕获到两项网关侧放大器：
  1. `14:36:59Z` 流内 `response.failed` 后仅 10s（`14:37:09Z`），`cpa-admission` 按 `early_probe_interval_seconds=10` 放行早探，直接撞上 CPA 核心 `transient-error-cooldown-seconds=30` 返回内部 `503`（13ms 零出网），二次升级退避阶梯。
  2. 已在排队中的请求若遇到新到达的上游 `Retry-After` 超过其剩余排队截止时间，此前会继续空等满 120s 才报 `queue_timeout`。

### 改动提交与远端投影回执

- **`f20f1a4`** — `fix(cpa-admission): raise early_probe_interval_seconds from 10 to 35 past core 30s cooldown`
  - `scripts/remote/cpa-admission.json`：`early_probe_interval_seconds` 从 `10` 提升至 `35`（严格大于 CPA 核心 `30s` 瞬时冷却）。
  - `scripts/remote/cpa_policy.py`：在 `_admission_config_issues()` 中钉定 `early_probe_interval_seconds > EXPECTED_CONFIG_PATHS[("routing", "cooldown", "transient-error-cooldown-seconds")]`。
  - `tests/test_cpa_admission.py`、`docs/runbooks/cpa-gateway.md`：同步更新单测与说明。
- **`38b37aa`** — `fix(cpa-admission): fast-fail queued requests when server Retry-After exceeds deadline`
  - `scripts/remote/cpa-admission.py` + `scripts/remote/cpa-admission-integrity-pin.txt`（新 pin `261a95eba2c31a34dcc1671d318a8ddf806ff763087ce9a02dfab9a16872c35d`）：当 `queued` 且 `self._server_not_before >= deadline and next_admission >= deadline` 时立即返回 `cooldown`。
- **分层验证**：
  - `repo_verified`：`scripts/run_gates.ps1 -RunParallel` 全绿（`CATALOG_CHECK=PASS`、`bandit`、`ruff`、`mypy` 165 files 0 issues、`pytest` `1896 passed, 30 skipped, 899 subtests passed in 101.36s`）；`scripts/check_docs.py` `DOCS_CHECK=PASS (42 documents)`。
  - `filesystem_projected` + `host_loaded`（仅 `bwg`）：
    1. 原子轮换（`outputs/deploy-admission-early-probe-fastfail-20261126.ps1`，先 `-DryRun` 后正式切换）：`DRY_RUN_OK` → `PREFLIGHT_OK`、`REQUEST_GUARD_EXCLUSIVE_OK`、`ADMISSION_IDLE_OK`、`ADMISSION_PIN_ROTATED old=dd49bd2c... new=261a95eb... config=ca018e92...`、`ADMISSION_INTEGRITY_OK`、`DEPLOY_OK`。
       - 远端备份：`/opt/cliproxyapi/backups/20261126T160529.134871342Z-admission-early-probe-fastfail-20261126`。
    2. 全量守卫投影（`scripts/cpa_bwg_guardrails.ps1 -Profile bwg -Apply`）：`GUARDRAILS_APPLIED`、`READY_STATUS=200`、`HEALTH_OK`、17/17 `PROJECTION_HASH_VERIFIED`。
       - 远端备份：`/root/cpa-guardrails-backup-20261126T160608.269100610Z`。
       - 校验和：`/opt/cliproxyapi/cpa-admission.py` `261a95eba2c31a34dcc1671d318a8ddf806ff763087ce9a02dfab9a16872c35d`、`/opt/cliproxyapi/cpa-admission.json` `ca018e92af69edb3c02600268ecee7f6d13a08082685b3245bbf13857b8fe0b9`、`/opt/cliproxyapi/cpa_policy.py` `ce30847a183f988c8c546dc3720b7830607d81d8a2aad18aec4e31bc1093612e`。
    3. strict doctor：`DOCTOR_CONTRACT_OK`、`nginx-syntax=OK`；`cpa_status.py --profile bwg` 全部在役 OAuth 车道 `OK`。
  - `controlled_live`（仅非 OAuth 车道）：`python3 /opt/cliproxyapi/cpa-health.py cache-canary glm-5.3-flash chat` 通过（`4114/0 (0.0000)` → `4114/4096 (0.9956)`、`HEALTH_OK`）。
