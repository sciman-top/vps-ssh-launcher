# BWG Antigravity 429 流式错误提示修复（2026-11-22）

## 现象与归因

- 北京时间 00:12–00:52，BWG 记录 `gemini-3.8-flash-high` 六次上游 HTTP 429。留存错误含 `RESOURCE_EXHAUSTED` 和 `check quota`，未见可用的等待时间。
- 用户确认仍有额度。本机 Antigravity 选择 `Gemini 3.8 Flash High`，无项目短请求返回 `OK`。该对照未验证桌面账号与 BWG 当次选中的凭据一致。
- 北京时间 01:13:39，另一条 `claude-opus-5-5-high` 请求收到结构化 `QUOTA_EXHAUSTED` 和约 17505 秒等待时间。BWG 接入层依现行策略将冷却应用到整个 `antigravity-oauth` 通道。部署前后的持久化截止时间均为北京时间 06:05:24。

## 修复与验证

- `5f6e329` 更新 Antigravity HTTP 429 的终止 SSE，显示上游来源以及有效的等待秒数。接入层收不到等待提示时明确提示未知。沿用已有 `retry_after` 字段和冷却逻辑，错误正文不透传。
- `pytest -q tests/test_cpa_admission.py -k 'antigravity_upstream_429_explains or stream_failure_message_names'`，4 项通过。新增两项覆盖有无 `Retry-After`，并验证旧版本会给出泛化断流提示。
- `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1 -Profile Full -RunParallel`，1830 项通过、30 项跳过、896 个子测试通过。
- 本地 `-AuditProjection` 通过。批量 `-Apply` 在写入前按 pin 转换保护拒绝执行，改用 `outputs/deploy-admission-antigravity-429-20261122.ps1`。预演通过，首次切换后的辅助断言误查外层方法并触发自动回滚，`ROLLBACK_VERIFIED`。修正断言后以同一候选版本排空、备份并切换，收到 `DEPLOY_OK`，接入服务启动且 `/healthz` 正常。
- 部署后 strict doctor 通过，`drift=cpa-admission.py MATCH`、`admission-integrity=OK`、`runtime_binary_hash=MATCH`。
- 备份位于 `/opt/cliproxyapi/backups/20261121T174051.848428508Z-admission-antigravity-429-20261122`。回滚须在 maintenance 和 request-drain 锁下成组恢复该备份的 admission、request guard 与 pin，再重启 admission 并复验完整性和就绪。保留风险状态文件。
- 本轮没有通过 BWG 发送 Antigravity 生成请求。官方客户端短请求与本地 SSE 回放均已完成，BWG 上 Flash High 的真实生成恢复仍需等待有效冷却结束后确认。
