# registry 决策定案：不做（2026-11-09）

**决策**：**不建私有 registry。** 用户当次明确指示："那就按你的建议不做私有 registry"。
**性质**：仓库文档定案 + 只读路径验证。**未创建任何 registry、未开端口、未加凭据。**

## 1. 定案内容

| 项 | 决定 |
|---|---|
| 私有 registry | **不做** |
| 镜像归属 | 只存在于 bwg 本地（`cli-proxy-api-fork:v8.0.61-gemini-raw`） |
| 换机/重建 | 按 `outputs/build-fork-image-20261109.ps1` 重放构建 |
| 跨机分发（如将来需要） | `docker save | zstd` + sha256 清单，不新建服务 |

## 2. 决定性理由

1. **现状为空**：无 registry 容器、无镜像、5000/5001/4433 无监听、
   `registry.local` 不解析、`/root/.docker/config.json` 不存在。
2. **registry 不解决真问题**：registry 的 tag 同样可变。本轮修掉的缺陷是
   "回滚集不携带运行身份"，靠 `record_image_ids` + `image-id` 记录解决；
   即使有 registry，备份 compose 仍写可变 tag，`prune` 仍保护错对象。
3. **代价不对等**：新增常驻服务 + 凭据轮换 + 存储配额/GC + 端口纳入
   `firewall_baseline.ps1` 的精确 allowlist（受控面变更），而 bwg 仅 2 GB 内存、
   磁盘已 77%。收益仅为"另一台机器能拉"，当前没有第二台跑 CPA 的机器。

## 3. 代价验证：重建与分发路径确实可跑

"不做 registry"只有在重建路径真的可用时才是安全的。演练结果（只读 + 往返后丢弃）：

```
=== A. 重建输入全部可解析
base_image_present=yes      (eceasy/cli-proxy-api:v8.0.61@sha256:fc714b43…)
fork_artifact_present=yes   (/root/cpa-v8061-gemini-raw-build/…)
plugin_artifact_present=yes (/root/cpa-multi-plugins-v0.8.97-staging/workbuddy.so)

=== B. 镜像自带 provenance
base=sha256:fc714b43…
fork=31f2f9d9…

=== C. tar 往返（文档化的跨机路径）
save_ok=yes size_mib=100
manifest_sha=5381037260e3007d2e9edcdf3fa77610d3ff667b06c4a0f7cc0fddee23203c9c
load_ok=yes

=== D. 运行态未受影响
image=sha256:786669dd…
binary=31f2f9d9…（= ledger）
```

注意 `size_mib=100`：`docker save` 只打包该镜像独有的层，与 400 MB 的解压体积
不同，跨机传输成本比预期低。tar 已删除，未占用磁盘。

## 4. 遗留的唯一可验证预测

下一次 `cliproxyapi-update.timer`（2026-11-10 04:00 UTC）应让
`/opt/cliproxyapi/auto-update.log` 出现：
`PRUNE scope=backups` 与 `PRUNE scope=backup_family`。
若缺失，说明 systemd 环境有前提未满足，需再查一次（见
[timer 取证](20261109-bwg-timer-evidence-and-branch-drill.md) §4）。

## 5. 重新评估的条件

仅当以下**全部**成立时再议（同 [registry 决策台账](20261109-bwg-registry-decision-and-rollback-identity.md) §5）：

1. 出现第二台长期运行 CPA 的机器；
2. 与 bwg 不同机房，tar 传不动；
3. 用户提供 registry 主机与 TLS 证书，并同意把端口纳入
   `scripts/firewall_baseline.ps1` 的 allowlist（需独立评审）。

**无论届时采用哪种形态，消费端都必须 `@sha256:` 钉定**，否则可变 tag 的缺陷
会原样重现。

## 6. 边界

- 本轮零生产写入：只读查询 + 隔离 namespace 内的 save/load 往返。
- 不改变任何镜像、容器、配置、凭据或账号姿态。
