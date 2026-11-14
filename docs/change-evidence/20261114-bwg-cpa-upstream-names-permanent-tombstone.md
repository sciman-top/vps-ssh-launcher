# 2026-11-14 bwg CPA：gpt-6-sol / gpt-reserve / codex-auto-review 永久排除

## 授权与目标

用户指令（2026-11-14，铁律合规的用户主动退役）："gpt-6-sol / gpt-reserve /
codex-auto-review 这 3 个去掉，以后不用了"。三名为上游 codex 目录新供但从未
声明的名字，此前仅靠 fail-closed 注册门隐藏；本次落成**永久双墓碑**，任何
未来漂移/自动重发现都无法在 OAuth 或 API-key 车道注册。

## 差距与变更（提交 4768174，曾以 7dd1b0a 预提交后两次 amend 修正信息）

- `codex-auto-review` 与 `gpt-6-sol`：`oauth_exclusions` +
  `codex_api_key_exclusions` 双墓碑原已齐备，零改动。
- `gpt-reserve`：仅 API-key 侧有墓碑（2026-11-04 退役时的有意最小形态），
  **补入 `oauth_exclusions`**。上游正在供名，`codex-auto-review` 先例证明
  此形态工作正常；冻结契约夹具同步翻转（`assertNotIn` → `assertIn`，属
  用户指令驱动的有意契约变更）并留注释。
- 预言机注释与 [codex-desktop-gateway-attach](../runbooks/codex-desktop-gateway-attach.md)
  口径同步（"以后不用"双墓碑）。

## 门禁与部署验收

- 前一轮 Full gates 唯一失败即上述夹具翻转点，修后实测：
  **1408 passed / 25 skipped / 693 subtests**（bare pytest 实测行），
  run_gates exit 0，bandit/ruff/format/mypy 全净。
- `-Apply`（2026-11-14 ~05:26 UTC）：`READY_STATUS=200`、
  `GUARDRAILS_APPLIED`、catalog `models=13`（墓碑不增名，符合预期）、
  备份 `/root/cpa-guardrails-backup-20261114T052654.377555740Z`。
- doctor 严格：`DOCTOR_CONTRACT_OK`、`MODEL_IDS_UNKNOWN=none`、
  `MODEL_CONTEXT_DRIFT=none`、`catalog_oauth_missing=none`、
  `admission-health=OK`。
- 派生面核对：远端 `config.yaml` `oauth.excluded-models` 实测含
  `codex-auto-review` / `gpt-6-sol` / `gpt-reserve` 三名。
- lane 健康单发（重启后一发不重试）：`gpt-6-luna HTTP=200 / 2048ms /
  completed / "OK"`。

## 回滚入口

- revert 4768174 后 `-Apply` 重投影；远端按
  `/root/cpa-guardrails-backup-20261114T052654.377555740Z` 反向恢复。

## ACCEPTANCE_RESULT=PASS

三名双墓碑齐备且已投影生效；doctor 全 MATCH；lane 重启后健康单发 200。
