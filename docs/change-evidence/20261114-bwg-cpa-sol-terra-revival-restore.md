# 2026-11-14 bwg CPA：gpt-6.1-sol / gpt-6.1-terra 上游复活后恢复

## 背景与授权

- 2026-11-13 两名随上游 400 `model_not_found` 退役（d2f2eb7）。2026-11-14
  用户指出两名"现在应该是可用了，但模型列表中没看到"，并要求恢复——本次
  为用户发起的反向事务。
- 同日固化铁律（用户指令）：**模型退役/改名只能由用户主动提出**，上游
  400/5xx/冷却等死名信号不构成退役授权；已写入
  [cpa-catalog-change-checklist](../runbooks/cpa-catalog-change-checklist.md)
  前置铁律段（按用户裁决不进项目 AGENTS.md，防根规则膨胀）。

## 上游复活实证（远端直探，只读）

- `GET https://chatgpt.com/backend-api/codex/models?client_version=0.159.0`
  （`Originator: codex_cli_rs` + codex_cli_rs UA + lane 凭据 Bearer，
  token 取自 bwg auth 文件进程内解出，未打印）：
  `HTTP 200 / 0.64s`，slugs 含 `gpt-6.1-sol` 与 `gpt-6.1-terra`
  （plan=plus，token 有效期至 2026-11-24）。
- 上游另新供 `gpt-6-sol` / `gpt-reserve` / `codex-auto-review`：均未声明
  且在排除清单/注册面 fail-closed（核心目录未放行，`/v1/models` 不见）。
  是否采纳属后续用户裁决，本次不动。
- doctor 基线（改前）：`DOCTOR_CONTRACT_OK`，无带病。

## 变更内容（提交 1d39b88，d2f2eb7 的镜像反转）

`cpa_provider_routes.json`（oauth_routes codex 两别名 + oauth_settings.codex
pin `sol=500000` / `terra=272000` + 摘除 11-13 加的两条 oauth 墓碑）、
`cpa-admission.json`（chatgpt-oauth lane models）、`cpa-guardrail-apply.sh`
（expected 字典 + `CODEX_OAUTH_ROUTES_READY` 文案）、`cpa-guardrail-doctor.sh`
（expected 字典）、`tests/cpa_catalog_expectations.py`、三个测试夹具文件
（admission/health_doctor/policy_runtime）、两本 runbook
（cpa-gateway / cpa-oauth-luna-slot）。桌面
`config/codex-model-context-overrides.json` 两条目退役期间未被移除，无需改。

## 门禁与部署验收

- 预检：`cpa_catalog_check.py` → `CATALOG_CHECK=PASS`；定向
  4 文件 419 passed / 4 skipped（POSIX）/ 148 subtests。
- Full gates：**1404 passed / 25 skipped / 693 subtests**，bandit、ruff
  check/format、mypy 全净（-Profile Auto 判 Full）。
- `-Apply`（2026-11-14 ~04:09 UTC）：`GUARDRAILS_APPLIED`、投影 11 文件
  哈希回执、catalog `models=13`（11+2）、`PRUNE scope=cpa_guardrails_backups
  removed=1 policy=keep_8`；备份
  `/root/cpa-guardrails-backup-20261114T040930.359511091Z`。（READY_STATUS
  行因日志 tail 截断未留档，readiness 由 doctor 与单发补证。）
- doctor 严格模式：`DOCTOR_CONTRACT_OK`、drift 全 MATCH、
  `MODEL_IDS_UNKNOWN=none`、`MODEL_CONTEXT_DRIFT=none`、
  `admission-health=OK`、`catalog_oauth_aliases=gpt-5.6-luna,gpt-6-luna,
  gpt-6.1-sol,gpt-6.1-terra`、`catalog_oauth_missing=none`。
- 8318 回环单发（各一发不重试，非流式 `/v1/responses`，"Reply with
  exactly: OK"）：

```text
gpt-6.1-terra HTTP=200 LATENCY_MS=1146 SERVED_MODEL=gpt-6.1-terra STATUS=completed OUTPUT_PRESENT=True SNIPPET=OK
gpt-6.1-sol   HTTP=200 LATENCY_MS=1128 SERVED_MODEL=gpt-6.1-sol   STATUS=completed OUTPUT_PRESENT=True SNIPPET=OK
LIVE_PROBE_RESULT=PASS
```

## 回滚入口

- 仓库 revert 1d39b88 后须 `cpa_bwg_guardrails.ps1 -Apply` 重投影（Git
  回滚不落远端）；远端按
  `/root/cpa-guardrails-backup-20261114T040930.359511091Z` 反向恢复。

## ACCEPTANCE_RESULT=PASS

两名经 admission 全链 LIVE_ACCEPTED（200/completed/回显正确/输出 "OK"），
doctor 全 MATCH，客户端 `/v1/models` 目录 13 名含两名。
