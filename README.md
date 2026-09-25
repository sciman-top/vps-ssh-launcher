# vps-ssh-launcher

Windows-first 的 Python/PowerShell SSH 启动器，面向少量 VPS 的连接、只读诊断和经授权的逐台维护。

核心边界：

- 真实配置与凭据只留在本机，仓库只提供 `target.example.json`。
- 默认动作是连接检查；真实 SSH 集成、安装器和远端写入都必须显式启用。
- `-RunAll` 只适合已授权的非破坏性命令；升级、重启和系统维护必须逐台执行。
- `sciman-v2ray-agent/` 是独立上游 checkout，不属于本仓版本历史。

## 快速开始

```powershell
.\run.cmd
```

首次运行会在 `%APPDATA%\vps-ssh-launcher\target.json` 创建模板并退出。填写真实主机信息后再次运行：

```powershell
.\run.cmd -Profile example
.\run.cmd -Profile example -Command "uname -a"
```

`run.cmd` 只是 `connect.cmd` 的短别名；调用链是：

```text
run.cmd -> connect.cmd -> connect.ps1 -> ssh_tool.py -> vps_ssh_launcher/cli.py
```

注意：该 cmd 转发链对带管道/`&&`/多级引号的复杂命令会损坏引号（转义 `\"`
必然坏）。这类命令改用 Git Bash 直跑
`./.venv/Scripts/python.exe ssh_tool.py [--profile <name>] run --command '<cmd>'`，
或把脚本 base64 后在远端解码执行。

`run` 支持 `--command-timeout <秒>`（默认 60，命令提交阶段也受此限制，开始输出后按
"无输出空闲"计时，有输出自动续期；`0` 关闭）与 `--command-hard-timeout <秒>`
（从命令提交开始计算的绝对上限）。静默长命令
（如 fixture 的 62 秒等待段、慢模型生成）必须显式调大或置 0，否则 60 秒即被
本地掐断——脱离会话的变通口径见 change-evidence 20260917 各篇。

## 配置与认证

默认配置查找顺序：

1. `%APPDATA%\vps-ssh-launcher\target.json`
2. 仓库根 `target.json`（旧版兼容）

示例：

```json
{
  "profiles": {
    "example": {
      "host": "YOUR_VPS_IP",
      "port": 22,
      "user": "root",
      "password_env": "VPS_EXAMPLE_PASSWORD"
    }
  },
  "default": "example"
}
```

支持 `password_env`、本机 `password`、`key` 或运行时 `-AllowAgent` / `-Key`。运行时认证参数优先于 profile 配置。相对 `key` 路径按配置文件所在目录解析。

不要提交或记录真实 `target.json`、密码、私钥、token、订阅地址或敏感命令完整回显。

## 常用参数

| 参数 | 行为 |
|---|---|
| `-Config <path>` | 指定配置文件 |
| `-Profile <name>` | 选择 profile |
| `-Command <text>` | 执行远端命令；省略时只检查连接 |
| `-CommandTimeout <seconds>` | idle timeout，默认 `60`，`0` 表示禁用 |
| `-CommandHardTimeout <seconds>` | 绝对 timeout，默认 `0` 表示禁用 |
| `-Key <path>` | 使用指定私钥 |
| `--password-stdin` | 从 stdin 读取一行 SSH 密码，避免密码出现在进程列表；优先使用此方式 |
| `-AllowAgent` | 使用 SSH Agent |
| `-StrictHostKeyChecking` | 拒绝未知主机密钥；默认模式把首次接受的密钥持久化到用户配置目录，后续密钥变化 fail-closed |
| `-RunAll` | 并发执行所有 profile；实现层只接受固定诊断命令及只读 `systemctl` 查询，写入/脚本命令必须逐台执行 |
| `-AllowGlobalBootstrap` | 明确允许向非隔离 Python 安装依赖 |
| `-Verbose` | 输出调试日志 |

退出码：

| 码 | 含义 |
|---|---|
| `0` | 成功 |
| `1` | SSH / 认证错误 |
| `2` | 配置错误 |
| `3` | 连接超时 |
| `4` | 网络错误 |
| `5` | 本地命令驱动、远端命令 timeout 或内部错误 |

