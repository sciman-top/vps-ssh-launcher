# BWG CPA zhipu developer-role 1214 修复（2026-11-03）

## 症状

ChatGPT desktop 经 CPA client key 调 `glm-5.3-flash` 报
`stream disconnected before completion: ... Upstream status=400`（重连 2/2
耗尽后硬错）。journal 21:54:38-52Z 三笔
`lane=zhipu-coding-plan model=glm-5.3-flash status=400 protocol_result=upstream_error`
（waited_ms=0、capacity=false、无 retry_after=上游拒绝非排队）。

## 根因链（转储逐层实证）

1. desktop codex wire 请求携带 **6 个 `role:"developer"` items**（28 assistant
   / 7 user / 6 custom_tool_call）。
2. CLIProxyAPI v8.0.51 `00b76771`（#7174）起 responses→chat-completions
   translation **原样保留 developer 角色**；本仓 11-02 升级 v8.0.50→51/52 后
   zhipu 车道首次桌面真实流量即触发。
3. 外发 zhipu payload 含 6 个 `developer`（另有 99 assistant/193 tool/7 user，
   `model` 字段正确为 `glm-5.3-flash`）→ zhipu 返回
   `{"error":{"code":"1214","message":"角色信息不正确"}}`（角色值非法，非模型
   名/参数问题）。

## 上游裁定与机制核实

- 同款回归已有案：#7202（DeepSeek 422 变体）被维护者 **closed not-planned**——
  新上游原生支持 developer，不能无条件改写；钦定杠杆=配置级
  `requests.payload.override` 旋钮（`'messages.#(role=="developer")#.role':
  "system"`，支持模型通配/别名与 `from-protocol` 限定）。
- 旋钮在在役 v8.0.52 存在且调用链闭合（源码核实）：`config_types.go`
  PayloadModelRule.FromProtocol → `helps/payload_helpers.go`
  `payloadFromProtocolMatches`/`normalizePayloadFromProtocol`（responses 归一）
  → `openai_compat_executor.go:170/404` `ApplyPayloadConfigWithRequestDoc`
  （fromProtocol=responses 传入）。

## 修复（单一真源 manifest，双侧派生）

- `cpa_provider_routes.json` 新增 `zhipu_developer_role_override`
  （`from_protocol: "responses"`，models = glm-5.3 / glm-5.3-flash，沿用既有
  显式模型名风格）。
- `cpa_policy.py` 从该键派生 override 规则入 `EXPECTED_PAYLOAD_TOOL_RULES`；
  spec 校验失败降级为"期望无规则"，规则在役的 live config 即 exact-match 红
  （fail closed）。
- `cpa-guardrail-apply.sh` 同键同形状派生（malformed 即 REFUSE），写入
  `requests.payload.override`；两 derivation 漂移由 semantic_policy exact-match
  门拦截。
- 生效规则（live config 投影回读一致）：
  `{"models":[{"name":"glm-5.3","from-protocol":"responses"},...],
  "params":{"messages.#(role==\"developer\")#.role":"system"}}`——仅改
  responses-wire 来源的 glm 请求；chat-wire 与其他车道零影响。

## 部署与验收

- `f2cad18`（full gates 全绿：1091 passed + 573 subtests + bandit/ruff/
  format/mypy clean；首轮 lint:format 打回两处格式化后复跑）→
  `GUARDRAILS_APPLIED`（事务内含 `docker restart cli-proxy-api`，备份 keep_8）。
- 受控实战验收（桌面形状：/v1/responses + instructions + developer item）：
  **200 / 5.2s / 93 events / PONG / completed**（修复前同形状 <1s 内 400）；
  journal `status=200 successful=true`。
- strict doctor：`DOCTOR_CONTRACT_OK`、`semantic-policy=OK`、投影
  （auto-update.sh/cpa-health.py/cpa_policy.py/cpa_provider_routes.json/
  cpa-admission.py）全 MATCH。

## 边界与备注

- 面积：仅 `glm-*` × responses-wire；zhipu 车道 chat-wire 客户端（无
  developer 角色）与其他车道不受影响。
- 隐性期：11-02 v8.0.51 上线起该车道桌面路径已坏，今日首次真实桌面 glm 流量
  才暴露——上游回归类缺陷的暴露依赖车道真实流量，doctor 无法预知（无桌面
  形状探针面），本轮后由 exact-match 门钉死配置面。
- 台账随附验收脚本 `outputs/cpa-zhipu-devrole-acceptance.sh`。
