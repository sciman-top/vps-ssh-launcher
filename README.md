# vps-ssh-launcher

<p align="center">
  <strong>⚡ Windows-first 的高效 SSH 启动器 · 声明式 VPS 维护控制平面 · 生产级 AI 网关防护工具栈</strong>
</p>

<p align="center">
  <a href="README.en.md">English Documentation</a> | <a href="#-为什么选择-vps-ssh-launcher">特性优势</a> | <a href="#-快速上手">快速上手</a> | <a href="#-三大核心能力架构">核心架构</a> | <a href="#-声明式维护控制平面-vps-maint">控制平面</a> | <a href="#-生产级-ai-网关防护-cpa-guardrails">AI 网关守护</a> | <a href="#-运行手册导航">手册导航</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-0078D6?logo=windows" alt="Platform">
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/PowerShell-7%2B-5391FE?logo=powershell" alt="PowerShell">
  <img src="https://img.shields.io/badge/SSH%20Core-Paramiko%205.x-green" alt="Paramiko">
  <img src="https://img.shields.io/badge/Code%20Style-Ruff-black?logo=ruff" alt="Ruff">
  <img src="https://img.shields.io/badge/Type%20Check-Mypy-blue" alt="Mypy">
  <img src="https://img.shields.io/badge/Security-Bandit-red" alt="Bandit">
</p>

---

## 📖 简介

`vps-ssh-launcher` 是一个专为 Windows 与多环境运维打造的 **Python/PowerShell SSH 启动器、声明式 VPS 维护控制平面与生产级 AI 网关（CLIProxyAPI）防护工具栈**。

它旨在解决 Windows 环境下管理少量 VPS 时的命令转义混乱、凭据安全边界脆弱、批量操作易误伤、缺乏声明式版本回滚机制，以及运维高并发 AI API 逆向/中转网关时频繁遭遇的“429 迷局”与封号风险。

> [!TIP]
> **本仓定义了三条清晰独立的产品边界**：
> 1. **通用 SSH 启动器**：告别 Windows 引号转义地狱，提供文件级纯净传输、SOCKS5 隧道、超时保活与白名单安全并发诊断。
> 2. **声明式 VPS 维护控制平面 (`vps-maint`)**：遵循 `State -> Inventory -> Plan -> Apply -> Receipt` 闭环，支持 Xray 与 Docker 容器精准 Pin、环境指纹防漂移与自动回滚。
> 3. **AI 网关防护与调度栈 (CPA Guardrails)**：专为 CLIProxyAPI 生产部署打造，首创四层 429 故障精准归因、四独立 Lane 并发管控、零停机可逆账号隔离与系统排他锁保护。

---

## ✨ 为什么选择 vps-ssh-launcher？

针对日常 Linux VPS 运维与 AI 网关搭建中的典型痛点，`vps-ssh-launcher` 提供了开箱即用的工程化解法：

| 运维痛点 | 传统方式面临的问题 | vps-ssh-launcher 的解法 |
| --- | --- | --- |
| **Windows 下的命令“转义地狱”** | CMD / PowerShell 的引号、管道（`\|`）、`&&`、换行在 SSH 转发中频频崩坏 | **原生 `-CommandFile` 支持**：将复杂 Shell 写入本地 UTF-8 文件，通过内置 Base64/分块传输引擎纯净直达远端，告别转义冲突 |
| **凭据泄露与命令误伤** | 配置文件意外提交到 Git；批量并发操作导致多台机器一并宕机 | **凭据物理隔离 + 严格只读白名单**：真实凭据只留本地 `%APPDATA%`；默认均为只读探针；`-RunAll` 并发模式仅允许白名单只读诊断命令，高危变更强制逐台人工确认 |
| **服务升级缺乏确定性与回滚保障** | 依赖一键脚本盲目升级，遇到环境变动或配置冲突无法回滚 | **声明式维护控制平面 (`vps-maint`)**：基于 SQLite 的状态跟踪，强制精确版本/镜像 SHA-256 Pin，执行前核对事实指纹（Fingerprint），失败全自动原子回滚 |
| **AI 网关与多账号并发“429 迷局”** | 客户端返回 429，分不清是本机限流、Nginx 限制、队列超时还是上游封禁 | **四层 429 智能故障归因**：结合请求等待预算与访问日志，按层定位根因；四条物理隔离的 Admission Lane，单模超载不波及全局 |
| **高价值 OAuth 账号封号连带风险** | 测试新模型或突发流量导致唯一 ChatGPT/Claude/Gemini 账号被全局封禁 | **零停机可逆账号隔离（Quarantine）**：一键将高危账号从可路由目录中安全隔离，保留凭据且无需重启服务，杜绝无节制重试引发连带风险 |
| **多任务 Crontab 冲突与被冲刷** | 自动化运维脚本互相打架，或第三方脚本重写 crontab 导致定时任务丢失 | **系统级分布式排他锁 + `/etc/cron.d/`**：统一持有 `/run/vps-ssh-launcher-maintenance.lock` 互斥锁，定时任务独立落盘，免疫配置重写 |

---

## 🏛️ 三大核心能力架构

```text
+---------------------------------------------------------------------------------+
|                         Local Host (Windows / Linux)                            |
|                                                                                 |
|  +-----------------------+  +-------------------------+  +-------------------+  |
|  |  Universal Launcher   |  |   Declarative Control   |  |   CPA Gateway     |  |
|  |                       |  |   Plane (vps-maint)     |  |   Guardrails      |  |
|  |  • run.cmd            |  |  • Inventory (Readonly) |  |  • 429 Triage     |  |
|  |  • connect.ps1        |  |  • Plan (Fingerprint)   |  |  • Risk Audit     |  |
|  |  • -CommandFile       |  |  • Apply (Pin/Rollback) |  |  • Lane Admission |  |
|  |  • -RunAll (Safe)     |  |  • Receipts (SQLite)    |  |  • Quarantine     |  |
|  +-----------+-----------+  +------------+------------+  +---------+---------+  |
|              |                           |                         |            |
|              +---------------------------+-------------------------+            |
|                                          |                                      |
|                       Local Auth: %APPDATA%/target.json                         |
|                       Paramiko 5.x / SOCKS5 / SSH Agent                         |
+------------------------------------------+--------------------------------------+
                                           |  Encrypted SSH (Base64/Chunked)
                                           v
+---------------------------------------------------------------------------------+
|                              Remote Linux VPS                                   |
|                                                                                 |
|    [ Unified Maintenance Lock: /run/vps-ssh-launcher-maintenance.lock (flock) ] |
|                                                                                 |
|  +---------------------------+  +---------------------+  +-------------------+  |
|  | Core Services             |  | AI Gateway (CPA)    |  | Scheduled Tasks   |  |
|  | • Xray / sing-box         |  | • Nginx (TLS 8443)  |  | (/etc/cron.d/)    |  |
|  | • Docker Compose Stacks   |  | • Admission (8318)  |  | • Kernel Updates  |  |
|  | • v2ray-agent (Pinned)    |  | • CPA Daemon (8317) |  | • Monthly Apt     |  |
|  +---------------------------+  +---------------------+  +-------------------+  |
+---------------------------------------------------------------------------------+
```

