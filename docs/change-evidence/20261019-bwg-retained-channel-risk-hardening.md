# BWG CPA 保留账号与渠道的风险防护落地（2026-10-19）

## 授权与边界

- 用户当次指令：现有 OAuth 账号和 API key 渠道都要保留，授权连续自动修复和优化。
- 仅 BWG；保留全部凭据、API 渠道、模型别名、公网 capability path、compose/DNS 配置与 CPA 镜像。
- 未重新登录、轮换凭据、切换 IP/UA、隔离 OAuth lane、增加并发或执行有效账号的生成探针。
- 并行任务原有的 WorkBuddy landing/runbook/risk-controls 改动未纳入本次提交。

## 仓库修复

- `79b3be8`：插件模型目录识别；ChatGPT 明确账号/鉴权风险持久化；WorkBuddy 独立 admission lane（并发 1）；恢复探针不能绕过人工 hold；修正事故分级。
- ChatGPT 保留并发 2、单模型 capacity scope 和原有受控容量恢复逻辑。持久账号风险与严格容量退避分别判定。
- 四条 OAuth lane 都使用独立风险状态文件；写前状态、异常退出后人工复核、明确错误信封及 SSE 提前锁定沿用既有实现。
- CPA 自身的 `{"error":"Invalid API key"}` / `Missing API key` 是客户端鉴权拒绝，不得锁住共享 OAuth 账号；在识别完整错误信封后、写给客户端前锁定真正的鉴权风险。
- WorkBuddy `11140 / request illegal` 或明确账号限制进入人工复核；未知配额等待窗口不会编造短 reset。`11128` 的 system-first 格式错误不据此认定封号。
- `6030f65`：固定上游 v0.8.68 的可重放 `selected` 模式补丁；保留 `off/credits` 兼容行为。生产改用 `selected`，所选账号耗尽、缺失、禁用或冷却时明确 Reject，不委托内置调度自动换号。面板人工选号仍可用。
- `selected` 的首次启动无选择时使用现有候选初始化逻辑；它不提供跨干净重启的持久账号 ID 绑定，也不宣称能够绕过或消除供应商政策风险。

## 部署一：admission 持久风险

- 入口：严格 SSH 经 `scripts/lib/project_environment.ps1` 的既有传输执行一次性事务；远端保留 `/root/cpa-oauth-risk-transaction-20261019.py`。
- 两次预检因空闲条件未满足，在运行时修改前退出。最终取得空闲窗口，持有既有 maintenance lock，编译/语义校验后部署。
- 备份：`/root/cpa-oauth-risk-backup-20261018T173948Z`。
- 六文件：admission 源码、JSON、systemd unit、完整性 pin、policy、route manifest；保留 live config、compose 和全部 auth 文件。
- 此阶段只重启 admission，CPA 容器 ID/StartedAt 未改变；凭据清单和内容均一致。
- Admission SHA-256：`a2d063b304718b7ef6c3d0fbd83956bb1672f6f70bbfa7bc902929ec64039615`。
- 回滚入口：`python3 /root/cpa-oauth-risk-backup-20261018T173948Z/rollback.py`；`--dry-run` 已通过。回滚不还原风险状态或凭据。

## 部署二：WorkBuddy 所选账号保护

