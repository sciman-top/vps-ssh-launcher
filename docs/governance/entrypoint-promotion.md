# Entrypoint Promotion

本文件对应 `.governed-ai/repo-profile.json` 中的 `required_entrypoint_policy.promotion_condition_ref`，用来解释当前 governed runtime 入口策略在本仓的含义。

## 当前状态

- `current_mode`: `targeted_enforced`
- `target_mode`: `repo_wide_enforced`
- canonical entrypoints:
  - `runtime-flow`
  - `runtime-flow-preset`
- direct allowlist:
  - `run-governed-task.status`
  - `session-bridge.inspect_status`
  - `session-bridge.inspect_evidence`
  - `session-bridge.inspect_handoff`
  - `verify-repo`

这里说的是 governed runtime / orchestration 入口，不是替换仓库操作员日常使用的 `run.cmd`、`connect.cmd`、`scripts/run_gates.ps1` 这些 README 入口。

## `targeted_enforced` 的仓库含义

当前只在下列治理相关 scope 上强制 canonical entrypoint 约束：

- `run_quick_gate`
- `run_full_gate`
- `verify_attachment`
- `govern_attachment_write`
- `write_request`
- `write_execute`
- `execute_attachment_write`

也就是说：

- 代码与运维入口仍按本仓 README / AGENTS 运行
- governed runtime 的写入或执行流，只有在上述 scope 内才被强制检查
- 观察、状态查询和证据读取类动作仍允许 direct allowlist 中的入口直达

## 何时可以提升到 `repo_wide_enforced`

满足下面条件后，才适合把目标从 `targeted_enforced` 推进到 `repo_wide_enforced`：

1. 所有实际存在的 governed runtime 写入 / 执行路径，都已经走 canonical entrypoints，或被明确列入 direct allowlist。
2. README、AGENTS、CI、脚本注释和管理文档中，不再指导使用未收敛的直接写入入口。
3. `repo-profile`、门禁脚本、managed-files provenance 与仓库实际入口已经同步，没有 contract drift。
4. 在迁移后的状态下，完整门禁仍然通过，且没有新增高风险例外。
5. 有新的 `docs/change-evidence/*.md` 记录这次升级的命令、关键输出、风险边界和回滚路径。

## 阻断条件

遇到下面任一情况，不应推进到 `repo_wide_enforced`：

- 仍有未纳管的 direct write / execute 入口在被 README、CI 或操作手册使用
- `repo-profile` 与 `scripts/run_gates.ps1`、仓库规则或 managed-files 之间存在漂移
- 当前还需要一个临时直连入口，但没有 owner、过期时间和恢复计划
- 证据不足，无法证明 canonical entrypoint 覆盖了真实使用路径

## 回滚

如果 promotion 后发现误伤正常流程，回滚动作应最少包含：

1. 把 `repo-profile` 中相关策略恢复到上一个已验证状态。
2. 回退与本次 promotion 同步修改的文档、脚本或 managed-files。
3. 重新执行本地完整门禁。
4. 在新的 `docs/change-evidence/*.md` 中记录“为何回滚、回滚了什么、现在的真实状态是什么”。
