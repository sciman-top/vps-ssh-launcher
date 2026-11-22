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

## 后续 fork 换代：antigravity 单凭据日量帽（同日第二代）

**动机**：affinity 把会话钉在单凭据上，重日会把全部流量（及 Google 的单账号
启发式）压在少数 G4-capable 号上；#7768 把"单凭据集中"列为关联信号。
`AntigravityConfig` 原本只有 sensitive-words/connection-pool/tos-pause-window，
无任何配额旋钮（`antigravity-credits` 是 Claude 模型的兜底路由，非配额帽）。

**P1（本代已实施）**：新增 `oauth.providers.antigravity.per-credential-daily-cap`
（0=禁用）。executor 在 Execute/ExecuteStream 入口（短冷却检查之后）按
`auth.ID` 计数当日（UTC）上游尝试，达帽→对该 (auth, model) 落
`markAntigravityShortCooldownRequired` 至 UTC 日终（复用既有短冷却机制，
selector 自动换兄弟号）+ 429（带 Retry-After）。计数器进程内存态、重启即清
（偏向服务可用）、带锁、UTC 日翻轮换零。overlay 五文件：
`internal/config/config_types.go`、
`internal/runtime/executor/antigravity_daily_cap.go(+_test.go)`、
`antigravity_executor_execute.go`、`antigravity_executor_stream.go`。
构建事务 `outputs/build-cpa-v8080-antigravity-guard-20261122.ps1`
（三 overlay 叠加：gemini-raw → agy-effort → guard；路径逃逸校验 + 6 文件
效果 sha + vet/test 覆盖 internal/config 与 internal/runtime/executor）。
镜像 `cli-proxy-api-fork:v8.0.80-gemini-raw-daily-cap`
（`outputs/build-fork-image-20261122.ps1`，base digest 不变，fork-binary
sha 进 OCI 标签）。

**部署记录（同日 15:28Z）**：binary sha `39bc774f`（bwg 容器内 vet+test 全绿）→
镜像 `cli-proxy-api-fork:v8.0.80-gemini-raw-daily-cap`（id `efd9ef31`，base
digest 不变）→ swap 事务 `outputs/cpa-v8080-guard-swap-transaction-20261122.ps1`
（drain 锁 + 回滚武装 + `LANE_STATE_DELTA=none` + auth disabled 集合不变）→
config 增 `per-credential-daily-cap: 150`（保 inode 热加载实证 15:28:25Z）。
事务教训两条入账：①post-recreate 健康探针撞 CPA 重启竞态（ConnectionReset），
脚本需有界重试；②插件注册行版本 pin 必须随插件升级同步（0.21.27→0.21.29 各
回滚一次）。账面 `scripts/remote/fork-binary.sha256` 已同 commit 更新。
备份：`/root/cpa-config-backup-before-daily-cap-*.yaml`、
`/opt/cliproxyapi/backups/20261122T152802*-swap-v8080-to-daily-cap/`。

**受控实战验收（同日 15:36–15:44Z）**：日量帽实弹演练——cap 压到 1（写穿容器
挂载热加载）后对 `gemini-3.8-flash-high` 三发探针：conductor 日志捕获两次
`429 … err=antigravity daily credential cap reached, 29948/29941 seconds
remaining`（分别落在 `b7177976`、`gantasalabojes`，距 UTC 午夜秒数数学吻合），
第二发换到未触帽凭据 200——**记账→触发→按凭据停车→selector 换号→客户端
有界拒绝**全链实证。恢复 cap=150 热加载 + e2e 200 + doctor
`DOCTOR_CONTRACT_OK`（lane-hold=OK、projection-drift 全 MATCH、
runtime-binary=39bc774f 账面吻合）。已知自愈副作用：演练短冷却把
b7177976/gantasalabojes 对 flash-high 停至 UTC 午夜（模型级冷却，neon/G4
不受影响，其余 flash 凭据可用）。

**新坑位（入 runbook 陷阱节语境）**：`sed -i`（tmp+rename）对本仓
`/opt/cliproxyapi/config.yaml` 的单文件 bind-mount **再次实证不可见**——宿主
与容器 inode 分裂（宿主 524619 vs 容器 525709），宿主后续同 inode 写全部失效；
修复=docker exec 写穿容器挂载（无停机）+ drain 锁下 force-recreate 重绑归一。
演练/恢复全程用写穿完成。

**P2（已定规格，下代实施）**：跨号 thoughtSignature 泄漏封口——
`restoreGeminiResponsesTextSignatures`（translator，授权无关阶段）按
model+messageID 回注缓存签名；affinity 重绑（删号/停用/重启）后首个请求会把
A 号铸造的签名注入 B 号请求（#7846：签名密文含 cloud_project_number）。
设计：①缓存条目记录铸造 authID（写路径有 auth）；②replayScope 已携带
sessionKey（"responses:"+Session-Id，caller-scope 命名空间化）——写路径建立
sessionKey→keys 索引；③selector 两处 "bound to new auth"（普通 1839/LCP
2000）绑定到不同 auth 时按 sessionKey 清刷缓存条目。缺失签名的降级=回到
特性前形态（缓存本为修复 carriers 被剥的问题），可接受；清刷只影响重绑会话。
风险=translator 写链需要穿 sessionKey，爆炸半径在 sdk 翻译层，须独立事务。
