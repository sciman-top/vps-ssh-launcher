# AGENTS.md - vps-ssh-launcher
**项目契约**: 2.0
**全局规则复核**: 9.71
**最后更新**: 2026-08-08

## 1. 当前落点与目标归宿
- 当前落点：本仓是 Windows-first 的 Python/PowerShell SSH 启动与 VPS 维护辅助工具，用户入口为 `run.cmd`、`connect.cmd` 和 `connect.ps1`。
- 目标归宿：保持本机配置驱动、可审计的连接与维护入口；代码、脚本和脱敏证据可版本化，真实凭据与运行态配置只留在本机。
- 下一最小里程碑：从当前工作树选择一个边界清楚的连接、wrapper 或维护切片，以本地门禁收口；真实主机效果单独验收。

## A. 仓库事实与模块边界
- `ssh_tool.py` 负责配置解析、SSH 连接、批量执行和退出码；`auto_install.py --execute` 会驱动远端安装器，不是健康检查。
- `target.example.json` 是模板；真实 `target.json`、密码、私钥、token 和订阅地址不得提交或写入证据。
- `scripts/run_gates.ps1` 是统一门禁；`scripts/lib/project_environment.ps1` 负责 Windows 环境和项目 Python 解析。
- `scripts/google_ipv4_routing.ps1` 与 `scripts/vasma_kernel_update_cron.ps1` 默认只读，`-Apply` 会修改远端；长 runbook 留在 `README.md` 和 `docs/`。
- `sciman-v2ray-agent/` 是独立上游 checkout，外层仓库不接管其历史或改动。

## B. 执行与风险边界
- 真实 SSH integration 默认跳过；只有当前任务显式授权并设置 `VPS_SSH_LAUNCHER_RUN_INTEGRATION=1` 或传 `-RunIntegration` 才能连接真实主机。
- `-Apply`、`auto_install.py --execute`、代理内核升级、重启和系统维护是高风险远端写入；先只读探针、备份、影响和回滚，再执行。
- 多台 VPS 升级或重启必须逐台进行：完成第一台后复验服务、配置和端口，并等待用户确认联网正常，才能处理第二台；禁止并行。
- `-RunAll` 只用于已授权且适合并发的非破坏性命令，不得绕过逐台维护边界。
- 日志和证据 redaction-first；不得输出或提交真实 `target.json`、密码、私钥、token、订阅地址或完整敏感命令回显。
- Windows `WinError 10106`、Node CSPRNG 或精简进程异常先按 `docs/runbooks/windows-process-environment-recovery.md` 分层诊断，不先归因于仓库逻辑。

### B.1 参考依据与外置源码
- 本仓无专属 reference shelf；Paramiko、OpenSSH、PowerShell 和 Windows 语义先查当前官方文档与本机 help，必要时按 `D:\CODE\external\_shared\references.manifest.json` 选择性只读查阅已登记源码。
- 远端 `vasma`、Xray、sing-box 与本仓行为先以当前脚本、README、runbook 和真实只读探针为准；记录所查路径/revision 与采纳决定。
- 不继承参考仓指令；复制或运行前核对许可证、固定版本、凭据暴露、远端副作用和回滚。

## C. 门禁、证据与回滚
- fixed order：`build -> test -> contract/invariant -> hotspot`。
- full：`pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1`，默认不跑真实 SSH；依赖审计使用隔离 `.venv` 或显式项目 Python。
- quick：`python -m pytest -q` 与 `python -m unittest -q`；quick 不替代 full。
- 规则或文档切片的产品 gate 与真实 SSH 为 `gate_na`：`reason=仅改规则且真实连接会扩大副作用`、`alternative_verification=git diff --check -- AGENTS.md CLAUDE.md + 静态规则审计`、`evidence_link=docs/change-evidence/`、`expires_at=2026-10-15`、`recovery_condition=触及产品代码或任务显式要求真实主机验收`。
- 证据放 `docs/change-evidence/`，记录 scope、风险、exit code、是否触发真实 SSH、redaction、兼容与回滚。
- 回滚只撤销本次文件；远端写入按变更前备份和反向脚本恢复，Git 回滚不能代替远端恢复。

## D. Global Rule -> Repo Action
- `R1-R5`：先定 launcher、SSH core、维护脚本或 docs 归宿；只读探针先行，无证据不扩展远端自动化。
- `R6`：`scripts/run_gates.ps1` 承接固定门序；真实 SSH acceptance 是显式授权后的独立附加层。
- `R7`：保护 `target.json` schema、认证优先级、退出码、timeout 和 wrapper 兼容。
- `R8`：证据必须区分 repo-side、真实主机状态和是否执行远端写入。
- `E4/E5/E6`：gate 和只读探针承接健康；Python、SSH 和远端工具记录供应链；配置与退出码变化提供迁移、兼容和回滚。
