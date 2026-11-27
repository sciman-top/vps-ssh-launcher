# BWG CPA 深度审查修复投影与受控验收证据（2026-11-27）

**性质**：真实远端写入（受审原子 admission 换代 + `cpa_bwg_guardrails.ps1 -Apply` 配置投影）+ 线级模拟与受控实战验收（redaction-first）。时间除特别注明外均为 UTC。
**授权**：用户指令（2026-11-27）"授权按你的推荐/建议连续自动修复和优化，直至完成所有任务"。
**提交**：`96636da`（`fix(cpa): repair live health audit, non-stream cost parsing, and responses/SDK identity sanitization`）。

## 1. 修复范围与根因

| 编号 | 模块 | 问题根因 | 修复内容 |
| --- | --- | --- | --- |
| 1 | `scripts/cpa_admission_risk_audit.py`、`README.md` | `206e1ed` 卸载 `workbuddy-ai-provider`（INTL）并移除 `workbuddy-intl-oauth` 后，`audit_live_health()` 仍要求 6 条泳道，导致对 BWG 实时 `/healthz` 必报 `live-health-lane-set` `FAIL`；`README.md` 速查表亦残留旧模型与 INTL 行 | 对齐 `cpa-admission.json` 五条在役泳道（`chatgpt-oauth`、`zhipu-coding-plan`、`zcode-start-plan`、`antigravity-oauth`、`workbuddy-oauth`），补反向单测断言已卸载泳道出现时报错 |
| 2 | `scripts/remote/cpa-admission.py` | 非流式响应分支调用 `usage_cost(bytes(probe))`，而 `usage_cost()` 首行要求 `isinstance(usage, dict)`，导致非 SSE JSON 响应的 `usage_cost_usd` 恒为 `None` | 新增 `usage_cost_from_body(payload: bytes)` 复用 `_event_usage_cost(data)` 解析嵌套费用字段，覆盖损坏/截断 JSON 安全回退 |
| 3 | `scripts/remote/cpa-admission.py` | `neutralize_agent_identity_text()` 在 `/v1/responses` 仅遍历 `list` 型 `input`，遗漏 OpenAI Responses 顶层 `instructions`（由 CPA 翻译为 `system` 消息）及字符串型 `input` | 按泳道角色集合覆盖 `/v1/responses` 的 `instructions`（`system` 角色，作用于 `workbuddy-oauth` 与 `zcode-start-plan`；`antigravity-oauth` 仍由 CPA 第一层处理 `systemInstruction`）与字符串型 `input`（`user` 角色） |
| 4 | `scripts/remote/cpa-admission.py`、`cpa_policy.py`、`cpa-guardrail-apply.sh` | Claude Code Desktop 在身份句末尾追加 `, running within the Claude Agent SDK.`，且 Agent SDK 使用独立句 `You are a Claude agent, built on Anthropic's Claude Agent SDK.`（`#7904`），原规则仅匹配到 `for Claude` 会留下残片 | 在 Python `AGENT_IDENTITY_SENTENCES` 与 Go RE2 `antigravity.sensitive-patterns-replace` 首条规则同步吞掉可选 SDK 尾缀与独立 Agent SDK 身份句，WSL Go `regexp.MustCompile` 实测通过 |

## 2. 远端投影事务与备份

1. **原子 Admission 换代（`outputs/deploy-admission-cost-responses-sdk-20261127.ps1`）**：
   - DryRun：`STAGED_RULE_OK cost_from_body_ok responses_top_ok claude_sdk_ok` → `ADMISSION_INTEGRITY_OK` → `DRY_RUN_OK`（零变更）。
   - Apply：在 `/run/vps-ssh-launcher-maintenance.lock` 与 `/run/cpa-admission/requests.lock` 排他空闲锁下原子轮换 `cpa-admission.py`、`cpa_request_guard.py` 与 `/etc/vps-ssh-launcher/cpa-admission.sha256`：
     `3a9df1d3d9c205c943d769bbdce75e08f5f1e7d854c0f39bdee5c1bc1a0c10ea` → `b99cfd2ff24c251a17eb5839f63af438991f692fadc6db5bf3a2f5091ff0a4a6`。
   - 备份路径：`/opt/cliproxyapi/backups/20261127T055801.474413081Z-admission-cost-responses-sdk-20261127`。
