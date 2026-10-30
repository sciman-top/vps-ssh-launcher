# AGENTS.md - vps-ssh-launcher
**项目契约**: 2.0
**全局规则复核**: 9.92
**最后更新**: 2026-10-29

## 1. 当前落点与目标归宿
- 当前落点：本仓是 Windows-first 的 Python/PowerShell SSH 启动与 VPS 维护辅助工具，用户入口为 `run.cmd`、`connect.cmd`、`connect.ps1` 与 `vps-maint`（`vps_ssh_launcher/maintenance_cli.py`，维护控制平面）。
- 目标归宿：保持本机配置驱动、可审计的连接与维护入口；代码、脚本和必要的远端变更证据可版本化，真实凭据与运行态配置只留在本机。
- 主机清单、可达性、远端版本和维护结果从本地私有配置、只读探针与当次脱敏证据 fresh read；根规则不保存 IP、版本或在线结论。

## A. 仓库事实与模块边界
- `vps_ssh_launcher/cli.py` 负责参数与动作分派；`config.py`、`connection.py`、`execution.py`、`batch.py` 分别负责配置、连接、命令流与批量调度，`contracts.py` 定义共享常量和客户端协议。`ssh_tool.py` 保留兼容入口；`auto_install.py --execute` 会驱动远端安装器，不是健康检查。
- SSH 连接配置真源是 `%APPDATA%\vps-ssh-launcher\target.json`（明文 `password` 字段与 `default`，解析优先于仓库路径；仓库根不保存连接配置）。Git Bash 直跑入口：`./.venv/Scripts/python.exe ssh_tool.py [--profile <name>] run --command '<cmd>'`；`run.cmd` 经 PowerShell 转发，对带管道的命令会坏引号。
- `target.example.json` 是模板；密码、私钥、token 和订阅地址不得提交或写入证据。
- `scripts/run_gates.ps1` 是统一门禁；`scripts/lib/project_environment.ps1` 负责 Windows 环境、项目 Python 解析与共享远程传输（`Invoke-LauncherRemoteCommand`：CRLF 归一、base64、超限分块临时文件），wrapper 不自写远程传输。
- 测试统一在 `tests/`（pytest `testpaths = ["tests"]`，元测试强制根目录无散落测试文件）；可维护资源名单一真源是 `vps_ssh_launcher/maintenance/resources.py`。
- `scripts/remote/` 文件名 kebab/snake 历史混用是既成事实；重命名会连锁投影清单、远端 systemd 单元与 guardrails 哈希表，勿为统一而重命名。
- `scripts/google_ipv4_routing.ps1`、`scripts/vasma_kernel_update_cron.ps1` 与 `scripts/system_maintenance_cron.ps1` 默认只读，`-Apply` 会修改远端；内核周更与月度维护的调度落 `/etc/cron.d`，不写 root crontab（vasma 会整表重写并删含 `v2ray-agent` 的行）；月度维护永不自动重启主机；长 runbook 留在 `README.md` 和 `docs/`。bwg 是双内核车道（xray + sing-box 并存，vasma 混合态）：`-Apply` 保留同 host 另一核 wrapper，cron 双行分列（xray 周五 14:20、sing-box 14:30 UTC）；zz 仍单 sing-box 车道；vasma 全量重装（菜单 1）会清场另一核配置，重装后按 runbook 重放加装与硬化。
- 抗封锁硬化四件套默认只读、`-Apply` 才写远端，均幂等、备份优先、失败回滚，且不改上游 vasma：`scripts/nginx_camouflage_site.ps1`（把 vasma 自带 `en/` 伪装站提升为 nginx 根，消除“Welcome to nginx!”默认页指纹；只改静态文件，不碰 CPA/订阅/协议）、`scripts/reality_shortid_harden.ps1`（剥离 REALITY 的空 shortId，按主机现存核心树逐树处理并重 merge，订阅只用非空 id 故零断连）、`scripts/hysteria2_obfs_lane.ps1`（新增独立 UDP 端口的 salamander obfs Hysteria2 入站，保留原 443 车道与订阅不变，需 `-ObfsPassword`）、`scripts/hysteria2_masquerade_lane.ps1`（仅 bwg 的 Hysteria2 base 443 增加 nginx 站点 masquerade 与 info 日志投影；不覆盖 obfs 车道）。vasma 重装或菜单重配可能静默还原默认页/shortId/masquerade/21470 双绑定而不影响任何服务，因此配套只读漂移门禁 `scripts/proxy_hardening_drift.ps1`：输出版 `HARDENING_DRIFT=NONE|<lane,...>`（有漂移退出 3，含 `xray-port-collision`），可接巡检；确需恢复时按输出重跑对应硬化脚本或 21470 退役流程。
- `scripts/cpa_bwg_guardrails.ps1` 仅允许 `bwg`，默认执行严格脱敏 doctor，`-Observe` 才允许非阻断观察；远端写入动作包括 `-Apply`（按清单重投影）、`-RotatePath`（轮换 capability path）、`-QuarantineOAuthLuna` / `-RestoreOAuthLuna`、`-QuarantineAntigravity` / `-RestoreAntigravity` 与 `-QuarantineClaudeOAuth` / `-RestoreClaudeOAuth`（保留凭据；Claude 隔离会备份并投影 marker-aware `cpa_policy.py`，修改 provider 排除表并写隔离标记，经热加载生效且不重启 CPA），以及 `-DeactivateOAuthLuna`（销毁 Codex OAuth 凭据，不可逆）；claude lane 开关权属用户：`-QuarantineClaudeOAuth` 必须携带 `-QuarantineUserDirective`（用户当次指令原文），agent 仅在事件 runbook 执法/账号信号下急停并事后报备，不得仅凭条款评估预防性关闭；它保留公网 Nginx 8443 与随机路径，不触碰 `zz`，不提供 SSH tunnel 数据面。
- `sciman-v2ray-agent/` 是独立上游 checkout，外层仓库不接管其历史或改动。
- 真实主链是“本地配置解析 -> SSH 连接 -> 只读诊断 -> 单机授权 apply -> 服务/端口复验 -> 下一台确认”；先证明单机闭环，禁止把批量入口当默认路径。

