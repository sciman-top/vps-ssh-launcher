# 2026-10-16 bwg CPA claude-haiku-5-5：增补未遂、退役、归因修正与同日上线

> 本文件记录同一日内两次结论翻转与最终落地；**终态见末两节**：受管 claude
> 通道族上线并双面验收 LIVE_ACCEPTED，以及真实 Claude Code 客户端经公网
> 8443 的自然验收通过。

## 任务与结论

用户指令"槽位 2 / opencode.ai 增加开放 Claude Haiku 5.5 模型"，授权本机
（office 工作站）自主执行全部投影与验收。结论：**不可服务，同日退役**。
zen go 端点对 API-key 凭据类别三面全闭，非协议适配问题，属凭据类别门控。

提交链：d8a3978（slot 2 直投影版目录变更）→ 665e2b8（迁 codex 族）→
66bd7ab（退役闭环）。本机 full gates 三轮均 621 passed / 302 subtests /
Bandit/Ruff/mypy 绿。

## 实证链（单发一发即停，未重试）

| # | 面 | 结果 | 定性 |
|---|---|---|---|
| 1 | 8318 → slot 2 openai-compat `/chat/completions` | 400 `ModelProtocolUnsupported` | 上游错误串（不在 CPA 源码，锚点 4b03069 同 muse） |
| 2 | 8318 → codex 族 `/v1/responses` | 400 `ModelProtocolUnsupported` | 同上；协议面同样关闭 |
| 3 | curl 直探 `https://opencode.ai/zen/go/v1/messages`（Anthropic 面，Bearer+anthropic-version+会话头） | 401 `"Upstream request failed: Go requires a credential"` | **凭据类别门控**：Claude 族在 go 端点要求登录态凭据，API-key 不可达 |
| — | `/zen/go/anthropic/v1/messages` | 404 | 路径不存在 |
| — | `GET /zen/go/v1/models`（python） | 403 | go 路径无目录读端点；python UA 被 Cloudflare 1010 拦（客户端指纹，勿绕；curl 可达） |

上游官方文档口径：Claude 族走 Anthropic Messages
（公开面 `opencode.ai/inference/anthropic/v1/messages`），Responses 面仅
GPT/Grok/Muse，chat 面仅 GLM/Kimi/DeepSeek 等——与本网关实测一致。

## 过程事务与回滚

- providers.env 本机缺失 → 经 SSH 只读提取远端 config 四槽位
  base-url/key（凭据仅指纹过日志：ea7459…f1e7 / 1c5d9a…k_wZ /
  1df6e4…o3lO / 7a9330…T5A6），写入 `%APPDATA%\vps-ssh-launcher\providers.env`。
- 第 1 次 -Apply（d8a3978）成功：BACKUP
  `cpa-guardrails-backup-20261016T035340Z`，DOCTOR_CONTRACT_OK（drift
  MATCH、MODEL_IDS 12 模型）。
- 第 2 次 -Apply（665e2b8）semantic_policy FAIL 回滚：`requests.payload`
  为钉死段，apply 只校验不生成。教训：条目须按 policy 派生的 **sorted
  模型名序**；失败 apply 不落盘（write-then-verify），`ROLLBACK_FAILED`
  且无 `BACKUP_DIR` 属 trap 噪音，远端实测无损伤。文件级扩段（快照
  `config.yaml.bak-20261016-payload-haiku`）+ 换序（快照
  `…-payload-order`）后第 3 次 -Apply 成功（BACKUP
  `…T041642Z`，DOCTOR_CONTRACT_OK）。
- /responses 验收仍 400 → 三面定性 → 退役。payload 还原（快照
  `…-payload-retire`）后第 4 次 -Apply 成功（BACKUP `…T042706Z`）。

## 终态（doctor 实测）

`DOCTOR_CONTRACT_OK`；drift 全 MATCH（want=66bd7ab manifest）；MODEL_IDS
11 模型（无 haiku，坏目录项已清除）；`MODEL_IDS_UNKNOWN=none`；
antigravity 排除表 8 项（haiku 墓碑保留：本网关无人认领该名，防
antigravity 目录自动注册）；luna available；admission OK。

## 补充归因修正（同日更晚，2026-10-16）

上表第 3 行的定性**有误，现予修正**：401 `Go requires a credential`
不是凭据类别门控，而是**鉴权头形差异**。zen go 端点的 Anthropic 面只认
Anthropic 原语 `x-api-key` 头；`Authorization: Bearer` 形不被读，故报
"requires a credential"。换 `x-api-key` + `anthropic-version` 后单发实测：

```
POST https://opencode.ai/zen/go/v1/messages
x-api-key: <slot-2 key>; anthropic-version: 2023-06-01;
x-opencode-session: cpa-bwg-gateway-01
→ HTTP 200, type=message, stop_reason=end_turn, text="OK",
  usage in=18/out=4（正常入账）
```

