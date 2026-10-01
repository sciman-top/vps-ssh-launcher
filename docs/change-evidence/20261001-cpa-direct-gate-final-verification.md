# CPA admission 与 Cockpit Direct API 并发闸门：验证、部署及实战证据

日期：2026-10-01。本文中的本地时间为 Asia/Shanghai（UTC+08:00）。

## 结论与边界

本次新增修复已完成源码验证。VPS admission 于 2026-10-01 17:27:53 本地完成投影并加载新源码；部署后的单次流式回放中 DeepSeek 与 GLM 通过，Luna 被 admission 冷却拒绝。Cockpit 新闸门构建成功，但安装/10909 有界重启仍被执行策略阻断，运行进程未加载新产物。因此尚未完成两侧新增修复的端到端实战验收，不能宣称上游容量问题或所有 429 已解决。

| 验证层级 | VPS admission 新增修复 | Cockpit Direct API 新增闸门 |
| --- | --- | --- |
| repo_verified | 已验证并按授权提交；补充类型检查保留基线问题，见下文 | Go 定向回归、当前 package 全量测试、vet、格式及构建通过 |
| filesystem_projected | true：仅 admission 源码原子替换，备份存在 | false：安装文件仍是旧 SHA-256 |
| host_loaded | true：MainPID=249191，新源码指纹匹配 | false：新产物未安装，10909 原 PID 未变 |
| controlled_live_replay | DeepSeek、GLM PASS；Luna 429/cooldown FAIL；均单次无重试 | 未进行新二进制加载后的实战回放 |
| live_accepted | 尚未达到 | 尚未达到 |

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

## Cockpit 安装与重启被执行策略阻断

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

## 恢复工作所需条件

1. VPS 本次投影与加载已完成，无需再次投影以清空冷却。OAuth/Luna 的实战验收未通过，保留 Retry-After 和原始结果；本轮不再追加该路由请求。
2. 本地安装/10909 有界重启需要执行策略允许该操作，或由用户选择并执行人工维护流程。已有任务授权不等同于执行策略已放行；不扩大到主程序或 OAuth sidecar 重启。
3. Cockpit 新二进制实际投影后仍需核对指纹、进程与受保护配置，再按届时授权边界验收；不能复用本轮旧 sidecar 的成功回放证明新闸门已加载。429/503 停止相应路由；HTTP 200 的 SSE 仍必须检查真实完成事件、非空输出及无 error/incomplete/capacity 事件。

目前没有满足新增修复端到端实战验收条件，不宣称上游容量问题或所有 429 已解决。
