# 槽位 1 gpt-image-2.5 图像路由退役 + 桌面目录旧名清理

**时间**：2026-10-04 12:20–13:15 CST
**背景**：用户核对五槽位目录路由后决策：(1) 确认粘性隐藏不可自愈；
(2) 清除桌面目录中可证伪的多余旧名；(3) 退役从未投产使用的
`gpt-image-2.5` 图像路由。本事务按 `docs/runbooks/cpa-catalog-change-checklist.md`
照单执行。cpa_catalog_expectations.py 不含该名，零改动。

## 仓库改动（commit 85656e5）

- `scripts/remote/cpa_provider_routes.json`：槽位 1 摘除
  `gpt-image-2.5` 模型条目并整删 `image_models` 键（`optional_models`
  仅剩 `gpt-6.1-sol-input`）；两份排除清单保留该名作退役墓碑。
- `scripts/cpa_bwg_guardrails.ps1`：apply 后目录探针行翻为
  `has_retired_slot1_image_gpt_2_5`（预期 False）。
- `test_scripts.py`：墓碑代表集纳入该名；槽位 1 断言同步；
  原 image-skip 用例改写为"无图像声明时生成矩阵全探测"契约
  （`_IMAGE_PROVIDER_MODEL_ALIASES == frozenset()`，cpa-health.py
  派生层零改动）。
- `docs/runbooks/cpa-gateway.md`、`cpa-oauth-luna-slot.md`：目录描述段记退役。
- full gates 绿：411 passed, 1 skipped, 312 subtests（lint/mypy/bandit 含）。

## Apply 与终验

- `commit → -Apply → doctor` 顺序执行；apply 备份
  `/root/cpa-guardrails-backup-20261004T050559.197654898Z`。
- apply 回执：`GUARDRAILS_APPLIED`、`READY_STATUS=200`、`HEALTH_OK`、
  `models=13`、`has_retired_slot1_image_gpt_2_5=False`、
  `CODEX_OAUTH_ROUTES_READY` 三 OAuth 名 allowed。
- doctor 终验：`DOCTOR_CONTRACT_OK`、drift 全 MATCH、
  `MODEL_IDS_UNKNOWN=none`、`catalog_oauth_missing=none`；
  客户端目录 = 13 个在册名全数暴露。
- 验收深度对照 20260924 退役先例（apply 回执 + 目录摘要 + doctor + SHA），
  退役不新增路由，未做生成探针。

## 粘性隐藏自愈问题的实证结论

不可自愈：隐藏态在 CPA 容器内存中，容器不重启即持续。本次 apply 自带的
容器重启顺带清空粘性态，重启后 `gpt-6-astra`、`gpt-6-astra-ciii`、
`deepseek-v4.1-flash`、`gpt-6.1-sol-input` 全部重新注册（此前 4 名缺失）——
证实此前塌缩 = 上游瞬时不可用 + 粘性态卡住，上游恢复后一次重启即全量回归。

## 桌面目录清理（用户授权，文件由 Cockpit app 管理）

- `~/.antigravity_cockpit/codex_model_providers.json`：
  - `CPA (local 10909)` 移除 `gpt-6-astra`、`gpt-6-astra-cii`、
    `gpt-image-2.5`（前两个为 provider-health 判定 local_gateway 不可路由）；
  - `fq.sciman.top` 移除 `gpt-image-2.5`（退役后网关不再路由）。
  - 备份：`codex_model_providers.json.before-rm-stale-img-20261004-1311.bak`。
- 直连上游条目（ai.input.im / codex.ciii.club / 35.213.82.91 / DeepSeek /
  Zhipu GLM）的 modelCatalog 是各上游自有清单的缓存，其 `gpt-image-2.5` /
  旧名仍被上游真实供给，不作清理；条目均休眠。
- 写后等 3 秒回读确认未被运行中 Cockpit 重投影；
  `cockpit_provider_health.py --check-all-providers` 退出码 0，
  `未发现配置层面的已知故障形态`。
- 全局 `~/.codex/cockpit-model-catalog.json`（9 slug）不含该名，零改动。

## 观察与边界

- apply 重启窗口后 1h 内出现 16 条 5xx（502×8 / 503×8），归因全部为
  单一外部客户端 /16 的**上游透传**（status_upstream 502/502、503/503，
  零本地冷却快败），与已知 sol 族慢性 502 窗口形态一致，与本变更无关
  （本次仅摘除未使用名单条）。
- 并行会话提示：本事务执行期间另一会话提交 43b1024（转储取证工具收口，
  不同切片）并在操作桌面 config.toml（13:10 前后）；本事务改动文件与其
  无交集，registry 编辑时该文件已稳定 45 分钟。

## 受控实战验收（2026-10-04 13:35–13:55 CST，用户授权补轮）

- 载体：远端 loopback `cpa-health.py generation-all`（canonical 单发不重试，
  经 admission 全链），加一发 gpt-6-luna 定向探针（8318，b64 投影，
  一发不重试）。
- 矩阵结果（10 个 API-key lane 模型）：**7×200/stop 生成级 LIVE_ACCEPTED**
  ——gpt-6-astra 6.6s、gpt-6.1-sol-input 7.7s、gpt-6.1-sol-91 7.5s、
  gpt-5.6-terra 3.5s、glm-5.3 1.4s、glm-5.3-flash 2.1s、deepseek-flash 0.7s
  （含此前塌缩回归的 astra / sol-input）；**3×502 上游透传**
  （error_class=transient_upstream：gpt-6-astra-ciii 1.4s、
  gpt-6.1-sol-ciii 1.1s、deepseek-v4.1-flash 11.5s），零本地层失败，
  `MATRIX_EXIT=10`=上游侧未就绪、本地契约通过。
- OAuth：gpt-6-luna 单发 429@879ms（该时段 admission 梯度冷却保护窗内，
  按契约不追发）；gpt-5.6-luna 按"傍晚限额重置后再单发验收"的既定决策
  未探针。
- 模拟验收（fixture）豁免：fixture 是二进制行为验收（过载/冷却/更新
  回滚，netns 隔离实体），本次变更不动二进制与行为契约，v8.0.13 升级轮
  已全绿；cpa-acceptance 合成 config 与新 manifest 天然对齐（从未引用
  图像名）。对照 20260924 退役先例。
- **验收后目录动态（保护层按设计工作，非回归）**：矩阵中吃 502 的
  ciii 双名与 deepseek-v4.1-flash 被 v8.0.8 粘性层重新摘出目录（单次上游
  502 → 模型级隐藏），OAuth 的 gpt-6.1-sol / gpt-5.6-luna 亦随上游可用性
  退出目录视图；fresh doctor `DOCTOR_CONTRACT_OK`、drift 全 MATCH、
  `catalog_oauth_missing=gpt-5.6-luna,gpt-6.1-sol`（OAuth 别名天生
  optional，优雅降级不红）。恢复路径不变：上游恢复 + 容器重启重注册；
  此时重启无意义（上游仍在故障窗，重启后即时再隐藏）。

## 回滚

- 仓库：`git revert 85656e5` 后重走 commit → apply → doctor。
- 远端：apply 备份目录（上记）按 `cpa-manual-rollback.md` 恢复。
- 桌面：`codex_model_providers.json.before-rm-stale-img-20261004-1311.bak`。
