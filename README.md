# vps-ssh-launcher

一个面向个人运维场景的轻量 SSH / VPS 启动辅助工具。它保留明文密码、私钥路径和 `root` 直连这些本机实用模式，同时把批量执行、真实 SSH 集成、Windows 启动链和高风险远端脚本边界收敛到一套可验证入口。

## 当前状态

- 当前版本：`1.1.1`
- 用户入口：`run.cmd` / `connect.cmd` / `connect.ps1`
- 核心实现：`vps_ssh_launcher/cli.py`（`ssh_tool.py` 为兼容入口）
- 本地完整门禁：`scripts/run_gates.ps1`
- CI：`.github/workflows/ci.yml` 在 `windows-latest` / `ubuntu-latest` × `Python 3.11` / `Python 3.13` 执行完整门禁
- 真实 SSH 集成：默认跳过，只在显式 `VPS_SSH_LAUNCHER_RUN_INTEGRATION=1` 或手动触发 `.github/workflows/integration-real-ssh.yml` 时运行
- 最新现场证据：`docs/change-evidence/20260528-bwg-live-function-maintenance.md`、`docs/change-evidence/20260528-zz-singbox-system-maint.md`

## 快速开始

第一次使用时，优先把配置放在 `%APPDATA%\vps-ssh-launcher\target.json`。如果该文件不存在，`connect.cmd` 会自动创建模板。

```powershell
.\connect.cmd
```

模板与推荐格式如下：

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

填完配置后，常用入口如下：

```powershell
.\connect.cmd                              # 交互选择 VPS，默认执行 check
.\connect.cmd -Profile bwg                 # 指定 profile 做连通性检查
.\run.cmd -Profile zz -Command "whoami"    # 通过 wrapper 执行单台远端命令
.\connect.cmd -Command "uptime" -RunAll    # 对所有 profile 并行执行
```

## 命令速查

| 命令 | 用途 |
|------|------|
| `.\connect.cmd` | 交互选择 profile 并执行 `check` |
| `.\connect.cmd -Profile bwg` | 指定 profile 做连通性检查 |
| `.\run.cmd -Profile bwg -Command "uptime"` | 执行单台远端命令 |
| `.\connect.cmd -Command "uptime" -RunAll -MaxWorkers 2` | 对所有 profile 并行执行，并限制并发数 |
| `.\scripts\run_gates.ps1` | 运行完整本地门禁 |
| `.\scripts\google_ipv4_routing.ps1 -Profile bwg` | 只读复查 Google/Gemini IPv4 出站规则 |
| `.\scripts\vasma_kernel_update_cron.ps1 -Profile zz -Kernel sing-box` | 只读复查远端 `vasma` 周更 wrapper / cron |
| `python .\auto_install.py --execute` | 显式执行高风险远端安装器，默认会被 guard 阻断 |

## 项目结构

