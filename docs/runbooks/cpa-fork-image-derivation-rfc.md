# RFC：把 fork 二进制收进派生子镜像（2026-11-09）

**状态**：已实施（2026-11-09，用户授权后落地；回执
[20261109-bwg-cpa-fork-image-convergence.md](../change-evidence/20261109-bwg-cpa-fork-image-convergence.md)）。
当前生产 = 派生镜像 `cli-proxy-api-fork:v8.0.79-gemini-raw`（2026-11-19 换芯，回执
[20261119-bwg-cpa-v8079-swap.md](../change-evidence/20261119-bwg-cpa-v8079-swap.md)），
bind-mount 已移除，`CLIProxyAPI-patched` 保留为只读留档。实施偏差（source 门禁降为
报告项、tag+hash 门禁替代 digest 钉定、canary 扩展为 fork 时代全拒）见回执 §4。
换代链：v8.0.75（2026-11-16）→ v8.0.77（2026-11-18/19，台账 `3555bfb`）→ v8.0.78
（2026-11-19，image_id `7bb4b400…`，binary `bf1e8fb5…`）→ v8.0.79
（2026-11-19，image_id `1d6eafc5…`，binary `ec1ad20c…`）。

**决策定案（2026-11-09，用户明确）**：§7 的"是否推送私有 registry"一项，
用户已答复 **不做**。镜像只存在于 bwg 本地；换机或重建按
[build-fork-image-20261109.ps1](../../outputs/build-fork-image-20261109.ps1)
重放构建（基线按 index digest 钉定、产物二进制与 OCI 标签双断言）。
若将来确需跨机分发，优先用 `docker save | zstd` + sha256 清单（文件哈希即身份，
无常驻服务、无凭据、不占监听端口），而不是新建 registry。
启用 registry 的三个前置条件见
[registry 决策台账](../change-evidence/20261109-bwg-registry-decision-and-rollback-identity.md) §5。

## 2026-11-16 更新：二进制重建已参数化

不再需要一次性脚本。一次上游换芯现在是三步，每步都可重放：

```powershell
# 1) 用目标 tag 重建二进制（脚本自带 tag→commit 核对、tarball 哈希、移植产物校验、
#    go vet + translator 测试、清理构建目录）
 pwsh -NoProfile -File .\scripts\cpa_fork_rebuild.ps1 `
   -UpstreamTag v8.0.75 -UpstreamCommit c19787b25262b5e6e46a9e8da62f4333e7e478bc `
   -PortSha256 <sha of outputs/cpa-gemini-raw-<tag>.tar.gz> `
   -StageDir /root/cpa-v8075-gemini-raw-build `
   -BuildImage golang:1.26.10-bookworm@sha256:<index-digest> -Apply

# 2) 派生镜像（base 按 index digest 钉定，构建后读回标签）
 pwsh -NoProfile -File .\outputs\build-fork-image-20261109.ps1 -UpstreamTag v8.0.75 `
   -BaseDigest <digest> -BinarySha256 <binary sha> `
   -ForkBinaryPath /root/cpa-v8075-gemini-raw-build/CLIProxyAPI-8.0.75-gemini-raw -Apply

# 3) 换芯事务（drain + 备份 + 回滚 + 后置断言），脚本形如
#    outputs/cpa-v8075-swap-20261116.sh
```

**移植（patch → port）**：原补丁写于 v8.0.61，对新 tag 往往不再逐字可应用。不要用模糊匹配
让它自己决定落点：把补丁对目标 tag 做一次 **3 路合并**，把结果收成
`outputs/cpa-gemini-raw-<tag>.tar.gz`（**只许包含 translator 包**，脚本会拒绝越界条目），
并在回执里记下移植后文件哈希。原始补丁继续作为移植的源保留。

**两条硬边界**：

`-Apply` 必须显式传入带 digest 的 `-BuildImage`。只给 tag 的 dry-run 会输出
本地 `BUILD_IMAGE_ID`；生产重建还会从 `RepoDigests` 回读该引用，tag 被重指向
或本地镜像不匹配时拒绝编译。该门禁只绑定工具链输入，不证明源码、补丁、镜像
制成品或宿主业务已验收。

