# CPA WorkBuddy 插件 runbook（bwg）

**状态**: WorkBuddy 插件实例已启用并注册（2026-10-18）；管理页显示“生效中 / 已注册”，有效插件 1/1。为降低非用户发起的账号活动，配置明确关闭 `checkin_auto`、`tasks_auto`、`lifecycle_auto`、`token_keepalive`；CPA 未重启。模型请求未做恢复后验收。
**适用主机**: bwg（CPA 容器 `cli-proxy-api`，镜像 `eceasy/cli-proxy-api:v8.0.34`，digest `sha256:439eb95f2ae9ba805d33a1e39d18a85c13cbd328f6545136dc9497cfc343bb9b` 锁定；2026-10-19 canary 事务升级，见 change-evidence 同日台账）。

## 1. 来源与固定版本

| 项 | 值 |
| --- | --- |
| 插件上游 | `github.com/mmqz/cpa-multi-plugins`（MIT） |
| release tag | `v0.8.68`（2026-10-12） |
| 插件版本 | workbuddy `0.9.58`（当前管理页与加载日志） |
| 产物 | `cpa-multi-plugins-linux-amd64.zip` |
| zip SHA-256 | `b54325f7c3426b0ca7083018e671af50f5e12b3311b42cefc2822cf573b910a5` |
| `workbuddy.so` SHA-256 | `5872d3da9d584cd25306ba3a329a8da1e898e333421b87271f84e33554a30edd` |
| 宿主版本 | CLIProxyAPI `v8.0.32`，镜像 digest `sha256:68f6e2d691f7b71aca21cfe87c77b7076cbea26c8ef490b15619b1623a7c0699` |

**当前在役构建（2026-10-19 换装，历史行保留作落地记录）**：源 tag `v0.8.72`
（workbuddy `0.9.60`，tarball SHA-256 `810c5457…e9ed`）+ `scripts/patches/` 之
selected-account、account-failover 两补丁重放（intl-system-first 补丁已由上游 PR #42
收编退役）；`workbuddy.so` SHA-256 `98dac310…a7e8`（12,012,776 B）。换装事务、补丁
矩阵与 11128 上游波归因见 change-evidence 2026-10-19 台账。