| 路径 | 说明 |
|------|------|
| `run.cmd` | `connect.cmd` 的短别名 |
| `connect.cmd` | Windows 启动入口，优先选择 `pwsh.exe` |
| `connect.ps1` | 配置发现、模板生成、Python 解析、依赖自安装、参数转发 |
| `ssh_tool.py` | 兼容入口，转发到包内实现 |
| `vps_ssh_launcher/cli.py` | CLI 主逻辑，负责配置解析、SSH 建连、`run` / `check` / `run_all` |
| `auto_install.py` | 驱动远端 `/etc/v2ray-agent/install.sh` 的高风险安装器 |
| `scripts\run_gates.ps1` | `build -> test -> contract/invariant -> hotspot` 的本地统一门禁入口 |
| `scripts\lib\project_environment.ps1` | Windows 进程环境补齐与项目 Python 解析共用 helper |
| `scripts\google_ipv4_routing.ps1` | Google/Gemini 双栈出口漂移的远端只读复查 / 显式修复入口 |
| `scripts\vasma_kernel_update_cron.ps1` | 通过远端 `vasma` 菜单安装/复查 Xray 或 sing-box 周更 wrapper |
| `target.example.json` | 配置模板；真实 `target.json` 默认放在 `%APPDATA%\vps-ssh-launcher\` |
| `docs\change-evidence\` | 每次代码或运维变更的证据归档 |
| `docs\security-waivers.md` | Bandit 等安全豁免与复审依据 |
| `docs\governed-runtime-batch-validation.md` | 当前批量验证基线与最新现场验证索引 |
| `docs\runbooks\windows-process-environment-recovery.md` | Windows 进程环境异常排障 runbook |
| `docs\governance\entrypoint-promotion.md` | governed runtime 入口收敛状态与升级条件 |

## Python 与 PowerShell 解析规则

- 本地命令默认优先使用仓库内 `.venv\Scripts\python.exe`
- 也可以显式设置 `VPS_SSH_LAUNCHER_PYTHON` 指向你想用的解释器
- 如果既没有 `.venv`，也没有设置环境变量，才回退到系统 `python`
- `connect.ps1`、`run_gates.ps1`、`google_ipv4_routing.ps1`、`vasma_kernel_update_cron.ps1` 都复用 `scripts\lib\project_environment.ps1`
- `connect.cmd` 默认优先使用 PowerShell 7 (`pwsh.exe`)；如需指定启动器，可设置 `VPS_SSH_LAUNCHER_POWERSHELL`
- `connect.ps1` 缺依赖时默认只允许在隔离 `.venv` 中安装；如果命中 PATH/global Python，必须显式传入 `-AllowGlobalBootstrap`

## 配置与认证

- 配置文件默认解析顺序是 `%APPDATA%\vps-ssh-launcher\target.json`，然后才是仓库根 `target.json`
- `profiles` 中每个 profile 至少要有 `host` 和 `user`
- 认证方式支持 `password_env`、`password`、`key`，或者运行时 `-AllowAgent` / `-Key`
- 运行时认证参数优先于 profile 默认认证：`-Key` 使用指定私钥，`-AllowAgent` 只走 SSH Agent，不读取 profile 中的 `password_env` / `password` / `key`
- `default` 是可选字段；未提供时，交互场景会弹出选择，非交互场景必须显式传入 `--profile`
- `key` 使用相对路径时，只按当前配置文件所在目录解析；不再回退尝试当前工作目录
- 本项目明确保留本机明文 `password`、明文 `key` 路径和 `root` 直登，不强制改成非 `root` 用户或仅密钥登录
- 真实凭据只允许留在本机 `target.json` 或环境变量，不要提交到仓库，也不要写进证据文件

## CLI 行为

### 基本动作

- 不带 `-Command` 时，wrapper 默认执行 `check`
- 带 `-Command` 时执行 `run`
- `-CommandTimeout` 默认 `60` 秒，`0` 表示禁用 idle timeout
- `-CommandHardTimeout` 默认 `0`，表示禁用绝对超时；设置后作为独立 hard timeout 生效
- `-RunAll` 会并行跑所有 profile；未指定 `-MaxWorkers` 时，默认并发数是 `min(profile_count, 32)`

### `-RunAll` 汇总输出

`-RunAll` 会先按 profile 打印每台主机的标准输出 / 错误，再输出 `[summary]` 汇总，便于批量维护时快速判断失败面：

```text
[summary] profiles=3 ok=2 failed=1 elapsed=1.23s
[summary] auth_error: 1
[summary] max_exit_code: 1
[summary] exit_code_histogram: 0=2, 1=1
[summary] failed_profiles: example
```

常见失败分类包括 `auth_error`、`network_error`、`connect_timeout`、`config_error`、`command_timeout`、`remote_nonzero` 和 `internal_error`。`max_exit_code` 是所有 profile 返回码中的最大值，也是 `-RunAll` 的进程退出码；`failed_profiles` 只列出非 0 的 profile。

### 退出码

| 码 | 含义 |
|----|------|
| `0` | 成功，或远端命令返回 `0` |
| `1` | SSH / 认证错误 |
| `2` | 配置错误 |
| `3` | 连接超时 |
| `4` | 网络错误 |
| `5` | 本地命令执行错误、远端命令 timeout、或 worker 内部错误 |

`run` 在 SSH 建连成功后会原样返回远端退出码（`0-255`）；只有本地命令驱动失败、idle timeout 或内部异常时，才回退到仓库级退出码 `5`。

### 其他选项

| 选项 | 说明 |
|------|------|
| `-Verbose` | 打开调试日志 |
| `-Key <path>` | 指定 SSH 私钥文件 |
| `-AllowAgent` | 使用 SSH Agent 认证 |
| `-StrictHostKeyChecking` | 拒绝未知主机密钥 |
| `-Config <path>` | 指定配置文件路径 |
| `-CommandTimeout <seconds>` | 远端命令 idle timeout；默认 60 秒，`0` 表示禁用 |
| `-CommandHardTimeout <seconds>` | 远端命令绝对超时；默认 `0` 表示禁用 |
| `-RunAll` | 并行执行所有 profile |
| `-MaxWorkers <n>` | 控制 `-RunAll` 的最大并发数，范围 `1-128` |
| `-AllowGlobalBootstrap` | 允许在非隔离 Python 上安装依赖；默认拒绝 |

## 开发与验证

### 开发环境

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

如果你要本地复现 CI 的最低支持版本，再单独安装 `Python 3.11` 并用它创建 `.venv`。

### 快速反馈

```powershell
python -m pytest -q
python -m unittest -q
python .\.governed-ai\verify-powershell-policy.py
```

### 完整本地门禁

```powershell
.\scripts\run_gates.ps1
```

完整门禁会按固定顺序执行：

1. `build`: `python -m compileall -q ...`
2. `test`: `python -m pytest -q`
3. `contract`: `python -m unittest -q` 与 `python .\.governed-ai\verify-powershell-policy.py`
4. `invariant/hotspot/lint/type`: `pip check`、`pip-audit`、Bandit、Vulture、Ruff、Mypy、Pyright

`run_gates.ps1` 中的 `pip check` / `pip-audit` 会检查整个解释器环境。本地应使用 `.venv` 或设置 `VPS_SSH_LAUNCHER_PYTHON` 指向本项目专用解释器；只有确认全局 Python 专用于本仓时，才使用 `-AllowGlobalPython` 覆盖。

### 真实 SSH 集成

真实 SSH 集成默认跳过，避免误连生产 VPS。需要显式开启时：

```powershell
$env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = "1"
$env:VPS_SSH_LAUNCHER_INTEGRATION_CONFIG = "$env:APPDATA\vps-ssh-launcher\target.json"
$env:VPS_SSH_LAUNCHER_INTEGRATION_PROFILE = "example"
python -m pytest -q test_integration_real_ssh.py
```

默认集成命令是 `printf vps-ssh-launcher-integration`，可用 `VPS_SSH_LAUNCHER_INTEGRATION_COMMAND` 和 `VPS_SSH_LAUNCHER_INTEGRATION_EXPECTED` 覆盖。

`run_gates.ps1` 也支持直接注入集成参数：

```powershell
.\scripts\run_gates.ps1 `
  -RunIntegration `
  -IntegrationConfig "$env:APPDATA\vps-ssh-launcher\target.json" `
  -IntegrationProfile "example" `
  -IntegrationCommand "printf vps-ssh-launcher-integration" `
  -IntegrationExpected "vps-ssh-launcher-integration"
```

