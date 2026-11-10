# AGENTS.md - vps-ssh-launcher
**项目契约**: 2.0
**全局规则复核**: 9.93
**最后更新**: 2026-11-10

## 1. 当前落点与目标归宿
- source of truth：本文件第 A 节的仓库事实、当前代码与脚本、`scripts/run_gates.ps1` 的门禁结果；运行态配置真源在仓外 `%APPDATA%\vps-ssh-launcher\target.json`。
- entrypoint：`run.cmd`、`connect.cmd`、`connect.ps1` 与 `vps-maint`（`vps_ssh_launcher/maintenance_cli.py`，维护控制平面）。
- 当前落点：本仓是 Windows-first 的 Python/PowerShell SSH 启动与 VPS 维护辅助工具。
- 目标归宿：保持本机配置驱动、可审计的连接与维护入口；代码、脚本和必要的远端变更证据可版本化，真实凭据与运行态配置只留在本机。
- 主机清单、可达性与维护结果从本地私有配置、只读探针与当次脱敏证据 fresh read；根规则不保存 IP、版本或在线结论。

## A. 仓库事实与模块边界
- domain invariants：下列条目即本仓不变量；实现细节以当前代码与脚本为准。
- `vps_ssh_launcher/cli.py` 负责参数与动作分派；`config.py`、`connection.py`、`execution.py`、`batch.py` 分别负责配置、连接、命令流与批量调度，`contracts.py` 定义共享常量与协议；`ssh_tool.py` 是兼容入口。`auto_install.py --execute` 驱动远端安装器，不是健康检查。
- SSH 连接配置真源是 `%APPDATA%\vps-ssh-launcher\target.json`（明文 `password` 与 `default` 字段，优先于仓库路径；仓库根不保存连接配置）。Git Bash 直跑：`./.venv/Scripts/python.exe ssh_tool.py [--profile <name>] run --command '<cmd>'`；`run.cmd` 经 PowerShell 转发，对带管道的命令会坏引号。
- `target.example.json` 是模板；密码、私钥、token 、订阅地址不得提交或写入证据。
- `scripts/run_gates.ps1` 是统一门禁；`scripts/lib/project_environment.ps1` 负责 Windows 环境、Python 解析与共享远程传输（`Invoke-LauncherRemoteCommand`：CRLF 归一、base64、超限分块临时文件），wrapper 不自写远程传输。
- 测试统一在 `tests/`（pytest `testpaths = ["tests"]`，元测试强制根目录无散落测试文件）；可维护资源名单一真源是 `vps_ssh_launcher/maintenance/resources.py`。
- `scripts/remote/` 文件名 kebab/snake 混用属既成事实；重命名会连锁投影清单、systemd 单元与 guardrails 哈希表，勿为统一重命名。
- 远端防火墙基线 `scripts/firewall_baseline.ps1` 维护独立 `inet vps_launcher_guard` 表（默认 drop、精确端口 allowlist、UDP 20000–20999 → Hysteria2 obfs 8444 hairpin），不接管 vasma/Docker 规则，drift lane 为 `firewall-baseline`。
- `scripts/google_ipv4_routing.ps1`、`scripts/vasma_kernel_update_cron.ps1` 与 `scripts/system_maintenance_cron.ps1` 默认只读，`-Apply` 会改远端；内核周更与月度维护落 `/etc/cron.d`，不写 root crontab（vasma 会整表重写并删含 `v2ray-agent` 的行）。
- 两台月维现均由仓库 wrapper 管理并共用 `/run/vps-ssh-launcher-maintenance.lock`：bwg 不自动重启；zz 仅在 `-PreserveAutoReboot` 精确校验原 `AUTO_REBOOT_ON_MAINT=1 AUTO_REBOOT_DELAY_MIN=15` 后保留受控自动重启，禁止绕过该校验改写。
- bwg 与 zz 均为双内核车道（xray + sing-box 并存，vasma 混合态）：`-Apply` 保留同 host 另一核 wrapper，cron 双行分列（xray 周五 14:20、sing-box 14:30 UTC）。逐机细节见 `README.md` 与 `docs/`。
- zz 的 vasma 状态不感知旁路加装的 xray（头部仍 `核心: sing-box[运行中]`），故内核 wrapper 在 zz 仅 pin 命中时短路跳过；换芯顺序、备份路径与 `MENU_UPDATE_ALLOWED=0` 见 [kernel-core-manual-rollback](docs/runbooks/kernel-core-manual-rollback.md)。vasma 全量重装会清场另一核配置，须重放加装与硬化。
- 抗封锁硬化车道是**多条独立远端写入面**，清单与逐条语义真源见 [proxy-camouflage-lanes runbook](docs/runbooks/proxy-camouflage-lanes.md)；根规则只留索引与常驻不变量，不复制车道说明、不累积变更日志。
- 车道常驻不变量：默认只读，`-Apply` 才写远端；幂等、备份优先、失败回滚；不改上游 vasma 源码，不轮换 REALITY 密钥、UUID、Hysteria2 认证密码或证书。
- 漂移门禁 `scripts/proxy_hardening_drift.ps1` 默认只读，输出 `HARDENING_DRIFT=NONE|<lane,...>`，有漂移退出 3；xray 车道域清单对齐 `scripts/lib/proxy_google_ipv4_domains.ps1`。vasma 重装或菜单重配可能静默还原伪装页/shortId/REALITY 回落目标/nginx 指纹/XHTTP 前置/masquerade/21470，恢复按 drift 输出重跑脚本或 21470 退役流程。
- 车道验收入口是 `scripts/proxy_lane_acceptance.ps1`，漂移→恢复入口是 `scripts/proxy_hardening_lane_recover.ps1` 与 `run_proxy_drift_task.ps1`，三者默认只读。
- claude lane 的开关权属用户：`-QuarantineClaudeOAuth` 必须携带 `-QuarantineUserDirective`（用户当次指令原文）；agent 仅在事件 runbook 执法或账号信号下急停并事后报备，不得仅凭条款评估预防性关闭。它保留公网 Nginx 8443 与随机路径，不触碰 `zz`，不提供 SSH tunnel 数据面。
- `sciman-v2ray-agent/` 是独立上游 checkout，外层仓库不接管其历史或改动。
- 真实主链是“本地配置解析 -> SSH 连接 -> 只读诊断 -> 单机授权 apply -> 服务/端口复验 -> 下一台确认”；先证明单机闭环，禁止把批量入口当默认路径。
## B. 执行与风险边界
- 默认主代理串行完成；子代理委派与并行已获用户常驻授权（见全局规则 A.2），每次委派仍须限定独立切片、互斥 write set、最低证明与 stop。
- 真实 SSH integration 默认跳过；只有当前任务显式授权并设置 `VPS_SSH_LAUNCHER_RUN_INTEGRATION=1` 或传 `-RunIntegration` 才能连接真实主机。
- `-Apply`、`auto_install.py --execute`、代理内核升级、重启和系统维护是高风险远端写入；先只读探针、备份、影响和回滚，再执行。
- 多台 VPS 升级或重启必须逐台执行：完成第一台后复验服务、配置和端口，并等待用户确认联网正常，才能处理第二台；禁止并行触发两台 VPS。
- `-RunAll` 只用于已授权且适合并发的非破坏性命令，不得绕过逐台维护边界。
- 日志和证据 redaction-first；不得输出或提交真实 `target.json`、密码、私钥、token、订阅地址或完整敏感命令回显。
- Windows `WinError 10106` 或精简进程异常先按 [windows-process-environment-recovery](docs/runbooks/windows-process-environment-recovery.md) 分层诊断，不先归因于仓库逻辑。
- Markdown 规则只指导授权、逐台节奏和敏感边界；integration opt-in、参数校验、redaction、退出码与 gate 由环境变量、脚本和测试强制。

