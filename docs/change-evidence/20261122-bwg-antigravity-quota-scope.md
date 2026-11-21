# BWG Antigravity 额度组冷却（2026-11-22）

## 依据与行为

- 北京时间 01:13:39，BWG 上的 `claude-opus-5-5-high` 收到 `429 RESOURCE_EXHAUSTED`。正文包含 Google `ErrorInfo`，其 `domain=cloudcode-pa.googleapis.com`、`reason=QUOTA_EXHAUSTED`，并带对应模型和 `quotaResetDelay=17504.619296109s`。接入层原先写入全通道窗口，导致 `gemini-3.8-flash-high` 被一并拦截。原持久化截止为北京时间 06:05:24（`1795298724.431822`）。
- [Google Antigravity Plans](https://antigravity.google/docs/plans#baseline-quota) 说明模型基础额度和计划刷新周期。[CLIProxyAPI PR #6364](https://github.com/router-for-me/CLIProxyAPI/pull/6364) 记录 `retrieveUserQuotaSummary` 返回的 Gemini Models 和 Claude and GPT models 两组。该 PR 已关闭且未合并。本次代码使用上述上游观测对已识别的 Antigravity 模型分类。
- `3af4ba3` 仅对正文结构、请求模型和有效重试时间均吻合的 Antigravity HTTP 429 持久化额度组窗口。无法归组的 429 和现有账号级保护继续按通道处理。`/healthz` 的 `model_cooldowns` 展开受影响模型。

## 模拟、投影与受控验收

- 同一候选版本的 HTTP mock 回放覆盖 Opus 429 后 Flash 可用、同组 Sonnet 受保护、重启后组窗口保留、模型错配和普通限流维持全通道冷却，以及流式错误提示。Full 门禁通过，1,833 项通过、30 项跳过、896 个子测试通过。一次性 clone 使用真实 `.git` 目录，并将 PowerShell 模板和 TLS helper 转成 LF 以满足现有夹具的字节断言，Git diff 无额外内容变更。
- 本地 `-AuditProjection` 通过。BWG 预演和应用均核对旧 pin、配置、路由、bootstrap、同一条 Opus 留存错误、接入日志和当前状态。02:16 左右，在两把锁下排空、备份并将现存旧窗口迁至 `claude-gpt`。部署事务返回 `DEPLOY_OK`，新 pin 为 `9569639e7fb9bbcacee66f69f92a054aeb9d40cdb267de617a56a1963227c786`。
- strict doctor 通过，`drift=cpa-admission.py MATCH`、`drift=cpa_request_guard.py MATCH`、`admission-integrity=OK`、`runtime_binary_hash=MATCH`。进程重启后 `/healthz` 显示 Opus 和 Sonnet 冷却、通道冷却剩余 0，Flash 开放。真实 Flash 请求后持久化截止仍为 06:05:24，状态文件无写入错误。
- 一次 Opus loopback `/v1/responses` 返回 HTTP 429、`quota_group_cooldown` 和 `Retry-After: 13743`。对应 journal 只有本地拒绝，没有准入或上游结果。一次从 Codex 配置的公网入口发起的 Flash High 请求收到 HTTP 200、`pong` 与 `response.completed`，总耗时约 3.27 秒，零重试。相同请求 ID 在 BWG journal 中为 `capacity=false`、`successful=true`、`protocol_result=completed`。
- 同次 Flash 响应的 CPA 上游模型告警显示 `gemini-3.8-flash-control`，请求名是 `gemini-3.8-flash-high`。这次实测验收了公网别名可用、长冷却隔离及流结束，实际 Flash High 模型身份未通过验收。此次只发了一次 Flash 生成请求。

## 回滚

- 备份目录为 `/opt/cliproxyapi/backups/20261121T181602.449885767Z-admission-antigravity-quota-20261122`。部署事务持有 maintenance lock 和 request-drain lock，先备份 admission 源码、request guard、pin 和旧风险状态。事务失败时恢复该组备份，重启 `cpa-admission` 并校验完整性。
- 部署后若要降级，在相同两把锁下排空请求并备份当时的风险状态。若文件含 `quota_group_not_before`，将所有组截止与当前 `not_before` 取最大值写回 `not_before`，删除 `quota_group_not_before`，原样保留 `version` 和 `hold_reason`。使用同目录临时文件原子替换并保持原 owner 和 `0600` 权限。再成组恢复 admission 源码、request guard 和 pin，重启 `cpa-admission`，复验完整性与 `/healthz` 后释放锁。后续降级使用当时的状态，避免用部署前状态覆盖后来出现的保护信号。
