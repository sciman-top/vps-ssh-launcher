# CPA admission 与 Cockpit Direct API 并发闸门：验证、部署及实战证据

日期：2026-10-01。本文中的本地时间为 Asia/Shanghai（UTC+08:00）。

## 结论与边界

本次修复需区分三个版本及其证据。VPS admission 于 2026-10-01 17:27:53 本地完成投影并加载修复源码；当时的单次流式回放中 DeepSeek 与 GLM 通过，Luna 被 admission 冷却拒绝。随后新增的 Direct/fixed-provider Retry-After 等待修复已通过源码回归、package 测试、vet、格式检查及构建，并已由并行会话投影到安装文件；Direct 10909 于 18:26:18 启动的新进程已加载该 SHA-256。VPS 服务和配置也由并行维护会话变更，当前 admission 正常。修正后的 Direct 10909 单请求回放中 `gpt-6.1-sol` 通过，并由 VPS journal 在同一时间窗口以 `200/capacity=false` 对照确认；Luna 的早先采集脚本中止，不能把那一次标为通过。因此不能宣称上游容量问题或所有 429 已解决。

| 验证层级 | VPS admission 修复 | Cockpit 基础并发闸门 | Cockpit 新增 Retry-After 等待修复 |
| --- | --- | --- | --- |
| repo_verified | 已验证并按授权提交；补充类型检查保留基线问题 | 原有 Go 回归、测试及构建通过 | 修复前复现；修复后定向回归、package 全量测试、vet、格式及构建通过 |
| filesystem_projected | true：源码指纹仍匹配，原始备份存在；config.yaml 已漂移 | true：安装文件 SHA-256 与新产物匹配，并保留 `cockpit-cliproxy.pre-backoff-20261001.exe` | true：新产物已成为安装文件 |
| host_loaded | 修复源码指纹匹配；当前 MainPID=252351，18:05:16 启动，来自并行维护 | 10909 PID=26284，18:26:18 启动，路径及 SHA-256 匹配新产物 | true：Direct 10909 已加载；14185 仍是 16:28:50 启动的旧内存实例 |
| controlled_live_replay | 历史单次回放：DeepSeek、GLM PASS；Luna 429/cooldown FAIL；均无重试 | 新进程已加载；`gpt-6.1-sol` 单请求 PASS，并由 VPS journal 对照 | Direct 路径已达到单模型受控通过；Luna 本轮采集器中止，未重复请求 |
| live_accepted | 尚未达到；近期自然流量仅为有界观察 | 尚未达到 | 尚未达到 |

## 授权范围

- VPS 仅提交并投影本次 admission 修复，保留回滚备份；不推送，不修改 CPA/provider/nginx 配置，不重启 CPA 容器。
- Cockpit 使用已授权的 v1.3.64 实际源码目录，仅追加 Direct API 并发闸门与回归测试，构建 sidecar；安装和重启范围仅为经重新核实的 10909 sidecar。
- 不停止 Cockpit 主程序、OAuth 14185 sidecar、ChatGPT 或 Codex，不改凭据、ACL 或既有并发额度。
- 实战验收仅允许顺序、有限请求、每个目标模型单次请求、无重试；429/503 停止受影响路由。

## VPS 源码修复与验证

仓库：`D:\CODE\vps-ssh-launcher`。

已提交：`533fcfaf24cf4944673bd054051cc288107378b5`，标题为 `fix: preserve CPA backoff and bound FIFO lane admission`。未推送。提交仅包含：

- `scripts/remote/cpa-admission.py`
- `test_cpa_admission.py`
- `docs/runbooks/cpa-gateway.md`

修复覆盖 FIFO 入场与取消清理、禁止新请求插队、入场前检查存活状态与期限、半开探测共享 inflight 上限、旧租约成功不得清除更新的失败/Retry-After 状态，以及显式上游 Retry-After 不得被提前探测绕过。请求关联 ID 由 admission 生成，并区分本地 admission 拒绝与上游拒绝；日志不记录凭据、请求正文或原始客户端身份。不增加内部重试、不改模型名称、不提高限额。

