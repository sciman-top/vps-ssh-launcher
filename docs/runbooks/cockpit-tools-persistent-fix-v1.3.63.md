# Cockpit Tools v1.3.63 Direct API 持久修复

本 runbook 固化 Direct API 的两个本地根因修复，并把它们放进同一个版本匹配的
源码补丁：

- provider gateway 的 Responses SSE writer 在 SSE 空行边界和流结束主动 `Flush()`；
- 生成器固定 `request-retry=0`，保留 `maxAccountConcurrency` /
  `accountConcurrencyWaitMs`，并生成 `stream-bootstrap-buffering=false`；
- sidecar 入口再次把 `stream-bootstrap-buffering` 设为 `false`，覆盖旧配置或外部配置。

## 当前版本资产

| 项目 | 值 |
|---|---|
| Cockpit 版本 | `v1.3.63` |
| 源码基线 | `ee816002b771b766af23b575b6f59b586d547acc` |
| 统一补丁 | `outputs/cockpit-tools-v1.3.63-persistent-fix.patch` |
| 补丁 SHA-256 | `F0879F2E9867287465ADA934993B806E7815E38D2E2354D0C55EEF59296F46FC` |
| 当前 `cockpit-tools.exe` | `BD4AC63BA9977C5C2EFFD540DB1C145803B4D7D67408E0AAD9066D41BFB29E75` |
| 当前 `cockpit-cliproxy.exe` | `EA48519CBFD2489236767E35E590759539B4B58985B178352C29EFC081AC91B2` |

补丁包含以下 8 个源文件的改动：

`sidecars/cockpit-cliproxy/main.go`、`sidecars/cockpit-cliproxy/provider_gateway.go`、
`sidecars/cockpit-cliproxy/provider_gateway_stream_identity_test.go`、
`src-tauri/src/modules/codex_local_access_foundation.rs`、
`src-tauri/src/modules/codex_local_access_provider_gateway.rs`、
`src-tauri/src/modules/codex_local_access_sidecar_config.rs`、
`src-tauri/src/modules/codex_local_access_tests_sidecar_gateway.rs`、
`src-tauri/src/modules/codex_local_access_tests_takeover.rs`。

## 重建规则

当前安装不再依赖单独的手工二进制字节补丁；二进制只是这个统一源码补丁的构建产物。
在同一个版本上重建时，先对干净的版本匹配 checkout 做：

```powershell
git apply --check --binary D:\CODE\vps-ssh-launcher\outputs\cockpit-tools-v1.3.63-persistent-fix.patch
git apply --binary D:\CODE\vps-ssh-launcher\outputs\cockpit-tools-v1.3.63-persistent-fix.patch
```

然后按仓库原有构建顺序执行 Go/Rust/前端门禁，并记录两个构建产物的 SHA-256。补丁
反向校验应通过：

```powershell
git apply --reverse --check --binary D:\CODE\vps-ssh-launcher\outputs\cockpit-tools-v1.3.63-persistent-fix.patch
```

如果 Cockpit 升级到新版本，不能把这个 v1.3.63 补丁或旧二进制回灌到新版本。应从
新 tag 重新审查锚点、生成同版本补丁、构建并做同样的隔离验证；若上游已经合并修复，
应删除相应本地补丁并直接采用上游版本。版本不匹配时应失败关闭，不得猜测套用。

## 投影与验收边界

安装替换前保留当前二进制和配置备份。重启或恢复实例后重新读取：

- 运行中的 `cockpit-cliproxy.exe` 命令行必须指向活动 provider profile；
- `10909` 必须由该新 sidecar 监听，`config.json` 中 `request-retry=0`、
  `codex.stream-bootstrap-buffering=false`；
- 活动 `manifest.json` 与 `config.json` 的 client key 必须一致；
- `maxAccountConcurrency=1`、`accountConcurrencyWaitMs=120000` 必须在活动 manifest 中保留。

随后每个目标模型只做一次低频 `stream=true` 请求，检查 HTTP 状态、`response.completed`、
delta 数量和 socket read 数量。受控回放通过只证明本地链路已加载并能工作；用户在
ChatGPT Desktop 中的自然会话仍需单独确认，不能由模型目录或一次 HTTP 200 代替。

回滚只恢复本次备份中的两个二进制和配置源，然后重新读取 PID、监听端口、活动 profile
和上述有效配置；不要用 Git 回滚替代运行态恢复。
