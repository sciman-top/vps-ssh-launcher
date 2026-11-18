# BWG CPA Antigravity Claude Opus 5.5 路由投影（2026-11-18）

## 目标与边界

- 用户要求在 CPA Antigravity OAuth 通道开放 `claude-opus-5-5-high`。
- 路由和上下文源于提交 `12872095baeae63464e6a9b6f7bd911e42448fbd`
  （`feat(cpa): open Antigravity Claude Opus 5.5 route`）；模型上下文为
  `1000000`。
- 此次只验证仓库契约、BWG 投影与 doctor。账号目前有持久风险 hold，因此没有发送
  Opus 5.5 生成请求，也没有把投影状态记作 `LIVE_ACCEPTED`。

## 本地证明

- `scripts/cpa_catalog_check.py --print-derived`：`CATALOG_CHECK=PASS`；
  派生的 `antigravity-oauth` 名单包含 `claude-opus-5-5-high`。
- `scripts/cpa_bwg_guardrails.ps1 -Profile bwg -AuditProjection`：
  `CPA_PROJECTION_AUDIT=PASS evidence=filesystem_projection`。

## 远端变更与回执

1. 首次 `-Apply` 因远端 `config.yaml` 仍排除新路由，返回
   `REFUSE antigravity exclusion blocks a declared antigravity alias`，并验证
   `ROLLBACK_VERIFIED`；该次没有完成投影。
2. 为匹配本次用户要求，先备份远端配置，再只移除
   `claude-opus-5-5-high` 这一条 Antigravity 排除项：
   - 备份：`/root/cpa-config-backup-antigravity-unexclude-20261118T041833Z-claude-opus-5-5-high.yaml`
   - 备份 SHA-256：`4dd4647c395f1b89fcee489a42a29ab0ee9e42ba5956058dd5532410fc4c465a`
   - 变更后配置 SHA-256：`08a53ada704f270c068ed4baa5db101dd6ac79a6a62d9a0347d9fdaf811d6d11`
   - 远端回执：`ANTIGRAVITY_EXCLUSION_COUNT=1->0`、
     `CONFIG_DELTA=exactly_one_antigravity_exclusion`、`REMOTE_EDIT_EXIT=0`
3. 随后 `scripts/cpa_bwg_guardrails.ps1 -Profile bwg -Apply` 退出码为 0，回执：
   - `BACKUP_DIR=/root/cpa-guardrails-backup-20261118T042557.036966829Z`
   - `READY_STATUS=200`
   - `GUARDRAILS_APPLIED`
   - 事务配置 SHA-256：`cd1bd4625cc872f273ccaaa1ddb4468642fa0d5400a8409ae664aa02013ccc01`
   - 重启窗口出现过短暂连接重置/回环端口未就绪；事务随后报告 `HEALTH_OK` 和
     readiness 200。
4. strict doctor（2026-11-18 04:27Z）确认：
   - `antigravity_lane=OK`，allowed 名单包含 `claude-opus-5-5-high`；
   - `MODEL_IDS_UNKNOWN=none`、`catalog_oauth_missing=none`；
   - `MODEL_CONTEXT_CATALOG` 中 `claude-opus-5-5-high:1000000`，
     `MODEL_CONTEXT_MISSING=none`；
   - 已部署文件投影 drift 全 `MATCH`；
   - 但 `runtime_binary_hash=MISMATCH`，且
     `lane-hold=HELD antigravity-oauth:account_restricted`、
     `lane-hold=FAIL`，最终为 `DOCTOR_CONTRACT_FAILED`（exit 1）。

## 验收判定

`repo_verified=PASS`、`filesystem_projected=PASS`、服务目录已读到新模型；
strict doctor 未通过，且 Antigravity lane 正处人工复核停流状态。
没有对该 lane 发起请求，所以 `LIVE_ACCEPTED=NO`。

依 [CPA 持久化风险状态人工恢复流程](../runbooks/cpa-gateway.md#oauth-持久化风险状态的人工恢复)，
先停止调用方并核对官方客户端、官方用量/限制提示和凭据状态。没有账号状态已解决且获准恢复的证据前，不清除风险状态、不轮换账号、不重试生成。恢复后，只有 strict doctor 通过，才可按 catalog checklist 对
`127.0.0.1:8318` 做一次受控请求；不重试。

## 回滚入口

- 代码回滚：撤销本次路由目录提交并重新投影。
- 本次 `-Apply` 事务备份：
  `/root/cpa-guardrails-backup-20261118T042557.036966829Z`。
- 手术前配置备份：
  `/root/cpa-config-backup-antigravity-unexclude-20261118T041833Z-claude-opus-5-5-high.yaml`。
- 回滚或恢复时保留 `/var/lib/cpa-admission/antigravity-risk.json` 的当前风险状态；
  不用配置回滚、服务重启或 `-RestoreAntigravity` 清除 lane hold。