### 模块边界与功能落地矩阵

SSH 核心按职责拆分；现有命令、退出码和 `ssh_tool.py` 入口保持兼容。
新增代码直接使用所属能力模块，`cli.py` 中的公开函数重导出用于兼容已有 Python 调用方。
包初始化只加载共享契约，调用 `main()` 时才加载 CLI。
launcher 单测按模块落位：`test_config.py` / `test_connection.py` / `test_execution.py` /
`test_batch.py` 对应四个能力模块，`test_ssh_tool.py` 只保留 CLI 解析、`main()` 与兼容
导入面，共享替身与 patch 助手在 `tests/launcher_fakes.py`；新增测试写入对应文件。

| 落点 | 职责 | 增删功能时的对应验证 |
| --- | --- | --- |
| `vps_ssh_launcher/contracts.py` | 默认值、退出码、远端客户端协议 | 检查所有消费者及兼容性 |
| `vps_ssh_launcher/config.py` | 本地配置、profile 选择、认证优先级 | 配置错误、CLI 覆盖、相对密钥路径 |
| `vps_ssh_launcher/connection.py` | SSH 生命周期、主机密钥、连接重试 | 严格信任、失败清理、只重试瞬时网络错误 |
| `vps_ssh_launcher/socks5.py` | 独立的 SOCKS5 CONNECT 握手与失败清理 | IPv4/IPv6/域名、截断响应、SSH 字节流对齐 |
| `vps_ssh_launcher/execution.py` | 命令提交、stdout/stderr 排空、超时与捕获上限 | 双流、UTF-8 分段、空闲/总超时、关闭通道 |
| `vps_ssh_launcher/batch.py` | 只读准入、并发上限、逐主机结果与汇总 | 写命令拒绝、输出限额、失败分类、稳定汇总 |
| `vps_ssh_launcher/cli.py` | 参数解析、动作分派、终端错误与退出码 | 命令行兼容、入口错误映射 |
| `vps_ssh_launcher/maintenance_cli.py` | `vps-maint` 子命令分派、apply 高危边界与退出码 | `tests/test_maintenance.py` 的 CLI/apply 流程 |
| `vps_ssh_launcher/maintenance/workflow.py` | 复核单台主机计划、执行 adapter 并逐步落盘 | 连接失败、结果标记、adapter 中断与客户端关闭 |
| `vps_ssh_launcher/maintenance/` | 策略、inventory、计划、状态、adapter、收据与无人值守约束 | `tests/test_maintenance.py` 中对应资源的计划/执行/回滚 |
| `vps_ssh_launcher/maintenance/resources.py` | 统一登记资源的 pin 校验、升级计划和命令构建 | 新增/删除资源的配置→计划→执行联动 |
| `vps_ssh_launcher/maintenance/fingerprint.py` | 规范 JSON 与 inventory 身份指纹，不依赖 SSH | volatile telemetry 不改变身份；监听器变化须重建计划 |
| `vps_ssh_launcher/maintenance/planner.py` / `models.py` / `state.py` | 计划构建与状态转换、状态契约、持久化边界验证 | profile 范围、未知状态拒绝、历史计划兼容 |
| `vps_ssh_launcher/maintenance/xray.py` / `docker.py` | 各资源的 pin schema、升级判断、命令与回滚 | 对应资源的计划、执行、失败恢复 |
| `vps_ssh_launcher/maintenance/pins.py` / `adapters.py` | 共享 pin 校验 / 注册表分派与结果判定 | 未注册资源拒绝、成功/回滚标记 |
| `scripts/cpa_bwg_guardrails.ps1` | CPA 参数准入、模板加载、哈希与投影、动作分派 | PowerShell 加载、源码漂移、原参数行为 |
| `scripts/remote/cpa-guardrail-*.sh` | doctor、apply、路径轮换、OAuth 停用与隔离/恢复模板 | Bash 语法、模板加载、对应操作的 fixture |
| `scripts/` 与 `scripts/remote/` | 本地运维入口和按字节部署的远端运行时 | `tests/` 对应领域测试、投影清单和回滚 |

依赖方向为 `CLI/维护入口 -> 能力模块 -> contracts`；维护领域直接调用配置、连接和
执行模块。能力模块不导入 CLI，配置模块不建立 SSH 连接。新增业务调用优先使用已有
公开接口；跨功能调用配置能力使用 `resolve_auth_for_entry()` 和 `coerce_port()`。
该依赖方向由 `tests/test_ci_meta.py` 的结构契约测试锁定。

功能增删遵循现有路径：

1. SSH 功能在对应能力模块实现，只有新增用户选项或动作时才改 CLI；沿原入口补充行为测试。
2. 新增维护资源按 `maintenance/resources.py` 文件头注释的四步走：资源模块实现
   `normalize_pin()` / `plan_upgrade()` / `build_from_pin()` → 注册表 `ResourceSpec` →
   `maintenance/inventory.py` 探针 facts → 计划/执行/回滚测试。
   config、planner 和 adapter 从同一注册表分派，不再分别维护资源分支或名单。
   远端写入需有版本或 hash pin、备份、结果复验与回滚；未知资源保持拒绝执行。
3. 删除维护资源先清理本机策略和调度中的引用，保留历史 SQLite 计划的读取与终态拒绝逻辑；
   删除本地入口不代表已卸载远端 cron、service 或 wrapper，远端卸载需独立授权。
4. CPA 文件按源码哈希投影。拆分远端文件时必须一起更新投影清单、部署目的路径、导入、
   doctor 哈希检查与回滚；本地重构不自动部署到 VPS。
   `cpa-guardrail-*.sh` 是由 PowerShell 加载并替换占位符的命令模板，不能直接当部署脚本运行。
   新增模板须登记入口的 `$guardrailTemplates` 并加入加载器参数集合；该表自动纳入
   Apply 的干净源码检查与准备期间的哈希漂移检查。缺失或空模板阻断准备。
   模板文件保持 LF；原入口、严格 doctor、五个远端写入开关及逐台授权语义保持不变。
