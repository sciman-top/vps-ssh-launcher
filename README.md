# vps-ssh-launcher

一个极轻量的多 VPS SSH 启动器。

## 它做什么
- 从用户配置目录的 `target.json` 读取多个 VPS profile
- 交互选择或指定某个 profile
- 连接后测试连通性或执行远程命令
- 支持对所有 profile 并行执行

## 文件说明

| 文件 | 用途 |
|------|------|
| `target.json` | 用户本地连接配置，默认位于 `%APPDATA%\\vps-ssh-launcher\\` |
| `target.example.json` | 示例配置模板 |
| `ssh_tool.py` | 主逻辑 |
| `connect.cmd` | 主入口，通过 PowerShell 启动 |
| `connect.ps1` | 启动脚本，自动检测 Python / 安装依赖 |
| `run.cmd` | `connect.cmd` 的短别名 |
| `requirements.txt` | Python 依赖 |
| `scripts/google_ipv4_routing.ps1` | Google/Gemini 代理出口 IPv4 固定规则的远端复查入口 |
| `scripts/vasma_kernel_update_cron.ps1` | 通过远端 `vasma` 菜单安装/复查代理内核周更任务 |

## Python 环境

- CI 同时验证 `Python 3.11` 和 `Python 3.13`。
- 本地命令默认优先使用仓库内 `.venv\Scripts\python.exe`。
- 也可以显式设置 `VPS_SSH_LAUNCHER_PYTHON` 指向你想用的解释器。
- 如果既没有 `.venv`，也没有设置环境变量，才回退到系统 `python` / `py`。
- `connect.cmd` 默认优先使用 PowerShell 7 (`pwsh.exe`)，避免落回 Windows PowerShell 5.1；如需指定启动器，可设置 `VPS_SSH_LAUNCHER_POWERSHELL`。

## 配置格式

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

最少要求：
- `profiles` 里放一个或多个 profile
- 每个 profile 至少包含 `host` 和 `user`
- 认证方式可以使用 `password_env`、`password`、`key` 或运行时 `-AllowAgent` / `-Key`
- 运行时认证参数优先于 profile 默认认证：`-Key` 使用指定私钥，`-AllowAgent` 只走 SSH Agent，不读取 profile 中的 `password_env` / `password` / `key`
- `default` 可选，不写时会交互选择
- `password` 和 `key` 可以本地明文配置，只放在本机 `target.json`
- `key` 使用相对路径时，按当前配置文件 (`target.json`) 所在目录解析
- 仓库内保留 `target.example.json` 作为模板
- 本项目明确保留 `password` 登录和 `root` 直登，不强制改成非 root 用户或仅密钥登录
- 这套工具面向日常运维和常驻服务场景，配置上优先保证可用性和直连效率

## 使用方法

### 基本连接
```powershell
.\connect.cmd                              # 交互选择 VPS，测试连通性
.\connect.cmd -Profile bwg                 # 指定 profile，测试连通性
```

### 执行远程命令
```powershell
.\connect.cmd -Command "uname -a"          # 交互选择 VPS，执行命令
.\connect.cmd -Profile zz -Command "uptime"
.\run.cmd -Profile bwg -Command "whoami"
```

### 所有 VPS 并行执行
```powershell
.\connect.cmd -Command "uptime" -RunAll
.\connect.cmd -Key "$env:USERPROFILE\.ssh\id_ed25519" -Command "uptime" -RunAll
```

### 其他选项

| 选项 | 说明 |
|------|------|
| `-Verbose` | 显示调试日志 |
| `-Key <path>` | 指定 SSH 私钥文件 |
| `-AllowAgent` | 使用 SSH Agent 认证 |
| `-StrictHostKeyChecking` | 拒绝未知主机密钥 |
| `-Config <path>` | 指定配置文件路径 |
| `-CommandTimeout <seconds>` | 远程命令 idle timeout；默认 60 秒，`0` 表示禁用 |

## 退出码

| 码 | 含义 |
|----|------|
| 0 | 成功，或远程命令返回 0 |
| 1 | SSH / 认证错误 |
| 2 | 配置错误 |
| 3 | 连接超时 |
| 4 | 网络错误 |
| 5 | 远程命令执行错误 |

`run` 命令会原样返回远程退出码（0-255）。

## 使用提示
- 如果没有 Python，启动脚本会提示安装
- 如果缺少 `paramiko`，启动脚本会自动安装依赖
- 允许本地明文密码/密钥路径，但只限个人机器上的 `target.json`
- 优先使用 `password_env` 或 `key`；避免把密码写在命令行参数里，命令行可能被系统进程列表或日志记录
- 配置文件里不要提交密码或私钥内容
- 新增 VPS 时，只需要在本地 `target.json` 里添加一个 profile
- 只想检查连通性时，直接运行不带 `-Command` 的连接命令
- 多个 profile 行为不同，先检查本地 `target.json` 的默认项和认证方式
- 长时间静默的维护任务应显式加大 `-CommandTimeout`，或让远端命令周期性输出 heartbeat，避免本地 SSH 客户端先断开 stdout 管道
- 每台 VPS 可以独立放置自动升级任务；代理内核周更必须通过远端 `vasma` / v2ray-agent 菜单执行，不在本项目里手写 GitHub release 下载替换逻辑

