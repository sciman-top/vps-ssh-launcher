# CPA 质量探针与远端取证修复

- 范围：修复 `cpa_error_dump_forensics.py --ssh-profile` 的入口，明确
  `quality-eval` 中希腊字母英文名称的要求，并保留所有现有渠道。
- 依据修正：远端保留的 OpenCode 单宿请求响应为 HTTP 403，
  `message` 为 `error code: 1010`，CPA 包装的 `code` 为
  `insufficient_quota`。最初将该包装码判作额度不足的结论有误。
  请求的 User-Agent 为 `Python-urllib/3.12`，模型随后从 `/models` 消失。
- 受控验收：使用既有 probe lock 和非 OAuth API key 路由，`glm-5.3-flash`
  与 `gpt-6.2-astra` 各通过四项质量用例。Astra 使用本次明确后的题目。
- 本地门禁：`scripts/run_gates.ps1 -RunParallel` 通过，pytest 1736 passed、
  31 skipped、823 subtests passed。远端取证入口专项测试通过。
- BWG 投影：在维护锁下备份并保留原文件 inode 写入
  `/opt/cliproxyapi/cpa-health.py`，脚本预编译与 `readiness` 通过。
  回滚文件为 `/opt/cliproxyapi/cpa-health-backup.nlYhKR/cpa-health.py`。
- 后续复验：模型自然恢复到 `/models` 后，使用 curl 经本机 CPA 发送单次
  `deepseek-v4.1-flash-oc` 生成请求，HTTP 200、`finish_reason=stop`，
  输出与预期 `OK` 一致。Python 健康探针触发的 1010 仍需避免重复触发。
- 限制：完整自动质量矩阵尚未通过，单次 curl 成功不代表长期稳定。
