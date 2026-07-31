# AGENTS.md - vps-ssh-launcher
**项目契约**: 2.0
**全局规则复核**: 9.57
**最后更新**: 2026-07-15

## 1. 当前落点与目标归宿
- 当前落点：本仓是 Windows-first Python SSH/VPS 启动与维护工具，入口为 `run.cmd`、`connect.cmd`、`connect.ps1`、`ssh_tool.py` 与 `auto_install.py`。
- 目标归宿：在不泄漏凭据、不误伤真实主机的前提下，提供稳定连接、配置解析、批量执行与可审计维护。
- 下一最小里程碑：完成当前本地/主机 slice，先过默认离线 full gate；真实 SSH 仅按明确 profile 和授权追加。

## A. 仓库事实与模块边界
- `ssh_tool.py` 承载配置、连接、并行执行和退出码；`auto_install.py` 只作依赖安装辅助。
- `target.json` 是本机敏感配置，优先位于用户配置目录；仓库只保留 `target.example.json`。
- `scripts/run_gates.ps1` 是本地聚合门禁；`test_*.py` 包含单元、wrapper 与默认跳过的真实 SSH 集成。
- `docs/security-waivers.md` 是安全豁免/复审依据；真实主机 runbook 与证据放 `docs/`。

## B. 执行与风险边界
- 明文密码、私钥路径与 root 直登是受支持的本机模式，但真实值不得提交、打印或写入证据。
- 真实 SSH 默认跳过；只有显式 `VPS_SSH_LAUNCHER_RUN_INTEGRATION=1` 或 `scripts/run_gates.ps1 -RunIntegration` 才能启用。
- 代理内核、vasma、xray/sing-box stop-start、月度维护等联网高风险操作必须逐台执行；一台完成并复验后暂停，等待用户确认正常才可处理下一台，禁止并行触发两台 VPS。
- 真实 SSH 失败先区分仓库逻辑、凭据/配置、远端主机与 Windows 网络/进程环境。
- 当前工作树可能含用户的主机维护实现/证据；本任务只修改规则/wrapper/rollout evidence，不回退或纳入其他改动。

## C. 门禁、证据与回滚
- fixed order：`build -> test -> contract/invariant -> hotspot`。
- build：`python -m compileall -q ssh_tool.py auto_install.py test_ssh_tool.py test_auto_install.py test_scripts.py test_integration_real_ssh.py`
- test：`python -m pytest -q`
- contract/invariant：`python -m unittest -q`
- hotspot/full：`pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1`，覆盖 pip check/audit、Bandit、Vulture、Ruff、Mypy/Pyright 等。
- full 默认不跑真实 SSH；集成 gate 必须显式传 `-RunIntegration` 与 profile/config，且执行前确认目标。
- 隔离 Python/dev 依赖缺失时按 README 建 `.venv` 或设置 `VPS_SSH_LAUNCHER_PYTHON`；不得默认污染全局 Python。
- 语法/测试/contract/security/type 失败即阻断；真实主机变更还需服务、配置、端口与回退验证。
- 证据放入 `docs/change-evidence/`；记录风险、命令、exit code、是否触发真实 SSH、目标 profile（无 secret）与回滚。
- 回滚只撤销本任务仓库切片；真实主机使用变更前备份/反向命令并逐台复验，不得用 Git 回滚代替主机恢复。

## D. Global Rule -> Repo Action
- `R1-R5`：先定 launcher/脚本/主机/文档落点，小步离线验证；不扩张无证据自动化。
- `R6`：C 章固定顺序与 full gate 收口；真实 SSH 是授权后的附加层。
- `R7`：保持 target schema、profile、凭据字段、wrapper 和退出码兼容。
- `R8`：证据区分离线、真实 SSH 与用户既有改动，并给恢复入口。
- `E4`：full/integration gate 承接健康；`E5`：Paramiko/SSH/security tools 记录供应链；`E6`：配置/profile/退出码变化必须有迁移、兼容和回滚。
