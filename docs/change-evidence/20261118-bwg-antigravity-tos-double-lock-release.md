# BWG CPA Antigravity TOS 服务禁用与双层锁释放（2026-11-18）

## 目标与边界

- 定性 antigravity-oauth lane 集体报错（客户端侧
  `retry after 89s`）的真实信号，并按用户"重新登入"指令执行受控恢复验证；
  申诉与"隔离该号+释放 hold"的裁决权在用户，agent 只做只读探针、
  单发回环定性与投影收口。
- 判决落地后不再向禁用账号发送任何请求（停手纪律）。

## 时间线与信号（UTC）

- 04:07:56 v8.0.77 镜像部署重建（destroy→rename→start，随后 sha256 复核
  exec）；04:08–04:14 lane 正常 200 服务，亲和全部落在凭据 sha8 `adc349fc`。
- 04:14:12 `docker restart`；04:14:14–20 客户端收
  `stale_write_ahead_cooldown retry_after 89→84`（重启写前窗指纹，良性，
  自行到期）。
- 04:16:19 写前窗到期，half_open 探针命中 `adc349fc` → 上游
  `403 PERMISSION_DENIED / TOS_VIOLATION`（cloudcode-pa.googleapis.com，
  "service disabled … submit an appeal"；ErrorInfo metadata 含
  `appeal_url`）→ admission latch `account_restricted` durable hold，此后
  `operator_review_required retry_after=300` 循环拒绝。
- 04:38 24h 日志普查：TOS_VIOLATION / 401 / 403 信号唯一凭据 = `adc349fc`，
  其余 7 份零信号 → 单账号命中，非扫荡波。

## 处置与回执

1. hold 释放事务 A（04:41）：备份
   `/root/antigravity-risk.before-release-20261118T044119Z.json` → stop
   admission → `antigravity-risk.json` 置
   `{"version": 1, "not_before": 0, "hold_reason": null}` → start；
   healthz OK，lane 释放后零自然流量（客户端重试会话已停止）。
2. 首发单发探针撞 CPA 池停：`503 "antigravity pool paused after
   TOS_VIOLATION"` —— `tos-pause-window: "2h"` 已被 04:16 真实 TOS 信号
   武装（kv TTL，重启不清）；且该 503 响应体里的 TOS_VIOLATION 字样把
   admission hold 再 latch 一轮（04:44:47 `lane_hold_latched`）。
3. 双层锁释放事务 B（04:49）：保 inode 翻 knob `"2h"`→`"0"`（config inode
   `525636` 全程不变；备份
   `/root/cpa-config-backup-before-tos-unpause-20261118T044*.yaml`）→
   等热加载 → 清 admission hold → 单发 8318 回环探针 → 翻回 `"2h"`。
4. **探针判决：同 403 TOS_VIOLATION 原样返回（0.34s，真实到达上游）**。
   该凭据 04:32:58Z 已由用户完成 OAuth 重登（auth json 刷新、
   `"disabled": false`）→ 实证**重登不解 Google 侧服务禁用**；凭据侧
   disabled 标记清零只代表本地路由面恢复，唯一出路 = `appeal_url` 申诉。
5. 投影收口（同日晚些时候，用户连续授权）：严格 doctor 发现部署版
   `cpa_policy.py` 内容哈希 = `3555bfb^`（affinity TTL 仍为 1h 旧值）——
   系当日并行会话以隔离分支 `1287209` 投影 Opus 5.5 路由时刻意未纳入
   `main` 其余改动所致（其台账已声明"不代表 main 整体已投影"）。执行
   `scripts/cpa_bwg_guardrails.ps1 -Profile bwg -Apply`（main HEAD，
   回执 `GUARDRAILS_APPLIED`，apply 统一重启）后复跑 doctor：
   `==projection-drift==` 无 MISMATCH；唯一 FAIL =
   `lane-hold antigravity-oauth HELD reason=account_restricted`（事故
   姿势，有意保持）；运行时权威读数 `/v0/management/config` 复核
   `tos-pause-window = "2h"`。

## 判决与挂账

- lane 维持 `account_restricted` hold（自保护：本地拒绝、零上游触碰），
  其余 lane 与其余 7 份凭据不受影响。
- 申诉为用户本人动作（"解封后再犯=永久"纪律）；用户其后明示指令并手动
  删除该凭据（等效隔离+出池），lane 于同日恢复服务（见下节终态补录）。

## 终态补录（用户删除凭据后收口）

- 用户手动删除 `adc349fc` 凭据文件（auth 目录池=7），CPA 05:54:47Z 热加载
  `auth file changed (REMOVE)`；删除前备份在位
  `/root/auth-backups/antigravity-adc349fc-backup-20261118.json`（可恢复）。
- 本会话释放 hold（备份
  `/root/antigravity-risk.before-release2-*.json`）。删除后模型面复核
  （`/v1/models`，17 模型）：唯 `gemini-4-carbon-high` 消失——该凭据为池内
  唯一 carbon 档承继者（与 doctor substitution 观察"16 次替换集中于单凭据
  e7926a37c0760544"互证）；argon / hydrogen / sonnet / opus / flash /
  astra 均在。**客户端请求 carbon-high 现为 CPA 本地
  `unknown provider` 400**（未触上游），需切换模型或补入带 carbon 档的新号。
- 第二轮池停揭示（机制补全）：04:16 的 kv 条目（TTL 2h，至 06:16:19Z）在
  knob 翻回 `"2h"` 后**恢复执法**——翻 `"0"` 只是抑制执法、不删除条目
  （"重启容器不清"实证）；其 503 响应体把 hold 再 latch 一轮
  （runbook 陷阱第二次实证）。
- 修正释放（事务 C，严格按 runbook 顺序）：备份
  `/root/cpa-config-backup-unpause-round2-*.yaml` → 保 inode 翻 `"0"`
  （inode 525632 不变）→ 清 hold → 单发探针 **`gemini-3.8-flash-high` 200
  （2.0s）/ `gemini-4-argon-high` 200（5.1s）**，hold 保持 null。
  保持 `"0"` 至 kv 条目自然过期（06:16:19Z），后台任务 06:17:50Z 翻回
  `"2h"` 并复核运行时读数（同事件内翻回契约；偏差与理由=翻回即重新压住
  lane 至 06:16 且 503 体反复再 latch hold，需再一轮手动释放）。
- doctor 终验在翻回 `"2h"` 后执行（钉定契约：knob 清零态下语义复验
  fail-closed，doctor 必须在 `"2h"` 态运行）。
- 本次对禁用账号的上游触靶仅第 3 步单发探针一发，验证目标即重登凭据本身。
- 双层锁释放顺序、事故指纹判别、重登不解禁三条已固化进
  `docs/runbooks/cpa-ban-throttle-incident-response.md`（同提交）。
- 回滚入口：池停/hold 状态备份见上文路径；投影回滚走
  `/root/cpa-guardrails-backup-*` 事务目录；本记录无仓库代码变更。
