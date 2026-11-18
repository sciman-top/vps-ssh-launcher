# CPA 质量探针与远端取证修复

- 范围：修复 `cpa_error_dump_forensics.py --ssh-profile` 的入口，明确
  `quality-eval` 中希腊字母英文名称的要求，并保留所有现有渠道。
- 依据：远端保留的 OpenCode 单宿请求响应为 HTTP 403、`permission_error`、
  `insufficient_quota`。模型随后从 `/models` 消失。该结果需要服务商侧确认额度。
- 受控验收：使用既有 probe lock 和非 OAuth API key 路由，`glm-5.3-flash`
  与 `gpt-6.2-astra` 各通过四项质量用例。Astra 使用本次明确后的题目。
- 本地门禁：`scripts/run_gates.ps1 -RunParallel` 通过，pytest 1736 passed、
  31 skipped、823 subtests passed。远端取证入口专项测试通过。
- BWG 投影：在维护锁下备份并保留原文件 inode 写入
  `/opt/cliproxyapi/cpa-health.py`，脚本预编译与 `readiness` 通过。
  回滚文件为 `/opt/cliproxyapi/cpa-health-backup.nlYhKR/cpa-health.py`。
- 限制：全矩阵质量验收仍受 OpenCode 的额度信号阻挡，未据此推断账号封禁或
  其他模型不可用。