1. `scripts/remote/fork-binary.sha256` 必须与二进制在**同一提交**更新——doctor 用它判断
   "容器里跑的到底是不是台账说的那个"，漏更新会以 `runtime_binary_hash=MISMATCH` 阻断。
2. 构建目录**不要**放 `/root`：2026-11-16 实测该机 overlayfs 上，连续两步编译会对尚未出现的
   文件报 `no such file or directory`；构建脚本改用 `/var/tmp/cpa-fork-build-<tag>` 并在成功后删除。

## 2026-11-23 更新：构建车道内存护栏（2 GB 主机硬边界）

bwg 主机只有 **2 GB RAM + 1 GB swap**，常态已用 ~630 MB（MemAvailable ~1.4 GB），
而本仓 `cpa_fork_rebuild.ps1` 曾给构建容器固定 `--memory=1900m`——**容器上限高于
主机可供量**，深编译阶段溢出容器 memcg 后由内核**全局** OOM 收编：2026-11-22 16:56
实测被杀的是构建自己的 `compile` 进程（945 MB RSS），但下一个受害者可能是 nginx/CPA。
现护栏（脚本内，dry-run 不经过）：

- 构建容器上限 = `min(1900m, MemAvailable − 256m)`（`/proc/meminfo` 读数，已计入
  可回收页缓存；两个 docker run 同用一值）；
- 折算后 `< 1500m` 直接拒跑（`BUILD_MEM_REFUSED`，exit 20）——实测编译+链接需求
  ≥1.4 GB anon，强行缩限只会在容器内 OOM 失败，拒跑给出
  "本地构建或先腾主机内存"的指引；
- 爆炸半径语义：cap 低于主机可供量后，内存不足只会杀构建容器自身（干净失败），
  不再可能误杀伤主进程。

**纪律**：fork 构建尽量在本地做（v8.1.1 代已实测"本地 git archive + overlay +
vet/test 预验证"可行），bwg 上构建仅在主机空闲窗进行；`outputs/` 里的逐代
overlay 构建脚本属历史记录，下代 fork 若再生成，必须继承本护栏（动态 cap + 1500m
下限），不得回退固定上限。

