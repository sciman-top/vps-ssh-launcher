# bwg CPA admission：桶信封不再武装全车道持久键（2026-11-24）

## 背景（首例自然实弹）

2026-11-23 18:44Z（北京 11-24 02:44）：一路 Codex 重循环（~15 发 × 27–32 万 input/4min，
cache ~94%，affinity 粘 82cc5858=gantasalabojes）烧穿该号的 argon 周桶。上游信封：
`429 RESOURCE_EXHAUSTED / QUOTA_EXHAUSTED "Individual quota reached … Resets in
111h21m59s"`。11-22 粒度合并的语义把该桶信封逐字镜像成全车道持久键
`model:gemini-4-argon-high → 1795860377`（400920s），后续 argon 请求全部本地
`quota_group_cooldown` 拒绝——而 core 侧同事件已给该凭据落了 kv 持久的 per-credential
× per-model quota 冷却（管理 API `cooldowns[]` 实测），且同刻其余模型（neon/flash）
全部 200。裁决（用户，2026-11-24）：单号周桶不应封死全车道，修语义并一并解除当次。

## 变更（10719bf）

`scripts/remote/cpa-admission.py` `antigravity_envelope_scope`：QUOTA_EXHAUSTED
（"Individual quota reached"）分支不再返回 `quota_group`——无条件回落 model-first
默认（内存模型窗，mirror cadence 600s 有界复检 + recheck slot；重启有意遗忘，
per-account 持久性由 core 的凭据级冷却承担）。`MODEL_CAPACITY_EXHAUSTED`/`CAPACITY_
EXHAUSTED` 带 reset 仍落持久键（全局容量真 lane-wide）；`account` scope 不变。
integrity pin 同 commit 轮换 `c1120d58… → 665df57b…`。

测试：分类器单测桶信封断言翻转为 None；argon e2e 重写（`test_argon_bucket_envelope_
parks_model_at_recheck_slot`：持久态零污染 + 600s recheck-slot 本地拒 + sibling 200 +
重启后重探上游）；opus restart-survival e2e 被取代删除。重写过程发现并修复一个测试
竞态：`release()` 持久化可晚于响应抵达，重建 proxy 前必须 drain `inflight==0`
（生产单实例 + stop→写→start 手筋无此窗口）。

## 门禁与部署

- Full gates `-RunParallel` EXIT=0：1878 passed / 30 skipped / 900 subtests；
  ruff check+format、mypy、bandit 全绿（receipt 树 91121a56…）。
- 事务 `outputs/deploy-admission-bucket-clamp-20261124.ps1`（四文件同构换代，
  marker=`per-account bucket verdict`）：`DRY_RUN_OK` → `DEPLOY_OK
  pin=c1120d58…→665df57b…`，config sha 不变（f3e6a568…），备份
  `/opt/cliproxyapi/backups/20261123T191535.415827692Z-admission-bucket-clamp-20261124`。

## 当次解除（stop→写中性→start 手筋）

备份 `/var/lib/cpa-admission/antigravity-risk.json.before-release-20261123T191618Z.json`
（含旧键，审计用）→ stop → 原 inode 写中性 `{"hold_reason": null, "not_before": 0,
"version": 1}`（owner/perms 经 --reference 保持 nobody:nogroup 0600）→ start →
healthz 1s 内 ok、五车道 OPEN。

## 验收（只认 8318 过 admission）

- `gemini-4-argon-high` 单发 PONG → **200**（`lane_admit → upstream 200`，
  usage_in=37；上游回显 slug `gemini-4-argon-trial` 属已知命名观察项）——上游放行
  即证明 core 将流量导到非烧灼号（若仍粘 gantasalabojes 会 429），活号承接实弹成立。
- `gemini-3.8-flash-high` 单发 PONG → 200（served flash-n 变体，同上观察项）。
- risk 文件保持中性，无 quota_group 回写。

## 探针 key 手筋修正

记忆口径"sha8=22bd415f"仍有效，但"`agt_gw_` 前缀 grep -oE"提取已失效：现 key 为
39 字符、内嵌 `agt_gw_` 子串格式，整文件 grep 只摘出 17 字符子串 → 401。正确提取 =
`config.yaml` `access.api-keys` 第一列表项（当前 line 65），sha8 核对 22bd415f。

## 回滚

远端：从备份目录 `20261123T191535…-admission-bucket-clamp-20261124` 重放四文件并
重启（或按事务 rollback 路径）。仓库：`git revert 10719bf`（pin 随 revert 回
c1120d58…）。当次冷却解除不可逆需求为零——若需重建旧键，语义已改，桶信封不会再
武装持久键（这正是本变更目的）。
