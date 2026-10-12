# 2026-10-12 BWG CPA v8.0.24 canary 升级证据

## 目标与范围

- 目标：`bwg` 的 CPA 从 v8.0.23 升级到 v8.0.24（v8.0.24 发布于
  2026-10-11 22:41Z，距升级约 13h，不足 72h 成熟期；用户当次明确指令
  直接升级，与 v8.0.20/21/23 先例同型）。
- 范围：**仅 `compose.yml` 镜像声明**。`config.yaml`、OAuth 凭据、Nginx、
  admission（5273f59e 代）、路由清单零改动，零配置新增。不触碰 `zz`，
  不轮换 key。

## 上游变更评估（v8.0.23...v8.0.24）

Release notes（2026-10-11 22:39Z，tag `091ff27`）：

- Claude `tool_search` 支持（Responses API + `upstream.claude.tool-search`
  配置开关）与 native tool search 广告口径收紧（仅原生支持的 Claude
  路由）——本栈无 Claude 凭据，不触达。
- Auth：**keep upstream error cooldown on canceled stream contexts**——
  取消的流上下文不再丢失上游错误冷却。对本栈是封号/限流保护正向修复：
  OAuth shed 窗口期客户端取消不会把正在冷却的上游重新暴露给后续请求。
- Codex：chat completions 超长 tool call ID 截短——OAuth/codex 通道兼容性
  修复，正向。
- Claude executor 忽略无效/空 `cache_control` 条目；deferred tool_search
  工具剥离 `cache_control`——无 Claude 凭据，不触达。
- Antigravity 能力提示持久化、Devin/Meta getter 重构、Gemini function
  response media 尾随 user turn——本栈无对应凭据，不触达。
- 无 breaking change、无既有 config schema 迁移要求；新增开关均默认关闭
  且仅作用于本栈不存在的 provider。零配置改动即升级。

## 夹具修复（本次入库的唯一代码改动）

fixture 验收首轮在 `actual_health`（`cpa-health.py generation`）以
exit 20（LOCAL_CONTRACT_FAILED，stderr 空）失败，v8.0.23 同夹具昨日 PASS。
定位链：干净夹具诊断（目录差集空、glm smoke 契约 OK）→
acceptance 前奏完整复刻复现 exit 20 → 进程内 `check()` 复现 →
`_loopback_request` 暴露 `KeyError('access.api-keys')`。

- 根因：`6d88f5d`（2026-10-11 13:55 +0800，CPA v8 原生 config schema
  迁移）把 `cpa-health.py` 的 client key 读取迁到 v8 布局
  `access.api-keys`，但 `scripts/remote/cpa-acceptance.py` 夹具 config
  仍写旧版顶层 `api-keys` 列表。KeyError 被目录循环的
  `except (KeyError, ...)` 吞成静默 20。v8.0.23 的 fixture 验收跑在
  `6d88f5d` 之前，故未暴露；HEAD 起任何版本的 fixture 验收都会挂在
  同一点（含 updater 四场景的 pre-state 门）。
- 修复：夹具 config 改为 `"access": {"api-keys": ["fixture-client"]}`
  （与生产迁移后布局一致；`_channel_enabled` 对无 v8 api-keys 映射的
  配置保持 fixture 兼容回退 True，无需其它改动）。二进制对旧版
  provider 段的兼容已由 `.cds` 落盘等行为证据确认，无需迁移。
- 测试：`pytest tests/test_cpa_updater_apply.py -k acceptance` 3 passed
  + 6 subtests；full gates 558 passed + 1 skipped
  （GBK 警告为本机已知环境特性）+ bandit/ruff/format/mypy 全绿。
- 教训：v8 schema 迁移类切片的"配置写出方"与"配置读取方"在同一提交内
  未同步收敛，静态测试未覆盖夹具 config 与 health 读取端的键形状耦合；
  后续 schema 迁移应把夹具 config 形状纳入同片验收。

## 事务前置

- digest 三信源一致：本机（经代理 registry-1.docker.io）、VPS（直连
  registry）、Docker Hub API 的 `v8.0.24` manifest 均为
  `sha256:302807246399682928f6dcf5a6f52032024ebffaf0e9c8bdeb05a7e235028d6f`。
- 前置只读探针（`outputs/probe-preflight-v8024.ps1`）：compose 钉 =
  `v8.0.23@sha256:fc250aaf…`（与 10-11 证据一致）、容器 running
  restart=0、admission pin `5273f59e`（文件=运行代码）、备份目录无并行
  会话新痕迹、`cpa-update` 无 pending、磁盘 21%。前置严格 doctor
  `DOCTOR_CONTRACT_OK`。
