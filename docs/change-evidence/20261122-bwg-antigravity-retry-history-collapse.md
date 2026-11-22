# BWG Antigravity 旧会话 retry-only 历史收敛（2026-11-22）

## 触发问题

Codex Desktop 旧会话在 Antigravity 车道连续失败后，客户端把失败的
user turn 留在本地 transcript 中；用户每次输入 `继续` / `continue`
重试，又追加一个相同 user item。失败窗口内请求没有产生任何
assistant、reasoning 或 tool 输出，于是上游看到的是连续重复的短
user 指令和大量 developer 注入，而不是正常对话历史。

19:20 自然复测补全了第一代守卫的盲区：该会话此时已包含
assistant/tool/reasoning 历史，但仍保留 `11` 条完整文本为 `继续` 的
user item。BWG 首次真实请求在 `846ms` 收到裸 `429 RESOURCE_EXHAUSTED`；
随后两次请求才被本地 `model_cooldown` 拒绝（`59s`、`56s`）。因此
“整段历史为空”不能作为收敛前置条件，重复 retry-only item 需要逐项识别。

本地 rollout 取证显示，问题窗口的请求约为 `364362` bytes，包含
`31` 个 message item：`22` 个 user、`9` 个 developer、`0` 个
assistant；`继续` 连续重复 5–6 次，`continue` 也重复出现。BWG 侧
对应的首次上游结果是 Google 原始裸 429：

```json
{"error":{"code":429,"message":"Resource has been exhausted (e.g. check quota).","status":"RESOURCE_EXHAUSTED"}}
```

该响应没有 `Retry-After`、`quotaResetDelay`、thinking signature 或
encrypted content 错误。后续 `Antigravity model ... is cooling down;
retry after 57s` 是 admission 对首次裸 429 的模型级冷却结果。

同一旧会话的对照信息显示：GLM 与 DeepSeek 模型可用，GPT 当时只是
额度耗尽；异常集中在 Antigravity 车道的 Claude/Gemini 模型。因此本切片
不把问题归因于 Codex 旧会话的通用上下文或所有上游配额，而只处理
Antigravity Responses 形状中由失败重试累积的重复 user turn。

## 仓库改动

第一代：

- `24e7f20` — `fix(cpa): collapse antigravity retry-only history`
- `scripts/remote/cpa-admission.py`
  - 新增 `collapse_antigravity_retry_user_turns()`
  - 仅作用于 `antigravity-oauth` 的 `/responses`
  - 仅当整个 `input` 不含 assistant/model role、reasoning、
    function call/output、custom tool call/output 时才允许收敛
  - 仅识别完整文本等于 `continue`、`retry`、`resume`、`继续`
    的 user item；同一文本重复时删除旧项，保留最新一项
  - 普通用户任务、图片、工具结果和已有正常 assistant/tool 历史的
    请求原样透传
  - 日志仅记录 `kind=antigravity_retry_history` 与删除数量，不记录
    用户正文或线程内容
- `tests/test_cpa_admission.py`
  - 覆盖重复 retry-only item 只保留最新一项
  - 覆盖正常 assistant 历史、图片和普通用户内容不被修改
- `scripts/remote/cpa-admission-integrity-pin.txt`
  - 更新为 `200273c266e67188e763402856e661f21e4d23ba90653b44a18fa5cdbfeb7d1a`

第二代：

- `c0fd146` — `fix(cpa): collapse retries across antigravity history`
- `scripts/remote/cpa-admission.py`
  - 移除“整个 input 无 assistant/tool/reasoning 历史”的总开关
  - 逐项识别完整 retry-only 文本；同一文本重复时删除旧项并保留最新项
  - assistant、developer、tool、reasoning、图片、任意普通用户文本和
    非 retry-only 用户项不做删除
  - 该语义仍只作用于 `antigravity-oauth` 的 `/responses`
