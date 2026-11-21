# BWG CPA 深度审查与接入层/风控/观测加固验收（2026-11-22）

**目标**：针对 `bwg` 上多 OAuth 账号（OpenAI/Codex、Antigravity/Gemini、WorkBuddy CN/INTL、已退役 Claude）与多 API key 渠道（`ai.input.im`、`zhipu-plan`、`zcode-start-plan`）开展全链路封号/限流/降智风控、缓存命中率与 TPS/TTFT/E2E 深度审查，落地 5 项接入层/配置/观测加固与 2 项 apply 事务修复，并完成 `bwg` 原子轮换、全量投影、strict doctor 与非 OAuth 受控实战验收。`zz` 未触碰；未新增 OAuth 生成探针，未改动任何用户 OAuth 凭据文件。

## 仓库提交与改动摘要

1. **`1f00764`** — `fix(cpa): harden admission headers, stream probe, RPM accounting, shutdown grace and doctor observability`
   - `scripts/remote/cpa-admission.py` + `scripts/remote/cpa-admission-integrity-pin.txt`（新 pin `862b1400352393719b049701c60d8a55588db1e71d362d783bbf3b7794a70f16`；渲染后 `cpa_request_guard.py` SHA256 `c6ca1b2824f79b2809dd67d697f9842eaa9febb2d5a1d963d2f9445966c0b834`）：
     - `_proxy()` 跳过转发上游 `Date` 与 `Server` 响应头（`GENERATED_RESPONSE_HEADERS`），消除 `BaseHTTPRequestHandler.send_response()` 已生成标准头后再透传上游头导致的 Nginx `upstream sent duplicate header line` 刷屏。
     - 新增可配置 `stream_probe_bytes`（区间 `[probe_bytes, max_body_bytes]`），在 `scripts/remote/cpa-admission.json` 配置为 `2097152`（2 MiB，同时保持 `probe_bytes=262144`），消除 `gpt-6-luna` 等长思考单帧 `response.completed` 超过 256 KiB 触发的误报 `inspection_limit`。
     - 将 `half_open` 与 `model_half_open` 恢复探针计入 `LaneState._rpm_window`，防止恢复探测叠加常规流量突破 60s `requests_per_minute` 上限（此前 `workbuddy-oauth` 曾出现 `admit_peak_60s=19` 超过 `18` RPM）。
   - `scripts/remote/cpa_policy.py` + `scripts/remote/cpa-guardrail-apply.sh`：
     - 钉定 `server.shutdown-timeout: 15`，与升级器 15s Docker stop 宽限期对齐，减少计划维护/重启期间的 502 截断。
     - 校验 `cpa-admission.json` 的 `stream_probe_bytes` 契约。
   - `scripts/remote/cpa-guardrail-doctor.sh`：
     - 新增只读观测 `oauth_<provider>_refresh_cluster_max_10s=<n>`，暴露多凭据 OAuth 车道 10 秒内集簇刷新风险。
     - 扩展 `credential-ban-signals` 匹配 `invalidated oauth token`（输出 `credential_ban_kind=invalidated_oauth_token`），消除此前 Codex 401 失效在 7d 汇总中记为 0 的盲区。
     - 新增只读观测 `workbuddy_failover_probe_401_7d` 与 `workbuddy_failover_probe_401_auths_7d`，量化已禁用 WorkBuddy 凭据触发 10 分钟探针 401 的频次。
   - 同步补齐 `tests/script_validation_support.py`、`tests/test_cpa_admission.py`、`tests/test_cpa_policy_runtime.py`、`tests/test_cpa_guardrails_script.py`。
