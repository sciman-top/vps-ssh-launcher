# RFC：把 fork 二进制收进派生子镜像（2026-11-09）

**状态**：已实施（2026-11-09，用户授权后落地；回执
[20261109-bwg-cpa-fork-image-convergence.md](../change-evidence/20261109-bwg-cpa-fork-image-convergence.md)）。
当前生产 = 派生镜像 `cli-proxy-api-fork:v8.0.61-gemini-raw`，bind-mount 已移除，
`CLIProxyAPI-patched` 保留为只读留档。实施偏差（source 门禁降为报告项、tag+hash
门禁替代 digest 钉定、canary 扩展为 fork 时代全拒）见回执 §4。

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
2. 是否允许把派生子镜像也推到私有 registry（否则它只存在于 bwg 本地，换机需重建）。