本轮已完成的验证：

- Python 编译检查通过。
- pytest：242 passed，1 skipped，258 subtests passed，46.08 秒。
- Bandit 通过；存在基线 nosec B507 提示，不作为新增漏洞结论。
- 配置范围内 Ruff 检查、22 个文件格式检查及 mypy 检查通过。
- admission 脚本补充 Ruff 检查与格式检查通过。
- admission 脚本补充 mypy 检查仍有 7 个错误；与修复前基线比较，错误类别与数量相同，不能声称该补充检查通过。
- `git diff --check` 通过。

候选源码按 LF 标准化后的 SHA-256：

`ffb5b28e56ec2559a8c9a0780ac44e1db3d38ad24cdd7081ec00e457fb0f8f74`

## VPS 部署前历史阶段：安全前置条件阻断

2026-10-01 09:19:29.511846 UTC（17:19:29.511846 本地），部署回执为 `REFUSED`，原因 `breaker_not_recovered`。写入与重启前即停止，`filesystem_projected=false`、`host_loaded=false`，该事务真实上游请求数为 0。

当时 `chatgpt-oauth`：cooldown_remaining=3，failure_streak=2，inflight=0，pending=0，half_open_probe=false。DeepSeek 与 Zhipu lane 的以上指标均为空闲/零值。此前曾观察到 OAuth 自然恢复但仍有活动请求；之后重新检查时再次出现熔断，不能据此推定持续恢复。

部署前只读检查：2026-10-01 09:22:46.012626 UTC（17:22:46.012626 本地）。

- `/opt/cliproxyapi/cpa-admission.py` 仍为旧 SHA-256：`bc86a8255b1ccfbde74261e3dfe1577cae158f0cad1ce7eea08dac3c29673e4b`。
- `cpa-admission.service`：active/running，MainPID=245060，NRestarts=0。
- 服务启动时间仍为 2026-10-01 07:20:32 UTC。
- OAuth lane：cooldown_remaining=2，failure_streak=2，inflight=0，pending=0，half_open_probe=false。
- DeepSeek 与 Zhipu lane：cooldown_remaining=0，failure_streak=0，inflight=0，pending=0，half_open_probe=false。
- 本轮 admission-only 新备份目录数量为 0；并未进入备份和替换阶段。
- 该次检查真实上游请求数为 0。

该阶段受保护配置的 SHA-256 与本轮部署前指纹一致：

| 文件 | SHA-256 |
| --- | --- |
| cpa-admission.json | 0f97942da161418a56561e1d0a91877a9d73dca92e114cc03236e6dc0aa38faf |
| config.yaml | 52c295f25e9f67c10f638760f9b3e384e7c948c187039cb225ee10f90b3c5698 |
| compose.yml | d3d130286a2cb5b440388396e4a272f0c370b0006f91c5bef3d265b5a89ba92a |

没有通过强制重启、清空熔断状态或发送恢复探测来绕过上述条件，也没有执行 guardrails 的全量 `-Apply`。以上是后续成功部署之前的历史状态，不代表最终部署结果。

## VPS 直接部署与运行态验证

用户随后确认授权直接部署。2026-10-01 09:27:52.925092 至 09:27:53.881542 UTC（17:27:52 至 17:27:53 本地），重新核实三个 lane 均无 inflight、pending、失败 streak、冷却或半开探测后，admission-only 部署事务返回 `PASS`：

- `filesystem_projected=true`、`host_loaded=true`；部署提交为 `533fcfaf24cf4944673bd054051cc288107378b5`。
- `/opt/cliproxyapi/cpa-admission.py` SHA-256 为 `ffb5b28e56ec2559a8c9a0780ac44e1db3d38ad24cdd7081ec00e457fb0f8f74`，文件模式为 0755。
- 回滚备份目录：`/root/cpa-admission-only-backup-20261001T092752.925051Z`。
- 仅重启 `cpa-admission.service`；MainPID 从 245060 变为 249191，启动时间为 2026-10-01 09:27:53 UTC，active/running、NRestarts=0。
- admission/provider/compose/nginx/systemd 受保护配置在部署事务前后未变；CPA compose 容器身份、启动时间和重启计数未变。
- 部署事务真实上游请求数为 0；未执行全量 guardrails `-Apply`，未增加并发额度，未绕过尚有效的 Retry-After。

