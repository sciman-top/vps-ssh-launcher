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

### 1. fork 补丁 `agy-effort`（`outputs/cpa-agy-claude-effort.patch`）

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

## 回滚入口

- 仓库：`git revert` 本提交；manifest 契约与 policy 校验均为新增，回滚后运行配置不变。
- fork：未换芯，无需回滚；构建产物与源码树位于 `/root/cpa-v8080-agy-effort-build/`，
  可按 `CURRENT_SHA=31f2f9d9…` 对照在役二进制。
