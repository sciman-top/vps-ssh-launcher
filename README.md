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
| `-AllowAgent` | 使用 SSH Agent |
| `-StrictHostKeyChecking` | 拒绝未知主机密钥；默认模式把首次接受的密钥持久化到用户配置目录，后续密钥变化 fail-closed |
| `-RunAll` | 并发执行所有 profile，仅限非破坏性命令 |
| `-MaxWorkers <n>` | `-RunAll` 最大并发数，范围 `1-128` |
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

SSH 建连成功后，`run` 会原样返回远端退出码 `0-255`。`-RunAll` 会按 profile 输出结果与失败分类，并以最大退出码作为进程退出码。

单机 `run` 会增量输出 stdout/stderr，长命令不再等到退出后一次性回显；`-RunAll` 为保持各 profile 输出不交错，会在内存中按流最多保留 64K 字符，超出部分继续排空但不再累积。即使启用 `-Verbose`，远端命令正文也不会写入调试日志。

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

## 远端维护入口

### Google IPv4 路由

默认只读检查：

```powershell
.\scripts\google_ipv4_routing.ps1 -Profile example
```

只有确认远端修复脚本存在且确需重新应用时才执行：

```powershell
.\scripts\google_ipv4_routing.ps1 -Profile example -Apply
```

### vasma 内核周更

默认只读检查：

```powershell
.\scripts\vasma_kernel_update_cron.ps1 -Profile example -Kernel xray
```

显式写入 wrapper 与 cron：

```powershell
.\scripts\vasma_kernel_update_cron.ps1 -Profile example -Kernel xray -Apply
```

`-Apply`、代理内核升级、重启和系统维护必须逐台执行：先备份并只读探测第一台，执行后用第二条 SSH 命令复验服务、配置和端口，等待用户确认联网正常后才能处理下一台。不要用 `-RunAll` 绕过此边界。

### 高风险安装器

`auto_install.py` 会驱动远端 `/etc/v2ray-agent/install.sh`，不是健康检查。它默认阻断，只有显式授权后才能运行：

```bash
python -m pip install '.[installer]'
python ./auto_install.py --execute
```

该入口必须在目标 Linux 主机上运行。执行前至少备份相关代理与 Web 配置，并记录远端恢复方式。

## 排障与证据

精简宿主进程里的 `WinError 10106`、Python 启动失败或基础环境变量缺失，先按 [Windows 进程环境恢复](docs/runbooks/windows-process-environment-recovery.md) 排查。

普通本地改动以 Git diff、测试和 CI receipt 为证据，不再为每次变更新建审计文档。只有真实远端写入、事故或 release 才在 `docs/change-evidence/` 留脱敏记录。现存记录是历史 receipt，不代表当前主机仍处于相同状态；任何在线结论都必须重新只读探测。