另证：`GET /zen/go/v1/models`（curl）= 200、37 模型，其中唯一 Claude 即
`claude-haiku-5-5`——此前 403 是 CF 拦 python UA，非路径不存在；控制台
`/inference/v1/models` 同 key 亦 200（47 模型、5 个 Claude ID）。

**修正后的结论**：模型经 API-key 当下即可直连调用（上述头形）；退役的
正确表述是"网关通道族缺失"——CPA 侧需新建 claude_api_key_routes 通道族
（`ClaudeExecutor` 发 `{base}/v1/messages?beta=true`，base 取
`https://opencode.ai/zen/go`）。建设前需核实 zen 对 `?beta=true` 后缀与
`count_tokens` 端点的容忍度。远端终态（不投影 haiku、墓碑保留）不变。

## 最终状态（同日更晚）：受管 claude 通道族上线并双面验收 LIVE_ACCEPTED

结论：**`claude-haiku-5-5` 已作为受管 claude 通道族上线并经双面单发实测**。
提交链续 `4a6d429`（新建 `claude_api_key_routes` 通道族）→ `cd357bf`
（claude 路由钉 `user-agent`）。本机 full gates 两轮均 622 passed /
1 skipped / 302 subtests / Bandit+Ruff+mypy 绿。

### 第三次归因修正：CF 1010 是调用方指纹，且会经凭据冷却放大

通道族落地后 8318 单发仍 503 `auth_unavailable`（0.01s 不出网）。读
`docker logs cli-proxy-api`（容器名不是 `cliproxyapi`）见唯一一次上游尝试：
`403 | 18ms | upstream execution failed: provider=claude
model=claude-haiku-5-5 auth=api_key=oc_s… err={…error-1010/}`——**CPA 已带上
注入的凭据头，是 Cloudflare 1010 拦的**。逐变量实测（同 key/URL/凭据头，
仅换 `User-Agent`）：`Python-urllib/3.13` → 403；`curl/8.5.0` /
`CLIProxyAPI/8.0.30` / `vps-ssh-launcher-probe/1.0` /
`claude-cli/2.1.280 (external, cli)` → 200 `end_turn`。即拦的是 **bot 形
客户端指纹**，与凭据头形无关。CPA 把该 403 记成该凭据的冷却，于是此后
**不分调用方**一律 503——一次 python 调用即可拖垮共享凭据。

### 处置：路由钉 UA（与依赖同族语义一致，最小改动）

CPA 的 codex 执行器对同类上游本就"不转发下游 UA"（源码注释直指 CF 1010：
`codex_executor_request.go` `applyCodexDirectImageHeaders` 上方
"Downstream client User-Agent values are not forwarded to reduce Cloudflare
1010 blocks"），claude api-key 路径尚无该处理。故由清单在
`claude_api_key_routes[0].headers` 钉 `user-agent: cpa-bwg-gateway/1.0`
（组自定义头在鉴权头之后应用，`ApplyCustomHeadersFromAttrs` 用
`Header.Set` 覆盖，生效位置同已钉的会话头与凭据头）；`cpa_policy.py` /
doctor 按清单反向核对该组头集合，组头漂移即 fail-closed。改动面 =
清单 + 一处测试期望 + 两份 runbook 说明，无代码、无契约、无密钥进仓库。

### 投影与验收证据

- `-Apply`（bwg）：`GUARDRAILS_APPLIED`、`READY_STATUS=200`、models=12；
  BACKUP `/root/cpa-guardrails-backup-20261016T104726.742743108Z`
  （PRUNE 保 8 份，现存 8）。
- strict doctor：`DOCTOR_CONTRACT_OK`（drift 全 MATCH、`MODEL_IDS_UNKNOWN=none`、
  `cooldown_state=none`、`auth_unavailable_by_lane={"claude/claude-haiku-5-5": 4}`
  为当日失败期残留 error dump，非当前态）。
- 落盘核对（只读解析活动 `config.yaml`，只打印布尔与公开值）：
  `base-url=https://opencode.ai/zen/go`、组头 =
  `user-agent,x-api-key,x-opencode-session`、
  `user-agent=cpa-bwg-gateway/1.0`、会话头钉值一致、
  `x-api-key` 解析等于本组 slot 2 凭据（**不打印密钥**）、模型仅
  `claude-haiku-5-5`。
- 8318 全链单发（调用方刻意用 python 默认 UA，即被拦指纹）：
  HTTP 200 / `stop_reason=end_turn` / 模型回显 `claude-haiku-5-5` /
  text=`OK` / usage 18→4，0.96s。