## 开发与验证

安装开发门禁依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

如果你要本地复现 CI 的最低支持版本，再单独安装 `Python 3.11` 并用它创建 `.venv`。

运行完整本地门禁：

```powershell
.\scripts\run_gates.ps1
```

`run_gates.ps1` 中的 `pip check` / `pip-audit` 会检查整个解释器环境。
本地应使用 `.venv` 或设置 `VPS_SSH_LAUNCHER_PYTHON` 指向本项目专用解释器；
只有确认全局 Python 专用于本仓时，才使用 `-AllowGlobalPython` 覆盖。

## VPS 代理内核周更任务

本仓的周更任务只负责在远端放置一个薄 wrapper，真正的代理内核更新必须由 VPS 上已有的
`vasma` 脚本执行：

- `bwg`：只启用 Xray-core 周更，菜单路径是 `16.core管理 -> 1.Xray-core -> 1.升级Xray-core`。
- `zz`：只启用 sing-box 周更，菜单路径是 `16.core管理 -> 2.sing-box -> 1.升级 sing-box`。

默认只读复查：

```powershell
.\scripts\vasma_kernel_update_cron.ps1 -Profile bwg -Kernel xray
.\scripts\vasma_kernel_update_cron.ps1 -Profile zz -Kernel sing-box
```

写入或修正远端 wrapper 与 cron：

```powershell
.\scripts\vasma_kernel_update_cron.ps1 -Profile bwg -Kernel xray -Apply
.\scripts\vasma_kernel_update_cron.ps1 -Profile zz -Kernel sing-box -Apply
```

默认计划是 `0 14 * * 5`，两台主机当前使用 `Etc/UTC`，对应北京时间每周五 22:00。
`-Apply` 会清理另一种内核的周更 cron，避免 `bwg` 触发 sing-box 或 `zz` 触发 Xray。

### 手动触发高风险任务

代理内核更新会短暂停止 `xray` 或 `sing-box`。手动触发这类任务时必须逐台执行：

1. 只触发第一台 VPS 的 wrapper。
2. 用第二条 SSH 命令复验服务、配置和端口。
3. 等待用户确认联网正常后，再触发第二台 VPS。

禁止并行触发两台 VPS 的代理内核更新或重启类动作，避免两个代理出口同时断网。手动触发 wrapper
时，远端命令只能包含 wrapper 本身：

```powershell
.\run.cmd -Profile bwg -Command "/bin/bash /etc/v2ray-agent/auto_update_xray.sh"
```

不要在同一条远端命令里串联 `/etc/v2ray-agent/xray/xray run -test ...` 或
`/etc/v2ray-agent/sing-box/sing-box check ...`。`vasma` 内部会用 `pgrep -f` 判断残留进程，
同一条 shell 命令中的检查路径可能被误判为内核进程未退出。复验必须用第二条命令执行。

### Windows 进程环境异常

如果在 Codex、CI 包装脚本或精简 PowerShell 进程里看到下面现象，不要先假定项目代码坏了：

- `python -c "import asyncio"` 报 `WinError 10106`
- `node -e "console.log('node ok')"` 报 `ncrypto::CSPRNG`
- `rg`、`cmd`、`Start-Process` 或 `pyright` 行为异常

这类问题通常是当前进程缺少 Windows 基础环境变量，而不是仓库逻辑错误。重点检查：
`ComSpec`、`SystemRoot`、`WINDIR`、`APPDATA`、`LOCALAPPDATA`、`PROGRAMDATA`。
本仓 `connect.ps1` 与 `scripts/run_gates.ps1` 会在启动时补齐缺失变量，并在 `pip-audit` / `pyright`
前验证 Python `asyncio` 与 Node CSPRNG。若普通管理员 PowerShell 也失败，再执行系统级修复：

```powershell
netsh winsock reset
netsh int ip reset
ipconfig /flushdns
shutdown /r /t 0
```

### Google/Gemini unusual traffic 与双栈出口漂移

如果 Gemini 或 Google 返回类似下面的拦截信息：

```text
Our systems have detected unusual traffic from your computer network.
IP address: <IPv4> != <IPv6>
URL: https://gemini.google.com/
```

优先按宿主网络 / 代理出口问题处理，不要先归因到本仓代码。常见根因是远端代理服务器同时具备 IPv4 和 IPv6 公网出口，浏览器同一会话中的 Google/Gemini 请求被服务端观察到两个不同出口身份。

本仓提供一个可选复查入口，默认只读检查，不会修改远端：

