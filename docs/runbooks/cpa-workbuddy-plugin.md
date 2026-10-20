# CPA WorkBuddy 插件 runbook（bwg）

**状态**: WorkBuddy 插件实例已启用并注册。2026-10-19 会话头补丁换装后，最小请求及后续网关流量已出现成功结果；多轮关联、重放和缓存语义尚未专项验收。配置明确关闭 `checkin_auto`、`tasks_auto`、`lifecycle_auto`、`token_keepalive`。换装涉及 CPA 重启，详见同日台账。
**适用主机**: bwg（CPA 容器 `cli-proxy-api`，镜像 `eceasy/cli-proxy-api:v8.0.34`，digest `sha256:439eb95f2ae9ba805d33a1e39d18a85c13cbd328f6545136dc9497cfc343bb9b` 锁定；2026-10-19 canary 事务升级，见 change-evidence 同日台账）。

## 1. 来源与固定版本

| 项 | 值 |
| --- | --- |
| 插件上游 | `github.com/mmqz/cpa-multi-plugins`（MIT） |
| 初始落地 release tag | `v0.8.68`（2026-10-12；以下为初始落地记录） |
| 初始插件版本 | workbuddy `0.9.58` |
| 产物 | `cpa-multi-plugins-linux-amd64.zip` |
| zip SHA-256 | `b54325f7c3426b0ca7083018e671af50f5e12b3311b42cefc2822cf573b910a5` |
| `workbuddy.so` SHA-256 | `5872d3da9d584cd25306ba3a329a8da1e898e333421b87271f84e33554a30edd` |
| 宿主版本 | CLIProxyAPI `v8.0.32`，镜像 digest `sha256:68f6e2d691f7b71aca21cfe87c77b7076cbea26c8ef490b15619b1623a7c0699` |

**当前在役构建（2026-10-19 换装，历史行保留作落地记录）**：源 tag `v0.8.72`
（workbuddy `0.9.60`，tarball SHA-256 `810c5457…e9ed`）+ `scripts/patches/` 之
selected-account、account-failover、conversation-headers 三补丁（intl-system-first
补丁已由上游 PR #42 收编退役）；`workbuddy.so` SHA-256
`3bca8a39d0e88194342c078d3ad0066727671bae11943f39ca0ebd8500a72218`。
换装事务、补丁矩阵与验收边界见[同日台账 §6](../change-evidence/20261019-bwg-cpa-v8034-canary-workbuddy-0960.md)。

