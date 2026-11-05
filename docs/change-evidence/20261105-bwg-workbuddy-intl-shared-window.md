# BWG WorkBuddy INTL 6004 共享窗口插件补丁（2026-11-05）

> **最终结论（2026-11-05 晚些时候）**：早期“INT realm 共享 6004 桶”假设已被
> 跨出口、跨 CDN 边缘与跨 token 的直连探针推翻。限制键是独立的
> `(token, model)` 日窗，不是出口 IP 或登录面共享桶。INT 共享窗口补丁因此
> 已退役，生产已切回官方 `cpa-multi-plugins v0.8.94` / WorkBuddy `0.9.86`。
> 下文 §1–§5 是历史假设与当时的运行记录，不得作为后续共享限流依据；
> 最终实例、换装回执与 issue 在文末[当前生产](#生产采用官方-0986)和
> [独立日窗校验](#独立日窗校验与历史结论撤回)。

## 目标与边界

用户授权按推荐连续修复。目标是把已经验证的 INTL 共享 6004 桶落实到插件调度，
避免同一客户端请求对 7 个 INTL 凭据逐一打上游；不解除上游窗口，不增加额度，
不修改凭据、模型目录、并发、RPM、重试、账号顺序或 CN 独立窗口。

仓库变更：

- 新增 `scripts/patches/workbuddy-v0.8.93-intl-shared-window.patch`。
- 新增构建、隔离加载和受控部署脚本
  `outputs/build-workbuddy-0985-intl-shared-20261105.ps1`、
  `outputs/cpa-v8054-workbuddy-intl-shared-load-20261105.ps1`、
  `outputs/deploy-workbuddy-0985-intl-shared-20261105.ps1`。
- 补丁在全部同步、异步和流式 6004 记录点建立 INTL realm/model reset；
  调度层跳过已知 INTL 候选，executor 层对无法识别 realm 的候选做二层快速失败。

## 构建与隔离验证

- 源：`cpa-multi-plugins v0.8.93` / commit
  `c326248ef6fd3dbeee9e16c53adf279ffa5904cf`，WorkBuddy `0.9.85`。
- 补丁 SHA-256：`06a4df9556af19877977ec37f8fbdbeded41e92c8cecddfc948934b56f24f200`。
- 远端构建通过 gofmt、`go vet ./...`、`go test -count=1 ./...` 与 Linux amd64
  C-shared build。
- 产物 SHA-256：
  `264722fa43b9da592fa3138ba29eeef35e7299ea54ff59beae41263c06e92f53`。
- 使用生产 CPA `v8.0.54@sha256:66e51ccb…` 在无凭据、无默认网络的
  mount/network namespace 中加载；`ISOLATED_PLUGIN_LOAD=PASS`，插件注册
  `version=0.9.85`，`scheduler_priority_reclaim=true`、
  `account_max_concurrent=1`。

## 生产事务

- dry-run：旧 SHA `79aa1491…`（首次补丁探针版）、新 SHA `264722fa…`，
  `active=0`、`holds=[]`。
- 部署前备份：
  `/opt/cliproxyapi/backups/20261104T174426.701855172Z-plugin-0985-intl-shared`。
- 备份插件、compose、config、nginx 入口与凭据哈希；入口短暂 503，在途归零后
  原子替换 `.so` 并只重启 CPA，随后恢复入口。
- 回执：`RESTART_APPLIED`、`PLUGIN_REGISTERED version=0.9.85`、
  `PATCH_MARKER_OK`、`HEALTH_OK`、`LANE_HOLD_DELTA=none`、
  `AUTH_BYTES_UNCHANGED`、`MAINTENANCE_GATE_OFF`、`DEPLOY_OK`。
- 后续独立任务把合法 `models_intl` pin 扩为两个模型，配置最终 SHA 为
  `2b6bc82c…`。部署脚本已改为：插件哈希已匹配时直接 health-check 并返回
  `ALREADY_APPLIED`；只有真正从旧插件换装时才要求配置基线匹配。
- strict doctor：`DOCTOR_CONTRACT_OK`；WorkBuddy lane `OPEN`，
  admission/config/projection 全部 `MATCH`，容器 `restarts=0`。

## 自然流量验收

首次部署只覆盖 HTTP `>=400` 路径，自然流量证明异步 200-内嵌 6004 仍在遍历
INTL；随后补齐全部三处 6004 记录点并重新构建、部署。二次部署后的自然流水中，
首个真实 INTL 6004 后，其余 INTL 候选为约 120–140ms 本地共享窗口拒绝；真实
上游拒绝只剩首个 INT 与 CN 三个独立账号窗口，随后请求继续到 OpenCode。

部署前 3 小时 admission 自然流量：WorkBuddy/DeepSeek `347/347 completed`，
capacity/upstream_error/client_disconnect 均为 0。二次部署后的最终 1 小时窗口
（含切换前流量）为 `152/152 completed`，同样三项失败计数为 0；期间直接观察到
共享 fast-fail 生效。后续 5 分钟自然日志中 WorkBuddy 6004 真实上游拒绝为 0，
约 76 条均为毫秒级本地 park/共享窗口拒绝。该结果不改变上游共享桶，也不证明
长期账号安全。

## CN 为何不使用同一持久共享桶

CN 与 INTL 的现场形态不同，不能把一个 INT 的 6004 外推给整个 CN realm：

- INT 的 7 个账号曾在同一秒同步 age-out，说明它们命中同一个 realm/登录面模型窗；
- CN 的账号 reset 分别约在次日 `11:47:44`、`12:44:20`、`13:49:58`，且账号
  余额各不相同；已观察到 3 个账号各自 6004、另一个账号返回个人 `14012`；
- 官方错误码页只说明 `6004` 是“请求频率受限，优先切换模型”，没有声明 CN
  账号共享一个窗口。

因此 CN 保持 `(credential, model)` 独立窗口。把 CN 也做成 realm-wide 持久
短路会把仍有独立余量的账号隐藏，降低真实可用容量，并把一次账号拒绝错误升级
为整个 realm 的拒绝。当前 4 个 CN executor 行多数是 `8–33ms` 的本地窗口回放，
不等于 4 次真实上游访问；一个冷启动请求在全部 CN 都尚未缓存时仍可能逐个探测，
这是独立配额语义的代价，不是日志扇出缺陷。若目标改为 AUP 保守的“6004 后不
跨账号续打”，应作为显式策略变更处理，不能冒充共享桶事实。

## 受控 CN 回放与凭据副本清理（同日补验）

- 单独的私有 CPA `v8.0.55` fixture 使用 2 个随机合成账号：合成 INT 账号请求失败后，
  同一请求继续到独立 CN 夹具，HTTP `200`、结束时正文精确 `PONG`。该回放
  证明请求级失败不会错误阻断 CN；它不替代真实 OAuth 额度验收。
- 回放件仅位于 `/root/wb-replay-artifacts/`，SHA-256
  `9e335b13…`，从未部署到生产；生产 `.so` 仍为 `264722fa…`。
- 只读扫描发现 4 个历史 auth-backup 目录、14 个文件含当时的当前 WorkBuddy
  access/refresh token。删除前以固定目录 allowlist、owner、维护锁和 admission
  `requests.lock` 排空 0 在途复核；删除后重扫 `16,654` 个文件，
  `FILES_WITH_CURRENT_TOKEN=0`，生产 11 份 auth 文件完整，health 正常。
  上述为历史备份副本，不代表本轮补丁造成新泄漏；credential rotation/relogin
  仍须用户单独授权。
- 最近 2 小时自然窗口：INT 共享窗口本地拒绝 120、CN 账号窗口本地拒绝 66、
  real upstream 6004 为 1；CN 日志行主要是本地快速失败，不是逐个真实打上游。

## 回滚（历史补丁，已退役）

在维护锁和入口排空下，从上述备份恢复 `workbuddy.so`，执行
`docker compose -f /opt/cliproxyapi/compose.yml restart cli-proxy-api`，
核对旧 SHA 回执、插件注册、readiness 与入口恢复。Git 回滚不能替代远端恢复；
不得回放凭据快照。

## 生产采用官方 0.9.86

用户继续授权按推荐修复与优化。0.9.86 官方构建只替换
`/opt/cliproxyapi/plugins/workbuddy.so`，不替换宿主，不改变
config/compose、账号顺序、并发、重试或凭据：

- 源码 tag `v0.8.94` / commit
  `6dccdfb9f0cef599324c420a24057ea605970789`；隔离构建通过
  `gofmt` 检查、`go vet ./...`、`go test -count=1 ./...` 与 Linux amd64
  C-shared build。
- 工件 SHA-256
  `04ed2703e32e3826257fbd0b78973b9583ad6f816b0b893bd555d5825df8d51d`，
  取代历史补丁 SHA `264722fa…`。
- `cpa-v8055-workbuddy-0986-isolated-load-20261105.ps1` 在生产 CPA
  `v8.0.55@sha256:afe1135e…` 上、无凭据、无默认网络的私有 mount/network
  namespace 中加载官方工件：`ISOLATED_WORKBUDDY_0986_LOAD=PASS`，注册
  `version=0.9.86`、`scheduler_priority_reclaim=true`、
  `account_max_concurrent=1`。
- `deploy-workbuddy-0986-official-20261105.ps1` 只接受旧 SHA
  `264722fa…` 或已换装的新 SHA；入口门禁、共享维护锁、admission
  `requests.lock` 内等待全部 lane 的 inflight/pending 归零后，备份插件/
  compose/config/入口及凭据哈希，再原子 swap 与一次 CPA restart。
- 生产备份：
  `/opt/cliproxyapi/backups/20261105T112945.868994148Z-plugin-0985-to-0986-official`。
- 实际回执：
  `DRAIN_OK`、`RESTART_APPLIED`、`READINESS_OK`、`LANE_HOLD_DELTA=none`、
  `AUTH_BYTES_UNCHANGED`、`PLUGIN_REGISTERED version=0.9.86`、
  `DEPLOY_OK`。新 `.so` 不含 `WorkBuddy INTL shared upstream rate-limit
  window` 历史 marker。
- 换装后单发 `flash-sg` 生产流式请求：HTTP `200`、4 个 SSE 事件、终态完整、
  正文精确 `PONG`；无 `6004`/`14012`/`11140`/`upstream_error` 回显。

首次事务在入口短暂关闭后，`/run/cpa-admission/requests.lock` 的持有者在启动
检查时看起来空闲、约 60 秒后才释放，因此以 `DRAIN_LOCK_TIMEOUT` 安全退出，
入口与 readiness 已恢复、凭据未变。未用失败 dry-run 冒充部署回执；稍后在
锁内重做成功。这个失败模式证明只读 `0/0` 快照不能代替排他排空。

## 独立日窗校验与历史结论撤回

WorkBuddy 官方本地客户端正常，而 BWG CPA 多账号出现 6004，根因不能归为
“所有账号共享一个 6004 桶”，也不能用积分余额推断。宿主侧使用现有凭据按插件
线形状直连上游、零 CPA、零账号/配置变更的探针得到：

- 同一 token 经 BWG 与 ZZ 两个出口，reset 精确到同一时刻；
- 同一 token 经两个 CDN 边缘，reset 同样相同；
- 不同 token 的 reset 时分不同，模型轴也独立（同 token 的
  `flash` 在窗内，`flash-sg` 仍可返回）。

因此“共享”一词只适用于上游可能采用的限流机制，而不是这批账号之间可交换的
一个额度池。早期 7 个账号同秒 age-out 是同一波流量下的相关开启，不能外推为
共享键。官方 0.9.86 会记录精确的 `(uid, model)` reset，并已有 executor 与
catalog 边界检查；但 `scheduler.pick` 尚未消费该 registry，可能在本地快速
失败后再次优先选中有记录的候选。已在
[issue #76](https://github.com/mmqz/cpa-multi-plugins/issues/76) 提交源码
路径、无凭据复现和测试候选；因候选 ID / stored UID 的规范关联需维护者确认，
暂不安装该 patch。

历史凭据副本审计还发现 4 个旧 auth-backup 目录、14 个文件含当时 token。
删除前固定 allowlist，在维护锁与 `requests.lock` 排空后以 dry-run 核对；
删除后重新递归扫描 `16,653` 个文件，`FILES_WITH_CURRENT_TOKEN=0`，生产
auth 保持 11 份。删除的是历史副本，不代表 0.9.86 产生了新泄漏。

## 验收与残留边界

本次已完成：

- `repo_verified`：0.9.86 构建/隔离加载/部署脚本及回归契约；
- `filesystem_projected`：按运行门禁投影 0.9.86 版本检查，并在 strict
  doctor 中复核；
- `host_loaded`：CPA v8.0.55 + 官方 WorkBuddy 0.9.86、新 SHA、容器 ready；
- `controlled_acceptance`：隔离加载、单次生产 `PONG` 与版本/凭据完整性复核；
- `live_observed`：换装后在途/告警与短窗自然流量，不升级为长期额度或
  封号安全结论。

尚未完成的事项仅在上游或需用户单独授权：scheduler 候选 ID 映射回复、
更长自然流量分布观察，以及可能需要的凭据轮换。后者须明确授权，不能以
验收为由自动删除、轮换或重新登录。本地事实不解除 WorkBuddy 上游日窗。

## 2026-11-05 候选：同一 6004 坐标在调度/目录/执行器三层收敛

对照官方 `cpa-multi-plugins v0.8.94`、CLIProxyAPI `v8.0.55` 插件 ABI 和
WorkBuddy 官方错误码后，确认两个具体的本地缺陷：

- 6004 registry 的键是 stored UID，而 `scheduler.pick` 收到的 `AuthID` 是
  文件派生 ID。官方 host 的候选、模型发现、执行器实际上都回传同一 ID，但
  UID 与 ID 不是可互换的字符串；
- 普通模型冷却表忽略传入 ID，自行改写成 UID，于是和执行器的 ID 查询再次
  错配。

候选补丁
`scripts/patches/workbuddy-v0.8.94-canonical-6004-registry.patch`（SHA
`d158b41e…`）只接受规范的 `workbuddy[-intl]-<uid>.json` 形态；scheduler
使用同一个 ID 查询 registry，模型发现则用请求 `StorageJSON` 的 UID 查询。
普通冷却仍只查 scheduler/executor 的同一 ID，**不跨凭据匹配**。候选不包含
realm 共享、重试放大、冷却清理或凭据改写。它在官方源码上通过 apply check
和全套 Go 测试。

构建脚本在 fresh source copy 上应用补丁、执行 `gofmt`/`go vet`/`go test`
并固定产物 SHA `7093a190…`；隔离 CPA `v8.0.55` 加载返回
`ISOLATED_WORKBUDDY_0987_CANONICAL_LOAD=PASS`。该验证证明加载和静态坐标
修复可达；当时尚未投产。后续已完成维护锁排空、只换 `.so`、严格 doctor、
candidate-matrix 受控回放、生产单发；详见下文“生产换装 local 0.9.87”。

还有一个独立失败点：一个客户端请求登录到同一 `(token, model)` 时，可能先
出现真实上游拒绝，再出现本地 registry 拒绝。前者确实是上游日窗，后者是
2 秒后的 CPA 内部日志；它不证明多账号逐个真实访问上游。要彻底消除异步流
失败帧在候选切换时的尾部延迟，应在官方 scheduler/stream 生命周期上再加
受控测试，不应把日志行数当成上游请求数。

上游 [`mmqz/cpa-multi-plugins`](https://github.com/mmqz/cpa-multi-plugins/issues/76)
当前仍是 open，没有维护者回复确认 ID 映射；本地候选不能冒充官方已修复。
上游日窗本身继续按官方建议：等待声明的重置点或切换模型，不能靠网关解除。

## 生产换装 local 0.9.87

2026-11-06 完成本地补丁的受控生产上线，当时生产 CPA 为
`v8.0.55@sha256:afe1135e…`，前态 WorkBuddy 为官方 `0.9.86`，新态 SHA 为
`7093a190…`。补丁仍以 `v0.8.94` / `6dccdfb9` 为源，新增规范化候选 ID
到 file-derived UID 的精确解析，接入 `scheduler.pick` 与 catalog；不同账号、
未知后缀、同一账号的 sibling model 保持隔离。

- 受控矩阵 `CANDIDATE_MATRIX=PASS`：A 受限时选取独立 B；A、B 均受限时不选
  dead pair；catalog 只隐藏 A 的受限模型；未知后缀不继承 A；6004 不连带
  同账号 sibling model；11115 仍归请求过长的独立语义。
- 部署 `DRY_RUN_OK` 后在 `/run/vps-ssh-launcher-maintenance.lock` 与
  admission `requests.lock` 排他锁内等待全部 lane `active=0/pending=0`，
  入口 503、原子替换、一次 CPA restart、readiness 后恢复入口。
- 备份：`/opt/cliproxyapi/backups/20261105T161523.027392066Z-plugin-0986-to-0987-canonical`。
  回执：`DRAIN_OK`、`RESTART_APPLIED`、`SCHEDULER_COMPATIBILITY_OK`、
  `READINESS_OK`、`LANE_HOLD_DELTA=none`、`AUTH_BYTES_UNCHANGED`、
  `PLUGIN_REGISTERED version=0.9.87`、`POST_READY active=0 pending=0`、
  `MAINTENANCE_GATE_OFF`、`DEPLOY_OK`。
- strict doctor 回读 `DOCTOR_CONTRACT_OK`；容器 `running/restarts=0`，实际
  `.so` SHA 仍为 `7093a190…`，单次 `flash-sg` 生产流式请求 HTTP `200`、
  4 个 SSE 事件、终态完整、正文精确 `PONG`。

**自然流量验收边界（不把一次 PONG 当限额修复）**：换装后的短窗日志仍有
真实上游 6004。两个不同 INT 凭据在同一请求中分别得到真实拒绝，说明容量
耗尽时宿主仍按独立 `(token, model)` 窗口遍历；它们不是旧补丁的 realm-wide
短路，也不证明某个账号被封。新补丁保证已知的精确 UID/model 对不会由
scheduler/catalog 主动再次提供，却不能保证其他独立凭据仍有额度。认证文件
11 份的 stored UID 与 file suffix 全部一致；此检查不解密或回显 token。
2026-11-06 的后续部署事务将 `routing.retry.max-retry-credentials` 从 `0`
收紧到 `1`，保持 `request-retry=0`、`scheduler_mode=credits`、并发和会话
粘性不变。此前的同一请求跨两个真实 INT UID 暴露不再作为当前行为；单请求
最多触达一个凭据。下一独立请求仍可按账号独立窗口选择，上游日窗不会因网关
策略变化而解除。
受控 apply 回执：`GUARDRAILS_APPLIED`、`READY_STATUS=200`；备份为
`/root/cpa-guardrails-backup-20261105T181002.970366537Z`，旧配置 SHA
`e0f69c26…`，新配置 SHA `26951cb9…`。apply 后 CPA 于
`2026-11-05T18:14:22.047481941Z` 重启一次；strict doctor 返回
`DOCTOR_CONTRACT_OK`，再次 `flash-sg` 生产流式请求返回 HTTP `200`、完整
终态和精确 `PONG`。截至回读，18:14 后自然 WorkBuddy executor 失败样本为 0；
该零样本只说明当前窗口没有可归因事件，不外推为长期不会 6004。
因此本轮可宣称“本地 UID 坐标已修正并加载”，**不可**宣称“6004 从此消失”、
“所有账号共享同一个窗口”，或把多账号真实拒绝当成纯日志放大。

**未完成的性能证据**：短窗未发现同请求同一精确 `(UID, model)` 的本地 6004
紧随真实 6004 的新样本，但样本数不足以证明异步流尾部竞态已消除。若需证明，
还要在无凭据 fixture 内对真实流生命周期做确定性回放；这属于下一轮独立实验，
不能通过清冷却、增加重试或修改真实账号来取得。

**`11115` 与上下文长度**：另一类 `1,283,807 tokens` 超过 `1,048,576`
上限的错误是请求本身超长，不归因于这个账号或 6004；此类请求不应通过换号
重试，也不应被计为上游账号额度不足。

## 0.9.86 回滚

若确认本地回归，在维护锁与 `requests.lock` 排空内，从上述备份恢复旧
`workbuddy.so`、执行一次 `docker compose restart cli-proxy-api`，核对旧 SHA
`264722fa…` 与 readiness。历史共享补丁不能作为“修复回滚”重新生产部署；
更早期的插件回滚点只能按相应独立台账选择。恢复入口配置，不回放凭据快照。

## 0.9.87 回滚

若本地回归，在相同维护锁与 admission 排空门禁内，从
`/opt/cliproxyapi/backups/20261105T161523.027392066Z-plugin-0986-to-0987-canonical`
恢复 `workbuddy.so`，令实际 SHA 回到 `04ed2703…`，执行一次
`docker compose restart cli-proxy-api`，核对当前进程注册 `0.9.86`、
readiness 与入口恢复。请不要回放备份中的凭据副本，且版本门禁须与新实际
注册版本同步投影。
