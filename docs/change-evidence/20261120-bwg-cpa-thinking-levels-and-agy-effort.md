# BWG CPA 思考等级契约、agy-effort fork 构建与吐字/命中率复审（2026-11-20）

**目标**：围绕"多 OAuth 账号 + 多 API key 渠道的封号/限流/降智风险、吐字速度与缓存命中率"做一次
证据驱动的复审，只落地有实测依据的改动；`zz` 未触碰，OAuth 凭据未新增、未迁移、未重授权。

## 结论

- **缓存命中率不是瓶颈**：24h journal 聚合（仅 `successful=true`）中 antigravity 输入 token 命中
  93.7%（564 请求），chatgpt 车道 90–97%，workbuddy 98–99%；`/healthz` 1h 窗口同步显示
  `deepseek-v4.1-flash` 0.9846–0.9951。任何"改写 prompt 前缀提升命中率"的提案前提不成立。
- **排队不是瓶颈**：三条共享车道 `waited_ms` p50 = 3ms、p90 ≤ 9ms，24h 无 `queue_timeout`。
- **"吐字慢"的真正来源是思考时长**：antigravity 564 次请求里 552 次 `upstream_header_ms ≥ 3s`，
  其中只有 2 次输出 < 4KB —— 不是"大输入小输出"被浪费；chatgpt 车道 `first_text_ms` p50 3.5s、
  p90 39.4s，尾部是长思考回合。
- **发现并修复一个真实的降智面**：antigravity 车道经 codex（Responses）协议服务，模型能力默认
  low/medium/high，而目录发布 `*-xhigh` 模型；且共享思考校验在 antigravity 上看不到信封里的
  等级，`xhigh` 回合没有任何可执行约束。已用 fork 补丁（`agy-effort`）让共享校验读取信封等级并
  按其能力钳制，同时在 manifest/policy 层把等级声明变成可校验契约。
- **限流/封号面的既有控制复核通过**：7d 敏感信号与 `/healthz` 各车道 `hold_reason=null`、
  无冷却；`routing.retry.max-retry-credentials=3`、`request-retry=0` 未变；OAuth 车道未新增探测。

## 改动

### 1. fork 补丁 `agy-effort`（构建阶段物化；补丁文本本身不入库，SHA-256 见下）

- `internal/thinking/apply.go`：新增 `ExtractReasoningEffortConfigPrefix` /
  `ExtractReasoningEffortConfigPrefixDoc` / `reasoningEffortConfigPrefix`；`applyThinkingDocCore`
  在"按 provider / 按源格式都取不到等级"时读取 antigravity 信封等级并交给既有校验管线。
- `internal/runtime/executor/helps/model_capabilities.go`：`sourceWithEnvelopeReasoningEffort`
  在调用方未自报等级时把信封等级拷到源 body；`thinkingEnvelopeLevelApplies` 限定为
  openai-response/codex/openai/gemini 源格式，显式 body 等级与 `(level)` 后缀优先。
- 回归测试：`internal/thinking/apply_envelope_effort_test.go`（新增）、
  `internal/runtime/executor/helps/thinking_test.go`（端到端：Responses 请求 → antigravity 翻译 →
  `xhigh` 钳制为 `high`）。

构建（`bwg` 宿主，`golang:1.26.10-bookworm`，`--cpus=2 --memory=1900m`）：

```
PATCH_PAYLOAD_VERIFIED        sha256 4cf3ade4…
SOURCE_TARBALL_SHA=69dc7219756ef471f20974bb4f13fe1c7ca7470194267a849eeb6c229b1647f2
PRISTINE_SOURCE_VERIFIED      go.mod 23a6a3b5…  apply.go d95a5bef…  model_capabilities.go 2c29280d…
PATCHED_APPLY_SHA=92dda438…   PATCHED_CAP_SHA=5c5bc6e4…
go vet / go test ./internal/thinking ./internal/runtime/executor/helps  → ok
BUILT_SHA=f60ea67e04c3a6bd6785302926e2a2cc7022636c5b6ec0c0843b6c815617bc24
(在役 v8.0.79-gemini-raw 二进制 CURRENT_SHA=31f2f9d9… 仅作对照)
```

> **未上线**：该二进制停在 `/root/cpa-v8080-agy-effort-build/`，未换芯。换芯需要按
> `outputs/cpa-v8079-swap-transaction-20261119.ps1` 的 drain + lane-hold 差分模板另起事务。

### 2. 仓库侧契约

