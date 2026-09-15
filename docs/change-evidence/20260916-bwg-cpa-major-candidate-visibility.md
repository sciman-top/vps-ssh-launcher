# 2026-09-16 BWG CPA 更新器 major 候选可见性

按审查建议补上"跨 major 永不自动升级"策略的可见性缺口：major 线出现成熟
候选时，更新器只记录日志供 doctor 带出，绝不执行升级。本次只处理 `bwg`。

## 变更

- `scripts/remote/cpa-auto-update.sh`：SELECTION 在常规候选输出之外，对
  "GitHub release 成熟 + Docker Hub 有 digest"的更高 major 候选输出一行
  `MAJOR_CANDIDATE available=<tag>`（只取最高者）；bash 侧在 CANDIDATE 行
  之后逐行写日志。该行天然命中 doctor timer 段的既有关键字 grep
  （`CANDIDATE`），无需改 doctor。选择目标仍被 `[0] == current_version[0]`
  锁定在同 major 线内，跨 major 升级保持人工评审 + canary 流程不变。
- `test_scripts.py`：复用既有夹具（v7.2.x/v7.3.0/v8.0.0）断言——v7 系
  current 输出且仅输出 `MAJOR_CANDIDATE available=v8.0.0`，v8 系 current
  不输出；选择目标永不跨 major。源级锚点断言 `MAJOR_CANDIDATE available=`
  存在。
- README 更新器段落补一句说明（只写日志、doctor 带出、不做动作）。

## 验证

- full gate：`128 passed, 1 skipped, 89 subtests passed`；Bandit、Ruff
  check/format、mypy 通过（首轮 format gate 抓出测试缩进问题，已修复后
  复跑全绿）；`git diff --check` 通过。
- 投影（备份 `/root/cpa-updater-majorvis-20260915T235612Z`）：远端 sha256
  `563b0dcbe3f432c693ab5f32d58f532b93dd4ab175176f73ff21f70edb208428` 与本地
  源一致；`bash -n` 通过；真机 `--check` exit 0
  （`CANDIDATE current=v7.3.4 target=v7.3.4`，当前无成熟 major，无 MAJOR
  行，符合预期）。投影后 strict doctor `DOCTOR_CONTRACT_OK`。

## 边界

- 本变更只新增日志输出，不改变选择/升级/回滚行为；夹具
  `cpa-update-acceptance.py` 的 stub 元数据无 major 候选，不受影响。
- 回滚 = 从备份目录拷回上一版 updater（`79ae7a89…`）。
