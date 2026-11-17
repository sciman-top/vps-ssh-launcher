# outputs/

一次性探针、canary 脚本与版本化模板副本的 scratch 归档区。治理口径：

- 例行验收/探针**不**在此建档：判据行（如 `ACCEPTANCE_RESULT=PASS`）写进提交信息，
  叙事进 `docs/change-evidence/`（仅真实远端写入、事故、release）。
- 每代 canary 只保留必要模板；当前 BWG 为 CPA 镜像 v8.0.61 + 运行时 fork 二进制
  `681ce41b+gemini-raw`（SHA `31f2f9d9…`）+ WorkBuddy 0.9.89
  （官方 `cpa-multi-plugins` v0.8.97 构建，`.so` SHA `f2c678ff…`）；
  **运行时二进制是 bind-mount 覆盖，换镜像 tag 不会改运行代码**，换装必须走
  `cpa-v8061-converge-transaction-20261108.sh` 这类收敛事务；
  生产版本/SHA 仍以逐次台账和 strict doctor 为准。
- 现役与近期代际固定保留（`cpa-v8021-canary.sh`、v8.0.61 收敛三件
  `cpa-v8061-converge-transaction-20261108.sh` / `run-cpa-v8061-converge-20261108.ps1` /
  `build-cpa-v8061-gemini-raw-20261108.ps1`、
  `cpa-v8061-fork-binary-isolated-load-20261108.ps1`、
  `run-lane-singles-v8061-20261108.ps1`、`cpa-v8061-swap-transaction-20261108.sh`、
  v8.0.58 隔离加载/验收/swap 事务四件、0985–0987 构建/隔离加载/部署件、
  `deploy-admission-text-finish-20261101.ps1`、`deploy-admission-first-text-20261104.ps1`、
  `deploy-admission-credits-6008-20261107.ps1`、`release-workbuddy-hold-20261107.ps1`）：
  在役代际的回滚素材与历史事务对照，不得再部署到生产。
- 2026-11-17 luode0320 迁移演练（隔离实例，未动生产）：mmqz 凭据**免重登平移实证**
  （CN 10+INTL 7 全部解析、ping 全通、free 模型端到端 200）。转换=auth 文件
  `type`/`provider` 改写（`workbuddy`→`workbuddy-provider`，intl→`workbuddy-ai-provider`）
  + intl 文件名 `workbuddy-intl-`→`workbuddyai-`；插件配置键=文件 stem。采纳素材暂存
  bwg `/root/wb-luode-rehearsal-20261117/`（`workbuddy-provider.so` SHA `a726f672…`
  0.21.27、`workbuddy-ai-provider.so` SHA `8fa992e8…` 0.1.40），fixture 凭据副本已删，
  切换另行裁决。
- 其余旧代一次性件已退役到 git 历史：需要时用
  `git log --diff-filter=D -- outputs/` 找回；新增件默认不入库
  （`.gitignore` 整目录忽略），被 tests/ 或 tracked 收据点名的事务脚本
  用 `git add -f` 单独入库。
- 被投影/引用的敏感值禁止出现（占位符纪律）。
