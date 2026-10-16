# 2026-10-16 bwg CPA claude-haiku-5-5 增补未遂与同日退役

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

## 复活条件与边界

- 上游对 go 端点 API-key 开放任一协议面，或取得 go 端点凭据类别凭据；
- 控制台 `inference` 面为另一计费通道（沿用"非 go 路径勿迁"边界）；
- python 直探上游被 CF 1010 拦属客户端指纹问题，不构成封锁依据，勿绕。
