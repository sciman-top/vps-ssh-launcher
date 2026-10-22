# 2026-10-22 BWG WorkBuddy 流截断守卫（0.9.64-local + truncation guard）

**范围**: 仅 `bwg`。未连接或改动 `zz`；未轮换 OAuth 凭据、未改 IP/UA/并发/重试策略；CPA 镜像保持 `v8.0.38` 不变；未发送 OAuth 生成探针。

## 问题与根因

客户端（Codex Desktop，`/v1/responses` 流式）偶发：

```
stream disconnected before completion: upstream stream closed before a terminal event
(last event: response.output_item.done)
```

`cpa-admission` 对同一请求记录 `status=200 successful=false`（例：2026-10-22T12:44:02Z `waited_ms=7814`，`request_id=560ef73443d14717847ada986234ff41`），说明 admission 已判定失败，但客户端只拿到一个语义含糊的「终端事件缺失」。

根因在插件侧，不在宿主：

- `pumpUpstreamStream`（`plugins/workbuddy/stream.go`）只在 `!seenPayload` 和 `scanner.Err() != nil` 时判失败；**200 流已发出内容后干净 EOF**（无 `finish_reason`、无 `[DONE]`）会走到最后一行 `publishUsage(..., false, ...)`，被当作成功关闭。
- `pumpStreamFrames` / `aggregateSSEWithCollector` / `aggregateCompletion` 三条路径都只有 `seenPayload` 空流守卫，没有终止见证（`sawFinish` / `sawTerminator`）。
- 上游插件 0.9.64 仍缺此守卫：同仓 `plugins/qoder` 已在上游 issue/PR #48 修过同一缺陷（`truncatedStreamError()` + `sawFinish`/`sawTerminator`），workbuddy 未移植。

后果分两种，后者更危险：

1. 纯 reasoning 帧后被截断 → CPA 只补出 `response.output_item.done`，没有 `response.completed`，客户端报「终端事件缺失」。
2. 已有正文后被截断 → CPA 的 chat→responses 转换在收流末尾合成 `[DONE]`，**可能把半截答案标记为 completed**，客户端静默接受残缺回答。

## 补丁

- 新增 `scripts/patches/workbuddy-v0.8.78-truncation-guard.patch`（MIT，源自上游 v0.8.78 / workbuddy 0.9.64）。
- `stream.go`：三条路径统一记录 `sawFinish`（任一 choice 的非空 `finish_reason`）与 `sawTerminator`（`[DONE]`）；`seenPayload && !sawFinish && !sawTerminator && scanner.Err()==nil` 时以 `truncated_stream` 失败关闭（pump 走 in-band 错误帧，collector/fold 返回错误）。
- `policy.go`：`truncatedStreamError()` 措辞含 `truncated_stream:` 与 `unexpected EOF`，落在 CPA `isConnectionLifecycleMessage` 的传输生命周期族，**不冷却健康凭据**。
- `stream_termination_test.go`：108 行、5 个用例（终止见证识别、错误措辞/分类、三条路径的截断拒绝与 `finish_reason`/`[DONE]` 放行）。
- 语义边界：`finish_reason=length` 与 `tool_calls` 仍视为上游主动结束，不改写 `max_tokens`。

## 隔离构建

- 目录 `/root/cpa-workbuddy-truncation-20261022`（v0.8.78 源码 + selected-account + account-failover + 本补丁）；`.pristine` 为打补丁前副本。
- 补丁自检：在 pristine 树上 `patch -p1 --dry-run` 与 `git apply --recount --check` 均通过，应用后与目标树逐文件一致。
- 构建脚本：`outputs/build-workbuddy-0964-trunc-20261022.sh` / `.ps1`（`golang:1.26-bookworm`、`--cpus=1 --memory=768m`、无生产挂载）。
- `gofmt -l` 空、`go vet ./...` 通过、`go test -count=1 ./...` `ok 8.536s`。
- 产物 `workbuddy.so` SHA-256 `86441f5d931a6ddeecd3875efcf115f53d7cc29fb1827cbaefcd067a761f15aa`，12,019,728 字节。

## 换装事务

