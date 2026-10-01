# BWG CPA admission 容量判定与退避修复投影（2026-10-01）

## 范围与结论

- 范围为当次授权的 BWG CPA 风控修复、远端投影、模拟验证与有界真实验收；不涉及 ZZ，不轮换凭据或随机路径，不修改账号池，不提高并发上限。
- 实现提交：`c1293db87f778e04afafc9c599a06fc89c4b13a8`；代码写集为 `scripts/remote/cpa-admission.py` 与 `test_cpa_admission.py`。
- 确定性回归和部署契约通过；真实 Direct API 流式验收失败。不得将本记录解释为“容量错误、429 和慢速已全部消除”。

## 修复内容

1. 容量关键词只检查 JSON/SSE 协议错误对象，不再扫描正常生成正文；已知路由与认证错误不作为共享账号容量故障。
2. 上游显式 `Retry-After` 建立不可提前探测的截止时间；本地早探测不能越过该最小等待时间，早探测同时受在途上限约束。
3. 探测只有在请求成功完成且没有协议错误时才能清除故障状态；传输失败、客户端断开和非成功响应不再被当作恢复。

## 投影与备份

- 原 `-Apply` 调用中断后，远端事务已经继续执行；恢复工作时先只读核对，没有再次执行 apply。
- 备份：`/root/cpa-guardrails-backup-20261001T072028.721716146Z`。
- CPA 容器启动时间：`2026-10-01T07:20:30.645652904Z`；admission 启动时间：`2026-10-01T07:20:32Z`，对应北京时间 15:20:32。
- CPA 镜像保持 `eceasy/cli-proxy-api:v8.0.8@sha256:7d7203c06b4f5fd844adbc8cc8be1965e92f7e292ef169146cb9321812587a65`；本轮不是新版本升级。
- 部署 admission SHA-256：`bc86a8255b1ccfbde74261e3dfe1577cae158f0cad1ce7eea08dac3c29673e4b`。
- 备份 admission SHA-256：`478ffffa759842b6f564719b0c76cec9786dd6b8609e3c09e5fb464c27a8e5b4`。
- 当前和备份配置 SHA-256 相同：`52c295f25e9f67c10f638760f9b3e384e7c948c187039cb225ee10f90b3c5698`。
- 严格 doctor 收据时间 `2026-10-01T07:23:30Z`：`DOCTOR_CONTRACT_OK`、`POLICY_OK`，全部投影 drift 条目为 MATCH；loopback 绑定、Nginx 路由、权限与语法契约成立。
- 07:20:30Z 附近三个请求出现连接关闭或重置，与本次容器重启时间吻合；保留为部署影响，不纳入持续上游故障结论。

## 确定性验证

- 当前提交重新运行 `python -B -m pytest -q test_cpa_admission.py`：`32 passed in 23.97s`，退出码 0。
- `git diff --check` 通过。
- 对远端已部署脚本和配置进行不调用供应商的模拟，五项均为 true：
  - 显式 `Retry-After` 阻止提前探测。
  - 失败探测保留冷却。
  - 正常正文中的容量关键词不触发容量判定。
  - 真实 SSE 过载错误进入容量判定。
  - 路由错误不导致账号通道熔断。
- 收据：`DEPLOYED_SIMULATION_RESULT=PASS real_provider_requests=0`。这是部署文件的模拟，不是在生产服务中注入故障。

## 真实验收与未解决项

- 当前 Desktop 使用 API key，经本机 10909 Responses 服务走 Direct API；Luna 在实际本机目录中可见，请求模型与日志中的上游模型均为 `gpt-6-luna`。
- 单次流式验收开始于 `2026-10-01T07:28:32.259564Z`，对应北京时间 15:28:32；请求为短计数任务，`max_output_tokens=192`，总时间上限 120 秒。
- 实际返回 HTTP 429，耗时 199ms，`Retry-After: 14`；无正文流，无首字延迟或流内速度指标，`CONTROLLED_STREAM_RESULT=FAIL`。没有重试，也没有绕过 admission 追加直连探测。
- 同时段远端 journal 确认该 Luna 请求被 `chatgpt-oauth` 通道以 `reason=cooldown retry_after=14` 拒绝，不是本机模型目录缺失，也不是此次请求的队列满。
- 之前 07:27:45Z 的 Luna 响应为 HTTP 200 但 `capacity=true`，07:28:14Z 又出现 HTTP 503、`capacity=true`、上游 `Retry-After`。容量保护有实际错误输入，不仅是正常正文误判。
- 因此本机日志的 HTTP 200 / success 计数不能单独证明 SSE 正常完成或模型质量；本轮没有获得该次请求的 `response.completed`。
- 更早的 14 次 OAuth 429 为 `reason=busy`；该类与本次容量冷却不同。观察窗口从 06:50Z 开始，OAuth 入队等待样本中位数 9031ms、最大 97101ms；部署后早期 13 个样本中位数 15145ms、最大 74336ms。队列等待是慢速的一个独立原因，不得全部归因于上游生成。
- 07:28Z 冷却期间，其他请求在剩余等待 30、28、27、25、22、18 秒时继续到达；07:29Z 又有短间隔请求被 `half_open_probe` 拒绝并返回 10 秒等待。日志证明存在提前再次请求，不能仅凭这些记录断言全部来自同一次 Desktop 自动重试。
- 观察到的 Astra passthrough 502/503 属于另一条中转路由，不能归因于 Luna/OAuth 账号。
- 本机 Direct OAuth 服务快照无账号和 client key，Desktop 当前凭据为 API key、无 OAuth token。本轮未切换认证模式、未迁移账号，Direct OAuth 真实验收仍未完成。
- 没有获得长期自然负载验收、上游账号不会受限的保证、模型无降质保证，或端到端低延迟通过结论。

## 后续只读快照

- `2026-10-01T07:30:42.489507Z`：admission `active`，PID 245060，`NRestarts=0`，启动时间仍为 07:20:32Z，部署脚本哈希仍匹配。
- OAuth 状态：`inflight=2`、`pending=0`、`failure_streak=0`、`cooldown_remaining=0`、`half_open_probe=false`；说明该瞬间已不处于冷却，不证明下一次请求可用。
- `retired_readers=0`、`retired_readers_total=1`；无当前滞留 reader，历史计数保留。
- OAuth 配置仍为 `max_inflight=2`、`max_pending=4`、`queue_timeout_seconds=120`；Luna 与 Sol 共用该账号通道。
- 已装 Cockpit 主程序与 sidecar 哈希分别为 `5CDAA7C32D85D3BBDDFB4734A85A44A1CF8F5891034AE927071B8034871393BE`、`6A996BE7B8E4F53812BC93140FABBB63341F72F7D66D0F06029F7992BF7B7A23`，与既有补丁构建一致；本轮未重启或替换本机程序。

## 回滚

- 本轮未执行回滚；已核实备份 admission 存在且哈希如上，配置与备份相同。
- 如需撤销本次逻辑修复，应在取得远端写入授权后，使用相同维护锁，确认部署文件没有后续变更，再仅从上述备份恢复 `/opt/cliproxyapi/cpa-admission.py`，保留其 755 权限。
- 对恢复文件进行语法检查后重启 `cpa-admission.service`；复验 `/healthz`、8318 loopback 监听和严格 doctor。重启会影响在途请求，须安排空闲窗口。
- 不恢复未变化的凭据或整个配置，不通过 Git 回滚代替远端恢复，不把尚未执行的回滚写为 `ROLLBACK_VERIFIED`。