5. Python 子包仅在 `vps_ssh_launcher/` 下扩展，由 `pyproject.toml` 限定包名前缀自动发现；
   子包须有 `__init__.py`，构建 wheel 后核对模块及入口完整性。测试统一放在 `tests/` 目录
   （pytest `testpaths = ["tests"]` 只收集该目录，天然排除独立上游 checkout）。
   保持 `scripts/run_gates.ps1` 与 CI 的检查范围一致。
6. 新的 PowerShell wrapper 不自写远程传输：`scripts/lib/project_environment.ps1` 的
   `Invoke-LauncherRemoteCommand` 已内置 CRLF 归一、base64 单发、超限分块临时文件
   传输与可选超时（行为契约见 `tests/test_ci_meta.py`）。
7. 新增 `vps-maint` 子命令在 `vps_ssh_launcher/maintenance_cli.py` 登记 parser 子命令、
   `_x_command` 参数/输出适配与 `main()` 分派，退出码沿用该文件头声明的语义；计划执行
   与状态转换放在 `maintenance/workflow.py` 和 `planner.updated_plan()`。流程测试进
   `tests/test_maintenance.py`。
8. 新增本地运维/审计工具放 `scripts/<域>_<名>.py` 并配对 `tests/test_<域>_<名>.py`；
   Ruff/Bandit 与 pytest 清单自动纳入该目录，无需改门禁或 `pyproject.toml`。

核心变更运行一次完整本地门禁；普通文档或 script 使用受影响的最低充分验证。
门禁只有本次显式传入 `-RunIntegration` 才启用真实 SSH，并在结束或失败时恢复进程环境；
直接运行 integration 测试仍使用其环境变量 opt-in。

---

## ⚡ 快速上手

```powershell
.\run.cmd
```

首次运行会在 `%APPDATA%\vps-ssh-launcher\target.json` 自动创建配置模板并退出。填写真实主机信息后再次运行：

```powershell
# 连通性探测
.\run.cmd -Profile example

# 执行远端单行命令
.\run.cmd -Profile example -Command "uname -a"
```

> [!NOTE]
> `run.cmd` 是 `connect.cmd` 的短别名；底层端到端调用链为：
> ```text
> run.cmd -> connect.cmd -> connect.ps1 -> ssh_tool.py -> vps_ssh_launcher/cli.py
> ```

### 告别 Windows 引号与转义地狱：`-CommandFile`

复杂命令不要直接放进 cmd 参数。先把远端 Shell 命令写入 UTF-8 文件，再传文件路径；
文件内容可包含管道、`&&`、多层引号和多行脚本，cmd 转发链只需处理简单路径，由内置 Base64/分块传输引擎无损直达远端：

```powershell
@'
printf '%s\n' "quoted" | grep quoted && echo 'done'
'@ | Set-Content -LiteralPath .\remote-command.sh -Encoding utf8NoBOM

.\run.cmd -Profile example -CommandFile .\remote-command.sh
```

直接使用 `connect.ps1` 时也可传 `-CommandFile <路径>`；`-CommandFile` 与 `-Command` 互斥。简单单行命令仍可直接使用 `-Command`。

### 超时机制与长命令保护

- `--command-timeout <秒>`（默认 `60`）：idle 超时控制。命令提交阶段受此限制，开始输出后按“无输出空闲”计时，有输出自动续期；`0` 表示禁用。
- `--command-hard-timeout <秒>`（默认 `0`）：从命令提交开始计算的绝对上限；`0` 表示禁用。
- 静默长命令（如慢模型生成、漫长编译）必须显式调大或置 `0`，否则 60 秒即被本地掐断。

---

## ⚙️ 配置与安全模型

### 查找顺序与物理隔离

1. `%APPDATA%\vps-ssh-launcher\target.json`（**主真源**，免除误提 Git 风险）
2. 仓库根 `target.json`（仅保留旧版兼容）

### 配置文件示例 (`target.json`)

```json
{
  "profiles": {
    "example": {
      "host": "YOUR_VPS_IP",
      "port": 22,
      "user": "root",
      "password_env": "VPS_EXAMPLE_PASSWORD",
      "socks5": "127.0.0.1:10808"
    }
  },
  "default": "example"
}
```

### 凭据与安全规范

> [!IMPORTANT]
> 严禁在代码、日志、文档或 Pull Request 中提交或记录真实 `target.json`、明文密码、私钥文件、Token、订阅地址或包含敏感回显的控制台日志。

- **多重认证支持**：支持环境变量 `password_env`、本地 `password`、私钥路径 `key`、运行时 `-Key` 及 `-AllowAgent`（SSH Agent 代理）。运行时指定参数优先级高于配置文件。相对密钥路径按配置文件所在目录解析。
- **管道安全输入**：支持 `--password-stdin`，从 stdin 读取一行密码，彻底避免明文密码出现在系统进程列表中。
- **SOCKS5 隧道集成**：支持 `socks5` 字段（如 `"127.0.0.1:10808"` 或 `{"host": "::1", "port": 1080}`）。配置后 SSH 连接底层通过 SOCKS5 代理打通，主机密钥比对与 `known_hosts` 仍严格绑定真实目的地址。
- **主机密钥校验**：默认采用 TOFU（Trust On First Use）模型并在用户目录持久化，后续密钥变动自动 Fail-Closed 拒连；`-StrictHostKeyChecking` 模式下直接拒绝任何未知的主机密钥。

---

## 📋 命令行参数与退出码

### 常用 CLI 参数一览

| 参数 | 行为 |
|---|---|
| `-Config <path>` | 指定配置文件 |
| `-Profile <name>` | 选择 profile |
| `-Command <text>` | 执行远端命令；省略时只检查连接 |
| `-CommandFile <path>` | 从本地 UTF-8 文件提交远端 Shell，规避 Windows 引号转义冲突 |
| `-CommandTimeout <seconds>` | idle timeout，默认 `60`，`0` 表示禁用 |
| `-CommandHardTimeout <seconds>` | 绝对 timeout，默认 `0` 表示禁用 |
| `-Key <path>` | 使用指定私钥 |
| `--password-stdin` | 从 stdin 读取一行 SSH 密码，避免密码出现在进程列表；优先使用此方式 |
| `-AllowAgent` | 使用 SSH Agent |
| `-StrictHostKeyChecking` | 拒绝未知主机密钥；默认模式把首次接受的密钥持久化到用户配置目录，后续密钥变化 fail-closed |
| `-RunAll` | 并发执行所有 profile；实现层只接受白名单诊断命令，写入与变更操作拒绝执行 |
| `-AllowGlobalBootstrap` | 明确允许向非隔离 Python 安装依赖 |
| `-Verbose` | 输出调试日志 |

