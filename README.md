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

SSH 建连成功后，`run` 会原样返回远端退出码 `0-255`。`-RunAll` 会按 profile 输出结果与失败分类，并以最大退出码作为进程退出码。

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
$env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = "1"；连接始终启用严格 host-key 校验，inventory 命令是固定只读探针。状态默认保存在 %APPDATA%\vps-ssh-launcher\maintenance.db，receipt 默认保存在同目录的 maintenance-receipts\，不保存密码、私钥、token、订阅地址或完整远端命令。

`vps-maint apply` 默认是 dry-run，即使计划为 `planned` 也不会连接或写入远端。必须同时使用
`--yes --remote-write --run-integration`，并设置
`$env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = "1"`，才会进入单 profile、单主机、串行的远端边界；执行前会重新收集 inventory 并要求 fingerprint 与计划完全一致，连接继续使用严格 host-key 校验。Xray 只接受显式版本和 SHA-256 pin，执行下载校验、备份、配置测试、重启、读回和失败回滚；非 CPA Docker 只接受绝对 Compose 路径、服务 allowlist 和 image digest pin，执行 Compose config、pull/up、健康与 digest 读回及失败回滚。没有 pin、fingerprint 漂移、CPA 路径/服务/镜像标识或任一门禁失败都会阻断。CPA、provider 和凭据维护继续使用既有的 BWG 专用 guardrail/runbook，不会被通用 Docker adapter 接管。

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