2026-10-01 17:30:45 本地，只读 strict doctor 返回退出码 0，捕获输出中匹配新 admission 源码指纹。输出中还出现 7 个 `ERROR` 与 1 个 `WARN` 字面 token；该计数不能单独用作当前健康状态判定，也不证明所有历史容量错误已消除。

2026-10-01 09:33:43.245269 UTC（17:33:43 本地），从已部署源码加载独立、内存内的测试状态执行四项不变量验证，结果 `PASS`：显式 Retry-After 不被本地上限截短或提前探测绕过；过时探测成功保留更新的退避；半开探测共享槽位上限且超时清理队列；已取消客户端不得获准入场。测试未修改运行服务的 lane 状态，真实上游请求数为 0。

2026-10-01 09:37:48.446861 UTC（17:37:48 本地）最终只读回读：新源码指纹、MainPID=249191、active/running、NRestarts=0 及备份目录均匹配；config.yaml 与 compose.yml 指纹未变。该次检查误用不存在的 `/opt/cliproxyapi/admission.json`，故汇总回执为 `FAIL`。09:38:19.142964 UTC（17:38:19 本地）根据 systemd ExecStart 重新核实实际配置是 `/opt/cliproxyapi/cpa-admission.json`，其 SHA-256 与部署前一致，路径纠正回执为 `PASS`；没有发生配置漂移或为此修改配置。

17:37:48 本地快照中 OAuth lane 冷却剩余 104 秒、failure_streak=4、inflight=0、pending=0、无半开探测；DeepSeek 与 Zhipu lane 以上计数均为空闲/零值。部署成功不代表 OAuth 上游持续恢复，不清空该状态或追加恢复请求。

## VPS 部署后的受控流式回放

回放边界均为现有 10909 sidecar 到新 VPS admission；不是尚未安装的 Cockpit 新闸门的运行态验收。三个目标模型顺序各发一次客户端 POST，自动重试数均为 0。通过条件包含 HTTP 200、真实 `response.completed` 且终态为 completed、非空正文 delta，以及无 error/incomplete 或畸形事件；仅 HTTP 200 不足以通过。

| 本地请求记录时间 | 模型 | 结果 | HTTP | 首正文 delta | 总耗时 | 正文 delta / 字符数 |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-10-01 17:33:41 | gpt-6-luna | FAIL：cooldown，Retry-After=49 秒 | 429 | 无 | 229ms | 0 / 0 |
| 2026-10-01 17:35:02 | deepseek-flash | PASS：completed | 200 | 998ms | 1010ms | 1 / 2 |
| 2026-10-01 17:35:03 | glm-5.3 | PASS：completed | 200 | 2628ms | 2629ms | 1 / 2 |

Luna 响应携带 `X-CPA-Admission-Reason: cooldown`，属于本地 admission 入场前拒绝，不应计作该请求访问上游后产生的新容量错误；已停止受影响 OAuth 路由，未重试、未绕过 admission 追加直连探测。DeepSeek 与 GLM 均完成非空流式输出且无错误/未完成/畸形事件；两字符短回复不构成持续吞吐或长会话性能验收。

请求关联 ID：Luna `b2804511b1974614ae11980d56021fab`；DeepSeek `0c7b98ce112e4e7085536271deea0ed2`；GLM `ef7a5bc707f946779d8868709a5d4415`。受控回放通过不等同于用户 Desktop 自然会话的 `live_accepted`。

## Cockpit Direct API 并发闸门

实际源码目录：`C:\Users\sciman\AppData\Local\Temp\cockpit-tools-v1.3.64-audit-20261001`。