### 标准化退出码

| 退出码 | 含义说明 | 处置方向 |
|:---:|---|---|
| `0` | 成功完成 (Success) | 正常结束 |
| `1` | SSH 鉴权失败 / 认证错误 | 检查密码、私钥、SSH Agent 或远端权限 |
| `2` | 本地配置解析错误 (Config Error) | 检查 `target.json` 语法、缺少 Profile 或字段类型 |
| `3` | 连接超时 (Connect Timeout) | 检查网络连通性、安全组/防火墙或 SOCKS5 代理状态 |
| `4` | 底层网络错误 (Network Error) | 检查 DNS 解析、端口监听或路由不可达 |
| `5` | 本地命令驱动 / 远端超时 / 内部错误 | 检查空闲超时配额或本地驱动管道 |
| `0-255` | 远端命令原生退出码 | SSH 建连成功后，`run` 会原样透传远端命令退出码 |

SSH 建连成功后，`run` 会原样返回远端退出码 `0-255`。`-RunAll` 会按 profile 输出结果与失败分类，并以最大退出码作为进程退出码。注意 `-RunAll` 的聚合退出码混合了两个命名空间：本地类别码 `1-5`（认证/配置/超时等）与远端命令码 `0-255`，按最大值返回，因此本地网络错误（`4`）可能被更大的远端码掩盖；需要区分时逐 profile 看失败分类输出。

单机 `run` 会增量输出 stdout/stderr，长命令不再等到退出后一次性回显；`-RunAll` 为保持各 profile 输出不交错，会在内存中按流最多保留 64K 字符，超出部分继续排空但不再累积。即使启用 `-Verbose`，远端命令正文也不会写入调试日志。

### 安全并发诊断：`-RunAll` 白名单机制

`-RunAll` 的固定诊断命令为 `uptime`、`uname`/`uname -a`、`df`/`df -h`/`df -Pk`、
`free`/`free -h`/`free -m`、`hostname`、`id`、`whoami`、`pwd`、`true`、`test`、
`ss -ltn`/`ss -ltnp`、`ps aux`、`docker ps`/`docker images`/`docker version`；另允许
`systemctl cat|is-active|is-enabled|show|status <literal-unit...>`。其他命令即使可读也应
通过单机 `run` 执行，避免多用途命令的写入参数绕过批量边界。

---

## 🐍 环境要求与运行时解析

### 解释器解析优先级

1. `VPS_SSH_LAUNCHER_PYTHON`
2. `.venv\Scripts\python.exe`
3. PATH 中的 `python`

### PowerShell 7+ 强制约束

`connect.cmd` 要求 PowerShell 7；可用 `VPS_SSH_LAUNCHER_POWERSHELL` 指定已批准的启动器，不再静默回退到 Windows PowerShell 5.1。共用的 Windows 环境与 Python 解析位于 `scripts/lib/project_environment.ps1`。

### Paramiko 5.x 现代 SSH 核心

如果缺少 Paramiko，或现有版本不满足 `>=5,<6`，`connect.ps1` 只会在隔离环境中自动安装或升级。命中全局 Python 时默认拒绝，除非显式传 `-AllowGlobalBootstrap`。探针通过的版本会缓存在配置目录的 `paramiko-probe.cache`（键含解释器身份、`requirements.txt` 戳与 Paramiko 发行目录存在性），避免每次调用都额外启动一个 Python 进程；任何不一致都会重新执行探针，缓存不会绕过安装或升级路径。

运行时依赖固定在已验证的 Paramiko 5.x。该主版本移除了不安全的 RSA/SHA-1 签名及部分旧密钥、KEX 和 GSSAPI 兼容路径；仍依赖这些算法的旧主机应先升级 SSH 配置，不通过降级客户端恢复连接。真实主机的密钥和算法兼容性需单独验收。

## 开发与验证

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

快速反馈时只运行本次受影响的测试，例如：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_cpa_admission.py
```

默认 Full 已运行全量 pytest；若后续会运行该门禁，不要先单独运行全量 pytest -q。

统一本地门禁：

```powershell
.\scripts\run_gates.ps1
```

默认 profile 是 `Full`，只执行一次必要证明：安装后导入、pytest、Bandit、Ruff lint/format 和 Mypy。它覆盖 `vps_ssh_launcher/` 的真实实现，不重复运行同一组 unittest，也不重复叠加第二套类型检查器。门禁也覆盖 `scripts/`（含 `scripts/remote/` 远端运行时）：它们进 Ruff check / Bandit（`hotspot:bandit-scripts` 只对 Medium 及以上失败，因为该目录的 Low 发现是"shell 出去调本地二进制 + 在一次性 fixture 上断言"的结构性产物）；mypy 与 `ruff format --check` 只覆盖顶层脚本，原因见 `scripts/run_gates.ps1` 内注释。

pytest 的测试清单只在 `pyproject.toml` 维护；静态门禁自动发现 `tests/` 下的测试模块，现有测试核对两者范围一致。`scripts/` 不再同时作为单文件和目录重复传入检查器；Mypy 使用 `--explicit-package-bases` 统一导入路径，无需为流式验收脚本另设排除。PowerShell 语法检查在一个进程中解析所有维护脚本。

CI 将静态门禁在一个组合中运行一次，将 Runtime（安装后导入与 pytest）保留在 Windows/Python 3.11 和 Ubuntu/Python 3.13 两个组合中。需要单独选择范围时可使用 `-FullScope Static` 或 `-FullScope Runtime`；本地默认 `FullScope All`。

在删除被安全策略阻断的宿主上（例如本机注入的 safe-delete shim），每个临时目录清理都会耗满 `conftest.py` 的预算，把测试步从几分钟拖到十几分钟，并在 `faulthandler_timeout` 处反复 dump 栈。可显式降低预算换墙钟时间，**断言与覆盖面完全不变**：

```powershell
$env:VPS_SSH_LAUNCHER_TEMP_CLEANUP_BUDGET_SECONDS = "0.5"
.\scripts\run_gates.ps1
```

超时被泄漏的临时目录会在 session 结束时列名，不影响测试结论。

日常修改可先按文件范围运行 `Focused` profile；它会运行能匹配到的测试，以及 Ruff lint（`ruff check`）、Ruff 格式检查（`ruff format --check`）和 Mypy，不运行 Bandit、依赖审计或真实 SSH：

```powershell
.\scripts\run_gates.ps1 `
  -Profile Focused `
  -FocusPath tests/test_cpa_admission.py,vps_ssh_launcher
