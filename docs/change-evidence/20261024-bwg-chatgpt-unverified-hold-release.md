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
