# bwg CPA 公网网关运行手册

本页聚合 CPA（CLIProxyAPI）网关的全部运维细节：更新器策略、健康探针、
doctor 门禁、guardrails 入口与应急开关。README 只保留定位与边界；本页是
操作细节的唯一展开处。风控事件的分级处置见
[cpa-ban-throttle-incident-response.md](cpa-ban-throttle-incident-response.md)，
凭据生命周期见 [cpa-oauth-failure-recovery.md](cpa-oauth-failure-recovery.md)
与 [cpa-oauth-luna-slot.md](cpa-oauth-luna-slot.md)，冷却滞留见
[cpa-stale-cooldown-recovery.md](cpa-stale-cooldown-recovery.md)，
手动回滚见 [cpa-manual-rollback.md](cpa-manual-rollback.md)。

## 流式受控验收

`scripts/cpa_stream_acceptance.py` 对指定本机 sidecar 发起一次 Responses 请求，
不重试、不跟随重定向、不使用环境 HTTP 代理。`--manifest` 指向该监听实例的
真实 manifest；仅从唯一启用的 key 读取凭据，不通过命令行传 key。

```powershell
.\.venv\Scripts\python.exe scripts/cpa_stream_acceptance.py --manifest $manifestPath --model gpt-6-luna
# 上一请求结束后，才单次验收 Sol
.\.venv\Scripts\python.exe scripts/cpa_stream_acceptance.py --manifest $manifestPath --model gpt-6.1-sol
```

`STREAM_ACCEPTANCE` 为 PASS 且退出码 0，要求 HTTP 200、合法 SSE/JSON、非空
`response.output_text.delta`、预期短文本与成功 `response.completed`，期间没有
error/failed/incomplete。记录响应头、首事件、首文本与 completion 的独立时间；
短输出不构成持续 token 吞吐基准。默认 240 s 总期限包含排队与生成，超时返回
非零。此入口只证明受控请求，不代替 Desktop 完整回合或长期稳定性验收。

## 入口与部署形态

`scripts/cpa_bwg_guardrails.ps1` 是只针对 `bwg` 的 CPA 风险收紧入口，默认
只读；它不会连接或修改 `zz`。部署形态固定保留公网 Nginx TLS 入口和随机
capability path，不改成 SSH tunnel、VPN 或仅内网监听：宿主机通过 Compose 将
CPA 发布为 `127.0.0.1:8317`，Nginx 对外监听 `8443`；容器内 `config.yaml` 的
`host: 0.0.0.0` 是让 Nginx 经容器端口访问的预期值，不等于宿主公网暴露。

```powershell
# 默认：严格 doctor（契约失败返回非零）
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg
# 只观察未收紧的旧状态（终判为 DOCTOR_CONTRACT_OBSERVE_OK /
# DOCTOR_CONTRACT_OBSERVE_FAILED，不退出非零）
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -Observe
# 确认影响和回滚后的单机 apply
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -Apply
# 随机路径轮换（怀疑泄露时，非常规步骤）
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -RotatePath
# 应急登出：唯一的凭据销毁入口（不可逆）
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -DeactivateOAuthLuna
# 可逆隔离 / 恢复 OAuth lane（流量闸门，不销毁凭据、不重置配额）
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -QuarantineOAuthLuna
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -RestoreOAuthLuna
```

`-Apply`、`-RotatePath`、`-DeactivateOAuthLuna`、`-QuarantineOAuthLuna`、
`-RestoreOAuthLuna`、每日 updater、系统维护、内核维护和通用远端 adapter 共享
`/run/vps-ssh-launcher-maintenance.lock` 的 `flock -n` 互斥：锁被占用时立即
拒绝，不排队等待，也不产生部分写入。

## CPA 出站代理与管理查询

