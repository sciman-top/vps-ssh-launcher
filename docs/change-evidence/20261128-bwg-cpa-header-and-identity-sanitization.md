# BWG CPA 跨端请求头隔离与多车道身份/计费头清洗投影验收（2026-11-28）

**性质**：真实远端写入（受审原子 `cpa-admission` 换代 + `cpa_bwg_guardrails.ps1 -Apply` 配置投影）+ 宿主回环隔离模拟与受控实战验收（redaction-first）。时间除特别注明外均为 UTC。
**授权**：用户指令（2026-11-28）“确认执行”。
**代码提交**：`21c7929`（`fix(cpa): guard chatgpt-oauth foreign headers and expand cross-lane prompt sanitization`）。

## 1. 修复范围与根因

| 编号 | 模块 | 问题根因 | 修复内容 |
| --- | --- | --- | --- |
| 1 | `scripts/remote/cpa-admission.py` | `upstream.codex.disable-codex-cloaking: true` 下，CPA `ensureHeaderWithConfigPrecedence` 会将下游非空 `User-Agent` / `Originator` 原样透传至 `chatgpt.com/backend-api/codex`；非 Codex 客户端调 `chatgpt-oauth` 会暴露异构客户端身份头与跨生态 SDK 头 | 新增 `filter_upstream_request_headers()`：在 `chatgpt-oauth` 车道剥离不含 `codex` 标识的异构 `User-Agent` / `Originator` 及 `anthropic-*`、`x-claude-*`、`x-stainless-*`、`x-zcode-*` 请求头，回退 CPA 内置 Codex 默认头，同时保留官方 Codex Desktop/CLI 原生头与 CPA 鉴权头（`Authorization`、`x-api-key`） |
| 2 | `scripts/remote/cpa-admission.py`、`cpa_policy.py`、`cpa-guardrail-apply.sh` | Codex CLI 调非 GPT 模型（`gemini-*`、`glm-*`、`deepseek-*`）时回退 `codex-rs/core/prompt.md` 模板，首句 `"You are a coding agent running in the Codex CLI... led by OpenAI."` 未被原 `You are Codex` 规则与 `antigravity.sensitive-patterns-replace` 覆盖 | 在 `AGENT_IDENTITY_SENTENCES` 与 `oauth.providers.antigravity.sensitive-patterns-replace`（第 4 条有序 Go RE2 规则）同步整句中性化为 `"You are a coding agent."`，保留 `.codex` / `.zcode` 路径与普通产品名词 |
| 3 | `scripts/remote/cpa-admission.py` | Antigravity IDE 调非 agy 编程套餐车道（`workbuddy-oauth`、`zcode-start-plan`）时，首句 `"You are Antigravity... designed by the Google Deepmind team"` 未被插件（缺 `designed by` 谓词）与 admission 覆盖 | 新增按车道作用域隔离的 `_ANTIGRAVITY_SELF_INTRO_SENTENCES`：仅在非 Antigravity 编程套餐车道中性化为 `"You are a coding assistant"`，`antigravity-oauth` 原生车道保持原字节不变 |
| 4 | `scripts/remote/cpa-admission.py` | Claude Code 注入 `x-anthropic-billing-header: cc_version=...; cch=<hash>;`；当 `/v1/messages` 经 `OpenAICompatExecutor` 转发至 `zcode-start-plan` 时未被剥离，既泄漏 Anthropic 计费头又因每轮 `cch` 变化打穿前缀缓存 | 在非 Claude 身份清洗车道剥离 `x-anthropic-billing-header: cc_version=...` 行，并在列表内容与 `/v1/messages` `system` 中剔除剥空后的空文本块，保证跨轮序列化字节一致 |

## 2. 远端投影事务与备份

1. **原子 Admission 换代（`outputs/deploy-admission-header-identity-sanitization-20261128.ps1`）**：
   - DryRun：`STAGED_RULE_OK header_guard_ok codex_prompt_ok agy_scope_ok claude_cch_ok` → `ADMISSION_INTEGRITY_OK` → `DRY_RUN_OK baseline=1dfe8243171d1739691673d16143500eb39d24a2f9453170ff5423141718aa47 candidate=b92fd0949951348885b8f44be6710fe3f2a580d12e91a22e243bc37e21a16fcb no_swap=true`。
   - Apply（`2026-11-28T13:46:06Z`）：等待 `gpt-6-luna` 模型级冷却自然归零并在 `/run/vps-ssh-launcher-maintenance.lock` 与 `/run/cpa-admission/requests.lock` 排他空闲锁下原子轮换 `cpa-admission.py`、`cpa_request_guard.py` 与 `/etc/vps-ssh-launcher/cpa-admission.sha256`（`1dfe8243...` → `b92fd094...`），返回 `DEPLOY_OK`。
   - 备份路径：`/opt/cliproxyapi/backups/20261128T134606.600082128Z-admission-sanitize-20261128`。
