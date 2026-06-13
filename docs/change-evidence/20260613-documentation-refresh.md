# 2026-06-13 documentation refresh

- rule_id: `R1/R2/R6/R8`
- risk_level: low
- scope:
  - `README.md`
  - `docs/governed-runtime-batch-validation.md`
  - `docs/security-waivers.md`
  - `docs/runbooks/windows-process-environment-recovery.md`
  - `docs/governance/entrypoint-promotion.md`
- change:
  - 重写 `README.md`，按当前入口、门禁、CI、运维脚本和证据路径重组文档结构
  - 用真实仓库状态替换 `docs/governed-runtime-batch-validation.md` 的占位内容
  - 为 `repo-profile` 已引用但缺失的 Windows 环境恢复 runbook 和 entrypoint promotion 文档补齐落点
  - 在 `docs/security-waivers.md` 中补上当前复审元数据
- real_ssh_triggered: no
- rollback: 用 git 回退上述文档文件；若只回退单项，优先保证 `README.md`、`docs/governed-runtime-batch-validation.md` 与新增 runbook/governance 文档同步恢复

## Commands

- `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1`
- `git diff --check`

## Key Output

- `scripts/run_gates.ps1`:
  - `build`: pass
  - `pytest`: `82 passed, 1 skipped, 22 subtests passed`
  - `unittest`: `Ran 83 tests ... OK (skipped=1)`
  - `contract:powershell-policy`: `status=pass`, `violation_count=0`
  - `pip check`: `No broken requirements found.`
  - `pip-audit`: `No known vulnerabilities found`
  - `bandit` / `vulture` / `ruff check` / `ruff format --check` / `mypy` / `pyright`: pass
- `git diff --check`: exit code `0`
  - note: Git 仍报告了与本次改动无关的既有 CRLF/LF 警告，涉及 `.governed-ai/managed-files/...` 和 `.governed-ai/repo-profile.json`

## Notes

- 本次没有重新执行 live SSH、远端维护或 `-Apply` 类高风险动作；README 中的现场状态引用继续以 `docs/change-evidence/20260528-bwg-live-function-maintenance.md` 和 `docs/change-evidence/20260528-zz-singbox-system-maint.md` 为准。
- 工作区里已有 `.governed-ai/**` 的未提交变更，本次文档刷新没有改动这些文件。
