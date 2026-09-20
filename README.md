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

## 远端维护入口

### bwg CPA 公网网关防护

CPA 自动更新的受版本管理脚本为 `scripts/remote/cpa-auto-update.sh`，部署到
`/opt/cliproxyapi/auto-update.sh`，由既有 `cliproxyapi-update.timer` 每日 UTC
04:00–04:30 调用（2026-09-18 起从每周改为每日：候选 72h 成熟期是真正的节拍门，
每日空跑成本仅一次 luna 冒烟，同时让成熟版本最多晚一天收编，
）。默认执行更新；`bash /opt/cliproxyapi/auto-update.sh --check`
仅检查候选并写既有更新日志，不修改服务。候选必须同时存在于官方 GitHub release
和 Docker Hub，并在两处均满 72 小时；默认仅在当前 major/minor 线内选择最高 patch，
minor/major 升级均需先做独立评审和 canary，不因更新鲜版本存在而跳过成熟版本，不降级。
发现成熟的 minor 或 major 候选时仅写 `MINOR_CANDIDATE available=<tag>` 或
`MAJOR_CANDIDATE available=<tag>` 日志（doctor 的 timer 段会带出），不做任何升级动作。
镜像固定 tag + digest，文件锁避免重叠执行；自动更新备份且只回滚它实际修改的
Compose 镜像声明，绝不删除、覆盖或回放 auth 凭据文件。OAuth 刷新可在候选镜像
运行期间轮换 token，凭据快照只能作为受控人工灾难恢复输入，不属于自动镜像回滚。
配套 `scripts/remote/cpa-health.py` 与 `scripts/remote/cpa_policy.py` 部署到同目录：
CPA v7.3.7 保留 `codex.stream-bootstrap-buffering: true` 以便在上游把
`server_is_overloaded` 藏在流内握手之后时进行正确分类；同时固定
`codex.stream-bootstrap-timeout: "20s"`，把慢 provider 的首包 bootstrap 等待设为
有界值，避免无限期延迟下游响应头。该上限不改变 provider 重试策略，也不把上游错误
变成本地成功；响应头提前提交后，后续流内错误仍由客户端按流语义处理。字段语义以
[CLIProxyAPI v7.3.7 官方配置](https://github.com/router-for-me/CLIProxyAPI/releases/tag/v7.3.7)
为准。
更新前使用单个代表性路由
（`gpt-5.6-luna`，走 ChatGPT Plus OAuth 槽位；OAuth 登出期间目录不完整，
生成检查自动按 exit 10 暂缓，不误报本地故障）做低频
生成检查，失败则暂缓；更新后
本地契约失败回滚并确认旧服务就绪；暂时上游失败只做本地 readiness 复验，不重复发送
生成请求，保留本地就绪镜像并以 exit 10 报未验收。目录瞬态 `408/429/5xx` 只做一次
请求并立即停止；只有 HTTP 200 但模型仍在注册时才允许最多两次短间隔复验，避免健康
检查自身放大 provider 限流。无新版本时也做一次 Luna 健康检查，可解除先前未验收状态；
更新器不再自动调用 `relay-soft`，因此每日定时路径不会额外发送 Sol/Terra generation。
目录已验证后仍可显式调用 `relay-soft`；当前软腿观察的是
`ai.input.im` 的 `gpt-5.6-sol` / `gpt-5.6-terra`，结果记录为
`HEALTH_OK|RELAY_DEGRADED`，且仍不参与任何更新决策。该通道属于第三方中转，可能出现
403、408/429/5xx、账号风控、上游模型
缺席或质量回退；sol/terra 只应作为非敏感备用通道使用，
敏感内容走 luna（OAuth）、`glm-5.3-flash` 或 `deepseek-flash`。2026-09-18 起 OAuth
侧 `oauth-excluded-models` 追加 `codex-*`、`gpt-5.7*`、`gpt-6*` 通配：上游新模型族
优先在该清单 fail-closed，目录健康门现在要求五个允许 ID 的裸集合（基础三路加
ai.input.im 的 Sol/Terra），未知 prefix
也直接失败（目录阶段的本地契约失败为 exit 20；目录已通过后，单路由 403 归类为
`UPSTREAM_UNAVAILABLE`，不把上游账号/路由决定误报为本地配置错误）。所有真实
generation、quality 和 cache 探针共享 `/opt/cliproxyapi/health-probe.lock` 的非阻塞
`flock`；并发探针立即输出 `PROBE_ALREADY_RUNNING`，不排队、不重试。
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
`UPSTREAM_UNAVAILABLE`，不误报为本地契约故障。ai.input.im 路由的 sol/terra 会进入
显式生成矩阵（`generation-all` / `quality-*`），定时门仍固定以 Luna 为目标。
上游即使返回 HTTP 200，只要响应体不是合法 JSON，也按
`UPSTREAM_UNAVAILABLE` / `RELAY_DEGRADED` 处理；不把异常 200 当作成功，不重试，
也不做内容包装后继续转发。
需要在路由或版本变动后做更高覆盖的人工评估时，运行
`python3 /opt/cliproxyapi/cpa-health.py quality-eval`。它对每条暴露路由发出版本化的
推理、JSON 指令遵循、受控 tool-call 结构和固定长上下文样例，且不打印正文；它只能
发现相对回归，不能证明真实底层模型身份或长期质量。更新失败回滚仅恢复旧 Compose；备份目录使用不可复用的
高精度时间戳，已存在的目标目录直接拒绝，避免覆盖旧回滚点。更新成功且验收通过后才执行有界清理：备份目录保留最新 8 个，镜像只保留
当前运行镜像和本次更新前的回滚镜像；上游不可用、验收失败或回滚路径不执行
清理。`--check` 不执行生成检查，也不清理文件。错误请求转储
（`auth/logs/error-*.log`）按 7 天保留期单独清理：每日无更新路径与更新成功
路径都会删除过期转储（`PRUNE scope=error_dumps`），防止积压重复
2026-09-17 那种 doctor 读取超时。
updater 每次运行还会把 `auth/logs` 修复为 `0700`、把保留的错误转储修复为
`0600`；strict doctor 和 `-Apply` 在读回时都阻断权限漂移。权限修复不读取或打印
转储正文，也不改变转储的 7 天保留策略。
strict doctor 还会扫描活动 `type=codex` OAuth JSON 的到期元数据和最近 7 天的
`invalid_grant`/刷新失败信号，只输出剩余天数、刷新年龄和计数，不输出 token；到期、
刷新失败或无法解析活动 token 到期时间会阻断 doctor。剩余天数门禁对齐上游刷新节奏：
CLIProxyAPI 只在到期前 24 小时自动刷新 codex OAuth，因此仅剩 1 天以内（刷新点已到或
已过而未滚动）才判 `ACTION_REQUIRED` 阻断，2-7 天为非阻断 `WARN_RENEWAL_WINDOW`。
OAuth 缺席时仅报告
`oauth_monitor=ABSENT_OPTIONAL`，因为非 OAuth 路由仍可独立提供服务。
doctor 的 `==model-substitution==` 段统计最近 7 天容器日志中上游静默模型替换
WARN（CLIProxyAPI v7.3.8 起默认开启，格式见 `usage_helpers.go`，仅含匿名
auth_index）的出现次数；v7.3.7 及更早版本上恒为 0，属预期而非"无替换"证明。
该段只计数、不回显日志行、不作为严格门禁。
更新器在任何 provider 探针前若无法把错误转储收紧到上述权限，会以
`SECURITY_BLOCK` 拒绝本次探针/更新，不以 warning 继续执行。
更新前会只读检查备份根目录不是软链接、权限为 `700` 且文件系统至少保留
2 GiB 可用空间；空间不足或备份目录异常时拒绝更新并保留现状。doctor 会输出
当前 access log 的 HTTP/上游状态和限流标记汇总，以及保留错误文件（最近 7 天
内最新的 30 个）中的 overload 标记数量；这些是定位信号，不是 provider 封禁或
恢复的证明。
部署此脚本属于远端写入，须遵循单机备份、回滚和复验流程，不能当作默认 doctor。
本次落地及公网 key / 缓存验证见
[`20260913-bwg-cpa-update.md`](docs/change-evidence/20260913-bwg-cpa-update.md)。
后续风控收口见 [`20260913-bwg-cpa-risk-closeout.md`](docs/change-evidence/20260913-bwg-cpa-risk-closeout.md)。
当前已撤销该记录中的全入口总并发 3，只保留每 IP 并发 6、10r/s 和 burst 20；
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

该 apply 会读取仓库根默认私有 `- 副本.env`（也可用 `-ProviderEnvPath` 指定），只取
`BASE_URL_1/API_KEY_1` 到 `BASE_URL_3/API_KEY_3`，并在内存中校验其分别对应
`ai.input.im/v1`、`open.bigmodel.cn`、`api.deepseek.com`；key 不打印、不写 Git。它会在
`/root/cpa-guardrails-backup-<UTC.nano>/` 创建权限为 700 的备份，原子替换三类
`openai-compatibility` provider（把 `gpt-5.6-sol/terra` 放到 ai.input.im、GLM 放到
官方 Coding Plan、DeepSeek 放到官方 API），删除旧 `35.213.82.91:8003`/`relay-8003`，
并收紧 `request-retry`、会话/冷却/首包策略，投影版本管理的 updater/health/policy/
fail2ban 源文件，校验完整 semantic policy 和 updater 密钥提取，再重启 CPA、reload
Nginx 并复验模型目录、端口和现有 `/etc/logrotate.d/nginx`。它不复制 `auth/logs`，但
备份的 `config.yaml` 会包含变更前 provider 配置，必须按远端权限保护；关键校验失败会
按备份恢复本次涉及的 CPA/updater/health/policy/Nginx 文件，并输出 `ROLLBACK_VERIFIED`
或 `ROLLBACK_FAILED`。

该入口只保护公网入口和本地配置卫生，不能替代 provider 的账号/模型配额，也不能保证第三方 relay 或 OAuth/Coding Plan 账户永不限流或封禁。`request-retry=0` 的目标是避免网关放大失败请求；实际使用仍应遵守 provider 条款和速率限制，连续复验与自然使用观察应分开记录。`-Apply`、`-RotatePath` 与 `-DeactivateOAuthLuna` 和每日 updater 共享 `/opt/cliproxyapi/auto-update.lock` 的 `flock -n` 互斥：锁被占用时立即 `REFUSE cpa_busy` 退出，不排队等待，也不产生部分写入。

上游冷却状态陈旧（[#5639](https://github.com/router-for-me/CLIProxyAPI/issues/5639)、[#5770](https://github.com/router-for-me/CLIProxyAPI/issues/5770)）在 `save-cooldown-status: true` 持久化下（2026-09-08～09-16）曾使模型在配额恢复后持续缺席且重启无法清理 `.cds` 持久冷却；2026-09-16 起部署为 `false`——冷却为纯内存态，重启即清，`.cds` 不再生成。恢复口径见 [`docs/runbooks/cpa-stale-cooldown-recovery.md`](docs/runbooks/cpa-stale-cooldown-recovery.md)，保持人工个案执行。

doctor 的 `==cooldown-state==` 段会脱敏输出 `cooldown_state`、
`cooldown_next_retry_after`、`catalog_luna` 与 `luna_state`。`active_cooldown` 是
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
并保留文件。现拓扑（2026-09-18 起）裸 `gpt-5.6-luna` 的唯一来源就是 ChatGPT
Plus OAuth，因此登出后目录中 luna 直接消失，其余稳定裸路由（`glm-5.3-flash`、
`deepseek-flash`、ai.input.im 的 `gpt-5.6-sol` / `gpt-5.6-terra`）必须存活才判定成功。
`OAUTH_REMOVAL_VERIFIED=yes` 只证明 VPS 本地不再持有可刷新 OAuth 材料，不证明
provider 侧会话已吊销；吊销需走账号官方安全控制，重新接入走受支持的交互式
device-login 流程，详见
[`docs/runbooks/cpa-oauth-luna-slot.md`](docs/runbooks/cpa-oauth-luna-slot.md)。

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
