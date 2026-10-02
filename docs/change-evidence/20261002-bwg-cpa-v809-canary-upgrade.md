# 2026-10-02 BWG CPA v8.0.9 手动 canary 升级证据

## 目标与范围

- 目标：将 `bwg` 的 CPA 从 v8.0.8 升级到 v8.0.9（用户当次授权；该 release
  发布于 2026-10-01 21:36 UTC，未满 updater 的 72h 成熟期，每日 updater 在
  2026-10-05 前不会自动收编，故走手动 canary）。
- 范围：仅 `compose.yml` 的镜像声明；`config.yaml`、OAuth 凭据、Nginx、
  admission、路由清单零改动。不触碰 `zz`，不轮换 key，不新增上游重试。

## 上游变更评估（v8.0.8...v8.0.9，共 2 个 commit）

- `3ebee06` `fix(interactions)`：Responses 翻译层桥接 apply_patch tool call
  的流式 delta/snapshot、step index 与错误传播。属翻译层修复，默认生效；
  客户端自带的 apply_patch 调用受益，服务端目录通告面不变。
- `63c04b4` `feat(config)`：新增 `client.codex` 配置段。`enable-apply-patch`
  默认 `false`（不写该段则 `/v1/models?client_version=` 目录不通告 freeform
  apply_patch 工具类型）；`optimize-multi-agent-v2` 旧路径（flat /
  `oauth.providers.codex` / `providers.codex`）仍被兼容读取，非静默忽略。
- 无破坏性变更；release notes 与 commit 范围内无风控/限流/OAuth 相关改动。

**风险决策**：不写入 `client.codex` 段。远端 `config.yaml` 预检确认无
`client:` 段且无 legacy `optimize-multi-agent-v2` 键
（`NO_CLIENT_SECTION_NO_LEGACY_KEYS`），升级后行为面与 v8.0.8 逐字段一致，
仅获得翻译层修复。新特性保持 opt-in 不开启。

## 升级事务

事务脚本逐行复刻 `/opt/cliproxyapi/auto-update.sh` 语义，仅固定目标版本：
`flock -n /run/vps-ssh-launcher-maintenance.lock` 互斥 → `secure_error_dumps`
权限收紧 → 前置 `cpa-health.py generation` 门（`glm-5.3-flash` 单次冒烟，
GLM lane，不消耗 OAuth 配额）→ 备份健康（700 / ≥2 GiB）→ 备份 `compose.yml`
→ digest-pinned pull → 正则原子替换镜像声明（替换计数 ≠1 即 REFUSE）→
`docker compose config --quiet && up -d --pull never` → 升级后 generation
验收 → 失败自动回滚（恢复备份 compose + `up -d --pull never` + readiness）。

- 执行方式：远端落盘 `/root/cpa-canary-v809.sh`（`bash -n` 通过），
  `setsid nohup` 后台执行，规避 SSH 空闲超时；完成后临时脚本与输出已删除，
  `/opt/cliproxyapi/manual-canary-v809.log` 留审计。
- 结果：`2026-10-02T00:56:36Z OK: updated v8.0.8 -> v8.0.9
  digest=sha256:3ee530683af98a5567e495648b687c2f45f66e9ff491bbe16eafc8fe309ea150
  backup=/opt/cliproxyapi/backups/20261002T005630.872159319Z-from-v8.0.8`；
  前置与升级后 generation 均 `HEALTH_OK`，回滚路径未触发。
- 清理：本次不执行 prune；v8.0.8 镜像保留为本地回滚集，由后续 updater 在
  成功验收后按既有保留策略（running + retained backups）收敛。

## Fixture 模拟验收（与生产隔离）

复刻 v8.0.7/v8.0.8 的 canary+fixture 双验收惯例：一次性 mount+net namespace
内把 mktemp 目录 bind-mount 到 `/opt/cliproxyapi`（仅命名空间内），运行
v8.0.9 镜像实体二进制（sha256 前缀 `5a1f11d9a1bcefcc`）+ 生产 `auto-update.sh`
原文 + stub docker/GitHub/Docker Hub。生产进程、挂载、网络、凭据零接触。

- 语义场景：目录契约（含墓碑名 fail-closed）→ overload（单次上游调用 +
  marker，`stream-bootstrap-buffering=false` 下期望 200）→ cooldown（零上游
  调用）→ 62s 后 recovered（200 + completed + 恰好 1 次上游调用）→
  `cpa-health.py generation` 实体验收。
- updater 四场景（stub 元数据 `v0.0.1→v0.0.2`，真实 CPA 进程与健康检查）：
  `start_fail` exit 1 + compose 回滚恢复 + ROLLBACK logged；`model_exposure`
  exit 1 + 回滚恢复；`transient` exit 10 + UNVERIFIED + compose 保留；
  `success` exit 0——生产 updater 与 v8.0.9 二进制组合的行为契约全部符合。
- 结果：`ACCEPTANCE_RESULT=PASS`、`ACCEPTANCE_EXIT=0`、`CLEANUP_OK`（无
  `/opt/cliproxyapi/CLIProxyAPI` 路径进程泄漏，fixture 目录已删）；转录在
  `outputs/cpa-v809-fixture-transcript.txt`（按惯例不入库）。远端临时脚本
  （acceptance 两脚本 + fixture runner）验收后即删。

## 验证与风控复核

- strict doctor（`scripts/cpa_bwg_guardrails.ps1 -Profile bwg`，脱敏）：
  `DOCTOR_CONTRACT_OK`（exit 0）。
- `==container==`：`running restart=0`，
  `image=eceasy/cli-proxy-api:v8.0.9@sha256:3ee53068...`。
- `==oauth-monitor==`：`oauth_monitor=OK`，`oauth_days_left=3`（已进入
  lead24h 自动刷新窗口，刷新链路健康：`oauth_refresh_failures_7d=0`），
  `oauth_quarantine=none`。
- `==cooldown-state==`：`cooldown_next_retry_after=none`、
  `catalog_oauth_aliases=gpt-5.6-luna,gpt-6-luna,gpt-6.1-sol`、
  `catalog_oauth_missing=none`、`luna_state=available`——重启后 OAuth 路由
  全别名在册，无冷却（`save-cooldown-status=false` 语义下重启即清，预期）。
- `==client-model-catalog==`：12 个登记 ID 全部在册，
  `MODEL_IDS_UNKNOWN=none`。
- admission：`admission-health=OK`、`admission-service=enabled-active`、
  loopback 断言通过、`legacy-admission=absent`；
  `==projection-drift==` 3×MATCH。
- `==gateway-statuses-current-log-24h==`：无 5xx、无 429、
  `admission_429_shape={}`、`retry_after_classes` 全 `absent`、
  499 count=0、`retained_overload_request_files=0`——升级重启窗口未产生
  异常流量信号。
- timer 语义自洽：次日 updater 将报告
  `CANDIDATE current=v8.0.9 target=v8.0.9 soak=72h`（无成熟候选 → 仅本地
  readiness，无生成、无动作、无回滚）。

## 边界与后续

- 本次升级不构成 provider 侧风控/配额变化的证明；账号级判据仍以 403/quota
  响应与 OAuth 刷新失败为准。
- `enable-apply-patch` 维持默认关闭；若后续 desktop Codex 出现 apply_patch
  工具通告需求，按目录变更清单单独评审后再开启。
