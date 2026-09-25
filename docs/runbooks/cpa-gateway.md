# bwg CPA 公网网关运行手册

本页聚合 CPA（CLIProxyAPI）网关的全部运维细节：更新器策略、健康探针、
doctor 门禁、guardrails 入口与应急开关。README 只保留定位与边界；本页是
操作细节的唯一展开处。风控事件的分级处置见
[cpa-ban-throttle-incident-response.md](cpa-ban-throttle-incident-response.md)，
凭据生命周期见 [cpa-oauth-failure-recovery.md](cpa-oauth-failure-recovery.md)
与 [cpa-oauth-luna-slot.md](cpa-oauth-luna-slot.md)，冷却滞留见
[cpa-stale-cooldown-recovery.md](cpa-stale-cooldown-recovery.md)，
手动回滚见 [cpa-manual-rollback.md](cpa-manual-rollback.md)。

## 入口与部署形态

`scripts/cpa_bwg_guardrails.ps1` 是只针对 `bwg` 的 CPA 风险收紧入口，默认
只读；它不会连接或修改 `zz`。部署形态固定保留公网 Nginx TLS 入口和随机
capability path，不改成 SSH tunnel、VPN 或仅内网监听：宿主机通过 Compose 将
CPA 发布为 `127.0.0.1:8317`，Nginx 对外监听 `8443`；容器内 `config.yaml` 的
`host: 0.0.0.0` 是让 Nginx 经容器端口访问的预期值，不等于宿主公网暴露。

```powershell
# 默认：严格 doctor（契约失败返回非零）
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg
# 只观察未收紧的旧状态（终判为 OBSERVE_OK / OBSERVE_FAILED，不退出非零）
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -Observe
# 确认影响和回滚后的单机 apply
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -Apply
# 随机路径轮换（怀疑泄露时，非常规步骤）
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -RotatePath
# 应急登出：唯一的凭据销毁入口（不可逆）
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -DeactivateOAuthLuna
```

`-Apply`、`-RotatePath`、`-DeactivateOAuthLuna`、每日 updater、系统维护、内核
维护和通用远端 adapter 共享 `/run/vps-ssh-launcher-maintenance.lock` 的
`flock -n` 互斥：锁被占用时立即拒绝，不排队等待，也不产生部分写入。

## 更新器策略（cpa-auto-update.sh）

受版本管理脚本为 `scripts/remote/cpa-auto-update.sh`，部署到
`/opt/cliproxyapi/auto-update.sh`，由 `cliproxyapi-update.timer` 每日 UTC
04:00–04:30 调用（2026-09-18 起从每周改为每日：候选 72h 成熟期是真正的节拍
门，让成熟版本最多晚一天收编）。要点：

- 默认执行更新；无成熟候选时只做本地 readiness 与最近 24 小时刷新失败标记
  检查，不发送 provider generation。`bash /opt/cliproxyapi/auto-update.sh
  --check` 仅检查候选并写既有更新日志，不修改服务，也不清理文件。
- 候选必须同时存在于官方 GitHub release 和 Docker Hub，并在两处均满 72
  小时；默认仅在当前 major/minor 线内选择最高 patch。minor/major 升级均需
  先做独立评审和 canary：发现成熟候选时仅写 `MINOR_CANDIDATE available=<tag>`
  或 `MAJOR_CANDIDATE available=<tag>` 日志（doctor 的 timer 段会带出），不做
  任何升级动作。不因更新鲜版本存在而跳过成熟版本，不降级。
- 镜像固定 tag + digest；文件锁避免重叠执行。更新前只读检查备份根目录不是
  软链接、权限为 `700` 且文件系统至少保留 2 GiB 可用空间；异常即拒绝更新。
- 自动更新备份且只回滚它实际修改的 Compose 镜像声明，绝不删除、覆盖或回放
  auth 凭据文件。OAuth 刷新可在候选镜像运行期间轮换 token，凭据快照只能作
  为受控人工灾难恢复输入（见 [cpa-manual-rollback.md](cpa-manual-rollback.md)），
  不属于自动镜像回滚。
