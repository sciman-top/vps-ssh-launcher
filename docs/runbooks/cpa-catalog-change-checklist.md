# CPA 裸名/别名目录变更 Checklist

适用范围：bwg CPA 网关裸名/别名目录的增补、改名、退役
（真源 `scripts/remote/cpa_provider_routes.json`）。本清单把一次目录变更
收敛为照单执行，以本文件为起点，不再考古历史 diff。

## 分层事实：哪些手改、哪些派生

- **真源（手改）**：`scripts/remote/cpa_provider_routes.json`
  - `oauth_routes[].models`（OAuth lane）或 `providers[].models`（通道槽位）；
  - 排除清单：新 OAuth 名进 `codex_api_key_exclusions`（API-key lane 永不
    抢注），新 API-key 名进 `oauth_exclusions`（OAuth lane 永不抢注）；
    **退役名不删除**，保留在对应排除清单作墓碑（防复用/抢注，readiness
    fail-closed 拦截旧名重现）。
- **派生层（零改动）**：`cpa-health.py`（探针矩阵、expected_models、目录
  门）、`cpa_policy.py`（`EXPECTED_*` 常量与语义校验）、远端 config 的
  `oauth-excluded-models` 与 codex-api-key `excluded-models`（apply 时从
  manifest 派生）、`cpa-acceptance.py` 的生成契约断言。

## 必须同步的手改点

| 文件 | 位置 | 说明 |
| --- | --- | --- |
| `cpa_provider_routes.json` | 路由条目 + 排除清单 | 真源；退役留墓碑 |
| `scripts/remote/cpa-admission.json` | `lanes[].models` | 仅共享账号 lane 名单变化时 |
| `scripts/cpa_bwg_guardrails.ps1` | 两处 `expected` lanes 字典 + `CODEX_OAUTH_ROUTES_READY` 文案 | 独立预言机，**有意保留字面量**，勿改为派生 |
| `cpa_catalog_expectations.py` | `OAUTH_ROUTE_ALIASES` / `ADMISSION_LANE_MODELS` / `PROVIDER_MATRIX_TAIL` | 测试预言机唯一字面量 |
| `tests/test_cpa_guardrails_script.py` / `tests/test_cpa_admission.py` | 仅复核故意变化型夹具（如 `matrix_targets` 里被刻意排除的别名） | 其余断言从常量模块派生 |
| `docs/runbooks/cpa-oauth-luna-slot.md`、`cpa-gateway.md` | 目录描述段落 | 记变更日期 |

## 执行顺序（铁律）

1. 按上表完成仓库改动。
2. full gates：`pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1`
   （目录/config 契约变更必走 full，不做 focused）。
3. **commit**：`-Apply` 要求干净树（脏树直接 REFUSE）。顺序是
   **commit → apply → doctor**；apply 后再补提交会让 projection-drift 门
   锚不上 HEAD 必 FAIL。
4. `-Apply`：确认 `GUARDRAILS_APPLIED`、`READY_STATUS=200`、备份目录行。
   apply 自带 CPA 容器与 cpa-admission 服务重启。
5. doctor 终验（默认严格模式）：`DOCTOR_CONTRACT_OK`、drift 全 MATCH、
   `MODEL_IDS_UNKNOWN=none`、`catalog_oauth_missing=none`。
6. 定向单发实测：远端 loopback `127.0.0.1:8318`（经 admission 全链），
   b64 投影探针脚本，`python3 -u` + `--command-timeout 300`；**一发不重试**
   （408/429/5xx 不 follow-up）；`finish=stop` 且回显模型名正确才算
   LIVE_ACCEPTED。
7. push（`main == origin`）；真实远端写入按需在 `docs/change-evidence/`
   留脱敏记录。

## 已知坑

- apply 前的 doctor 基线里 `admission-health=FAIL` /
  `client-model-catalog-contract=FAIL` 可能只是"工作区已改、远端未投影"的
  预期差异；上游目录先出现新名时 `MODEL_IDS_UNKNOWN` fail-closed 拦截属按
  设计。先对照最近收尾证据再判断，不要带病 -Apply。
- doctor 的 `catalog_oauth_missing` 按排序输出，不是 manifest 序。
- OAuth 别名在 health 目录门里天生 optional：上游撤名 = `not_listed`
  优雅降级不红；上游新增未声明名 = fail-closed。
- doctor 探针目录契约（`test_cpa_doctor_luna_state_*`）直接消费 guardrails
  内嵌 Python 块，改 `CODEX_OAUTH_ROUTES_READY` 附近逻辑后必须跑
  `tests/test_cpa_guardrails_script.py` 全量而非单测。
