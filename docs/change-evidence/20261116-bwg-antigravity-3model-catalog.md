# bwg Antigravity 车道开放 3 模型（2026-11-16）

## 授权与目标

用户 2026-11-16 指令：4 凭据池（3 pro + 1 free，当日在役）只开放
`Gemini 4 Hydrogen` / `Gemini 3.8 Flash` / `Claude Sonnet 5.5`，并按既定
风控纪律（≤80–100 请求/日/号、TOS 应急 runbook、验证只走 8318 回环）
连续执行直至收口。属用户发起的目录增补/恢复（符合
docs/runbooks/cpa-catalog-change-checklist.md 前置铁律）。

## 账号矩阵（本地 IDE 实证，2026-11-16）

- `gemini-4-hydrogen-xhigh`（上游以 xhigh 档注册）与
  `claude-sonnet-5-5-high` 仅其中一份 pro 凭据可供（IDE 实证）——
  单点承重、无 failover；`gemini-3.8-flash-high` 全池可供。
- context-length 取证：Hydrogen 1048576（Google 官方 1M 统一窗口）；
  Sonnet 5.5 1000000（Anthropic 官方 1M）。

## 仓库变更（commit 9ccf476）

- manifest `oauth_routes[antigravity-oauth].models` + 路由/设置模型表
  加 2 名（含 max-context-length）；`antigravity_oauth_exclusions` 摘
  `claude-sonnet-5-5-high`；`antigravity_upstream_static_models` 加
  `gemini-4-hydrogen-xhigh`；`codex_api_key_exclusions` 加 2 名防抢注。
- `cpa-admission.json`、apply/doctor 内嵌 expected lanes 表、
  `tests/cpa_catalog_expectations.py` 同步为 3 模型（同一顺序）。
- 隔离夹具修正：marker 必须覆盖车道全部别名（load_quarantine_aliases
  的 all-aliases 契约在 3 模型车道下首次可观测）。
- catalog_check PASS；full gates 1717 passed / 31 skipped / 815 subtests。

## 远端事务序列（含两次拦截与处置）

1. 首次 `-Apply`：SSH 中途断连（本机 10051/远端 10054），事务在写阶段
   前终止——容器未重启、无当日备份目录、远端零变更（只读复核后清
   `/tmp/cpa-guardrails-*.b64` 残留重试）。
2. 重试 `-Apply`：staged 字节语义校验
   `REFUSE antigravity exclusion blocks a declared antigravity alias`
   ——apply 的排除派生为「远端现值 ∪ manifest」只增不减，manifest 摘名
   不会从远端配置摘名。按 2026-10-15 sensitive-words 既有手术模式：
   备份 `/root/cpa-config-backup-antigravity-unexclude-20261116.yaml`
   （sha16 ad1cf731723e4bf3）→ `sed -i` 摘除
   `claude-sonnet-5-5-high` 单行（count 1→0，块其余 11 名逐字未动，
   摘后 sha16 d29196aa4e69ef0a）→ 不单独重启（留给 apply 事务统一
   重启，无"开而不治"窗口）。
3. 三次 `-Apply`：`GUARDRAILS_APPLIED`（PRUNE keep_8）。

## 终验（doctor 严格模式）

- `antigravity_lane=OK allowed=claude-sonnet-5-5-high,gemini-3.8-flash-high,gemini-4-hydrogen-xhigh`
- `MODEL_IDS_UNKNOWN=none`（11-16 早前 hydrogen 漏名未治理状态一并收口）
- 14 项投影 drift 全 MATCH（锚 HEAD 9ccf476）；`DOCTOR_CONTRACT_OK`。
- 8318 回环单发（各一发、不重试、经 admission 全链）：
  - `gemini-4-hydrogen-xhigh` → finish=stop，content=OK；上游回显基名
    `gemini-4-hydrogen`（去后缀命名习惯，同 gemini-3.8-flash-n 已知
    名称变体模式；请求侧契约名不变，观察项）。
  - `claude-sonnet-5-5-high` → finish=stop，content=OK，回显精确同名。
- `ACCEPTANCE_RESULT=PASS`

## 风控记账

- 72h TOS_VIOLATION / PERMISSION_DENIED / invalid_grant = 0/0/0
  （doctor credential_ban_kind 同证）；6008×47 属 workbuddy 车道日窗
  代码，与本车道无关。
- 4 份 antigravity 凭据已备份 `/root/auth-backups/`（SHA 全 VERIFIED：
  bc1673cd / 8ac28aef / bb8f71a0 / 4e9e1f27）。
- 纪律不变：≤80–100 请求/日/号（含本机 IDE 同号用量）；TOS 应急按
  cpa-ban-throttle-incident-response / 全池停 6h runbook；sensitive-words
  [gpt,openai] 与 admission antigravity-oauth inflight=2 未动。

## 回滚

仓库回滚 9ccf476 后重跑 `-Apply`；远端恢复
`/root/cpa-config-backup-antigravity-unexclude-20261116.yaml` 并重启容器。
凭据回滚用 `/root/auth-backups/`。