SSH 建连成功后，`run` 会原样返回远端退出码 `0-255`。`-RunAll` 会按 profile 输出结果与失败分类，并以最大退出码作为进程退出码。注意 `-RunAll` 的聚合退出码混合了两个命名空间：本地类别码 `1-5`（认证/配置/超时等）与远端命令码 `0-255`，按最大值返回，因此本地网络错误（`4`）可能被更大的远端码掩盖；需要区分时逐 profile 看失败分类输出。

单机 `run` 会增量输出 stdout/stderr，长命令不再等到退出后一次性回显；`-RunAll` 为保持各 profile 输出不交错，会在内存中按流最多保留 64K 字符，超出部分继续排空但不再累积。即使启用 `-Verbose`，远端命令正文也不会写入调试日志。

`-RunAll` 的固定诊断命令为 `uptime`、`uname`/`uname -a`、`df`/`df -h`/`df -Pk`、
`free`/`free -h`/`free -m`、`hostname`、`id`、`whoami`、`pwd`、`true`、`test`、
`ss -ltn`/`ss -ltnp`、`ps aux`、`docker ps`/`docker images`/`docker version`；另允许
`systemctl cat|is-active|is-enabled|show|status <literal-unit...>`。其他命令即使可读也应
通过单机 `run` 执行，避免多用途命令的写入参数绕过批量边界。

## Python 与 PowerShell

入口优先使用：

1. `VPS_SSH_LAUNCHER_PYTHON`
2. `.venv\Scripts\python.exe`
3. PATH 中的 `python`

`connect.cmd` 要求 PowerShell 7；可用 `VPS_SSH_LAUNCHER_POWERSHELL` 指定已批准的启动器，不再静默回退到 Windows PowerShell 5.1。共用的 Windows 环境与 Python 解析位于 `scripts\lib\project_environment.ps1`。

如果缺少 Paramiko，或现有版本不满足 `>=5,<6`，`connect.ps1` 只会在隔离环境中自动安装或升级。命中全局 Python 时默认拒绝，除非显式传入 `-AllowGlobalBootstrap`。

运行时依赖固定在已验证的 Paramiko 5.x。该主版本移除了不安全的 RSA/SHA-1 签名及部分旧密钥、KEX 和 GSSAPI 兼容路径；仍依赖这些算法的旧主机应先升级 SSH 配置，不通过降级客户端恢复连接。真实主机的密钥和算法兼容性需单独验收。

## 开发与验证

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

快速反馈：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

统一本地门禁：

```powershell
.\scripts\run_gates.ps1
```

默认门禁只执行一次必要证明：Python 编译、pytest、Bandit、Ruff lint/format 和 Mypy。它覆盖 `vps_ssh_launcher/` 的真实实现，不重复运行同一组 unittest，也不重复叠加第二套类型检查器。

只有依赖文件变化或供应链复核时追加：

```powershell
.\scripts\run_gates.ps1 -RunDependencyAudit
```

这会额外执行 `pip check` 与 `pip-audit`，要求隔离项目 Python。常规 CI 只在一个组合执行依赖审计；兼容性矩阵保留 Windows/Python 3.11 与 Ubuntu/Python 3.13 两个互补组合。

## 真实 SSH 集成

默认测试会跳过真实 SSH。显式验收时：

```powershell
$env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = "1"
$env:VPS_SSH_LAUNCHER_INTEGRATION_CONFIG = "$env:APPDATA\vps-ssh-launcher\target.json"
$env:VPS_SSH_LAUNCHER_INTEGRATION_PROFILE = "example"
.\.venv\Scripts\python.exe -m pytest -q test_integration_real_ssh.py
```

也可以通过统一入口传入集成参数：

```powershell
.\scripts\run_gates.ps1 `
  -RunIntegration `
  -IntegrationConfig "$env:APPDATA\vps-ssh-launcher\target.json" `
  -IntegrationProfile "example"