- 来源：`mmqz/cpa-multi-plugins` v0.8.68，commit `aa3057b63d6dcc5ce7e4d83bb6d0a176d83b3d86`，MIT。
- 固定 tarball SHA-256：`6163f688bf902b7e366f4d4ca11ff674b1f042fd34d39e3289136fa87972e552`。
- 顺序应用本仓 INTL system-first 补丁与 selected-account 补丁；应用/反向检查通过。CPA v8.0.33 源码核对支持 SchedulerPickResponse 的 Reject ABI；插件旧 SDK 缺少该结构字段，补丁按宿主 JSON ABI 编码拒绝。
- 一次性源码副本位于 `/root/cpa-workbuddy-selected-build-20261019`，未操作外置参考仓。隔离容器无生产挂载或凭据，限制 1 CPU / 768 MiB / 128 pids；没有账号维护或模型请求。
- 构建镜像：`golang:1.26-bookworm`，本机 image ID `sha256:d9c68c2c51161e12fd77e4c6320687c9cd86e1af1e3ad6e6cd63ff970641453c`。
- Scheduler、dashboard、selected 模式及 INTL system-first 定向 Go 测试通过（`ok .../plugins/workbuddy 0.023s`）；CGO c-shared 构建完成。C 导出符号及动态依赖检查通过。
- SSH 构建观察曾因 60s 无输出退出；当时只读确认唯一构建容器仍运行，未重复启动构建。随后从该容器取得测试通过输出并确认产物完成，构建容器自动移除。
- 原现役 so SHA-256：`815a8f23b73ee037287ca7e3c290e9815bce9e8aec0d26358bae68cfa253ed23`。
- 新现役 so SHA-256：`700f62cf3d6f02fb013e0e91655362d6a8f6c37eae0f82bf8bd6e2a6fd1c62aa`。
- 备份：`/root/cpa-workbuddy-selected-backup-20261018T174724Z`；事务入口 `/root/cpa-workbuddy-selected-transaction-20261019.py`。
- 在数据面空闲窗口，备份插件与配置，校验新库在实际 CPA 容器内的依赖，原子 rename 换装 so。实际配置的语义变化仅 `plugins.configs.workbuddy.scheduler_mode: credits -> selected`，保留 bind-mounted 配置 inode。
- 短暂停止 admission 接收流量，重启 CPA 加载新库，readiness 与插件注册通过后恢复 admission。凭据清单及内容一致；compose/公网入口未变。未重启 Codex App。
- 回滚入口：`python3 /root/cpa-workbuddy-selected-backup-20261018T174724Z/rollback.py`；`--dry-run` 已通过。回滚恢复旧插件和配置并重启 CPA，不回滚凭据或风险状态；执行前仍需维护窗口。
- 升级时必须保留两项补丁的等价行为并确认宿主 Reject ABI；上游提供等价实现后再退役对应补丁，不把现役 so 当作原版上游二进制。

## 最低充分证明与分层结论

- `repo_verified`：完整门禁通过，671 tests / 317 subtests，1 项真实 SSH integration 按约定跳过；build、bandit、ruff、format、mypy 均通过。新增插件补丁另有固定源码的 Go 定向证明。
- `filesystem_projected`：六项投影哈希读回一致；so 哈希读回一致；两套备份及回滚 dry-run 通过。
- `host_loaded`：admission 四条 OAuth lane 持久状态加载；ChatGPT 2、Antigravity 2、Claude 2、WorkBuddy 1；最终均无人工 hold。WorkBuddy selected 配置、插件注册和 CPA readiness 通过。
- 两次部署后的最终严格 doctor：`DOCTOR_CONTRACT_OK`，四条 OAuth lane 为 `OPEN / hold_reason=none`；再次验证两套回滚入口 `--dry-run` 均通过。
- `controlled_live_replay`：本机 fake upstream 验证 HTTP/SSE 错误、正常文本不误判、风险跨重启保留、恢复探针不能越过人工 hold、单模型容量不扩大为整 lane；BWG 两次无效客户端 key 请求均返回 401，ChatGPT/WorkBuddy hold 仍为空，没有发送有效账号生成请求。
- `live_accepted`：本轮未执行真实账号生成、质量基准、压力或长期观察；doctor 成功和插件注册不证明供应商不会封号、限流或改变模型质量。

## 不能通过本次工程控制消除的风险