源码基线 HEAD：`4434c32e7bb02f33246941eb5179b0252a93481a`。本次新增修改限于：

- `sidecars/cockpit-cliproxy/provider_gateway.go`：Direct 与 fixed-provider 路径接入同一账号闸门。
- `sidecars/cockpit-cliproxy/provider_gateway_concurrency.go`：复用已有账号槽位跟踪器、请求生命周期释放和有界等待机制。
- `sidecars/cockpit-cliproxy/provider_gateway_concurrency_test.go`：新增回归。

账号槽位使用 `cockpit-provider:<accountID>`，不同 API key 与不同模型不能绕过同一账号的并发限制；槽位覆盖完整 SSE 生命周期。等待取消、超时和全局等待人数上限均保持有界。上游错误正文及 Retry-After 保持透传。保留实际源码目录中的既有 bootstrap 和 Rust 配置生成器修改，不重新应用 v1.3.64 官方源码已包含的 SSE flush 修复。

回归先在未修复路径复现：持有 SSE 的首个请求未结束时，第二个请求仍返回 200 且上游计数为 2；等待、取消及超时用例也观察到绕过账号槽位。fixed-provider 的早期 fixture 缺少声明模型，属于测试夹具问题，修正后验证两个路径。

修复后的定向测试、当前 Go package 全量 `go test .`、`go vet .`、三个修改文件的 gofmt 检查以及构建均通过。回归覆盖 SSE 结束前持有槽位、多 key/多模型共享账号限制、等待释放、取消、超时、槽位/等待计数清理及上游 429 的 Retry-After 透传。

构建产物：

`C:\Users\sciman\AppData\Local\Temp\cockpit-tools-v1.3.64-audit-20261001\sidecars\cockpit-cliproxy\cockpit-cliproxy-direct-gate-20261001.exe`

产物 SHA-256：`9CF5D3B2E4922BD7A1E0FB3CCA6353EED1C2B1A3DEF15764F868DEFBC9DCFC7C`。

工具链为 go1.26.3/windows/amd64，CGO_ENABLED=0；使用 `-trimpath` 与 `-ldflags '-s -w'` 构建。仅证明产物构建成功，不证明运行中的 sidecar 已加载它。

## 历史：Cockpit 安装与重启被执行策略阻断

以下拒绝及未部署结论对应 17:18 至 17:37 的历史检查。后续只读检查发现基础产物已由本轮操作之外的流程安装，详见后文；该变化不能证明执行策略已放行，也不能证明新增退避版本已部署。

安装/有界重启事务在 CreateProcess 前被执行策略拒绝，报 `blocked by policy`；命令没有运行。用户随后确认授权直接部署，再次同范围尝试仍在 CreateProcess 前被拒绝；没有本地备份、替换或重启成功回执。未改用另一 shell、编码脚本、拆分命令或其他工具绕过拒绝。

2026-10-01 17:18:27.0658666 本地检查：新备份数量为 0，新投影日志数量为 0，安装文件与原进程未变。

2026-10-01 17:21:59.8702732 本地检查，及再次拒绝后 17:37:48.1323739 本地最终回读：

- 安装文件 `C:\Users\sciman\AppData\Local\Cockpit Tools\cockpit-cliproxy.exe` SHA-256 仍为 `6A996BE7B8E4F53812BC93140FABBB63341F72F7D66D0F06029F7992BF7B7A23`。
- 与新产物 SHA-256 不同，不能认定已安装。
- Direct 10909 listener PID=7672。
- OAuth 14185 listener PID=30712。
- Cockpit 主进程 PID=23428 仍存在。
- Direct 进程父 PID=23428，运行可执行路径与安装路径匹配。
- 活动 manifest SHA-256=`3F8825F5301B8A5850B388BC12AA61E74CCF5D410C28EB343E7DE2E615825ED3`；实际生成配置为 `config.json`，SHA-256=`B35834CAD79423AF1A9C98E42AC8E568A733AFFB0F8046386FD8B9B35668B718`，均未变。
- manifest 中 `maxAccountConcurrency=1`、`accountConcurrencyWaitMs=120000`；字段存在不等于旧运行二进制已执行本次新增 Direct 闸门。