CPA 自动更新的受版本管理脚本为 `scripts/remote/cpa-auto-update.sh`，部署到
`/opt/cliproxyapi/auto-update.sh`，由既有 `cliproxyapi-update.timer` 每日 UTC
04:00–04:30 调用（2026-09-18 起从每周改为每日：候选 72h 成熟期是真正的节拍门，
让成熟版本最多晚一天收编）。默认执行更新；无成熟候选时只做本地 readiness
与最近 24 小时刷新失败标记检查，不发送 provider generation。
`bash /opt/cliproxyapi/auto-update.sh --check`
仅检查候选并写既有更新日志，不修改服务。候选必须同时存在于官方 GitHub release
和 Docker Hub，并在两处均满 72 小时；默认仅在当前 major/minor 线内选择最高 patch，
minor/major 升级均需先做独立评审和 canary，不因更新鲜版本存在而跳过成熟版本，不降级。
发现成熟的 minor 或 major 候选时仅写 `MINOR_CANDIDATE available=<tag>` 或
`MAJOR_CANDIDATE available=<tag>` 日志（doctor 的 timer 段会带出），不做任何升级动作。
镜像固定 tag + digest，文件锁避免重叠执行；自动更新备份且只回滚它实际修改的
Compose 镜像声明，绝不删除、覆盖或回放 auth 凭据文件。OAuth 刷新可在候选镜像
运行期间轮换 token，凭据快照只能作为受控人工灾难恢复输入，不属于自动镜像回滚。
配套 `scripts/remote/cpa-health.py` 与 `scripts/remote/cpa_policy.py` 部署到同目录：
当前 BWG fresh doctor 的运行版本以主机实际镜像 tag/digest 为准（本轮读到
`v7.3.15`）；下文的 v7.3.7 只作为字段语义基线，不代表当前运行版本。CPA 保留
`codex.stream-bootstrap-buffering: true` 以便在上游把
`server_is_overloaded` 藏在流内握手之后时进行正确分类；同时固定
`codex.stream-bootstrap-timeout: "20s"`，把慢 provider 的首包 bootstrap 等待设为
有界值，避免无限期延迟下游响应头。该上限不改变 provider 重试策略，也不把上游错误
变成本地成功；响应头提前提交后，后续流内错误仍由客户端按流语义处理。字段语义以
[CLIProxyAPI v7.3.7 官方配置](https://github.com/router-for-me/CLIProxyAPI/releases/tag/v7.3.7)
为准。
更新前使用单个代表性非 OAuth 路由
（`glm-5.3-flash`，走官方 GLM Coding Plan；OAuth 登出期间 Luna 目录不完整，
显式 Luna 检查自动按 exit 10 暂缓，不误报本地故障）做低频
生成检查，失败则暂缓；更新后
本地契约失败回滚并确认旧服务就绪；暂时上游失败只做本地 readiness 复验，不重复发送
生成请求，保留本地就绪镜像并以 exit 10 报未验收。目录瞬态 `408/429/5xx` 只做一次
请求并立即停止；只有 HTTP 200 但模型仍在注册时才允许最多两次短间隔复验，避免健康
检查自身放大 provider 限流。无新版本时不做 Luna generation；需要解除先前
`UNVERIFIED` 状态时由人工显式执行一次低频探针。更新器不自动调用 `relay-soft`，
因此每日无候选路径不会发送 Luna/Sol/Terra/Astra generation。
目录已验证后仍可显式调用 `relay-soft`；当前软腿观察的是
`ai.input.im` 的 `gpt-6-astra`，结果记录为
`HEALTH_OK|RELAY_DEGRADED`，且仍不参与任何更新决策。该通道属于第三方中转，可能出现
403、408/429/5xx、账号风控、上游模型
缺席或质量回退；sol/terra 只应作为非敏感备用通道使用，
敏感内容走 Luna（OAuth）、`glm-5.3-flash` 或 `deepseek-flash`。GPT-6 Luna 是新的
OAuth 裸名。当前裸名路由将 `gpt-6-sol`、`gpt-6-astra` 固定在 ai.input.im；槽位 2 额外
提供 `gpt-6-astra-cii`、`gpt-6-sol-cii`，槽位 3 提供 `gpt-6-sol-91` 与裸名 `gpt-5.6-terra`。两个 Sol 别名映射到
渠道目录中的上游 `gpt-5.6-sol`。CIII 仍保留为渠道，但 `codex-auto-review`、`gpt-5.5`、
`gpt-5.6`、`gpt-reserve` 四个旧别名继续从 OAuth/Codex API-key 路由排除。
OAuth 侧保留 `codex-*` 和 `gpt-5.7*` 排除，让 `gpt-6-luna` 留在 OAuth。
`scripts/remote/cpa_provider_routes.json` 将 `gpt-6-luna` 显式映射到 ChatGPT Plus OAuth
lane；它不属于 `openai-compatibility` provider。目录健康门要求路由清单登记的模型 ID
（ai.input.im 裸名、CIII/HTTP 别名、BigModel 与 DeepSeek 当前目录模型）；尚未开放的 GPT-6 Luna 或
GPT-6 Sol 可缺席并标为未验证，未知模型和 prefix
仍直接失败（目录阶段的本地契约失败为 exit 20；目录已通过后，单路由 403 归类为
`UPSTREAM_UNAVAILABLE`，不把上游账号/路由决定误报为本地配置错误）。所有真实
generation、quality 和 cache 探针共享 `/opt/cliproxyapi/health-probe.lock` 的非阻塞
`flock`；并发探针立即输出 `PROBE_ALREADY_RUNNING`，不排队、不重试。
CLIProxyAPI 这里提供的是 OAuth 排除规则，不是请求级正向 allowlist；移除旧 `gpt-6*`
排除以放行 Luna 后，未来 Codex GPT-6 新模型可能进入上游目录。健康门会把未登记 ID
判为本地契约失败，但不能拦截直接推理请求；每次上游目录变化后都需先审阅并更新
精确路由策略。
缓存优化目前只做不会改变 provider 语义的请求侧约束：稳定的系统提示和工具说明
放在 prompt 前缀，时间戳、随机 ID、用户私有内容等动态部分放在后部；不要跨用户
复用 session/cache key，也不要仅为追求命中率盲目打开 `support-prompt-cache-key`。
DeepSeek 的命中率应以官方返回的 `prompt_cache_hit_tokens` /
`prompt_cache_miss_tokens` 观测，不能把一次受控样本外推为长期收益；OAuth/Codex
路由和 ai.input.im 也不能套用 DeepSeek 或 OpenAI API 的缓存结论。显式运行
`python3 /opt/cliproxyapi/cpa-health.py cache-canary` 会在 `deepseek-flash` 上以同一
短期随机 session 发送两次相同的安全长前缀请求，只输出模型、输入、缓存读/写/未命中
token 和命中比例，绝不输出 key、session、prompt 或响应正文；没有 provider telemetry
时以 `CACHE_TELEMETRY_UNAVAILABLE` 明确失败，不宣称缓存已改善。它不进定时器，且
OAuth、ai.input.im 与其他 provider 必须各自得到同类证据后才可作结论。
需要检查全部已暴露路由时，显式运行 `python3 /opt/cliproxyapi/cpa-health.py generation-all`；
该模式会增加真实 provider 请求，不由定时更新器调用；它会继续完成剩余模型的单次探针，
逐行输出 `model`、HTTP `status`、`latency_ms`、`finish` 和脱敏 `error_class`，不输出
key、请求体或响应正文。需要在版本或路由变动后检查
最小语义契约时，显式运行 `python3 /opt/cliproxyapi/cpa-health.py quality-canary`；该模式
对每条已暴露路由仅发送一次非敏感算术/JSON 请求，不输出正文，不能证明长期模型质量。
它只接受原始 JSON 或单层 `json` Markdown 围栏；目录已验证后单条 ai.input.im `403` 记为
`UPSTREAM_UNAVAILABLE`，不误报为本地契约故障。ai.input.im 已登记的 Sol/Terra/Astra、两个
槽位 3 的 `-91` 别名以及其它清单模型会进入显式生成矩阵（`generation-all` / `quality-*`）。GPT-6 Sol 即使已在 CPA 本地目录登记，
也可能尚未被 ai.input.im 开放；显式探针的 403/404 归类为 `UPSTREAM_UNAVAILABLE`，
不自动重试。定时门固定以 `glm-5.3-flash` 为目标；
DeepSeek 仍保留在显式矩阵中，Luna 只在显式矩阵或人工指定的低频检查中参与。
上游即使返回 HTTP 200，只要响应体不是合法 JSON，也按
`UPSTREAM_UNAVAILABLE` / `RELAY_DEGRADED` 处理；不把异常 200 当作成功，不重试，
也不做内容包装后继续转发。

ChatGPT Plus OAuth 经此 CPA 网关转发仍有账号与使用条款风险：OpenAI 文档建议脚本化
工作流优先用 API key，并要求不要把 Codex 执行暴露给不可信或公共环境。
本配置保留了公网 Nginx 入口，因此只应供本人控制的客户端使用，严格保管随机路径和
访问 key；不得共享或做高频自动化。遇到 OAuth `401`/`403`、`429` 或连续上游失败时
停止请求并人工检查，不自动切换模型或重试。现有零自动重试、单次探针、互斥探针锁和
低频 OAuth 检查只能降低流量放大风险，不能保证账号不会被限流或采取其他措施。
需要在路由或版本变动后做更高覆盖的人工评估时，运行
`python3 /opt/cliproxyapi/cpa-health.py quality-eval`。它对每条暴露路由发出版本化的
推理、JSON 指令遵循、受控 tool-call 结构和固定长上下文样例，且不打印正文；它只能
发现相对回归，不能证明真实底层模型身份或长期质量。更新失败回滚仅恢复旧 Compose；备份目录使用不可复用的
高精度时间戳，已存在的目标目录直接拒绝，避免覆盖旧回滚点。更新成功且验收通过后才执行有界清理：备份目录保留最新 8 个，镜像只保留
当前运行镜像和本次更新前的回滚镜像；上游不可用、验收失败或回滚路径不执行
清理。`--check` 不执行生成检查，也不清理文件。错误请求转储
（`auth/logs/error-*.log`）按 24 小时保留期单独清理：每日无更新路径与更新成功
路径都会删除过期转储（`PRUNE scope=error_dumps`），防止积压重复
2026-09-17 那种 doctor 读取超时。
updater 每次运行还会把 `auth/logs` 修复为 `0700`、把保留的错误转储修复为
`0600`；strict doctor 和 `-Apply` 在读回时都阻断权限漂移。权限修复不读取或打印
转储正文，也不改变转储的 24 小时保留策略；CPA 自身最多保留 5 个错误文件。
strict doctor 还会扫描活动 `type=codex` OAuth JSON 的到期元数据、保留错误转储的
响应侧段落与最近 7 天容器日志中的刷新失败信号。请求正文永不参与 OAuth 判定；
每个匹配转储只计一个事件，后续成功刷新会把更早的转储信号标为已消解。输出仅含
剩余天数/小时、刷新年龄、计数和不完整覆盖范围，不输出 token；未消解的刷新失败、
到期或无法解析活动 token 到期时间会阻断 doctor。
剩余时间门禁按精确小时对齐上游刷新节奏：CLIProxyAPI 只在到期前 24 小时自动刷新
codex OAuth（按到期时间调度、最长 30 秒唤醒校正、5 分钟失败退避），因此进入 72 小时仅显示非阻断
`WARN_RENEWAL_WINDOW`；进入 24 小时自动刷新窗口并耗尽 2 小时调度宽限仍未滚动
（剩余 ≤22 小时）才判 `ACTION_REQUIRED` 阻断——取整天数阈值会提前近 24 小时
误报，已弃用。
OAuth 缺席时仅报告
`oauth_monitor=ABSENT_OPTIONAL`，因为非 OAuth 路由仍可独立提供服务。
doctor 的 `==cache-usage==` 段聚合真实业务流量的缓存遥测：从内存 usage 队列
（需 `usage-statistics-enabled: true`，保留期上限 3600 秒）按 provider/model lane 汇总
input/cache_read/cached/cache_creation token 与聚合命中率。命中率按 lane 语义
取分子：deepseek 系 input 不含缓存命中（`hit_ratio = cache_read/input`），
OpenAI/codex 系 cached 是 input 的子集（`hit_ratio = cached/input`），混用公式
会出现比率超过 1 或减半的假象。doctor 默认不消费该队列；只有同时设置
`-ConsumeUsageQueue -AcknowledgeUsageQueueConsumption` 才执行一次明确的观察。
usage-queue 是 destructive raw-record API，抓取和归约在同一 Python 进程完成，
原始记录不进入 shell 变量、命令行或输出；只输出模型名与数字，不输出 session、请求 ID
或原始记录。覆盖率受内存保留期限制
（自上次消费起 ≤1 小时），因此长期命中率仍以 `cache-canary` 受控实测与客户端
usage 透传为准，本段用于观察业务趋势而非精确核算。
doctor 的 `==model-substitution==` 段在运行版至少为 v7.3.8 时统计最近 7 天容器日志中上游静默模型替换；旧版本明确报告 `UNAVAILABLE_VERSION`，不会把缺少观测能力误报为零事件。
WARN（CLIProxyAPI v7.3.8 起默认开启，格式见 `usage_helpers.go`，仅含匿名
auth_index）的出现次数；v7.3.7 及更早版本没有该观测能力。该段只计数、不回显
日志行、不作为严格门禁。
更新器在任何 provider 探针前若无法把错误转储收紧到上述权限，会以
`SECURITY_BLOCK` 拒绝本次探针/更新，不以 warning 继续执行。
更新前会只读检查备份根目录不是软链接、权限为 `700` 且文件系统至少保留
2 GiB 可用空间；空间不足或备份目录异常时拒绝更新并保留现状。doctor 会输出
当前 access log 的 HTTP/上游状态和限流标记汇总，以及保留错误文件（最近 7 天
内最新的 30 个）中的 overload 标记数量；对 499 客户端中断还会汇总
`request_time` 的 count/min/p50/max——紧簇（如 ~45.0s）即可实证调用端固定
总超时签名（2026-09-20 实测 198 个 499 中 197 个落在 45.04s 簇，确认调用端
固定 45 秒总超时；唯一离群 6.4s 为真实取消）。安全 access log 只增加不含随机
公网路径的 `route_class`（`models`、`chat`、`responses`、`other`），用于把
499/502/503 按请求类别定位；旧日志会标为 `legacy_unknown`。这些是定位信号，不是 provider
封禁或恢复的证明。日志还只记录上游 `Retry-After` 的类别
(`absent`、`seconds`、`other`)，不保存原始 header；503 的本地/上游归因先看
`upstream_status`，再按耗时分桶，避免把上游快速 503 误判为本地冷却。
部署此脚本属于远端写入，须遵循单机备份、回滚和复验流程，不能当作默认 doctor。
管理面有两种受认可状态：完全关闭（`allow-remote: false`，doctor 报
`management-remote=DISABLED`），或"loopback 加密钥"（`allow-remote: true` 且
`secret-key` ≥ 32 字符，doctor 报 `management-remote=LOOPBACK_KEYED`）——后者用于
经 SSH 隧道访问管理面板：docker-proxy 转发后容器内看到的源 IP 不是 127.0.0.1，
纯 loopback 判定对隧道访问必然 403；8317 端口绑定的 loopback-only 断言与
`nginx-no-management-route` 检查共同保证公网暴露面不因开启而扩大。CPA 自带
管理密钥 5 次失败封 30 分钟；`secret-key` 支持明文（首次加载时自动 bcrypt 哈希
并回写 config.yaml）或直接写哈希。`-Apply` 在 `allow-remote: true` 而密钥不足
32 字符时拒绝执行；`remote-management` 段不在 Apply 改动白名单内，直接落在
config.yaml 上的状态会被原样保留。
本次落地及公网 key / 缓存验证见
[`20260913-bwg-cpa-update.md`](docs/change-evidence/20260913-bwg-cpa-update.md)。
后续风控收口见 [`20260913-bwg-cpa-risk-closeout.md`](docs/change-evidence/20260913-bwg-cpa-risk-closeout.md)。
当前已撤销该记录中的全入口总并发 3，只保留每 IP 并发 6、10r/s 和 burst 10（排队处理，不使用 nodelay）；
OAuth、GLM、r1 不再共享全局 3 请求上限。任何后续账号级限流须依据实际负载，
不把入口阈值当作上游官方配额或防封号保证。修正验证见
[`20260913-bwg-cpa-global-limit-removal.md`](docs/change-evidence/20260913-bwg-cpa-global-limit-removal.md)。
真实 timer 触发与隔离过载/回滚验收见
[`20260913-bwg-cpa-controlled-acceptance.md`](docs/change-evidence/20260913-bwg-cpa-controlled-acceptance.md)。
健康检查区分“目录接口失败/越权模型”与“冷却导致模型暂时缺席”；后者不再误触发版本回滚。
本次 guardrail/fail2ban 远端最终收口见
[`20260913-bwg-cpa-guardrails-closeout.md`](docs/change-evidence/20260913-bwg-cpa-guardrails-closeout.md)。
updater 备份健康检查与远端投影复验见
[`20260913-bwg-cpa-updater-backup-health.md`](docs/change-evidence/20260913-bwg-cpa-updater-backup-health.md)。

`scripts/cpa_bwg_guardrails.ps1` 是只针对 `bwg` 的 CPA 风险收紧入口，默认只读；它不会连接或修改 `zz`。部署形态固定保留公网 Nginx TLS 入口和随机 capability path，不改成 SSH tunnel、VPN 或仅内网监听：宿主机通过 Compose 将 CPA 发布为 `127.0.0.1:8317`，Nginx 继续对外监听 `8443`；容器内 `config.yaml` 的 `host: 0.0.0.0` 是让 Nginx 经容器端口访问的预期值，不等于宿主公网暴露。
公网 gateway 同时固定校验 `client_max_body_size 32m`、`client_body_buffer_size
128k`、SSE `proxy_buffering off` 以及 300s 读写超时；这些参数用于避免大请求或
流式响应在传输层被截断或反复落盘缓冲，不能替代 provider 账号/模型级配额控制。
Compose 侧为容器 stdout 日志固定 `json-file` 轮转（`max-size=32m`、`max-file=3`），
doctor 会校验其生效；镜像内无有界轮转属上游默认，依赖该显式配置。

先执行脱敏 doctor：

```powershell
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg
```

默认 doctor 是严格契约检查；公网监听、随机路径、CPA loopback、Compose 端口绑定、合并后的 Nginx 路由或裸/错误路径语义发生漂移时返回非零。只想观察未收紧的旧状态时才使用：

```powershell
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -Observe
```

doctor 也检查安全访问日志的时间戳和 fail2ban 实际文件监控。Debian 默认
`backend=systemd` 不会读取 Nginx 文件日志，因此 `cpa-gateway` jail 必须显式
`backend=polling`，`logpath=/var/log/nginx/cpa_gateway.access.log tail`。
`tail` 避免启用时将旧的无时间戳日志当作当前失败；日志保留状态、入口限流结果、耗时和时间，
不记录随机路径或 Authorization。修改 jail 后应只重载该 jail，并用低于阈值的
单次公网 401 验证 `Total failed` 增长；服务 active 或正则匹配通过均不足以证明计数生效。

确认影响和回滚后，才执行单机 apply：

```powershell
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -Apply
```

该 apply 会读取仓库根默认私有 `- 副本.env`（也可用 `-ProviderEnvPath` 指定），路由映射
由 `scripts/remote/cpa_provider_routes.json` 管理，包含 `gpt-6-luna` 的 ChatGPT Plus OAuth lane；当前引用槽位为
`1/2/3/4/5`。槽位 2 把上游 GPT-6 Astra 和 GPT-5.6 Sol 分别映射到 `-cii` 客户端别名。
第 3 槽固定到
`http://35.213.82.91:8003/v1`；CPA 会将该槽 API key
以明文发送给中转。该明文 HTTP 是用户明确保留的精确例外，后续审查不得自动禁用、改成 HTTPS 或移除该路由；仍须持续报告其传输风险。其他渠道仍须 HTTPS。脚本只把清单引用的 `BASE_URL_n/API_KEY_n` 行
编码进 SSH 远端事务，不打印或写入 Git。槽位 3 把上游 GPT-5.6 Sol 映射到
`gpt-6-sol-91`，并把上游 GPT-5.6 Terra 映射为裸名 `gpt-5.6-terra`；槽位 4 只暴露 `glm-5.3`、`glm-5.3-flash` 两个裸名，其余 BigModel 模型（含 `glm-5.3-flashx`）均不投影；
槽位 5 当前 `/models` 返回的两个 DeepSeek 模型以原 ID 作为裸名。未列入清单的上游目录模型不会自动暴露。
远端 `cpa_policy.py`、`cpa-health.py` 和 apply 共用该清单校验 provider、alias 唯一性、
OAuth/API-key 排除与可见模型集合。上游 `/models` 目录响应只用于清单候选核实，
不代表生成语义已验收；只有显式矩阵模式会向已列出的模型发送生成请求。

apply 会在 `/root/cpa-guardrails-backup-<UTC.nano>/` 创建权限为 700 的备份，原子替换五个
`openai-compatibility` provider（`gpt-6-sol/astra` 固定在 ai.input.im；CIII 提供
`gpt-6-astra-cii`、`gpt-6-sol-cii`；槽位 3 提供 `gpt-6-sol-91` 与 `gpt-5.6-terra`；BigModel 只提供
`glm-5.3`、`glm-5.3-flash`；DeepSeek 两个目录模型使用原始 ID 裸名），
清理清单以外的旧 provider，并把 `gpt-6-luna` 留给 Codex OAuth，同时把所有
GPT/Codex provider 裸名排除出竞争的 OAuth/API-key 路由。它还收紧 `request-retry`、会话、
冷却和首包策略，投影版本管理的 updater/health/policy/
fail2ban 源文件及 provider route manifest，校验完整 semantic policy 和 updater 密钥提取，再重启 CPA、reload
Nginx 并复验模型目录、端口和现有 `/etc/logrotate.d/nginx`。它不复制 `auth/logs`，但
备份的 `config.yaml` 会包含变更前 provider 配置，必须按远端权限保护；关键校验失败会
按备份恢复本次涉及的 CPA/updater/health/policy/Nginx 文件，并输出 `ROLLBACK_VERIFIED`
或 `ROLLBACK_FAILED`。

该入口只保护公网入口和本地配置卫生，不能替代 provider 的账号/模型配额，也不能保证第三方 relay 或 OAuth/Coding Plan 账户永不限流或封禁。`request-retry=0` 的目标是避免网关放大失败请求；实际使用仍应遵守 provider 条款和速率限制，连续复验与自然使用观察应分开记录。`-Apply`、`-RotatePath` 与 `-DeactivateOAuthLuna` 和每日 updater 共享 `/opt/cliproxyapi/auto-update.lock` 的 `flock -n` 互斥：锁被占用时立即 `REFUSE cpa_busy` 退出，不排队等待，也不产生部分写入。

上游冷却状态陈旧（[#5639](https://github.com/router-for-me/CLIProxyAPI/issues/5639)、[#5770](https://github.com/router-for-me/CLIProxyAPI/issues/5770)）在 `save-cooldown-status: true` 持久化下（2026-09-08～09-16）曾使模型在配额恢复后持续缺席且重启无法清理 `.cds` 持久冷却；2026-09-16 起部署为 `false`——冷却为纯内存态，重启即清，`.cds` 不再生成。恢复口径见 [`docs/runbooks/cpa-stale-cooldown-recovery.md`](docs/runbooks/cpa-stale-cooldown-recovery.md)，保持人工个案执行。

doctor 的 `==cooldown-state==` 段会脱敏输出 `cooldown_state`、
`cooldown_next_retry_after`、`catalog_gpt6_luna` 与 `luna_state`（2026-09-24 起
`gpt-5.6-luna` 兼容别名已从 OAuth lane 排除，`gpt-6-luna` 是 luna 唯一目录名）。`active_cooldown` 是
正常退避，不能清除；仅当冷却已过期且 Luna 仍缺席时，
`stale_cooldown_suspected` 才允许按 runbook 做单文件、备份优先的人工恢复。该段不
输出 auth 文件名、凭据、响应正文，也不证明 provider 当前可生成内容。2026-09-16
起持久化关闭后该段失去 `.cds` 数据源（`cooldown_state` 恒为 `none`、
`stale_cooldown_suspected` 不再出现），滞留判据转为重启复验，见 runbook。
配置侧字段边界（v7.2.158 源码核实）：`excluded-models` 仅在 `codex-api-key`、
`gemini-api-key`、`claude-api-key` 等命名凭据条目上生效；`openai-compatibility`
条目没有该字段，写入会被静默忽略——模型范围请使用其 `models:` 声明控制。

随机路径是公网入口的 capability URL，不是认证替代品。普通 `-Apply` 会锁定现有路径，不会自动轮换；若怀疑路径泄露，使用单独的显式轮换操作，并通过安全渠道重新分发新入口：

```powershell
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -RotatePath
```

轮换操作会备份 Nginx 配置、生成新的 64-bit hex 路径（16 个 hex 字符）、原子替换并 reload，验证旧路径 404、新路径在未认证时为 401；失败会恢复备份。新路径不会输出到命令结果、Git、receipt 或 access log。轮换不是日常维护步骤，也不使用 SSH tunnel 作为数据面替代。

应急登出使用显式开关，这是唯一的凭据销毁入口：

```powershell
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -DeactivateOAuthLuna
```

该事务先停止 CPA，再从 `/root`、CPA 备份目录与活动 auth 目录删除全部 Codex
OAuth JSON；不制作任何备份，也不编辑 config.yaml（config 级
`oauth-excluded-models` 已把重登范围约束在 luna），任何拓扑意外都会 `REFUSE`
并保留文件。现拓扑（2026-09-24 起）裸 `gpt-6-luna` 是 OAuth lane 唯一暴露名
（旧兼容别名 `gpt-5.6-luna` 已加入 `oauth-excluded-models` 排除），且唯一来源就是 ChatGPT
Plus OAuth，因此登出后目录中 luna 直接消失，其余稳定裸路由（`glm-5.3-flash`、
`deepseek-flash`、ai.input.im 的 `gpt-6-sol` / `gpt-6-astra`、CIII 的两个 `-cii` 别名及
槽位 3 的 `gpt-6-sol-91` / `gpt-5.6-terra` 路由）必须存活才判定成功。
`OAUTH_REMOVAL_VERIFIED=yes` 只证明 VPS 本地不再持有可刷新 OAuth 材料，不证明
provider 侧会话已吊销；吊销需走账号官方安全控制，重新接入走受支持的交互式
device-login 流程，详见
[`docs/runbooks/cpa-oauth-luna-slot.md`](docs/runbooks/cpa-oauth-luna-slot.md)。

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
`-Apply` 会先备份两个 wrapper 与当前 crontab；写入、语法复验或 cron 安装失败会恢复备份并报告 `ROLLBACK_VERIFIED`/`ROLLBACK_FAILED`，成功时输出 `APPLY_BACKUP_DIR` 供后续人工回滚。它仍必须逐台执行，不能替代升级后的真实服务与端口复验。

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
