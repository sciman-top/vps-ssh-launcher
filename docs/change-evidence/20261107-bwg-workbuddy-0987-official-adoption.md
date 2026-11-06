# 2026-11-07 BWG CPA：WorkBuddy 0.9.87 官方件收编（v0.8.95）

## 目标与等价性审查

- 生产在役为 11-05 的 canonical 本地构建（`workbuddy.so` SHA `7093a190…`，
  6dccdfb9 + 本地 `scripts/patches/workbuddy-v0.8.94-canonical-6004-registry.patch`）。
- 上游 2026-11-06 发布 `v0.8.95`，其中 `744f255`（workbuddy 0.9.87，issue #76，
  提交信息注明源自本仓 11-05 审计）与本地补丁同源：6004 窗口在 scheduler.pick
  与 catalog（model.for_auth）双边界尊重、registry 按 canonical auth id 重键。
  归一化 diff 证实语义一致（上游为打磨形：`modelRateLimitActive` 布尔助手 +
  `filterModelRateLimitedModels` 目录过滤器；scheduler 侧 9 行、models 侧
  21 行差异均为 API 形状与注释）。`v0.8.95` 其余增量（qoder 0.8.74、darwin
  CI runner 调整）不进入 workbuddy.so。
- 收编动机：退役本地补丁，钉官方 CI 产物；doctor 版本门禁仍钉 0.9.87，不变。

## 工件与来源

- 发布资产 `cpa-multi-plugins-linux-amd64.zip`（tag `v0.8.95`）：
  zip SHA-256 `fc2d1e38b1ff48cc8da84b6aff6713fe8ffb7dd768d10948280ca3cb0b92bc5b`
  （本地下载取证），`workbuddy.so` SHA-256
  `b2b80c066beade522caaed89f43748ee4b96a66545fa956473419bc81e74882b`
  （8,848,768 字节；本地提取与 bwg 侧 curl 下载逐字节一致）。
- 字节取证：`0.9.87` 版本串 ×2、`filterModelRateLimitedModels` 符号在、
  intl-shared 退役 marker 不在。

## 事务与验收

1. **隔离加载**（`outputs/cpa-v8058-workbuddy-0987-official-isolated-load-20261107.ps1`）：
   无凭据、无默认网络 namespace 中官方 .so × v8.0.58 宿主：
   `ISOLATED_OFFICIAL_0987_LOAD=PASS`，注册 `version=0.9.87`，
   `scheduler_priority_reclaim=true`、`account_max_concurrent=1` applied，
   零 panic/fatal。
2. **生产换装**（`outputs/deploy-workbuddy-0987-official-20261107.ps1`，
   2026-11-06T18:40Z；先 `-DryRun` 全绿）：
   - staging：bwg 侧 curl 官方 zip，zip 与 .so 双 SHA 断言通过。
   - 入口 503 门 → `requests.lock` 排他 drain（`DRAIN_OK`）→ 备份
     `/opt/cliproxyapi/backups/20261106T184021.813421324Z-plugin-0987-official`
     （plugin/compose/config + auth 哈希）→ 原子换装 → 单次
     `docker compose restart`。
   - 回执：`PLUGIN_REGISTERED version=0.9.87 sha=b2b80c06…`、`HEALTH_OK`、
     `LANE_HOLD_DELTA=none`、`AUTH_BYTES_UNCHANGED`、
     `REFUSE_PLUGIN_ONLY_VIOLATION`（config/compose 未动）、
     `MAINTENANCE_GATE_OFF`（入口配置按 sha 复原）、`restarts=0`、
     `DEPLOY_OK`。

## 验收与回滚

- strict doctor：`workbuddy-version=0.9.87` 继续全绿；13 项投影 MATCH。
- 回滚：同维护锁/drain 内从备份目录恢复 `workbuddy.so` 并
  `docker compose restart`（事务内置 rollback 路径，验收失败自动执行）。
  Git 回滚不能替代远端恢复。

## 边界

- 收编仅证明同一语义的官方构建可加载、可注册、护栏项 applied 与就绪；
  6004 窗口尊重的请求层行为由 11-05 candidate matrix 与上游自身测试覆盖，
  本事务未注入探针请求。WorkBuddy 官方不认可逆向接入，低频与单发验收不改变
  该边界。
