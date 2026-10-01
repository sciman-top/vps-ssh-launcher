# Cockpit Tools v1.3.64 Direct API 持久修复

本说明针对官方刚升级到的 Cockpit Tools `v1.3.64`。该版本官方源码已在
`writeProviderGatewayResponsesStream` 的 SSE 空行事件边界执行 `Flush()`，所以本地补丁
不重复修改该生产函数，也不再携带 v1.3.63 的重复 Flush 回归改动；本补丁只修复升级后
会被官方生成器覆盖的配置。

仓库中保留的 v1.3.63 patch/runbook 仅用于历史审计，不属于当前活动修复路径；构建、投影
和回滚均只使用本 v1.3.64 版本匹配补丁。

## v1.3.64 官方基线与检查结果

| 项目 | 值 |
|---|---|
| 上游 tag | `v1.3.64` |
| 上游 tag commit | `4434c32e7bb02f33246941eb5179b0252a93481a` |
| 统一补丁 | `outputs/cockpit-tools-v1.3.64-persistent-fix.patch` |
| 补丁 SHA-256 | `D8203F2DC3FAE375532DA2B73023E7BF42519C9EFDA5BC1A88CA828FE95DF979` |
| 修复构建主程序 SHA-256 | `5CDAA7C32D85D3BBDDFB4734A85A44A1CF8F5891034AE927071B8034871393BE` |
| 修复构建 sidecar SHA-256 | `6A996BE7B8E4F53812BC93140FABBB63341F72F7D66D0F06029F7992BF7B7A23` |
| 当前已安装官方原版主程序 | `D15531C89248212F49A10BFCBACA4A33646FD5ED4C89F41AEA4967BD87FEB7DE` |
| 当前已安装官方原版 sidecar | `60F2E0BBAF3EEFCA7B28420873672A7DB8DE02C89B7D3F67BCFD03FF696D2A8A` |

本补丁保留的行为：

- 生成器把 `request-retry` 固定为 `0`，避免同一请求在本机被额外放大重试；
- provider gateway 重建时保留配置源中的 `maxAccountConcurrency` 和
  `accountConcurrencyWaitMs`；
- sidecar 生成配置固定 `stream-bootstrap-buffering=false`；
- sidecar 入口再次强制关闭 bootstrap buffering，防止旧配置或外部配置绕过生成器；
- 保留生成器 concurrency 与 bootstrap 回归测试；SSE Flush 以官方 v1.3.64 源码和官方测试为准。

此版本补丁共涉及 6 个源码文件。官方已有的 SSE flush 实现应以版本匹配源码为准，升级后
重新检查 `writeProviderGatewayResponsesStream`；若后续上游重构或回退该行为，应重新审查
并生成针对新 tag 的补丁，不能把本补丁直接套到其他版本。

## 上游生成器修复状态（2026-10-01）

Provider Gateway 并发字段丢失已单独提交到官方仓库：Issue
[#2676](https://github.com/jlcodes99/cockpit-tools/issues/2676)，PR
[#2677](https://github.com/jlcodes99/cockpit-tools/pull/2677)。PR 基于官方当前 `main`
（`3a097821d9ef6a116907f816139b1c813cc581e1`），只改生成器字段复制和回归测试；与 SSE
Flush PR [#2659](https://github.com/jlcodes99/cockpit-tools/pull/2659) 分开，未改变重试默认值。

提交时 PR 为 `OPEN` 且 `MERGEABLE`，GitHub Actions 的 Build Matrix 与 CodeQL 因 fork PR
审批门槛处于 `action_required`，尚无 CI job 结果。本地 Rust 测试目标已编译；测试二进制
启动遇到 Windows `STATUS_ENTRYPOINT_NOT_FOUND (0xc0000139)`，因此运行断言仍待上游 CI。

在 PR 合并并进入目标 release 前，v1.3.64 本地持久补丁继续保留这两项字段复制和对应回归
测试。升级到包含上游修复的源码后，先比对新 tag；确认已包含后，从本地 patch 与生成器
回归测试中移除重复改动，再验证补丁对新版本的适用性。

## 应用与构建

必须在干净且版本匹配的 `v1.3.64` checkout 上先检查补丁：

```powershell
git apply --check --binary D:\CODE\vps-ssh-launcher\outputs\cockpit-tools-v1.3.64-persistent-fix.patch
git apply --binary D:\CODE\vps-ssh-launcher\outputs\cockpit-tools-v1.3.64-persistent-fix.patch
```

按上游项目的 Go、Rust、前端和 Tauri release 构建顺序验证，并记录生成的主程序与
sidecar SHA-256。当前 Go 测试、sidecar 构建、前端 typecheck/build 和 Tauri release
构建均通过；Rust lib 测试已完成编译，但本机执行测试二进制时返回
`STATUS_ENTRYPOINT_NOT_FOUND (0xc0000139)`，属于运行库环境缺口，不是测试断言失败。
补丁可逆检查：

```powershell
git apply --reverse --check --binary D:\CODE\vps-ssh-launcher\outputs\cockpit-tools-v1.3.64-persistent-fix.patch
```

版本升级后先查新 tag 是否已经包含这些修复。只对缺失项生成新版本补丁；若上游已合入，
删除对应本地补丁改动并使用官方实现。tag/源码不匹配、测试失败或生成器结构无法识别时
应停止，不把旧二进制回灌到新版。

## 投影与回滚

换入前备份安装目录中的 `cockpit-tools.exe`、`cockpit-cliproxy.exe` 和 Direct API 配置源。
替换、启动并恢复后 fresh read：

- `server.json` 版本与运行 PID；
- 10909 provider gateway sidecar 的命令行、监听 PID 和二进制 SHA-256；
- 活动生成配置 `request-retry=0`、`codex.stream-bootstrap-buffering=false`；
- 活动 manifest 的 `maxAccountConcurrency=1`、`accountConcurrencyWaitMs=120000`，并确认
  client key 在 manifest 与 config 一致（只比较脱敏摘要，不打印 key）。

每个目标模型最多做一次顺序、低频的真实 `stream=true` 回放；检查 HTTP 状态、流终止事件、
delta 数和 socket reads。遇到 429/503 即保留原始结果并停止该路由，不重复重试或压测。
受控回放通过不等于用户 Desktop 自然会话的 `live_accepted`。

回滚只恢复本次备份的二进制与配置源，然后重新核对活动 PID、端口、profile 和有效配置。
Git 回滚不能替代运行态恢复。
