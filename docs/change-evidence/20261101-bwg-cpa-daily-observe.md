# BWG CPA 每日只读观察 — deploy 前基线（2026-11-01）

本文件记录 v8.0.49 升级前后可对比的只读趋势基线，由每日只读观察自动化增量维护。
证据层：`repo_verified`（`cpa_natural_traffic_audit.py` 24h admission journal 聚合）
+ `host_loaded`（strict doctor 只读）。仅聚合计数，无 prompt/请求 ID/客户端 key/凭据。

## 基线（2026-11-01 09:30 CST / 01:30 UTC，deploy 前，运行 v8.0.48）

| 指标 | 值 |
|---|---|
| 运行版本 | `v8.0.48@sha256:c198886a…` |
| 插件 | `workbuddy.so` `81f0ccf3…`，version `0.9.82` |
| 容器 | running，restarts=0 |
| lane-hold | chatgpt OPEN / workbuddy OPEN / antigravity OPEN；claude HELD(inert) |
| cooldown_state | none |
| 24h capacity 事件 | 16，全部 chatgpt-oauth（sol 4 / luna 8 / terra 2 / 5.6-luna 2） |
| 24h lane 拒绝 | rate_limited 10、operator_review 8、downstream_gone 6、stale_write_ahead 6、queue_timeout 4、busy 1 |

按 lane 聚合（24h）：

| lane/model | n | cache hit | header p50/p90 | waited p50/p90 |
|---|---|---|---|---|
| workbuddy/deepseek-v4.1-flash | 3189 | 0.99 | 3176 / 6264 ms | 底噪 / ~8772 ms |
| zhipu/glm-5.3-flash | 85 | 0.95 | 5683 / 7595 ms | ~0 |
| chatgpt/gpt-6-luna | ~1342 | ~0.97 | 1136 / 1869 ms | 2 / ~6489 ms |
| antigravity/gemini-3.8-flash-high | ~12 | ~0.89 | ~5226 / ~8869 ms | ~0 |
| passthrough/* | ~175 | n/a（单发） | 4517 / 8062 ms | ~0 |

## 解读

- 缓存复用接近上限（workbuddy 0.99、zhipu 0.95），主机制是 `session-affinity`。
- “吐字慢”尾部：chatgpt/workbuddy 是 admission 排队（单账号 + inflight/RPM 护栏的固有
  代价），antigravity/zhipu/passthrough 是上游 TTFT，均非本地缓冲。
- capacity 全部集中在单账号 chatgpt——这是配置的已知集中风险面，缓解已按模型隔离
  （model-level-cooling），进一步降低需加账号（用户决策）。

## 对比口径

deploy 到 v8.0.49 后，本表新增一行即可对比：Gemini/Antigravity 质量（预期更少
降智/回落）与 `selectGroupResponses` 性能（预期 antigravity 延迟下降）。
