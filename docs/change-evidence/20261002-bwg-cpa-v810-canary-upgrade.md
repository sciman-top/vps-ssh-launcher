# 2026-10-02 BWG CPA v8.0.10 手动 canary 升级证据

## 目标与范围

- 目标：将 `bwg` 的 CPA 从 v8.0.9 升级到 v8.0.10（用户当次授权；该 release
  发布于 2026-10-02 03:05 UTC、Docker tag 03:08 UTC，未满 updater 的 72h
  成熟期，故走手动 canary，与 v8.0.9 同一套流程）。
- 范围：仅 `compose.yml` 的镜像声明；`config.yaml`、OAuth 凭据、Nginx、
  admission、路由清单零改动。不触碰 `zz`，不轮换 key，不新增上游重试。

## 上游变更评估（v8.0.9...v8.0.10，共 6 个 commit）

- `028f6a1` `docs(readme)`：README provider 模型清单刷新（Muse Code、
  Devin），纯文档。
- `f3fd2f2`+`9f35c1d`+`6fecc6e`（PR #6271）：Gemini 签名冗余日志抑制与
  sanitize 逻辑重构。本仓目录无 Gemini lane，语义上不触达。
- `cf3102c` `refactor(executor)`：翻译逻辑优化与 plugin invocation 处理。
  唯一需要盯的变更面：OAuth `/v1/responses` 翻译层。升级后真实桌面流量
  （26.5s 流式 /v1/responses）200 正常返回（见"验证"）。
- `2783e10` `fix(executor)`：antigravity 流 usage 先发布；未使用该 provider。
- 无新配置项、无破坏性变更、无 OAuth/Codex/chatgpt 后端行为改动。

**风险决策**：`config.yaml` 保持零改动（预检确认无 `client:` 段，
`NO_CLIENT_SECTION`），v8.0.9 引入的 `client.codex`/`enable-apply-patch`
继续 opt-in 不开启；升级后行为面与 v8.0.9 逐字段一致。不探针 OAuth lane
（Plus 限额周期内，维护既有纪律），验收走 GLM lane 与 fixture。

## 前置波折：canary 前置门正确 DEFER 一次（未动生产）

- 首次启动（13:54:33 UTC）前置 generation 门返回 exit 10
  `UPSTREAM_UNAVAILABLE`，事务按设计 DEFER，镜像未变。
- 归因：13:30:19/13:31:31 UTC ai.input.im 上游对 `gpt-6.1-sol-input`
  返回 502 `"Upstream access forbidden"`（上游用户分组权限抖动，已知
  涨落模式），CPA 将该模型从目录视图摘除；generation 门的 required 集
  含该非 optional 别名，门在目录完整性检查即返回 10，**未发出任何生成
  请求**（容器日志无对应 POST，41ms 内失败的是同期真实桌面大请求）。
- 直探上游 `/v1/models` 确认 ai.input.im 仍挂 `gpt-6.1-sol`（13 模型），
  即 CPA 目录视图滞后而非上游下架；v8.0.9 运行态对 openai-compat
  provider 无周期性上游目录重取（config 无 refresh 键、12h 日志无
  per-provider 刷新记录），视图不会自行恢复。
- 处置：`docker restart cli-proxy-api`（14:14 前置步骤，凭据/配置落盘、
  停机秒级），重启后 `/v1/models` 恢复 12 个登记 ID 全在册
  （`gpt-6.1-sol-input` 回归），readiness HEALTH_OK。
- 结论：门的行为正确（目录契约不完整时拒绝升级事务），本次 DEFER 不是
  false alarm；"openai-compat 目录塌缩需重启重注册"记为运维 watch item，
  不在本任务内改门或改 optional 语义。

## 升级事务

事务脚本逐行复刻 `/opt/cliproxyapi/auto-update.sh` 语义，仅固定目标版本：
`flock -n /run/vps-ssh-launcher-maintenance.lock` 互斥 → compose 现值必须
精确等于 v8.0.9@3ee53068 钉（否则 REFUSE）→ error-dumps 权限收紧 → 前置
`cpa-health.py generation` 门（`glm-5.3-flash` 单次冒烟，GLM lane，不消耗
OAuth 配额）→ 备份健康（700 / ≥2 GiB，实测 free 32 GB）→ 备份 `compose.yml`
→ digest-pinned pull（`v8.0.10@sha256:0b007a6a...`，Docker Hub tag 元数据
预核）→ 正则原子替换镜像声明（替换计数 ≠1 即 REFUSE）→
`docker compose config --quiet && up -d --pull never` → 升级后 generation
验收 → 失败自动回滚（恢复备份 compose + `up -d --pull never` + readiness）。

- 执行方式：脚本本地生成（LF）、base64 落盘 `/root/cpa-canary-v810.sh`
  （`bash -n` 通过），`setsid nohup` 后台执行规避 SSH 空闲超时；完成后
  临时脚本与输出已删，`/opt/cliproxyapi/manual-canary-v810.log` 留审计。