```

`-FocusPath` 可以是 Python 文件或目录。若包含测试文件，Focused 会运行这些测试；若只包含源码，门禁会按文件名寻找相关测试，找不到时仍执行 Ruff、格式和 Mypy 静态检查。

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
.\.venv\Scripts\python.exe -m pytest -q tests/test_integration_real_ssh.py
```

也可以通过统一入口传入集成参数：

```powershell
.\scripts\run_gates.ps1 `
  -Profile Integration `
  -RunIntegration `
  -IntegrationConfig "$env:APPDATA\vps-ssh-launcher\target.json" `
  -IntegrationProfile "example"
```

`Integration` profile 只运行 `test_integration_real_ssh.py`；完整本地门禁由普通 CI 负责，避免在真实 SSH workflow 中重复执行。GitHub Actions 的真实 SSH workflow 只运行固定的无副作用 round-trip，不接受自定义远端命令。启用前必须在 `vps-production` Environment 中配置 required reviewer，以及 `VPS_SSH_LAUNCHER_INTEGRATION_TARGET_JSON` 和经过带外核验的 `VPS_SSH_LAUNCHER_INTEGRATION_KNOWN_HOSTS` 两个 environment secrets。临时 runner 强制严格 host-key 校验。

Integration round-trip 的远端命令固定为 `printf vps-ssh-launcher-integration`；该入口没有自定义远端命令参数，避免把集成验收误用为批量远端 Shell 执行器。

真实 SSH、主机在线状态和远端服务效果是独立验收层；本地 gate 通过不能外推为 live accepted。

---

## 🎯 声明式 VPS 维护控制平面 (`vps-maint`)

针对传统运维中“一键脚本盲目执行、无法审计、不可回滚”的痼疾，`vps-maint` 确立了声明式闭环管理：

```text
State (TOML) ──> Readonly Inventory ──> Deterministic Plan ──> Controlled Apply ──> Redacted Receipt
```

### 1. 核心流程与标准命令

```powershell
# 1. 从模板复制并初始化本地维护策略
Copy-Item .\maintenance.example.toml "$env:APPDATA\vps-ssh-launcher\maintenance.toml"

# 2. 收集只读资产与环境事实 (Inventory)
$env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = "1"
vps-maint inventory --run-integration --target-config "$env:APPDATA\vps-ssh-launcher\target.json" --output .\inventory.json

# 3. 对比期望状态生成确定性升级计划 (Plan)
vps-maint --config "$env:APPDATA\vps-ssh-launcher\maintenance.toml" plan --inventory-file .\inventory.json

# 4. 查看历史计划与执行状态
vps-maint --config "$env:APPDATA\vps-ssh-launcher\maintenance.toml" history
```

> [!NOTE]
> 真实 inventory 必须显式传入 `--run-integration` 并设置 `$env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = "1"`。CLI 和 `connect.ps1` 默认启用严格 host-key 校验（TOFU 模式需显式指定 `--allow-unknown-host-key`）。状态默认持久化在 `%APPDATA%\vps-ssh-launcher\maintenance.db`（SQLite），收据落盘在 `maintenance-receipts\`，严格对密码、私钥、Token 脱敏。

### 2. 环境事实指纹防漂移 (Fingerprint Verification)

`vps-maint apply` 默认是 dry-run，即使计划处于 `planned` 状态也不会连接或写入远端。必须同时使用 `--yes --remote-write --run-integration` 并设置 `$env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = "1"`，才会进入单 profile、单主机、串行的远端边界。

执行前会重新收集 inventory 并要求 **Fingerprint 与计划完全一致**：
- **指纹范围**：仅覆盖关键身份 Fact（内核、架构、OS、核心 SHA-256、镜像 Digest、容器清单、监听端口、服务后端、vasma 脚本/源配置哈希和重启要求等）。
- **动态遥测豁免**：磁盘百分比、内存占用等易变遥测不参与指纹计算，良性漂移不会迫使重建计划。
- **防漂移阻断**：若核心身份 Fact 变动，计划立即拒绝执行并要求重新生成，坚决杜绝在未预期环境下执行变更。

### 3. 精准 Pin 与自动回滚保障

- **Xray 核心**：只接受显式版本和 SHA-256 pin，当前通用 adapter 明确只允许 `x86_64/amd64` 的 `Xray-linux-64.zip`；执行下载校验、自动备份、配置测试、重启、状态读回与失败原子回滚。
- **Docker 容器**：非 CPA Docker 只接受绝对 Compose 路径、服务 allowlist 和 image digest pin；执行 Compose config 检查、pull/up、健康度与 digest 读回及失败回滚。
- 任何缺失 pin、fingerprint 漂移、架构不匹配或验证失败均会自动阻断。

升级执行示例（需在策略中配置 `upgrade` 并补齐 pin）：

```powershell
$env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = "1"
vps-maint --config "$env:APPDATA\vps-ssh-launcher\maintenance.toml" plan --live-inventory --run-integration --target-config "$env:APPDATA\vps-ssh-launcher\target.json" --profile bwg --output .\upgrade-plan.json
vps-maint --config "$env:APPDATA\vps-ssh-launcher\maintenance.toml" apply --plan-id <plan-id> --yes --remote-write --run-integration --target-config "$env:APPDATA\vps-ssh-launcher\target.json" --profile bwg
```

### 4. PowerShell 本地计划任务自动化

可安装一个每日 `20:00` 的静默观察任务（仅生成 Plan 与本地日志，不触碰远端 Apply）：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_vps_maintenance_task.ps1 -WhatIf
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_vps_maintenance_task.ps1
```

若要撤销观察任务：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_vps_maintenance_task.ps1 -Remove
```

---

## 🛡️ 生产级 AI 网关防护 (CPA Guardrails)

BWG 上的 CLIProxyAPI (CPA) 经 Nginx TLS 8443 配合高熵随机 capability path 对公网提供 OpenAI 兼容接口。后端服务架构严格分层分流：
- **公网入口**：Nginx (8443) 负责 TLS 终结与前置随机路径鉴权，本地限流拦截返回带 `Retry-After` 的标准 429；
- **准入网关**：`cpa-admission.service` (监听 `127.0.0.1:8318`)，主业务请求必须先经此准入调度；
- **核心容器**：CPA 容器仅绑定本地 `127.0.0.1:8317`，外部无法绕过准入层直达核心。

`scripts/cpa_bwg_guardrails.ps1` 是该主机的统一运维与防线守护脚本（约 4300 行，默认只读探测）。部署架构、路由清单、更新器策略与应急开关详见 [CPA 网关运行手册](docs/runbooks/cpa-gateway.md)。

### 1. 四层 429 智能故障归因 (Triage)

在分布式 AI 网关场景中，客户端收到的 `429 Too Many Requests` 可能源自四个完全不同的层级，各层处置方案互斥：
1. **本机客户端限流**：桌面端 / Sidecar 并发超限或未遵守退避窗口；
2. **Nginx 入口速率与连接限制**：未命中白名单或突发过载；
3. **Admission Lane 准入队列限制**：单模型并发达上限（`max_inflight`）或排队超时（120s）；
4. **上游服务商真实熔断**：服务商返回 `Selected model is at capacity`、`429/503` 或账号配额耗尽。

通过 `cpa_failure_triage.py`，结合本地访问日志（`codex_local_access_logs.sqlite`）中的等待预算指纹与远端 doctor 事实，精准区分本地拒绝与上游真实过载：

```powershell
# 只读诊断最近 4 小时的 429 / 慢速根因
.\.venv\Scripts\python.exe scripts/cpa_failure_triage.py --hours 4

