# BWG Antigravity 旧会话图片 detail 兼容性（2026-11-22）

## 触发问题

Codex Desktop 旧会话在历史 `input` 中保留了 `input_image.detail="original"`。
失败请求同时包含约 171 KiB 的 data URL 图片，且会话历史已累积多轮
`model_switch` / `skills_instructions` 内容。旧会话实际错误为
`stream disconnected before completion: Antigravity model ... is cooling down`
以及前置的 `RESOURCE_EXHAUSTED` 429。

外置 CLIProxyAPI 参考源码的 Responses → Chat Completions 转换将
`detail="original"` 映射为 `high`，理由是 Chat Completions 不支持
Codex 的 `original` 值。参考 revision 为本机
`D:\CODE\external\vps-ssh-launcher-references\repos\CLIProxyAPI`
中的 `a1eeb60e` 及其当前工作树。

## 仓库改动

- `9cb5529` — `fix(cpa): normalize stale antigravity image detail`
- `scripts/remote/cpa-admission.py`
  - 新增 `normalize_antigravity_image_detail()`
  - 仅作用于 `antigravity-oauth` 的 `/responses`
  - 仅转换直接用户消息内容中的 `input_image.detail="original"` 为
    `"high"`
  - 保留图片 URL / data URL，保留其他 detail 值
  - 不修改工具结果或 assistant 内容中的图片
  - 记录数量型 `protocol_normalized` 日志，不记录图片数据
- `tests/test_cpa_admission.py`
  - 覆盖图片保留、工具结果保护、非 Antigravity lane、非 Responses 路径
  - 覆盖正常 `Handler -> upstream` 转发和 `Content-Length` 重算
- `scripts/remote/cpa-admission-integrity-pin.txt`
  - 更新为 `95075d599719f769dd948ae5cc919519d171570c73ee884631a87a4b95c0291d`

## BWG 投影与回滚

- 先执行 `outputs/deploy-admission-image-detail-20261122.ps1 -DryRun`
  - `STAGED_CONFIG_LOAD_OK`
  - `ADMISSION_INTEGRITY_OK`
  - `DRY_RUN_OK baseline=2822eb0a... candidate=95075d59... no_swap=true`
- 正式事务
  - `REQUEST_GUARD_EXCLUSIVE_OK`
  - `ADMISSION_IDLE_OK`
  - `BACKUP_READY`
  - `ADMISSION_PIN_ROTATED old=2822eb0a... new=95075d59...`
  - `ADMISSION_IMAGE_DETAIL_LIVE_OK`
  - `DEPLOY_OK`
- 远端备份与回滚入口
  - `/opt/cliproxyapi/backups/20261122T102128.043122259Z-admission-image-detail-20261122`
  - 回滚时还原其中的 `cpa-admission.py`、`cpa-admission.json`、
    `cpa_request_guard.py`、`cpa-admission.sha256`，重启
    `cpa-admission`，再运行 `cpa-admission-integrity-check`

## 分层验证

| 层级 | 证据 | 结果 |
| --- | --- | --- |
| `repo_verified` | 受影响测试 | `353 passed, 3 skipped` |
| `repo_verified` | Full 门禁 `scripts/run_gates.ps1 -RunParallel` | `1860 passed, 30 skipped, 896 subtests passed` |
| `filesystem_projected` | 轮换事务 | `DEPLOY_OK`，备份已生成 |
| `host_loaded` | strict doctor | `DOCTOR_CONTRACT_OK`、`admission-integrity=OK`、`admission-health=OK`、`projection-drift MATCH` |
| `live_accepted` | 用户继续使用原旧会话 | 待自然复测，未发送 OAuth 合成探针 |

## 边界

这次兼容层修复旧会话的图片 detail 形状，不删除历史图片，也不改变
Antigravity 的 OAuth 重试、账号、模型或冷却策略。BWG 既有
`RESOURCE_EXHAUSTED` 429 仍属于上游容量/配额信号，不能仅凭本次代码和
doctor 证明其与历史大小存在单一因果关系。
