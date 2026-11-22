# BWG Antigravity 已回复历史用户截图占位省略（2026-11-22）

## 触发问题与设计决策

在 `c0fd146` 上线后，旧会话在 19:41 的自然复测中确认重复 retry-only
`继续` 已按预期收敛（删除 `11` 条旧项，保留最新 `1` 条），但首次真实
上游请求仍在 `783ms` 返回裸 `429 RESOURCE_EXHAUSTED`。归一化后请求体
中最大的历史残留项是一张 `171,258` bytes 的 inline base64 用户截图
（位于早期用户消息，后面已有 `4` 条 assistant 消息与 `12` 组工具调用）。

经用户确认采用“历史截图只看一次、后续轮次省略”的策略：

- 最新一轮及尚未得到模型回复/动作的用户截图必须完整保留，确保模型至少
  能看图一次。
- 已经被后续 assistant/model/reasoning/tool-call 轮次处理过的更早历史
  用户截图，在 `antigravity-oauth` `/responses` 转发前替换为简短占位文本
  （提示模型此处曾有历史截图、现已省略，如需细节请用户重发），本地会话
  transcript 保持不变。
- 工具结果中的图片、非 `antigravity-oauth` 车道和非 `/responses` 路径均
  原样透传。

## 仓库改动

- `79d81b0` — `fix(cpa): omit answered historical user images on antigravity`
- `scripts/remote/cpa-admission.py`
  - 新增 `omit_antigravity_historical_user_images()`，并在
    `normalize_antigravity_image_detail()` 之前接入。
  - 仅作用于 `antigravity-oauth` 的 `/responses`。
  - 仅在 `input` 中存在至少一条 assistant/model/reasoning/function_call/
    custom_tool_call 项时，将最后一个模型响应位置之前的直接用户消息中的
    `input_image` 替换为占位 `input_text`。
  - 日志记录 `protocol_normalized model=... kind=antigravity_historical_image omitted=N`，
    不记录图片内容或用户正文。
- `tests/test_cpa_admission.py`
  - 新增历史已回复截图替换、最新未回复截图保留、工具结果图片保留、
    无回复历史豁免及非 Antigravity 车道/路径豁免测试。
- `scripts/remote/cpa-admission-integrity-pin.txt`
  - 更新为 `01d61b49ff4bbe644077d56b4605073ebbbc130c71534d374db42d15d49ee5c7`。

## 分层验证

| 层级 | 证据 | 结果 |
| --- | --- | --- |
| `repo_verified` | admission 与 pin 回归测试 | `359 passed, 3 skipped` |
| `repo_verified` | Full 门禁 `scripts/run_gates.ps1 -RunParallel` | `1865 passed, 30 skipped, 896 subtests passed`；catalog/build/bandit/ruff/mypy 全绿 |
| 离线回放 | BWG retained error dumps（`185508`、`192019`、`194101`、`194912`、`194923`） | 无图样本 `omitted=0`；4 份含旧截图样本均 `omitted=1`，单次请求减少 `171,157` bytes |
| 部署预演 | `outputs/deploy-admission-historical-image-omit-20261122.ps1 -DryRun` | `STAGED_CONFIG_LOAD_OK`、`ADMISSION_INTEGRITY_OK`、`DRY_RUN_OK ... no_swap=true` |
| `filesystem_projected` | BWG 正式原子轮换 | `REQUEST_GUARD_EXCLUSIVE_OK`、`ADMISSION_IDLE_OK`、`ADMISSION_HISTORICAL_IMAGE_OMIT_LIVE_OK`、`DEPLOY_OK` |
| `host_loaded` | strict doctor | live hash `01d61b49...e5c7`、`admission-integrity=OK`、`admission-health=OK`、projection drift 全部 `MATCH`、`DOCTOR_CONTRACT_OK` |
| `live_accepted` | 用户在原旧会话自然复测 | 待自然复测；未发送 OAuth 合成生成探针 |

## 回滚

远端备份路径：

```text
/opt/cliproxyapi/backups/20261122T120048.637179137Z-admission-historical-image-omit-20261122
```

回滚时在同一维护事务内恢复备份中的 `cpa-admission.py`、
`cpa_request_guard.py` 与 `cpa-admission.sha256`，重启 `cpa-admission`，
再运行 `cpa-admission-integrity-check` 与 strict doctor。

## 边界

本改动不清冷却、不轮换 OAuth 凭据、不改 `request-retry=0` 或并发预算、
不触碰 `zz`，也不修改客户端本地会话文件。历史截图在后续轮次中不再对
Antigravity 上游可见，若用户需要模型重新核对旧图细节，需要重新附带该图。
doctor 与投影通过不能替代原旧会话的自然复测，也不能证明上游不会返回
真实的模型级配额或容量 429。
