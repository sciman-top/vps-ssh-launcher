# bwg admission 稳定化换芯与受控验收（2026-11-12）

## 范围与授权

用户授权连续完成审查、修复、投影与受控验收。本轮只写 bwg admission
源码与 integrity pin，不修改 zz、Nginx 数据面、OAuth/API key 凭据、
冷却、并发、重试或其它凭据状态。执行前以只读 doctor 确认当前运行代与
安装 pin，再以仓库 reviewed deploy transaction 做单机 drain、备份、
双文件原子替换、只重启 admission 和就绪/恢复契约检查。

## 实际部署证据

- 目标源码与 pin 均为
  `d3e7f8e3cb16c4927096f426830aa726c92667412f946e5574f4c7371b095d77`。
  部署前 live=`c410187f…`、installed pin=`375da238…`；只读 dry-run 输出
  `ADMISSION_IDLE_OK`、`ADMISSION_INTEGRITY_OK` 与 `no_swap=true`。
- 事务备份：
  `/opt/cliproxyapi/backups/20261112T141016.368763566Z-admission-stabilization-20261111/`。
  该备份包含旧脚本、pin、service snapshot 与 lane state；回滚入口为用
  旧 `c410187f…` 代重跑 reviewed transaction，而不是只回退 Git。
- 一次实际 apply 完成了双文件替换、admission 重启、integrity 与恢复契约
  检查，但 Bash 退出处理在收尾阶段返回 code 2。随后只读 doctor 确认 live
  与安装 pin 已是目标 `d3e7f8e3…`，且没有凭据/lane 状态被改动；该次事务
  仍按失败处理，不把它误报为事务 `DEPLOY_OK`。
- 修复事务的 coprocess guard：临时脚本在启动 guard 前落盘，独立的 Python
  flock guard 使用明确的 commit 控制通道，失败/完成均释放 guard，并在
  rollback 中显式清理 staging。修复后的 dry-run 只执行只读校验，命中
  `ALREADY_APPLIED mode=dry-run pin=d3e7f8e3…`。
  此修复尚未再次执行写事务，因为目标代已经部署且不再重复换芯。
- 实际 apply 绑定旧运行的 `c410187f…` 作为 live baseline，原有安装 pin
  `375da238…` 作为独立基线；事务不把“旧代正在运行”当作 `DEPLOY_OK`。
  事务成功分支只接受目标代的 live 与 installed pin 双双一致。

## 主机加载与受控回放

换芯后严格只读 doctor 返回 `DOCTOR_CONTRACT_OK`：admission integrity、
healthz、三条主要 OAuth 车道与 15 项受管投影检查均通过，投影哈希全部
`MATCH`。`gpt-6.1-sol` 已在模型目录中可见，且没有清除既有冷却或 hold。

按顺序各执行一次单请求、零重试的 public gateway Responses SSE 回放：

| 模型 | HTTP | terminal | 请求 ID | 重试 | 容量标记 |
|---|---:|---|---|---:|---|
| `gpt-6-luna` | 200 | `response.completed` / `PASS` | `fb0ac6573d0446b7ba6907cc7c1c272a` | 0 | false |
| `gpt-6.1-sol` | 200 | `response.completed` / `PASS` | `130f743c93084e909dba269f3e2fa22e` | 0 | false |

两请求的同一 request ID 都能在 admission journal 找到 `http -> lane_admit
-> upstream_result`，上游为 HTTP 200、成功、协议完成且无容量标记。这证明
受控 public replay 与本地 gateway/admission 关联通过，不证明各真实客户端、
办公室断热点或长期稳定性已经通过。

## 自然流量边界

24 小时自然流量审计读取现有 journal，只输出聚合值。观测到 64 次 Luna、
88 次 Sol、119 次 Terra 自然请求，以及 WorkBuddy、BigModel 等其它车道；
其中有容量等待与客户端断流，未重发或掩盖。审计记录为 3681 条，统计
窗口为 24 小时；这不是 7 天覆盖率或 SLA 证明。审计还报告 WorkBuddy 60 秒
突发峰值与 hy4free 前缀缓存命中偏低两个 advisory；本轮不通过轮换账号、
提高并发或重试来消化这些运行态风险。持续自然验收因此保持 `PARTIAL`。

## 后续只读复核

本轮继续核验后，严格只读 doctor 再次输出 `DOCTOR_CONTRACT_OBSERVE_OK`；
target source 与 installed pin 同为 `d3e7f8e3…`，admission integrity、
healthz、服务 enabled/active、公共路径未认证 401 与裸路径 404 均通过。
受管投影检查全部 `MATCH`。随后一次 public gateway、单请求、零重试的
`gpt-6-luna` replay 返回 HTTP 200、`response.completed`、`pong`、
`retries=0`、`attempt=1`，request ID 为
`5282fa17607541e490f4fc02b389e174`。该 ID 在 admission journal 中仅按
同一 ID 关联到 `http -> lane_admit -> upstream_result`，上游 HTTP 200、
`successful=true`、`protocol_result=completed`、`capacity=false`。
这仍是受控 replay，不升级为真实客户端或 OFFICE 网络验收。

`tests/test_cpa_updater_apply.py` 与 `tests/test_cpa_admission.py` 的当前
结果分别为完整文件验证 `358 passed, 1 skipped, 47 subtests passed`（Windows
symlink 权限不可用）；仅有本地测试证据，不替代上述主机读回和请求关联。

幂等 `-Apply` 当前输出 15 项 `PROJECTION_HASH_VERIFIED`、
`READY_STATUS=200`、`GUARDRAILS_APPLIED`，备份目录为
`/root/cpa-guardrails-backup-20261112T144813.967905513Z`。投影期间有
一次预期内的 admission 重启 connection reset；后续 bounded readiness
返回 200，且投影前目标代已经部署，本次不是源码换芯。

该 Apply 后 admission 再次启动于 `2026-11-12 14:48:20 UTC`、NRestarts=0，
live 与 installed pin 仍为 `d3e7f8e3…`。再次执行的 public gateway replay
request ID `84e7c9448e4e4f67bf92ece67d96de19` 返回 HTTP 200、
`response.completed`、`pong`、`attempt=1`、`retries=0`，同一 ID 对上
`http -> lane_admit -> upstream_result`，上游 200、completed、无容量标记。
随后只读 doctor 为 `DOCTOR_CONTRACT_OBSERVE_OK`。全仓门禁当前结果为
`1343 passed, 21 skipped, 671 subtests passed`，Ruff check/format、
Bandit、Mypy 均通过。

## 回滚与后续

- 部署回滚需要同时恢复 source 与安装 pin，并重启 admission；上述私密备份
  即回滚材料。Git 回滚不能代替远端恢复。
- 本次目标代已通过严格 doctor 与受控回放，但写事务自身的收尾退出仍未
  取得成功回执。不要为了获得该输出重新换回旧代；未来事务必须单独证明
  成功分支或 `ROLLBACK_VERIFIED`，不能仅凭最终 doctor 判为事务成功。
- 真实 OFFICE 断热点与业务请求仍需在 OFFICE 完成受控网络验收；不能由本轮
  HOME 的 public replay 替代。