- 更新前用单个代表性非 OAuth 路由（`glm-5.3-flash`，走官方 GLM Coding
  Plan）做低频生成检查，失败则暂缓；OAuth 登出期间 Luna 目录不完整，显式
  Luna 检查自动按 exit 10 暂缓，不误报本地故障。更新后本地契约失败回滚并
  确认旧服务就绪；暂时上游失败只做本地 readiness 复验，不重复发送生成请求，
  保留本地就绪镜像并以 exit 10 报未验收。
- 目录瞬态 `408/429/5xx` 只做一次请求并立即停止；只有 HTTP 200 但模型仍在
  注册时才允许最多两次短间隔复验，避免健康检查自身放大 provider 限流。
  无新版本时不做 Luna generation；解除先前 `UNVERIFIED` 状态由人工显式执行
  一次低频探针。更新器不自动调用 `relay-soft`，每日无候选路径不会发送
  Luna/Sol/Terra/Astra generation。
- 更新成功且验收通过后才执行有界清理：备份目录保留最新 8 个，镜像只保留
  当前运行镜像和本次更新前的回滚镜像；上游不可用、验收失败或回滚路径不
  执行清理。错误请求转储（`auth/logs/error-*.log`）按 24 小时保留期单独
  清理（`PRUNE scope=error_dumps`），防止积压重复 2026-09-17 那种 doctor
  读取超时。
- updater 每次运行把 `auth/logs` 修复为 `0700`、保留的错误转储修复为
  `0600`；strict doctor 和 `-Apply` 读回时阻断权限漂移。权限修复不读取或
  打印转储正文。任何 provider 探针前若无法收紧上述权限，以 `SECURITY_BLOCK`
  拒绝本次探针/更新，不以 warning 继续。

## 版本与字段语义基线