- `tests/test_cpa_guardrails_script.py` 中 policy 负例排除清单与夹具目录
  文本同形，编辑锚点须带三行上下文。
- **受管 codex 组加模需先文件级扩 `requests.payload`**：apply 只校验该段
  不生成（缺段/顺序错即 semantic_policy FAIL 回滚）。条目必须按 policy
  派生的 **sorted 模型名序**排列（`==` 比较列表序敏感，`[muse, haiku]`
  会被 `[haiku, muse]` 期望拒绝）；扩段写入磁盘即可，随后的 -Apply 事务
  备份含该改动并自带容器重启，无需手动 docker restart。claude 族同受该
  钉死段约束，且在 `EXPECTED_PAYLOAD_TOOL_RULES` 里共有 **三处** claude
  追加，顺序均敏感：`override` 列表追加一条带 `exist` 门 `thinking.type`
  的 binding-controls 注入（`drop_block` +
  `betas=["thinking-binding-controls-2026-08-01"]`）；`filter` 列表先追加
  `safeguards` / `context_management` /
  `messages.#(output_config)#.output_config` 三个删除参数，再追加一条条件
  条目（match `messages.@reverse.0.role=="assistant"` → 删 `messages.-1`）。
  三处都要逐字段对齐，只补删除参数会 semantic_policy FAIL。
- **失败 apply 不落盘**：write-then-verify 下投影先在暂存字节上校验，
  semantic_policy 失败时磁盘保持原状；此时日志 `ROLLBACK_FAILED` 且无
  `BACKUP_DIR` 行属"无可恢复"的 trap 噪音，不是状态损坏——以远端
  manifest 哈希 + payload 段实测为准。
- 2026-10-16 实测：zen go 端点 Claude 族只走原生 Anthropic Messages
  （`POST /zen/go/v1/messages`，鉴权必须 `x-api-key` 头；`Bearer` 形
  401 `Go requires a credential` 是头形差异非凭据门控——初判曾误读）；
  chat/responses 面 400 `ModelProtocolUnsupported`。python 直探上游（自带
  `Python-urllib/…` 指纹）被 Cloudflare 1010 拦，curl 可达；go 路径
  `GET /models` 用 curl 可读（200）。受管 claude 路由据此钉 UA（见下条
  （4）），故本仓 python 探针不必伪造 UA 也能全链验收。
- **claude 族加模（2026-10-16 落地为 `claude_api_key_routes`）三处硬约束**：
  （1）base-url 停在 `/v1` 之前（`ClaudeExecutor` 自拼
  `{base}/v1/messages?beta=true`，zen 取 `https://opencode.ai/zen/go`）；
  （2）CPA 只对 `api.anthropic.com` 发 `x-api-key`，自定义网关一律发
  `Authorization: Bearer`，所以路由 headers 必须用占位符
  `<slot-api-key>` 声明凭据头，由 -Apply 展开成该 slot 的 `API_KEY_<n>`；
  组头与组密钥不一致、缺该头，或占位符出现在非 claude 族，
  `cpa_policy.py` 直接 FAIL（**只有** claude 族允许该占位符）；
  （3）`count_tokens` 无需适配：非 Anthropic base 一律本地估算
  （`shouldUseClaudeUpstreamTokenCount` 门），上游 404 不可达；
  （4）路由 headers 需钉 `user-agent`（值 `cpa-bwg-gateway/1.0`）：
  api-key 的 Claude 执行路径逐字转发调用方 UA，python 等 bot 形指纹会被
  上游 Cloudflare 1010 拒（403），且失败被 CPA 记为该凭据冷却，连带
  拖垮其他调用方（503 `auth_unavailable`）；codex 执行器对同类上游
  自带"不转发下游 UA"处理（源码注释直指 CF 1010），claude api-key 路径
  尚无，故由清单补齐。
  旁证：`cpa-health.py` 的 claude 探针走 `/v1/messages` 并要求
  `stop_reason=end_turn`；`cpa-acceptance.py` 本地夹具用顶栏
  `claude-api-key` 旧拼写（v8 加载时迁移到 `api-keys.claude`），并在
  收不到夹具密钥头时按 zen 口径回 401，防"投影丢了凭据头"只在真上游暴露。
- 时序参考：2026-09-30 gpt-6.1-sol 增补从改动到 LIVE_ACCEPTED 约 1 小时，
  其中编辑约 15 分钟，其余是门禁与四轮远端验收往返。
