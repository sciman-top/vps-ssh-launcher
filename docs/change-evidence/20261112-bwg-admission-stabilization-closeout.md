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

新鲜的 bwg 自然流量只读审计读取 `5291` header 与 `5156` 配对记录，窗口
24h。观察到 Luna 118、Sol 110、Terra 142 次自然请求，分别有
97、93、124 次 completed 和 19、16、17 次 capacity；WorkBuddy 4290 次
自然请求中 4285 completed、1 capacity、4 client disconnect。WorkBuddy
60 秒 admit 峰值为 16，审计给出 burstiness advisory。这些是上游等待、
流量窗口和已存在的自然行为，本文不将它们消融为成功，也没有通过清冷却、
轮换账号、提高重试/并发来改变结果。因此持续自然验收仍为 `PARTIAL`。

## 投影契约复核

doctor 原先用 `sha256(pin file)` 与已部署 pin 文件内容比较，两个哈希域
不同；它只是在旧 pin 文件哈希碰巧等于源码哈希时短暂通过。现在 `.ps1`
的 projection hash 继续表示“文件字节”，用于 `PROJECTION_HASH_VERIFIED`
与 `.sha256` 文件漂移；admission generation 校验改为读取 pin 内容并与
源码内容哈希比较。两者共享一个按字节读取 HEAD blob 的函数，避免再次维护
两套 Git 子进程实现和编码路径。

修复提交 `9ed8d2a` 后，`cpa_bwg_guardrails.ps1 -Observe` 首次输出
`DOCTOR_CONTRACT_OBSERVE_OK`，`admission-integrity=OK`，admission 源码、
pin、service、integrity checker 全部 `MATCH`。随后只读幂等 `-Apply`
写入 15 项文件，每项 `PROJECTION_HASH_VERIFIED`，返回
`READY_STATUS=200`、`GUARDRAILS_APPLIED`；备份为
`/root/cpa-guardrails-backup-20261112T153517.289425998Z`。Apply 再次
重启 admission，启动于 `2026-11-12 15:35:23 UTC`，NRestarts=0。
再次只读 doctor 为 `DOCTOR_CONTRACT_OBSERVE_OK`，live 与 installed pin
均为 `d3e7f8e3…`。

最新零重试 replay request ID `607ab1c326eb487d942194fdb5d234dc`
返回 HTTP 200、`response.completed`、`pong`、`attempt=1`、`retries=0`；
同 ID journal 包含 `http -> lane_admit -> upstream_result`，上游 HTTP
200、`successful=true`、`protocol_result=completed`、`capacity=false`。
该提交之后的完整 `run_gates.ps1` 为 `1343 passed, 21 skipped,
671 subtests passed`，build、Bandit、Ruff check/format 与 Mypy 均通过。

## 回滚与后续

- 部署回滚需要同时恢复 source 与安装 pin，并重启 admission；上述私密备份
  即回滚材料。Git 回滚不能代替远端恢复。
- 目标代已经由 reviewed transaction 实际部署；首次换芯事务的最终退出码
  未取得 `DEPLOY_OK`，后续幂等 `-Apply` 才返回 `GUARDRAILS_APPLIED`。
  不得将幂等 Apply 回执倒推为首次换芯事务的退出码成功。不要为了获得旧
  输出重新换回旧代；未来事务必须单独证明成功分支或 `ROLLBACK_VERIFIED`，
  不能仅凭最终 doctor 判为事务成功。
- 真实 OFFICE 断热点与业务请求仍需在 OFFICE 完成受控网络验收；不能由本轮
  HOME 的 public replay 替代。