- 结果：`2026-10-02T14:14:20Z CANARY_START` → `14:14:26Z OK: updated
  v8.0.9 -> v8.0.10 digest=sha256:0b007a6abd15aec1f5314908417ceab99f049f75353e3703f52f0968747f6110
  backup=/opt/cliproxyapi/backups/20261002T141422.461275045Z-from-v8.0.9`；
  前置与升级后 generation 均 `HEALTH_OK`，回滚路径未触发。
- 清理：本次不执行 prune；v8.0.9 镜像保留为本地回滚集，由后续 updater 在
  成功验收后按既有保留策略（running + retained backups）收敛。

## Fixture 模拟验收（与生产隔离，先于 canary 完成）

复刻 v8.0.7/v8.0.8/v8.0.9 的 canary+fixture 双验收惯例：一次性
mount+net namespace 内把 mktemp 目录 bind-mount 到 `/opt/cliproxyapi`
（仅命名空间内），运行 v8.0.10 镜像实体二进制（sha256 前缀
`5f46577bf2cd639f`）+ 生产 `auto-update.sh` 原文 + stub docker/GitHub/
Docker Hub。生产进程、挂载、网络、凭据零接触；acceptance 两脚本
（`cpa-acceptance.py`/`cpa-update-acceptance.py`）版本无关、sha256 与
仓库原文逐字节一致直接使用，runner 见 `outputs/cpa-v810-fixture-run.sh`。

- 语义场景：目录契约（含墓碑名 fail-closed）→ overload（单次上游调用 +
  marker，`stream-bootstrap-buffering=false` 下 200）→ cooldown（零上游
  调用）→ 62s 后 recovered（200 + completed + 恰好 1 次上游调用 + 同
  进程）→ `cpa-health.py generation` 实体验收 `HEALTH_OK`。
- updater 四场景（stub 元数据 `v0.0.1→v0.0.2`，真实 CPA 进程与健康检查）：
  `start_fail` exit 1 + compose 回滚恢复 + ROLLBACK logged；`model_exposure`
  exit 1 + 回滚恢复；`transient` exit 10 + UNVERIFIED + compose 保留；
  `success` exit 0——生产 updater 与 v8.0.10 二进制组合的行为契约全部符合。
- 结果：`ACCEPTANCE_RESULT=PASS`、`ACCEPTANCE_EXIT=0`、`CLEANUP_OK`（无
  `/opt/cliproxyapi/CLIProxyAPI` 绝对路径进程泄漏）；转录
  `outputs/cpa-v810-fixture-transcript.txt`（不入库）。远端临时脚本验收
  后即删。

## 验证与风控复核

- strict doctor 基线（升级前，v8.0.9）：`DOCTOR_CONTRACT_OK`；
  strict doctor 复核（升级后）：`DOCTOR_CONTRACT_OK`。
- `==container==`：`running restart=0`，
  `image=eceasy/cli-proxy-api:v8.0.10@sha256:0b007a6a...`。
- `==oauth-monitor==`：`oauth_monitor=OK`、`oauth_days_left=3`、
  `oauth_quarantine` 无异常；`==cooldown-state==`：
  `cooldown_next_retry_after=none`、`luna_state=available`——重启后 OAuth
  路由全别名在册（`gpt-5.6-luna` 本轮已随上游恢复回归目录，12 ID 全在册，
  `MODEL_IDS_UNKNOWN=none`）。
- `==projection-drift==` 9×MATCH（auto-update.sh / cpa-health.py /
  cpa_policy.py / cpa_provider_routes.json / cpa-admission.{py,json} /
  cpa-admission.service / cpa-gateway.conf×2）。
- `==gateway-statuses-current-log-24h==`：升级窗口（14:14 UTC）后小时段
  仅 3×503 为重启数秒内的连接抖动，随后 200 恢复；24h 窗口内的 502/503
  （ai.input.im `gpt-6.1-sol-input` 2×502 权限抖动、OAuth sol 晚高峰
  `server_is_overloaded` 503）均为升级前已存在的上游容量/权限事件，
  429 为既有 nginx 限流形状、无新增模式；`model_substitution_warnings_7d=0`。
- 实战流量：升级完成后数分钟内 `/v1/responses`（26.5s 流式）与
  `/v1/chat/completions` 均 200——OAuth 翻译层在 v8.0.10 上的首个真实
  流量样本正常。
- timer 语义自洽：次日 updater 将报告
  `CANDIDATE current=v8.0.10 target=v8.0.10 soak=72h`（无成熟候选 → 仅
  本地 readiness，无生成、无动作、无回滚）。

## 边界与后续

- 本次升级不构成 provider 侧风控/配额变化的证明；账号级判据仍以 403/quota
  响应与 OAuth 刷新失败为准。OAuth lane 的翻译层回归（`cf3102c`）以真实
  桌面流量持续观察即可，无需专项探针。
- `enable-apply-patch` 维持默认关闭；`client.codex` 段维持不写入。
- 运维 watch item（不在本任务内修复）：openai-compat provider 的目录视图
  在上游权限抖动后被摘除后不会自行恢复，需容器重启重注册；generation 门
  会如实 DEFER，属预期保护。
- 既有待办不变：`gpt-5.6-luna` 的生成级 LIVE_ACCEPTED 待 ≈10/4 傍晚 Plus
  周限额重置后单发补验，重置前不探针。