- 公网 8443 随机 capability 路径单发：HTTP 200 / `end_turn` / text=`OK` /
  usage 18→4，0.76s——外部数据面同样可用。

### 边界与遗留

- 本仓探针不再需要伪造 UA；网关向上游呈现自身身份而非某个客户端指纹，
  因此路由可用性不再取决于调用方 HTTP 库，也不再存在"单枚 bot 指纹调用
  拖垮共享凭据"的路径。
- 未做（当时）：真实 desktop / Claude Code 会话级自然验收（客户端仍按其
  自身 UA 直连网关，网关已统一改写）——该窗口已由末节自然客户端验收关闭；
  `auto-update` 心跳当日 `busy` 属既有节奏，与本次改动无关。

## 自然客户端验收与两处字段补钉（同日最后一轮）

按用户"需要投影，模拟/受控实战验收？"的追问，追加真实客户端验收：真实
Claude Code 2.1.300 进程，env 覆盖 `ANTHROPIC_BASE_URL` 为公网
`https://fq.sciman.top:8443/<capability 前缀>`、`ANTHROPIC_API_KEY` 为网关
access key，`CLAUDE_CONFIG_DIR` 指向隔离目录（仅变量承载，不落盘不回显）。

### 首轮暴露：zen 未声明的三个字段

同一 88KB 真实客户端会话体被上游逐个 400，均为
`[invalid_request_error] <path>: Extra inputs are not permitted`：

1. 顶层 `safeguards`（权限模式/auto 模式分类器上下文）；
2. 顶层 `context_management`；
3. 挂在 system 消息上的 `output_config`（`{"effort": "medium"}`）。

单变量实测：不去该字段即报该字段，去掉即前进；三者全去后同一请求 200 且
`text/event-stream` 正常开流（收敛点，非猜测）。

### 处置：钉死段按模型名扩到三个参数

`requests.payload.filter` 的 claude 条目参数由 `[safeguards]` 扩为
`[safeguards, context_management, messages.#(output_config)#.output_config]`，
顺序由 `cpa_policy.py` 派生、apply/doctor 反向核对（该段 apply 只校验不
生成，故远端 `config.yaml` 同步手工扩条，备份
`config.yaml.bak-20261016-safeguards` 与
`config.yaml.bak-20261016-safeguards2`）；`cpa-health.py` 的 claude 生成
探针每次携带这三个字段做行为级回归，钉死段丢失即 doctor FAIL。提交：
`07d5521`（首版仅 `safeguards`）→ `ca2fa92`（补齐另两个）。本机 full
gates 622 passed / 1 skipped / 302 subtests，bandit/ruff/mypy 绿。

### 投影、doctor 与自然验收证据

- `-Apply`（bwg）：`GUARDRAILS_APPLIED`、`READY_STATUS=200`、models=12；
  BACKUP `/root/cpa-guardrails-backup-20261016T113626.418039951Z`；
  PRUNE 保 8 移除 1。
- strict doctor：`DOCTOR_CONTRACT_OK`；12 项 projection-drift 全 MATCH；
  `MODEL_IDS_UNKNOWN=none`、`cooldown_state=none`。
- 自然验收（同脚本复跑）：`exit_code=0`，2.87s，stdout 尾行 `OK`。
- 日志窗口（access log 字节 offset 548539 → 549562，客户端 IP 掩码、
  长 token 脱敏）：claude 请求**恰一条**
  `method=POST route=other status=200 upstream_status=200
  request_time=1.299 auth_status=200`（`16/Oct/2026:11:37:50 +0000`）；
  窗口内另 3 条 `route=responses 200`（96–245KB）属并发其他消费方，与本
  次会话无关。
- 容器日志同刻仅一条 `model=claude-haiku-5-5`（`selector.go: session-affinity:
  cache miss`，单发、无重试、无重放）。
- `/opt/cliproxyapi/auth/logs/` 无新增 `error-v1-messages-*.log`：仍为当日
  失败期那 5 份，最新一份 `...T192716-...` 属上一轮（修前）会话。

判据对照：修前同一自然会话被上游 400 打断并按 400 落 error dump；修后同一
会话单发直通、无 dump。

### 客户端旁注（非本侧缺陷）

2.1.300 打印一行 auto mode 分类器计费提示，称本会话"isn't eligible because
your requests go through fq.sciman.top:8443"，并给出上游文档链接。原因是网关
按上游要求剥离 `safeguards`，客户端据此判定自家新计费路径不可用而继续走旧
计费；属客户端提示性降级，会话与作答不受影响。

### 本轮边界

- 未跑 desktop / Codex 侧会话；公网面未再单独单发（自然验收经公网 8443
  完成，已覆盖该面）。
- capability 前缀与网关 access key 仅存在于验收进程变量中；本轮日志取证
  全部按字节窗口 + 掩码/脱敏输出。