- fixture 验收（`outputs/fixture-v8020.ps1 -ImageRef <digest> -Tag v8024`，
  收据 `outputs/fixture-v8024.txt`；夹具修复后重跑）：repo acceptance
  脚本分块上传 sha 双端校验 + `py_compile` 通过；v8.0.24 实体二进制
  （digest-pinned `docker create`+`docker cp` 抠出，binary sha 前缀
  `912c741501085bf1`）在 `unshare --mount --net --fork` 私有 namespace
  内运行：目录契约（裸目录 10 ID 在册）→ admission 三阶段
  （overload 200+marker+1 上游调用 / cooldown 503+0 上游调用 /
  62s 后 recovered 200+completed）→ `actual_health` exit 0 → updater
  四场景 `start_fail` exit=1+还原、`model_exposure` exit=1+还原、
  `transient` exit=10+UNVERIFIED 落日志、`success` exit=0。
  `ACCEPTANCE_RESULT=PASS`、`ACCEPTANCE_EXIT=0`、`CLEANUP_OK`。

## 升级事务

`outputs/cpa-v8024-canary.sh`（本地 LF sha256
`7c7d593ceef21c3349803ce113eb21b6b89d5991c6404ad0948ec4ac926f8d9d`，
`bash -n` 通过），经 `outputs/deploy-cpa-v8024-canary.ps1` 以
`-Profile bwg` 注入 `VPS_SSH_LAUNCHER_PROFILE=bwg` 执行。保留
`cpa-auto-update.sh` 全部本地安全栅栏：flock 共享维护锁 → compose 现值
精确钉校验（`v8.0.23@fc250aaf…`）→ error-dump 收权 → 前置 generation
门 → 磁盘水位 → 备份 compose → digest-pinned pull（fixture 已预拉取）→
原子单行替换 → `docker compose config --quiet && up -d --pull never` →
后置 generation 门 → 任一失败自动回滚（INT/TERM 显式 130/143）。不 prune。

- 一次通过：04:04:53Z `CANARY_START` → 04:04:59Z
  `OK: updated v8.0.23 -> v8.0.24`（约 6 秒），前后 generation 门均
  HEALTH_OK，回滚未触发。
- 备份：`backups/20261012T040455.886900154Z-from-v8.0.23-canary-v8024/`。
- 回滚集：v8.0.23 镜像（`fc250aaf`）保留在本地镜像列表，未 prune。

## 验证与风控复核

- **post doctor**：容器 `v8.0.24@30280724` `restart=0`、
  `MODEL_IDS_UNKNOWN=none`、投影全 `MATCH`、`admission-health=OK`、
  `gateway-443-lane=OK`、`model_substitution_warnings_7d=0`、
  `DOCTOR_CONTRACT_OK`（`outputs/doctor-20261012-post-v8024.txt`）。
- **lane 验证**（8317 单发，`outputs/probe-minimatrix-v8024.ps1` 等）：
  - `deepseek-v4.1-flash` 200/stop/OK 1.87s；`gpt-5.6-terra`
    200/stop/OK 5.87s；`glm-5.3-flash` 由 cache-canary 覆盖
    （200/stop/OK ×2）。
  - `gpt-6-astra`/`gpt-6.1-sol-input` 502，错误体为 ai.input.im 渠道方
    自己的 `{"message":"Upstream access forbidden, please contact
    administrator","type":"upstream_error"}`。**直连同 key 绕过 CPA
    复现同一 502**，且别名的 404 `model_not_found` 证明 key 认证有效
    （无效 key 应为 401/403）——渠道侧账号组级模型禁用/故障，
    **非升级回归**；CPA 粘性再隐藏按设计把故障模型摘出目录，自然流量
    自动避开。与 10-10 起 astra-ciii 慢性 502 同渠道谱系。
    渠道复查（升级后约 2h，`outputs/probe-channel-recheck.ps1`）：
    两模型仍 502 forbidden，事件持续中；完整 quality-canary 矩阵
    exit 0 的干净收口待渠道恢复后补跑（每日观察任务盯守）。
  - quality-canary 契约为 transient 单发不重试，astra 首发即停属设计
    行为；失败模型自隐藏后目录收敛。
- **受控 OAuth 回放**（真实桌面入口 `fq.sciman.top:8443` 随机路径单发，
  `outputs/probe-live-fq-sol-20261012.txt`）：`gpt-6.1-sol` stream，
  `HTTP=200`、无 `X-CPA-Admission-Reason`、无 `Retry-After`，但流 3 事件
  后停滞无 completed。journal 对账
  `upstream_result status=200 capacity=true waited_ms=0`（half-open
  lane probe 穿透一发）——既有 OAuth 账号级 shed 形态（luna/sol 窗口），
  非升级回归；lane 冷却按设计再封锁。桌面侧缓解口径不变：窗口期切
  `gpt-5.6-terra` 或 glm/deepseek。