```

GitHub Actions 的真实 SSH workflow 只运行固定的无副作用 round-trip，不接受自定义远端命令。启用前必须在 `vps-production` Environment 中配置 required reviewer，以及 `VPS_SSH_LAUNCHER_INTEGRATION_TARGET_JSON` 和经过带外核验的 `VPS_SSH_LAUNCHER_INTEGRATION_KNOWN_HOSTS` 两个 environment secrets。临时 runner 强制严格 host-key 校验。

真实 SSH、主机在线状态和远端服务效果是独立验收层；本地 gate 通过不能外推为 live accepted。

## 本地维护控制平面

控制平面负责“声明状态 → 只读 inventory → 确定性 plan → 受控 apply → 脱敏 receipt”，不把仓库变成任意远端 Shell 执行器。入口为：

    Copy-Item .\maintenance.example.toml "$env:APPDATA\vps-ssh-launcher\maintenance.toml"
    vps-maint inventory --run-integration --target-config "$env:APPDATA\vps-ssh-launcher\target.json" --output .\inventory.json
    vps-maint plan --config "$env:APPDATA\vps-ssh-launcher\maintenance.toml" --inventory-file .\inventory.json
    vps-maint history --config "$env:APPDATA\vps-ssh-launcher\maintenance.toml"

真实 inventory 必须同时显式传入 --run-integration 并设置
$env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = "1"；CLI 和 `connect.ps1` 默认启用严格 host-key 校验，只有显式传入 `--allow-unknown-host-key` 或 `-AllowUnknownHostKey` 才进入兼容性的 TOFU 模式。inventory 命令是固定只读探针。状态默认保存在 %APPDATA%\vps-ssh-launcher\maintenance.db，receipt 默认保存在同目录的 maintenance-receipts\，不保存密码、私钥、token、订阅地址或完整远端命令。

`vps-maint apply` 默认是 dry-run，即使计划为 `planned` 也不会连接或写入远端。必须同时使用
`--yes --remote-write --run-integration`，并设置
`$env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = "1"`，才会进入单 profile、单主机、串行的远端边界；执行前会重新收集 inventory 并要求 fingerprint 与计划完全一致（指纹只覆盖身份 fact：内核、架构、OS、核心 SHA-256、镜像 digest、容器清单、监听端口、服务后端、vasma 脚本/源配置哈希和重启要求等；磁盘百分比、内存等易变遥测不参与，良性漂移不会迫使重建计划，身份 fact 变化仍会按设计拒绝并要求重建），连接继续使用严格 host-key 校验。Xray 只接受显式版本和 SHA-256 pin，当前通用 adapter 明确只允许 `x86_64/amd64` 的 `Xray-linux-64.zip`，执行下载校验、备份、配置测试、重启、读回和失败回滚；非 CPA Docker 只接受绝对 Compose 路径、服务 allowlist 和 image digest pin，执行 Compose config、pull/up、健康与 digest 读回及失败回滚。没有 pin、fingerprint 漂移、CPA 路径/服务/镜像标识、架构不匹配或任一门禁失败都会阻断。CPA、provider 和凭据维护继续使用既有的 BWG 专用 guardrail/runbook，不会被通用 Docker adapter 接管。

升级示例（先把 `xray = "present"` 改为 `xray = "upgrade"` 并补齐 pin；当前示例默认不会升级）：

    $env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = "1"
    vps-maint plan --config "$env:APPDATA\vps-ssh-launcher\maintenance.toml" --live-inventory --run-integration --target-config "$env:APPDATA\vps-ssh-launcher\target.json" --profile bwg --output .\upgrade-plan.json
    vps-maint apply --config "$env:APPDATA\vps-ssh-launcher\maintenance.toml" --plan-id <plan-id> --yes --remote-write --run-integration --target-config "$env:APPDATA\vps-ssh-launcher\target.json" --profile bwg

本仓还提供了 PowerShell 7 的本地落盘入口。它每次都要求 fresh inventory，默认只生成
plan 和本地运行日志，不包含远端 apply：

    pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\vps_maintenance.ps1 -RunIntegration

可安装一个每日 `20:00` 的静默观察任务（任务只带 `-RunIntegration`，不会带
`-Apply` 或 `-RemoteWrite`；任务和 PowerShell 窗口均隐藏，日志仍保存在本地）：

    pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_vps_maintenance_task.ps1 -WhatIf
    pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_vps_maintenance_task.ps1

高风险无人值守 apply 不是默认行为。只有在本地策略显式设置
`[automation] mode = "unattended_apply"`、写入精确的
`acknowledge = "I_ACKNOWLEDGE_BWG_SINGLE_HOST_AUTOMATION"`，并确认
`profiles = ["bwg"]`、资源 allowlist、`20:00–22:00` 维护窗口和尝试上限后，才可用
`-AutoApply` 重新投影任务：

    pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_vps_maintenance_task.ps1 -Replace -AutoApply -At 20:00

`-AutoApply` 只会在 fresh plan 恰好包含一个新的显式 pin action 时进入远端边界；策略漂移、旧计划、窗口外、并发锁、重复 pin、非 BWG、多个 action、连接或验证失败都会 fail closed。Xray/Docker 适配器负责远端 backup、apply、状态读回和失败 rollback；每个 pin 最多尝试一次，失败后需要人工复核或更换 pin。任务仍静默运行，失败通过本地日志、receipt 和非零 Task Scheduler 结果暴露。

观察任务名为 `VPS-SshLauncher-BWG-Observe`，日志和计划保存在
`%LOCALAPPDATA%\vps-ssh-launcher\maintenance-runs\`。保持默认观察模式时，真实远端写入仍需人工显式执行：

    pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\vps_maintenance.ps1 -RunIntegration -Apply -RemoteWrite

若要撤销观察任务：

    pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_vps_maintenance_task.ps1 -Remove

## 远端维护入口

### bwg CPA 公网网关防护

bwg 上的 CPA（CLIProxyAPI）经 Nginx 8443 + 随机 capability path 对公网提供
OpenAI 兼容入口，容器只绑定 `127.0.0.1:8317`；`scripts/cpa_bwg_guardrails.ps1`
是该主机的唯一 guardrail 入口（默认只读，不触碰 `zz`）。部署形态、路由清单、
更新器策略、doctor 门禁与应急开关的全部操作细节见
[CPA 网关运行手册](docs/runbooks/cpa-gateway.md)。

固定不变量（改动需用户显式决策）：

- 公网 Nginx TLS 入口 + 随机路径，不改为 SSH tunnel、VPN 或仅内网监听。
- 路由唯一事实源是 `scripts/remote/cpa_provider_routes.json`；槽位 3 的明文
  HTTP 中转（`http://35.213.82.91:8003/v1`）是用户明确保留的精确例外。
