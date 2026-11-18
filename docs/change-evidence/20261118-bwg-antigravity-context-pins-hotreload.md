# BWG CPA Antigravity 模型上下文锚点补齐与保 inode 热加载（2026-11-18）

## 背景

1. `scripts/remote/cpa_provider_routes.json` 的 `oauth_routes`（`antigravity-oauth`）与本地 `config/codex-model-context-overrides.json` 已声明 `gemini-4-carbon-high`、`gemini-4-argon-high`（均为 `1048576`），但 `oauth_settings.antigravity` 与远端 `/opt/cliproxyapi/config.yaml` 的 `oauth.settings.antigravity` 此前仅钉住 3 个模型，导致远端 `MODEL_CONTEXT_CATALOG` 仅覆盖 15/17 个活跃模型，`scripts/cpa_catalog_check.py` 对 `oauth_settings` 缺项采用了跳过而非 fail-closed。
2. `scripts/cpa_cache_reset_probe.py` 此前直接将 `usage_in` 当作总输入并在小于 `min_prefix_tokens` 时过滤，导致把未命中增量写入 `usage_in`、缓存命中写入 `usage_cached` 的加和型渠道在热命中轮次被漏计或比率失真，且缺少 runbook 声明的 `--ssh-profile` / `--hours` 入口。

## 变更

- **仓库契约与门禁**：
  - `scripts/remote/cpa_provider_routes.json`：在 `oauth_settings.antigravity` 中补齐 `gemini-4-carbon-high`（`1048576`）与 `gemini-4-argon-high`（`1048576`）。
  - `scripts/cpa_catalog_check.py` + `tests/test_cpa_catalog_check.py`：对 `config/codex-model-context-overrides.json` 中非退役模型在 manifest 缺失 `max-context-length` 的情况改为 fail-closed 报错。
  - `scripts/cpa_cache_reset_probe.py` + `tests/test_cpa_cache_reset_probe.py`：对齐 `usage_cached > usage_in` 加和型 token 判据（`prompt_tokens` / `uncached_tokens`），补齐 `--ssh-profile` 与 `--hours` 参数支持。
- **远端 `bwg` 零中断投影（2026-11-18 02:14:44Z）**：
  - 因当前会话在线流经 `bwg`（`antigravity-oauth` 车道 `inflight=1`），按无中断约束不触发容器重启，在 `/run/vps-ssh-launcher-maintenance.lock` 排他锁内执行：
  - 快照备份：`/opt/cliproxyapi/backups/20261118T021444Z-antigravity-context-pins`（`0700`；另含前一次命令参数预演自动回滚留存的同基线快照 `20261118T021215Z-antigravity-context-pins`），原 `cpa_provider_routes.json` SHA-256 为 `5ad930776573bf2af1aa6ae5db65508b938eab86a44b5dfacc85d12dc9482084`。
  - 更新 `/opt/cliproxyapi/cpa_provider_routes.json`（`0644`，LF 归一，SHA-256 `247332d2eeae3806de16037a11a5c011ed86398018eba879a02f45f65b002aa6`）。
  - 先对候选配置跑 `cpa_policy.py` 取得 `POLICY_OK`，再以 `r+b` 保 inode 原地更新 `/opt/cliproxyapi/config.yaml`（保持 `st_ino=525497`、`0600`），保留全部既有 OAuth 凭据（26 项）与 API key 渠道（4 项），零删除、零轮换、零断流。

## 验证回执

- `cpa_policy.py /opt/cliproxyapi/config.yaml`：`POLICY_OK`
- CPA watcher 热加载日志：`oauth-settings[antigravity]: updated (3 -> 5 entries)` -> `config successfully reloaded, triggering client reload` -> `server clients and configuration updated: 30 clients (26 auth entries + ... + 4 OpenAI-compat)`
- `cpa-health.py readiness`：`HEALTH_OK`
- `/v1/models?client_version=0.154.0` 上下文目录：`CATALOG_COUNT=17/17`，`gemini-4-argon-high:1048576`、`gemini-4-carbon-high:1048576` 均已纳入 `MODEL_CONTEXT_CATALOG`，零漂移。
- `cpa_cache_reset_probe.py --ssh-profile bwg --hours 48`：`exit=0`（`eligible_cache_hit_ratio=0.9669`）。

## 回滚

- 远端：在维护锁下从 `/opt/cliproxyapi/backups/20261118T021444Z-antigravity-context-pins` 将 `cpa_provider_routes.json` 原子拷回，并以 `r+b` 保 inode 写回原 `config.yaml` 触发 watcher 热回滚。
- 仓库：`git revert` 本次提交后同步恢复远端这两个文件。

ACCEPTANCE_RESULT=PASS
