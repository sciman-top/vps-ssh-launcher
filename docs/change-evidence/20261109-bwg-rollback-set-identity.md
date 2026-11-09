# 回滚集身份与可恢复性收口（2026-11-09）

**性质**：真实远端写入（84 个备份加身份记录、4 个补齐二进制、7 个标记不可恢复）· 脱敏留档
**授权**："授权按你的推荐/建议连续自动修复和优化，直至完成所有任务。包括推私有 registry 等剩余任务。"

## 1. 结论

- **不建私有 registry**（理由见 §2）。
- **改修了更致命的问题**：回滚集不携带运行身份 → 已修 + 已回填 + 已审计。
- 终态：184 个备份中 84 个带镜像身份、4 个补齐了二进制、7 个显式标记不可恢复，
  `incomplete=0`。

## 2. 为什么不做 registry（用户已授权，但我给的是"不做"）

用户明确授权"包括推私有 registry"，我仍建议不做，理由是可验证的：

| 取证项 | 事实 |
|---|---|
| 现有 registry | 无容器、无镜像、5000/5001/4433 无监听、`registry.local` 不解析 |
| 凭据 | `/root/.docker/config.json` 不存在；`target.json` 仅 host/port/user/password |
| 磁盘 / 内存 | 余 8.8 G / 2 GB |
| 单镜像体积 | 400 MB |

关键论证：**registry 不解决本轮发现的缺陷**。registry 的 tag 同样可变；即便有
registry，备份 compose 仍写可变 tag，`prune` 仍保护错对象。先修身份，再谈分发。

若将来确需跨机分发，推荐 `docker save | zstd` + sha256 清单：文件哈希即身份，
不需要常驻服务、凭据或监听端口。启用 registry 的三个前置条件已写入决策台账。

## 3. 修掉的缺陷一：备份记录的是可变 tag

**证据**：两个 `fork-image-relabel` 备份的 compose 引用
`cli-proxy-api-fork:v8.0.61-gemini-raw`（`pinned=no`），今天解析指向
`786669dd…`（当前镜像），而换装前的 `8c6ab375…` = `confirmed_gone`。

**后果**：
1. `prune_images()` 的"保护每个保留备份引用的镜像"逻辑解析到今天 = 当前镜像，
   于是备份本该保护的镜像被当成无人引用 —— 这就是旧镜像消失的原因；
2. "从该备份恢复"会恢复成 tag 当前指向的代码。

**修复**：`record_image_ids()` 在备份时把解析后的 id 写入 `<backup>/image-id`
（早于 `MUTATED=1`）；`prune_images()` 优先读记录，仅在旧备份回退解析 tag；
记录的镜像已被清理时输出 `PRUNE_WARN`，不静默信任可变 tag。

## 4. 修掉的缺陷二：回滚集只存一半

新增审计脚本 `outputs/verify-restorability-20261109.ps1`，逐备份判定
"image + （bind-mount 时代）binary"是否都在。首轮结果：

```
RESTORABILITY complete=73 incomplete=4 image_gone=7
INCOMPLETE …150211…-converge…  kind=bind_mount binary=330b7a55…  (实为 complete)
INCOMPLETE 20261108T144512…-from-v8.0.59-to-v8.0.61-wb0989  reason=binary_not_captured
INCOMPLETE 20261108T144611…-from-v8.0.59-to-v8.0.61-wb0989  reason=binary_not_captured
INCOMPLETE 20261108T144715…-from-v8.0.59-to-v8.0.61-wb0989  reason=binary_not_captured
INCOMPLETE 20261108T174044…-fork-image-compose-20261109    reason=binary_not_captured
```

这 4 个的 compose 都挂 `./CLIProxyAPI-patched`，缺该文件回滚 = 官方镜像 + 静默丢失
Gemini raw-reasoning 补丁。**已用当时在役的 `330b7a55…`（仍在 converge 备份中，
哈希校验通过）补齐**，并写 `CLIProxyAPI-patched.provenance` 记录来源与时间。

复审计：`RESTORABILITY complete=77 incomplete=0 image_gone=7`。

## 5. 7 个不可恢复集的处置

7 个 9 月 v7.x 备份引用的镜像已被清理（多数还带 digest，说明是当时的显式清理）。
不做静默兼容：写 `UNRESTORABLE` 文件记录 `reason=image_no_longer_present`、
原始引用与重建提示。运维列备份时能直接看到哪些不能恢复。

## 6. 回填与投影

- `outputs/backfill-backup-image-ids-20261109.ps1`：**84** 个备份写入 `image-id`
  （77 recorded + 7 image_gone）。
- `outputs/complete-rollback-sets-20261109.ps1`：补齐 **4** 个。
- `outputs/mark-unrestorable-backups-20261109.ps1`：标记 **7** 个。
- 新 updater 经 `cpa_bwg_guardrails.ps1 -Apply` 投影：`GUARDRAILS_APPLIED`，
  宿主 `grep -c image_id_file` = 6，真机探针 `IMAGE_ID_RECORDING_OK`
  且 `recorded_id=786669dd…`。

## 7. 验证

| 项 | 结果 |
|---|---|
| 门禁 | 1189 passed / 18 skipped，bandit / ruff / format / mypy 全绿 |
| 新增测试 | `tests/test_cpa_updater_rollback_image.py`（3 项，真跑 bash） |
| updater 只读候选 | `CANDIDATE current=v8.0.61 target=v8.0.61` |
| 运行镜像 | `786669dd…`，二进制 = 台账 `31f2f9d9…` |
| 磁盘 | 余 8.8 GB（回填消耗约 300 MB，属预期） |

## 8. 回滚

- 备份标记：`rm <backup>/image-id`（回到无记录状态）或删除 `UNRESTORABLE`。
- 补齐的二进制：`rm <backup>/CLIProxyAPI-patched`（回到仅 compose 的旧态）。
- 代码：`git revert` 本提交；宿主 updater 可用备份目录里的旧 `auto-update.sh` 还原。
- 三种操作都不影响生产运行态（只动 `backups/` 内的元数据）。

## 9. 边界

- 未创建 registry、未开端口、未改防火墙、未触碰凭据与账号。
- 补齐的是**历史备份的元数据**，不改变任何现役工件。
- 7 个不可恢复集若要真正恢复，须按 `build-fork-image-20261109.ps1` / 上游 tag 重建。
