# BWG Antigravity user 角色身份字面量定点清洗（2026-11-25）

> 时间均为 UTC，本机日期 2026-11-25。生产 fork 代 `v8.1.2-7966fix` 不变。

## 指令与范围

用户 2026-11-24 指令：现有 OAuth 账号与 API key 渠道全部保留；按社区结论清洗
Codex 等的系统提示词以防 Antigravity 封号；授权连续执行到完成。本次没有退役
任何模型、账号或渠道。

## 取证（一手）

- 上游对 `request.systemInstruction` 提供三档机制：零宽 `sensitive-words`（自
  2026-11-17 起失效）、字面替换 `sensitive-words-replace`（本栈在役，4 个键）、
  有序 RE2 `sensitive-patterns-replace`（本栈未用）。三档都不作用于 user 角色。
  细节见 [上游台账](../runbooks/cpa-observed-baselines-and-upstream-log.md) 六节。
- 维护者在 [CLIProxyAPI #7553](https://github.com/router-for-me/CLIProxyAPI/issues/7553)
  （2026-11-15）的口径：即时 `429 RESOURCE_EXHAUSTED` 来自上游对系统提示中特定
  短语的内容过滤，应改用 `sensitive-words-replace` 的字面替换或删除，触发句逐客户端
  二分定位且可能有多段。零宽方案
  [#6280](https://github.com/router-for-me/CLIProxyAPI/pull/6280) 已于 2026-11-17
  关闭未合并。
- 实机基线：agy 车道 7 天 846 发里 831 发走 Responses 协议，WAF 型裸 429 只有
  2026-11-22 的 fork 会话（请求哈希 `1b0aa645df9f`），该会话当天以 403
  TOS_VIOLATION 收场，此后再未使用 agy 车道。

## 根因定位（离线，不发探针）

用本地 Codex rollout 与 admission journal 按线程关联：`client_request_hash` 即
`sha256(线程 UUID)[:12]`。

| 线程 | 成功 / 快拒 429 | user 角色里的身份句 | 工具输出里的 `codex` |
|---|---|---|---|
| `0f43c4f7c4ff` | 22 / 0 | 0 | 2005 |
| `17fdba2e83ea` | 78 / 0 | 0 | 572 |
| `98a98d8d465c`（同期对照） | 44 / 0 | 0 | 468 |
| `1b0aa645df9f`（fork） | 0 / 3 | 5（压缩交接摘要） | 157 |

成功的会话同样带压缩交接摘要、同样在压缩后用过 agy，差别只在身份句所在的角色。
卡死线程继承的压缩后历史里，交接摘要（user 消息，由上一个模型生成）复述了
`"You are Codex, an agent based on GPT-5..."`。systemInstruction 的字面替换够不到
user 角色；全量 user 文本改写已于 `b2419b5`（2026-11-21）因改写自然内容而摘除。
这与 2026-11-18 的受控探针一致：指纹句放进 user 消息即返回裸 429。

## 受控回放（经 8318，单发）

| 时间 | 条件 | 结果 |
|---|---|---|
| 16:03Z | 换代前的现行管线；重建的该会话历史 88 项 + 当日 Codex 桌面端转储信封（18 个工具） | 上游 610ms 裸 `RESOURCE_EXHAUSTED` |
| 16:43Z | 换代后的管线；同一历史 + 最小信封（转储已轮转，无工具） | `200` / `completed`，`usage_in=67246` |

第二次回放的 journal 依次命中 `antigravity_developer_notice model_switch=9
skills_instructions=2`、`antigravity_developer_context deduped_parts=11
promoted_bundles=1`、`antigravity_retry_history duplicate_user_turns=13`、
`antigravity_identity_user_text handoff_parts=1`，随后
`upstream_result status=200 successful=true protocol_result=completed`。

**归因保留**：两次回放的信封不同（有无工具）。归因依据是 11-18 受控探针、上表的
跨会话对照和 journal 显示的改写命中，不是严格的同信封 A/B；补做“同信封、关掉
清洗”的对照需要再制造一次 WAF 命中，没有做。回放共消耗 1 次 WAF 命中（16:03Z，
首发绑定到的在役免费号）。

## 仓库改动

- `06e76b2` — `fix(cpa): swap agent identity literals in identity-bearing user text on the antigravity lane`
  - `scripts/remote/cpa-admission.py`：新增
    `sanitize_antigravity_identity_user_text()`，仅作用于 `antigravity-oauth` 的
    `/responses`。user 文本段是 Codex 压缩交接摘要（以 "Another language model
    started to solve this problem" 开头）或含身份短语（`you are codex/gpt/...`）
    时，套用与 systemInstruction 相同的字面替换表；其余 user 文本、developer、
    assistant、工具输出原样保留。日志只记
    `kind=antigravity_identity_user_text handoff_parts=… identity_parts=…`。
  - `tests/test_cpa_admission.py`：只改写带身份的段且幂等、车道与路由作用域、
    替换表与 `cpa_policy` 的 systemInstruction 词表同源。
  - `scripts/remote/cpa-admission-integrity-pin.txt`：
    `5170375a917e985264d1a7e22a3406e88bffe4bc25e1bfc8737a0ef313f7a759`（同时带上
    `1cd54b9` 的跨错误帧账号限制保持）。
  - `scripts/remote/cpa_policy.py`：`sensitive-words-replace` 的注释改为复审结论，
    键值不变。
  - runbook：[封号应急](../runbooks/cpa-ban-throttle-incident-response.md) 的
    2026-11-25 复审条、上游台账六节的对照表。
- `4ddf460` — `disable-codex-cloaking` 断言（此前待投影，本次 `-Apply` 落地）。

## 分层验证

| 层级 | 证据 | 结果 |
| --- | --- | --- |
| `repo_verified` | Full 门禁 `scripts/run_gates.ps1 -RunParallel` | `1886 passed, 30 skipped, 903 subtests`；catalog/build/bandit/ruff/mypy 全绿；`DOCS_CHECK=PASS` |
| 部署预演 | `outputs/deploy-admission-identity-swap-20261125.ps1 -DryRun` | `STAGED_CONFIG_LOAD_OK`、`ADMISSION_INTEGRITY_OK`、`DRY_RUN_OK` |
| `filesystem_projected` | 正式换代 | `REQUEST_GUARD_EXCLUSIVE_OK`、`ADMISSION_IDLE_OK`、`ADMISSION_PIN_ROTATED old=665df57b… new=5170375a…`、`ADMISSION_IDENTITY_SWAP_FUNCTIONAL_OK`、`DEPLOY_OK` |
| `filesystem_projected` | `scripts/cpa_bwg_guardrails.ps1 -Profile bwg -Apply` | 17 项 `PROJECTION_HASH_VERIFIED`、`HEALTH_OK`、`READY_STATUS=200`、`GUARDRAILS_APPLIED` |
| `host_loaded` | strict doctor | `DOCTOR_CONTRACT_OK`；`admission-integrity=OK`、`POLICY_OK`、投影零不匹配 |
| `live_accepted` | 16:43Z 单发回放 | `200` / `completed`；journal 改写命中 |

## 回滚

- admission：备份
  `/opt/cliproxyapi/backups/20261124T163111.667198616Z-admission-identity-swap-20261125`。
  在同一维护事务内恢复其中的 `cpa-admission.py`、`cpa_request_guard.py`、
  `cpa-admission.sha256`，重启 `cpa-admission`，再跑
  `cpa-admission-integrity-check` 与 strict doctor。
- 投影：`/root/cpa-guardrails-backup-20261124T164048.063070496Z`。

## 边界

- 未改 `request-retry=0`、未清冷却、未轮换凭据、未改 `config.yaml` 键值、未改 fork。
- 只处理 Codex 交接摘要与含身份短语的 user 文本。Claude Code、ZCode 的身份句未纳入：
  两者不走本车道；#7553 报告它们的系统提示同样触发，若接入需先给
  `sensitive-words-replace` 加键并二分验证。
- 句子级触发不可穷举，上游规则可随时变化。清洗只针对内容过滤这条路径；封号的
  离线打标路径（见 #7768 遥测）不受影响，不构成账号保护。
- 回放用的重建是近似（当时转储已轮转），临时文件已在 bwg 与本机删除。