- `scripts/remote/cpa_provider_routes.json`：新增 `oauth_settings_contract.channels.antigravity
  .thinking-levels = [low, medium, high]`；`zhipu-plan` 两个模型声明
  `thinking-levels = [low, medium, high, xhigh]`。
- `scripts/remote/cpa_policy.py`：`EFFORT_LEVELS` 词汇表；`_route_manifest_issues` 校验契约
  （渠道必须被服务、等级必须合法、不得重复）；`_provider_thinking_levels` /
  `_expected_thinking_levels` 让运行配置的 `models[].thinking.levels` 与 manifest 对齐。
- `scripts/remote/cpa-guardrail-apply.sh`：`model_entry` 接受并投影 `thinking-levels` 为
  `thinking.levels`。
- 测试：`tests/test_cpa_policy_runtime.py::test_thinking_levels_contract_is_declared_and_enforced`、
  `tests/script_validation_support.py` / `tests/test_cpa_updater_apply.py` /
  `tests/test_cpa_guardrails_script.py` 的投影期望。

## 验证层级

- **`repo_verified`**：`scripts/run_gates.ps1 -RunParallel` → `CATALOG_CHECK=PASS`、`bandit`、
  `ruff check`、`ruff format --check`、`mypy`、`pytest` `1764 passed, 31 skipped, 833 subtests`；
  receipt `outputs/gates/receipts/test.json` status `PASS`。
- **fork `repo_verified`（远端容器内）**：见上；上游 `internal/api` 既有一个与本改动无关的
  失败（`TestModelsWithClientVersionReturnsCodexCatalog`，pristine 树同样失败）。
- **`host_loaded`（既有状态，未因本次改动变化）**：CPA `v8.0.79-gemini-raw` 在役，`/healthz` 五条
  车道 `status=ok`、`hold_reason=null`、无冷却。
- **`controlled_live`**：本次未发起任何 OAuth 生成请求（fork 未上线，manifest/policy 尚未投影）。

## 复审发现但未改动的项（有依据才动）

- **OAuth access 认证绕过面**：上游会话与 v8.0.79 源码中 `accessAuthMiddleware` 在认证管理器存在时
  要求有效 key；本机用 `Authorization: Bearer <provider 上游 key>` 对 `/v1/models` 返回 200（真实
  入站 key 同样 200），失败形态不是上游的 `401 {"error": ...}` JSON。结论：**当前无法判定是绕过**，
  需带上游版本复核认证路径；在判定前不据此改配置。
- **`prompt_cache_key` 覆写面**：claude 之外，codex 写出侧的 `FinalizePayloadDoc` 会改写为
  `Session-Id` 派生 UUID；当前 chatgpt/antigravity 命中率 90–97%，`cache_reset_probe` 归因里
  `rebind` 为 0，**未观测到可归因损失**，不启用 `awaiting_side_effect`，也不设策略键。
- **`session-affinity-rebind-on: quota`**：与上游 v8 注释相反——`quota` 在 429/403 时**重绑**到
  另一凭据，"any" 反而不重绑。现行配置意味着单账号 lane 无实际差异，多账号 lane 会把 429 回合
  推到别的账号（`max-retry-credentials=3` 下至多 3 个）。是否改为 `any` 属策略决策，留待用户裁决。
- **轻量标题/摘要调用**：24h 内 `route=other`（p50 1.5KB 响应）约占全部 200 响应的 88%，每次 3s
  量级；合并/停用这类调用是降低账号消耗最直接的杠杆，但属客户端行为，不属于网关默认改动。
- **Claude 车道**：`claude` OAuth 已退役（`claude-risk.json` 仍保留 `authentication_required` 墓碑），
  Antigravity 上的 Claude 模型走 provider `antigravity`，本补丁对两者都不改变认证/重绑语义。


## 2026-11-20 授权后的连续执行（用户明确要求模拟/受控实战验收并连续自动修复）

### 1. 换芯上线（`agy-effort`）

- 派生镜像：`outputs/build-fork-image-20261109.ps1 -UpstreamTag v8.0.79 -BaseDigest sha256:ce02dd2b…
  -BinarySha256 f60ea67e… -ForkBinaryPath /root/cpa-v8080-agy-effort-build/CLIProxyAPI-v8079-agy-effort -Apply`
  → `BUILT_BINARY_SHA_OK`、`LABELS_OK`（base/fork 标签一致），`image_id=sha256:d8f6aad7…`。
