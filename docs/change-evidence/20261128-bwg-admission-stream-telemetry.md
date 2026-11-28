# BWG admission 对话清洗与流式遥测发布（2026-11-28）

2026-11-28 20:49（北京时间），BWG 通过独立事务切换到提交 `6d886b076013d3ceea8ec8233e25e9a723622824` 的 admission。完整性 pin 从 `0863327399deda8d2937700eb2d0d71b295ad24d2d0273888f57d8ab6a0902c0` 更新为 `1dfe8243171d1739691673d16143500eb39d24a2f9453170ff5423141718aa47`。

事务在 ChatGPT 模型冷却自然到期后才执行。在维护锁下排空在途请求，检查冷却与停流状态，备份后替换 admission 源码、生成的请求守卫与 pin。只重启 `cpa-admission.service`，重启前后停流状态比对通过。事务返回 `DEPLOY_OK`。

| 验收层 | 证据 |
|---|---|
| 仓库 | `scripts/run_gates.ps1 -RunParallel` 通过，1913 个测试通过、30 个跳过、903 个子测试通过。`-AuditProjection` 返回 `CPA_PROJECTION_AUDIT=PASS`。提交已推送至 `origin/main`。 |
| 生产投影与加载 | 严格检查返回 `DOCTOR_CONTRACT_OK`，17 项投影全部 `MATCH`，服务完整性与健康检查通过。系统服务主进程、admission 子进程、启动回执及 guard 与候选代际匹配。 |
| 隔离回放 | BWG 候选回放区分 Gemini 思考片段和可见正文、缺失与显式为零的缓存计数，并保留被助手回复隔开的「继续」。事务夹具验证正常切换、新增停流时回滚、冷却期间拒绝重启。切换后以服务身份重放生产源码，Gemini 首字和相邻重试断言通过。 |
| 受控实战 | 经 8318 `/v1/responses` 单发 `glm-5.3-flash`，零重试。HTTP 200，响应模型匹配，返回预期文本并到达 `response.completed`。探针可见首字 1.106 秒、全程 1.126 秒。同请求 ID（前缀 `b3c4e6a77cee`）的日志显示 `zhipu-coding-plan`、`protocol_result=completed`、`sse_terminal_seen=true`、输入 23 / 缓存 0 / 输出 21 token。 |
| 自然流量 | 截至 20:52（北京时间），同一次服务启动内排除上述受控请求后共 43 条自然请求，全部协议完成。`gpt-6.2-astra` 直通 9 条，`deepseek-v4.1-flash` WorkBuddy 34 条。Gemini、ChatGPT OAuth、ZCode 和 Claude 在这个短窗口没有自然完成样本。 |

直通和 WorkBuddy 的 43 条均回报了输入和缓存计数。两组短窗缓存命中率分别为 89.2% 和 99.1%，网关总时长 p50 分别为 14.709 秒和 4.345 秒。直通只有 1 条可见首字样本，WorkBuddy 没有可见首字样本。此窗口可验证新版的自然请求和计量链路，流量结构不足以做发布前后性能比较、判断模型质量或推断长期账号风险。

备份位于 `/opt/cliproxyapi/backups/20261128T124915.578555304Z-admission-telemetry-20261128`，含 `cpa-admission.py`、`cpa_request_guard.py` 和 `cpa-admission.sha256`。事务内失败会用这三份备份恢复。若后续需人工回滚，先核对在役 pin 仍为本次候选，再持维护锁和请求排空锁，按原权属与模式恢复三件套，重启 admission 并复验完整性、健康和停流状态。再对齐仓库提交并运行 strict doctor。
