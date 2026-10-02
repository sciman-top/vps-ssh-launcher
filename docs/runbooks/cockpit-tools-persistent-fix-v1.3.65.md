# Cockpit Tools v1.3.65 Direct API 持久修复

本说明针对官方 `v1.3.65`(tag commit `0b6514b40880efdfd7752ebd5113c6811bafe721`)。
官方应用更新(2026-10-02 01:13 本地)替换了带本地修复的 sidecar 二进制,且官方
生成器在每次应用启动、每次 sidecar 拉起前都会重写活动 config/manifest,把
`request-retry=1`、`stream-bootstrap-buffering=true`(OAuth sidecar)与
`maxAccountConcurrency=0`(provider gateway sidecar)写回,完整回退 v1.3.64 时代的
本地闸门与退避修复。本补丁在 sidecar 二进制层重建修复并以入口钳制兜底生成器缺陷。

v1.3.63/v1.3.64 patch/runbook 保留用于历史审计;当前活动修复路径以本文为准。

## 相对官方 v1.3.65 的修改(7 个文件)

统一补丁:`outputs/cockpit-tools-v1.3.65-persistent-fix.patch`(789 行,含新文件,
`git add -N` 后导出)。源码树:`%TEMP%\cockpit-tools-v1.3.65-build-20261002`。

1. **Direct/fixed-provider 账号并发闸门**(自 v1.3.64 移植,官方仍缺失):
   `provider_gateway.go` 把绑定账户传入闸门,入场前 `admitDirectProviderAccount`,
   响应后 `recordDirectProviderBackoff` 缓存 429/503 的有效 Retry-After;
   `manifest_policy.go` tracker 增加 `providerBackoffs` 与同锁退避检查;
   `provider_gateway_concurrency.go` 为闸门与退避实现。
2. **入口钳制 `stream-bootstrap-buffering=false`**(自 v1.3.64 移植):
   `main.go` 加载 config 后强制关闭,防生成器写回 true 造成慢吐字回归。
3. **入口钳制 `request-retry=0`**(v1.3.65 新增):官方生成器每次启动写回 1,
   sidecar 入口强制归零,维持本机不放大重试契约。
4. **入口钳制 `maxAccountConcurrency<=0 -> 3`**(v1.3.65 新增,
   `clampMaxAccountConcurrency`):官方生成器构建 provider gateway collection 时
   丢失该字段复制(上游 [PR #2677](https://github.com/jlcodes99/cockpit-tools/pull/2677)
   未合并),manifest 被写 0 会静默禁用 Direct 闸门;零值桥接为本地契约值 3
   (`codex_local_access.json` 顶层 `maxAccountConcurrency`,改动真源后需同步
   `localDefaultMaxAccountConcurrency` 常量并重建)。

Rust 生成器侧的两行字段复制修复(PR #2677 内容)未在本版重建主程序;sidecar
入口钳制已覆盖其危害,主程序保持官方原版。

## 构建与验证(2026-10-02)

| 项目 | 值 |
|---|---|
| 安装产物(r2)SHA-256 | `C03966F524D8CF4491B30C3CDBBB2124B05F38F3A482B9691BFCDCAE363E5D95` |
| 大小 | 44,315,136 字节 |
| 工具链 | go1.26.3/windows/amd64,CGO_ENABLED=0,`-trimpath -ldflags '-s -w'` |
| go test . -count=1 | 全量 PASS(3.657s,含 3 个 clamp 单测) |
| go vet / gofmt(行归一化) | 通过 |
| 受控回放 | 10909 `/v1/responses` `glm-5.3`(zhipu lane,零 OAuth 配额):200 / completed / 正文 ok / 1.6s |

中间产物 r1(SHA `4041...8860`,无 maxConc 钳制)已被 r2 取代;两者差异仅
`clampMaxAccountConcurrency` 及其测试。

## 安装备份(安装目录 `C:\Users\sciman\AppData\Local\Cockpit Tools\`)

- `cockpit-cliproxy.exe.official-v135-20261002.bak`:官方 1.3.65 原版
  (SHA `076D5082B2875607E2AE7890381436C1FA04BC946A92CCCC36CAB22187BEA35B`),
  回滚目标。
- `cockpit-cliproxy.exe.v135-gate-r1-20261002.bak`:r1 中间版,审计用。

安装分为磁盘投影与运行加载。先记录当前 sidecar PID、启动时间、映像路径与
SHA,再 rename 旧 exe 为备份、copy 版本匹配的新 exe。运行中的进程仍使用旧
映像;此时只能报告 `filesystem_projected`,运行加载验收仍待完成。

需要加载新产物时,在用户已授权的维护窗口通过 Cockpit 的正式启停入口完成重载,
随后核对双 sidecar 的映像 SHA、启动时间、监听端口及受控响应行为。日常诊断与
验收不得执行 `taskkill /F /T` 或停止现有 Cockpit。重载后活动 config/manifest
文件值会被生成器再次写坏,属预期;运行态由入口钳制兜底,验收以映像与行为为准,
单独的磁盘 SHA、端口存活或 `/v1/models` 200 均不能证明补丁已加载并生效。

## Cockpit 应用升级后复查清单

每次官方版本更新(主程序版本号变化)后执行:

1. `Get-Item ...cockpit-tools.exe | % VersionInfo.ProductVersion` 确认新版本号;
2. sidecar exe SHA 是否仍等于本补丁产物;被官方更新替换则按本文重建
   (上游新 tag → `git apply` 旧 patch 或手工移植 → 测试 → 构建 → 安装);
3. 顺序单发一次非 OAuth 模型请求(如 `glm-5.3`)验证 200/completed;
4. 检查新版本是否包含上游 PR #2677 的并发字段传递修复,并实测正值配置能够
   保存、生成和加载,四路请求仍按账号上限排队;通过后才移除对应的
   `clampMaxAccountConcurrency`。该 PR 不修复重试或流式缓冲,`request-retry`
   与 buffering 钳制必须分别核对官方实现并通过对应行为验收后才可移除;
5. OAuth lane 行为验收(单发 luna)遵守配额窗口纪律,周限额触顶期间勿探。

日常重启(app 或 sidecar)不需要重建或重装;入口钳制保证运行态不受生成器
重写影响。文件值 drift(request-retry=1 / buffering=true / maxConc=0)不构成
告警,除非 sidecar 版本回退。

## 回滚

rename 当前 exe → 还原 `cockpit-cliproxy.exe.official-v135-20261002.bak` 为
`cockpit-cliproxy.exe`,先完成磁盘回滚。运行中的旧映像继续服务;只有在用户已
授权的维护窗口通过正式入口重载并核对进程映像后,运行回滚才完成。加载官方
备份后恢复其原有行为:无 Direct 闸门、无 Retry-After 等待、request-retry=1
放大与 buffering=true 慢吐字回归,按 v1.3.64 文档口径处理。Git 不参与本回滚
(产物不入库)。