# 结合远端 doctor 输出生成 24 小时完整 JSON 归因报告
.\.venv\Scripts\python.exe scripts/cpa_failure_triage.py --hours 24 --doctor outputs/doctor-latest.txt --json
```

详细判别表与闭环调优见 [故障归因与调优手册](docs/runbooks/cpa-failure-triage.md)。

### 2. 共享账号准入风险与契约审计 (Audit)

归因回答“某次请求为何失败”，而契约审计回答：**共享账号 admission 的配置是否自洽安全？对外宣告的模型是否已实质失效？**

```powershell
# 执行 24 小时契约与路由姿态审计
.\.venv\Scripts\python.exe scripts/cpa_admission_risk_audit.py --hours 24

# 快速跳过姿态检查，仅验证清单配置契约
.\.venv\Scripts\python.exe scripts/cpa_admission_risk_audit.py --skip-posture
```

判别口径与职责边界详见 [admission 风险与契约审计](docs/runbooks/cpa-admission-risk-audit.md)。
统一执行工作流入口见 [CPA recovery workflow](docs/runbooks/cpa-recovery-workflow.md)：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\cpa_recovery_workflow.ps1 -Mode Audit
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\cpa_recovery_workflow.ps1 -Mode Triage -Hours 4
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\cpa_recovery_workflow.ps1 -Mode Verify
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\cpa_recovery_workflow.ps1 -Mode ControlledReplay
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\cpa_recovery_workflow.ps1 -Mode WaitCapSimulation
```

### 3. 四大物理隔离 Admission Lane 与模型级熔断

为避免单一模型或单账号异常导致网关全局雪崩，CPA 划分为四条互不干扰的准入泳道：

| 准入泳道 (Lane) | 承载模型标识 | 并发控制 (Inflight / Queue) | 熔断机制与冷却策略 |
|---|---|:---:|---|
| **`chatgpt-oauth`** | `gpt-6-luna`, `gpt-5.6-luna`, `gpt-6.1-sol` | `max_inflight=2`<br>`max_pending=4` (120s) | 区分**模型级容量**与**账号级容量**：单个模型超载仅熔断该模型，同账号其他模型仍可服务；支持 `60-900s` 指数退避 |
| **`zhipu-coding-plan`** | `glm-5.3`, `glm-5.3-flash` | `max_inflight=3`<br>`max_pending=4` (120s) | 遵循智谱 Coding 协议，纯文本模型阻断图像预检（422 Fast-Fail）；支持 86400s 上限与按需半开探测 |
| **`antigravity-oauth`** | `gemini-3.8-flash-high` | `max_inflight=2`<br>`max_pending=4` (120s) | 首创严格冷却机制：无提前探测，精确对齐 `RetryInfo.retryDelay` 最长窗口；`401/403` 自动触发停流保护 |
| **`workbuddy-oauth`** | `deepseek-v4.1-flash` | `max_inflight=1`<br>`max_pending=4` (120s) | 单飞严格限流，杜绝并发突发；旧版通道保留安全墓碑，防止无效流量透传 |

> [!IMPORTANT]
> **SSE 协议完整性与流式断连保护**：仅当观测到符合协议规范的成功终态（无错误、扫描完整、客户端正常接收）时才计入成功并清除熔断。仅有握手事件或半截 delta 后的 EOF 不算成功，坚决避免将假性 200 误判为恢复。

### 4. 零停机可逆账号隔离 (Quarantine) vs 应急销毁

```powershell
# 1. 严格只读健康检查 (Doctor 门禁)
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg

# 2. 按清单重投影配置 (Apply)
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -Apply

# 3. 账号可逆隔离 (Quarantine)：从路由目录中移除别名并置标记，保留物理凭据，零停机保护账号
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -QuarantineOAuthLuna
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -QuarantineAntigravity
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -QuarantineClaudeOAuth -QuarantineUserDirective "用户授权隔离"

# 4. 故障排除后安全恢复 (Restore)
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -RestoreOAuthLuna
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -RestoreAntigravity
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -RestoreClaudeOAuth

# 5. 极端应急处置：永久销毁凭据（不可逆）
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -DeactivateOAuthLuna
```

> [!TIP]
> 隔离期间 `-Apply` 会自动阻断执行，防止未预期重投影冲掉隔离状态。Claude 订阅隔离采用特有的配置热加载机制，不重启 CPA 守护进程；Luna 与 Antigravity 隔离仅修改排除列表并置标记，完全保留原账号凭据。

### 5. 客户端分流与 Luna 双模式切换

- **流量分流优先级**：DeepSeek 官方 API（低成本/非敏感重负载） > GLM Coding Plan（合规编码任务） > Luna ChatGPT Plus OAuth（高交互/高价值交互） > ai.input.im（备用通道）。
- **Luna 切换模式**：Cockpit 客户端支持在 **Direct OAuth** 与 **Direct BWG API** 之间无缝切换。Direct OAuth 遭遇的容量上限属于 OpenAI 官方当前过载；Direct BWG API 链路则由本仓 Admission 准入层全程守护。
- **本地 Sidecar 守护与端到端健康检查**：
  ```powershell
  # 本地桌面 Sidecar 契约审计与状态验证
  pwsh -NoProfile -File .\scripts\cockpit_sidecar_guardrails.ps1 -Mode Audit
  pwsh -NoProfile -File .\scripts\cockpit_sidecar_guardrails.ps1 -Mode Verify

  # 桌面 Provider 配置与端点匹配检查
  .\.venv\Scripts\python.exe scripts/cockpit_provider_health.py
  ```

---

## ⏰ 计划任务与自动化运维

