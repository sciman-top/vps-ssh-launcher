# BWG CPA 深度审查修复：Passthrough SSE 遥测与 Antigravity 刷新错峰（2026-11-22）

## 审查发现与处理决策

对 BWG VPS 上的 CPA（`CLIProxyAPI v8.0.80`）及本仓准入网关 `cpa-admission.py` 开展多 OAuth 账号（OpenAI/Codex、Antigravity/Gemini、WorkBuddy、Claude/Anthropic）与 API key 渠道的深度风控、缓存与性能审查，形成四项处置结论：

1. **修复 `passthrough` API-key 通道 SSE 遥测盲区（代码改动 + 远端上线）**
   - 此前 `cpa-admission.py` 仅在命中受控 OAuth/coding-plan `lane` 时启用 `StreamErrorProbe`，导致 `passthrough` API-key 通道（如 `ai.input.im` 的 `gpt-6.2-astra`）的流式响应在日志中全部呈现 `first_text_ms=na`、`sse_bytes=0`、`usage_*=na`，无法度量 TTFT、缓存命中率或中段流式中断。
   - 改动后所有 `text/event-stream` 响应均经 `StreamErrorProbe` 检测并入账 `/healthz` 遥测，同时对 `lane is None` 保持零 lane 冷却副作用。
2. **为 8 个 Antigravity OAuth 凭据配置互质素数刷新错峰（远端凭据配置变更）**
   - 此前 8 个活跃 Antigravity 凭据未配置 `refresh_interval_seconds`，默认共用统一刷新周期，存在同 10 秒内集中刷新的风控指纹聚类风险。
   - 在备份优先前提下，为 8 个凭据分别写入互质素数 `refresh_interval_seconds`（`1399`、`1459`、`1523`、`1657`、`1709`、`1753`、`1787`、`1789` 秒，约 23.3–29.8 分钟），保持 `0600` 权限与其他字段不变。
3. **复核 `model_substitution` 热点凭据 `439a78c5c9cb566b`（零改动）**
   - 7 天容器日志取证显示 550/598（92.0%）为正常的 thinking 后缀规范化（`gemini-4-neon <== gemini-4-neon-xhigh` 324 次、`gemini-4-hydrogen <== gemini-4-hydrogen-xhigh` 226 次），A/B 变体分布于全部 6 个活跃凭据，非单号降智，不降权、不禁用。
4. **评估 `CLIProxyAPI v8.1.0` 升级（按仓库 72h 浸泡契约暂缓）**
   - 上游 `v8.1.0`（`6960763455c4ac3d4828fb1736aa5de2183ccecf`）发布于 `2026-11-22 02:48:47 +0800`；`scripts/remote/cpa-auto-update.sh` 强制 72 小时浸泡期（预计 `2026-11-25 02:49 +0800` 满期）。本次不绕过浸泡门禁升级容器镜像，也不修改本地只读外置仓 `D:\CODE\external\_shared\repos\CLIProxyAPI`。

## 仓库改动

- `494e7f8` — `fix(cpa): inspect passthrough SSE streams for telemetry and integrity`
  - `scripts/remote/cpa-admission.py`：`StreamErrorProbe` 接受 `lane: dict[str, Any] | None` 并覆盖全部 `text/event-stream` 响应，对 `passthrough` 通道采集 `first_text_ms`、`usage_in/cached/out/cost_usd`、`sse_bytes`、`sse_events`、`sse_terminal_seen`、`sse_text_finish` 及中段错误检测，不触发受控 lane 冷却。
  - `tests/test_cpa_admission.py`：新增 `passthrough` SSE 单元与本地 HTTP E2E 回归测试。
  - `scripts/remote/cpa-admission-integrity-pin.txt`：同步更新；随后被并行提交 `a8f8591` 合并演进至 `28542fc75880c1d5216ac7eaa8bc578cb4ef6e1f7f646d93f25772da9da21069`。

## 远端变更与脱敏清单

- **Admission 原子轮换**（`2026-11-22T12:26:33Z`）：上线 `28542fc75880c1d5216ac7eaa8bc578cb4ef6e1f7f646d93f25772da9da21069`（含 `494e7f8` 与并行合入的 `a8f8591`）；随后 `12:27:08Z` 并行护栏 apply 重建容器并保持同一 live hash。
- **Antigravity 刷新错峰**（`2026-11-22T12:20:25Z`，路径 `/opt/cliproxyapi/auth/antigravity-*.json`，权限 `0600`）：

| 脱敏凭据 ID（SHA-256 前 16 位） | `refresh_interval_seconds` | 容器重启刷新后留存核验（`12:27:09Z`） |
| --- | --- | --- |
| `6f80343fbd07e6d0` | `1399` | 保留、`0600` |
| `463ffe6f537bacf5` | `1459` | 保留、`0600` |
| `450dc450e2527c70` | `1523` | 保留、`0600` |
| `5c56694a5e0ee2bf` | `1657` | 保留、`0600` |
| `7b23fdc4fbd13649` | `1709` | 保留、`0600` |
| `82cc585881162376` | `1753` | 保留、`0600` |
| `ae5e80076b9f15e6` | `1787` | 保留、`0600` |
| `492eadcfae48720c` | `1789` | 保留、`0600` |