2. **Guardrails 与 Policy 投影（`scripts/cpa_bwg_guardrails.ps1 -Profile bwg -Apply`）**：
   - 17 项远端目标全部 `PROJECTION_HASH_VERIFIED`；INTL 7 枚凭据保持 `disabled=7 of=7`；`/opt/cliproxyapi/config.yaml` 热更新 `sensitive-patterns-replace`（SHA-256 `a6ca2f21...`），容器日志零 `invalid regex` / `rule dropped`。
   - 备份路径：`/root/cpa-guardrails-backup-20261127T055809.298613535Z`。

## 3. 分层验证回执

| 层级 | 验证方式 | 结果 |
| --- | --- | --- |
| `repo_verified` | WSL Go RE2 编译与替换实测 + `scripts/run_gates.ps1 -Profile Full -RunParallel` | `GO_RE2_VERIFIED=OK`；`CATALOG_CHECK=PASS`、bandit、ruff、format、mypy（165 files, 0 issues）、pytest `1898 passed, 30 skipped, 903 subtests passed` |
| `filesystem_projected` | `cpa_bwg_guardrails.ps1 -Profile bwg -AuditProjection` | `CPA_PROJECTION_AUDIT=PASS evidence=filesystem_projection`；17 项远端文件哈希与 `HEAD` 完全一致 |
| `host_loaded` | `cpa_bwg_guardrails.ps1 -Profile bwg` + 实时 `/healthz` `cpa_admission_risk_audit.py --skip-posture` | `DOCTOR_CONTRACT_OK`；`-- fail=0 warn=3 -- verdict: PASS with warnings`（原 `live-health-lane-set` `FAIL` 已消除） |
| 宿主隔离模拟 | BWG 回环临时加载线上 `/opt/cliproxyapi/cpa-admission.py` 对接本地 Mock Upstream | `SIMULATION_E2E_OK=True`：顶层 `instructions` 清洗为 `"You are a coding assistant. Help."`、字符串 `input` 清洗且保留 `C:\x\.zcode\skills\a.md`、非流式 JSON `usage.cost=0.00042` 正确写出 `usage_cost_usd=0.000420` |
| `live_accepted` | 受控最小单发正向探针（`max_output_tokens=16`，零重试，遵守探测纪律） | ① `workbuddy-oauth/glm-5.3-flash-wb`（`stream=false`，含 `#7904` Desktop `instructions` + 字符串 `input`）→ `200 completed`，journal 记 `kind=agent_identity_neutralized system_parts=1 user_parts=1`、`usage_in=39 usage_out=3`；<br>② `antigravity-oauth/gemini-4-hydrogen-xhigh`（`stream=true`，含 Agent SDK `instructions` + Desktop SDK `input`）→ `200 completed`（`sse_terminal_seen=true`），journal 记 `kind=antigravity_identity_user_text user_parts=1`、`usage_in=58 usage_out=37` |

## 4. 边界与回滚

- **未重复探测通道**：`chatgpt-oauth`（Plus 5h 滚动配额敏感，本轮未改动其路由或请求头，复用同日 `v8.1.7` 换芯与 48h 自然流量证据）与 `zcode-start-plan`（验收时点 `06:00Z` / `14:00 CST` 不在夜间/周末免费窗口，仅核验宿主服务、桥接监听与配置契约）。
- **远端回滚入口**：
  1. 恢复 `/opt/cliproxyapi/backups/20261127T055801.474413081Z-admission-cost-responses-sdk-20261127` 中的 `cpa-admission.py`、`cpa_request_guard.py`、`cpa-admission.sha256` 并重启 `cpa-admission.service`。
  2. 恢复 `/root/cpa-guardrails-backup-20261127T055809.298613535Z` 中的 `/opt/cliproxyapi/config.yaml` 与 `cpa_policy.py`（或本地 `git revert 96636da` 后重跑原子换代与 `-Apply`），再复跑 `cpa_bwg_guardrails.ps1 -Profile bwg`。