- 脚本：`outputs/install-workbuddy-0964-trunc-20261022.sh` / `.ps1`。
- 前置：`HEALTH_OK`；`chatgpt-oauth` 冷却中（容量窗，与本 lane 无关）、`workbuddy-oauth` OPEN 且 `inflight=0/pending=0`。
- 排空：admission `/run/cpa-admission/requests.lock` 排它 `flock -w 60`，取锁后复验 `traffic_guard` 指向同一锁；`PRE_HOLDS` 为空。
- `LDD_OK`；备份 `/opt/cliproxyapi/backups/20261022T133038.434808589Z-plugin-0964-to-0964trunc/workbuddy.so`（旧 `8d531f30…`）。
- 原子 `install` + `mv` 替换，`docker compose restart cli-proxy-api`，注册断言 `version=0.9.64`，`readiness=HEALTH_OK`，`POST_HOLDS` 无新增。

## 换装后验证

- 插件文件 SHA-256 `86441f5d…`；容器 `StartedAt=2026-10-22T13:30:38Z`，`RestartCount=0`。
- `plugin registered plugin_id=workbuddy ... version=0.9.64`；`cpa-health.py readiness` = `HEALTH_OK`；strict doctor = `DOCTOR_CONTRACT_OK`。
- 真实 INTL 流量：13:30Z 起 16 次 `workbuddy-oauth/deepseek-v4.1-flash` 全部 `status=200 successful=true`；`docker logs` 中无 `truncated_stream`，说明守卫未对正常流误报。
- 未做：受控制造截断（不主动诱发上游截断）、并发提升、凭据变更。

## 观测与风险边界

- 该补丁**只修失败语义与静默成功**，不修复上游答案完整性；被上游截断的回合仍然失败，只是客户端会收到明确的 `truncated_stream` 原因，而不是含糊的终端事件缺失。
- 若上游存在「正常结束但既无 `finish_reason` 也无 `[DONE]`」的合法流，本守卫会误报。当前证据（正常流 16/16 成功、历史 379 次成功均带终止）不支持该形态；观察窗内一旦出现异常高的 `truncated_stream` 比例，按回滚段恢复。
- 真正提升完整性需要上游修复或插件层重试/续传，属高风险，不在本次范围。

## 回滚

- 恢复备份：`cp -a /opt/cliproxyapi/backups/20261022T133038.434808589Z-plugin-0964-to-0964trunc/workbuddy.so /opt/cliproxyapi/plugins/.workbuddy.so.rollback && chown --reference=/opt/cliproxyapi/plugins/workbuddy.so /opt/cliproxyapi/plugins/.workbuddy.so.rollback && mv -f /opt/cliproxyapi/plugins/.workbuddy.so.rollback /opt/cliproxyapi/plugins/workbuddy.so`，再 `cd /opt/cliproxyapi && docker compose restart cli-proxy-api`。
- 回滚后应复验 `workbuddy.so` 回到 `8d531f30…`、注册版本 `0.9.64`、`readiness=HEALTH_OK`。Git 回滚不能替代远端恢复。

## 上游跟进建议

- **已提 PR（2026-10-22）**：<https://github.com/mmqz/cpa-multi-plugins/pull/50> —— 标题 `fix(workbuddy): stop reporting a truncated stream as a complete answer`；分支 `sciman-top:fix/workbuddy-stream-truncation-guard`；3 文件 +188/-3，OPEN 且 `MERGEABLE`。合并后本仓本地补丁按补丁头 "Retire when upstream ships" 退役。
- 提 issue：workbuddy 200 流在 `response.output_item.done` 后 EOF、无 `finish_reason`/`[DONE]` 被当成功；附 `request_id=560ef73443d14717847ada986234ff41`、`waited_ms=7814`、#48 作为 qoder 先例。
- 或直接 PR：把 #48 的 `sawFinish`/`sawTerminator` 守卫移植到 workbuddy 三条路径 + `stream_termination_test.go`；本仓补丁即候选 diff。
- 另一层：CLIProxyAPI 侧 reasoning-only 流在 EOF 时缺 `response.completed`（上游 PR #6784 正在处理），与插件守卫互补。
