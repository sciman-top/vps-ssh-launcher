# BWG CPA 状态页的模型别名与凭据脱敏

2026-11-26 22:52（北京时间），使用 `scripts/cpa_status_deploy.ps1 -Profile bwg -Deploy` 部署状态页代码并重启 `cpa-status.service`。

- 来源校验通过。部署前 SHA-256 为 `5e3283e74d3a76bc09d1d2648bc46b83e0d6e8d79b12c27539f6e46776fc5a29`，部署后为 `0ee386c01c90bb19d7ecaa1105b20da6c74ddd75a90fd4ee84da493594247a39`。
- `STATUS_JSON_OK` 和 `STATUS_HTML_OK`。WorkBuddy `-wb` 归属 WorkBuddy CN，裸名归属 BigModel Coding Plan。凭据文件名中的邮箱通过短哈希脱敏。部署审计返回 `sync=MATCH`，状态服务运行中。
- 对比部署前后的 CPA 配置哈希、容器启动时间和凭据计数。活跃凭据为 Antigravity 2、Codex 1、WorkBuddy CN 8，另有 WorkBuddy INTL 停用凭据 7 个。BigModel `glm-5.3-flash` 非 OAuth 缓存探针两次均读到 4,096 / 4,114 个缓存 token，返回 `HEALTH_OK`。
- 回滚时从 `/opt/cpa-status/cpa_status.py.bak.20261126145218` 恢复 `cpa_status.py`，重启 `cpa-status.service` 后复核服务、JSON 和源文件哈希。

## 追加验收

2026-11-26 23:12 前后（北京时间），状态服务 JSON、HTML 和部署哈希复验通过。CPA strict doctor 退出 0，17 项投影匹配，`admission-integrity=OK`，`semantic-policy=OK`，`MODEL_IDS_UNKNOWN=none`。本地状态页、冷却、配额和 SSE 相关验证为 `141 passed`。该批测试包含工作区中并行修改的 admission 候选，生产 admission 以 doctor 匹配的已提交版本为准。

受控实战经 BWG admission 回环入口执行。每个模型只发一个算术流式请求，发前检查目录、车道忙闲和冷却。验收要求可见文本为 `42`、SSE `[DONE]` 和 `finish_reason=stop`。

| 请求模型 | 结果 | 响应模型 | 首段文本 | 完成耗时 |
| --- | --- | --- | ---: | ---: |
| `glm-5.4` | 通过 | `glm-5.4` | 886 ms | 915 ms |
| `glm-5.4-wb` | 通过 | `glm-5.4` | 2,863 ms | 3,170 ms |
| `gemini-4-hydrogen-xhigh` | 通过 | `gemini-4-hydrogen` | 2,190 ms | 2,340 ms |
| `glm-5.3-flash-start` | 失败 | 无回报 | 无可见文本 | 8,009 ms |

Start Plan 对应 admission `status=502 protocol_result=upstream_error successful=false capacity=false`。CPA 同时记录到额度提示，sidecar 仍为 `active`。本次仅发一条请求。Gemini 的回报模型是基础名称，语义样例覆盖简单算术和流终止。

BigModel `glm-5.3-flash` 的 Responses 协议缓存探针从 `0 / 4113` 提升到 `4096 / 4113`（99.59%），返回 `HEALTH_OK`。同时段半小时自然流量中，`gpt-6.1-terra` 5/5 完成，`gpt-6.2-astra` 278/278 完成。网关回环耗时和该自然样本用于当前车道验收，桌面端 E2E 比较仍缺少匹配的样本。
