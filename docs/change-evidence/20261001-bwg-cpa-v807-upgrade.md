# bwg CPA v8.0.7 手动 canary 升级收据（2026-10-01）

## 授权与目标

- 用户当次明确授权：在 `bwg` VPS 上把 CPA（CLIProxyAPI）升级到 GitHub release `v8.0.7`，并持续执行必要的风控检查与可逆修复。
- 范围：仅 `bwg` 的 CPA 容器；不触碰 `zz`、OAuth 凭据、随机公网路径、provider 配置、Nginx、admission 或本仓外置源码。
- 72 小时成熟度等待：该 release 发布不足 72 小时，按用户的明确版本目标采用一次性、可回滚的手动 canary；未启用重复重试或压测。

## 发布物与风险审查

- GitHub release：`v8.0.7`，发布时间 `2026-09-30T19:14:25Z`。
- 发布说明中的 CPA/Codex 改动为工具整数归一化、首个 payload 前上游断流归类为 bad gateway、以及按目标 executor 传递兼容转换和 token counting。
- GitHub `checksums.txt`：`CLIProxyAPI_8.0.7_linux_amd64.tar.gz` 的公开 SHA-256 为
  `89b97d522fb7dd7704545ef878ca61ceffe63bb166ee3156fe90576c1f443fb1`。
- 本次没有改变 UA、请求头伪装、账号轮换、刷新节奏、重试放大、入口并发或 provider 路由；继续保留 CPA `request-retry=0`、`save-cooldown-status=false`、`codex.stream-bootstrap-buffering=false`。

## 远端变更

- 升级前镜像：`eceasy/cli-proxy-api:v8.0.5@sha256:a3ffe52b…`。
- 变更方式：先通过 `readiness` 与单目标 `generation` 门禁，再备份 compose，按不可变 digest 拉取并只替换 compose 的 image 行，执行 `docker compose config --quiet && docker compose up -d --pull never`。
- 目标镜像：`eceasy/cli-proxy-api:v8.0.7@sha256:35779b5d3d6f19a5bc20d62cc79394a5b7eb4d7b74689eb1ce8b8de269fe4e17`。
- 备份：`/opt/cliproxyapi/backups/20261001T015403.267274389Z-canary-from-v8.0.5`，目录权限 `700`，保留旧 compose 与本地回滚镜像。
- canary 收据：`HEALTH_OK`（前置 readiness/generation）→ pull → 容器重建 → `HEALTH_OK`（后置 generation）→ `CANARY_OK`，时间 `2026-10-01T01:54:12Z`。

## 复验

- 容器当前：`running`，`restart=0`，启动时间 `2026-10-01T01:54:10Z`。
- 容器内版本：`CLIProxyAPI Version: v8.0.7, Commit: 97f244b, BuiltAt: 2026-09-30T19:15:01Z`；与 release 说明中的 commit 对齐。
- 2026-10-01T01:58:52Z strict doctor：`DOCTOR_CONTRACT_OK`。
- projection：`auto-update.sh`、`cpa-health.py`、`cpa_policy.py`、路由清单、admission、Nginx 等全部 `MATCH`；语法、端口、Nginx、fail2ban、权限和模型目录门禁通过。
- 风控读数：OAuth monitor `OK`（未过期、7 日刷新失败 `0`），OAuth quarantine `none`，目录 `luna_state=available`，无 `.cds` 冷却文件，模型替换告警 `0`，当前日志没有 5xx/429 形态。
- 受控请求边界：canary 使用已有的单目标非 OAuth generation 门禁；没有为追求“通过”而重复发送 OAuth 请求或制造负载。自然用户流量与 provider 长期资格仍不由 doctor 或一次 200 推断。

## 回滚

如需仅回滚本次切片：恢复上述备份中的 `compose.yml`，执行
`docker compose up -d --pull never`，再运行 `python3 /opt/cliproxyapi/cpa-health.py readiness`；失败时保留现场并以 `ROLLBACK_FAILED` 报告，不删除 `auth/` 或错误转储。

## 结论与边界

- `repo_verified`：本仓 guardrail 源文件与远端投影保持 `MATCH`。
- `filesystem_projected`：v8.0.7 digest、compose、备份和远端文件已读回。
- `host_loaded`：容器以 v8.0.7 运行，进程 banner 与 release commit 对齐，restart=0。
- `controlled_live_replay`：单目标 generation canary 前后均 `HEALTH_OK`。
- `live_accepted`：不宣称；上游账号配额、封禁、长期限流和自然桌面流量需在正常低频使用中单独观察。