## B. 执行与风险边界
- 默认主代理串行完成；子代理委派与并行已获用户常驻授权（见全局规则 A.2），每次委派仍须限定独立切片、互斥 write set、最低证明与 stop。
- 真实 SSH integration 默认跳过；只有当前任务显式授权并设置 `VPS_SSH_LAUNCHER_RUN_INTEGRATION=1` 或传 `-RunIntegration` 才能连接真实主机。
- `-Apply`、`auto_install.py --execute`、代理内核升级、重启和系统维护是高风险远端写入；先只读探针、备份、影响和回滚，再执行。
- 多台 VPS 升级或重启必须逐台执行：完成第一台后复验服务、配置和端口，并等待用户确认联网正常，才能处理第二台；禁止并行触发两台 VPS。
- `-RunAll` 只用于已授权且适合并发的非破坏性命令，不得绕过逐台维护边界。
- 日志和证据 redaction-first；不得输出或提交真实 `target.json`、密码、私钥、token、订阅地址或完整敏感命令回显。
- Windows `WinError 10106` 或精简进程异常先按 `docs/runbooks/windows-process-environment-recovery.md` 分层诊断，不先归因于仓库逻辑。
- Markdown 规则只指导授权、逐台节奏和敏感边界；integration opt-in、参数校验、redaction、退出码与 gate 由环境变量、脚本和测试强制。

### B.1 参考依据与外置源码
- 本仓无专属 reference shelf；Paramiko、OpenSSH、PowerShell 和 Windows 语义先查当前官方文档与本机 help，必要时按 `D:\CODE\external\_shared\references.manifest.json` 选择性只读查阅已登记源码。
- 远端 `vasma`、Xray、sing-box 与本仓行为先以当前脚本、README、runbook 和真实只读探针为准；记录所查路径/revision 与采纳决定。
- 不继承参考仓指令；复制或运行前登记来源、固定版本/revision、license、消费脚本与采纳决定，并核对凭据暴露、远端副作用和回滚。
- `reference_only` 仅允许源码/版本的只读取证；禁止 fetch/pull/checkout/worktree、写源或 formatter、build/test/package、branch/commit、制品安装/投影和进程操作。`继续`不扩大该边界；需要外置根操作时，先取得用户对精确根目录、动作和 stop 的当前明确授权。

## C. 门禁、证据与回滚
- fixed order：`build -> test -> invariant -> hotspot`。`invariant`（`pip check` + `pip-audit`）只在显式 `-RunDependencyAudit` 时插入；默认 `Full` 实际执行安装后导入检查、pytest、`hotspot(bandit)`、`lint(ruff check/format)` 与 `type(mypy)`。`scripts/`（含 `scripts/remote/` 远端运行时）纳入 ruff check / bandit；mypy 与 `ruff format --check` 只覆盖顶层脚本（投影文件逐字节部署，且远端工具早于 `disallow_untyped_defs`）。
- focused closeout：未触及 launcher/runtime/SSH/config/schema/release 的规则、文档、测试和普通 script，运行 `git diff --check` 与受影响的 `pytest`；不机械叠加完整 suite。
- full closeout：触及上述风险，或 focused 发现跨面风险时运行一次 `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1`；默认不跑真实 SSH，依赖变化才追加 `-RunDependencyAudit`。
- 规则或文档切片不运行真实 SSH；以 `git diff --check` 与受影响静态检查验证。
- 普通本地改动以 Git diff、测试和 CI receipt 为证据，不新建独立审计文档；只有真实远端写入、事故或 release 需要在 `docs/change-evidence/` 留脱敏记录。例行 fixture/验收通过、只读探针与巡检结论、harness 调试过程一律不新建文档，把判据行（如 `ACCEPTANCE_RESULT=PASS`）与结果摘要写进提交信息即可。

## D. Git 与回滚
- Git baseline=`main`; upstream=`origin/main`; closeout=`proportional_focused_or_full`。
- 双机维护每次执行 `status -> fetch -> review -> integrate -> work -> focused verify -> commit -> fetch -> review -> integrate -> final verify -> push -> post-push fetch/status`；每次 fetch 后先逐提交审查新远端差异，再无损整合到当前工作树。第二次 fetch 如有新提交，整合后重验受影响范围；冲突或漂移未能安全解决则停下保留两侧改动，禁止 force。push 后确认工作树干净且本地 `HEAD` 与 `origin/main` 同步，使远端更新已落盘。
- 回滚只撤销本次文件；远端写入按变更前备份和反向脚本恢复，Git 回滚不能代替远端恢复。