省略 `-IntegrationConfig` 时，`run_gates.ps1` 与主 launcher 一样优先使用 `%APPDATA%\vps-ssh-launcher\target.json`，再回退到仓库根的 `target.json`。如果该配置包含多个 profile 且没有 `default`，必须显式传入 `-IntegrationProfile`，避免真实 SSH gate 落入交互式选择。

### GitHub Actions

- 常规 CI：`.github/workflows/ci.yml`
- 真实 SSH 集成：`.github/workflows/integration-real-ssh.yml`
- Actions Secret：`VPS_SSH_LAUNCHER_INTEGRATION_TARGET_JSON`
- `integration-real-ssh.yml` 可选输入：
  - `integration_profile`
  - `integration_command`
  - `integration_expected`
  - `skip_dependency_audit`

## 运维脚本与风险边界

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

它会核对：

- 远端 Xray 版本与 `systemctl is-active xray`
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

### VPS 代理内核周更任务

本仓的周更任务只负责在远端放置一个薄 wrapper，真正的代理内核更新必须由 VPS 上已有的 `vasma` 脚本执行：

- `bwg`：只启用 Xray-core 周更，菜单路径是 `16.core管理 -> 1.Xray-core -> 1.升级Xray-core`
- `zz`：只启用 sing-box 周更，菜单路径是 `16.core管理 -> 2.sing-box -> 1.升级 sing-box`

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

