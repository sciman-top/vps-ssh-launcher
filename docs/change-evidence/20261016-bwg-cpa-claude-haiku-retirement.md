# 2026-10-16 bwg CPA claude-haiku-5-5：增补未遂、退役、归因修正与同日上线

> 本文件记录同一日内两次结论翻转与最终落地；**终态见末节**「最终状态
> （同日更晚）：受管 claude 通道族上线并双面验收 LIVE_ACCEPTED」。

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
- 未做：真实 desktop / Claude Code 会话级自然验收（客户端仍按其自身 UA
  直连网关，网关已统一改写）；`auto-update` 心跳当日 `busy` 属既有节奏，
  与本次改动无关。