当前 BWG fresh doctor 的运行版本以主机实际镜像 tag/digest 为准（字段语义
基线是 [CLIProxyAPI v7.3.7 官方配置](https://github.com/router-for-me/CLIProxyAPI/releases/tag/v7.3.7)，不代表当前运行版本）。CPA 保留
`codex.stream-bootstrap-buffering: true` 以便在上游把 `server_is_overloaded`
藏在流内握手之后时进行正确分类；同时固定 `codex.stream-bootstrap-timeout:
"20s"`，把慢 provider 的首包 bootstrap 等待设为有界值。该上限不改变 provider
重试策略，也不把上游错误变成本地成功；响应头提前提交后，后续流内错误仍由
客户端按流语义处理。

公网 gateway 同时固定校验 `client_max_body_size 32m`、
`client_body_buffer_size 128k`、SSE `proxy_buffering off` 以及 300s 读写
超时；这些参数用于避免大请求或流式响应在传输层被截断或反复落盘缓冲，不能
替代 provider 账号/模型级配额控制。Compose 侧为容器 stdout 日志固定
`json-file` 轮转（`max-size=32m`、`max-file=3`），doctor 会校验其生效；
镜像内无有界轮转属上游默认，依赖该显式配置。

## 路由清单与目录契约

路由映射由 `scripts/remote/cpa_provider_routes.json` 管理，包含
`gpt-6-luna` 的 ChatGPT Plus OAuth lane；当前引用槽位为 `1/2/3/4/5`：

- 槽位 1（ai.input.im）：`gpt-6-sol` / `gpt-6-astra` / `gpt-5.6-sol` 裸名与
  `gpt-image-2.5` 图像路由（图像为 optional，不参与 chat 生成冒烟）。
- 槽位 2（CIII）：上游 GPT-6 Astra / GPT-5.6 Sol 映射到 `gpt-6-astra-cii`、
  `gpt-6-sol-cii`。
- 槽位 3：固定到 `http://35.213.82.91:8003/v1`，CPA 会将该槽 API key 以明文
  发送给中转。**该明文 HTTP 是用户明确保留的精确例外**，后续审查不得自动
  禁用、改成 HTTPS 或移除该路由；仍须持续报告其传输风险。上游 GPT-5.6 Sol
  映射到 `gpt-6-sol-91`，上游 GPT-5.6 Terra 映射为裸名 `gpt-5.6-terra`。
- 槽位 4（BigModel）：只暴露 `glm-5.3`、`glm-5.3-flash` 两个裸名，其余
  BigModel 模型（含 `glm-5.3-flashx`）均不投影。
- 槽位 5（DeepSeek）：目录模型以原 ID 作为裸名。

未列入清单的上游目录模型不会自动暴露。远端 `cpa_policy.py`、
`cpa-health.py` 和 apply 共用该清单校验 provider、alias 唯一性、OAuth/API-key
排除与可见模型集合。上游 `/models` 目录响应只用于清单候选核实，不代表生成
语义已验收；只有显式矩阵模式会向已列出的模型发送生成请求。

`scripts/remote/cpa_provider_routes.json` 将 `gpt-6-luna` 显式映射到 ChatGPT
Plus OAuth lane；它不属于 `openai-compatibility` provider。两个 Sol 别名映射
到渠道目录中的上游 `gpt-5.6-sol`。CIII 仍保留为渠道，但 `codex-auto-review`、
`gpt-5.5`、`gpt-5.6`、`gpt-reserve` 四个旧别名继续从 OAuth/Codex API-key
路由排除。OAuth 侧保留 `codex-*` 和 `gpt-5.7*` 排除，让 `gpt-6-luna` 留在
OAuth。CLIProxyAPI 这里提供的是 OAuth 排除规则，不是请求级正向 allowlist；
移除旧 `gpt-6*` 排除以放行 Luna 后，未来 Codex GPT-6 新模型可能进入上游
目录。健康门会把未登记 ID 判为本地契约失败，但不能拦截直接推理请求；每次
上游目录变化后都需先审阅并更新精确路由策略。

目录健康门要求路由清单登记的模型 ID（清单派生，无硬编码名单）；尚未开放的
GPT-6 Luna 或 GPT-6 Sol 可缺席并标为未验证，未知模型和 prefix 仍直接失败
（目录阶段的本地契约失败为 exit 20；目录已通过后，单路由 403 归类为
`UPSTREAM_UNAVAILABLE`，不把上游账号/路由决定误报为本地配置错误）。上游即使
返回 HTTP 200，只要响应体不是合法 JSON，也按 `UPSTREAM_UNAVAILABLE` /
`RELAY_DEGRADED` 处理；不把异常 200 当作成功，不重试，也不做内容包装后继续
转发。

配置侧字段边界（v7.2.158 源码核实）：`excluded-models` 仅在 `codex-api-key`、
`gemini-api-key`、`claude-api-key` 等命名凭据条目上生效；
`openai-compatibility` 条目没有该字段，写入会被静默忽略——模型范围请使用其
`models:` 声明控制。

## 健康探针矩阵（cpa-health.py）

- `readiness`（定时路径）：本地目录契约 + 单次低频检查；`generation` 默认
  目标 `glm-5.3-flash`。缓存命中可见于 usage 透传；DeepSeek 官方缓存折扣
  语义见下文缓存段。
- `relay-soft`：目录已验证后可显式调用；当前软腿观察 `ai.input.im` 的
  `gpt-6-astra`，结果记录为 `HEALTH_OK|RELAY_DEGRADED`，不参与任何更新
  决策。该通道属于第三方中转，可能出现 403、408/429/5xx、账号风控、上游
  模型缺席或质量回退；sol/terra 只应作为非敏感备用通道使用，敏感内容走
  Luna（OAuth）、`glm-5.3-flash` 或 `deepseek-flash`。
- `generation-all`：需要检查全部已暴露路由时显式运行；会增加真实 provider
  请求，不由定时更新器调用。继续完成剩余模型的单次探针，逐行输出
  `model`、HTTP `status`、`latency_ms`、`finish` 和脱敏 `error_class`，不输出
  key、请求体或响应正文。GPT-6 Sol 即使已在 CPA 本地目录登记，也可能尚未
  被 ai.input.im 开放；显式探针的 403/404 归类为 `UPSTREAM_UNAVAILABLE`，不
  自动重试。
- `quality-canary`：版本或路由变动后检查最小语义契约；对每条已暴露路由仅
  发送一次非敏感算术/JSON 请求，不输出正文，不能证明长期模型质量。只接受
  原始 JSON 或单层 `json` Markdown 围栏。
- `quality-eval`：需要更高覆盖的人工评估；发出版本化的推理、JSON 指令
  遵循、受控 tool-call 结构和固定长上下文样例，且不打印正文。只能发现相对
  回归，不能证明真实底层模型身份或长期质量。
- `cache-canary`：`python3 /opt/cliproxyapi/cpa-health.py cache-canary` 在
  `deepseek-flash` 上以同一短期随机 session 发送两次相同的安全长前缀请求，
  只输出模型、输入、缓存读/写/未命中 token 和命中比例，绝不输出 key、
  session、prompt 或响应正文；没有 provider telemetry 时以
  `CACHE_TELEMETRY_UNAVAILABLE` 明确失败，不宣称缓存已改善。不进定时器；
  OAuth、ai.input.im 与其他 provider 必须各自得到同类证据后才可作结论。

所有真实 generation、quality 和 cache 探针共享 `/opt/cliproxyapi/health-probe.lock`
的非阻塞 `flock`；并发探针立即输出 `PROBE_ALREADY_RUNNING`，不排队、不重试。
定时门固定以 `glm-5.3-flash` 为目标；DeepSeek 保留在显式矩阵中，Luna 只在
显式矩阵或人工指定的低频检查中参与。

## 缓存约束

只做不会改变 provider 语义的请求侧约束：稳定的系统提示和工具说明放在
prompt 前缀，时间戳、随机 ID、用户私有内容等动态部分放在后部；不跨用户
复用 session/cache key，不仅为追求命中率盲目打开 `support-prompt-cache-key`。
DeepSeek 的命中率以官方返回的 `prompt_cache_hit_tokens` /
`prompt_cache_miss_tokens` 观测，不把一次受控样本外推为长期收益；OAuth/Codex
路由和 ai.input.im 不套用 DeepSeek 或 OpenAI API 的缓存结论。

doctor 的 `==cache-usage==` 段聚合真实业务流量的缓存遥测：从内存 usage 队列
（需 `usage-statistics-enabled: true`，保留期上限 3600 秒）按 provider/model
lane 汇总 input/cache_read/cached/cache_creation token 与聚合命中率。命中率
按 lane 语义取分子：deepseek 系 input 不含缓存命中
（`hit_ratio = cache_read/input`），OpenAI/codex 系 cached 是 input 的子集
（`hit_ratio = cached/input`），混用公式会出现比率超过 1 或减半的假象。
doctor 默认不消费该队列；只有同时设置
`-ConsumeUsageQueue -AcknowledgeUsageQueueConsumption` 才执行一次明确的观察。
usage-queue 是 destructive raw-record API，抓取和归约在同一 Python 进程完成，
原始记录不进入 shell 变量、命令行或输出；只输出模型名与数字。覆盖率受内存
保留期限制（自上次消费起 ≤1 小时），长期命中率仍以 `cache-canary` 受控实测
与客户端 usage 透传为准。

## OAuth 到期与刷新监控

strict doctor 扫描活动 `type=codex` OAuth JSON 的到期元数据、保留错误转储的
响应侧段落与最近 7 天容器日志中的刷新失败信号。请求正文永不参与 OAuth
判定；每个匹配转储只计一个事件，后续成功刷新会把更早的转储信号标为已消解。
输出仅含剩余天数/小时、刷新年龄、计数和不完整覆盖范围，不输出 token；
未消解的刷新失败、到期或无法解析活动 token 到期时间会阻断 doctor。

剩余时间门禁按精确小时对齐上游刷新节奏（到期前 24h 自动刷新、最长 30 秒
唤醒校正、5 分钟失败退避）：进入 72h 显示非阻断 `WARN_RENEWAL_WINDOW`；
进入 24h 窗口且耗尽 2h 调度宽限仍未滚动（剩余 ≤22h）才判 `ACTION_REQUIRED`
阻断——取整天数阈值会提前近 24 小时误报，已弃用。OAuth 缺席时仅报告
`oauth_monitor=ABSENT_OPTIONAL`，因为非 OAuth 路由仍可独立提供服务。
处置流程见 [cpa-oauth-failure-recovery.md](cpa-oauth-failure-recovery.md)。

## 冷却状态

上游冷却状态陈旧（[#5639](https://github.com/router-for-me/CLIProxyAPI/issues/5639)、
[#5770](https://github.com/router-for-me/CLIProxyAPI/issues/5770)）在
`save-cooldown-status: true` 持久化下（2026-09-08～09-16）曾使模型在配额
恢复后持续缺席；2026-09-16 起部署为 `false`——冷却为纯内存态，重启即清，
`.cds` 不再生成。doctor 的 `==cooldown-state==` 段脱敏输出 `cooldown_state`、
`cooldown_next_retry_after`、`catalog_gpt6_luna` 与 `luna_state`（luna 在册性
以规范名 `gpt-6-luna` 为准；兼容别名 `gpt-5.6-luna` 同走 OAuth lane）。
`active_cooldown` 是正常退避，不能清除。持久化关闭后该段失去 `.cds` 数据源
（`cooldown_state` 恒为 `none`），滞留判据转为重启复验，恢复口径见
[cpa-stale-cooldown-recovery.md](cpa-stale-cooldown-recovery.md)，保持人工
个案执行。

## 访问日志与管理面

doctor 会输出当前 access log 的 HTTP/上游状态和限流标记汇总，以及保留错误
文件（最近 7 天内最新的 30 个）中的 overload 标记数量；对 499 客户端中断
汇总 `request_time` 的 count/min/p50/max——紧簇（如 ~45.0s）即可实证调用端
固定总超时签名（2026-09-20 实测 198 个 499 中 197 个落在 45.04s 簇）。安全
access log 只增加不含随机公网路径的 `route_class`（`models`、`chat`、
`responses`、`other`），用于把 499/502/503 按请求类别定位；旧日志标为
`legacy_unknown`。日志只记录上游 `Retry-After` 的类别（`absent`、`seconds`、
`other`），不保存原始 header；503 的本地/上游归因先看 `upstream_status`，再
按耗时分桶，避免把上游快速 503 误判为本地冷却。这些是定位信号，不是
provider 封禁或恢复的证明。

doctor 也检查安全访问日志的时间戳和 fail2ban 实际文件监控。Debian 默认
`backend=systemd` 不会读取 Nginx 文件日志，因此 `cpa-gateway` jail 必须显式
`backend=polling`，`logpath=/var/log/nginx/cpa_gateway.access.log tail`。
`tail` 避免启用时将旧的无时间戳日志当作当前失败；日志保留状态、入口限流
结果、耗时和时间，不记录随机路径或 Authorization。修改 jail 后应只重载该
jail，并用低于阈值的单次公网 401 验证 `Total failed` 增长；服务 active 或
正则匹配通过均不足以证明计数生效。

管理面有两种受认可状态：完全关闭（`allow-remote: false`，doctor 报
`management-remote=DISABLED`），或"loopback 加密钥"（`allow-remote: true` 且
`secret-key` ≥ 32 字符，doctor 报 `management-remote=LOOPBACK_KEYED`）——后者
用于经 SSH 隧道访问管理面板：docker-proxy 转发后容器内看到的源 IP 不是
127.0.0.1，纯 loopback 判定对隧道访问必然 403；8317 端口绑定的 loopback-only
断言与 `nginx-no-management-route` 检查共同保证公网暴露面不因开启而扩大。
CPA 自带管理密钥 5 次失败封 30 分钟；`secret-key` 支持明文（首次加载时自动
bcrypt 哈希并回写 config.yaml）或直接写哈希。`-Apply` 在 `allow-remote: true`
而密钥不足 32 字符时拒绝执行；`remote-management` 段不在 Apply 改动白名单
内，直接落在 config.yaml 上的状态会被原样保留。

## apply 细节

`-Apply` 会读取 `%APPDATA%\vps-ssh-launcher\providers.env` 默认私有 env（也可
用 `-ProviderEnvPath` 指定）。脚本只把清单引用的 `BASE_URL_n/API_KEY_n` 行
编码进 SSH 远端事务，不打印或写入 Git。

apply 在 `/root/cpa-guardrails-backup-<UTC.nano>/` 创建权限为 700 的备份，
原子替换五个 `openai-compatibility` provider，清理清单以外的旧 provider，把
`gpt-6-luna` 留给 Codex OAuth，并把所有 GPT/Codex provider 裸名排除出竞争的
OAuth/API-key 路由。它还收紧 `request-retry`、会话、冷却和首包策略，投影
版本管理的 updater/health/policy/fail2ban 源文件及 provider route manifest，
校验完整 semantic policy 和 updater 密钥提取，再重启 CPA、reload Nginx 并
复验模型目录、端口和现有 `/etc/logrotate.d/nginx`。它不复制 `auth/logs`，
但备份的 `config.yaml` 会包含变更前 provider 配置，必须按远端权限保护。
关键校验失败会按备份恢复本次涉及的 CPA/updater/health/policy/Nginx 文件，
并输出 `ROLLBACK_VERIFIED` 或 `ROLLBACK_FAILED`。

该入口只保护公网入口和本地配置卫生，不能替代 provider 的账号/模型配额，
也不能保证第三方 relay 或 OAuth/Coding Plan 账户永不限流或封禁。
`request-retry=0` 的目标是避免网关放大失败请求；实际使用仍应遵守 provider
条款和速率限制，连续复验与自然使用观察应分开记录。

## 路径轮换与应急登出

随机路径是公网入口的 capability URL，不是认证替代品。普通 `-Apply` 会锁定
现有路径，不会自动轮换；若怀疑路径泄露，使用 `-RotatePath` 显式轮换，并
通过安全渠道重新分发新入口。轮换操作会备份 Nginx 配置、生成新的 64-bit hex
路径（16 个 hex 字符）、原子替换并 reload，验证旧路径 404、新路径在未认证
时为 401；失败会恢复备份。新路径不会输出到命令结果、Git、receipt 或 access
log。轮换不是日常维护步骤，也不使用 SSH tunnel 作为数据面替代。

`-DeactivateOAuthLuna` 的语义、目录契约与恢复路径见
[cpa-oauth-failure-recovery.md](cpa-oauth-failure-recovery.md)。契约核心：先
停 CPA，再从 `/root`、CPA 备份目录与活动 auth 目录删除全部 Codex OAuth
JSON；不备份、不编辑 config.yaml；登出后目录契约由清单派生断言校验（清单
provider 别名必须存活、OAuth 别名必须消失、清单外 ID 即失败），任何拓扑
意外都会 `REFUSE` 并保留文件。`OAUTH_REMOVAL_VERIFIED=yes` 只证明 VPS 本地
不再持有可刷新 OAuth 材料，不证明 provider 侧会话已吊销。

## 相关证据

- [`20260913-bwg-cpa-update.md`](../change-evidence/20260913-bwg-cpa-update.md)：
  公网 key / 缓存验证落地。
- [`20260913-bwg-cpa-risk-closeout.md`](../change-evidence/20260913-bwg-cpa-risk-closeout.md)：
  后续风控收口。
- [`20260913-bwg-cpa-global-limit-removal.md`](../change-evidence/20260913-bwg-cpa-global-limit-removal.md)：
  撤销全入口总并发 3，只保留每 IP 并发 6、10r/s、burst 10（排队处理，不使用
  nodelay）；OAuth、GLM、r1 不再共享全局 3 请求上限。任何后续账号级限流须
  依据实际负载，不把入口阈值当作上游官方配额或防封号保证。
- [`20260913-bwg-cpa-controlled-acceptance.md`](../change-evidence/20260913-bwg-cpa-controlled-acceptance.md)：
  真实 timer 触发与隔离过载/回滚验收。
- [`20260913-bwg-cpa-guardrails-closeout.md`](../change-evidence/20260913-bwg-cpa-guardrails-closeout.md)：
  guardrail/fail2ban 远端最终收口（含"目录接口失败/越权模型"与"冷却导致
  模型暂时缺席"的区分）。
- [`20260913-bwg-cpa-updater-backup-health.md`](../change-evidence/20260913-bwg-cpa-updater-backup-health.md)：
  updater 备份健康检查与远端投影复验。