- drain 事务：`outputs/cpa-v8080-agy-effort-swap-transaction-20261120.ps1`（先 `-DryRun` 预演）→
  `PREFLIGHT_OK`、`DRAIN_OK`、`LANE_HEALTH_OK idle_checked=True`、`IMAGE_ID_READBACK_OK`、
  `START_LOG_CLEAN`（插件 0.21.27 注册）、`RUNTIME_BINARY_OK sha=f60ea67e…`、`READINESS_OK`、
  `AUTH_BYTES_UNCHANGED`、`LANE_STATE_DELTA=none`、`SWAP_OK`。
  备份：`/opt/cliproxyapi/backups/20261119T234106.753473156Z-swap-v8079-gemini-raw-to-agy-effort`。
  上一二进制 `ec1ad20c…` 只在 `/root/cpa-v8079-gemini-raw-build/` 尚存（7 天保留窗内）；
  镜像层本身仍保留旧 tag 名，回滚需按备份中的 `new-image-id` 与构建记录重建。
- 投影：`-Apply` → `GUARDRAILS_APPLIED`；读回 `zhipu-plan` 两个模型均为
  `thinking.levels=[low,medium,high,xhigh]`；INTL 7 份凭据 `disabled=true` 回读一致。
- 严格 doctor：`DOCTOR_CONTRACT_OK`（`runtime_binary_hash=MATCH`、五条车道 `OPEN`、
  `credential_ban_signals_7d=0`、`model_substitution=OK`）。

### 2. 受控实战发现并修复的两个缺陷

**(a) Responses 回合被探测预算截断被误判为路由故障。** 首次 ONE_SHOT 在 `glm-5.3-flash` 上以
`response.incomplete` 结束（`incomplete_details.reason=max_output_tokens`，32 个输出 token 中 31 个是
推理），admission 记为 `protocol_result=upstream_error`。chat/completions 分支早有"预算过紧=合同失败"
注释，Responses 分支没有。修复：`cpa-health.py` 对 `incomplete + max_output_tokens` 输出
`error_class=probe_budget`（exit 20），测试 `test_cpa_health_generation_reports_incomplete_budget_as_probe_budget`。
不设预算时同一请求 `response.completed`、text=OK，证明不是上游或补丁问题。

**(b) updater 调度门把 systemd 默认超时读成了 20 分钟。** 服务未设 `TimeoutStartSec`，
`systemctl show … TimeoutStartUSec` 返回 `20min`（systemd 的默认值渲染），doctor 判
`updater-schedule-budget=FAIL`。修复：优先读 `DefaultTimeoutStartUSec` 的生效值（显式 per-unit 设置仍优先）。
修复后 doctor 输出 `updater-schedule-timeout-start=90s`、`updater-schedule-budget=OK`、
`updater-schedule=OK anchors=4`。测试在 `test_updater_schedule_split_is_projected_and_gated` 内补断言。

### 3. 受控实战验收（`controlled_live`）

- 公网随机路径（`https://127.0.0.1:8443/<cap>/v1/responses`，入站 key，不打印）→ admission → CPA
  `v8.0.79+agy-effort` → 上游：`status=200 header_ms=9 first_event_ms=1293 first_text_ms=1763
  terminal=response.completed text=OK total_ms=1822`。
- 修复前后同形状请求：`response.incomplete` ×2 → `response.completed` ×3（差额全部来自去掉过紧预算）。
- 受控请求全部落在**非 OAuth 车道**（`zhipu-coding-plan`）；本窗口 OAuth 车道零拒绝、零容量事件、
  零 hold；`workbuddy-oauth` 98 次请求 97 次 `completed`，唯一非 completed 为 `client_disconnect`
  （客户端主动断开，非上游失败）。

### 4. 仍未关闭 / 待用户裁决

- **fork 补丁的运行时效果尚未在真实 antigravity 请求上被观测**（见文首"未上线"注记已被本次换芯取代，
  但 thinking 等级钳制要有 `xhigh` 或显式 `reasoning.effort` 的 antigravity Claude 回合才会触发；
  doctor 的 `==model-substitution==` 继续作为观察面）。
- **OAuth 入站认证面**：之前记录的"provider key 得 200"仍需带上游版本复核；本次未改动认证配置。
- **`session-affinity-rebind-on: quota`** 的实际重绑语义（多账号 antigravity 池在 429 时会换号）仍属策略决策。

## 2026-11-20 第二轮：思考等级端到端取证、入站认证复核与 Claude 缓存观察

用户授权消耗额度后，以下结论均来自受控实测。

### 1. `xhigh` 钳制已在真实请求上工作（`controlled_live` + 离线取证）