该插件实现的是第三方逆向协议。官方 [Acceptable Use Policy](https://www.workbuddy.ai/document/acceptable-use-policy) 与[个人服务协议](https://www.workbuddy.ai/document/term)限制未经授权的自动化访问、逆向工程、绕过使用限制及未获许可的互操作方式，并保留限制或停用账号的权利。没有公开、可核验的“安全请求数”或“安全频率”阈值；低频、换 IP、分离设备或多账号均不能据此视为安全。插件已注册不等于 WorkBuddy 官方认可此接入方式。

## 2. 部署与当前状态

- 插件文件仍保留在 `/opt/cliproxyapi/plugins/workbuddy.so`；WorkBuddy 凭据文件仍为 2 个，内容未读取或改动。认证管理页在本次操作前显示两份 WorkBuddy 凭据均启用。2026-10-19 换装后该文件为 0.9.60-local 构建（原子 rename 替换，旧 0.9.58p 备份在 `/opt/cliproxyapi/backups/20261019T130612…-plugin-0958p-to-0960p`）；同日晚终态为 `3bca8a39…`（再叠加 `scripts/patches/workbuddy-v0.8.72-conversation-headers.patch`，备份 `20261019T133954…`）。同日起上游 INTL 曾出现与本仓工件无关的 11128 `request illegal` 波（v8.0.33/v8.0.34、0.9.58p/0.9.60 跨矩阵同败），**同日已修复收口**：修复=补齐三个会话关联头（`X-Conversation-ID/-Request-ID/-Message-ID`）；Global 的作者 A/B 与本仓 Intl 补丁后成功支持该修复，但未证明网关仅作存在性校验或无需跨轮连续性，详见同日台账 §6。再遇 11128 时先取 displayMsg 全文分辨子条件（system-first 文案 / #31 WAF 签名句 / 会话头通道校验 / 其它），转储在容器 `/root/.cli-proxy-api/logs/` 且随容器重建丢失。
- 当前配置为 `plugins.enabled: true`、`plugins.configs.workbuddy.enabled: true`。为防止启用后自动执行账号维护请求，另将 `checkin_auto`、`tasks_auto`、`lifecycle_auto`、`token_keepalive` 显式设为 `false`。模型请求的恢复证据见同日台账 §6；成功生成不证明四类自动任务的运行时效果。
- 2026-10-18 恢复时 `config.yaml` 权限 `0600`，SHA-256 `a2c7eb2e03872add97f70798aba748f57af688b6b9f18cfc57d598772a5e3`；该历史哈希不代表后续升级后的当前配置。恢复前快照为 `/opt/cliproxyapi/config.manual-snapshot-20261018T145110Z-pre-workbuddy-reenable.yaml`，权限 `0600`，SHA-256 `a8e731b68fd43641b0e1e98178ed302261eb85ad5942d0623e849e80d5dfcf1c`。快照是整份旧配置，见 §6 后再用于恢复。
- 2026-10-18 管理页读回“生效中 / 已注册”、有效插件 `1/1` 及 `0.9.58` 加载日志属于历史证据。2026-10-19 最终换装记录为 13:39:54Z 事务成功、注册 `version=0.9.60`；同日台账 §6 保存了产物哈希与回滚备份。
- 插件的历史关闭路径有 no-op shutdown，CPA loader 的逻辑卸载不代表 Go scheduler goroutine 已退出。2026-10-19 已经发生换装重启；旧进程未重启的限制不再适用于该次换装，但仍没有对自动任务生命周期作专项运行时验收。
- 详细操作、管理页证据、备份及真值分层见[恢复记录](../change-evidence/20261018-bwg-workbuddy-risk-controls.md)。

### 2.1 CN（国内版）+ INTL 双 realm 共挂

2026-10-20 评估定案：插件原生支持同一实例共挂 CN 与 INTL 凭据，无需代码改动。`login_region`（枚举 `cn`/`intl`）只决定**新登录**进哪个 realm（`cn`=copilot.tencent.com，`intl`=codebuddy.ai IDE 入口；Global 走面板凭据导入），每次登录使用独立 cookie jar，已有凭据不受影响；凭据文件按 realm 限定命名（`workbuddy-intl-<uid>.json` 与 CN/Global 文件不冲突），每份凭据自带 `Domain` 字段，payload 注入门、会话头补丁与 billing 全部按单账号域分派（CN 路径恒定逐字节不动）。

加入 CN 凭据属真实远端写入（config.yaml 变更 + 新凭据落盘），按 change-evidence 流程执行；仅限用户本人完成浏览器 OAuth（腾讯实名体系），agent 不代登录、不代注册。CN 出口为 bwg 美国 IP 访问 copilot.tencent.com，与国内版典型用户地理画像差异明显；CN 侧是否按 IP 地理聚合风控（对照 `14003`）零实测证据，加入前由用户自决。流程：

1. 管理页把 `login_region` 切到 `cn` **之后**再发起 CN 登录——这是 token 血统正确性要求，不只操作卫生：上游 issue #36 实证 intl 态下登录的凭据带 IDE 平台血统、走错头族（0.9.58 起登录平台持久化到 auth 文件已修，但血统在登录时铸定，事后不可迁移）。完成后立即把 `login_region` 翻回 `intl`。该值粘性且只影响新登录（存量凭据的请求路由恒按各自 auth 文件的域分派），残留 `cn` 会让后续新登录意外进 CN realm。
2. 同日钉定 `models_cn` 为该账号实际权益模型集。CN 目录真源是 copilot.tencent.com 按凭据返回（5 分钟缓存），不钉定会复刻 2026-10-18 INTL 目录 26 个幻影模型进入 `/v1/models` 的教训；`step-5-preview` 同样禁止写入或猜测。
3. 若要求账号行为画像按 realm 隔离（社区教训：行为特征>账号数量；出问题时归因面干净），把 `models_cn` 与 `models_intl` 钉成**不相交**集合——host 按模型目录过滤候选，不相交钉定使同一请求的候选池天然不混 realm（调度器本身不按 realm 过滤，同模型 ID 双 realm 都有时冷却回退可跨运营实体）。仅当两 realm 必须使用同一模型 ID 才需要 scheduler 按 realm 过滤的补丁；当前无此需求，不做。
4. 当日按独立受控变更验收：config 变更前后快照、`/v1/models` 读回只含钉定集、CN 账号单发受控验证、doctor 全绿，记入 change-evidence。

风控边界（2026-10-20 已核实，勿重复评估）：自动 failover 仅对 `11128` 通道级信号冷却并切换（`isChannelRiskControl` 只认 11128），`11140`/`11115`/403 账号级信号不触发自动换号，与 §4 处置表一致；冷却键按 authID+model，双 realm 冷却互不污染。外部取证（2026-10-20）：官方[错误码文档](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Error-Code)未列 `11140`/`11128`，也无任何地域/IP 限制码；`6003`/`6004` 官方口径"专业版也可能出现"，`14003` 官方归模型侧（社区按出口 IP 聚合画像属非官方观测）；插件上游 issue 区 CN realm 在役使用（#29/#30/#36）未见 CN 封号潮报告，CN 已知缺陷（#30 `<thought>` 泄漏、#36 login_region 流式路径/血统错配）均已修于在役 0.9.60 之前。CN 侧风控强度（`6003`/`6004`/`11140` 在 CN 的实测阈值）仍无直接量化证据，出现时按 §4 表处置并记录，不预建 CN 专属工程。

## 3. API 与模型目录边界

- 2026-10-18 的 WorkBuddy Intl OAuth 插件目录观测中没有 `step-5-preview`。WorkBuddy Desktop 界面显示该名称，不能证明插件 OAuth API 或其他 API 接受该模型；也没有证据足以断言它“只能由 Desktop 调用”。
- 用户已确认当前没有可用的阶跃星辰独立 API，因此本 runbook 不把 StepFun API 列为回退路线。
- 国内 [WorkBuddy Open API 文档](https://open.workbuddy.cn/en/docs/openapi)描述的是注册应用和用户授权的接口；其云任务接口没有公开模型选择字段，也不能证明该 API 适用于 `workbuddy.ai` 国际版账号。
- `step-5-preview` 不得写入 `models_intl`、不得猜测模型别名或通过逆向插件试探。若未来官方文档明确提供当前账号可用的接口，再单独评估其适用范围、授权和限流规则。

## 4. 风控与事件处置

**2026-10-20 现行覆盖**：BWG 已将 `scheduler_mode` 由 credits 改为 selected，
保留现有插件与全部凭据，配置热加载成功。selected 固定面板选择，在所选
账号缺失、禁用、额度耗尽或冷却时拒绝自动回退；人工面板选择仍可用，
不得用于接续账号受限任务。下文 credits 与自动换号描述属于历史配置。
隔离模式测试与部署边界见[本次记录](../change-evidence/20261020-workbuddy-selected-risk-control.md)。

`11128` 不能单凭数值认定为可安全换账号的通道错误。当前插件补丁会对该码
执行账号级五分钟本地冷却并让调度器选择其他符合条件的凭据；这描述的是现行
行为，不是官方错误分类或安全保证。§2.1 所述仅认 11128 指该补丁的触发范围。
出现未知 11128 时按下表停止当前任务，不由调用端重试或跨 realm 续打；已知
system-first / 会话头格式问题应先修正协议。该文档约束不能阻止插件在同一个
请求内部已发生的换凭据；彻底改变该行为需要修改插件、隔离验证并在维护窗口
换装，不能以修改 admission 或设置 `request-retry=0` 冒充已经修复。

当前工程状态保持 WorkBuddy 插件注册和用户发起的模型调用入口；四项插件后台自动任务关闭。最新 BWG 配置为 `routing.retry.request-retry=0`、全局 `max-retry-credentials=0`（2026-10-19 account-failover 改值：同一请求按 tried 集在凭据池内遍历、全池失败如实拒绝，语义以[事故 runbook 禁止节](cpa-ban-throttle-incident-response.md)为准）。只读 `cockpit_provider_health.py --json` 没有报告客户端重试放大，但报告 `public_gateway` 的 DeepSeek / Muse 模型不可路由；这不等同于本地 WorkBuddy 插件未注册，也不能证明该 Codex profile 可端到端使用 WorkBuddy。调用端仍应将请求与流式自动重试都设为 0，避免同一失败请求被放大；此处只核验了当前 Codex 公网 provider。没有任何本地阈值可保证不封号；不得以指纹词/审核边界探测、伪造设备信息、轮换 IP 或账号、跨 realm 切换、清理冷却、反复 OAuth 登录或批量并发来尝试绕过风控。不要把 `scheduler_mode: credits` 或自动 failover 当作账号保护。

| 信号 | 处置 |
| --- | --- |
| `403 request illegal`、`11140`、官方客户端也提示账号受限 | 暂停当前调用任务并关闭调用端重试；保持凭据，不换账号、IP、设备或 token，不刷新 OAuth、不清冷却、不再用探针确认。保存时间、客户端/插件版本、模型、request ID 与脱敏错误，按官方支持渠道反馈。没有官方恢复信号前不要自动续打。 |
| `429`、`6003`、`6004` 或服务端返回冷却时间 | 停止当前任务，调用端不自动重试并保留 `Retry-After` / reset 信息；不跨凭据或跨 realm 续打，不调用 cooldown-clear。此逆向 OAuth 插件没有可验证的通用安全等待值，不能套用其他 API 的退避数字。 |
| `14003` "模型繁忙"（官方归模型侧；2026-10-20 增补，同日策略更新） | 先分流：双账号同现且面板额度未耗尽＝模型/通道侧，非账号级（单账号独现才按上一行账号级 429 处置）。停止调用端连点重试（实测 Codex 客户端 5 连发会加深突发画像）。自 2026-10-20 起 admission 对模型侧 14003 **不再置 operator-review hold**：无 `Retry-After` 的 429 只落冷却阶梯首档（~21 分钟，客户端见 429+冷却窗而非 403），窗后自然恢复；账号型信号（`rate_limit_error`/6003/6004/11140/认证类）仍照旧持锁。人工干预仅在整个阶梯走完仍失败时按 [cpa-gateway 人工恢复](cpa-gateway.md#oauth-持久化风险状态的人工恢复) 评估。实测窗口 2026-10-20 为 ~31 分钟自愈（[台账 §6](../change-evidence/20261020-bwg-cpa-v8035-canary.md)）；社区"按出口 IP 聚合"属非官方观测，官方桌面端同账号换网络 A/B 可鉴别。 |
| `11128`、`11115`、超时或普通 5xx | 不循环重发原请求。保留一次脱敏错误上下文并停止自动化；若是请求格式错误，在本地修正后再按官方支持路径评估，不通过反复探测来确认账号状态。 |
| 输出质量变化（“降智”） | 不把单次回答或模型自动路由结果定性为账号处罚。记录实际模型/Auto 状态、套餐或积分、上下文长度、时间与 request ID；只在官方产品提供的固定模型/设置下做少量同任务对照，避免反复试探。没有受控对照或官方状态说明时标记为“原因未证实”。 |

官方[错误码说明](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Error-Code)将 `6003/6004` 列为频率限制信号，但没有公开可通用于该插件的请求阈值。社区 [issue #2152](https://github.com/yetone/magpie/issues/2152) 报告过 `403/11140` 与账号受限；这是单个用户报告，变量未经控制，不能推断封禁阈值、触发原因或安全 IP/请求节奏。

与[事故响应 runbook](cpa-ban-throttle-incident-response.md) 的词汇对应（2026-10-19 增补）：本页 `6003/6004`（官方频率限制）、`11128/11115/11140`（request illegal 家族）对应其增补词汇表 workbuddy 各行；社区观测另录 `14003`（按出口 IP 聚合的限流定量画像）、`11158`（intl 免费号模型级墙）、`11134/11102`（模型级故障/下线）、`14018`（credits）。处置入口仍是本页 §4 表 + 事故 runbook 分级；账号级 vs 通道级的判别法（官方桌面同账号 A/B 单发）以事故 runbook 行为准。

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

启用原生插件时，后台 CPA 镜像更新遇到新候选返回 `77 / deferred / PLUGIN_CANARY_REQUIRED`，保留当前镜像与插件，不发送生成探针。这是待人工复核的升级候选，不是插件已损坏。无新候选的每日 readiness 继续运行。

联合更新顺序：固定 CPA digest、插件源码 revision、补丁与产物 SHA-256；核对 Go 工具链、构建选项及共同依赖；在隔离且无真实凭据的环境验证加载/注册；备份镜像引用与插件；在维护窗口更新后读回实际注册版本和哈希。只有加载证据不能升级为业务验收；需要业务验收时按独立授权执行。回滚应匹配宿主与插件组合，保留当前凭据，不覆盖整份旧配置。