该插件实现的是第三方逆向协议。官方 [Acceptable Use Policy](https://www.workbuddy.ai/document/acceptable-use-policy) 与[个人服务协议](https://www.workbuddy.ai/document/term)限制未经授权的自动化访问、逆向工程、绕过使用限制及未获许可的互操作方式，并保留限制或停用账号的权利。没有公开、可核验的“安全请求数”或“安全频率”阈值；低频、换 IP、分离设备或多账号均不能据此视为安全。插件已注册不等于 WorkBuddy 官方认可此接入方式。

## 2. 部署与当前状态

- 插件文件仍保留在 `/opt/cliproxyapi/plugins/workbuddy.so`；WorkBuddy 凭据文件仍为 2 个，内容未读取或改动。认证管理页在本次操作前显示两份 WorkBuddy 凭据均启用。2026-10-19 换装后该文件为 0.9.60-local 构建（原子 rename 替换，旧 0.9.58p 备份在 `/opt/cliproxyapi/backups/20261019T130612…-plugin-0958p-to-0960p`）；同日起上游 INTL 出现与本仓工件无关的 11128 `request illegal` 波（v8.0.33/v8.0.34、0.9.58p/0.9.60 跨矩阵同败），处置见 §4 与同日台账：停止探针、不加压、恢复走自然回退或用户驱动的 INTL 重登。
- 当前配置为 `plugins.enabled: true`、`plugins.configs.workbuddy.enabled: true`。为防止启用后自动执行账号维护请求，另将 `checkin_auto`、`tasks_auto`、`lifecycle_auto`、`token_keepalive` 显式设为 `false`。用户发起的模型请求通道仍由已注册插件提供；没有发送模型请求验证上游账号可用性。
- 当前 `config.yaml` 权限 `0600`，SHA-256 `a2c7eb2e03872add97f70798aba748f57af688b6b9f18cfc57d598772a5e3`。恢复前快照为 `/opt/cliproxyapi/config.manual-snapshot-20261018T145110Z-pre-workbuddy-reenable.yaml`，权限 `0600`，SHA-256 `a8e731b68fd43641b0e1e98178ed302261eb85ad5942d0623e849e80d5dfcf1c`。快照是整份旧配置，见 §6 后再用于恢复。
- 已登录管理页读回 WorkBuddy“生效中 / 已注册”，有效插件 `1/1`；日志在 2026-10-18 22:52:24（UTC+8）记录 `plugin loaded` 与 `plugin registered`，版本 `0.9.58`。配置热加载成功；容器状态 running，重启数 `0`。
- 插件的历史关闭路径有 no-op shutdown，CPA loader 的逻辑卸载不代表 Go scheduler goroutine 已退出。当前进程没有重启，不能证明 goroutine 数或其生命周期；当前可见配置将四类自动任务置为 false，但未通过账号请求验证其运行时效果。
- 详细操作、管理页证据、备份及真值分层见[恢复记录](../change-evidence/20261018-bwg-workbuddy-risk-controls.md)。

## 3. API 与模型目录边界

- 2026-10-18 的 WorkBuddy Intl OAuth 插件目录观测中没有 `step-5-preview`。WorkBuddy Desktop 界面显示该名称，不能证明插件 OAuth API 或其他 API 接受该模型；也没有证据足以断言它“只能由 Desktop 调用”。
- 用户已确认当前没有可用的阶跃星辰独立 API，因此本 runbook 不把 StepFun API 列为回退路线。
- 国内 [WorkBuddy Open API 文档](https://open.workbuddy.cn/en/docs/openapi)描述的是注册应用和用户授权的接口；其云任务接口没有公开模型选择字段，也不能证明该 API 适用于 `workbuddy.ai` 国际版账号。
- `step-5-preview` 不得写入 `models_intl`、不得猜测模型别名或通过逆向插件试探。若未来官方文档明确提供当前账号可用的接口，再单独评估其适用范围、授权和限流规则。

## 4. 风控与事件处置

当前工程状态保持 WorkBuddy 插件注册和用户发起的模型调用入口；四项插件后台自动任务关闭。最新 BWG 配置读回为 `routing.retry.request-retry=0`、全局 `max-retry-credentials=1`；前者限制 CPA 自身重试，后者不是 WorkBuddy 专属设置，插件路径的具体凭据回退语义未核实。只读 `cockpit_provider_health.py --json` 没有报告客户端重试放大，但报告 `public_gateway` 的 DeepSeek / Muse 模型不可路由；这不等同于本地 WorkBuddy 插件未注册，也不能证明该 Codex profile 可端到端使用 WorkBuddy。调用端仍应将请求与流式自动重试都设为 0，避免同一失败请求被放大；此处只核验了当前 Codex 公网 provider。没有任何本地阈值可保证不封号；不得以指纹词/审核边界探测、伪造设备信息、轮换 IP 或账号、跨 realm 切换、清理冷却、反复 OAuth 登录或批量并发来尝试绕过风控。不要把 `scheduler_mode: credits` 或自动 failover 当作账号保护。

| 信号 | 处置 |
| --- | --- |
| `403 request illegal`、`11140`、官方客户端也提示账号受限 | 暂停当前调用任务并关闭调用端重试；保持凭据，不换账号、IP、设备或 token，不刷新 OAuth、不清冷却、不再用探针确认。保存时间、客户端/插件版本、模型、request ID 与脱敏错误，按官方支持渠道反馈。没有官方恢复信号前不要自动续打。 |
| `429`、`6003`、`6004` 或服务端返回冷却时间 | 停止当前任务，调用端不自动重试并保留 `Retry-After` / reset 信息；不跨凭据或跨 realm 续打，不调用 cooldown-clear。此逆向 OAuth 插件没有可验证的通用安全等待值，不能套用其他 API 的退避数字。 |
| `11128`、`11115`、超时或普通 5xx | 不循环重发原请求。保留一次脱敏错误上下文并停止自动化；若是请求格式错误，在本地修正后再按官方支持路径评估，不通过反复探测来确认账号状态。 |
| 输出质量变化（“降智”） | 不把单次回答或模型自动路由结果定性为账号处罚。记录实际模型/Auto 状态、套餐或积分、上下文长度、时间与 request ID；只在官方产品提供的固定模型/设置下做少量同任务对照，避免反复试探。没有受控对照或官方状态说明时标记为“原因未证实”。 |

官方[错误码说明](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Error-Code)将 `6003/6004` 列为频率限制信号，但没有公开可通用于该插件的请求阈值。社区 [issue #2152](https://github.com/yetone/magpie/issues/2152) 报告过 `403/11140` 与账号受限；这是单个用户报告，变量未经控制，不能推断封禁阈值、触发原因或安全 IP/请求节奏。

## 5. 历史观测（仅作故障背景）

- 过去 OAuth 管理流程曾成功创建 WorkBuddy Intl 凭据，API 目录观测过 26 个模型；账号实际权益有限，因此当时将 `models_intl` 钉为 `deepseek-v4.1-flash`。该历史目录和单次成功调用不证明当前权益、账号状态或调用安全。
- 一次 user-first 请求得到 `11128`，随后出现凭据冷却；这只说明该次错误可影响后续调用，不代表可用的固定重试时长。
- 历史配置含 `scheduler_mode: credits`，本次保留该值；其具体账号选择和切换行为未独立复核，因此不把它描述成防封控制。
- 现行配置明确关闭签到、任务、生命周期和 token keepalive 自动动作。不要依赖“保活”或后台任务去维持账号。
- 上一轮逻辑卸载不能证明旧 scheduler goroutine 已退出；当前管理器已重新注册，CPA 未重启。若以后需要进程级清除，应单独评估重启对其他 provider 的影响，不能把普通风控处置自动升级为重启。

## 6. 配置恢复与回滚

WorkBuddy 当前应保持注册；不要把禁用实例作为常规风控措施。恢复前快照 `config.manual-snapshot-20261018T145110Z-pre-workbuddy-reenable.yaml` 是整份配置，并且记录的是 WorkBuddy 关闭、自动任务键未显式关闭的旧状态。直接覆盖当前配置会关闭 WorkBuddy 并可能撤销其他配置变更。仅在当前配置损坏时先比较快照与现状，备份之后的变化，再做语义级最小恢复；Git 回滚不能替代远端恢复。

## 7. 上游更新

未来如评估插件更新，先核对官方服务条款、插件 release / asset digest、宿主 ABI 与插件源码实现，再执行独立受控变更；禁止未核哈希覆盖 `.so`。常规风险控制不包含自动下载或更新插件。