- 更新策略：patch 自动收编（双源 72h 成熟期）、minor/major 仅记录 canary
  候选、跨 major 永不自动、永不降级。
- 应急登出 `-DeactivateOAuthLuna` 是唯一的凭据销毁入口：不可逆、不备份，
  登出后目录契约由清单派生断言校验。

日常入口速览：

```powershell
# 默认：严格 doctor；失败即非零退出
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg
# 单机 apply（先 doctor、确认影响与回滚）
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -Apply
```

`-Observe`、`-RotatePath`、`-DeactivateOAuthLuna`、`-ConsumeUsageQueue` 的语义
与限制在运行手册内展开。该入口与每日 updater、系统维护、内核维护和通用远端
adapter 共用 `/run/vps-ssh-launcher-maintenance.lock` 的 `flock -n` 互斥。

上游冷却状态陈旧（[#5639](https://github.com/router-for-me/CLIProxyAPI/issues/5639)、
[#5770](https://github.com/router-for-me/CLIProxyAPI/issues/5770)）的恢复口径见
[cpa-stale-cooldown-recovery.md](docs/runbooks/cpa-stale-cooldown-recovery.md)，
2026-09-16 起 `save-cooldown-status: false`（纯内存冷却、重启即清）。

### CPA 流量分配与账号暴露边界

2026-09-21 确立的客户端分流顺序（降低唯一 OAuth 账号暴露，优先于任何服务端
限流调整）：

- DeepSeek 官方 API：可批处理、可重试、非敏感重负载与成本敏感任务的首选。
- GLM Coding Plan：仅承载符合其条款的编码工作负载，不当通用聚合后端。
- luna（ChatGPT Plus OAuth）：保留给交互式、高价值、低并发请求；客户端可先做
  单账号同时 1 个长请求的 semaphore，仅当自然流量持续超出该预算时再评估
  独立入口或按 lane 限流。
- ai.input.im（sol/terra/astra）：非敏感备用，不承载关键主链。

容灾通道顺序（未实施；接入前必须先有明确消费者与故障切换规则）：官方
Gemini API key → 其他官方按量 API → 官方 Gemini OAuth → 第二个第三方中转。
服务器侧 OAuth 全局并发闸门暂缓：现有观测是上游 502/503 引发单凭据冷却，
不是并发过高的确定性证据，且共享的全局 Nginx 限额会误伤 glm/deepseek 独立
通道。明确不做：定时缓存落盘治理面、第二 Codex 账号轮换、冷却/重试再调参、
identity-confuse。

### Google IPv4 路由

默认只读检查：

```powershell
.\scripts\google_ipv4_routing.ps1 -Profile example
```

只有确认远端修复脚本存在且确需重新应用时才执行：

```powershell
.\scripts\google_ipv4_routing.ps1 -Profile example -Apply -RemoteApplySha256 <sha256>
```

### vasma 内核周更

默认只读检查：

```powershell
.\scripts\vasma_kernel_update_cron.ps1 -Profile example -Kernel xray
```

准备好经过官方发布页核验的版本和 SHA-256 后再显式写入 wrapper 与 cron；这里的哈希是 vasma 安装完成后对应架构的实际二进制文件哈希：

```powershell
.\scripts\vasma_kernel_update_cron.ps1 -Profile example -Kernel xray -Version 26.3.27 -InstalledSha256 <installed-binary-sha256> -VasmaSha256 <vasma-script-sha256> -Apply
```

sing-box 使用相同的 `-Version`/`-InstalledSha256` pin；`-VasmaSha256` 固定部署版菜单脚本，
脚本仍通过 vasma 的 `16.core管理` 菜单执行，不直接替换上游下载链。只读模式可以省略 pin，
`-Apply` 不能省略。

```powershell
.\scripts\vasma_kernel_update_cron.ps1 -Profile example -Kernel sing-box -Version 1.12.0 -InstalledSha256 <installed-binary-sha256> -VasmaSha256 <vasma-script-sha256> -Apply
```

菜单管道输入与部署版 vasma 的提示位置强耦合：两个 wrapper 在驱动 vasma 前会先校验
部署版脚本 hash、菜单行、分发函数与中文/英文更新提示锚点，任一缺失即以 exit 12/13
拒绝；只读 readout 会输出部署版脚本 hash 与锚点在位状态。若远端通过菜单 17 或重装
更新了 vasma，必须重新读取并 pin 新 hash 后重投影 wrapper，旧 wrapper 会拒绝运行。

zz 约束：zz 的内核 wrapper 是旧模板变体（无锚点预检、无 pin 强制），与原生
`auto_system_maint.sh` 共用旧锁 `/run/v2ray-agent-maint.lock`；**禁止用当前
仓库模板对 zz `-Apply` 重投影**——模板锁路径硬编码为统一新锁，重投影会破坏
zz 原生维护互斥。zz 的 sing-box 升级前置步骤见
[sing-box 配置迁移](docs/runbooks/singbox-core-update-migration.md)，内核升级
后的人工回滚见 [内核人工回滚](docs/runbooks/kernel-core-manual-rollback.md)。

`-Kernel xray -Apply` 会移除 sing-box 的自动更新 wrapper（反之亦然）：一台主机同一时刻只对一条内核 lane 做周更，双内核主机需要拆分为两次显式操作。sing-box 内核从旧版基线首次升级（≥1.12）前必须先人工迁移配置，见 [sing-box 内核升级前的配置迁移](docs/runbooks/singbox-core-update-migration.md)；wrapper 的 fail-closed 回滚是安全网而非替代步骤。

`-Apply`、代理内核升级、重启和系统维护必须逐台执行：先备份并只读探测第一台，执行后用第二条 SSH 命令复验服务、配置和端口，等待用户确认联网正常后才能处理下一台。不要用 `-RunAll` 绕过此边界。
`-Apply` 必须显式提供版本、已安装二进制 SHA-256 和 vasma 脚本 SHA-256 pin；wrapper
会在下载前备份当前二进制，升级后复验版本、哈希、配置和服务，失败时恢复二进制及
sing-box 配置目录并报告 `ROLLBACK_VERIFIED`。缺少 pin、latest 漂移或校验失败都会
fail closed。它还会先备份两个 wrapper 与当前 crontab；写入、语法复验或 cron 安装失败
会恢复备份并报告 `ROLLBACK_VERIFIED`/`ROLLBACK_FAILED`，成功时输出 `APPLY_BACKUP_DIR`
供后续人工回滚。它仍必须逐台执行，不能替代升级后的真实服务与端口复验。

调度写入 `/etc/cron.d/vps-launcher-kernel-update`（含 `root` 用户位），不写 root crontab：vasma 的证书定时任务会整表重写 crontab 并删除所有含 `v2ray-agent` 的行（2026-09-24 在 bwg 实际发生过），`/etc/cron.d` 不受其影响；`-Apply` 会同时把旧的 crontab 行迁出。

### v2ray-agent 管理脚本周更

`scripts/v2ray_agent_script_update_cron.ps1` 只更新 mack-a/v2ray-agent 的管理脚本
`/etc/v2ray-agent/install.sh`，不执行脚本菜单、不调用 `vasma`、不重装 Xray 或
sing-box，也不重启代理服务。远端 updater 从官方 HTTPS raw 地址获取候选，做文件大小、
`bash -n`、版本标记和 `coreVersionManageMenu`/`xrayVersionManageMenu`/`17.更新脚本`
锚点校验，然后在同一文件系统内原子替换。替换后只读复验现有 Xray、Nginx、fail2ban
服务和 Xray 配置；失败恢复 `/var/backups/v2ray-agent-script-update.*` 中的旧脚本。

先读当前远端脚本 hash：

```powershell
$env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = "1"
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\v2ray_agent_script_update_cron.ps1 `
  -Profile bwg
```

确认 hash 来自 fresh inventory 后，再投影长期 cron（默认每周五 14:40 UTC）：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\v2ray_agent_script_update_cron.ps1 `
  -Profile bwg -InstallSha256 <fresh-install.sh-sha256> -Apply
```

投影写入 `/usr/local/sbin/vps-launcher-v2ray-agent-update.sh` 和
`/etc/cron.d/vps-launcher-v2ray-agent-update`，与其他维护入口共用
`/run/vps-ssh-launcher-maintenance.lock`。cron 运行 `--apply`；`--check` 只下载、
校验并复验服务，不替换脚本。该更新器只校验官方地址和结构，不等同于对上游 master
每次变更的人工源码审查；若需要代理配置或核心恢复，必须另走高风险
`auto_install.py`/vasma 流程，不能把脚本周更当作重装恢复。

详细回滚和受控验收步骤见
[v2ray-agent 管理脚本更新 runbook](docs/runbooks/v2ray-agent-script-update.md)。

### 月度系统维护

每月 1 日执行一次 apt 升级与清理（update + upgrade + autoremove --purge + autoclean + 30 天 journal vacuum）。调度使用服务器本地时间：UTC 主机用默认值即北京时间 22:00；主机本身运行在 UTC+8 时传 `-Schedule '0 22 1 * *'`。

默认只读检查：

```powershell
.\scripts\system_maintenance_cron.ps1 -Profile example
```

显式写入 wrapper 与 cron：

```powershell
.\scripts\system_maintenance_cron.ps1 -Profile example -Apply
```

脚本永不自动重启主机；需要重启时只在 `/var/log/monthly-maintenance.log` 记录
`reboot required`，由人工决定。apt 阶段后会按已检测到的 Xray、sing-box、Nginx、fail2ban
服务执行配置和服务复验，任何失败都以非零码退出。它与内核周更、CPA updater 和本地
远端 adapter 共用 `/run/vps-ssh-launcher-maintenance.lock`，不会并行执行；调度与内核周更
同理写入 `/etc/cron.d/vps-launcher-monthly-maintenance`，不依赖 root crontab。

### 高风险安装器

`auto_install.py` 会驱动远端 `/etc/v2ray-agent/install.sh`，不是健康检查。它默认阻断，只有显式授权后才能运行：

```bash
python -m pip install '.[installer]'
python ./auto_install.py --execute --install-script-sha256 <sha256>
```

该入口必须在目标 Linux 主机上运行。执行前至少备份相关代理与 Web 配置，并记录远端恢复方式。默认必须提供当前 `/etc/v2ray-agent/install.sh` 的 SHA-256；`--allow-unpinned-script` 只适用于已完成带外源码审查的人工一次性运行，不得用于无人值守。执行还要求 `VPS_DOMAIN=<域名>` 环境变量（安装器以它预期域名提示，仓库内不内置域名）。

## 排障与证据

精简宿主进程里的 `WinError 10106`、Python 启动失败或基础环境变量缺失，先按 [Windows 进程环境恢复](docs/runbooks/windows-process-environment-recovery.md) 排查。

普通本地改动以 Git diff、测试和 CI receipt 为证据，不再为每次变更新建审计文档。只有真实远端写入、事故或 release 才在 `docs/change-evidence/` 留脱敏记录。现存记录是历史 receipt，不代表当前主机仍处于相同状态；任何在线结论都必须重新只读探测。