- `scripts/remote/cpa-admission-integrity-pin.txt`
  - 更新为 `ebbec1d431f7c8df3417b12e169bef2fc44df71c6cf1925b4b68d63a6c37d1a2`
- `tests/test_cpa_admission.py`
  - 覆盖已有 assistant/function history 时仍收敛重复 retry-only item
  - 覆盖收敛时保留 assistant、工具历史和图片项

## 验证

| 层级 | 证据 | 结果 |
| --- | --- | --- |
| `repo_verified` | 第一代 admission 回归与 pin 测试 | `356 passed, 3 skipped` |
| `repo_verified` | 第一代 Full 门禁 | `1862 passed, 30 skipped, 896 subtests passed`；catalog/build/bandit/ruff/mypy 通过 |
| `repo_verified` | 第二代 admission 回归与 pin 测试 | `357 passed, 3 skipped` |
| `repo_verified` | 第二代 Full 门禁 | `1863 passed, 30 skipped, 896 subtests passed`；catalog/build/bandit/ruff/mypy 通过 |
| 离线回放 | 用候选源重放 retained error dumps | 18:49 样本删 `5` 留 `1`；18:52 删 `9` 留 `1`；18:53 与 19:20 均删 `10` 留 `1` |
| 部署预演 | `outputs/deploy-admission-retry-history-20261122.ps1 -DryRun` | `STAGED_CONFIG_LOAD_OK`、`ADMISSION_INTEGRITY_OK`、`DRY_RUN_OK ... no_swap=true` |
| `filesystem_projected` | 正式原子轮换 | `REQUEST_GUARD_EXCLUSIVE_OK`、`ADMISSION_IDLE_OK`、`ADMISSION_PIN_ROTATED`、`ADMISSION_RETRY_HISTORY_LIVE_OK`、`DEPLOY_OK` |
| `host_loaded` | strict doctor | `admission-integrity=OK`、`admission-health=OK`、`admission=OK`、projection drift 全部 `MATCH`、`DOCTOR_CONTRACT_OK` |
| 部署预演 | `outputs/deploy-admission-retry-history-anywhere-20261122.ps1 -DryRun` | `STAGED_CONFIG_LOAD_OK`、`ADMISSION_INTEGRITY_OK`、`DRY_RUN_OK ... no_swap=true` |
| `filesystem_projected` | 第二代正式原子轮换 | `REQUEST_GUARD_EXCLUSIVE_OK`、`ADMISSION_IDLE_OK`、`ADMISSION_RETRY_HISTORY_ANYWHERE_LIVE_OK`、`DEPLOY_OK` |
| `host_loaded` | 第二代 strict doctor | live admission hash `ebbec1d4...1a2`；`admission-integrity=OK`、`admission-health=OK`、projection drift 全部 `MATCH`、`DOCTOR_CONTRACT_OK` |
| `live_accepted` | 用户继续使用原旧会话 | 待自然复测；未发送 OAuth 合成生成探针 |

## 回滚

远端备份：

```text
/opt/cliproxyapi/backups/20261122T110746.196263558Z-admission-retry-history-20261122
/opt/cliproxyapi/backups/20261122T113714.753574147Z-admission-retry-history-anywhere-20261122
```

回滚时在同一维护事务中恢复备份内的 `cpa-admission.py`、
`cpa_request_guard.py`、`cpa-admission.sha256`，重启
`cpa-admission`，再运行 `cpa-admission-integrity-check` 与 strict doctor。

## 边界

本修复不清理冷却状态、不轮换 OAuth 凭据、不增加重试或并发、不改动
`request-retry=0`，也不触碰 `zz`。它不会删除普通用户内容、图片或工具
结果；只有 Antigravity Responses 中完整文本等于 `continue`、`retry`、
`resume` 或 `继续` 且同一文本重复出现的 user item 会删旧留新。上游仍可能
返回真实容量或配额 429，doctor 与投影通过不能外推为账号长期安全、
封号风险或输出质量验收。
