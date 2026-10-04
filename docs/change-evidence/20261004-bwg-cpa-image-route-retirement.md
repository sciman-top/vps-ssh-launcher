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

## 回滚

- 仓库：`git revert 85656e5` 后重走 commit → apply → doctor。
- 远端：apply 备份目录（上记）按 `cpa-manual-rollback.md` 恢复。
- 桌面：`codex_model_providers.json.before-rm-stale-img-20261004-1311.bak`。