## 分层验证

| 层级 | 证据 | 结果 |
| --- | --- | --- |
| `repo_verified` | `494e7f8` 全量并行门禁 `scripts/run_gates.ps1 -RunParallel` | `1867 passed, 30 skipped, 896 subtests passed`；`CATALOG_CHECK=PASS`、build/bandit/ruff/mypy 全绿 |
| `repo_verified` | 合入 `a8f8591` 后 `pytest tests/test_cpa_admission.py tests/test_cpa_guardrails_script.py` | `432 passed, 3 skipped, 120 subtests passed` |
| `filesystem_projected` | `outputs/deploy-admission-passthrough-sse-20261122.ps1`（`-DryRun` + Apply） | `STAGED_CONFIG_LOAD_OK`、`REQUEST_GUARD_EXCLUSIVE_OK`、`ADMISSION_IDLE_OK`、`ADMISSION_PIN_ROTATED`、`ADMISSION_PASSTHROUGH_SSE_LIVE_OK`、`DEPLOY_OK` |
| `filesystem_projected` | Antigravity 8 凭据原子写入与热重载 | `POST_RELOAD_CHECK error_lines=0`、`STAGGER_COMPLETE_OK`；`12:27:09Z` CPA 重启刷新写回后 8/8 `refresh_interval_seconds` 与 `0600` 完整保留 |
| `host_loaded` | `scripts/cpa_bwg_guardrails.ps1 -Profile bwg` strict doctor | `DOCTOR_CONTRACT_OK`、17/17 `projection-drift MATCH`、`admission-integrity=OK`、`admission-health=OK`、`semantic-policy=OK`、`nginx-syntax=OK`；确认 live `routing.strategy=round-robin` |
| `controlled_live` | 非 OAuth `cpa-health.py cache-canary glm-5.3-flash chat` | `sample=1 hit_ratio=0.0000` -> `sample=2 hit_ratio=0.9956 (4096/4114)`、`HEALTH_OK` |
| `live_accepted`（passthrough SSE） | 自然流量 `request_id=db988b9d25a8477a82cfe2b84b815a1a`（`2026-11-22T12:28:36Z`，`lane=passthrough model=gpt-6.2-astra`） | `status=200`、`first_text_ms=7318`、`sse_bytes=2860`、`sse_events=9`、`sse_last_event=response.completed`、`sse_inspection_complete=true`、`sse_terminal_seen=true`、`sse_text_finish=stop`、`usage_in=5425`、`usage_cached=4653`；`/healthz` `telemetry.models["gpt-6.2-astra"]` `cache_hit_ratio=0.8577` |
| `pending_live_window`（Antigravity 错峰） | 首个稳态刷新周期（预计 `2026-11-22T12:50:28Z`–`12:56:59Z`） | 待自然窗口观测验证 10 秒聚类收敛至 1；因 `CLIProxyAPI v8.0.80` `auto_refresh_loop.go` 在 Antigravity JSON 仅存 `timestamp` 无 `last_refresh` 时重启瞬间会触发一次冷启动全量刷新，稳态周期错峰在内存 `LastRefreshedAt` 填充后生效 |

## 回滚

1. **Admission 网关回滚**：使用维护前备份 `/opt/cliproxyapi/backups/20261122T122633.229271937Z-admission-passthrough-sse-20261122`（或 `e16fb7e` 对应备份 `/root/cpa-guardrails-backup-20261122T122658.315434152Z`），在同一维护事务内恢复 `cpa-admission.py`、`cpa_request_guard.py` 与 `cpa-admission.sha256`，重启 `cpa-admission` 并执行 `cpa-admission-integrity-check` 与 strict doctor。
2. **Antigravity 刷新错峰回滚**：从 `/opt/cliproxyapi/backups/20261122T122025Z-antigravity-refresh-stagger`（`0700`）仅移除 `refresh_interval_seconds` 字段（勿直接覆盖已更新的 `access_token`/`timestamp`，避免回退有效令牌），随后确认容器热重载零报错。

## 边界

- 未发送任何 OAuth 合成生成探针，未轮换账号/IP/UA，未清冷却，未放宽 `request-retry=0` 或并发上限，未触碰 `zz`，未修改本地只读外置仓 `CLIProxyAPI`。
- `passthrough` SSE 自然流量验证仅证明 API-key 通道流式遥测与完整性采集生效；Antigravity 长期 OAuth 风控安全、429 率、以及首次稳态错峰周期需在后续自然流量窗口中持续观察。
