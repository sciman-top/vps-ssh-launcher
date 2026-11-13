# CPA WorkBuddy 插件 runbook（bwg）

**2026-11-08 当前状态**：CPA `v8.0.61`（运行时为本地 fork 二进制
`681ce41b+gemini-raw`，SHA `31f2f9d9…`）+ WorkBuddy `0.9.89`（官方
`cpa-multi-plugins v0.8.97` 构建，生产 `.so` SHA `f2c678ff…`）。
0.9.89 只把 **模型级**上游故障（HTTP 500 + 业务码 `11134`）记入 #35 冷却表
（保守 60 s unknown 窗口），使调度、catalog 与 executor 快失败按
`model_provider_fault` 轮换掉该死组合；不改账号选择、粘性、并发与
`max-retry-credentials`。doctor 版本门禁随之钉 `0.9.89`，证据见
[v8.0.61 窗台账](../change-evidence/20261108-bwg-cpa-v8061-wb0989-gemini-raw.md)。
**2026-11-07 历史状态**：CPA `v8.0.59` + WorkBuddy `0.9.88`（官方
`cpa-multi-plugins v0.8.96` 构建，生产 `.so` SHA `6b2b2284…`）。本地
canonical UID 补丁（`7093a190…`）与 0.9.85 INT 共享窗口补丁均已退役为重建
素材——同一语义由上游 `744f255`（issue #76）承载，官方件经 zip+so 双 SHA
staging、隔离加载与入口门受控换装收编，证据见
[官方收编台账](../change-evidence/20261107-bwg-workbuddy-0987-official-adoption.md)。
0.9.88（issue #77）是纯观察面升级：6004 模型级限频窗并入冷却清单、清除
动作与面板卡片，`scheduler.pick`、catalog 过滤与"never shorten an active
window"语义零改动；doctor 版本门禁随之钉 `0.9.88`。换芯与收编同窗完成，
证据见 [v8.0.59 窗台账](../change-evidence/20261107-bwg-cpa-v8059-window.md)。
2026-11-06 的受控事务将宿主 `routing.retry.max-retry-credentials` 由
`0` 收紧到 `1`，以削减 consumer OAuth 跨账号暴露（实测一次 6004 扇出到两个
UID）。2026-11-08 按该台账的再评审门槛重审并放宽到 `3`：单凭据上限曾使一个
凭据的 billing 形状 429 成为整条车道响应，经 admission 的 billing 落锁把两次
上游瞬态抖动（11-06/11-07）放大成全车道人工停摆；`3` 保持扇出有界（每失败
请求至多 3 个凭据），后备尝试可到达在役凭据。`request-retry=0`、credits
调度、并发与会话粘性均不变。
6004 的限制键是**每个凭据、每个模型独立日窗 `(token, model)`**，不同账号之间
可以独立回退；这不等同于共享上游限流或共享额度。0.9.87 只收口
`scheduler.pick`、catalog、executor 的 UID/ID 查询坐标，不跨账号共享窗口，
不增加重试或解除上游日窗。受控换装只改插件 `.so`，配置与凭据字节未变；
换装后单次生产流式为 HTTP `200`、终态完整、正文精确 `PONG`。证据见
[0.9.86→0.9.87 台账](../change-evidence/20261105-bwg-workbuddy-intl-shared-window.md#生产换装-local-0987)。

一次未命中本地 6004 registry 的裁决**不会解除上游限制**，最多省掉对同一
`(token, model)` 的重复上游访问。CN 与 INTL 都不能以 realm-wide 持久窗口把
一个账号的结果外推到其他账号；CN 与 INTL 均保留独立账号/模型窗口。日志中的
本地窗口回放不等于新的上游调用；同一请求若在冷启动时确实逐个访问多个账号，
应依据逐账号真实拒绝与日窗核查，不能按日志行数推断。
早期上游官方 0.9.86 的 `scheduler.pick` 不消费 6004 registry，调度器可能再次
选出已知受限对。该 registry 接入调度与目录边界已由上游 `744f255` 收编进
0.9.87 正式发布（v0.8.95），现役 0.9.88 原样承载；上游 issue #76 记录该缺陷
沿革，本仓不再维护对应的本地生产修复分支。
如需保守地“6004 后不跨账号续打”，应作为独立的 AUP 策略显式切换。
priority reclaim 已由上游以 opt-in `scheduler_priority_reclaim: true` 正式实现，
本地 `workbuddy-v0.9.84-priority-reclaim.patch` 已退役；0.9.85 还在插件重注册后
自动补取 credits 快照，避免 reserve/daily-cap guardrail 静默 fail-open。当前保持
`scheduler_mode=credits`、每凭据 `account_max_concurrent=1`、
`identity_switch_on_429=false`、CN/INTL 模型 pin 与 INTL 登录区偏好。升级事务、
短窗自然流量与回滚见
[本次台账](../change-evidence/20261104-bwg-cpa-v8054-plugin-0985.md)。

**官方 0.9.86 是零补丁构建（2026-11-05 复核）**：
`outputs/build-workbuddy-0986-20261105.ps1` 只做 `git clone --branch v0.8.94` +
`gofmt`/`go vet`/`go test`/c-shared 构建，**没有 `git apply`**；上游 v0.8.94 的
`scheduler.go` 无 `schedulerModeSelected`/`Reject` 分支，`cooldown.go` 无
`channel_rejected`。⇒ `scripts/patches/` 下的 `selected-account`、
`account-failover`（11128 五分钟凭据熔断）与 `exhausted-pool-delegate`
**均未随当前 0.9.88 部署**，只作 0.9.84 及更早的重建素材；早期
`intl-shared-window` 也已退役。另注意 `selected`
已不是可用调度值：上游枚举只有 `off`/`credits`，写 `selected` 等于让插件把
每次 pick 都 defer 给宿主调度（doctor 仍强制 `credits`）。上游化评估与已提交的
issue 正文见 `outputs/upstream-drafts-20261104/README.md`（上游 `#74`/`#75`；
cockpit `#2676` 已补代码证据）。

**2026-11-03 历史状态**：CPA `v8.0.52` + WorkBuddy `0.9.84`
（人工联合 canary：隔离加载证明 v8.0.52 的 #7159 系 pluginhost 变更下 ABI
兼容，同窗配对双 antigravity aicode re-login。证据见
[本次台账](../change-evidence/20261103-bwg-cpa-v8052-antigravity-reauth.md)；
前一棒 v8.0.51 见 [2026-11-02 台账](../change-evidence/20261102-bwg-cpa-v8051.md)。
WorkBuddy 0.9.84 由上游
`v0.8.92`（workbuddy 0.9.82）叠加本仓 selected-account、account-failover、
exhausted-pool-delegate 与字符串化 11-128 冷却构成。未改缓存、准入、额度或
OAuth 策略：`account_max_concurrent=1`、`request-retry=0`、
`max-retry-credentials=3`、现有账号池、CN/INTL 顺序与会话粘性均保留，
`identity_switch_on_429=false` 仍由 doctor 强制。联合加载、宿主更新 fixture、
drain 部署、strict doctor 与受控非 OAuth 验收已通过；OAuth 只观察自然流量。
0.9.84 的唯一实质插件修复：`isChannelRiskControl` 原本用整数结构接
`"code":"11-128"`，与上游字符串 wire shape 不符，渠道拒绝未能进入本地
冷却分类；现在业务码保持整数，渠道码单独按字符串解析。0.9.84 还带入上游
0.9.82 的 auth.parse operator-field 回填，避免重启后 proxy/priority 等字段
从内存记录中丢失；当日 v8.0.50 事务证据见
[该台账](../change-evidence/20261102-bwg-cpa-v8050-plugin-0984.md)。
下方历史版本保留。
本仓不把官方 store 二进制当作与本地账号保护补丁等价的工件；只接受经
Linux 源码、`go vet`、全套测试与 C-shared 构建验证并记录 SHA-256 的工件。

**2026-10-26 更新**：宿主 CPA `v8.0.43`（amd64 digest `sha256:cfb97095…`，联合
canary 事务 00:06:52Z，插件 0.9.69 随行钉定），WorkBuddy `0.9.70-local`（上游
`v0.8.84` 的 workbuddy `0.9.70`：billing 头按账号派生稳定 `X-Machine-ID`/
`X-Session-ID`+每调用新 `X-Request-ID`，消除多账号池"零设备区分"指纹；新增
opt-in `account_max_concurrent` 每凭据在途并发闸）。本地补丁迁移为
`scripts/patches/workbuddy-v0.8.84-{selected-account,account-failover}.patch`
（仅 main.go 配置清单 hunk 上下文适配，语义与 0.9.68 版一致；隔离构建 `go vet`+
`go test ./...` ok 13.590s）。`config.yaml` 新增
`plugins.configs.workbuddy.account_max_concurrent: 1`（每凭据同时只跑一路大上下文
会话，防并行会话堆积触发模型级 6004；占位失败为 plain error 供宿主轮换下一凭据，
不冒充限流语义），doctor `==workbuddy-config==` 门禁同步钉定该键（缺失/非 1 即
FAIL）。事务、备份与验证边界见
[当日台账](../change-evidence/20261026-bwg-cpa-v8043-plugin-0970.md)。

**2026-10-25 当前状态**：CPA 已切换到官方 `v8.0.42` 固定 amd64 digest，
WorkBuddy 已更新为 `0.9.69-local`（正式 release `v0.8.83` 源码 + 既有
selected-account/account-failover 补丁）。隔离构建、注册、strict doctor、
非 OAuth 缓存/SSE 与短窗自然流量通过；CN/Intl 分别归因、Antigravity 升级后
自然验收和长期账号/质量结论仍未证明。现有账号、渠道、模型钉定、并发、重试
与冷却约束保留。版本、哈希、维护窗口及回滚见
[升级记录](../change-evidence/20261025-bwg-cpa-v8042-plugin-0969.md)。下方较早版本行保留为历史。

**2026-10-24 更新**：当前宿主 CPA `v8.0.41`（同日 canary 事务升级，见
[当日台账](../change-evidence/20261024-bwg-cpa-v8041-canary.md)），WorkBuddy `0.9.66-local`
（上游 `v0.8.80` 的 workbuddy `0.9.66`：idle 连接切断归类为连接级计费错误、
走 4 级重试阶梯而非一次性逃逸；原 selected-account/account-failover 补丁不变，
补丁面与 billing.go 零重叠）。注册断言 `version=0.9.66`、readiness、lane-hold
快照与单发实战（HTTP 200 / 1.0s）通过；构建/安装脚本与回滚备份见
[升级台账](../change-evidence/20261024-bwg-cron-hardening-and-plugin-0966.md)。
上游 release 线止于 `v0.8.80`（`v0.9`+ 的 tag 是无 release 的 CI 连续打点，
勿当发版追）。前一日 0.9.65 的记录见
[当日台账](../change-evidence/20261023-bwg-cpa-v8040-canary.md)，下方日期较早的
版本与 hash 为历史记录。

**状态**: WorkBuddy 插件实例已启用并注册。2026-10-19 会话头补丁换装后，最小请求及后续网关流量已出现成功结果；多轮关联、重放和缓存语义尚未专项验收。自 2026-10-24 起，CN 账号启用 `checkin_auto=true` 以领取签到积分；`tasks_auto`、`lifecycle_auto`、`token_keepalive` 仍显式关闭。换装涉及 CPA 重启，详见同日台账。2026-10-22 已换装到 `0.9.64-local`，换装后 doctor `DOCTOR_CONTRACT_OK`，真实 INTL 流量（`deepseek-v4.1-flash`）恢复成功。同日再叠加 **truncation guard**（`scripts/patches/workbuddy-v0.8.78-truncation-guard.patch`）：200 流无 `finish_reason`/`[DONE]` 干净 EOF 时以 `truncated_stream` 失败关闭，不再把半截答案当成功；见[同日台账](../change-evidence/20261022-bwg-workbuddy-truncation-guard.md)。
**适用主机**: bwg（CPA 容器 `cli-proxy-api`，镜像 `eceasy/cli-proxy-api:v8.0.51`，digest `sha256:15d13889e1a39a688be306dfbf2f6cf9003e99cd913b5070fa463f8ab2aa0031` 锁定；2026-11-02 canary 事务升级，见 [本次台账](../change-evidence/20261102-bwg-cpa-v8051.md)）。

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

**历史构建（2026-10-26 换装）**：源 tag `v0.8.84`（workbuddy `0.9.70`，tarball
SHA-256 `403fa287169be84ba9a2497e449ed8f0f800efca15aa52a108049ec197834413`）+
`scripts/patches/` 之 v0.8.84 版 selected-account、account-failover 两补丁（v0.8.68
版补丁对 v0.8.84 仅 main.go 配置清单 hunk 上下文失配——上游在 stream_head_timeout
后插入了 account_max_concurrent 条目；conversation-headers/intl-system-first/
truncation-guard 均维持退役）。`workbuddy.so` SHA-256
`33f2cb46c3a44b0ba34f8848dc251c1bb3ea7d1ebcdecbc8bc043c18bcbaaea5`（隔离构建
golang:1.26-bookworm 1CPU/768M，`go vet`+`go test ./...` ok 13.590s；回滚备份
`/opt/cliproxyapi/backups/20261026T000914.859852202Z-plugin-0969-to-0970`）。
上游要点：per-account billing 机器身份（#58）与 opt-in `account_max_concurrent`
（#60，本仓设 `1`；上游口径：并行大上下文会话是模型级限流的最快路径）。
见[当日台账](../change-evidence/20261026-bwg-cpa-v8043-plugin-0970.md)。

**历史构建（2026-10-22 换装，历史行保留）**：源 tag `v0.8.78`（workbuddy `0.9.64`，tarball
SHA-256 `feb582d5ac7307b275f1162285774e531cf4b5bd9506e21d8eb212cc16fe0998`）+
`scripts/patches/` 之 selected-account、account-failover、truncation-guard 三补丁。上游 0.9.63 将 11128
触发短语清洗扩展到全部角色（user/assistant 历史、`tool_calls` arguments、reasoning
mirrors），0.9.64 仅修正注册元数据中的错误 GitHub 仓库地址。`workbuddy.so` SHA-256
`86441f5d931a6ddeecd3875efcf115f53d7cc29fb1827cbaefcd067a761f15aa`（12,019,728 字节；
同日 truncation-guard 换装前为 `8d531f30…`）。**流截断语义**：`truncated_stream: workbuddy
upstream ended without finish_reason (unexpected EOF)` 属传输生命周期族，不冷却凭据；
`finish_reason=length`/`tool_calls` 仍算上游主动结束。该守卫只修失败语义与静默成功，
不修上游答案完整性。
换装事务、补丁矩阵与验收边界见[同日台账](../change-evidence/20261022-bwg-workbuddy-0964-plugin.md)。

**上一代构建（2026-10-21 换装，历史行保留作落地记录）**：源 tag `v0.8.76`
（workbuddy `0.9.62`，tarball SHA-256
`45e177377c3dc76020b244b8e97f59efc18368c99d71f57fbe6d8400ceef1409`）+ selected-account、
account-failover 两补丁；`workbuddy.so` SHA-256
`94a1c82550fc5ef2e3ee6098847f7efdb1e9292b93152906f0bc40c716194929`。该代将 11128
分类为 request-scoped（`statusError{400}`，插件层不再轮换/冷却账号）。
见[同日台账](../change-evidence/20261021-bwg-cpa-v8037-canary-plugin-0962.md)。

**上一代构建（2026-10-19 换装，历史行保留作落地记录）**：源 tag `v0.8.72`
（workbuddy `0.9.60`，tarball SHA-256 `810c5457…e9ed`）+ `scripts/patches/` 之
selected-account、account-failover、conversation-headers 三补丁（intl-system-first
补丁已由上游 PR #42 收编退役）；`workbuddy.so` SHA-256
`3bca8a39d0e88194342c078d3ad0066727671bae11943f39ca0ebd8500a72218`。
换装事务、补丁矩阵与验收边界见[同日台账 §6](../change-evidence/20261019-bwg-cpa-v8034-canary-workbuddy-0960.md)。

该插件实现的是第三方逆向协议。官方 [Acceptable Use Policy](https://www.workbuddy.ai/document/acceptable-use-policy) 与[个人服务协议](https://www.workbuddy.ai/document/term)限制未经授权的自动化访问、逆向工程、绕过使用限制及未获许可的互操作方式，并保留限制或停用账号的权利。没有公开、可核验的“安全请求数”或“安全频率”阈值；低频、换 IP、分离设备或多账号均不能据此视为安全。插件已注册不等于 WorkBuddy 官方认可此接入方式。

## 2. 部署与当前状态

- 插件文件仍保留在 `/opt/cliproxyapi/plugins/workbuddy.so`。**2026-10-26 只读核对确认当前有 3 份凭据（1 CN、2 INTL）**；本次 admission 投影未读取、复制或改动任何凭据文件。以下换装哈希和数量属于历史记录。2026-10-19 换装后该文件为 0.9.60-local 构建（原子 rename 替换，旧 0.9.58p 备份在 `/opt/cliproxyapi/backups/20261019T130612…-plugin-0958p-to-0960`）；同日晚终态为 `3bca8a39…`（再叠加 `scripts/patches/workbuddy-v0.8.72-conversation-headers.patch`，备份 `20261019T133954…`）；2026-10-21 换装后为 0.9.62-local（`94a1c825…`，旧 0.9.60 备份在 `20261021T131547…-plugin-0960-to-0962`）；**2026-10-22 换装后为 0.9.64-local（`8d531f30…`，原子 rename，旧 0.9.62 备份在 `/opt/cliproxyapi/backups/20261022T123315.481884659Z-plugin-0962-to-0964`）**。同日起上游 INTL 曾出现与本仓工件无关的 11128 `request illegal` 波（v8.0.33/v8.0.34、0.9.58p/0.9.60 跨矩阵同败），**同日已修复收口**：修复=补齐三个会话关联头（`X-Conversation-ID/-Request-ID/-Message-ID`）；Global 的作者 A/B 与本仓 Intl 补丁后成功支持该修复，但未证明网关仅作存在性校验或无需跨轮连续性，详见同日台账 §6；**2026-10-21 起会话头由上游 realm 无条件填充（0.9.61+），本地补丁退役**。再遇 11128 时先取 displayMsg 全文分辨子条件（system-first 文案 / #31 WAF 签名句 / 会话头通道校验 / 其它），转储在容器 `/root/.cli-proxy-api/logs/` 且随容器重建丢失。
- 当前配置为 `plugins.enabled: true`、`plugins.configs.workbuddy.enabled: true`。CN 每日签到已启用；成长中心任务循环、积分耗尽后的自动账号状态变更、定时 token 刷新仍关闭。当前策略和备份入口见 [2026-10-24 变更记录](../change-evidence/20261024-bwg-workbuddy-cn-catalog-and-checkin.md)。
- 2026-10-18 恢复时 `config.yaml` 权限 `0600`，SHA-256 `a2c7eb2e03872add97f70798aba748f57af688b6b9f18cfc57d598772a5e3`；该历史哈希不代表后续升级后的当前配置。恢复前快照为 `/opt/cliproxyapi/config.manual-snapshot-20261018T145110Z-pre-workbuddy-reenable.yaml`，权限 `0600`，SHA-256 `a8e731b68fd43641b0e1e98178ed302261eb85ad5942d0623e849e80d5dfcf1c`。快照是整份旧配置，见 §6 后再用于恢复。
- 2026-10-18 管理页读回“生效中 / 已注册”、有效插件 `1/1` 及 `0.9.58` 加载日志属于历史证据。2026-10-19 最终换装记录为 13:39:54Z 事务成功、注册 `version=0.9.60`；同日台账 §6 保存了产物哈希与回滚备份。2026-10-21 换装记录为 13:15:52Z 事务成功、注册 `version=0.9.62`（`go test` 全套 ok 8.484s）。2026-10-22 换装记录为 12:33:15Z 事务成功（drain 排空锁 + lane hold 差分）、注册 `version=0.9.64`（隔离构建 `go test ./...` ok 8.548s），换装后 readiness `HEALTH_OK`、strict doctor `DOCTOR_CONTRACT_OK`；同日 13:30:38Z 再换装 truncation-guard 构建（`86441f5d…`，`go test` ok 8.536s），备份 `20261022T133038.434808589Z-plugin-0964-to-0964trunc`，见[截断守卫台账](../change-evidence/20261022-bwg-workbuddy-truncation-guard.md)。
- 插件的历史关闭路径有 no-op shutdown，CPA loader 的逻辑卸载不代表 Go scheduler goroutine 已退出。2026-10-19 已经发生换装重启；旧进程未重启的限制不再适用于该次换装，但仍没有对自动任务生命周期作专项运行时验收。
- 详细操作、管理页证据、备份及真值分层见[恢复记录](../change-evidence/20261018-bwg-workbuddy-risk-controls.md)。

### 2.1 CN（国内版）+ INTL 双 realm 共挂

2026-10-20 评估定案：插件原生支持同一实例共挂 CN 与 INTL 凭据，无需代码改动。`login_region`（枚举 `cn`/`intl`）只决定**新登录**进哪个 realm（`cn`=copilot.tencent.com，`intl`=codebuddy.ai IDE 入口；Global 走面板凭据导入），每次登录使用独立 cookie jar，已有凭据不受影响；凭据文件按 realm 限定命名（`workbuddy-intl-<uid>.json` 与 CN/Global 文件不冲突），每份凭据自带 `Domain` 字段，payload 注入门、会话头补丁与 billing 全部按单账号域分派（CN 路径恒定逐字节不动）。

加入 CN 凭据属真实远端写入（config.yaml 变更 + 新凭据落盘），按 change-evidence 流程执行；仅限用户本人完成浏览器 OAuth（腾讯实名体系），agent 不代登录、不代注册。CN 出口为 bwg 美国 IP 访问 copilot.tencent.com，与国内版典型用户地理画像差异明显；CN 侧是否按 IP 地理聚合风控（对照 `14003`）零实测证据，加入前由用户自决。流程：

1. 管理页把 `login_region` 切到 `cn` **之后**再发起 CN 登录——这是 token 血统正确性要求，不只操作卫生：上游 issue #36 实证 intl 态下登录的凭据带 IDE 平台血统、走错头族（0.9.58 起登录平台持久化到 auth 文件已修，但血统在登录时铸定，事后不可迁移）。完成后立即把 `login_region` 翻回 `intl`。该值粘性且只影响新登录（存量凭据的请求路由恒按各自 auth 文件的域分派），残留 `cn` 会让后续新登录意外进 CN realm。
2. 同日钉定 `models_cn` 为该账号实际权益模型集。CN 目录真源是 copilot.tencent.com 按凭据返回（5 分钟缓存），不钉定会复刻 2026-10-18 INTL 目录 26 个幻影模型进入 `/v1/models` 的教训；`step-5-preview` 同样禁止写入或猜测。
3. 若要求账号行为画像按 realm 隔离（社区教训：行为特征>账号数量；出问题时归因面干净），把 `models_cn` 与 `models_intl` 钉成**不相交**集合——host 按模型目录过滤候选，不相交钉定使同一请求的候选池天然不混 realm（调度器本身不按 realm 过滤，同模型 ID 双 realm 都有时冷却回退可跨运营实体）。2026-10-24 的已知例外是两边均钉定 `deepseek-v4.1-flash`：credits 模式下该公共名的冷却回退可跨运营实体（无法按 realm 指定）。不要把该别名当作严格的 CN/INTL 路由；需要客户端可选 realm 时，先实现并验收 realm-aware alias 与调度。
   2026-11-06 增补 `hy4free`：WorkBuddy CN 与 INTL 均免费支持（x0.00 credits），作为两边均钉定的第二个公共池化模型。
4. 当日按独立受控变更验收：config 变更前后快照、`/v1/models` 读回只含钉定集、CN 账号单发受控验证、doctor 全绿，记入 change-evidence。

Strict doctor 不把登录窗口中的 `cn` 当作立即失败，但会显式输出
`workbuddy-login_region=cn expected=intl warning=sticky_cn_new_logins_only`；
`intl` 才是常态。未知值只显示 `unknown`，不回显原始配置内容。看到 CN 残留时，
先确认没有正在进行的 CN 登录，再在 Cockpit 管理面切回 `intl`；不要在代理层或
凭据文件中伪造 realm，也不要通过重登来“清理”这个字段。

也可使用受控 CLI，默认只读，`-Apply` 才写入：

```powershell
pwsh -NoProfile -File .\scripts\cpa_workbuddy_login_region.ps1 -Profile bwg -Region intl
pwsh -NoProfile -File .\scripts\cpa_workbuddy_login_region.ps1 -Profile bwg -Region intl -Apply
```

`-Apply` 先取得维护锁与 admission `requests.lock`，等待最多 45 秒完成排空；
随后备份 `config.yaml`、保持同一 inode 只替换 `plugins.configs.workbuddy.login_region`
一个字段，并验证 YAML 差异只有该路径。脚本不重启 CPA；配置 watcher 热加载后
strict doctor 应显示 `workbuddy-login_region=intl state=ok`。回滚时恢复输出中的
备份文件并按现有 guarded restart 契约处理。

风控边界（2026-10-20 已核实，勿重复评估；**下列 failover 行为属补丁构建，
0.9.85 零补丁后已不存在，见文首**）：自动 failover 仅对 `11128` 通道级信号冷却并切换（`isChannelRiskControl` 只认 11128），`11140`/403 账号级信号不触发自动换号；`11115` 为官方定义的输入过长，也不应通过换号重试，与 §4 处置表一致；冷却键按 authID+model，双 realm 冷却互不污染。外部取证（2026-10-20）：官方[错误码文档](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Error-Code)未列 `11140`/`11128`，也无任何地域/IP 限制码；`6003`/`6004` 官方口径"专业版也可能出现"，`14003` 官方归模型侧（社区按出口 IP 聚合画像属非官方观测）；插件上游 issue 区 CN realm 在役使用（#29/#30/#36）未见 CN 封号潮报告，CN 已知缺陷（#30 `<thought>` 泄漏、#36 login_region 流式路径/血统错配）均已修于在役 0.9.60 之前。CN 侧风控强度（`6003`/`6004`/`11140` 在 CN 的实测阈值）仍无直接量化证据，出现时按 §4 表处置并记录，不预建 CN 专属工程。2026-10-21 起插件上游 0.9.62 将 11128 分类为 request-scoped（`statusError{400}`）：上游网关拒绝不再触发插件层账号轮换/冷却（此前裸错误曾致 v8 宿主轮遍全池并冷却全部 CN 账号、后续 503 auth_unavailable）；本仓 account-failover 补丁的 11128→5 分钟凭据级熔断分支保留作本地保险丝（只短冷却、不轮换），两层语义兼容。

## 3. API 与模型目录边界

- 2026-10-18 的 WorkBuddy Intl OAuth 插件目录观测中没有 `step-5-preview`。WorkBuddy Desktop 界面显示该名称，不能证明插件 OAuth API 或其他 API 接受该模型；也没有证据足以断言它“只能由 Desktop 调用”。
- 用户已确认当前没有可用的阶跃星辰独立 API，因此本 runbook 不把 StepFun API 列为回退路线。
- 国内 [WorkBuddy Open API 文档](https://open.workbuddy.cn/en/docs/openapi)描述的是注册应用和用户授权的接口；其云任务接口没有公开模型选择字段，也不能证明该 API 适用于 `workbuddy.ai` 国际版账号。
- `step-5-preview` 不得写入 `models_intl`、不得猜测模型别名或通过逆向插件试探。若未来官方文档明确提供当前账号可用的接口，再单独评估其适用范围、授权和限流规则。
- **面板 PUT 整段替换插件配置——pin 丢失的真根因（2026-10-24 深夜二次定案，
  推翻同日早间"pin 非 sticky/裸 reconfigure"初判）**：CPA 管理面
  `PUT /v8/management/config/plugins/configs/<id>` 是**整段替换**
  （`internal/api/handlers/management/plugins.go` 的 `PutPluginConfig` 直接
  `Configs[id] = updated`；`PATCH` 才是浅合并），而 config 变更 differ
  （`internal/watcher/diff/config_diff.go` 的 `BuildConfigChangeDetails`）
  **完全不覆盖 plugins.* 段**。面板以未渲染全部字段的表单发 PUT 时，
  `models_cn/models_intl` 会被**静默删除**且变更日志不可见；config reload
  后插件以新段重注册、按设计解 pin，下一次 `/v1/models` 触发 realm 全量
  discovery（实测 32 模型快照、29 个未批准 ID 晋升进客户端目录），doctor
  `MODEL_IDS_UNKNOWN` fail-closed 变红。插件与宿主各层均为按设计行为：
  admission lane 模型名单（仅 `deepseek-v4.1-flash`）全程挡住实际调用，
  fail-closed 分层无缺口。恢复 = 把两个键写回配置（面板完整表单重存、或对
  同一端点用 `PATCH` 合并 `models_cn`/`models_intl`、或直接改 config.yaml，
均随 reload/restart 生效）；**不要做 sticky 补丁**——那会对抗合法的解 pin
编辑。预防：面板保存插件配置后跑一次 strict doctor——`==workbuddy-config==`
门禁（2026-10-24 起）直接核对 `models_cn`/`models_intl` pin 存在性与四个
auto 开关的策略值（`checkin_auto=true`、`tasks_auto`/`lifecycle_auto`/
`token_keepalive=false`；键缺失即 FAIL，因上游四开关缺省均为 `true`，静默
翻回默认等于重开任务循环；2026-10-26 起并核对 `account_max_concurrent=1`，
缺省 0=并发闸关闭同样按漂移 FAIL），`==client-model-catalog==` 的
`MODEL_IDS_UNKNOWN` 仍作目录外溢的第二层兜底。源码锚与时间线见
[当日台账](../change-evidence/20261024-bwg-cpa-v8041-canary.md)的根因修正节。

## 4. 风控与事件处置

**2026-10-26 调度检查补强**：保留 WorkBuddy 粘性选择与故障回退、OpenCode
双宿兜底；不调整渠道优先级、凭据数量、并发或重试预算。doctor 的
`==workbuddy-config==` 现在要求 `scheduler_mode: credits`，字段缺失、`off`
或其他值均报告漂移，避免面板 PUT 丢键后静默退回宿主调度。`selected`
是不同策略，若用户明确切换，须同步复核该检查。此检查只报告配置漂移，
不自动修复配置或阻断已运行的数据面。

`credits` 不按积分余额最大值排序，而是粘住当前合格账号，再选其他合格候选。
宿主 `request-retry=0` 禁止额外轮次，`max-retry-credentials=3`（2026-11-08 再评审，
原为 1）将单个客户端请求的上游凭据触达限制为至多 3 个。它不是客户端重试，
也不保证上游配额隔离；`account_max_concurrent=1`、
入口 lane 与会话粘性仍限制并发和请求扩散。6004 的模型级 reset 由插件内存表
负责；CN、INTL、Global 均按独立账号/模型（0.9.85 的 realm 共享补丁已退役），
普通失败冷却与 admission 的入口冷却是不同状态；配置热重载保留插件
进程内状态，进程重启会丢失这些内存表，不为验收而重启或清冷却。
OpenCode 兜底成功只证明最终请求成功，不证明三个 WorkBuddy 凭据健康；
判断其压力应结合现有分支日志中的上游错误，不能仅按 admission 的最终 200。

**2026-10-25 现行覆盖**：BWG 已将 `scheduler_mode` 由 selected 改回 credits
（事务回执与行为验证见[本次记录](../change-evidence/20261025-bwg-workbuddy-credits-failover.md)）。
动机：2026-10-25 三凭据在同日内先后吃到上游 6004 模型级限流（约 24h 窗），selected 模式把全部
deepseek 流量钉在单一账号、叠加客户端重试风暴加速触顶，且人工换号只是把同样压力搬到下一个账号。
credits 语义（0.9.66 源码核实）：粘性选择+故障回退（`active_auth.go:58-78`）、跳过 6004 park 的
(账号,模型)对（`scheduler.go:76`，issue #35）、park 按上游声明重置时间自然到期自动回归。自动回退
仍**不是**账号保护：没有任何本地阈值能防止 6004，credits 只提供优雅降级与到期自愈；上游容量窗、
`max_inflight=1` 与调用端 `request_max_retries=0` 等减压边界不变。

**2026-10-20 历史覆盖（已被 10-25 覆盖取代）**：BWG 曾将 `scheduler_mode` 由 credits 改为 selected，
动机是 11128 信号性质未明时防止自动选择其他账号扩散风险。selected 固定面板选择，在所选
账号缺失、禁用、额度耗尽或冷却时拒绝自动回退。下文 selected 描述仅为历史策略。
隔离模式测试与部署边界见[本次记录](../change-evidence/20261020-workbuddy-selected-risk-control.md)。

`11128` 不能单凭数值认定为可安全换账号的通道错误。**当前生产 0.9.88 构建
中本地 account-failover 熔断仍不存在**：上游把 11128 归为
request-scoped 400，插件层既不冷却凭据也不轮换，宿主不再因该码遍历账号池；
需要"五分钟凭据级熔断"时必须先重新打补丁并重建，不能假设它仍在。现行
credits 模式允许选择其他符合条件的凭据，历史 selected 模式则在所选账号
不可用时拒绝自动回退（该模式同样只存在于补丁构建）。§2.1 所述仅认 11128
指补丁的触发范围，不是官方错误分类或安全保证。
出现未知 11128 时按下表停止当前任务，不由调用端重试或跨 realm 续打；已知
system-first / 会话头格式问题应先修正协议。selected 的拒绝行为由插件与宿主
Reject ABI 共同实现，不能仅凭 admission 或 `request-retry=0` 推导；干净启动
无所选账号时仍会初始化候选，不承诺跨重启持久绑定。

当前工程状态保持 WorkBuddy 插件注册和用户发起的模型调用入口；仅 CN `checkin_auto` 开启，其他三项插件后台动作关闭。2026-11-06 将 `routing.retry.max-retry-credentials` 收紧为 `1`（6004 扇出止损）；2026-11-08 按该收紧台账的再评审门槛放宽为 `3`：单请求至多触达 3 个凭据，在役凭据上的后备尝试可吸收单凭据拒绝、避免其成为整条车道响应（详见 change-evidence 2026-11-08 台账）；后续独立请求仍可由 `credits` 选择其他合格凭据。现有账号与 API key 未删除。调用端仍应将请求与流式自动重试都设为 0，避免同一失败请求被放大；没有任何本地阈值可保证不封号；不得以指纹词/审核边界探测、伪造设备信息、轮换 IP 或账号、跨 realm 切换、清理冷却、反复 OAuth 登录或批量并发来尝试绕过风控。不要把 `scheduler_mode: credits` 当作账号保护。

| 信号 | 处置 |
| --- | --- |
| `403 request illegal`、`11140`、官方客户端也提示账号受限 | 暂停当前调用任务并关闭调用端重试；保持凭据，不换账号、IP、设备或 token，不刷新 OAuth、不清冷却、不再用探针确认。保存时间、客户端/插件版本、模型、request ID 与脱敏错误，按官方支持渠道反馈。没有官方恢复信号前不要自动续打。 |
| `429`、`6003`、`6004` 或服务端返回冷却时间 | 停止调用端自动重试并保留 `Retry-After` / reset 信息；同一请求只允许 CPA 当前配置的最多 3 个凭据做有界回退，不跨 realm 续打、不继续进入 OpenCode、不调用 cooldown-clear。此逆向 OAuth 插件没有可验证的通用安全等待值，不能套用其他 API 的退避数字。2026-10-21 晚实测（CPA v8.0.35）：6004 生产 wrapper 形状=HTTP 500+`server_error`/`internal_server_error` 信封（message 尾段内嵌转义的上游 JSON，raw 正文自带 reset 时间戳），admission 归 lane 阶梯自恢复、无 hold，与官方频率限制自恢复语义一致，勿当缺陷或回归。 |
| `14003` "模型繁忙"（官方归模型侧；2026-10-20 增补，同日策略更新） | 先分流：双账号同现且面板额度未耗尽支持模型/通道侧故障的判断，但不能单独排除共享出口或账号风控；单账号独现也不足以确认账号受限，须结合完整错误及官方账号状态。停止调用端连点重试（实测 Codex 客户端 5 连发会加深突发画像）。自 2026-10-20 起 admission 对模型侧 14003 **不再置 operator-review hold**：无 `Retry-After` 的 429 只落冷却阶梯首档（当前首档 60 秒，阶梯 60/120/240/480/900 秒；可信上游期限可延长窗口），窗后允许后续请求但不保证上游恢复；账号型信号（`rate_limit_error`/6003/6004/11140/认证类）仍照旧持锁。人工干预仅在整个阶梯走完仍失败时按 [cpa-gateway 人工恢复](cpa-gateway.md#oauth-持久化风险状态的人工恢复) 评估。实测窗口 2026-10-20 为 ~31 分钟自愈（[台账 §6](../change-evidence/20261020-bwg-cpa-v8035-canary.md)）；社区"按出口 IP 聚合"属非官方观测，不将换网络试探作为常规恢复动作。**2026-10-21 缺陷收口**：部署后首波 14003 经 CPA 包装信封（wrapper `type=rate_limit_error`）误置 durable hold——wrapper 标签击败 header 层阶梯改动（生产形状未被原钉住测试覆盖）；已修复并部署（[台账](../change-evidence/20261021-bwg-workbuddy-14003-wrapper-fix.md)，pin `19d64361`）：分类按 wrapper message 内嵌上游 code，wrapper-14003 归阶梯自恢复，裸 rate_limit_error/内嵌 6003/6004 仍持锁。 |
| `11115 INPUT TOO LONG` | 按官方错误码说明缩短输入或压缩上下文，在本地检查请求大小；该码本身不是账号受限证据，不换号重发超长请求。 |
| `11128`、超时或普通 5xx | 不循环重发原请求。保留一次脱敏错误上下文并停止自动化；若是请求格式错误，在本地修正后再按官方支持路径评估，不通过反复探测来确认账号状态。 |
| 输出质量变化（“降智”） | 不把单次回答或模型自动路由结果定性为账号处罚。记录实际模型/Auto 状态、套餐或积分、上下文长度、时间与 request ID；只在官方产品提供的固定模型/设置下做少量同任务对照，避免反复试探。没有受控对照或官方状态说明时标记为“原因未证实”。 |

官方[错误码说明](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Error-Code)将 `6003/6004` 列为频率限制信号，但没有公开可通用于该插件的请求阈值。社区 [issue #2152](https://github.com/yetone/magpie/issues/2152) 报告过 `403/11140` 与账号受限；这是单个用户报告，变量未经控制，不能推断封禁阈值、触发原因或安全 IP/请求节奏。

**403 分层（2026-10-31 增补）**：响应体是 HTML/纯文本的边缘拒绝
（Cloudflare `Access denied` 之类，非 JSON）只说明链路被挡，不构成账号证据：
admission 按瞬时信号走冷却阶梯、不再落持久人工锁；只有 JSON `11140`、
`permission_error` 或认证类 403 才进入上表的人工复核流程。人工锁出现时先读
`lane_hold_latched lane=… reason=… source=…` 定位来源，再按
[CPA 故障归因 runbook](cpa-failure-triage.md) 的释放流程处理。

**6004 在 admission 侧的窗口（2026-11-06 增补）**：`deepseek-v4.1-flash` 是
`pooled_aliases`（WorkBuddy 主、OpenCode `priority: -1` 备），而 admission 按**模型名**
判 lane。6004 的 reset 曾被整段镜像进模型冷却（实测一次 22254s），于是
`lane_reject reason=model_cooldown` 在 CPA 之前就挡掉所有 provider——客户端报
`Shared upstream lane workbuddy-oauth is cooling down or busy; retry after 22210s`，
而同一账号在官方客户端正常，OpenCode 备份也永远轮不到。现改为按
`WORKBUDDY_6004_MAX_COOLDOWN_SECONDS = 300` 封顶：最多停 5 分钟，之后请求可再落到别的
凭据或备份 provider。注意这**不**恢复 CPA 侧无界的自动回退——`routing.retry.request-retry: 0`
与 `max-retry-credentials: 3`（2026-11-08 再评审，原为 1）只允许单请求至多触达
3 个凭据（见 §7 与 `cpa_policy.py` 的期望值）。

与[事故响应 runbook](cpa-ban-throttle-incident-response.md) 的词汇对应（2026-10-19 增补）：本页 `6003/6004`（官方频率限制）、`11128/11140`（需结合完整错误判断的 request illegal 信号）、`11115`（官方 INPUT TOO LONG）对应其增补词汇表 workbuddy 各行；社区观测另录 `14003`（按出口 IP 聚合的限流定量画像）、`11158`（intl 免费号模型级墙）、`11134/11102`（模型级故障/下线）、`14018`（credits）；另有 HTTP 200 空内容完成形态（额度到量的降智式表现，上游 [#6526](https://github.com/router-for-me/CLIProxyAPI/issues/6526)，2026-10-21 增补）——先查面板 credits/额度再归因，勿当健康绿灯。处置入口仍是本页 §4 表 + 事故 runbook 分级；账号级 vs 通道级的判别法（官方桌面同账号 A/B 单发）以事故 runbook 行为准。

**延迟与“吐字缓慢”的读法（2026-10-22 增补）**：`/v1/responses` 的 access log 总时长是**整段生成**耗时，不是首字延迟；`/v1/models` 是本地 0 秒目录读取，`/v1/responses` 常见 3–10 秒是上游生成时间，不能据此判定“吐字缓慢”。判断链路延迟要看 `stream-bootstrap-buffering=false` / `stream-bootstrap-timeout=0`、Nginx `proxy_buffering off` 与真实 SSE 首字；本机非 OAuth lane 的 `cache-canary` 与流式探针是唯一可复现的对照。不要为了压低时长改并发、轮换账号或放宽流式缓冲——那些会改变风控画像。单次长尾（p99 数十秒）在共享上游容量窗内属正常，按 §4 容量/`429` 处置，不做客户端连点重试。

## 5. 历史观测（仅作故障背景）

- 过去 OAuth 管理流程曾成功创建 WorkBuddy Intl 凭据，API 目录观测过 26 个模型；账号实际权益有限，因此当时将 `models_intl` 钉为 `deepseek-v4.1-flash`。该历史目录和单次成功调用不证明当前权益、账号状态或调用安全。
- 一次 user-first 请求得到 `11128`，随后出现凭据冷却；这只说明该次错误可影响后续调用，不代表可用的固定重试时长。
- 历史配置含 `scheduler_mode: credits`，本次保留该值；其具体账号选择和切换行为未独立复核，因此不把它描述成防封控制。
- 现行配置仅开启 CN 自动签到以领取签到积分；任务循环、生命周期和 token keepalive 自动动作关闭。不要依赖 token 保活或任务循环去维持账号。
- 上一轮逻辑卸载不能证明旧 scheduler goroutine 已退出；当前管理器已重新注册，CPA 未重启。若以后需要进程级清除，应单独评估重启对其他 provider 的影响，不能把普通风控处置自动升级为重启。

## 6. 配置恢复与回滚

WorkBuddy 当前应保持注册；不要把禁用实例作为常规风控措施。恢复前快照 `config.manual-snapshot-20261018T145110Z-pre-workbuddy-reenable.yaml` 是整份配置，并且记录的是 WorkBuddy 关闭、自动任务键未显式关闭的旧状态。直接覆盖当前配置会关闭 WorkBuddy 并可能撤销其他配置变更。仅在当前配置损坏时先比较快照与现状，备份之后的变化，再做语义级最小恢复；Git 回滚不能替代远端恢复。

## 7. 上游更新

### 保留全部账号与渠道时的运行约束

- 当前 credits 允许合格凭据回退；同名 CN/INTL 模型目录允许跨 realm 候选，
  不能宣称账号隔离或将回退当成防限流措施。遇账号风险或未知拒绝信号时停止
  当前任务，不由调用端连点重试、不清冷却或人工换号续打。
- OpenCode 的 DeepSeek 兜底槽位历史：2026-11-04 起固定 `priority: -1`
  （wb 主 oc 备）；2026-11-11 oc Go 订阅失效且用户决定暂不续订，slot-2
  整腿连同 `deepseek-v4.1-flash-oc` 单宿别名一并退役（routes manifest 删
  slot 2，apply 对账重建即清 live 段）。动因：wb 全候选耗尽时宿主兜底打到
  死 oc 腿回 JSON 403 `permission_error`，admission 按认证类分层落持久锁，
  把配额窗故障放大成整车道人工停摆。**2026-11-14 订阅续订后按用户指令
  重新收录并收窄：slot 2 只开放 `deepseek-v4.1-flash-oc` 单宿别名，裸名
  `deepseek-v4.1-flash` 不回 oc 槽、仍属 workbuddy 插件车道独占，旧
  dual-home 与 priority 主备排序不恢复**（doctor `deepseek-route-order`
  期望 alias-only 模型集，终行 `WB_BARE_ONLY_OC_ALIAS`）。
  providers.env 的 BASE_URL_2/API_KEY_2 映射两次退役期间均保留未动，
  续订沿用原 key。WorkBuddy 插件凭据保持默认 `priority=0`；priority 变更
  热加载不生效，须重启容器。
  wb 层内的 CN/INTL 候选自 2026-11-02 晚起是 priority-reclaim 每请求取
  文件名序最前 live 的软顺序（见 runbook 顶部），已有会话亲和可以暂时
  保持原 provider。
- `request-retry=0` 只禁止额外轮次；`max-retry-credentials=3` 让单个请求至多触达
  三个合格凭据（2026-11-08 再评审，原为 1），因此深度双宿请求不会遍历整池，
  也不会无限扇出到 OpenCode。`credits` 仍负责后续请求的粘性选择，不能因凭据数
  增加便提高并发。多个 API key 可能共享组织/项目额度。
- 限流同时核对上游错误、HTTP/SSE 信封、可信 reset 与调用方重试；并发上限
  不是 RPM/TPM 预算。未知供应商额度不设置猜测的安全阈值。
- 明文 HTTP 渠道在用户要求保留时如实提示传输风险；2026-10-31 只读
  TCP/TLS 探针确认 `35.213.82.91:443` 主动拒绝、`8003` 仍开放，未发现可
  替换的 HTTPS 端点。保持该渠道为已接受传输风险，不自动改 URL、放宽证书
  校验、轮换 key 或关闭渠道。
- 本地风险锁与官方账号状态分开：`unverified_request` 表示结果未确认，复核
  admission 停启与请求时间线后才按既有人工恢复流程处理，不自动清锁。
- 质量观测固定模型、reasoning 设置、上下文与输出预算；served model 名称
  差异只作线索，不改名消警，不以周期性 OAuth 生成探针证明账号安全。
- 2026-11-01 复核确认 `codex_local_access` 与 `fq_sciman_top` 均使用
  `request_max_retries=0`；现役 provider 使用 `stream_max_retries=2`，对照
  provider 保持 0。请求级失败不重放，已开始 SSE 的传输截断允许一次有界重连。
  隔离 A/B（Codex CLI 0.166.0）确认 0 只发 1 次并显示解码错误，2 自动重连一次
  后完成。仅证明磁盘配置与隔离客户端行为，下一会话加载效果待验证。
  Cockpit 管理目录中的退役模型
  仍须通过其 UI 获取上游模型后同步，禁止外部覆盖生成目录。

未来如评估插件更新，先核对官方服务条款、插件 release / asset digest、宿主 ABI 与插件源码实现，再执行独立受控变更；禁止未核哈希覆盖 `.so`。常规风险控制不包含自动下载或更新插件。

启用原生插件时，后台 CPA 镜像更新遇到新候选返回 `77 / deferred / PLUGIN_CANARY_REQUIRED`，保留当前镜像与插件，不发送生成探针。这是待人工复核的升级候选，不是插件已损坏。无新候选的每日 readiness 继续运行。

联合更新顺序：固定 CPA digest、插件源码 revision、补丁与产物 SHA-256；核对 Go 工具链、构建选项及共同依赖；在隔离且无真实凭据的环境验证加载/注册；备份镜像引用与插件；在维护窗口更新后读回实际注册版本和哈希。只有加载证据不能升级为业务验收；需要业务验收时按独立授权执行。回滚应匹配宿主与插件组合，保留当前凭据，不覆盖整份旧配置。
