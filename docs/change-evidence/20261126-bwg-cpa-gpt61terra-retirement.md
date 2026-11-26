# 2026-11-26 BWG CPA 退役 gpt-6.1-terra

按用户指令，提交 `540dec1` 将 `gpt-6.1-terra` 从 ChatGPT OAuth 路由、admission 名单及客户端上下文覆盖中移除，保留 OAuth 和 Codex API-key 两份排除项。

- 独立检出的 Full 门禁通过，1,895 项测试及 899 个子测试通过，30 项跳过。`CPA_PROJECTION_AUDIT=PASS`。
- BWG 标准 `-Apply` 返回 `GUARDRAILS_APPLIED` 和 `READY_STATUS=200`。回滚备份为 `/root/cpa-guardrails-backup-20261126T154916.323634272Z`，恢复流程见 [CPA 手工回滚](../runbooks/cpa-manual-rollback.md)。
- 严格 doctor 返回 `DOCTOR_CONTRACT_OK`，投影哈希均匹配，`MODEL_IDS_UNKNOWN=none`、`catalog_oauth_missing=none`。经 admission 读取 `/v1/models` 确认 Terra 已撤下，`gpt-6-luna`、`gpt-6.1-sol` 和 `gpt-6.2-sol` 仍在目录中。
- ChatGPT lane 空闲时，经 `127.0.0.1:8318` 发送一条 `/v1/responses` 请求，返回 HTTP 400 和未知模型或路由错误。`TERRA_NEGATIVE_ACCEPTANCE=PASS`，请求次数为 1。
- 部署前后客户端和供应商 API-key 配置一致。Doctor 回读的 Codex、Antigravity、Claude OAuth 文件数分别为 1、2、0，WorkBuddy CN 和 INTL 计数分别为 8、7，前后相同。
