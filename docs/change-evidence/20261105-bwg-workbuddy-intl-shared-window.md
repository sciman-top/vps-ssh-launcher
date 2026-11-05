# BWG WorkBuddy INTL 6004 共享窗口插件补丁（2026-11-05）

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

## 回滚

在维护锁和入口排空下，从上述备份恢复 `workbuddy.so`，执行
`docker compose -f /opt/cliproxyapi/compose.yml restart cli-proxy-api`，
核对旧 SHA 回执、插件注册、readiness 与入口恢复。Git 回滚不能替代远端恢复；
不得回放凭据快照。
