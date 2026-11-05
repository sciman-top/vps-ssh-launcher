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

## 回滚

在维护锁和入口排空下，从上述备份恢复 `workbuddy.so`，执行
`docker compose -f /opt/cliproxyapi/compose.yml restart cli-proxy-api`，
核对旧 SHA 回执、插件注册、readiness 与入口恢复。Git 回滚不能替代远端恢复；
不得回放凭据快照。