本节指 CPA 容器访问上游的代理，不是浏览器/Claude Code 的代理，也不是 Nginx
入站转发。上游维护者在 [#6395](https://github.com/router-for-me/CLIProxyAPI/issues/6395)
明确：`HTTP_PROXY` / `HTTPS_PROXY` 不是 CPA 支持的代理配置入口。部分模型请求
偶然继承 Go 默认 transport 的环境代理，不能保证管理接口或自定义 transport 同样继承。
管理侧 `api-call`（包括部分额度查询）未选中显式代理时直接连接，忽略环境代理。

`v8.0.29` 的 [管理接口实现](https://github.com/router-for-me/CLIProxyAPI/blob/v8.0.29/internal/api/handlers/management/api_tools.go)
仍有此行为；[v8 配置转换](https://github.com/router-for-me/CLIProxyAPI/blob/v8.0.29/internal/config/config_v8.go)
将旧全局字段 `proxy-url` 映射到 `requests.proxy-url`，canonical 字段存在时优先。
确有合法出站代理需求时，先核对运行版本、容器实际加载的配置、凭据覆盖与请求覆盖，
不要只设置容器环境变量。代理地址可能含认证信息，检查只输出是否配置，不打印值。

没有出站代理需求时，无须为了“出口一致”新增代理。本仓 provider transport override
受 `cpa_policy.py` / apply 拒绝，不能照搬 issue 向 provider 块添加 `proxy-url`；改变
出口策略须独立评审、备份与回滚。未读取 auth 文件时，不宣称已排除全部凭据级覆盖。
源码与配置检查只证明选择规则，不证明实际出口、账号资格或防封效果；不要通过携带
真实凭据的额度查询或生成请求来测出口，需复现时使用本地假上游和无效测试凭据。

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
- `config.yaml` 含全部 provider 明文 API key，strict doctor 现在也对其
  fail-closed（`config-permissions=owner-only`，即除属主外无任何读写位）；
  此前它只被 `stat` 打印、从不参与门禁。

## 版本与字段语义基线

当前 BWG fresh doctor 的运行版本以主机实际镜像 tag/digest 为准（字段语义
基线是 [CLIProxyAPI v8.0.2 官方配置](https://github.com/router-for-me/CLIProxyAPI/releases/tag/v8.0.2)，不代表当前运行版本）。CPA
**关闭** `codex.stream-bootstrap-buffering`（`false`，`stream-bootstrap-timeout`
固定为 `"0"`）。开启该开关时 CPA 会把下游响应头一直扣到上游开始生成为止：
2026-09-28 实测每轮 `gpt-6-luna` 的首字节要等 9.4–10.3 秒，之后整个握手
一次性爆出（分块间隔 p50=0ms），客户端在这段时间里看不到任何字节。关闭后
握手随上游首个事件立即下发，客户端立刻能看到流存活，模型推理时间不再被
误读成网关卡死。该开关原本换来的过载分类在本机是冗余的：Codex 凭据池只有
一个账号（`auth/` 下唯一 `*-plus.json`，没有可切换的凭据），而
`cpa-admission` 自己就能在 HTTP 200 响应体内识别 capacity marker —— 实测
6 小时 13 次 `status=200 capacity=true` —— 并据此打开对应 lane 熔断。关闭
后流内过载仍按流语义交给客户端，lane 冷却与 `Retry-After` 契约不变。
（v7.3.17 与 v8.0.2 的 bootstrap 实现逐字节同构，该结论与镜像版本无关。）

公网 gateway 同时固定校验 `client_max_body_size 32m`、
`client_body_buffer_size 128k`、SSE `proxy_buffering off` 以及 300s 读写
超时；`cpa_safe` 日志格式末尾追加 `upstream_header_time=$upstream_header_time`，
该字段测量 Nginx 收到上游响应头的等待时间，不等于首个 SSE 事件或首个文本
delta 的延迟；`upstream_time` 包含 admission 等待与上游响应过程，也不能独立
定位模型生成耗时。首字与文本间隔需要在客户端解析 SSE 后测量。新字段追加在末尾，
避免移位 `bytes=` / `limit_req=` 等既有解析锚点。这些参数用于避免大请求或
流式响应在传输层被截断或反复落盘缓冲，不能替代 provider 账号/模型级配额
控制。Compose 侧为容器 stdout 日志固定
`json-file` 轮转（`max-size=32m`、`max-file=3`），doctor 会校验其生效；
镜像内无有界轮转属上游默认，依赖该显式配置。

入口限流自身也是受校验的契约：doctor 断言 `limit_req zone=cpa_rl burst=10;`
指令在位（`gateway-per-ip-rate-limit=OK`），而不只是 `limit_req_zone` 声明和
日志里的 `limit_req_status` 变量；本地节流必须回答 `429`
（`gateway-throttle-status=429`），因为 nginx 默认的 `503` 会让自伤限流与上游
过载在状态码层面无法区分，也让客户端收不到退避信号。2026-09-09 已设置 429，
但直到 2026-09-26 才纳入版本化契约：`-Apply` 缺失即就地补齐并回写校验
（`ROLLBACK gateway_throttle_contract`），strict doctor 缺失即 fail-closed。

## CPA 流量分配与账号暴露边界

> [!IMPORTANT]
> **有入口限流 ≠ 有账号配额保护。** 本节说明两者的语义边界，避免将服务端
> 节流误读为 provider 配额的替代品。

### 入口限流的实际作用

公网 nginx 入口限流（`limit_req zone=cpa_rl burst=10 rate=10r/s`、
`limit_conn cpa_cc 20`）是**本地 per-client-IP 保护**，对抗的是：

- 单个客户端 IP 的突发请求放大
- 本地端口耗尽 / upstream 连接堆积

> [!IMPORTANT]
> **为什么是 20（2026-10-02 上调）。** 连接预算必须覆盖 admission 三条 lane
> 的持有连接上限：OAuth `2+4=6`、GLM `3+4=7`、DeepSeek `3+4=7`，合计
> `20`。旧值 `6` 与中间值 `12` 都曾在四会话自然流量中被撞满；被 admission
> 排队的请求在等待期间**一直占着 nginx 连接**，而每个 Desktop 会话还会打开
> 辅助 response/model 连接。新值 `20` 仍是有界 per-IP 保护，并未放开到无界。
> 判据：`limit_conn=REJECTED` + `upstream_status=-` + `request_time<1s`。
> 20 是三条 lane 的理论持有上限（6+7+7）；超出该值仍由 Nginx 连接保护拒绝。

被本地限流器拒绝的响应是 `429`，并带 `Retry-After: 1`：

```nginx
map "$limit_req_status:$limit_conn_status" $cpa_throttle_retry_after {
    default "";
    "~REJECTED" 1;
}
...
add_header Retry-After $cpa_throttle_retry_after always;
```

要点：

- 这个 `map` 只在 `$limit_req_status` 或 `$limit_conn_status` 为 `REJECTED`
  时给出非空值；nginx 对空值的 `add_header` 不会发出该头，因此
  `200`/`401`/`404` 与**上游透传的 `429`/`5xx` 都不会被伪造或覆盖**。
- `1` 是下界而非预测：`burst=10` 配 `10r/s` 时被拒的槽位约 1s 内回补，并发
  拒绝则随任一在途请求结束而解除。客户端仍应按自身退避策略处理。
- `add_header` 写在 **server 级**。这是安全的**前提**是文件内没有其它
  `add_header`：nginx 的 `add_header` 不做叠加继承，内层只要声明一个就会顶掉
  继承来的整组头。新增任何内层 `add_header` 时必须同时把这一行搬过去。
- strict doctor 用 `safe-throttle-retry-after=OK` 与
  `throttle-retry-after-map-count=1` 冻结该契约（既要求头存在，也要求它由
  throttle-status map 作用域约束，而不是无条件加）。

**受控压测验收**（可重复；必须用有效 key，否则 20 次 401 会触发 fail2ban
自伤封禁）：

```bash
# 从 VPS 本机对公网入口突发 60 个已认证 /v1/models 请求
# 期望：混合 200 与 429；429 全部带 Retry-After: 1，200 一个都不带
python3 - <<'PY'
import re, ssl, urllib.request, urllib.error
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import yaml
conf = Path('/etc/nginx/conf.d/cpa-gateway.conf').read_text()
prefix = re.search(r'/([0-9a-f]{16})/v1/', conf).group(1)
key = yaml.safe_load(Path('/opt/cliproxyapi/config.yaml').read_text())['api-keys'][0]
url = 'https://127.0.0.1:8443/%s/v1/models' % prefix
ctx = ssl._create_unverified_context()
def one(_):
    req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + key})
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
            return r.status, r.headers.get('Retry-After')
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get('Retry-After')
with ThreadPoolExecutor(max_workers=24) as pool:
    out = list(pool.map(one, range(60)))
print('codes', dict(Counter(str(c) for c, _ in out)))
ra = {}
for code, value in out:
    ra.setdefault(str(code), set()).add('none' if value is None else value)
print('retry-after', {k: sorted(v) for k, v in ra.items()})
PY
```

2026-09-26 实测：`codes {'200': 12, '429': 48}`、
`retry-after {'200': ['none'], '429': ['1']}`，窗口排空后单次请求
`CONTROL_STATUS=200` / `CONTROL_RETRY_AFTER=none`。

它**不能**代替 provider 的账号/模型级配额，原因如下：

| 维度 | 入口限流实际控制的 | 入口限流不控制的 |
|---|---|---|
| 作用粒度 | 每个客户端 IP | provider 账号、OAuth 订阅、Coding Plan 余额 |
| 计数依据 | nginx `$binary_remote_addr` | provider 侧的请求计数、token 消耗、日配额 |
| 对象 | 从 VPS 公网入口进入的流量 | provider 直接返回的 429/403/quota 错误 |

### 443 车道的入口契约（Desktop 实际路径）

ChatGPT desktop 走的是 **443**：`fq.sciman.top:443` → xray dokodemo-door
→ VLESS REALITY（`realitySettings.target = fq.sciman.top:33387`）→ nginx
`/etc/nginx/conf.d/subscribe.conf`（`listen 33387 ssl`）。也就是说
`00-cpa-443-http.conf` + `subscribe.conf` 这条 2026-09-30 以「后备车道」身份
上线、repo 此前只按计数容忍的路径，实际是桌面端的唯一入口；8443 车道只服务
直接指向 `:8443` 的客户端。

这条车道的入口限流与传输形态曾系统性落后于 8443 车道：

- `limit_conn cpa_cc443` 停在 `6`（2026-10-02 之前的值），而 8443 已按三条 lane
  的持有预算升到 `20`。2026-10-10 在维护锁下改为 `20`；备份
  `/root/cpa-443-lane-backup-20261010T025311Z/`。
- 未声明 `limit_req_status` / `limit_conn_status`，落入 nginx 默认的 `503`。
  本机 21 并发打该车道限流：修复前 `10×503` 且无 `Retry-After`，修复后
  `10×429` 且 `retry-after: 1`。2026-10-10 修复；备份
  `/root/cpa-443-lane-backup-20261010T025904Z/`。
- 缺 `client_body_buffer_size 128k;`（8443 车道 2026-09-15 已补）。默认
  `8k/16k` 下超过缓冲的请求体会先写进 `client_temp` 临时文件再转发。
  2026-10-10 实测 A/B：同一 40708 字节请求体经该车道发一次，修复前
  `error.log` 出现 1 条 `a client request body is buffered to a temporary
  file`，修复后 0 条；207208 字节仍落盘（与 8443 车道同界）。修复；备份
  `/root/cpa-443-lane-backup-20261010T033221Z/`。

strict doctor 现在按「车道存在才校验」冻结这三点：`gateway-443-lane=OK
conn-budget=<n> body-buffer=<n>`，其中预算与 `cpa-gateway.conf` 的
`limit_conn cpa_cc` 比较、缓冲与同文件的 `client_body_buffer_size` 比较，
都不钉字面量，避免两条车道再次分叉；同时要求
`proxy_buffering off;`、`proxy_read_timeout 300s;`、`proxy_send_timeout
300s;` 三条 SSE 传输指令在位（丢失 `proxy_buffering off` 会让 token 成块
到达，回落到默认 60s 读超时会截断长回合）。车道未投影时输出
`gateway-443-lane=ABSENT`，不新增 fail-closed 要求。车道存在性仍由
`gateway-443-fallback-lane=` 报告。

**该车道的已知残余。** xray 的 REALITY 目标是为自己公网域名新建连接，
nginx 看到的 `$remote_addr` 是 VPS 自身地址，所以这条路径上的 per-IP 限流实际
等价于一个全局桶（`10r/s`、并发 `20`），无法按真实客户端 IP 区分。恢复 per-IP
语义需要改成带 `xver=1` 的 PROXY protocol 回退并让 nginx 用
`proxy_protocol` 接（属 v2ray-agent/xray 配置域），本次未改。另外
`subscribe.conf` 由 v2ray-agent 的 `install.sh` 生成，重装会连带删掉 CPA
location：doctor 的 `random-route-count` 会 fail-closed，需按本文件重建车道。

### ChatGPT Plus OAuth 账号的特殊性

- 整个部署只有**一个 ChatGPT Plus 订阅账号**，所有经由 OAuth lane（`gpt-6-luna` /
  `gpt-5.6-luna` / `gpt-6.1-sol`）的请求共用同一账号的使用配额与
  风控窗口。
- 入口 `limit_conn` 是 per-IP，允许多个不同 IP 同时打 Luna，**不构成聚合上限**。

### 多账号 / 多 key 的条款与配额边界（2026-10-21 复核）

> 结论先行：**多账号和多 key 不是防风控手段。** 消费级 OAuth 通过第三方网关使用，
> 在 Antigravity 与 WorkBuddy 的官方条款中已被明确限制；OpenAI ToS 也禁止共享
> 账号凭据和绕过 rate limits。技术控制只能降低概率和影响，不能把条款风险变成零。

| 类型 | 官方口径 | 允许用途 | 禁止用途 | 现有控制 | 缺口 |
|---|---|---|---|---|---|
| ChatGPT Plus OAuth | [OpenAI ToS](https://openai.com/policies/terms-of-use/) 禁止共享账号凭据、绕过 rate limits/保护措施 | 个人、单用户、低频；官方桌面/CLI 形态 | 多人共享、转售、pool、多账号轮换、对外当 API 卖 | `chatgpt-oauth` lane、inflight 2、`request-retry=0`、session affinity | 无 per-account RPM/TPM 预算；OAuth 条款风险不可技术消除 |
| Antigravity / Gemini | [Antigravity FAQ](https://antigravity.google/docs/faq) 明确第三方软件访问违反 ToS、可暂停/终止；建议 AI Studio/Enterprise API key；消费级不支持自动化账号切换 | 个人、单账号、低频；迁移官方 API key | 第三方 agent 自动化、账号轮换、headless 批量、用 OAuth 替代 API | `antigravity-oauth` lane、inflight 2、完整 Retry-After、persistent hold | OAuth 本身是条款风险；`sensitive-words` 只是兼容补丁，不是防封控制 |
| WorkBuddy CN/INTL | [WorkBuddy AUP](https://www.workbuddy.ai/document/acceptable-use-policy) 禁止自动化 bot/scraper、逆向、绕过限制、多账号规避、未授权互操作插件 | 个人、单账号、低频；CN/INTL 按合法身份分离 | 多账号 pool、跨 realm 续打、后台 checkin/tasks/keepalive、用插件绕配额 | `selected`、inflight 1、后台任务全关、CN/INTL 目录分离 | 逆向协议无官方安全阈值；CN 用 BWG 美国 IP 属地理异常，仅作实验 lane |
| API key 渠道 | 官方允许编程访问，但受组织/项目配额、条款与 relay 条款约束 | 每消费者独立 key、最小权限、spend cap、HTTPS、pin model | 多 key 轮换绕配额、明文传敏感数据、用未审计 relay 承载生产数据 | provider 路由钉定、`openai-compatibility` 校验、exclusion 表 | CPA 核心无 per-key RPM/TPM；多 key 可能共享同一组织/项目额度 |

- **多账号放置规则**：一账号一 lane；每账号独立配额、独立告警；不自动 failover；
  不共享、不 pool、不轮换；CN/INTL 模型目录保持不相交；`selected`/sticky 优先于
  round-robin。只有合法分离（工作/个人、CN/INTL）才增加账号，不用来“扩容”。
- **多 key 放置规则**：每消费者独立 key；最小权限；设置 spend cap；只用 HTTPS；
  钉死模型；监控 401/403/429；不要跨渠道自动回退到质量不同的模型；不要用 relay
  承载敏感数据。多个 key 若共享同一组织/项目额度，轮换 key 不增加配额。
- **per-key 预算**：CPA 管理面没有 per-key/per-account 的 QPS/RPM/token 预算键；
  Nginx 的 per-IP `10r/s` 是入口保护，不是账号配额。对外消费者场景必须在
  admission 或网关插件层增加预算，再考虑扩容。
- **明文 HTTP**：slot 3 `http://35.213.82.91:8003` 是用户保留的已接受传输风险；
  不传敏感数据、不把 key 写入日志/证据；迁移 HTTPS 需独立评审。只读审计会以
  `plaintext-http-provider` 警告持续暴露它。

### shared-account admission 与熔断

主业务请求由 `/opt/cliproxyapi/cpa-admission.py` 在 CPA 前单独代理，只拦截
`/v1/chat/completions`、`/v1/responses` 中属于已审查共享官方账号的模型别名；
其它模型、第三方中转路由和 `/v1/models` 直接透传。认证 `auth_request` 仍直达
`127.0.0.1:8317`，不会把本地拒绝误报成上游调用。

固定配置来自 `scripts/remote/cpa-admission.json`：

裸名/别名目录的增补、改名、退役按
[cpa-catalog-change-checklist.md](cpa-catalog-change-checklist.md) 执行
（真源 manifest、同步点、commit→apply→doctor 顺序与已知坑）。

- 三条 lane 分别是 `chatgpt-oauth`（`gpt-6-luna` / `gpt-5.6-luna` /
  `gpt-6.1-sol`）、
  `zhipu-coding-plan`（`glm-5.3` / `glm-5.3-flash`）与
  `deepseek-official`（`deepseek-flash`；`deepseek-v4-pro` 已退役）。
- `chatgpt-oauth` 固定 `max_inflight=2`，`zhipu-coding-plan` 与
  `deepseek-official` 固定 `max_inflight=3`；三条 lane 都是
  `max_pending=4`、`queue_timeout_seconds=120`。OAuth lane 的值是 2026-09-28
  实测后从 1 提到 2 的：CPA 侧一轮 turn 实测 39 s / 112 s / 216 s，`max_inflight=1`
  下同一轮的第二个请求必然排满整轮，然后在 120 s 预算处收到 429，而客户端在 ~45 s
  就已放弃 —— 那些拒绝是结构性的，不是上游过载。2 个槽位让 desktop 常见的主响应
  + 标题/摘要对不再互锁，同时没有把账号开放成扇出。排队等待由 `waited_ms`
  在 journal 里如实记录；客户端中途离开时，`acquire()` 会在一个 1 s 分片内
  交回 pending 槽位（`reason=downstream_gone`），不会占满整个预算。一条
  lane 的容量窗口不会拒绝另外两条。
- **120 s 预算对半开探针同样适用，且经 2026-10-01 复核后刻意保持不变。**
  探针是一条真实客户端请求，时长约等于一次正常生成（当日 `route=responses`
  `upstream_time` p50 21 s / p90 77 s / p99 188 s）；这些分位点不能推出
  120 s 的精确覆盖百分位，且该时长包含 admission 等待。上调
  会把探针失败时的最坏挂起一起拉长，下调则丢掉实测收益（用当日两次真实探针
  重算：5 个被拒请求中 4 个会在预算内被服务）。改这个值要同时改
  `ADMISSION_QUEUE_TIMEOUT_SECONDS` 与配置校验，属策略改动；
  **复访条件**：一旦 journal 出现 `reason=queue_timeout`，把它的时间戳与同一
  lane 最近的 `lane_probe`/`upstream_result` 配对，若多数超时都发生在探针成功
  前数秒，才说明预算偏小。
- 容量类 `429/503` 或 `Selected model is at capacity`、`model_at_capacity`、
  `server_is_overloaded`、`usage_limit_reached`、`too many requests` 等已审查
  文本信号进入对应 lane 的熔断。
- **容量信号分两个作用域。** `model_capacity_markers`（本栈 = `selected model
  is at capacity`、`model_at_capacity`）里的文本说的是**单个模型**，只停该模型：
  `chatgpt-oauth` 一条凭据承载 `gpt-6-luna` / `gpt-5.6-luna` / `gpt-6.1-sol`，
  2026-10-10 实测 `gpt-6-luna` 的容量事件把整条 lane 冷却后，紧接着的
  `gpt-6.1-sol` 请求被本地拒绝并回 `retry_after=471` —— 桌面侧就是"重试次数
  耗尽"的 429，而那个模型从未容量不足。其余文本（`server_is_overloaded`、
  `usage_limit_reached`、`rate limit`、`too many requests`）与"无标记的
  `429/503`"描述的是**凭据/账号**，仍然停整条 lane。模型级窗口同样走
  `60/120/240/480/900` 阶梯、同样尊重上游 `Retry-After`（期间不提前探针）、
  同样只放一个探针并按代际校验成功；不同点是它只影响被点名的模型，并发预算
  仍由整条 lane 共享。运行态可从 `/healthz` 的 `cooldown_scope`
  （`lane` / `model` / `none`）与 `model_cooldowns` 观察；被点名模型的本地
  拒绝在 journal 里是 `lane_reject reason=model_cooldown`，探针是
  `lane_probe ... scope=model`。作用域由配置声明并由 doctor
  `admission-health`、审计 `lane-model-capacity-scope-missing` /
  `lane-model-capacity-marker-scope` 双向 fail-closed。
- **只有上游真实答复的容量信号才计数**：连接被拒/reset、读超时等传输层失败
  会返回本地 `503` 并附 `Retry-After`，但**不推进失败阶梯**（journal 里
  `upstream_error ... transport_failure=true`），避免网络抖动单独打开熔断。
- 无有效 `Retry-After` 时按 `60/120/240/480/900` 秒退避；有有效值时优先使用，
  安全上限为 86400 秒。**失败阶梯要求连续**：默认阈值 2 次；未跨越更新容量
  失败的普通成功可清零，过期请求的成功不能覆盖更新的失败或服务端退避。
- 仅对本地选择的冷却，按 `early_probe_interval_seconds`（默认 10s）放行
  **一个按需提前探针**；上游明确给出的 `Retry-After` 不允许提前绕过。探针同样
  受 `max_inflight` 约束，只有完整成功且期间没有更新容量失败才能清零解锁；
  传输错误、流内协议错误或客户端离开不能证明恢复。
- 等待者按 FIFO 获取空位，新到请求不能插队；超过自身排队截止时间或客户端已
  离开的请求不再占用生成槽。`busy` 表示队列已满，不等于上游额度耗尽。
- 熔断或队列拒绝返回本地 `429` 和 `Retry-After`，不修改请求体中的 model，不
  自动切到其它 lane，也不重放已开始输出的流式请求。半开探针在途时，并发请求
  优先进入现有有界 FIFO pending 队列，待探针完成后再按顺序入场；只有 pending
  已满或请求耗尽 120 秒排队预算时才返回 `429`，避免把一个长探针放大成快速
  重试风暴。**冷却期（`open_until` 未到）内的新到达走同一条队列**：剩余冷却
  不超过 120 秒预算时被扣住，待服务端退避到期或本地提前探针条件满足后，
  由队首作为探针发出；超过预算才立即拒绝。
  journal 因此以 `lane_probe`/`upstream_result` 的 `waited_ms` 记录这段等待，
  `lane_reject reason=cooldown` 只出现在冷却长于预算的情况。
- `queue_timeout`（耗尽排队预算）与 `busy`（pending 已满）的 `Retry-After`
  取**该 lane 下一次会重新评估的时刻**：冷却到期与下一个探针槽位两者较晚者，
  并以探针节奏（10s）为下限。这两个拒绝都不能报 `1` —— 已经等满预算的客户端
  若被要求 1 秒后重试，只会再次入队、再等一个完整预算。`cooldown`（未入队即
  被拒）仍按冷却剩余秒数返回。
- 每个请求生成独立的 `X-CPA-Request-Id`，并关联 `lane_reject`、`lane_probe`、
  `upstream_result` 等 journal 事件；本地拒绝额外返回
  `X-CPA-Admission-Reason`，上游 `429` 不带该本地标记。日志只记录入口类别及
  客户端请求/实例标识的短哈希，不记录原始标识或请求 URI。重复哈希仅表示客户端
  复用了标识，不能单独证明重试或实际运行实例身份；标识缺失时为 `-`。
- 客户端在 `429` 落地前已断开（desktop 重试风暴的常见形态）是良性竞态：
  admission 静默丢弃该响应并记 `downstream_gone`（响应侧）或
  `downstream_gone stage=request_line`（keep-alive 读下一请求行时），不产生
  journal traceback。

这会让突发重试更快得到可退避的本地响应，减少新的共享官方账号生成请求；代价是
命中容量窗口的 lane 会暂时变慢或明确不可用。

**冷却期内的新到达默认被"扣住"而不是立刻拒绝**：只要剩余冷却不超过该 lane 的
排队预算（120 s），请求就进入有界 FIFO 队列，由队首在允许的探针时刻发出。
上游明确的 `Retry-After` 必须等满；仅由本地选择的冷却仍允许按既有节奏提前探针，
因此不能把所有冷却都描述为与客户端等满 backoff 完全等价。服务端保持请求
不会消耗调用方的重试预算 —— 2026-10-01 的已配对失败回合没有成功等待恢复，而是让
**整个回合**以 `exceeded retry limit, last status: 429` 失败，因此"调用方会尊重
`Retry-After`"这个前提不成立。剩余冷却超过预算（例如配额冷却 525 s）时仍立即
拒绝：等下去也不可能等到，早失败对调用方更有用。GLM 与 DeepSeek 各有一条独立
lane，因此不会因 OAuth lane 熔断而被误伤。

### Luna 两种可切换模式的独立归因

Cockpit 可以在 direct OAuth 与 direct BWG API 之间切换。本节不假设两种模式
同时产生请求；必须使用各自模式下的新鲜日志分别归因。

- **direct OAuth**：`Selected model is at capacity` 是官方 direct OAuth 链路的
  capacity/overload 响应。BWG admission 不经过这条链路，因此不能靠调整 BWG
  队列修复它。
- **direct BWG API**：请求经过 Nginx 和本机 admission。当前本机记录能看到
  `503/429`；必须用远端 `upstream_status`、`Retry-After` 和 admission 状态区分
  上游响应与本地冷却返回的 `429`。`exceeded retry limit` 表示调用方报告自身
  重试上限已耗尽；这个错误本身不能证明 direct OAuth 与 BWG 同时并发。

**Windows provider gateway 还存在独立的账号闸门。** 当前 10909 将 BWG
聚合账号上的 OAuth 与 passthrough 模型共用 `maxAccountConcurrency=2`、
`accountConcurrencyWaitMs=120000`。2026-10-01 22:47:27 CST 已确认 Luna
等待 120.026 s 后在本机返回 429 并结束桌面回合；同期远端没有匹配的
admission 拒绝。因此远端 lane 修复不能证明本机超时也已解决。
复访此故障时先配对本机 `requestId/latencyMs` 与远端记录，确认阻塞层；
不要用提高全局并发或延长全部等待预算代替聚合 provider 的独立策略。
现有正式设置由全局 collection 复制进 provider manifest，Go 只在启动时加载；
配置重载走 sidecar stop/start，可能中断在途请求。仅编辑生成 manifest 不算已加载，
需要持久化策略、允许重载的窗口和加载后验收共同闭环。

两种模式先后切换时，必须分别记录模式、模型、上游状态、本地冷却状态和客户端
重试节奏。`request-retry=0` 只约束 CPA 自身；客户端 SDK 与 Cockpit sidecar
仍必须尊重 `Retry-After`。不要把 sidecar 并发上限调大或增加重试来处理 BWG
429。`-QuarantineOAuthLuna` 仅用于需要停止 BWG OAuth 流量的应急窗口，不是
两种 Cockpit 模式切换的日常前置条件。

应用入口：

```bash
systemctl is-enabled --quiet cpa-admission.service
systemctl is-active --quiet cpa-admission.service
curl --noproxy '*' -fsS http://127.0.0.1:8318/healthz
```

`healthz` 返回 lane、模型别名、配置并发上限和计数状态，不包含 token、请求体或
客户端地址；strict doctor 会用它确认新 admission 进程已加载有界并发配置。
该服务必须只监听 `127.0.0.1:8318`；Nginx 的随机公网路径仍是唯一外部数据面。

首次应用新版本时，`-Apply` 会先备份并停止/禁用旧的
`cpa-luna-admission.service`，再移除旧脚本、配置和 unit，最后启动
`cpa-admission.service`。任一阶段失败都会从同一个 backup 恢复旧文件及服务
启停状态；doctor 对任何旧服务、旧文件或旧 unit 残留均 fail-closed。

### doctor 的账号压力可见性

每次 strict doctor 的 `==gateway-statuses-current-log-24h==` 段输出包含：

- `route_classes`：按路由类别（`models`/`chat`/`responses`/`other`）分类的 24h 计数
- `last_1h_statuses`：最近 1h 各状态码计数（快速发现激增）
- `retry_after_classes`：上游 `Retry-After` 的类别分布（`absent`/`seconds`/`other`）
- `five_xx_local_vs_upstream`：500/502/503 的本地冷却快失败（<0.5s）vs 上游传递（≥3s）归因
- `admission_429_shape`：对 `203 bytes`、快速返回且 Nginx 限流均为 `PASSED` 的
  `upstream_status=429` 做“可能来自 admission”形态分类；这是日志启发式，具体
  cooldown/half-open 原因仍以 `cpa-admission` journal 为准
- `statuses_by_client_class`：每个状态码按客户端平面拆分（`loopback/…` =
  探针与本机压测残留，`external/…` = 真实客户端）。**读 429 总数前先看这一项**：
  两者混在一起会把压测残留读成生产故障
- `statuses_by_hour`：按小时（UTC）分桶的 `total` / 各状态码 / `limit_rejected`
  （Nginx 连接或速率层拒绝）。用于把一次突发定位到具体小时，判断它是否仍在持续

`statuses_by_client_class` 与 `statuses_by_hour` 都只输出类别与计数，不输出任何地址。

这些是**定位信号**，不是 provider 封号或配额恢复的证明。shared-account
admission 的本地 `429` 也不能证明账号已经恢复，它只证明本机没有把新的 lane
请求送进 CPA。
账号级压力的最终判据只能来自 provider 侧的 403/quota 响应或 OAuth 刷新失败。

### 各通道账号暴露特征对比

| 通道 | 账号类型 | 聚合保护 | 风险特征 |
|---|---|---|---|
| ChatGPT Plus OAuth（Luna） | 一个 Plus 订阅 | 本机 lane `max_inflight=2` + 4 个有界排队槽 + capacity 熔断 | 风控窗口敏感；turn-state 积累；OAuth 刷新每 24h 一次 |
| ai.input.im（Sol/Astra） | 第三方中转账号 | 无（中转方自行管理） | 中转账号本身可能有配额或风控；403/408/5xx 按 `UPSTREAM_UNAVAILABLE` 处理 |
| opencode.ai zen（deepseek-v4.1-flash / muse / claude-haiku-5-5） | 第三方中转账号 | 无 | 同上；2026-10-11 替换退役的 CIII 渠道。deepseek 走 openai-compat（chat 协议）；muse 走受管 codex 通道族（原生 /responses）；claude-haiku-5-5 走受管 claude 通道族（原生 Anthropic Messages，2026-10-16 增补）。该通道不透传缓存计数字段，缓存优化仅对 BigModel 通道有意义。muse 按上游口径以请求数据训练，敏感流量避开 |
| Slot 3 明文 HTTP（terra） | 第三方中转账号 | 无；明文传输 API key | API key 在传输链路明文可见；用于非敏感备用 |
| BigModel Coding Plan（GLM） | 官方 Coding Plan | 本机 lane 有界并发 + 4 个有界排队槽 + capacity 熔断 | 计划余额耗尽仍是上游真实信号，本地闸门不能提高额度 |
| DeepSeek 官方 API（flash） | 官方 API key | 本机 lane 有界并发 + 4 个有界排队槽 + capacity 熔断 | **2026-10-11 退役**（用户经管理 API 移除；admission lane 同步删除） |

## 路由清单与目录契约

路由映射由 `scripts/remote/cpa_provider_routes.json` 管理，包含
`gpt-6-luna` / `gpt-5.6-luna` / `gpt-6.1-sol` 的 ChatGPT Plus OAuth lane
（`gpt-6-sol` 已于 2026-09-30 退役，排除清单留墓碑；`gpt-5.6-luna` 曾于
2026-10-01 退役、2026-10-02 按用户要求恢复），以及 2026-10-14 新增的
Antigravity OAuth lane（Google AI Pro 订阅，凭据
`antigravity-sciman.phys@gmail.com`）。同日按用户决定收窄为只开放
`gemini-3.8-flash-high`（antigravity 目录无裸名 `gemini-3.8-flash`，3.8
flash 档仅此一名）：该凭据暴露的其余 7 个模型（`gemini-3-flash` /
`gemini-3.1-flash-image` / `gemini-3.1-flash-lite` /
`gemini-3.5-flash-lite` / `claude-opus-4-6-thinking` /
`claude-sonnet-4-6` / `gpt-oss-120b-medium`）与 2026-10-16 加入的防抢注
墓碑 `claude-haiku-5-5`（槽位 2 同日认领同名裸名；该 ID 是否在
antigravity 目录未实测）通过 config
`oauth.excluded-models.antigravity` 做 provider 级排除（-Apply 从
manifest `antigravity_oauth_exclusions` 派生，doctor
`==antigravity-lane==` 门禁核对漂移），并在 `codex_api_key_exclusions`
留墓碑防 API-key lane 抢注；上游目录若新增未声明模型，doctor
`MODEL_IDS_UNKNOWN` fail-closed 拦截。共享订阅 OAuth 通道按仓库不变量挂
`antigravity-oauth` admission lane（inflight=2/pending=4，与 ChatGPT
OAuth lane 同族参数）；`-QuarantineOAuthLuna` / `-DeactivateOAuthLuna`
只作用于 ChatGPT Plus 路由，不影响该通道）；当前
引用槽位为 `1/2/3/4`（2026-10-11 起槽位 5 退役）：

- 槽位 1（ai.input.im）：`gpt-6-astra` /
  `gpt-6.1-sol-input`（上游 ID `gpt-6.1-sol`，改名路由避免与 OAuth lane 抢名）裸名。
  `gpt-image-2.5` 图像路由已于 2026-10-04 退役（从未投产使用，排除清单留
  墓碑；"图像不参与 chat 生成冒烟"的 skip 逻辑保留待未来图像路由复用）。
  `gpt-6.1-sol-input` 自 2026-10-03 起为 optional：上游的 sol 生成路径存在
  慢性 502 `upstream access forbidden` 窗口（10/2 三次、最长 2.5h+），窗口
  内 CPA 将其从目录视图摘除且不自行恢复，健康门不再把它当必需（模型仍
  声明，上游恢复后重启重注册即回归服务）。裸名
  `gpt-5.6-sol` 已于 2026-09-30 从该槽位退役；`gpt-6-sol-input`（上游
  `gpt-6-sol`）同日由 `gpt-6.1-sol-input` 接替；`deepseek-v4.1-flash` 曾于
  2026-10-01 短暂退役、同日按用户要求增补回，最终于 2026-10-11 迁往槽位 2
  （该名不入排除清单，readiness fail-closed 拦复活）。
- 槽位 2（opencode.ai zen）：2026-10-11 替换退役的 CIII 渠道，路径固定
  `https://opencode.ai/zen/go/v1`，只暴露 `deepseek-v4.1-flash` 一个
  openai-compat 裸名（别名即原名）。**该端点生成请求必须携带
  `x-opencode-session` 静态头**
 （缺失报 400 MissingSessionID），精确值
  `{x-opencode-session: cpa-bwg-gateway-01}` 钉在清单 slot 2 中、由 -Apply
  逐字投影，是 2026-09-18 传输键禁令的唯一受控豁免。`deepseek-v4.1-flash`
  已于 2026-10-11 经 8318 全链单发实测 LIVE_ACCEPTED，同日客户端 key 公网
  验收通过；GLM 缓存基线 0.9956 复测逐位一致；slot2 流式 TTFB 实测
  1.33s，且该通道不透传缓存计数（hit=0/create=null），缓存优化仅对
  BigModel 通道有意义。
  `muse-spark-1.3-contributor` 曾因 Responses-only 协议与 CPA openai-compat
  执行器（只发 `chat/completions`，源码锚点
  `openai_compat_executor.go` `endpoint := "/chat/completions"`）不匹配于
  同日短暂退役；**同日已经受管 codex 通道族回归**（manifest
  `codex_api_key_routes`：原生 `/responses` 执行器，锚点
  `codex_executor_execute.go` `{base_url}/responses`；凭据复用 slot 2、
  同枚会话头经 `addConfigHeadersToAttrs`/`ApplyCustomHeadersFromAttrs`
  上线），8318 全链单发 LIVE_ACCEPTED（200/completed/0.96s）。该模型按
  上游口径以请求数据训练（敏感流量避开）；`zen/v1` 非 go 路径是另一计费
  通道，勿迁。
  **2026-10-16：`claude-haiku-5-5` 增补（初判三改后落地为受管 claude
  通道族）。** go 端点目录（`GET /zen/go/v1/models` 200；python UA 被
  CF 1010 拦勿绕，curl 可达）37 模型中唯一 Claude 即 haiku-5-5。协议
  路由：chat 面与 codex 族 `/responses` 面均 400
  `ModelProtocolUnsupported`（单发实测即停）；Claude 走原生 Anthropic
  Messages：`POST /zen/go/v1/messages` + **`x-api-key` 头** +
  `anthropic-version` + 会话头，实测 200（end_turn，usage 正常入账）。
  当日初判"凭据类别门控"（`Authorization: Bearer` 形 401 `Go requires a
  credential`）**有误**：实为鉴权头形差异，Bearer 在该面不被读取，
  两种头同时存在仍 200。故落地 = 新建第三条 api-key 通道族
  `claude_api_key_routes`（muse 规模）：CPA `ClaudeExecutor` 原生发
  `{base}/v1/messages?beta=true`，base-url 因此取
  `https://opencode.ai/zen/go`（**停在 `/v1` 之前**），凭据复用 slot 2。
  **槽位凭据头**：CPA 只在 base 为 `https://api.anthropic.com` 时发
  `x-api-key`，自定义网关一律发 `Authorization: Bearer`（源码锚点
  `claude_executor.go` `PrepareRequest` 与 `claude_executor_request.go`
  的 `isAnthropicBase && useAPIKey` 分支），而组自定义头在鉴权头之后应用
  （`ApplyCustomHeadersFromAttrs`，同 muse 族会话头），所以清单在该路由
  headers 里用占位符 `<slot-api-key>` 声明 `x-api-key`，由 -Apply 从
  slot 2 的 `API_KEY_2` 逐字展开投影（**仓库与证据都不存密钥**）；
  `cpa_policy.py` 反向按组内自己的 api-key 校验该头，组头与组密钥不一致
  即 fail-closed（占位符只允许出现在 claude 族）。`count_tokens` 无缺口：
  CPA 只对 `api.anthropic.com` 上调上游 `/v1/messages/count_tokens`，自定义
  base 一律本地估算（`shouldUseClaudeUpstreamTokenCount` 门），zen 对该
  路径的 404 不可达。**调用方指纹不外传（同日实证补钉）**：api-key 的
  Claude 路径把调用方 `User-Agent` 逐字转发上游，python 直探
  （`Python-urllib/…`）被 Cloudflare 1010 回 403，CPA 随即把该失败记成
  该凭据的冷却，此后不分调用方一律 503 `auth_unavailable`（0.01s 不出网）
  ——一次 bot 指纹调用即拖垮共享凭据。codex 执行器对同类上游本就"不转发
  下游 UA"（源码注释直指 CF 1010：`codex_executor_request.go`
  `applyCodexDirectImageHeaders` 上方 "Downstream client User-Agent values
  are not forwarded to reduce Cloudflare 1010 blocks"），claude api-key
  路径尚无该处理，故清单在该路由 headers 钉 `user-agent:
  cpa-bwg-gateway/1.0`（组头在鉴权头之后应用，生效位置同凭据头），由
  -Apply 逐字投影：路由可用性不再取决于调用方用哪个 HTTP 库，本仓 python
  探针无需伪造 UA，上游看到的是网关自身身份而非某个客户端指纹。
  **Claude Code 三个未声明字段（同日实测补钉）**：Claude Code 2.1.300 在
  messages 体里带三个 zen 未声明的字段——顶层 `safeguards`（权限模式分类器
  上下文）与 `context_management`，以及挂在 system 消息上的 `output_config`
  （effort 控制）。zen 的 Anthropic 面严格校验字段，逐个回 400
  `[invalid_request_error] <path>: Extra inputs are not permitted`（三者
  都实测：不去则该字段报错，去掉即前进，全部去掉后 88KB 真实客户端请求
  200 且 `text/event-stream` 正常开流）。故 `requests.payload.filter` 增一条
  按模型名钉死、顺序为 `safeguards` / `context_management` /
  `messages.#(output_config)#.output_config` 的删除规则：`cpa_policy.py` 的
  `EXPECTED_PAYLOAD_TOOL_RULES` 按清单 claude 路由模型名派生，apply/doctor
  反向核对（该段 apply 只校验不生成，改动须同时扩远端 `config.yaml` 的钉死
  段）；`cpa-health.py` 的 claude 生成探针每次携带该段做行为级回归——规则
  一旦丢失即健康门失败，而不是让真实客户端会话退回 400。
  **2026-10-16 晚：claude 路由 binding/prefill 两规则（`requests.payload`）。**
  Codex Desktop 经该路由的重连/继续回合必然重放历史，触发两族上游
  `invalid_request_error`：Anthropic 对重放的 thinking 块做创建时绑定校验
  （签名绑定创建时的会话前缀与 `tools` 清单，报 `Invalid 'signature' in
  'thinking' block ... bound to a different conversation`），且 haiku 拒绝
  尾部 assistant 消息（报 `This model does not support assistant message
  prefill`）。处置 = `requests.payload` 两处新增、按模型名钉死：`override`
  增 `thinking.block_binding.prefix_mismatch_behavior=drop_block` 与 body 键
  `betas=["thinking-binding-controls-2026-08-01"]`（claude 执行器
  `extractAndRemoveBetas` 在规则之后把它转成 `anthropic-beta` 头），令失配
  块由上游丢弃而不是整请求失败；`filter` 增条件条目（match
  `messages.@reverse.0.role=="assistant"` → 删 `messages.-1`）。两个实测坑已
  由 policy 注释与测试钉住：（1）该注入必须带 `exist` 门 `thinking.type`，
  否则 sjson 会给无 thinking 配置的请求合成裸 `thinking.block_binding`，上游
  对每个这类请求回 400 `thinking.type: Field required`（实测影响窗口约 6
  分钟，容器日志实证零真实流量命中）；（2）gjson 读无负索引，match 只能走
  `@reverse`，删除则用 sjson 文档支持的 `messages.-1`，两个引擎有意分工。
  `cpa-health.py` 的 `_claude_payload_rule_eval` 每次 claude 生成检查附带
  binding/prefill 两个微型探针，规则失效即 gate 20。**残留**：断流重连回合
  若被过滤删成空历史，该单回合失败（上游本就拒绝空 `messages`），不伤会话；
  `drop_block` 丢弃失配块 = 该回合损失部分旧推理上下文。**退役条件**：上游
  将 haiku 纳入 `claudeModelRejectsAssistantPrefill` 家族并原生注入
  binding controls 后，按 muse 先例删除规则与钉死段；已提交
  router-for-me/CLIProxyAPI#6692（正文副本
  `outputs/upstream-issue-claude-replay-400.md`）。
  **同日双面验收 LIVE_ACCEPTED**：-Apply 成功（`READY_STATUS=200`、
  `GUARDRAILS_APPLIED`、models=12）且 doctor 给出 `DOCTOR_CONTRACT_OK`
  （drift 全 MATCH、`MODEL_IDS_UNKNOWN=none`）后，经 8318 全链单发
  `claude-haiku-5-5`，调用方刻意使用 python 默认 UA（即此前被 CF 1010 拒
  的那枚指纹）：HTTP 200 / `stop_reason=end_turn` / 模型回显
  `claude-haiku-5-5` / text=OK / usage 18→4，0.96s；同一单发经公网 8443
  随机路径亦 200（0.76s），外部数据面同样可用。排除清单合并块对无 `excluded-models` 键的投影组需显式回写
  （游离 `.get` 默认列表曾静默吞掉全部 tombstone——已修并由
  `test_cpa_apply_config_transform_passes_policy_on_v8_fixture` 事务级
  回归测试钉住）。
  **2026-10-11 晚：desktop 工具形状三规则（`requests.payload`）。**
  ChatGPT desktop 的 Codex 引擎随每个请求发送的完整工具声明经 codex 执行器
  原样直通上游，zen 对其中三个形状报 400：`tool_search` 的 `required`
  未含可选键 `limit`（严格 schema 校验，报
  `'required' is required... Missing 'limit'`）、`web_search` 的
  `search_content_types`（仅 preview 支持）、`custom` 免格式工具
  （apply_patch grammar，报 `custom tools are not supported`）。处置 =
  CPA v8 配置级 payload 规则（生效锚点：codex 执行器
  `codex_executor_request.go` `cacheHelper` 内 `FinalizePayload`，规则路径
  支持 `tools.#(type=="tool_search")` 条件查询）：override 补
  `required=["query","limit"]`、filter 删 `search_content_types` 与整个
  `custom` 工具，全部按模型名钉死在受管 codex 通道族模型上，其他 lane 零
  影响。`cpa_policy.py` 的 `EXPECTED_PAYLOAD_TOOL_RULES` 按清单派生钉死该
  段（缺段/篡改即 FAIL），`cpa-health.py` 的 codex 生成探针每次携带三个
  工具形状做行为级回归。**代价**：desktop 会话中 muse 的文件编辑退化为
  shell 方式（`custom` apply_patch 被过滤；改写成 function 形状需
  function_call↔custom_tool_call 响应侧翻译，Responses 直通路径无此翻译，
  未验证不赌）。**退役条件**：上游（CPA codex 执行器内置清洗或 opencode
  zen 放宽校验）修复后可删规则与钉死，无害残留。管理 API 的
  `secret-key` 是 bcrypt 哈希、明文密钥不在服务器上，配置热改路径不可用；
  文件级编辑需 `docker restart`（在静默窗口执行，先快照）。
  旧 `gpt-6-astra-ciii` 与 `gpt-6.1-sol-ciii` 仅保留在
  排除清单中作为退役墓碑，`codex.ciii.club` 已入 `retired_hosts`。
- 槽位 3：固定到 `http://35.213.82.91:8003/v1`，CPA 会将该槽 API key 以明文
  发送给中转。**该明文 HTTP 是用户明确保留的精确例外**，后续审查不得自动
  禁用、改成 HTTPS 或移除该路由；仍须持续报告其传输风险。2026-10-11 起只
  保留上游 `gpt-5.6-terra` 裸名（`gpt-6.1-sol-91` 退役，别名留在排除清单；
  旧别名 `gpt-6-sol-91` 同样留清单防抢注）。
- 槽位 4（BigModel）：只暴露 `glm-5.3`、`glm-5.3-flash` 两个裸名，其余
  BigModel 模型（含 `glm-5.3-flashx`）均不投影。
- 槽位 5（DeepSeek 官方）：**2026-10-11 退役**。`deepseek-flash` 与
  `api.deepseek.com` 一并移除（`deepseek-v4-pro` 已于 2026-09-30 退役），
  `retired_hosts` 留 `api.deepseek.com`，共享 lane `deepseek-official` 从
  admission 配置同步删除；本地缓存金丝雀的 DS 基线随之退役，默认金丝雀
  模型改为 `glm-5.3-flash`。

未列入清单的上游目录模型不会自动暴露。远端 `cpa_policy.py`、
`cpa-health.py` 和 apply 共用该清单校验 provider、alias 唯一性、OAuth/API-key
排除与可见模型集合。上游 `/models` 目录响应只用于清单候选核实，不代表生成
语义已验收；只有显式矩阵模式会向已列出的模型发送生成请求。

`scripts/remote/cpa_provider_routes.json` 将 `gpt-6-luna` / `gpt-6.1-sol` 显式
映射到 ChatGPT Plus OAuth lane；它们不属于 `openai-compatibility` provider。
槽位 1 的 `gpt-6.1-sol-input` 映射到上游 `gpt-6.1-sol`；槽位 2 的
`deepseek-v4.1-flash`、受管 codex 族的 `muse-spark-1.3-contributor`、
槽位 3 的 `gpt-5.6-terra` 均为上游原名直通。
`codex.ciii.club` 与 `api.deepseek.com` 自 2026-10-11 起退役，但
`codex-auto-review`、
`gpt-5.5`、`gpt-5.6`、`gpt-reserve` 四个旧别名继续从 OAuth/Codex API-key
路由排除。OAuth 侧保留 `codex-*` 和 `gpt-5.7*` 排除，让 `gpt-6-luna` 留在
OAuth。CLIProxyAPI 这里提供的是 OAuth 排除规则，不是请求级正向 allowlist；
移除旧 `gpt-6*` 排除以放行 Luna 后，未来 Codex GPT-6 新模型可能进入上游
目录。健康门会把未登记 ID 判为本地契约失败，但不能拦截直接推理请求；每次
上游目录变化后都需先审阅并更新精确路由策略。

目录健康门要求路由清单登记的模型 ID（清单派生，无硬编码名单）；尚未开放的
GPT-6 Luna 或 GPT-6.1 Sol 可缺席并标为未验证，未知模型和 prefix 仍直接失败
（目录阶段的本地契约失败为 exit 20；目录已通过后，单路由 403 归类为
`UPSTREAM_UNAVAILABLE`，不把上游账号/路由决定误报为本地配置错误）。上游即使
返回 HTTP 200，只要响应体不是合法 JSON，也按 `UPSTREAM_UNAVAILABLE` /
`RELAY_DEGRADED` 处理；不把异常 200 当作成功，不重试，也不做内容包装后继续
转发。

strict doctor 自身现在也 fail-closed：`==client-model-catalog==` 从远端
`cpa_provider_routes.json` 派生 allowed 集合，输出 `MODEL_IDS=` 与
`MODEL_IDS_UNKNOWN=`，任何未登记 ID（含复活的退役别名）即 `mark_fail`。冷却
只会让 ID 变少、不会变多，因此该检查不会在合法瞬态冷却期误报；必要模型缺席
仍由 `cpa-health.py` 的 required 口径判定，doctor 只拦"多出来的"。

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

`generation-all` / `quality-canary` / `quality-eval` 的 OAuth lane 准入默认是
**排除**：两个 Luna 别名只有在显式设置 `CPA_HEALTH_INCLUDE_OAUTH=1` 时才回到
矩阵，默认运行时逐行输出
`ROUTE_PREPARED ... kind=oauth_lane_suppressed reason=default_opt_out`。
`CPA_HEALTH_NO_OAUTH=1` 保留为优先的硬抑制，即使同时写了 `INCLUDE` 也不会
回选（输出 `reason=CPA_HEALTH_NO_OAUTH`）。每次运行还会输出一行
`PROBE_BUDGET mode=... models=... cases_per_model=... planned_generation_requests=...
oauth_lane=...`，用于在执行前确认本次运行的计划生成请求量。定时路径本来就
避开 OAuth；该开关只影响显式矩阵模式，不改变 `readiness` / `generation` 的
既有语义。

探针开关只约束探针自身。要挡住**外部消费者**对唯一 OAuth 账号的访问，必须用
`-QuarantineOAuthLuna`（见下文"路径轮换与应急登出"后的隔离小节）。

## 缓存约束

只做不会改变 provider 语义的请求侧约束：稳定的系统提示和工具说明放在
prompt 前缀，时间戳、随机 ID、用户私有内容等动态部分放在后部；不跨用户
复用 session/cache key，不仅为追求命中率盲目打开 `support-prompt-cache-key`。
DeepSeek 的命中率以官方返回的 `prompt_cache_hit_tokens` /
`prompt_cache_miss_tokens` 观测，不把一次受控样本外推为长期收益；OAuth/Codex
路由和 ai.input.im 不套用 DeepSeek 或 OpenAI API 的缓存结论。

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
`cooldown_next_retry_after`、`catalog_gpt6_luna`、`catalog_oauth_aliases`、
`catalog_oauth_missing` 与 `luna_state`。**在册性按整条 OAuth 路由判断**，期望
别名集合由 `cpa_provider_routes.json` 的 `oauth_routes` 派生（当前为
`gpt-6-luna` 与 `gpt-5.6-luna`），而不是只看规范名 `gpt-6-luna`：上游/账号侧
的模型授权会变动，裸名可能消失而兼容别名仍在服务。因此：

- `available`：期望别名全部在册；
- `available_partial`：只有部分在册（lane 仍可达，不是故障），缺失项见
  `catalog_oauth_missing`；
- `unavailable_unclassified`：整条 OAuth 路由都不在册且无冷却——这才需要按
  上游或本地目录故障排查；
- `active_cooldown` / `stale_cooldown_suspected`：见
  [cpa-stale-cooldown-recovery.md](cpa-stale-cooldown-recovery.md)；
- `unknown_route_manifest` / `unknown_catalog_unreadable`：读不到清单或目录，
  不做可用性结论。

`catalog_gpt6_luna` 保留为单名兼容读数（只反映裸名），诊断时优先看
`luna_state` 与 `catalog_oauth_missing`。`active_cooldown` 是正常退避，不能
清除。持久化关闭后该段失去 `.cds` 数据源（`cooldown_state` 恒为 `none`），
滞留判据转为重启复验，恢复口径见
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
版本管理的 updater/health/policy/admission/fail2ban 源文件及 provider route
manifest，
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

## OAuth lane 可逆隔离（quarantine / restore）

Claude subscription OAuth 使用 `-QuarantineClaudeOAuth` /
`-RestoreClaudeOAuth`，事务先备份并投影 marker-aware `cpa_policy.py`，再改
`oauth.excluded-models.claude` 与运行 marker
`/opt/cliproxyapi/claude-oauth-quarantine.json`。配置以保留文件 inode 的受控写入触发
CPA watcher，marker 原子写入；不重启 API 服务：

```powershell
# 隔离：保留凭据，不清额度或冷却；事务依赖 CPA 配置热加载，不重启服务
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -QuarantineClaudeOAuth
# 恢复：必须人工显式调用
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -RestoreClaudeOAuth
```

Marker 固定记录 Claude OAuth route 的全部别名，以及隔离前/后的排除集合；恢复会
比较当前配置与隔离后集合，存在漂移即拒绝，不覆盖第三方改动。隔离前事务校验
Claude OAuth 凭据恰好一份、语义基线通过、`claude-oauth` lane 三次 idle、当前目录
含齐清单别名；三次 idle 快照在备份、策略投影和目录预检后紧邻配置写入，但不锁住入口；执行期间
应暂停本机自动或并行派发。隔离后要求这些别名全从 `/v1/models` 消失。失败会恢复配置和 marker，
然后验证原目录；热加载失败不触发容器重启，需在维护窗口另行处置。strict doctor
报告 `claude_lane=QUARANTINED` 与 `CLAUDE_OAUTH_CATALOG=blocked`。

普通 `-Apply` 会保留 Claude quarantine marker 与通配排除，避免无意重开路由，但
`-Apply` 本身仍会重启 CPA；隔离期间不需要为此运行 Apply。显式 restore 后才可按
常规维护窗口执行 Apply。Claude 隔离仅阻止后续模型路由，不撤销 OAuth 授权/刷新，
也不代表账号已解封、免限流或通过质量验收。

每次事务的 `/root/cpa-oauth-quarantine-backup-*` 在写入前保存并逐字节校验原
`config.yaml`、marker（若有）和 `cpa_policy.py`；历史按 keep-8 保留。事务失败时
自动回滚并读回原文件与目录，备份本身保留以便调查。

Antigravity 已登录通道使用独立的 `-QuarantineAntigravity` / `-RestoreAntigravity`。
它复用同一事务，选择 `antigravity` provider，只改
`oauth.excluded-models.antigravity` 与 `/opt/cliproxyapi/antigravity-quarantine.json`。
标记包含 `provider=antigravity`、该 provider 的全部别名、
`previous_provider_exclusions` / `applied_provider_exclusions` 和时间、原因。
恢复前核对排除表未漂移；Luna 的配置与标记保持独立。`-Apply` 在 Codex /
Antigravity 隔离标记存在时拒绝执行，避免维护时重新开放流量；Claude marker 的
Apply 保留行为见上节。严格 doctor 显示
`antigravity_lane=QUARANTINED`，语义门拒绝无标记停用、排除表漂移和意外开放退役模型。

这两个开关是应急处置能力，正常通道不自动停用。发生明确账号限制、认证异常或
持续配额拒绝时，先停止调用方任务，再按需隔离；不通过换模型、换账号、清冷却
继续请求。隔离只停止生成路由，后台 token 刷新继续，不能证明 Google 侧风险状态
已解除。事务会重启 CPA，执行前须确认当前请求已结束；失败恢复备份并复验。

Google 的 [Antigravity FAQ](https://antigravity.google/docs/faq#why-cant-i-use-third-party-software-such-as-claude-code-openclaw-or-opencode-with-my-antigravity-login)
明确说明第三方软件访问 Antigravity 违反其条款，可能导致账号暂停或终止，并建议
第三方编程工具使用 AI Studio 或 Gemini Enterprise API key。当前保留的 OAuth
通道受此风险约束；订阅、固定出口、inflight=2、低频使用与一次成功均不是防封号
保证。官方 API 迁移需要独立凭据、项目与计费预算，不能把订阅 OAuth 自动替换为 API。
质量异常先查精确模型路由、thinking、token 上限、上下文和流式结束状态；
`gemini-3.8-flash-high` 返回 `gemini-3.8-flash-n` 的已记录观察不能单独证明降智。

### Antigravity 冷却与人工复核停流

保留 OAuth 通道时，admission 对 Antigravity 使用完整窗口退避：不执行原有
10 秒提前半开探测；窗口到期后，只放行一个真实来流请求验证恢复，不主动生成
巡检流量。上游 `Retry-After` 与 Google 结构化错误详情中的等待时间取最长值，
超过一天也不截短。Responses、Chat Completions、Messages、Gemini 原生
`generateContent` / `streamGenerateContent` 共用同一 lane；不改写模型或请求体。

上游 `401/403`、错误体/SSE 中明确的账号或服务停用消息进入人工复核停流。
后续请求本地返回 `403 operator_review_required`，无自动重试提示；这不代表
所有普通 `403` 都是封号。健康状态的 `hold_reason` 区分认证/权限待复核和明确
服务限制，`strict_backoff`、`state_persisted` 与 `state_write_failed` 可检查防护状态。
仍在途的成功请求不能清除该停流。

systemd 以 `nobody` 运行，并使用 mode 0700 的 `StateDirectory=cpa-admission`。
`/var/lib/cpa-admission/antigravity-risk.json` 只记录版本、等待截止时间和脱敏停流原因，
以原子替换保存，不保存凭据、请求或回答。启动时文件缺失、格式错误会拒绝启动；
运行中保存失败会停止该 lane 并将健康状态标记为 degraded。文件须随部署初始化，
不能用服务重启、删除文件或 `-RestoreAntigravity` 自动清除风险状态。
每次实际请求上游前先写入 `unverified_request` 标记，全部在途请求结束后才保存
最终状态；若进程崩溃或后续保存失败，重启会保留该标记并要求人工复核，避免将
未确认完成的请求当作安全重试依据。HTTP 认证/权限状态和流式错误事件一到达就
锁存，不等待流结束。若首次预写即失败，请求会在接触上游前拒绝；恢复存储并重启
后仍须先成功预写才可放行，该分支没有已发生的上游调用需要保留。

人工恢复必须先停止调用方任务，检查官方客户端/官方用量和限制提示；确认认证、
权限或账号限制已解决并获得恢复授权后，等待所有 lane 的 inflight/pending 清零，
停止 admission、备份风险状态文件，再将 `hold_reason` 清为 null。真实等待窗口未到
不得缩短 `not_before`；仅在已确认官方恢复/配额重置且授权复核后才可清为 0。
保持文件 owner=nobody、group=nogroup、mode=0600 后启动服务，先查 `/healthz`，
再至多单发一次授权验证请求。恢复失败立即停流，不轮换账号、不清冷却反复试通。
隔离标记与风险停流相互独立；`-RestoreAntigravity` 仅恢复排除表。

现有 `sensitive-words` 仅保留为兼容补丁；请求内容相关 429 的观察不能证明上游
检测机制，也不能证明混淆降低封号风险。不扩展混淆范围，迁移官方 API 时退役。
日常质量/生成矩阵默认排除所有 OAuth lane，保持这一已有开关即可。

`-QuarantineOAuthLuna` 与 `-RestoreOAuthLuna` 是 L3 风控静默期的**流量硬门**：
把该 provider 的全部别名从可路由目录移除，使持有公共 key 的外部消费者也无法再触达
唯一 ChatGPT Plus 账号。分级处置口径见
[cpa-ban-throttle-incident-response.md](cpa-ban-throttle-incident-response.md)。

- **机制**：改写 `config.yaml` 的 `oauth-excluded-models.codex`（与 `-Apply`
  计算排除清单是同一条路径，方向相反），并写状态标记
  `/opt/cliproxyapi/oauth-quarantine.json`（`0600`）。标记字段：`version`、
  `state`、`aliases`、`previous_codex_exclusions`（隔离前的完整排除清单）、
  `applied_codex_exclusions`（隔离后的完整排除清单）、`since`、`reason`。
  标记不是投影文件，属于运行态。
- **精确恢复**：`-RestoreOAuthLuna` 不靠"删掉 Luna 条目"猜测，而是先校验
  标记的 `aliases` 与当前路由清单一致、当前 `codex` 与 `applied_codex_exclusions`
  逐项相等（不等即 `REFUSE OAuth quarantine config drift detected`），然后写回
  `previous_codex_exclusions`。因此隔离期间任何第三方对排除清单的改动都会被
  拒绝而不是被静默覆盖。
- **整账号语义**：标记必须列全该 provider 的 OAuth 别名（当前是 `gpt-6-luna`、
  `gpt-5.6-luna` 与 `gpt-6.1-sol`）。只列一部分的标记会被 `cpa_policy.py` 判为
  `must name every configured OAuth alias`，不允许用它授权更宽的阻断。
- **凭据边界**：只读取拓扑，不修改、不复制、不删除、不回放凭据 JSON；后台 token 刷新继续
  运行，slot 不会因静默期过期；不调用 `reset-quota`，不清除冷却状态。输出
  `OAUTH_CREDENTIAL_RETAINED=yes`、`QUOTA_STATE_RESET=no`。销毁凭据仍只有
  `-DeactivateOAuthLuna`。
- **前置拓扑校验**：事务开始前严格确认 auth 目录里恰好一个目标 provider OAuth
  凭据（含 token 字段）；允许已登记的 Codex / Antigravity 凭据共存，不符即
  `REFUSE`，不发生任何写入。
- **基线语义门**：写入前先跑一次 `cpa_policy.py <config>`，基线不合法即
  `REFUSE baseline_policy` 并回显策略日志。隔离事务不是用来"修复"一个已经
  漂移的配置的——漂移要先单独归因处理。
- **事务性**：备份 config.yaml 与标记到 `/root/cpa-oauth-quarantine-backup-*`，
  原子改写、`docker restart cli-proxy-api`、轮询本地目录直至 200，然后断言
  隔离方向（Luna 必须消失、清单外 ID fail-closed）或恢复方向（Luna 不再被
  排除），最后跑 `cpa_policy.py` 语义复验；任一步失败即 `restore_all` 并输出
  `ROLLBACK_VERIFIED` / `ROLLBACK_FAILED`。幂等性：重复隔离或对无标记状态执行
  恢复都会被拒绝，不会静默 no-op。
- **拒绝不重启**：回滚前用 `cmp -s` 比对 `config.yaml` 与备份。字节一致说明
  什么都没改、运行中的容器仍持有隔离前配置，于是输出
  `ROLLBACK_SKIPPED no_mutation` 并跳过重启与就绪探测；只有确有改动才
  `docker restart` 并输出 `ROLLBACK_VERIFIED`。因此"拒绝执行"不会造成一次
  无谓的全量容器重启。
- **`RESTORED_OAUTH_ALIASES=pending_catalog`** 是预期读数而非失败：恢复后
  CPA 需要一点时间把 OAuth 路由重新登记进 `/v1/models`，此刻目录尚未更新。
  真正的恢复确认看下一次 doctor 的 `luna_state=available` 或
  `available_partial`（`catalog_oauth_aliases` 非 `none`）。注意不要只盯
  `catalog_gpt6_luna`：裸名 `gpt-6-luna` 会因上游/账号侧授权变动而缺席，
  兼容别名仍在服务时 lane 就是可用的。
- **互斥**：与其他远端写事务共用
  `/run/vps-ssh-launcher-maintenance.lock` 的非阻塞 `flock`。
- **与 `-Apply` 的关系**：隔离期间 `-Apply` 拒绝执行（
  `REFUSE OAuth lane quarantine is active`），避免例行投影把排除清单改回原样
  而静默撤销风控决定；需要 apply 时先 `-RestoreOAuthLuna`。
- **doctor 读数**：`==oauth-quarantine==` 输出 `oauth_quarantine=` 之一：
  `none`（无标记、无阻断，正常）、`active`（标记与阻断集合一致，契约成立）、
  `UNAVAILABLE`（config.yaml 不可读，不阻断）、`UNMARKED_OAUTH_BLOCK`（阻断
  无标记）、`INVALID_MARKER`（标记不可读或字段不合法）、`INCONSISTENT`（标记
  与阻断集合不一致）。除 `none` / `active` / `UNAVAILABLE` 外都会
  `mark_fail oauth-quarantine`。
- **语义层防回归**：`cpa_policy.py` 在没有标记时禁止阻断 OAuth 路由
  （`POLICY_FAILED`）；有标记时要求被阻断集合与标记完全一致——漏阻断报
  "still served"，多阻断报 "outside the quarantine marker"。

## 相关证据

- [`20260926-bwg-cpa-oauth-quarantine-projection.md`](../change-evidence/20260926-bwg-cpa-oauth-quarantine-projection.md)：
  OAuth lane 可逆隔离的投影与单机受控验收（含隔离/恢复往返的 `config.yaml`
  SHA-256 证据、拒绝路径不重启的实测、`pending_catalog` 读数口径）。
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


## OAuth 持久化风险状态的人工恢复

Claude 使用 `claude-risk.json`，Antigravity 使用 `antigravity-risk.json`，均在
`/var/lib/cpa-admission/`；provider 独立，保留 version 1 的字段
`version/not_before/hold_reason`。升级只创建缺失的 Claude 初始文件，不覆盖旧风险
状态；旧 Antigravity 状态逐字节保留。回滚代码和 unit 时仍保留两份状态，禁止
恢复旧快照覆盖新风险或因回滚而缩短上游等待窗口。

Claude 的 `strict_backoff/state_persisted` 与 Antigravity 均须为 true。
`hold_reason` 非空时先停止调用方并核对官方界面、限额截止时间、凭据状态。
`retry_window_review_required` 表示缺少可信等待期限，并不等于已封号；CPA 错误头
传递缺口见 [Claude 风险说明](cpa-claude-account-risk.md)。

只有确认官方已恢复并授权人工恢复后，才在所有 lane 空闲的维护窗口停止 admission，
备份目标 provider 状态，把 `hold_reason` 改为 null；未到期不得缩短 `not_before`。
只有独立确认官方额度已重置且获准复核，才可将截止时间归零。保持目标文件
owner=nobody、group=nogroup、mode=0600，父目录 0700，再启动服务并检查 `/healthz`。
如需生成验收，另做一次明确授权的正常请求；失败立即停流。不得轮换账号、清除状态
重试，或以服务重启、目录 restore、通用 reset 探针解除此锁。

初始化状态不复制任何 OAuth token；缺失或损坏状态由服务拒绝启动。正常运行前的
write-ahead、流式信号到达即锁存和持久化失败停流对两个 provider 同样成立。
