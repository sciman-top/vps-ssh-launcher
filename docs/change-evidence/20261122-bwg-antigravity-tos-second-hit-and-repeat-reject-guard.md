# 2026-11-22 bwg：Antigravity 第二例 TOS 停用、双层锁释放与 repeat-reject 防护换代

**性质**：真实远端写入 + 事故证据留档（redaction-first；账号一律以 sha8 代号记录）。

## 事件时间线（UTC）

| 时刻 | 事件 |
| --- | --- |
| 12:04–13:09 | 线程 `01a2026a` 的一个卡死 turn（warm-fork 自 `01a20243`，`client_request_hash=1b0aa645df9f`，body ~342 KiB / ~84K input tokens）在 `gemini-4-hydrogen-xhigh` ×3、`gemini-4-neon-xhigh` ×2 连续吃上游裸 429（`RESOURCE_EXHAUSTED` 无 reset），每次落 60s 模型窗后窗内重试被本地拒。同小时 neon 200×54、全池其余请求正常 → 形状特异，非配额、非波次。 |
| 13:19 | 并行会话部署 `ac1a192`（bare-429 合成 retry 时长修正；pin `1d7960e1`）并重启 admission。 |
| 13:33:15 | 该 turn 重启后首发：上游返回 `403 PERMISSION_DENIED / TOS_VIOLATION`（账号级服务停用，响应带 appeal_url）。CPA 挂起凭据 `antigravity-ai.sciman.top@gmail.com.json`（=sha8 `ae5e8007`）+ 武装全池暂停 `tos-pause-window=2h`（TTL 至 15:33:15Z）；admission `lane_hold_latched reason=account_restricted`。 |
| 13:46:58 | 用户手动删除该凭据出池（CPA `(REMOVE)` 增量处理），余 7 号在册。 |
| 13:55:58 | 双层锁释放（见下节），8317 直探 200，余池健康。 |
| 13:57:33 | admission hold 释放（stop→写中性 risk→start），8318 回环探针 200。 |
| 13:58:50 | 提前翻回 `tos-pause-window=2h` → 8317 探针即刻 `503 "antigravity pool paused after TOS_VIOLATION"`：**翻 0 只抑制执法不删 kv 条目，旧 TTL 复活**（第二次实证，runbook 第 4 步据此修正）。 |
| 13:59 | 重新翻 `"0"`，复探 200；已挂 15:35Z（原 TTL 过期后）延时任务翻回 `"2h"` 并双探针复验。 |

## 双层锁释放（第二次闭环）与备份

备份：`/root/cpa-config-backup-before-tos-unpause-20261122T135558Z.yaml`、
`/root/antigravity-risk.before-release-20261122T135558Z.json`。

顺序：保 inode 翻 0 → 8317 直探 → `systemctl stop cpa-admission` →
`antigravity-risk.json` 置 `{"hold_reason": null, "not_before": 0, "version": 1}`
（nobody:nogroup 0600）→ start → 8318 回环探针 →（新）**持 0 至原 TTL 过期后**
翻回 2h + 单发复验。余池 7 号（含 3 个 G4-capable）探针 200，无连坐。

## repeat-reject 防护换代（本日新增远端写入面）

**动机**：裸 429 现行语义（`ac1a192`）下模型窗仍会在 release 侧收窄武装
（`capacity_error=... or antigravity_quota_check`），把同 payload 重试隔成
~60s 一发；但跨模型换窗逃逸（今天 hydrogen→neon）与长时锤击仍会把被拒内容
反复递给上游——即 CLIProxyAPI #7562/#7636/#7768 记录的 TOS 升级路径。

**语义**：以 `client_request_hash`（= `X-Client-Request-Id` 诊断哈希，同 turn
重试共享）为单位计数 bare-429 暴露；**30 分钟内第 3 次**武装 30 分钟请求级
暂停（`repeat_rejected_cooldown`，429 + `Retry-After` + 明确文案），在请求
进入上游前本地拦截；同 hash 之外的一切请求（兄弟线程、兄弟模型）不受影响。
内存态、重启即清、上限 512 条目、锁保护。日志行
`repeat_reject_pause_armed` / `lane_reject reason=repeat_rejected_cooldown`。

**换代**：pin `1d7960e1` → `1db06a15`（事务脚本
`outputs/deploy-admission-repeat-reject-20261122.ps1`，marker
`ANTIGRAVITY_REPEAT_REJECT_ARM_THRESHOLD = 3`，失败自动回滚）。
测试：`tests/test_cpa_admission.py::test_antigravity_repeat_reject_pause_semantics`
（阈值/到期/重臂/窗口滑出）与
`::test_antigravity_repeat_reject_pauses_unchanged_payload_before_upstream`
（1s 阶梯模拟真实节奏：3 发上游暴露后第 4 发本地拦、兄弟 id 不受累）。

**回滚**：部署事务自带 backup-first 回滚；代码回滚 =
`git revert` 单提交后重跑同模板事务（改 pin 对）。