本次 Go 修复未提交或推送。源目录中的无关既有修改保留。

## 新增 Retry-After 等待修复及源码验证

基础并发闸门限制同一绑定账户的同时请求，但不缓存上一响应的 Retry-After；后续独立请求仍可能在上游冷却窗口内立即发出。修复前的本地 mock 回归已复现 Direct/fixed 路径未等待，以及取消、超时、零等待请求继续到达 mock 上游的问题。

本次修改仅位于已授权的 v1.3.64 临时源码目录，不修改受保护的外部源码目录、凭据、模型路由或并发额度：

- provider_gateway.go：在解析实际上游模型后、发送请求前执行闸门；在转发响应前记录 429/503 的有效 Retry-After，保留原响应，不自动重试。
- manifest_policy.go：将退避检查和账户槽位预留放在同一 tracker 锁内。
- provider_gateway_concurrency.go：以绑定账户、网关地址和实际上游模型缓存等待期限；支持秒数及 HTTP 日期，较短期限或成功响应不清除仍有效的较长期限。
- provider_gateway_concurrency_test.go 与 provider_gateway_backoff_test.go：保留基础并发测试，补充跨 key 等待、取消、超时、零等待、解析、绑定隔离及等待不占账户槽位的回归。

等待受既有 120000 ms 上限、队列上限及客户端取消约束；等待期间不占用账户槽位。不同模型仍共享原有账户并发额度。该本地缓存仅遵守相应请求收到的 Retry-After，不据此判断上游过载是否仅限某模型，不拆分或绕过 VPS 的 OAuth 共享保护。此前 server_is_overloaded 样本不足以证明模型级容量故障，故本轮未修改 VPS 的保护范围。新增逻辑仅覆盖带绑定账户的 Direct/fixed-provider 路径，不将自动路由宣称为同等覆盖。

2026-10-01 18:08 源码验证回执：

- 定向回归：go test . -run '^TestDirectProvider(Backoff|Concurrency)' -count=1 -timeout 45s，通过，耗时 2.361 s。
- 当前 sidecar package 全量测试：go test . -count=1 -timeout 120s，通过，耗时 3.750 s。
- go vet .、五个相关 Go 文件的 gofmt 检查及相关已跟踪文件的 git diff --check 均通过；Git 的 LF/CRLF 提示不是测试失败。
- 所有新增回归使用本地 mock；本轮真实 provider 测试请求为 0。未运行 race detector，不把本次结果描述为竞态检测通过。

新增独立产物：

`C:\Users\sciman\AppData\Local\Temp\cockpit-tools-v1.3.64-audit-20261001\sidecars\cockpit-cliproxy\cockpit-cliproxy-direct-gate-backoff-20261001.exe`

SHA-256：`C7335D546F3A2A395BAED4BB97ABC4FA6E56FF66F686DA34D63E0BB1A8FAA291`。构建回执时间 18:08:23；工具链 go1.26.3/windows/amd64、CGO_ENABLED=0，使用 -trimpath 与 -ldflags '-s -w'。未覆盖基础闸门产物，也未覆盖安装文件。

## 18:08 至 18:12 运行状态及并行变化

本地只读检查：