- 公网随机路径单发 `claude-sonnet-5-5-high`，请求带 `reasoning.effort=xhigh`：
  `status=200 header_ms=9 first_text_ms=1079 terminal=response.completed text=OK`。
- 离线取证（模块内临时探针，已删除）：同一路径 `xhigh → thinkingLevel=high`、`medium → medium`，
  即共享思考校验确实读到了信封等级并按模型能力钳制，而不是把 `xhigh` 原样转给上游。
- 受控脚本 `outputs/probe-agy-thinking-20261120.py`（仓外存档，`outputs/` 不入库）记录该轮；
  暂开 `debug: true` 的日志窗口没有产生 thinking debug 行（`logs` 目录为空、日志走容器 stdout），
  因此改用离线探针与线上行为双证；该窗口已还原，`config.yaml` 字节回到开启前。

### 2. 入站认证面复核：已确认强制，先前"provider key 得 200"为代理绕过假象

此前记录的"用 provider 上游 key 访问 `/v1/models` 返回 200"无法解释为鉴权缺陷，原因已定位并实测：

- CPA 的入站鉴权对未命中任何 access 认证器的请求按**缺失凭据**处理；本机 `nginx` 的 8443 车道转发到
  `127.0.0.1:8318` 时，admission 再把 `Authorization` 原样送进 CPA。
- 当 `trusted-proxies` 未配置时，CPA 看到非回环来源便忽略 `Authorization`，落到"无凭据"分支。
- 实测（临时加 `trusted-proxies: ["127.0.0.1", "::1"]` 后立即还原）：`valid-key=200`、`no-key=401`、
  `wrong-key=401` —— `VERDICT auth_enforced`。也就是说对外入口**确实**按 key 鉴权，
  "provider key 也 200"是绕过 nginx、直连 `8317` 时宿主回环来源被信任造成的假象，不构成公网绕过。
- 未保留 `trusted-proxies` 改动：本轮目标是取证，不是改认证策略；是否长期保留由用户裁决（保留后 CPA 可
  区分真实客户端来源，但也会让本机直连的调用计入真实客户端，属于策略变更）。

### 3. Antigravity Claude 模型提示缓存观察（未定性，不改代码）

6 小时自然流量 + 受控复现的一致结果：

| 模型族 | 样本 | 输入 token | 命中 | 命中率 |
|---|---|---|---|---|
| `deepseek-v4.1-flash`（WorkBuddy） | 247 | 97,505,979 | 97,397,504 | 0.9989 |
| `gemini-4-neon-xhigh`（Antigravity Gemini） | 2 | 13,070 | 4,478 | 0.3426 |
| `claude-sonnet-5-5-high`（Antigravity Claude） | 14 | 149,123 | 0 | **0.0000** |
| `claude-opus-5-5-high`（Antigravity Claude） | 2 | 9,016 | 0 | **0.0000** |

受控复现：同一前缀两连发、续打回合，经公网、经 admission、绕过 admission 三种路径，Claude 模型全部
`cached=0`（`usage_in` 稳定，说明缓存值被真实解析而非缺失字段）。对照 Gemini 模型在同一 6.5k 前缀下
`cached=4478`，在同一 2.4k 小前缀下为 0（小前缀不命中属预期）。

**边界**：只证明了"在 Claude 模型上未观测到命中"，未证明根因。已排除：admission、payload 改写形状、
推理等级、凭据轮换（测量窗口内 7 份凭据 17 分钟内未刷新）。**未关闭的前置问题**：Antigravity 对 Claude
模型是否原生提供 prompt caching。在该问题上游无明确答复前，不改写请求前缀、不注入缓存标记、不动
`support-prompt-cache-key`——这些改动在"上游本不支持"的前提下只会增加请求差异，不产生收益。

### 4. `session-affinity-rebind-on` 语义复核（不改）

上游 v8 实际语义与早先笔记相反：`quota` 在 429/403 时**重绑**到另一凭据，`any` 反而不重绑瞬态错误。
本栈现值为 `quota`，意味着多账号 antigravity 池在限流时会把回合换到另一账号（受 `max-retry-credentials=3`
约束）。这是"可用性优先"的默认；改为 `any` 属"账号集中度优先"的策略选择，需要用户裁决，本轮不动。
## 回滚入口

- 仓库：`git revert` 本提交；manifest 契约与 policy 校验均为新增，回滚后运行配置不变。
- fork：未换芯，无需回滚；构建产物与源码树位于 `/root/cpa-v8080-agy-effort-build/`，
  可按 `CURRENT_SHA=31f2f9d9…` 对照在役二进制。