- 现存明文 API 渠道保持启用。不携带凭据的 BWG TLS 探测：原端口返回 `WRONG_VERSION_NUMBER`，443 `ConnectionRefusedError`；没有可直接替换的验证通过的 TLS 入口。供应商提供有效 HTTPS 端点前，不能宣称该段已加密；没有关闭证书校验、搭建伪加密包装或轮换 key。
- CPA v8.0.33 的 Claude 错误头透传缺口仍由上游修复跟踪（PR #6700）。本轮以可信最长 deadline 及未知窗口人工 hold 保护，未宣称已恢复丢失的上游响应头，也未替换 CPA 镜像。
- Antigravity/Claude/WorkBuddy 的第三方 OAuth 资格与服务条款风险不因本地低并发、选中账号或持久 hold 消失。保留渠道是用户明确决定，不等同于官方授权该用法。
- 多个 key 不能推导为独立项目/组织配额；供应商共享额度和其他客户端的重试仍须按实际使用方核对。服务端保护不会保证所有外部调用方配置一致。
- served/requested 模型命名差异或 turn-state 长度不是独立的降智证明。保留证据与人工质量复核入口，不用重复账号生成探测制造“安全绿灯”。

## 参考

- https://antigravity.google/terms
- https://antigravity.google/docs/faq
- https://code.claude.com/docs/en/legal-and-compliance
- https://www.codebuddy.ai/document/term
- https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms
- https://github.com/router-for-me/CLIProxyAPI/pull/6700

## 同日补充：HTTP 成功状态内的协议错误

- 用户再次明确现有账号、API key 和明文 HTTP 全部保留；本次只更新 admission 源码与完整性 pin，不修改 CPA live config、compose、插件或凭据。
- WorkBuddy `6003/6004` 仅在 WorkBuddy lane 的明确错误信封中识别；无可信等待期限时持久人工复核，有明确期限时执行 lane 冷却。正常生成文本中引用错误代码不触发保护。
- ChatGPT、Claude、WorkBuddy 在 HTTP 200 JSON/SSE 中携带的明确鉴权错误、禁用信号、硬额度/账单拒绝同样锁定；完整 JSON 错误在上游 EOF 前落盘，客户端中断不能绕过。硬额度拒绝不因 `Retry-After` 自动恢复。
- 定向回归首次为 178 passed；全量运行暴露原有探针测试的时间竞争：其 0.1s 下一探针间隔小于 0.5s 队列预算，等待者可合法成为下一探针。改为 1s 间隔与 1.1s 等待，保留原队列超时断言；该用例单独复验通过，生产探针策略未改。
- 全量首次结果 688 passed、3 failed、1 skipped、313 subtests passed；另两项失败来自并行会话正在修改的 renewTLS wrapper 与旧静态测试不一致。本任务未改动或吸收那些文件；另以当前 HEAD 加本任务三个代码/测试文件的临时快照执行完整门禁。
- 投影事务：严格 SSH、维护锁、校验旧 generation、Nginx worker 新连接 fence、8317/8318 socket 与 lane 空闲检查、候选编译、备份和回滚 dry-run 后，原子替换两个文件并只重启 admission；退出清理 fence。
- 备份与回滚：`/root/cpa-risk-followup-backup-20261019T150902Z/rollback.py`，`--dry-run` 通过。实际回滚仍需维护窗口；不恢复或清空风险状态。
- 事务 `/root/cpa-risk-followup-transaction-20261019.py` 返回 `DEPLOY_OK`；源码 SHA-256 `dc9065a76f81705b588b0b6c9074d11bee0495a90fd31de88942e2deed9613f9`；凭据清单和字节、live config、compose、CPA 容器 ID/StartedAt 均未变。
- `host_loaded`：四条 OAuth 持久状态加载通过；WorkBuddy 并发 1、ChatGPT 并发 2。未制造真实账号风险、额度或生成错误以验证保护。
- 当前 CPA 已为其他同日已授权事务部署的官方 v8.0.34；本补充未替换其镜像。PR #6700 于 2026-10-19 15:01 UTC 仍 open、目标 dev、未合并，已发布 v8.0.34 仍缺 Claude 错误响应头透传；未知期限人工 hold 继续保留。
- WorkBuddy request-scoped stop 配置属于候选：v8.0.34 支持按 provider `workbuddy` 的 HTTP status 与正文匹配，同步错误能保留 403/11140；异步流错误路径覆盖不同。本任务未凭此核验结果扩展修改生产配置。
