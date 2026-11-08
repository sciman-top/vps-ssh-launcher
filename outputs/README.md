# outputs/

一次性探针、canary 脚本与版本化模板副本的 scratch 归档区。治理口径：

- 例行验收/探针**不**在此建档：判据行（如 `ACCEPTANCE_RESULT=PASS`）写进提交信息，
  叙事进 `docs/change-evidence/`（仅真实远端写入、事故、release）。
- 每代 canary 只保留必要模板；当前 BWG 为 CPA 镜像 v8.0.61 + 运行时 fork 二进制
  `681ce41b+gemini-raw`（SHA `31f2f9d9…`）+ WorkBuddy 0.9.89
  （官方 `cpa-multi-plugins` v0.8.97 构建，`.so` SHA `f2c678ff…`）；
  **运行时二进制是 bind-mount 覆盖，换镜像 tag 不会改运行代码**，换装必须走
  `cpa-v8061-converge-transaction-20261108.sh` 这类收敛事务；
  上一代 `cpa-v8059-*` /`cpa-v8061-swap-transaction-20261108.sh`（只换镜像的
  早期模板，含 ERR-only 回滚缺陷）与 0.9.87/0.9.88 只作历史与回滚素材，不得再部署；
  生产版本/SHA 仍以逐次台账和 strict doctor 为准；
  旧代退役到 git 历史，需要历史脚本时用 `git log --diff-filter=D -- outputs/` 找回。
- 可复用工具与被 `tests/` 引用的模板固定保留（如 `cpa-v8021-canary.sh`、
  `cpa-v8061-converge-transaction-20261108.sh`、
  `run-cpa-v8061-converge-20261108.ps1`、
  `build-cpa-v8061-gemini-raw-20261108.ps1`、
  `cpa-v8061-fork-binary-isolated-load-20261108.ps1`、
  `run-lane-singles-v8061-20261108.ps1`、
  `deploy-admission-text-finish-20261101.ps1`、
  `deploy-admission-first-text-20261104.ps1`、
  `deploy-admission-credits-6008-20261107.ps1`、
  `release-workbuddy-hold-20261107.ps1`、
  `cpa-v8058-stage-isolated-load-20261107.ps1`、
  `cpa-v8058-acceptance-20261107.ps1`、
  `cpa-v8058-swap-transaction-20261107.sh`、
  `run-cpa-v8058-swap-20261107.ps1`、
  `cpa-v8058-workbuddy-0987-official-isolated-load-20261107.ps1`、
  `deploy-workbuddy-0987-official-20261107.ps1`、
  `build-workbuddy-0985-intl-shared-20261105.ps1`、
  `cpa-v8054-workbuddy-intl-shared-load-20261105.ps1`、
  `deploy-workbuddy-0985-intl-shared-20261105.ps1`、
  `build-workbuddy-0986-20261105.ps1`、
  `cpa-v8055-workbuddy-0986-isolated-load-20261105.ps1`、
  `deploy-workbuddy-0986-official-20261105.ps1`、
  `build-workbuddy-0987-canonical-20261105.ps1`、
  `deploy-workbuddy-0987-canonical-20261105.ps1`、
  `cpa-v8055-workbuddy-0987-candidate-matrix-20261105.ps1`、
  `audit-workbuddy-0987-uid-coordinates-20261105.ps1`、
  `audit-workbuddy-0987-snapshot-coordinates-20261105.ps1`、
  `snapshot-workbuddy-natural-window-20261105.ps1`、
  `accept-workbuddy-cn-isolation-20261105.ps1`、
  `build-workbuddy-cn-local-replay-20261105.ps1`、
  `audit-workbuddy-token-copies-20261105.ps1`、
  `scrub-workbuddy-token-backups-20261105.ps1`、
  `run-workbuddy-production-pong-20261105.ps1`、
  `snapshot-workbuddy-natural-window-20261105.ps1`）。
- 被投影/引用的敏感值禁止出现（占位符纪律）。
