# bwg CPA fork 派生镜像收敛（2026-11-09）

**性质**: 真实远端写入（派生镜像构建 + compose 换芯 + updater 投影）· 脱敏留档
**用户授权**: "授权按你的推荐/建议连续自动修复和优化，直至完成所有任务。"（2026-11-09）
**设计**: [cpa-fork-image-derivation-rfc.md](../runbooks/cpa-fork-image-derivation-rfc.md)（并行会话 b08ae41 提交，本会话实施）
**前序**: [20261108-bwg-cpa-v8061-converge-acceptance.md](20261108-bwg-cpa-v8061-converge-acceptance.md)（bind-mount 时代的收敛）

## 1. 一句话

生产网关从「官方镜像 v8.0.61 + bind-mount 覆盖二进制」迁移为「派生镜像
`cli-proxy-api-fork:v8.0.61-gemini-raw`」，doctor 新增 `==runtime-binary==` 门禁
（运行容器二进制 sha 必须等于仓库台账），updater 进入 fork 时代 fail-closed。

## 2. 事务链（全部收据）

| 步骤 | 收据 |
|---|---|
| 镜像构建 | `BUILD_DONE`，`FROM eceasy/cli-proxy-api:v8.0.61@sha256:fc714b43…` + `COPY --chmod=0755`；镜像内二进制 sha256 预检 = 台账 |
| compose 换芯 | `BACKUP_OK dir=/opt/cliproxyapi/backups/20261108T174044.604605158Z-fork-image-compose-20261109 pre=9ec4fbcc…` → `QUIET_OK`（inflight=0）→ `COMPOSE_REWRITE_OK`（四断言：镜像行改写 / bind-mount 行移除 / config+auth+plugins volume 保留 / umask+entrypoint 不变量保留）→ 容器重建 |
| 验证链 | `status=running restarts=0 image=cli-proxy-api-fork:v8.0.61-gemini-raw` → `BINARY_LEDGER_MATCH`（容器内 sha256 = `31f2f9d9…4f0be5`）→ `HEALTH_OK`+`READINESS_OK`（8317 直探）→ 三车道单发 200（ag 2.31s / wb 1.33s / codex 3.22s）→ **`SWAP_OK`** |
| updater 投影 | `-Apply` → `GUARDRAILS_APPLIED`；复验 `drift=auto-update.sh MATCH` |
| doctor 终验 | `runtime_binary_source=image` + `runtime_binary_hash=MATCH` → **`DOCTOR_CONTRACT_OK`** |
| updater --check | `CANDIDATE current=v8.0.61 target=v8.0.61`（fork tag 解析正常）+ `BACKUP_HEALTH ok`（无候选路径） |

镜像 id：`sha256:8c6ab3755b600f6eeabf85c2aff4acc3c5e2d53a38fb827e25714afa0a9f5e5a`
（本地构建无 registry RepoDigest，完整性由 doctor 的 hash 门禁承接，见 §4 偏差 2。）

## 3. 仓库面（c85e178）

- `scripts/remote/fork-binary.sha256`：唯一台账（裸 sha256，wrapper HEAD 锚定读取 + 格式校验）。
- `scripts/cpa_bwg_guardrails.ps1`：读台账并注入 doctor `__CPA_FORK_BINARY_SHA_VALUE__`。
- `scripts/remote/cpa-guardrail-doctor.sh`：新增 `==runtime-binary==`——source 由
  `docker inspect .Mounts` 判（`bind_mount|image`，仅 `unknown` FAIL），hash 由
  `docker exec sha256sum` 对台账（MISMATCH 必 FAIL）。
- `scripts/remote/cpa-auto-update.sh`：`compose_cpa_version` 与 selection 解析认识
  `cli-proxy-api-fork:v<upstream>-gemini-raw` 约定；canary 扩展——镜像仓库非
  `eceasy/cli-proxy-api` 时 `FORK_IMAGE_REBUILD_REQUIRED` exit 77，fork 时代自动
  换镜像被具名拒绝（新版本采纳 = 重建事务，与本表同构）。
- 测试：90 passed + 132 subtests（`test_cpa_updater_apply.py` + `test_cpa_guardrails_script.py`）。

## 4. 与 RFC 的实施偏差（有意，均有理由）

1. **`runtime_binary_source=image` 不做 strict FAIL**：bind-mount 是合法回滚终态，
   回滚后 doctor 必须保持绿色，否则事故恢复被自家门禁挡住。FAIL 条件收窄为
   `unknown`（容器不可读）与 hash 不匹配（执行了台账之外的二进制）。
2. **compose 钉 tag 而非派生镜像 digest**：本地构建镜像没有可 fetch 的 RepoDigest，
   `image: …@sha256:<imageid>` 可行但可读性差；完整性由 hash 门禁承接——tag 被重打
   指向别的构建时，容器内二进制 sha 即 MISMATCH FAIL。
3. **updater canary 扩展为 fork 时代全拒**：RFC 原文"updater 保持不变"在派生镜像
   世界会重新引入「官方 tag 覆写丢 fork 补丁」的静默降级（7602259 的 canary 只在
   bind-mount 世界生效）；具名拒绝 + 台账/解析/换芯事务同构更新才是 fail-closed 终态。

## 5. 回滚

```bash
BK=/opt/cliproxyapi/backups/20261108T174044.604605158Z-fork-image-compose-20261109
cp -a "$BK/compose.yml" /opt/cliproxyapi/compose.yml
docker compose -f /opt/cliproxyapi/compose.yml up -d
# bind-mount 世界恢复：CLIProxyAPI-patched 仍在原位（未删除），doctor 门禁
# （source=bind_mount 报告项 + hash 对台账）保持绿色。
```

## 6. 边界

- `/opt/cliproxyapi/CLIProxyAPI-patched` 降级为只读留档，不再被容器引用；删除与否
  由后续磁盘决策（非本事务）。
- 派生镜像仅存于 bwg 本地（无私有 registry 基建，不为此新建）；换机需按本表重放构建。
- 上游发布 v8.0.62+ 时：updater 会具名拒绝（`FORK_IMAGE_REBUILD_REQUIRED`），采纳
  走重建事务（重放 §2 构建 + 更新台账 + compose tag + 本回执新条目）。
- 本台账不改变账号侧任何姿态。