2. **Guardrails 与 Policy 配置投影（`scripts/cpa_bwg_guardrails.ps1 -Profile bwg -Apply`）**：
   - 17 项远端受管文件全部 `PROJECTION_HASH_VERIFIED`；INTL 7 枚凭据保持 `disabled=7 of=7`；`/opt/cliproxyapi/config.yaml` 更新 4 条 `sensitive-patterns-replace` 规则，容器日志 `CONFIG_AND_CONTAINER_LOGS_OK rules=4 regex_warnings=0`。
   - 备份路径：`/root/cpa-guardrails-backup-20261128T134613.937659447Z`。

## 3. 分层验证回执

| 层级 | 验证方式 | 结果 |
| --- | --- | --- |
| `repo_verified` | WSL Go RE2 编译与替换实测 + `scripts/run_gates.ps1 -RunParallel` | `GO_RE2_VERIFIED=OK`；`CATALOG_CHECK=PASS`、`bandit`、`ruff`、`format`、`mypy`、pytest `1915 passed, 30 skipped, 903 subtests passed` |
| `filesystem_projected` | `cpa_bwg_guardrails.ps1 -Profile bwg -AuditProjection` + 远端哈希核对 | `CPA_PROJECTION_AUDIT=PASS evidence=filesystem_projection`；17 项远端文件 `PROJECTION_HASH_VERIFIED` |
| `host_loaded` | `cpa_bwg_guardrails.ps1 -Profile bwg` + 实时 `/healthz` `cpa_admission_risk_audit.py --skip-posture` | `DOCTOR_CONTRACT_OK`（`credential_ban_signals_7d=0`）；风险审计 `-- fail=0 warn=3 -- verdict: PASS with warnings` |
| 宿主隔离模拟 | BWG 加载线上 `/opt/cliproxyapi/cpa-admission.py` 对接本地回环 Mock Upstream 跑 5 组端到端用例 | `SIMULATION_E2E_OK=True cases=5`：① `chatgpt-oauth` 剥离 `claude-cli/...`、`Originator: antigravity`、`anthropic-beta`、`x-stainless-lang` 并保留 `Authorization`/`x-api-key`；② `chatgpt-oauth` 原样透传 `Codex Desktop` 原生头；③ `workbuddy-oauth` 同时中性化 Antigravity `instructions` 与 Codex CLI `prompt.md` `developer` 并保留 `~/.codex/config.toml`；④ `antigravity-oauth` 保留原生 Antigravity 自述句同时中性化引用的 Codex CLI 双句；⑤ `zcode-start-plan` 跨轮剥离不同 `cch=` 计费头后请求体字节完全一致且零空文本块 |
| `live_accepted` | 受控最小单发正向探针（零自动重试）+ 换代后自然流量观察 | ① `antigravity-oauth/gemini-4-hydrogen-xhigh`（`req_id=11aa3631ce69`，携带 `instructions` 与 `user` 的 Codex CLI `prompt.md` 双句）→ `HTTP 200`、`response.completed`（`sse_terminal_seen=true`、`first_text_ms=1149`、`usage_in=58 usage_out=5`），journal 记 `kind=antigravity_identity_user_text user_parts=1`；<br>② `workbuddy-oauth/glm-5.3-flash-wb`（`req_id=ad22b5c9c5e5`，携带 Antigravity `instructions` + Codex CLI `prompt.md` `developer`）→ `HTTP 200`（`upstream_header_ms=1231`，因推理预算 `max_output_tokens=16` 截断停在 `response.incomplete`，按纪律零重发），journal 记 `kind=agent_identity_neutralized developer_parts=1 system_parts=1`、零 `11128` 拦截；<br>③ 换代后自然流量（排除上述 2 条探针）：已完成 **260 条**（`deepseek-v4.1-flash` 249、`glm-5.3-flash-start` 4、`hy4flash-free` 3、`other` 4），全部 `protocol_result=completed` |

## 4. 边界与回滚

- **未发生成探针的车道**：`chatgpt-oauth`（单张 Plus 5h 滚动配额敏感，且本次头过滤逻辑属纯 HTTP 转发层确定性字典裁剪，已由宿主回环真实 `Handler` + Mock Upstream 100% 字节级验证）与 `zcode-start-plan`（线上已有自然流量完成验证，不额外抢占 `max_inflight=1` 槽位）。
- **远端回滚入口**：
  1. 在维护锁与请求排空锁下，从 `/opt/cliproxyapi/backups/20261128T134606.600082128Z-admission-sanitize-20261128` 还原 `cpa-admission.py`、`cpa_request_guard.py`、`cpa-admission.sha256` 并重启 `cpa-admission.service`。
  2. 从 `/root/cpa-guardrails-backup-20261128T134613.937659447Z` 还原 `/opt/cliproxyapi/config.yaml` 与 `/opt/cliproxyapi/cpa_policy.py`（或本地 `git revert 21c7929` 后重跑原子换代与 `-Apply`），再复跑 `cpa_bwg_guardrails.ps1 -Profile bwg` 核验 `DOCTOR_CONTRACT_OK`。
