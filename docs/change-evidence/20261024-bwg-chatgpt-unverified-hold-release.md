# 2026-10-24 BWG chatgpt-oauth `unverified_request` hold 释放（v8.0.41 canary 部署副作用收口）

## 授权与边界

- 用户出示 Codex 客户端截图：同一入口连续三类报错（403
  `Shared upstream lane chatgpt-oauth requires operator review`、503
  `CPA upstream is temporarily unavailable`、502 nginx Bad Gateway），并以
  "总是不停报错！"加当日常驻的连续修复授权提出请求。
- 仅改动 `/var/lib/cpa-admission/chatgpt-risk.json` 的 `hold_reason` 字段，
  并做一次 admission 停启；`not_before` 保持 0，未缩短任何上游等待窗口。
- 未改动凭据、`config.yaml`、compose、插件、模型目录、并发/重试、冷却与任何
  OAuth/API-key 渠道；未轮换身份、IP 或 UA，未生成 OAuth 探针流量。
- Claude 退役态与 WorkBuddy/Antigravity 风险文件未触碰。

## 三类错误的成因分层

| 错误 | 时间窗 | 成因 | 处置 |
| --- | --- | --- | --- |
| 403 `requires operator review` | 04:03Z 之后持续 | 容器重建时一条在途请求结果未确认，write-ahead 按设计落 `hold_reason=unverified_request` | 本次人工恢复流程释放 |
| 503 `CPA upstream is temporarily unavailable` | 重建窗口 | CPA 重启期间上游连接断开，非凭据失效 | 随服务就绪自行消失，未做额外动作 |
| 502 nginx Bad Gateway | 容器切换瞬间 | 边缘到 CPA 的上游短暂不可达 | Nginx 日志各 1 次 503/502 后自然恢复 |

时间线（UTC，来源为 journal、CPA 日志与 doctor 读数）：

- 03:59:05 v8.0.41 canary 事务完成（见
  `20261024-bwg-cpa-v8041-canary.md`）；03:49:57 / 03:54:23 的两次重启是该
  台账中记录的目录漂移恢复动作，非本次新增事件。
- 04:03:05 admission 随容器重建停启，一条在途流被切断，状态被锁存为
  `{"version":1,"not_before":0,"hold_reason":"unverified_request"}`。
- 之后 chatgpt-oauth 对客户端统一回 403 `operator review`；同时段出现的
  503/502 属重建窗口的边缘错误。

判定：`unverified_request` 是 fail-closed 的正确锁存，不是账号风控信号；
凭据本体健康（doctor `oauth_monitor=OK`，无活动冷却）。这是同一固有竞态第三次
伴随容器重建出现（10-20 ladder、10-21 canary、本次 v8.0.41）。

## 处置

`outputs/release-chatgpt-hold-20261024.sh` 固化整套流程（维护锁、lane 空闲断言、
hold 原因精确断言、备份、权限保持、admission 停启、healthz 复验、失败自动拉起）：

1. 释放前断言：四 lane `inflight=0 / pending=0 / half_open_probe=false`；
   holds 恰为 `chatgpt-oauth=unverified_request`，其余三 lane 为 `null`。
2. 备份：`/opt/cliproxyapi/backups/20261024T041721.745389427Z-chatgpt-hold-release/chatgpt-risk.json`
   （原字节保留，备份目录由脚本以 `mkdir -m 700` 创建）。
3. `systemctl stop cpa-admission` → 只把 `hold_reason` 改 `null`（临时文件 +
   fsync + `chmod 0600` + `chown nobody:nogroup` + `os.replace`）→ start。
4. 复验：healthz `status=ok`，四 lane `hold_reason=null`、无在途/排队，
   状态文件 owner/mode 未变。

## 验收

- 受控单发（一次，非探针风暴）：`gpt-6.1-sol` 经 admission → **HTTP 200**，
  `response_status=completed`，正文 `OK`，1.514 s。
- 严格 doctor：释放后一次、收口前再跑一次，均 `DOCTOR_CONTRACT_OK`、exit 0；
  收口那次读到三条 OAuth lane `OPEN`、`lane-hold=OK`、Claude 墓碑
  `unconfigured-tombstone-inert`。
- 收口时刻只读复核（04:21:58Z，`outputs/probe-hold-state-20261024.sh`）：
  `HEALTH ok`，chatgpt/antigravity/workbuddy/zhipu 四 lane
  `hold=None inflight=0 pending=0 cooldown_active=False`；
  `chatgpt-risk.json` 为 `{"version": 1, "not_before": 0, "hold_reason": null}`，
  `0600 nobody:nogroup`。该文件在 04:20:38Z 被服务自身重写（格式与脚本写入不同），
  hold 仍为 `null`，说明释放结果已被运行时持久化。

