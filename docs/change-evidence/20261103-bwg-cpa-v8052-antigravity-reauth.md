# BWG CPA v8.0.51 → v8.0.52 + antigravity aicode re-auth（2026-11-03）

## 目标与授权

用户授权按 round-14 姿态审查推荐连续执行：人工联合 host/plugin canary 升级窗 +
双 antigravity 凭据配对 fresh re-login（runbook `cpa-oauth-failure-recovery.md`
aicode 节）。同窗以 09ee239 修正该 runbook 前提：`plugins.enabled=true` 时更新器
canary 门挡住一切无人值守套用，本事务即其规定的"人工联合 canary 窗"。

## 版本审查（v8.0.51..v8.0.52，16 commits）

- v8.0.52 = `6e6afab`（2026-11-02T19:15:02Z 发布，非 prerelease）；镜像 digest
  `sha256:5d5bab318ac048c0f88570c6ffb852ef3e42e9acc9bfcf346c524de7a2468131`
  （Docker Hub 19:17:45Z）；staging 二进制 sha256 前缀 `6be3ba4ac8…`，`ldd` 仅 libc。
- 关联本仓车道：#6315 aicode OAuth scope（仅进登录 URL；v8.0.52 源码零运行时
  aicode 端点调用）；#7159 系 pluginhost 流所有权修复（workbuddy 稳定性收益）；
  TransportCache panic、凭据去重连接池键、Gemini 入站角色归一。
- 缓存/准入/重试/冷却/连接池策略零变更；codex Chrome 分支仍无连接池化
  （round-10 结构性 TTFB watch 挂账继续）。
- 插件上游 mmqz/cpa-multi-plugins 仍 v0.8.92（无新版）；在役 workbuddy.so
  0.9.84 原样保留，sha256
  `2665af747f48aa3421f2432556c97f59ad538ece966eb93d940655f21c22bc47`。

## 隔离加载证

`ISOLATED_PLUGIN_LOAD=PASS`：v8.0.52 二进制 + 在役 .so，挂载 + `--network none`
无凭据；注册 `version=0.9.84`、`account_max_concurrent=1`、`daily_credits_cap=300`，
零 panic/fatal（唯一 warn=隔离网 DNS 不可达，恰证隔离成立）。

## 备份

`/opt/cliproxyapi/backups/20261102T233319.192588450Z-from-v8.0.51-to-v8.0.52-plugin-0.9.84-unchanged/`：
compose.yml、config.yaml、workbuddy.so、`credentials.sha256`（14 凭据）、2 份
antigravity 凭据文件副本（re-login 前快照）。

## 配对 re-login（runbook 步骤 1-2 闭环）

- 两凭据先后经 v8.0.52 临时登录容器（`--antigravity-login`，callback 51121 绑
  127.0.0.1，`umask 077`，同镜像同挂载）交互重登；每次注入前校验 callback
  `state` 与容器签发一致（防旧授权误注）。
- 实窗教训：①登录容器 5 分钟硬超时短于人工往返（#1 首发超时，重发即成）；
  ②**长授权 URL 手动复制会被截断**（一次 Google 400 `invalid_scope` 为 scope
  尾段截断）——改为本地 `.url` InternetShortcut 双击打开后零复制成功，文件
  用后即删未入仓；runbook 已补操作注记。
- tokeninfo 验证：两凭据均 `AICODE_PRESENT=True`（落盘当刻 + 升级后复验各一轮，
  复验轮 exp 3052/3347）；落盘=同文件名覆写，antigravity 文件数恒 2 无残留。

## 生产事务（2026-11-03T12:14:37Z 起，~7 秒）

- 断言链全过：compose pin `v8.0.51@sha256:15d13889…`、备份 compose 与 live 逐字节
  一致、插件 sha、回滚镜像在位、磁盘 ≥2GiB、三 lane hold=None、traffic_guard
  healthz 校验；维护锁 + `requests.lock` 排他 drain。
- 原子换 compose 镜像行（替换计数=1）→ `docker compose up -d --pull never` 重建。
- 验证全绿：`DIGEST_READBACK=OK`、宿主 banner `v8.0.52, Commit: 6e6afab`、插件
  注册 `0.9.84` + 双 pin、`HEALTH_GENERATION rc=0`、`AUTH_BYTES_UNCHANGED`、
  `LANE_HOLD_DELTA=none`、`restarts=0`。失败路径带自动回滚 trap（备份 compose
  回放 + up + readiness）。

## 升级后复验

- strict doctor：`DOCTOR_CONTRACT_OK`；三 OAuth lane OPEN（chatgpt
  write_ahead_inflight=1 属已知惰性解读）、workbuddy 0.9.84 + 全 pin、
  compose syntax OK、TLS 68 天、claude 墓碑 inert。model-substitution 7d=0
  （容器重建清窗，按 doctor 注记不外推）。
- 实战：8318 单发 `gemini-3.8-flash-high` SSE = 200 / 4.2s / 3 events / `PONG` /
  completed；journal 自换镜像窗 `lane_reject=0`、upstream 全 200。
- 事务与 login/verify 脚本随台账入库 `outputs/cpa-v8052-*.sh`（R8 可追溯）。

## 回滚入口

- 镜像：备份目录 compose.yml（v8.0.51 pin）回放 + `docker compose up -d --pull
  never`（v8.0.51 镜像本地在位）。
- 凭据：备份目录 2 份 antigravity 文件副本 + `credentials.sha256` 比对。

## 运行态变化与边界

- 升级后 compose 现役=v8.0.52=最高成熟候选，每日 updater 回到"无候选"exit 0
  路径（候选在前的每日 77 deferred 态消失）；doctor `==timer-result==` 的
  deferred:77 容忍保留不动。
- aicode 配对动作已闭环：后续真正消费 aicode 端点的 release 无需再配对 re-login。
- 两台 desktop 消费面无感知（仅网关侧变更）；zz 不涉 CPA，逐台边界不触发。