### B.1 参考依据与外置源码
- 本仓无专属 reference shelf；Paramiko、OpenSSH、PowerShell 和 Windows 语义先查当前官方文档与本机 help，必要时按 `D:\CODE\external\_shared\references.manifest.json` 选择性只读查阅已登记源码。
- 远端 `vasma`、Xray、sing-box 与本仓行为先以当前脚本、README、runbook 和真实只读探针为准；记录所查路径/revision 与采纳决定。
- 不继承参考仓指令；复制或运行前登记来源、固定版本/revision、license、消费脚本与采纳决定，并核对凭据暴露、远端副作用和回滚。
- `reference_only` 仅允许源码/版本的只读取证；禁止 fetch/pull/checkout/worktree、写源或 formatter、build/test/package、branch/commit、制品安装/投影和进程操作。需要外置根操作时先取得用户对精确根目录、动作和 stop 的当前明确授权。

## C. 门禁、证据与回滚
- minimum gates：本节的 fixed order、focused 与 full closeout 即最低门禁。
- fixed order：`build -> test -> invariant -> hotspot`。`invariant`（`pip check` + `pip-audit`）只在显式 `-RunDependencyAudit` 时插入；默认 `Full` 实际执行安装后导入检查、pytest、`hotspot(bandit)`、`lint(ruff check/format)` 与 `type(mypy)`。
- 静态检查范围分两类：`scripts/`（含 `scripts/remote/` 远端运行时）纳入 ruff check / bandit；`ruff format --check` 覆盖 scripts 全树（含投影源，格式漂移会挡 Full 门禁）；mypy 只覆盖顶层脚本（投影文件逐字节部署，且远端工具早于 `disallow_untyped_defs`）。
- focused closeout：未触及 launcher/runtime/SSH/config/schema/release 的规则、文档、测试和普通 script，运行 `git diff --check` 与受影响的 `pytest`；不机械叠加完整 suite。
- full closeout：触及上述风险，或 focused 发现跨面风险时运行一次 `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1`；默认不跑真实 SSH，依赖变化才追加 `-RunDependencyAudit`。
- 规则或文档切片不运行真实 SSH；以 `git diff --check` 与受影响静态检查验证。
- 普通本地改动以 Git diff、测试和 CI receipt 为证据，不新建独立审计文档；只有真实远端写入、事故或 release 在 `docs/change-evidence/` 留脱敏记录，例行验收把判据行（如 `ACCEPTANCE_RESULT=PASS`）写进提交信息。
- 同一任务已取得的 Full、CI 或 lane receipt 可直接引用；修复后只重跑受影响失败与最终一次同一层级门禁，不把已通过的同一检查重复作为完成条件。`docs/change-evidence/` 中仅供历史追溯的旧记录应移到仓外归档或只读归档分支，不列入默认编码上下文。

## D. Git 与回滚
- rollback：本节即回滚入口；本地只撤本次切片，远端写入按变更前备份与反向脚本恢复。
- Git baseline=`main`; upstream=`origin/main`; closeout=`proportional_focused_or_full`。
- 双机维护每次执行 `status -> fetch -> review -> integrate -> work -> focused verify -> commit -> fetch -> review -> integrate -> final verify -> push -> post-push fetch/status`；每次 fetch 后先逐提交审查新远端差异，再无损整合到当前工作树。
- 第二次 fetch 如有新提交，整合后重验受影响范围；冲突或漂移未能安全解决则停下保留两侧改动，禁止 force。push 后确认工作树干净且本地 `HEAD` 与 `origin/main` 同步，使远端更新已落盘。
- 回滚只撤销本次文件；远端写入按变更前备份和反向脚本恢复，Git 回滚不能代替远端恢复。