针对 Linux 运维中常见的定时任务相互打架或被第三方脚本意外冲刷问题，本仓确立了两项铁律：
1. **全局分布式互斥排他锁**：所有计划任务统一持有 `/run/vps-ssh-launcher-maintenance.lock`（通过 `flock -n`），杜绝任务并发冲突。
2. **完全解耦 Root Crontab**：所有计划任务均独立落盘至 `/etc/cron.d/` 独立单元，彻底免疫第三方一键脚本（如 vasma 证书更新）对 crontab 的整表重写与冲刷。

### 1. Xray / sing-box 内核安全周更 (`vasma_kernel_update_cron.ps1`)

每周自动执行核心升级，执行前强校验官方二进制 SHA-256 与 vasma 菜单脚本指纹，升级失败自动原子回滚：

```powershell
# 1. 默认只读检查当前内核与脚本状态
pwsh -NoProfile -File .\scripts\vasma_kernel_update_cron.ps1 -Profile bwg -Kernel xray

# 2. 显式指定经核对的官方版本、架构二进制哈希与脚本指纹，受控投影更新任务
pwsh -NoProfile -File .\scripts\vasma_kernel_update_cron.ps1 -Profile bwg -Kernel xray `
  -Version 26.3.27 -InstalledSha256 <binary-sha256> -VasmaSha256 <script-sha256> -Apply

# bwg 双内核车道：sing-box 与 xray 并存（vasma 混合态），cron 双行分列 14:20/14:30 UTC
pwsh -NoProfile -File .\scripts\vasma_kernel_update_cron.ps1 -Profile bwg -Kernel sing-box `
  -Version 1.14.4 -InstalledSha256 <binary-sha256> -VasmaSha256 <script-sha256> `
  -Schedule '30 14 * * 5' -Apply
```

> [!NOTE]
> 调度落盘于 `/etc/cron.d/vps-launcher-kernel-update`。针对 Google/Gemini 与 Claude 出站，配合 `scripts/google_ipv4_routing.ps1` 实现严格的 `ForceIPv4` / `resolve + ipv4_only` 路由分流与配置备份回滚。

### 2. v2ray-agent 管理脚本固定 Commit 周更 (`v2ray_agent_script_update_cron.ps1`)

远端 updater 从官方 HTTPS 获取更新，严格校验代码锚点与文件结构，通过 `scripts/remote/v2ray-agent-source-pin.json` 固化审计过的 Git Commit 与文件 SHA-256，坚决拒绝直接拉取未经审计的 `master` 分支：

```powershell
# 1. 读取远端脚本当前 Hash
pwsh -NoProfile -File .\scripts\v2ray_agent_script_update_cron.ps1 -Profile bwg

# 2. 部署经过审查的周更定时任务 (每周五 14:40 UTC)
pwsh -NoProfile -File .\scripts\v2ray_agent_script_update_cron.ps1 -Profile bwg -InstallSha256 <fresh-sha256> -Apply
```

同时配套 `scripts/v2ray_agent_renewtls_cron.ps1`，将原本脆弱的证书自动续期迁移到独立的 `/etc/cron.d/vps-launcher-v2ray-agent-renewtls` 单元中受控执行。

### 3. 月度系统安全维护 (`system_maintenance_cron.ps1`)

每月 1 日执行安全更新与系统清理（`update` + `upgrade --with-new-pkgs` + `autoremove --purge` + 30 天 journal 深度轮转）：
- **绝不自动重启**：若需要重启仅在 `/var/log/monthly-maintenance.log` 登记标记，由管理员自主择机处置；
- **更新后服务自愈校验**：更新完成后自动校验 Xray、Nginx、fail2ban 等关键守护进程，确保网络服务持续在线。

```powershell
# 1. 只读巡检系统维护计划
pwsh -NoProfile -File .\scripts\system_maintenance_cron.ps1 -Profile bwg

# 2. 投影月度系统维护定时任务
pwsh -NoProfile -File .\scripts\system_maintenance_cron.ps1 -Profile bwg -Apply
```

### 4. 抗封锁硬化（伪装页 / REALITY shortId / Hysteria2 obfs）

针对 GFW/运营商主动探测与指纹识别，提供三条默认只读、`-Apply` 才生效、幂等且带备份回滚的硬化车道。它们都不改上游 vasma，vasma 重装后重跑即可恢复：

```powershell
$env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = "1"

# a. 伪装页：把 vasma 自带 en/ 企业站提升为 nginx 根，消除 "Welcome to nginx!" 默认页指纹
pwsh -NoProfile -File .\scripts\nginx_camouflage_site.ps1 -Profile bwg          # 只读
pwsh -NoProfile -File .\scripts\nginx_camouflage_site.ps1 -Profile bwg -Apply

# b. REALITY shortId：剥离服务端空 shortId（订阅只用非空 id，零断连）
pwsh -NoProfile -File .\scripts\reality_shortid_harden.ps1 -Profile zz           # 只读（按主机现存核心树逐树处理；bwg 双核，zz sing-box）
pwsh -NoProfile -File .\scripts\reality_shortid_harden.ps1 -Profile zz -Apply

# c. Hysteria2 obfs 备用车道：独立 UDP 端口 + salamander 混淆，保留原 443 与订阅不变
pwsh -NoProfile -File .\scripts\hysteria2_obfs_lane.ps1 -Profile zz -Port 8444
pwsh -NoProfile -File .\scripts\hysteria2_obfs_lane.ps1 -Profile zz -Port 8444 -ObfsPassword '<shared-salt>' -Apply
pwsh -NoProfile -File .\scripts\hysteria2_obfs_lane.ps1 -Profile bwg -Port 8444   # bwg 双核加装 hysteria2 后同款车道
```

> [!IMPORTANT]
> 逐台执行：一台验证联通正常后再动另一台。obfs 车道是新增端口，客户端需手动按该端口 + obfs 密码配置；原订阅节点不受影响。

硬化之后最重要的一条：vasma 重装/菜单重配会静默还原默认页与空 shortId，而**服务照常运行、订阅照常可用**，所以没有任何告警。用只读漂移门禁定期确认：

```powershell
pwsh -NoProfile -File .\scripts\proxy_hardening_drift.ps1 -Profile bwg   # 只读，有漂移时退出码 3
```

输出单行判据 `HARDENING_DRIFT=NONE`；若为 `HARDENING_DRIFT=camouflage,reality-shortid,hysteria2-obfs` 的子集，按名字重跑对应硬化脚本即可恢复。

### 5. BWG 全链一键巡检与即时维护 (`bwg_full_maintenance.ps1`)

串行执行控制面计划、CPA Doctor、脚本校验、系统状态及 IPv4 路由检查，生成标准化脱敏报告：

```powershell
$env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = "1"

# 只读观察模式
pwsh -NoProfile -File .\scripts\bwg_full_maintenance.ps1 -Mode Observe -RunIntegration