```powershell
.\scripts\google_ipv4_routing.ps1 -Profile bwg
```

检查内容包括：

- 远端 Xray 版本和 `systemctl is-active xray`
- `/etc/systemd/system/xray.service.d/20-google-ipv4-routing.conf`
- `/etc/v2ray-agent/apply-google-ipv4-routing-config.sh`
- `/etc/v2ray-agent/reapply-google-ipv4-routing.sh`
- `/etc/v2ray-agent/xray/conf/09_routing.json` 中的 `gemini.google.com` / `google_ipv4_out`
- `/etc/v2ray-agent/xray/conf/98_google_ipv4_outbound.json` 中的 `ForceIPv4`
- `xray run -test -confdir /etc/v2ray-agent/xray/conf`
- 远端 IPv4 / IPv6 public egress

只有确认远端已经具备 `/etc/v2ray-agent/reapply-google-ipv4-routing.sh`，且需要重新应用规则时，才显式执行：

```powershell
.\scripts\google_ipv4_routing.ps1 -Profile bwg -Apply
```

`-Apply` 会调用远端已有修复脚本，然后再执行同一组复查。它不会读取或输出真实密码、私钥或 token。

修复目标不是禁用整台 VPS 的 IPv6，而是让 Google/Gemini 相关代理流量稳定走 `ForceIPv4` 出站，避免同一会话出现 IPv4/IPv6 身份不一致。若 v2ray-agent 或 Xray 更新后再次出现拦截，先运行只读检查；如果 `ExecStartPre`、`google_ipv4_out` 或 `ForceIPv4` 丢失，再运行 `-Apply`。

真实 SSH 集成测试默认跳过，避免误连生产 VPS。需要显式开启时：

```powershell
$env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = "1"
$env:VPS_SSH_LAUNCHER_INTEGRATION_CONFIG = "target.json"
$env:VPS_SSH_LAUNCHER_INTEGRATION_PROFILE = "example"
python -m pytest -q test_integration_real_ssh.py
```

默认集成命令是 `printf vps-ssh-launcher-integration`，可用
`VPS_SSH_LAUNCHER_INTEGRATION_COMMAND` 和
`VPS_SSH_LAUNCHER_INTEGRATION_EXPECTED` 覆盖。

`run_gates.ps1` 也支持直接注入集成参数：

```powershell
.\scripts\run_gates.ps1 `
  -RunIntegration `
  -IntegrationConfig "target.json" `
  -IntegrationProfile "example" `
  -IntegrationCommand "printf vps-ssh-launcher-integration" `
  -IntegrationExpected "vps-ssh-launcher-integration"
```

### GitHub Actions 真实 SSH 集成

- 常规 CI（`.github/workflows/ci.yml`）默认不触发真实 SSH，避免误连生产环境。
- 需要真实联通回归时，手动触发 `.github/workflows/integration-real-ssh.yml`。
- 在仓库 `Secrets and variables -> Actions` 中配置：
  - `VPS_SSH_LAUNCHER_INTEGRATION_TARGET_JSON`：完整 JSON 字符串，内容格式与 `target.json` 一致。
- 触发工作流时可选填写 `integration_profile`，默认使用配置中的 `default`。

## 安全与仓库边界

- `target.json` 是本机敏感配置，已被 `.gitignore` 排除。
- `sciman-v2ray-agent/` 是独立上游 fork checkout，外层仓库不接管它；如需版本化，应单独维护或显式转换为 submodule。
- 默认允许未知主机密钥以保持 copy-and-run 体验；需要更严格安全边界时使用 `-StrictHostKeyChecking`。
- Bandit 安全豁免记录在 `docs/security-waivers.md`，需要按过期日期复审。

## VPS 日常运行建议

如果这两台 VPS 会长期跑常驻服务、代理、隧道或转发，建议优先做下面这些最小优化：

- 保持 SSH 可达和认证稳定，避免把入口配置改得过于复杂
- 打开并固定服务自启，确保重启后自动恢复
- 配置进程守护和失败重启，减少偶发退出带来的人工干预
- 配置日志轮转，避免长期运行后磁盘被日志占满
- 监控磁盘、内存、负载和网络丢包，先观察再调参
- 只有在出现断连、超时、CPU/内存/IO 明显瓶颈时，再针对性调整系统参数

## 高风险安装器边界

`auto_install.py` 会驱动远端 `/etc/v2ray-agent/install.sh`，不是健康检查工具。
它可能重写 Xray、nginx、订阅和端口配置。默认直接运行会阻断，必须显式传入：

```powershell
python auto_install.py --execute
```

执行前必须先做远端快照，至少覆盖 `/etc/v2ray-agent`、`/etc/nginx` 和当前
`systemctl status xray nginx`。如果出现未知提示、超时或连接中断，先停止残留
`install.sh` 进程，再检查 `xray`、`nginx`、Reality target 端口和订阅文件，不要连续重跑。