**运行时对照（同一内存面）**：CPA 容器 `mem_limit=512MB`（当前水位 ~33 MB）是
把潜在尖峰转成"容器 OOM 重启（杀流）"而非主机 OOM 的有意设计。上游
[#7946](https://github.com/router-for-me/CLIProxyAPI/issues/7946)（open）报 v8.1.1
大上下文流式内存尖峰 ~2.3 GB：**并发大上下文（320K 试用形状）+ 容器无解释重启
时首查此因**；上游修复落地并随 fork 换代收编前，不要为"防重启"上调 512MB 限制
（那会把爆炸半径还给主机）。

## 2026-11-26 更新：v8.1.4 代的可复用做法

当前生产 = `cli-proxy-api-fork:v8.1.4-gemini-raw-daily-cap-authiso-sigiso`（binary
`424fb393…`，upstream 锚 `38fe9295`）。脚本：
`outputs/build-fork-image-v814-20261126.ps1`、`cpa-v814-swap-transaction-20261126.ps1`、
`cpa-v814-rollback-transaction-20261126.ps1`、`accept-v814-postswap-20261126.ps1`。

- **三方合并不需要旧的 merge 仓**：WSL `/root/build/src` 就是现役二进制的源码树。
  用 `git archive` 导出两个纯净 tag（Windows 侧要加 `-c core.autocrlf=false`，否则
  导出成 CRLF），在一个新仓里按「纯净旧 tag → 生产树 → 纯净新 tag」提交后
  `git merge`，冲突只落在两边都改过的文件上。生产树里有三个文件是 CRLF，提交前
  归一化，否则 git 看到的是整文件差异。
- **全量测试三边对照**（合并树、纯净新 tag、生产树）能把上游抖动与自有回归分开。
- **先 `-Apply`、后换芯**：主机目录里出现清单未声明的模型时，`cpa-health.py`
  readiness 的目录门 fail-closed，换芯脚本的 `wait_readiness` 会失败并回滚。凡是
  同时声明新模型的换代，先把清单投影上去。换芯脚本因此改为事务内快照配置哈希。
- 插件升级可以并进换芯的同一次容器重建：插件按 rename 替换，回滚时连同 compose
  一起还原。WorkBuddy 插件是 `-buildmode=c-shared` 的 C ABI 库，不要求与宿主同
  工具链；换前用 scratch 容器（无网络、最小配置）确认新插件能被新二进制注册。

## 问题

现役 CPA 不是“镜像说了算”，而是 `<image tag> + /opt/cliproxyapi/CLIProxyAPI-patched`
的二元组：compose 用只读 bind mount 把本地 fork 构建盖在镜像内二进制之上。

这条设计带来四个已在 2026-11-08 实际发生的缺点：

1. **镜像 tag 说谎**。容器 `Config.Image` 显示 `v8.0.61`，进程实际跑的是
   `v8.0.59 + 补丁`。任何只看 `docker inspect` 的人（包括我自己第一轮）都会误判。
2. **每日 updater 会静默降级。** `auto-update.sh` 重建容器时，旧二进制继续生效，
   新映像里的修复（message_stop 连接复用、SSE keep-alive、SIWC 刷新）全部不生效，
   而更新日志与 doctor 都会报“已升级”。下一次成熟版本发布时就会踩到。
3. **每次发布版本都要手工重建。** 需先在 `/root` 起 1.9 GB 的 golang 镜像编译，
   再走收敛事务，两步都依赖人工判断。
4. **执行位只在运行时存在。** 备份里的同名可执行文件一旦在 tmpfs 上被还原就会
   `exec format error`（2026-11-08 已实测过一次 exit-126 崩溃循环）。

## 方案：派生镜像 + 显式来源声明

```dockerfile
FROM eceasy/cli-proxy-api:v8.0.61@sha256:fc714b43…
# 唯一差异：用本地 fork 构建替换镜像内二进制。补丁内容见
# outputs/build-cpa-v8061-gemini-raw-20261108.ps1 的 PATCHED_TRANSLATOR_SHA。
COPY --chmod=0755 CLIProxyAPI-fork /CLIProxyAPI/CLIProxyAPI
```

- 构建：在 bwg 上用现有 `golang:1.26.10-bookworm` + 已有的源码缓存目录，
  `docker build` 产出 `cli-proxy-api-fork:v8.0.61-gemini-raw`，再把它的
  **index digest** 钉进 compose。
- compose 去掉 `CLIProxyAPI-patched` 挂载；`/opt/cliproxyapi/CLIProxyAPI-patched`
  降级为“只读留档”，不再被容器引用。
- doctor 增加一条门禁：`runtime-binary-source=image`（compose 中不存在
  `CLIProxyAPI` 的 bind mount），并从容器内读二进制哈希与台账声明比对。
- updater 保持不变（它只改 image 行），但现在改的是**真正会生效**的那一行。

## 收益

- `docker inspect` / updater / doctor 三者对“跑的是什么”给出一致答案。
- updater 的自动升级重新有意义，不再需要人工重建。
- 少一个 75 MB 的可执行文件在`/opt` 上以 bind mount 暴露给容器。

## 风险与回滚

- **风险**：自定义镜像绕开了官方 digest 的可验证性。缓解：Dockerfile 首行钉官方
  index digest，构建后记录派生镜像 digest，并在台账里记录上游 tag、补丁 SHA、
  构建镜像版本；doctor 断言容器二进制哈希等于台账声明值。
- **风险**：下次上游发布时，派生镜像必须在同一事务里重建，否则 compose 指向旧
  派生 tag——这正是我们要用 doctor 门禁抓的情况（tag 与声明不一致即失败）。
- **回滚**：compose 恢复 bind-mount 行 + 恢复 `CLIProxyAPI-patched`，与今天的
  路径逐字节等价；备份目录保留全部历史。

## 需要用户确认的点

1. 是否接受“自定义镜像 = 自建供应链”的取舍（我认为值得，因为现状的静默降级
   风险更高）。
2. ~~是否允许把派生子镜像也推到私有 registry~~ → **已定案：不推**（2026-11-09，用户明确）。
   镜像只存在于 bwg 本地；换机按 `outputs/build-fork-image-20261109.ps1` 重放构建，
   跨机分发用 `docker save` + sha256 清单。