- 安装文件 SHA-256 为基础并发闸门的 `9CF5D3B2E4922BD7A1E0FB3CCA6353EED1C2B1A3DEF15764F868DEFBC9DCFC7C`，不是新增退避产物的 SHA-256。
- Direct 10909 PID=28824，启动时间 17:59:09.133；可执行路径匹配安装路径，文件时间早于进程启动时间，命令参数引用预期 runtime root、config.json 和 manifest.json。
- Windows 创建父 PID=33104 已不在，但参数中的逻辑监管 PID=23428 存在；不能仅依据创建父进程消失断言 sidecar 无监管。检查时存在 1 条已建立的 Direct 连接，未中断。
- OAuth 14185 PID=30712、Cockpit PID=23428 仍存在；本轮未停止或重启这些进程。
- Direct config.json SHA-256 仍为 `B35834CAD79423AF1A9C98E42AC8E568A733AFFB0F8046386FD8B9B35668B718`；manifest.json SHA-256 仍为 `3F8825F5301B8A5850B388BC12AA61E74CCF5D410C28EB343E7DE2E615825ED3`。maxAccountConcurrency=1、accountConcurrencyWaitMs=120000 未改变。

VPS 18:11:17 状态及 18:12:52 配置回读：

- admission 源码 SHA-256 仍为 `ffb5b28e56ec2559a8c9a0780ac44e1db3d38ad24cdd7081ec00e457fb0f8f74`；admission.json 与 compose.yml 指纹仍与此前记录匹配。原始 admission 备份目录及其中 cpa-admission.py 仍存在。
- admission.service active/running，MainPID=252351、NRestarts=0，启动时间 18:05:16。该启动不是本轮操作；NRestarts=0 不能据此断言从未被人工重启。
- config.yaml 当前 SHA-256 为 `3f9b3ee7330f6264c85f0d74c7fe0e6b8c1e79b6b9e303f5f7bcff7a6af0830b`，不同于原记录；修改时间 18:05:12.776003。本轮未修改该文件，未回滚未知来源的变化。
- 白名单字段回读：request-retry=0、max-retry-credentials=1、max-retry-interval 未设置、save-cooldown-status=false、disable-cooling=false、transient-error-cooldown-seconds=60、codex.stream-bootstrap-buffering=false、codex.stream-bootstrap-timeout="0"。这些字段不代表已审计整个配置差异，也不能由文件回读证明 CPA 容器已经加载它们。
- 状态快照中 OAuth inflight=1、pending=0，三个受保护 lane 的 failure_streak 与 cooldown_remaining 均为 0，retired_readers=0；仅为该时刻观察，不清除冷却、不扩大额度。
- 从 17:59:09 起、最多 2000 条 journal 的有界窗口中，Luna 有 2 条、Sol 有 6 条、GLM 有 2 条 upstream_result HTTP 200/capacity=false；未见 lane_reject 或 capacity=true。另有 passthrough 404/502/503，不把它们归为受保护模型的容量事件。

这些自然流量不能归属到特定客户端，也没有逐条证明 SSE 非空完成，因此不替代实战验收或持续稳定性证明。本轮只读检查没有远端修改，没有真实模型测试请求，也没有重试安装事务或绕过历史策略拒绝。

## 并行会话部署后的审核（18:47 至 19:05）

本轮重新读取到的 Direct 状态：

- 安装文件 `C:\Users\sciman\AppData\Local\Cockpit Tools\cockpit-cliproxy.exe`、候选文件 `cockpit-cliproxy-direct-gate-backoff-20261001.exe` 的 SHA-256 均为 `C7335D546F3A2A395BAED4BB97ABC4FA6E56FF66F686DA34D63E0BB1A8FAA291`，大小均为 44,314,624 字节。
- 安装文件修改时间为 18:08:23.444；Direct 10909 PID=26284，启动时间为 18:26:18.354865，使用同一路径和 runtime root、`config.json`、`manifest.json`。该时间顺序和进程指纹足以证明新二进制进入 Direct 进程。
- 安装目录保留 `cockpit-cliproxy.pre-backoff-20261001.exe`；其大小为 44,304,896 字节，作为本次替换前的基础闸门备份。未删除任何备份。
- Direct 当前配置 SHA-256=`B35834CAD79423AF1A9C98E42AC8E568A733AFFB0F8046386FD8B9B35668B718`，manifest SHA-256=`3F8825F5301B8A5850B388BC12AA61E74CCF5D410C28EB343E7DE2E615825ED3`，`maxAccountConcurrency=1`、`accountConcurrencyWaitMs=120000`、`request-retry=0`。
- OAuth 14185 仍由同名安装路径提供，但 PID=30712 于 16:28:50 启动，早于 18:08:23 的文件替换；本次 Direct 版本审核不能外推到 OAuth 14185。用户当前目标是 Direct API，因此没有为 OAuth 进程追加重启。

