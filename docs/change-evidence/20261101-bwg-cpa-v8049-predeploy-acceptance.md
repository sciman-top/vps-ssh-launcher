# BWG CPA v8.0.49 联合 canary 部署前验收（2026-11-01）

## 目标与结论先行

按用户"连续自动修复/优化直至完成"授权，推进唯一待办升级：上游 `v8.0.49`
（pluginhost 多 OAuth 登录项 + Antigravity/Gemini 质量与性能修复）。本轮**未改
运行配置、未动生产容器**；完成了联合 canary 的 **stage / accept / load** 三阶段
隔离验收（真实 v8.0.49 二进制 + 未变的 WorkBuddy `0.9.82` 插件，mock 上游、隔离
网络+mount 命名空间、无真实凭据/流量）。仅 `deploy`（可逆单机生产切换）留待
72h soak 成熟后再执行。

**关键结论**：v8.0.49 的 pluginhost 变更是插件宿主（bwg）最大风险面；隔离验收证明
`0.9.82` 插件在新二进制上注册成功、`account_max_concurrent=1 applied`、无
panic/fatal。`deploy` 入口已就绪，成熟即可一键执行。

## 固定版本

| 项 | 值 |
|---|---|
| 当前运行 | `v8.0.48@sha256:c198886a86b57f49cb2508a68b6170e23b2d509c648934af5d5dd827cf6faa16` |
| 目标 | `v8.0.49@sha256:20925aef8323b3f2539605aa048bb59ff17cc9761cba311e2eb1e1723b1ff42e` |
| 插件（不变） | `workbuddy.so` sha256 `81f0ccf3082e878fbe3cb66bf167c377c8d1029ece86dac5b322352a9c806be6`，version `0.9.82` |

## 新增工件（`outputs/`）

- `_gen_v8049_canary.py`：从已评审 v8.0.48 模板机械派生 v8.0.49 脚本，每处 plaintext 锚点替换都断言命中数（V 路径/插件版本/镜像 pin 均不在 base64 fixture 内，故 fixture 字节复用安全）。
- `stage-v8049-20261101.sh` / `accept-v8049-20261101.sh` / `plugin-load-v8049-20261101.sh` / `deploy-cpa-v8049-canary-20261101.sh` / `run-cpa-v8049-canary.ps1`。

## 验证证据

- `repo_verified`：四脚本 `bash -n` 全通过；`pytest tests/test_cpa_updater_apply.py`
  = `27 passed, 51 subtests passed`（含 `test_deploy_canaries_replicate_the_traffic_guard_drain`
  对新增 deploy 脚本的 drain/flock 契约门禁）。
- `stage`（host，隔离）：拉取 v8.0.49 镜像至本地（digest `20925aef…`），抽取二进制
  sha `ed404b6f3734b7f3f92bfc0702aa865c5bcd8b1bb6440bbfe08f16f4e1eebdf4`
  （`CLIProxyAPI Version: v8.0.49, Commit: 250d598`），插件由其已评审构建目录复制
  （sha 与线上 `81f0ccf3…` 一致）。
- `accept`（host，隔离 net+mount ns）：`ACCEPTANCE_RESULT=PASS`。catalog 契约一致；
  sol 冒烟 `200 gpt-6.1-sol stop`；容量分类正确；update 场景
  `start_fail/model_exposure` 回滚恢复、`transient` 记 UNVERIFIED、`success` 直通。
- `load`（host，隔离）：`ISOLATED_PLUGIN_LOAD=PASS`；
  `plugin registered plugin_id=workbuddy version=0.9.82`；
  `workbuddy: account_max_concurrent=1 applied`；无 panic/fatal。
- `host_loaded`（生产只读基线，验收前后各核）：仍 `v8.0.48@c198886a…`，容器 running、
  restarts=0、`/healthz status=ok`；插件 sha 未变。**生产未被三阶段触及。**

## 门禁与 deploy 条件（未执行）

- 成熟点：`v8.0.49` 发布于 2026-10-31 21:09 CST，72h soak 成熟于 **2026-11-03 21:09 CST**。
- 宿主为插件宿主，updater 对新候选一律 `PLUGIN_CANARY_REQUIRED`（exit 77），故须本
  canary 的 `deploy` phase（`run-cpa-v8049-canary.ps1 -Phase deploy`）。
- deploy 事务：pin 校验 → 插件 sha 校验 → admission idle 快照 → 备份（compose/config/
  plugin.sha/auth.sha）→ 拉取并校验目标镜像 → drain（`flock requests.lock`）→ 原子替换
  compose → recreate → 断言插件注册 `version=0.9.82` → lane-hold 差分（仅本事务重启
  期间新出现的 durable hold 才判失败）→ generation/readiness → 任一不满足即回滚到
  `v8.0.48`。

## 回滚与边界

- 本轮无生产写入、无配置变更，无回滚对象；隔离 fixture 与已拉取的 v8.0.49 镜像可随时清理。
- deploy 的回滚只恢复本次 `compose.yml`（备份目录 + auth/plugin 哈希校验）；Git 回滚不替代远端恢复。
- 边界：stage/accept/load 为隔离受控验收，**不证明** v8.0.49 在真实自然流量下的长期
  质量、上游配额或 China 网络验收；deploy 后仍须 strict doctor + 单发 SSE + 短窗自然流量复核。
