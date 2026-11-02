# outputs/

一次性探针、canary 脚本与版本化模板副本的 scratch 归档区。治理口径：

- 例行验收/探针**不**在此建档：判据行（如 `ACCEPTANCE_RESULT=PASS`）写进提交信息，
  叙事进 `docs/change-evidence/`（仅真实远端写入、事故、release）。
- 每代 canary 只保留最新一套模板副本（当前 v8.0.51 + WorkBuddy 0.9.82）；
  旧代退役到 git 历史，需要历史脚本时用 `git log --diff-filter=D -- outputs/` 找回。
- 可复用工具与被 `tests/` 引用的模板固定保留（如 `cpa-v8021-canary.sh`、
  `deploy-admission-text-finish-20261101.ps1`）。
- 被投影/引用的敏感值禁止出现（占位符纪律）。
