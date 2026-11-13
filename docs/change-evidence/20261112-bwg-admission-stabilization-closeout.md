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

## 请求锁 owner 独立化（历史候选；生产结果见下节）

后续审查发现，旧 admission 把 `requests.lock` 的共享描述符交给请求工作线程。
被 retire 的 SSE reader 可在一次维护 drain 后继续持有该描述符，使维护事务
无法取得新的 exclusive lock。Linux `subprocess` 的 `pass_fds` 还会重排子进程
描述符，父进程直接按原 FD 号控制子进程因此不可靠。

候选把锁 owner 移到启动器 fork 的单线程 guard；请求线程只通过 broker
申请/释放租约，guard 按 `lock_id` 分别管理锁，父进程死亡时清理全部租约。
admission 的父子进程描述符由 exec 后 fresh 查询并以 `SO_PEERCRED` 和
`/proc` start time 绑定。候选静态检查、完整 WSL admission 与 guard 回归、
Windows 专项和 filesystem projection 已通过。当前目录没有可引用的本代
最终全仓门禁或 bwg host loading receipt，候选远端文件仍待单独 reviewed transaction
部署；Git 专项结果不能升级为 host_loaded。

最新受控 `-DryRun` 仍在修改主机前返回 `REFUSE traffic_not_drained`。
只读 healthz 显示存在自然 inflight/pending，未为制造空闲窗口重启服务、
清理冷却或提高重试/并发。当前唯一线上代仍为 `d3e7f8e3…`；部署要求
自然排空和 exclusive lock 同时成立。

状态边界：本节是历史候选记录，已被下方 2026-11-13 生产收口取代。

## 请求锁 owner 独立化（2026-11-13 BWG 生产收口）

2026-11-13 连续修复了启动协议、post-fork guard 身份、reviewed child
参数顺序、config 传递和部署验收器。前 8 次 apply 均按事务失败处理并
成功回滚；每一次的 `ROLLBACK_VERIFIED` 都意味着旧代 healthz 200 已恢复，
没有用清流量、清冷却、轮换凭据、提高 retry/concurrency 或强行 restart
来制造通过证据。关键回滚 receipt 备份包括：

- 2026-11-13T141312…、142007…、142556…、143235…、143832…、144712…、
  144949…、145722…request-guard。
- 最终成功事务备份：
  `/opt/cliproxyapi/backups/20261113T150403.149540890Z-request-guard`。

最终事务返回：

```text
DEPLOY_OK generation=29fa3b1c1c92a56ff8a26252f13c5a25069f2493b751b318aac5105f4cb34e95
          guard=e8d344b3a47e294c9c3901d10ce4e1810e56683efb763ebbe6ffe32e2af4fdb3
          backup=/opt/cliproxyapi/backups/20261113T150403.149540890Z-request-guard
```

部署后 fresh readback：service active，MainPID/bootstrap=990686，
guard=990692，admission=990693；marker 为 8 字段
`1 1794582245380782788 9 990692 990686 60780321 990693 60780335`；
live admission=`29fa3b1c…`，bootstrap=`0f209871de46e4321c0e68422176d1778e064cd4dfb276fda4b9fce41e68fbca`，
guard=`e8d344b3…`。`ADMISSION_INTEGRITY_OK`、
`PROCESS_LINEAGE_OK`、`GENERATION_BOUND_OK`、`IDLE_REQUEST_LOCK_OK`。
只读投影审计为 `CPA_PROJECTION_AUDIT=PASS evidence=filesystem_projection`。

部署后严格 doctor 的 admission-integrity、service/listener、lane hold、
syntax 与 14 项受管 projection drift 均为 OK/MATCH；但整体返回
`DOCTOR_CONTRACT_FAILED`，触发点是既有 CPA updater 心跳
`result=deferred exit_code=77`。这不是本代 guard 的失败，也没有在本轮
清除 pending/deferred 状态来换取绿色合同。

受控 public replay 保持 `BLOCKED`：`gpt-6-luna` request
`0d06a28d5e6d4fefaf8db2d2606fd916` 与 `gpt-6.1-sol` request
`2037ffa40b674d1e8feaf07acf0aa7ff` 都是 `attempt=1,retries=0`，
HTTP 200 到达 admission 后被既有 lane cooldown 拒绝，分别显示约
20993s 与 20957s。没有清冷却或换号重试。因此不能把这两次请求说成
受控业务 PASS。

自然/live 边界为 `PARTIAL`：post-deploy 自然 `glm-5.3-flash` request
`d32e3cfb68ef4b2d9baf68cbb9e23b04` 在同一 ID 下完整关联
`http -> lane_admit -> upstream_result`，上游 HTTP 200、
`successful=true`、`protocol_result=completed`、`capacity=false`，
时间 2026-11-13 15:08:04–15:08:13 UTC。这证明该自然请求已穿过新
admission/guard 主链，但不覆盖 chatgpt/workbuddy OAuth、不证明 retired
reader 场景在真实流量中复现过，也不能替代冷却解除后的 controlled
replay。`retired-reader` 的锁归属仍以 WSL fork/SCM_RIGHTS 专项和事务内
idle exclusive 复核为证据；真实 retired-reader 生产验收仍未发生。

最终证据分层：`repo_verified`（全仓 pytest 1343 passed、ruff/bandit
通过、类型修复后 focused mypy 通过、WSL request guard 6/6）；
`filesystem_projected`（projection audit PASS）；`host_loaded`
（DEPLOY_OK、hash/marker/lineage fresh readback）；controlled replay
`BLOCKED`（既有 cooldown，未清除）；natural/live `PARTIAL`
（GLM 自然请求闭环，OAuth 与 retired-reader 未见真实样本）。

## 2026-11-14 新鲜复核与受控 replay 解阻

后续仓库推进到 `c54c7962cd952d4f71d6eee926126744ee9ce308`（本地与
`origin/main` 同步，工作树干净）。该提交只新增本地只读状态视图；request
guard 与 admission 部署面源码未被后续提交改动。当前 HEAD 复核：

- 风险相关 focused tests：`400 passed, 4 skipped, 85 subtests passed`；
  4 个 skip 是 Windows 不提供 POSIX fcntl/runtime API 的预期跳过。
- focused Ruff check/format 与 Mypy：全部 exit 0。
- `cpa_bwg_guardrails.ps1 -AuditProjection`：
  `CPA_PROJECTION_AUDIT=PASS`。
- `-Observe`：`DOCTOR_CONTRACT_OBSERVE_OK`；admission-integrity OK，
  live admission 仍为 `29fa3b1c…`，14 项 projection drift 全 `MATCH`，
  request guard 24h refusals 为 0，三条 OAuth lane hold 均 OPEN。

原有受控 replay 阻塞解除后，重新执行一次 public gateway、零重试、
`gpt-6-luna` 请求：

```text
STREAM_ACCEPTANCE result=PASS attempt=1 retries=0 http_status=200
                 completed=true text_matches=true
request_id=696d44d66bc247d7a2ec7b527ce22651
```

同一 request ID 在 admission journal 中关联
`http -> lane_admit -> upstream_result`；上游 HTTP 200、
`successful=true`、`protocol_result=completed`、`capacity=false`、
`sse_last_event=response.completed`。因此 request-guard 收口的
controlled replay 边界由 `BLOCKED` 更新为 `PASS`。该请求仍只证明
这条受控链路；OFFICE 有线、断开热点后的 WEB/GIT 自然业务验收和真实
retired-reader 生产样本仍未发生，持续 natural/live 验收保持 `PARTIAL`。