VPS 并行维护后的只读回读：

- 18:49:17 admission.service active/running，MainPID=252351、NRestarts=0，源码 SHA-256=`ffb5b28e56ec2559a8c9a0780ac44e1db3d38ad24cdd7081ec00e457fb0f8f74`，当前三个 lane 均 `failure_streak=0`、`cooldown_remaining=0`，`retired_readers=0`。
- 从该服务启动起的 journal 有 13 条 Luna `200/capacity=false`、13 条 Sol `200/capacity=false`，另有 1 条 Sol `200/capacity=true`、1 条 Sol `502/capacity=true` 和 1 条 Sol `503/capacity=true`。Sol 的上游容量波动仍存在，但当前没有形成 admission 冷却；Luna 当前窗口没有容量失败或 lane 拒绝。
- 18:56:08 的相关 journal 又记录了多条 Luna 200；这些请求不能可靠归属到本轮采集器，不能作为新 Direct 版本的逐条验收。

本轮先对 Direct 10909 发起了一次 `gpt-6-luna` 单请求、无重试流式回放；采集器在读取空的 `Retry-After` 响应头时异常退出，未记录完整 HTTP/SSE 结果，因此不把它标为 PASS，也不重复发送同一模型请求。随后使用修正后的空头处理对 `gpt-6.1-sol` 发起唯一一次单请求：HTTP 200，首个正文事件 915 ms，总耗时 4145 ms，9 个 SSE data 行，包含 `response.completed`，无 error/incomplete，自动重试 0。VPS journal 在 19:03:24.605 记录同一模型 `upstream_result status=200 capacity=false`，与本地回放对照一致；当时三个 lane 均无冷却、无排队、无半开探测。VPS 侧未发生本轮远端修改。

## 19:33 至 19:52 用户现场 429 归因（本地闸门等待超时）

19:43 用户报告 ChatGPT desktop 仍频繁 429。随后只读归因（无远端修改、无测试请求）：

- VPS 19:50 探针：healthz 三 lane 均无冷却、无半开探测，OAuth inflight=1；admission journal 最近 90 分钟 0 条 `lane_reject`，全部 `upstream_result status=200 capacity=false`（其中 OAuth lane 有 `waited_ms=9102` 至 `29949` 的排队后成功）；nginx 当前日志 429 计数与 18:51 doctor 完全一致（237），即该窗口 VPS 零新增 429。
- 本地 `logs/codex-api.log.2026-10-01`：19:00 后 provider gateway 路径完成 25×200、5×429、1×400。5 条 429 的 `latencyMs` 为 120019–120061（模型 gpt-5.5、gpt-6.1-sol×3、gpt-5.6-terra），与 manifest `accountConcurrencyWaitMs=120000` 精确对应，属于新 sidecar 本地排队等待超时后自行返回的 429，未到达 VPS。
- 同窗口 27 条 200 中 22 条耗时 ≥35 秒（35.9s–407.0s，中位约 60–90s）：单条慢流长时间占满 `maxAccountConcurrency=1` 的唯一账户槽位，是排队超时 429 的直接成因；同时也再次印证上游 OAuth lane 流式吐字慢仍然存在，属上游容量面而非本机链路新增延迟。
- 历史 429 分两段：15:26–17:37 的紧簇（latency 200ms–1.1s 为主，另有 19.7s/24.3s/44.1s 散点）对应旧二进制与基础闸门时期；18:26 退避二进制加载后紧簇消失，仅剩 120s 等待超时型。Retry-After 等待修复按设计生效。