默认计划是 `20 14 * * 5`，两台主机当前使用 `Etc/UTC`，对应北京时间每周五 22:20；这会避开每月 1 日 22:00 的系统维护任务，防止同一把维护锁导致其中一个任务跳过。`-Apply` 会清理另一种内核的周更 cron，避免 `bwg` 触发 sing-box 或 `zz` 触发 Xray。Xray / sing-box wrapper 会先按 `vasma` 当前脚本可见的稳定版窗口做预检；如果稳定版为空或与当前版本相同，只复验现有服务和配置，不自动确认空版本下载或同版本重装。

### 手动触发高风险任务

代理内核更新、重启类任务和真实系统维护都属于高风险操作，必须逐台执行：

1. 只触发第一台 VPS 的 wrapper。
2. 用第二条 SSH 命令复验服务、配置和端口。
3. 等待用户确认联网正常后，再触发第二台 VPS。

禁止并行触发两台 VPS 的代理内核更新或重启类动作，避免两个代理出口同时断网。手动触发 wrapper 时，远端命令只能包含 wrapper 本身：

```powershell
.\run.cmd -Profile bwg -Command "/bin/bash /etc/v2ray-agent/auto_update_xray.sh"
```

不要在同一条远端命令里串联 `/etc/v2ray-agent/xray/xray run -test ...` 或 `/etc/v2ray-agent/sing-box/sing-box check ...`。`vasma` 内部会用 `pgrep -f` 判断残留进程，同一条 shell 命令中的检查路径可能被误判为内核进程未退出。复验必须用第二条命令执行。

### 高风险安装器边界

`auto_install.py` 会驱动远端 `/etc/v2ray-agent/install.sh`，不是健康检查工具。它可能重写 Xray、nginx、订阅和端口配置。默认直接运行会阻断，必须显式传入：

```powershell
python .\auto_install.py --execute
```

执行前必须先做远端快照，至少覆盖 `/etc/v2ray-agent`、`/etc/nginx` 和当前 `systemctl status xray nginx`。`--expect-timeout` 只允许 `1-59` 秒，避免未知交互提示拖到 launcher 的远端 idle timeout。自动化提示响应现在只输出脱敏 transcript 摘要，不再直接透传安装器原始回显；仍不要把真实密码、私钥、订阅地址或 token 粘贴进证据文件。

### Windows 进程环境异常

如果在 Codex、CI 包装脚本或精简 PowerShell 进程里看到下面现象，不要先假定项目代码坏了：

- `python -c "import asyncio"` 报 `WinError 10106`
- `node -e "console.log('node ok')"` 报 `ncrypto::CSPRNG`
- `rg`、`cmd`、`Start-Process` 或 `pyright` 行为异常

这类问题通常是当前进程缺少 Windows 基础环境变量，而不是仓库逻辑错误。重点检查：`ComSpec`、`SystemRoot`、`WINDIR`、`APPDATA`、`LOCALAPPDATA`、`PROGRAMDATA`。本仓入口脚本会先补齐这些变量，再执行 Python / Node 相关探针。详细排障步骤见 `docs/runbooks/windows-process-environment-recovery.md`。

## 安全与仓库边界

- `target.json` 是本机敏感配置，已被 `.gitignore` 排除
- `sciman-v2ray-agent/` 是独立上游 fork checkout，外层仓库不接管它；如需版本化，应单独维护或显式转换为 submodule
- 默认允许未知主机密钥以保持 copy-and-run 体验；运行时会保留显式兼容告警，需要更严格安全边界时使用 `-StrictHostKeyChecking`
- Bandit 安全豁免记录在 `docs/security-waivers.md`，需要按过期日期复审

## 文档与证据

- `docs/change-evidence/`：每次代码、门禁或现场运维的证据归档
- `docs/governed-runtime-batch-validation.md`：最新批量验证基线与现场验证索引
- `docs/security-waivers.md`：当前保留的安全折中项及复审计划
- `docs/runbooks/windows-process-environment-recovery.md`：Windows 进程环境异常排障步骤
- `docs/governance/entrypoint-promotion.md`：governed runtime 入口收敛状态、阻断条件与升级条件
