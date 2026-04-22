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

## 配置格式

```json
{
  "profiles": {
    "example": {
      "host": "YOUR_VPS_IP",
      "port": 22,
      "user": "root",
      "password": "YOUR_PASSWORD"
    }
  },
  "default": "example"
}
```

最少要求：
- `profiles` 里放一个或多个 profile
- 每个 profile 至少包含 `host` 和 `user`
- 认证方式二选一：`password` 或 `key`
- `default` 可选，不写时会交互选择
- `password` 和 `key` 可以本地明文配置，只放在本机 `target.json`
- 仓库内保留 `target.example.json` 作为模板
- 本项目明确保留 `password` 登录和 `root` 直登，不强制改成非 root 用户或仅密钥登录
- 这套工具面向日常运维和常驻服务场景，配置上优先保证可用性和直连效率

## 使用方法

### 基本连接
```powershell
.\connect.cmd                              # 交互选择 VPS，测试连通性
.\connect.cmd -Profile us-datacenter-1     # 指定 profile，测试连通性
```

### 执行远程命令
```powershell
.\connect.cmd -Command "uname -a"          # 交互选择 VPS，执行命令
.\connect.cmd -Profile us-residential-1 -Command "uptime"
.\run.cmd -Profile us-datacenter-1 -Command "whoami"
```

### 所有 VPS 并行执行
```powershell
.\connect.cmd -Command "uptime" -RunAll
```

### 其他选项

| 选项 | 说明 |
|------|------|
| `-Verbose` | 显示调试日志 |
| `-Key <path>` | 指定 SSH 私钥文件 |
| `-AllowAgent` | 使用 SSH Agent 认证 |
| `-StrictHostKeyChecking` | 拒绝未知主机密钥 |
| `-Config <path>` | 指定配置文件路径 |

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
- 配置文件里不要提交密码或私钥内容
- 新增 VPS 时，只需要在本地 `target.json` 里添加一个 profile
- 只想检查连通性时，直接运行不带 `-Command` 的连接命令
- 多个 profile 行为不同，先检查本地 `target.json` 的默认项和认证方式
- 每台 VPS 可以独立放置自动升级任务，例如 Xray-core 周更检查

## VPS 日常运行建议

如果这两台 VPS 会长期跑常驻服务、代理、隧道或转发，建议优先做下面这些最小优化：

- 保持 SSH 可达和认证稳定，避免把入口配置改得过于复杂
- 打开并固定服务自启，确保重启后自动恢复
- 配置进程守护和失败重启，减少偶发退出带来的人工干预
- 配置日志轮转，避免长期运行后磁盘被日志占满
- 监控磁盘、内存、负载和网络丢包，先观察再调参
- 只有在出现断连、超时、CPU/内存/IO 明显瓶颈时，再针对性调整系统参数