结论：admission 与本地闸门均按设计工作并已挡住对上游的重试风暴；用户当前看到的 desktop 429 是 `maxAccountConcurrency=1` 叠加慢流占槽产生的新本地失败面，属容量/配置权衡问题，不是本次修复的回归。

## 20:00 至 20:14 并发参数执行与 luna 实战验收

用户授权按建议连续执行后，本会话完成：

- 真源修改：`~/.cockpit_tools/codex_local_access.json` 顶层 `maxAccountConcurrency` 1→2（修改前备份 `codex_local_access.json.bak-maxconc1-20261001T2000`）。源码核实该字段经 `load_collection_from_disk()` 同时驱动 14185 与 10909 两个 sidecar 的 manifest 再生成（provider gateway 的 collection 亦以同一磁盘 collection 为模板），上限常量 `MAX_ACCOUNT_CONCURRENCY_LIMIT=64`，值 2 合法。
- 重启窗口：19:59 优雅关闭未退出（托盘驻留），20:00 强制结束 Cockpit 进程树并确认 14185/10909 监听释放，随后编辑真源并重启。20:09:22 出现一次托盘 `quit`（用户手动退出第一次重启的实例），20:09:52 二次启动后稳定。
- 加载验证：主程序 PID=30516（20:09:52）；OAuth 14185 PID=13352（20:09:53）、Direct 10909 PID=15916（20:09:55），两进程可执行路径均为安装路径（SHA-256 `C7335D546F3A2A395BAED4BB97ABC4FA6E56FF66F686DA34D63E0BB1A8FAA291`）。双 manifest 再生成后均为 `maxAccountConcurrency=2`、`accountConcurrencyWaitMs=120000`；未认证 `GET /v1/models` 双端 401。本文此前的"14185 旧内存实例"状态由 19:33 与本次重启自然消除。
- luna 受控单请求回放（20:12，经 10909，attempt=1、retries=0、max_output_tokens=16）：HTTP 200、`response.completed`、无 error/incomplete，首个 data 事件 31.1s、总耗时 40.6s。VPS journal 同窗对照 `upstream_result lane=chatgpt-oauth model=gpt-6-luna status=200 capacity=false waited_ms=0 route=responses`（request_id `0a7b9e7d440c45e5b910a303e0bacd1d`）——即时入场证明 31s 首字节为上游延迟而非本机或 VPS 排队，慢吐字仍属上游容量面。同窗另有 route=chat 的 luna 请求排队 4.0–25.7s 后 200，及 1 条 `downstream_disconnect`（waited 36.2s）与 1 条 `lane_reject reason=downstream_gone`（waited 45.8s），与既有 45s 探针簇形态一致，不归因为本次变更。
- 披露：manifest 检查时嵌套的 `providerGateway.apiKey` 字段曾一次性回显于本地会话输出；未写入任何文件、证据或提交，是否轮换为用户侧选项。

## 恢复工作所需条件

1. 并行会话已完成 Direct 新产物投影和 10909 重启；当前无需重复替换或重启。后续若继续验收，先保持该进程和配置稳定。
2. Direct API 已有 `gpt-6.1-sol` 的受控通过；Luna 那次采集器中止，不重复消费同一模型。后续若要单独验收 Luna，应在新的明确验收窗口使用修正采集器，并保持每目标模型单次、无重试，429/503 立即停止受影响路由。
3. 14185 OAuth 仍是旧内存实例；除非重新纳入 OAuth 目标，否则不把 Direct 结论外推到 OAuth，也不为此扩大重启范围。
4. 针对 120s 排队超时 429 的候选缓解：`maxAccountConcurrency` 1→2 已于 20:00–20:14 执行并验证（见上文）；`accountConcurrencyWaitMs` 保持 120000 未改。结构性解法仍是增加第二个 OAuth 账号（需用户先提供第二个 ChatGPT Plus 账号并完成浏览器授权登录，随后按既定流程加入 CPA、镜像 excluded-models 清单并按双账号复核 admission lane 并发上限）。

目前没有满足新增修复端到端实战验收条件，不宣称上游容量问题或所有 429 已解决。