边界：以上只证明"部署引入的 403 锁已解除、admission 数据面恢复"。**尚未观察
自然客户端持续无错**；上游过载窗口内的 503/502 仍可能偶发，与本次释放无关。

## 回滚

需要重新置锁时，恢复备份目录中的 `chatgpt-risk.json`（保持 `0600
nobody:nogroup`）并 `systemctl restart cpa-admission`；这是远端状态回滚，
不等同于 Git 回滚。Git 侧回滚只撤销本文件与释放脚本。

## 遗留问题（未在本次修改）

- v8.0.41 部署脚本确实对 `/run/cpa-admission/requests.lock` 取排他锁，但
  04:03:05Z 仍有请求穿过重启窗口，且未留下 `request_guard_refused` 记录。
  现有证据不足以判定跨锁竞态，**不改锁协议**；后续应先对齐并发部署操作日志与
  完整请求时间线，再决定是否加固。

## 遗留问题取证闭环（同日午后，独立会话；只读取证 + 本仓修复）

对齐部署事务与请求时间线后，"跨锁竞态"假说被证伪，上方遗留问题改判：

- canary 事务 03:59:01 启动，03:59:05 在 lane 空闲窗重建容器（该窗零
  `RemoteDisconnected`），exit 0，`requests.lock` 随进程退出释放；
  04:00:05–04:02:57 admission 连续 200 成功（含 04:01–04:03 的稳定流量）。
- 04:03:05,365 请求 `b676377a…`（chatgpt-oauth/gpt-6.1-sol）报
  `RemoteDisconnected`，与新容器 `StartedAt=04:03:05.565` 同刻；04:03:07
  systemd 停启 cpa-admission。canary 脚本无任何 systemctl 步骤——该重启
  属于目录漂移恢复收尾的 operator 重启（v8.0.41 台账 root-cause 段所记
  03:49:57 / 03:54:23 / 04:03:05Z 之一），未走 drain 契约。
- 03:58–04:05 全窗 `request_guard_refused`=0：guard 无失灵——排它锁根本
  未被持有，请求合法通行，被切于无保护重启瞬间。

判定：canary drain 契约（2026-10-22 起）按设计工作；残余面是**不走契约的
重启路径**（guardrails `-Apply` 的容器/admission 重启原本同样无 drain）。
修复（同日本仓）：`scripts/remote/cpa-guardrail-apply.sh` 在两处重启前加
同款排它 drain + 空闲断言（公网探针前释放锁，避免把 maintenance 503 误判
为网关故障）；新增 `outputs/cpa-guarded-restart-20261024.sh` 作为手动重启
的 guarded 入口（container/admission/both，含 lane-hold 差分）；
`docs/runbooks/cpa-gateway.md` 更新器策略段补重启纪律条目。锁协议本身未改。

## 修复的分层验收（同日午后）

- 投影：N/A——apply.sh 不在投影清单（guardrails 每次调用现传，占位符
  `__CPA_ADMISSION_SHA256__` 逐次代入），doctor 13×MATCH 无 drift 债。
- 模拟：两个 healthz python 块以文件原文字节对生产 healthz 实跑通过；
  hold 差分 `comm` 逻辑双情形仿真（空差分/latch 检出）通过；bash -n 双
  脚本；focused 62 passed + full gates 737 passed；apply.sh 的 nginx 动作
  仅 graceful reload（SIGHUP 旧 worker 收尾存量连接），无需纳入 drain。
- 受控实战（wrapper `both` 模式，detached，2026-10-24T05:32:34Z 基线）：
  `GUARDED_RESTART_OK mode=both`。journal 取证：排它 flock 自 ~05:32:40
  等待在途流共享锁（admission 每请求 `LOCK_SH`，见 cpa-admission.py
  request_guard），最后一笔 05:32:56,381 完成后授予排它（等待 ~16s<60s
  预算）；老容器在零在途时被停（stop 即时，新容器 05:32:57.748 起动）；
  admission 05:32:58 重启；全程 `RemoteDisconnected/upstream_error`=0、
  新增 lane-hold=0、四 lane `hold=None`，流量随后正常恢复。锁窗口内无
  新业务请求到达故无 `request_guard_refused` 记录，与"排它授予前放行、
  授予后必拒"的语义一致。
- apply.sh 新路径的实战：同日尝试 `-Apply` 被 clean-tree 守卫 fail-closed
  拒绝（并行会话在途编辑 cpa-guardrail-doctor.sh，守卫行为正确）——drain
  机制已由 wrapper 实战同构证明，apply.sh 集成面（restore_all 交互、公网
  探针前释放排序）留待下一次自然 `-Apply` 首跑；远端 /tmp 传输件已清理。