2. **`eb4a380`** — `fix(cpa-apply): allowlist server.shutdown-timeout and fix rollback check line continuation`
   - 首次 `-Apply` 实测暴露两项缺陷并安全回滚：
     - 在 `scripts/remote/cpa-guardrail-apply.sh` 中对 `server` 块精确仅放行 `server.shutdown-timeout` 变动，保持 `server.host`、`server.port`、`server.tls`、`remote-management` 等其余字段严格不可变。
     - 修复 `cpa-guardrail-apply.sh` 回滚自检 `[ ... =` 缺少续行符 `\` 的 Bash 语法错误，并补充 `tests/test_cpa_guardrails_script.py` 回归测试。

## 远端投影与回滚备份（仅 `bwg`）

1. **`cpa-admission` 原子轮换**（`outputs/deploy-admission-headers-probe-rpm-20261121.ps1`）：
   - 先 `-DryRun` 通过：`DRY_RUN_OK baseline=53bb40c5b78a3d2279ef9238f0164a2380af17124ef7a483a7b685a1db9f3fc9 candidate=862b1400352393719b049701c60d8a55588db1e71d362d783bbf3b7794a70f16 no_swap=true`。
   - 正式切换通过：`REQUEST_GUARD_EXCLUSIVE_OK`、`ADMISSION_IDLE_OK`、`ADMISSION_PIN_ROTATED`、`ADMISSION_INTEGRITY_OK`、`ADMISSION_CONTRACT_20261121B_OK`、`DEPLOY_OK`。
   - 远端备份：`/opt/cliproxyapi/backups/20261121T155107.478990965Z-admission-headers-probe-rpm-20261121`。
2. **守卫全量投影**（`scripts/cpa_bwg_guardrails.ps1 -Profile bwg -Apply`）：
   - 首次因 `server.shutdown-timeout` 未在白名单安全中止并回滚（服务保持在线）；修复提交 `eb4a380` 后二次运行遇到 1 条在途 `chatgpt-oauth` 请求按契约返回 `REFUSE cpa_apply_requires_idle_lanes`（退出码 75，未写入）；全车道空闲后第三次运行成功：`GUARDRAILS_APPLIED`、`READY_STATUS=200`、`HEALTH_OK`、17/17 `PROJECTION_HASH_VERIFIED`。
   - 投影后校验和：
     - `/opt/cliproxyapi/config.yaml`: `deb1cef508cd823944de21b54b3120e7f07bd96defbbb27c469271558a389a0d`
     - `/opt/cliproxyapi/cpa-admission.py`: `862b1400352393719b049701c60d8a55588db1e71d362d783bbf3b7794a70f16`
     - `/opt/cliproxyapi/cpa-admission.json`: `fb054ae6afd50ad3cc2e765af6824379a86ff281a93ee118b35377936da3b9f7`
   - 远端备份：`/root/cpa-guardrails-backup-20261121T162540.783834735Z`。

## 分层验证证据

| 层级 | 执行内容 | 结论与关键回执 |
| --- | --- | --- |
| `repo_verified` | `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1 -RunParallel`（覆盖最终代码提交 `eb4a380`）+ `bash -n scripts/remote/cpa-guardrail-apply.sh scripts/remote/cpa-guardrail-doctor.sh` | `CATALOG_CHECK=PASS`、`bandit` / `bandit-scripts`、`ruff check`、`ruff format --check`（178 files）、`mypy`（165 source files, 0 issues）、`pytest` `1828 passed, 30 skipped, 896 subtests passed in 87.86s`、`BASH_SYNTAX_OK` |
| `filesystem_projected` + `host_loaded` | 只读 strict doctor（`scripts/cpa_bwg_guardrails.ps1 -Profile bwg`） | `DOCTOR_CONTRACT_OK`、`admission-integrity=OK`、17/17 `projection-drift MATCH`、`semantic-policy=OK`、`nginx-syntax=OK`；新增观测信号全部生效：`oauth_antigravity_refresh_cluster_max_10s=7`、`credential_ban_kind=invalidated_oauth_token n=1`、`workbuddy_failover_probe_401_7d=65`（`workbuddy_failover_probe_401_auths_7d=1`） |
| `controlled_live`（仅非 OAuth 车道） | 1) `cpa-admission:8318` 直连 `glm-5.3-flash` SSE<br>2) `Nginx:8443 -> cpa-admission:8318 -> CLIProxyAPI:8317` 全链路 `glm-5.3-flash` SSE + `nginx/error.log` 校验<br>3) `cpa-health.py cache-canary glm-5.3-flash chat` | 1) `CONFIG_RUNTIME_OK shutdown_timeout=15 stream_probe_bytes=2097152`、`status=200`、`date_header_count=1`、`server_header_count=1`、`header_ms=4`、`first_chunk_ms=1247`、`total_ms=1247`、`has_done=True`<br>2) `NGINX_8443_LIVE status=200 date_headers=1 server_headers=1 header_ms=10 total_ms=2348 bytes=3485 has_done=True dup_header_log_delta=0 dup_since_1552_utc=0`<br>3) sample 1 `0/4114 (0.0000)` -> sample 2 `4096/4114 (0.9956 = 99.56%)`、`HEALTH_OK` |

## 未验证边界与后续跟踪项

- 本次受控在线验收仅覆盖非 OAuth 车道（`zhipu-coding-plan` / `glm-5.3-flash`），未向 OpenAI/Codex、Antigravity 或 WorkBuddy OAuth 车道发送合成探针；OAuth 账号的长期防封与自然流量缓存/吐字表现需继续以自然流量统计与 strict doctor 周期跟踪。
- 上游 `CLIProxyAPI` `origin/dev`（尚未打 `v8.0.81` 标签）已有 9 项高价值修复待发版后评估升级：`#7758`（Antigravity `connection-pool.max-idle-conns-per-host` 在 `v8.0.80` 失效）、`#7749`（执行期刷新持久化/串行化）、`#7755`（`thoughtSignature` 保留防降智）、`#7760`（多模态首轮 `sessionId` 扫描）、`#7761`（插件调度器会话亲和）、`#7813`（Antigravity Hub 版本对齐）、`#7756/#7754`（禁用/TOS/401 凭据防复活）、`#7745/#7741`（断流上游及时取消）、`#7718`（Codex token 原子写入 `fsync`）。
- 以下 3 项涉及用户凭据或跨车道配置，留待用户确认后单独立项执行：
  1. 是否归档/移出已 `disabled` 的 `workbuddy-eb624ce3...` 凭据文件，以彻底停止 WorkBuddy 插件每 10 分钟的 401 failover probe 噪声；
  2. 是否同步将 Nginx 8443 与 443 两条车道的 `client_body_buffer_size` 由 `128k` 提升至 `1m`，消除大上下文请求落盘 `/var/lib/nginx/body` 的临时文件 I/O；
  3. `zcode-start-plan`（`172.17.0.1:18082`）明文 HTTP 环回桥接与 `aliyun` watchdog 周期性后台合成探测频次优化。
