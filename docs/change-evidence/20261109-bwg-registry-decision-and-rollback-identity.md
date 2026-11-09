# 派生镜像的 registry 决策与回滚集身份修复（2026-11-09）

**性质**：仓库代码修复 + 远端只读取证 + 决策记录
**用户授权**："授权按你的推荐/建议连续自动修复和优化，直至完成所有任务。包括推私有 registry 等剩余任务。"

## 1. 结论先行

**推荐：不建私有 registry**，改为让回滚集自带身份（本轮已实现）。理由见 §3。
若必须跨机分发，推荐"镜像 tar + sha256"而非维护一个 registry。§4 给出可立即执行的方案。

## 2. 本轮修掉的真实缺陷：回滚集记录的是可变 tag

### 2.1 缺陷

备份目录只 `cp -a compose.yml`，其中 image 行是
`cli-proxy-api-fork:v8.0.61-gemini-raw` —— **可变 tag**。

2026-11-09 实测（只读）：

```
backup=20261108T231257…-fork-image-relabel
  ref=cli-proxy-api-fork:v8.0.61-gemini-raw
  pinned=no
backup=20261108T231346…-fork-image-relabel
  ref=cli-proxy-api-fork:v8.0.61-gemini-raw
  pinned=no
tag_resolves_to = sha256:786669dd…   ← 当前镜像
换装前镜像 8c6ab375… = confirmed_gone
```

后果两条，都不是理论问题：

1. **`prune_images()` 保护错的对象**。它的设计是"保护运行镜像 + 每个保留备份引用的
   镜像"，但备份引用解析到今天 = 当前镜像，于是**换装前的镜像被当成无人引用**，
   这就是 `8c6ab375` 消失的原因。
2. **"从该备份恢复"会恢复成别的代码**。文档写的回滚指令 `cp compose.yml` +
   `docker compose up -d`，实际拉起的是 tag 当前指向的镜像，不是备份当时的镜像。
   这与 2026-11-08 的结论"镜像 tag 不等于运行代码"是同一个陷阱的第二形态。

### 2.2 修复

- `scripts/remote/cpa-auto-update.sh` 新增 `image_id_file()` / `record_image_ids()`：
  建备份时把**解析后的镜像 id** 写入 `<backup>/image-id`（`BACKUP_IMAGE_ID=…` 日志）。
- `prune_images()` 改为**优先读记录的 id**，读不到才回退解析 compose 引用；记录的
  镜像已被清理时输出 `PRUNE_WARN scope=images stage=recorded_id_missing`，不静默
  信任可变 tag。
- 时间顺序断言进测试：`record_image_ids` 必须发生在 `MUTATED=1` 之前。

### 2.3 测试

新增 `tests/test_cpa_updater_rollback_image.py`（3 项）：

- 备份目录确实产出 `image-id` 且内容为解析后的 id（真跑 bash，stub `docker`）；
- `prune_images` 中 `image_id_file` 的查询**早于** `compose_image_ref`；
- `record_image_ids` 在 `MUTATED=1` 之前执行。

写测试时踩的坑也记一笔：用 `bash -c` 传脚本在 Windows 上必须先走仓库的
`ScriptValidationMixin`（Git Bash 需要 `-l`，路径要转 MSYS 形式），否则所有符号
都报 `command not found`，看起来像脚本坏了。

## 3. 为什么推荐不建 registry

现况取证（只读）：

| 项 | 事实 |
|---|---|
| 现有 registry | 无：无容器、无镜像、5000/5001/4433 无监听、`registry.local` 不解析 |
| 凭据 | 无：`/root/.docker/config.json` 不存在；`target.json` 只有 host/port/user/password |
| 磁盘 | 40G 用 29G，余 9.1G；单个 fork 镜像 400 MB |
| 内存 | 2 GB |

建 registry 的代价与收益：

- **代价**：新增一个常驻服务（内网 5000 或 TLS 反代）、一套凭据与轮换、一份
  存储配额与 GC 策略、一个必须纳入 doctor/备份/防火墙的组件。bwg 只有 2 GB 内存，
  且现有 `firewall_baseline` 是精确端口 allowlist，开端口要改受控面。
- **收益**：仅"另一台机器能拉这个镜像"。而 §2 证明**真正的问题是回滚集不携带身份**，
  registry 不能解决它——即使有 registry，备份 compose 仍写着可变 tag，
  `prune` 仍保护错对象。
- **风险**：registry 里的 tag 同样可变。不钉 digest 的 registry 只是把同一个陷阱
  搬到另一个地址。

结论：**先修身份，再谈分发**。身份修复已完成且零新增组件。

## 4. 需要跨机分发时的推荐方案（不建 registry）

```bash
# 导出：镜像 + 清单 + 二进制哈希
docker save cli-proxy-api-fork:v8.0.61-gemini-raw \
  | zstd -19 -T2 > /root/cpa-fork-v8.0.61-gemini-raw.tar.zst
sha256sum /root/cpa-fork-v8.0.61-gemini-raw.tar.zst > /root/cpa-fork-v8.0.61-gemini-raw.tar.zst.sha256

# 导入（另一台机器）
docker load < cpa-fork-v8.0.61-gemini-raw.tar.zst
docker image inspect cli-proxy-api-fork:v8.0.61-gemini-raw \
  --format '{{index .Config.Labels "org.opencontainers.image.fork-binary.sha256"}}'
# 必须等于 scripts/remote/fork-binary.sha256
```

tar 附带全部 layer 与 OCI 标签，天然不可变（文件哈希即身份），不需要常驻服务、
不需要凭据、不占监听端口。导出物由 `sha256` 清单钉定，等价于 registry 的 digest
语义而少一整套运维面。

体积参考：镜像 400 MB，`zstd -19` 后通常显著更小；若只作灾备，可用
`--platform linux/amd64` 导出单架构以减半。

## 5. 若坚持自建 registry：启用条件与最小形态

仅在以下条件**全部**成立时才值得：

1. 出现第二台长期运行 CPA 的机器（当前只有 bwg 一台跑 CPA，zz 不跑）；
2. 该机与 bwg 不同机房，tar 传不动；
3. 用户提供 registry 主机与 TLS 证书，且同意把端口纳入
   `scripts/firewall_baseline.ps1` 的 allowlist（需改受控面，属独立评审）。

最小形态（届时执行，非现在）：

```bash
# 仅监听回环，经既有 nginx 反代暴露，避免新增裸端口
docker run -d --name registry --restart=unless-stopped \
  -p 127.0.0.1:5000:5000 \
  -v /opt/registry:/var/lib/registry \
  -e REGISTRY_STORAGE_DELETE_ENABLED=true \
  registry:2
# 推送必须钉 digest 消费，并在 doctor 增加"消费端用 digest 而非 tag"门禁
```

**无论哪种形态，消费端都必须 `@sha256:` 钉定**，否则 §2 的缺陷会原样重现。

## 6. 边界

- 本轮未创建任何 registry、未新增监听端口、未改防火墙、未触碰凭据。
- `record_image_ids` 只对新备份生效；历史备份仍只有可变 tag，已在
  `prune_images` 里以 `PRUNE_WARN` 显式暴露，不做静默兼容。
- 账号侧姿态零改动。