# 即时执行维护链 (月度 apt + 脚本更新 + 严格 Doctor + Inventory)
pwsh -NoProfile -File .\scripts\bwg_full_maintenance.ps1 -Mode RunNow -RunIntegration
```

### 6. 高风险安装器边界 (`auto_install.py`)

`auto_install.py` 会驱动远端 `/etc/v2ray-agent/install.sh`，不是健康检查。它默认严格阻断，仅在 Linux 宿主、显式授权、提供脚本 SHA-256 及 `VPS_DOMAIN` 环境变量的前提下人工执行：

```bash
python -m pip install '.[installer]'
python ./auto_install.py --execute --install-script-sha256 <sha256>
```

---

## 🩺 排障、取证与证据边界

### Windows 进程环境异常排查

当精简宿主或沙箱环境中遭遇 `WinError 10106`（Winsock 目录损坏）、Python 进程无法启动或基础环境变量缺失时，请先遵循 [Windows 进程环境恢复手册](docs/runbooks/windows-process-environment-recovery.md) 进行系统网络套接字修复，切勿盲目归因于脚本逻辑。

### 凭据脱敏与工程化证据链 (Redaction First)

- **零泄露承诺**：本工具链所有输出、收据与日志默认经过严格的敏感信息清洗引擎，严禁向版本库或外部控制台输出真实主机 IP、明文密码、私钥、Token、订阅链接或包含敏感回显的交互片段。
- **证据分级管理**：例行测试、只读探针与常规巡检结果直接附于 Commit Message 或现有工单中；仅当发生真实远端变更、故障排障或版本 Release 时，才在 `docs/change-evidence/` 生成归档收据。

---

## 📚 运行手册导航 (Runbooks)

本仓沉淀了 23 篇覆盖 AI 网关运维、客户端防护、VPS 核心设施与故障自愈的详尽实战手册：

| 分类 | 运行手册名称 | 核心关注场景与解决方案 |
|---|---|---|
| **AI 网关防护 (CPA)** | [cpa-gateway.md](docs/runbooks/cpa-gateway.md) | CPA 部署拓扑、Nginx TLS + 随机 Capability 路径配置、严格 Doctor 门禁 |
| | [cpa-failure-triage.md](docs/runbooks/cpa-failure-triage.md) | 四层 429 智能归因、等待预算指纹分析、客户端超时与上游熔断识别 |
| | [cpa-admission-risk-audit.md](docs/runbooks/cpa-admission-risk-audit.md) | 共享账号准入配置自洽性审查、失效路由探针、降智与风控防护 |
| | [cpa-recovery-workflow.md](docs/runbooks/cpa-recovery-workflow.md) | 一站式网关故障恢复工作流（Audit -> Triage -> Verify -> ControlledReplay） |
| | [cpa-ban-throttle-incident-response.md](docs/runbooks/cpa-ban-throttle-incident-response.md) | 遭遇突发封号、大面积 429 触发时的应急响应方案与停流措施 |
| | [cpa-stale-cooldown-recovery.md](docs/runbooks/cpa-stale-cooldown-recovery.md) | 纯内存冷却状态管理与陈旧 Cooldown 强制解除机制 |
| | [cpa-oauth-failure-recovery.md](docs/runbooks/cpa-oauth-failure-recovery.md) | OAuth 凭据失效、刷新失败与容量过载排查手册 |
| | [cpa-oauth-luna-slot.md](docs/runbooks/cpa-oauth-luna-slot.md) | Luna 专用插槽模型映射与多模型协同调度机制 |
| | [cpa-catalog-change-checklist.md](docs/runbooks/cpa-catalog-change-checklist.md) | 模型目录新增、重命名或下线变更清单自检核对表 |
| | [codex-model-catalog-change.md](docs/runbooks/codex-model-catalog-change.md) | Codex 实例目录、模型可见性与上下文适配的变更步骤与回滚 |
| | [cpa-claude-account-risk.md](docs/runbooks/cpa-claude-account-risk.md) | Anthropic 使用条款约束、订阅 OAuth 风险边界与官方 API 替代方案 |
| | [cpa-claude-oauth-attach.md](docs/runbooks/cpa-claude-oauth-attach.md) | Claude OAuth 热加载隔离与事务回滚控制机制 |
| | [cpa-workbuddy-plugin.md](docs/runbooks/cpa-workbuddy-plugin.md) | WorkBuddy 接入规范与 DeepSeek 单飞并发约束 |
| | [cpa-error-dump-forensics.md](docs/runbooks/cpa-error-dump-forensics.md) | 网关异常报错快照转储与网络取证流程 |
| | [cpa-manual-rollback.md](docs/runbooks/cpa-manual-rollback.md) | CPA 部署版本、配置清单与投影环境的人工回滚方案 |
| | [cpa-observed-baselines-and-upstream-log.md](docs/runbooks/cpa-observed-baselines-and-upstream-log.md) | 上游请求基线数据观测与延迟行为基准对照 |
| **客户端与 Sidecar** | [cockpit-sidecar-guardrails.md](docs/runbooks/cockpit-sidecar-guardrails.md) | 本地 Cockpit Sidecar 并发限制、等待上限（45s）与契约验收 |
| | [cockpit-tools-persistent-fix-v1.3.65.md](docs/runbooks/cockpit-tools-persistent-fix-v1.3.65.md) | 客户端工具链持久化修复方案与版本演进说明 |
| | [cockpit-fq-access-mode-options.md](docs/runbooks/cockpit-fq-access-mode-options.md) | 直连与网关访问模式技术选型对比 |
| **VPS 核心基础设施** | [v2ray-agent-script-update.md](docs/runbooks/v2ray-agent-script-update.md) | v2ray-agent 管理脚本安全更新、Commit 校验与回滚指南 |
| | [kernel-core-manual-rollback.md](docs/runbooks/kernel-core-manual-rollback.md) | Xray / sing-box 内核异常人工应急回滚指南 |
| | [singbox-core-update-migration.md](docs/runbooks/singbox-core-update-migration.md) | sing-box 1.12+ 架构演进与配置无损迁移指南 |
| **系统与环境诊断** | [windows-process-environment-recovery.md](docs/runbooks/windows-process-environment-recovery.md) | Windows 进程环境变量丢失、套接字损坏恢复权威指南 |

---

## 🤝 参与贡献与开源协议

欢迎通过 Pull Request 或 GitHub Issue 提交改进建议！在提交代码前，请确保执行本地门禁校验：

```powershell
# 运行完整质量门禁 (静态检查 + 单测 + 格式化)
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_gates.ps1 -Profile Full
```

本项目采用 MIT License 开源协议。