- **缓存命中率**（`cpa-health.py cache-canary` 双样本，GLM lane）：
  样本 1 冷启动 `hit_ratio=0.0000`（升级重启后 LCP 缓存 miss+首次写入，
  属预期），样本 2 `cache_read_tokens=4096/4114` →
  `hit_ratio=0.9956`，与 v8.0.20/21/23 基线逐位一致。零配置改动；
  fill-first + session-affinity 未动。（DeepSeek lane 的 0.9579 基线
  口径自 085ff35 起 cache-canary 收窄为 GLM 专用，本次不测。）
- **吐字/TTFT**：健康 lane 单发 1.9-5.9s，处于既有健康区间；"吐字缓慢"
  既有定案（上游生成主导、本地占比 3-4%）不变。
- **降智指标**：`model_substitution_warnings_7d=0`（升级前后 doctor
  一致）。
- **风控台账**：v8.0.24 的取消流冷却保留是封号/限流保护正向；本栈
  OAuth 账号级 shed 与 ai.input.im 渠道禁用均为上游侧事件，本侧无新增
  请求放大（quality-canary 单发不重试、诊断总账见下）。
- **探针预算总账**（真实 provider generation，升级后共 11 发）：
  - 经 CPA：astra 502 ×3（quality-canary 首发 1 + 单发复验 1 +
    约 2h 后渠道复查 1，渠道侧 forbidden）、sol-input 502 ×1（渠道
    复查）、deepseek-v4.1-flash 200 ×1、gpt-5.6-terra 200 ×1、
    glm-5.3-flash 200 ×2（cache-canary）、OAuth `gpt-6.1-sol` ×1
    （200+capacity=true shed）；
  - 直连 ai.input.im 对照（绕过 CPA，同 key）：astra 502 ×1、
    sol-input 上游名 404 `model_not_found` ×1（证 key 认证有效）；
  - 其中上游侧失败 7 发均为渠道/账号级外部事件；OAuth lane 恰好 1 发。
    fixture 全程合成上游，零真实 provider 流量。收据：
    `outputs/post-v8024-probes*.txt`、`outputs/cache-canary-v8024.txt`、
    `outputs/probe-live-fq-sol-20261012.txt`。

## 自然流量窗

升级窗 04:04:57Z→04:36Z（约 31 分钟，夜间低谷）：
`restart=0`、容器日志零 panic/fatal；admission journal 升级后仅 1 个
`capacity=true`（即上述受控 OAuth 回放），零 lane_reject、零自然容量
事件、零 429；目录粘性再隐藏按探针结果轮转（astra 曾被摘除后回到目录，
sol-input 在其探测窗内隐藏）——外部渠道事件的预期形态。窗口样本薄，
持续观察以用户日常使用为准。

## 回滚

必要时按 [cpa-manual-rollback.md](../runbooks/cpa-manual-rollback.md) 场景 A：

```bash
cp -a /opt/cliproxyapi/backups/20261012T040455.886900154Z-from-v8.0.23-canary-v8024/compose.yml /opt/cliproxyapi/compose.yml
docker compose -f /opt/cliproxyapi/compose.yml up -d --pull never
```

v8.0.23 镜像已确认本地保留：digest-pinned canary 镜像无标签
（`docker images` 显示 `<none>`），按 digest 精确核验
`PRESENT id=sha256:fc250aaf…`（≈291MB）；历史各代 canary 镜像
（`831f2fd4`=v8.0.21、`bdd21270`=v8.0.20 等）同样在册。

## 证据层级

| 层级 | 结论 |
| --- | --- |
| `repo_verified` | canary 脚本 sha256 本地核对；digest 三信源一致；本仓改动为夹具修复+证据文档 |
| `filesystem_projected` | doctor 投影全 MATCH（admission 5273f59e 代不变） |
| `host_loaded` | 容器 `v8.0.24@30280724` restart=0、双 generation 门 HEALTH_OK、`DOCTOR_CONTRACT_OK` |
| `controlled_live_replay` | 非 OAuth 三 lane 200/stop（astra/sol-input 502 经直连同 key 对照证明渠道侧）；OAuth 回放 200+capacity=true 为既有账号级 shed；cache-canary GLM lane 基线逐位一致 |
| `natural_live_accepted` | 升级窗约 30 分钟（见上节补记），持续观察以用户日常使用为准 |
