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

**演练副作用清除（同日 16:4xZ）**：cap=1 演练的短冷却把 b7177976/gantasalabojes
对 flash-high 停至 23:59:59Z（KV 持久化，重启不清；admission 侧表现为 600s
有界模型窗循环，`lane_reject retry_after=597→464`）。kv.db 非 sqlite（homekv
自有格式，禁止文件手术）；**正确路径=管理 API**：清单
`GET /v0/management/auth-files`（X-Management-Key，`/root/.cpa-management-key`；
v0/v8 两前缀路由组不同——auth-files 只在 v0，cooldown/reset 只在 v8）→
`POST /v8/management/routing/cooldown/reset` `{"auth_index": "<sha8>"}` 逐号
清除。两号 200 ok 后 flash-high 直连/过 admission 双路 200、neon 200、零
lane_reject。

## 升级后体检（v8.1.1 fork，2026-11-22 17:03Z swap，17:43Z 复核）

并行会话完成 `ops(cpa): upgrade bwg to CLIProxyAPI v8.1.1 fork and
workbuddy-provider 0.21.30`（9475f2f）：**三补丁栈完整移植**（gemini-raw +
agy-effort + antigravity-guard/daily-cap，Commit=`6eb8554f+gemini-raw+
agy-effort+daily-cap`，binary `e8286533`，账面已更新），overlay v8.1.1 移植与
v8.0.80 原版逐字一致（仅行尾差）。复核全绿：admission pin `1db06a15` 未动、
config 两旋钮存活、WorkBuddy 0.21.30 插件 active ping 8/8 + credits sweep 8/8
+ 16 models、池 7 号、探针 flash/neon 200、`DOCTOR_CONTRACT_OK`。
缓存边界对比（按 swap 真实边界 17:03:36Z 拆窗）：antigravity 0.911→**0.946**
(N=162)、chatgpt 0.150→**0.749**（0.15=recreate 后会话重预热瞬态，N=23）——
**零缓存回归**。上游 83 commits 风控面速览：Retry-After header 兜底（body
分类不变，对 admission 无影响）、CountTokens 加冷却（desktop 不走该路径、
不进日量帽计数）、403/401 凭据级隔离（利好）、reasoning replay 提交时序两改
（P2 规格需按新代码复核）、新增 Copilot provider（非本车道）。外置参考仓已
重置至生产锚 6eb8554f（工作树零污染，历史"本地污染"标记解除）。

**v8.1.1 日量帽受控演练（同日 17:54Z）**：cap=1 写穿热加载后双探针——
conductor 实证两笔 `429 … err=antigravity daily credential cap reached,
21955/21954 seconds remaining`（b7177976 于 argon-high=用户实时流量、
jalumuruvenkatarao 于 flash-high=演练探针；距 UTC 午夜秒数吻合），第二发换号
200。恢复 cap=150 后经管理 API 读**运行态** config 确认
`per-credential-daily-cap=150`（日志窗口屡次漏抓 reload 行，运行态读数才是
权威判据），演练冷却两号经管理 API reset 清空，flash/neon 双 200。手筋固化：
**演练/恢复一律写穿+运行态读数验收+收尾清冷却**。附加发现（17:54Z）：演练期
cap=1 的 429（`rate_limit_error` + Retry-After 21955s）被 admission 镜像成
**600s 有界模型窗**（`lane_reject retry_after=597→346`，579KB argon turn 的
dump=CPA 自产 cap 拒绝非上游判定）——有界复检设计按预期工作，但意味着演练
期间目标模型对全体客户端 600s 循环不可用，**演练协议追加：恢复 cap 后须等
admission 窗过期（≤600s）或明示用户窗到期时刻**。窗过期后 18:04:14Z 复验：
`lane_admit → lane_probe model_half_open → upstream_result 200` 全序列实证，
argon-high 经 admission 端到端恢复。**验证方法铁律：客户端路径验收只能走
8318（过 admission）**——8317 直连探针本次三次造成"已恢复"假阳性
（18:01 用户在窗内被拒 vs 直连 200 的矛盾即源于此）。

**P2（已被上游社区方案取代——自研路线判废，改为盯采纳）**：跨号
thoughtSignature 泄漏封口——`restoreGeminiResponsesTextSignatures`
（translator，授权无关阶段，调用点 request.go:85）按 model+messageID 回注缓存
签名；affinity 重绑（删号/停用/重启）后首个请求会把 A 号铸造的签名注入 B 号
请求（#7846：签名密文含 cloud_project_number）。
**2026-11-22 23:28Z 判废修正→同日 01:08Z 反转为采纳实施**：社区成员 photobrand 在
[#7846](https://github.com/router-for-me/CLIProxyAPI/issues/7846) 贴出 hermetic
`go test -race` 复现器 + 基于 `SelectedAuthMetadataKey`（conductor 已发布至
opts.Metadata）的 **21 行最小修复**——sessionKey 加 `auth:<hash8>:` 前缀使缓存
键天然按凭据隔离。**采纳实施（用户授权连续修复，d310a2c）**：overlay
`outputs/cpa-crossauth-replay-v8.1.1.tar.gz`（pristine 6eb8554f + 双 hunk +
helper，gofmt 净）→ 构建事务 `outputs/build-cpa-v811-crossauth-20261123.ps1`
（四 overlay 叠加+路径逃逸校验+7 效果 sha+`go test -race` 覆盖
ReasoningReplay/DailyCap 全过）→ binary `b4fedb2d` → 镜像
`cli-proxy-api-fork:v8.1.1-gemini-raw-daily-cap-authiso`（id `b40bc82f`，base
`e2f9c436`=v8.1.1 base）→ swap `outputs/cpa-v8111-authiso-swap-transaction-20261123.ps1`
一次通过（SWAP_OK，LANE_STATE_DELTA=none）。Commit 串
`6eb8554f+gemini-raw+agy-effort+daily-cap+crossauth-replay`。事务坑+1：派生
swap 脚本漏改 BASE_DIGEST/TAG_LABEL 字面断言（三处 dry-run 才凑齐）。

**【第三例 TOS 停用（2026-11-23 01:09:20Z，b7177976=sha8 b8be7576）】**：authiso
swap 后首个上游请求（我方 flash 探针）发现该号已被 Google 服务端停用（403
TOS_VIOLATION；3-token 探针不可能触发，探针=发现者非成因）——单账号非波次
（全日志唯一 suspended 行），**与 11-18 ditchgieck354、11-22 ae5e8007 同型**，
flash-only 档。CPA 自动挂起+全池 2h 暂停（TTL 03:09:20Z）+ admission hold
`account_restricted`——新 authiso 栈的 TOS 处理路径全链实战运转。处置：暂停
不动（保护性，runbook 铁律），03:11Z 延时任务放 hold+三模型过 admission 实测；
**b7177976 删号 or 申诉=用户裁决**（模型退役铁律：禁自动墓碑）。生产泄露面注
记：photobrand 修复覆盖 executor reasoning-replay 面；translator
trailing-signature 缓存（`gemini-responses-text:<msgID>` 键、TTL 1h）仍无凭据
维度=残余面，rebind 后 1h 内同一会话仍可能回注旧号签名——待上游跟进或下代
fork 补执行器侧过滤。

**v8.1.1 复核结论**：原 sessionKey 索引方案（穿 translator 写链）爆炸半径过
大；精化为**执行器侧签名→凭据索引**（零 translator/cache/selector 改动）：
①新文件 `internal/runtime/executor/antigravity_signature_auth_isolation.go`
——进程内 `sha256(sig)[:16] → {authID, seenAt}` 索引（TTL 1h、上限 2 万、带
锁）；②写点=上游 200 后从响应体递归收集 `thoughtSignature` 值记入索引（非流
=bodyBytes；流=需先核实 StreamErrorProbe 是否保留聚合 body——**唯一待 recon
项**，若无则流式退化为 strip-unknown 规则）；③剥离点=发送前对 translated
Gemini payload 的 `request.contents[].parts[].thoughtSignature` 逐项查索引：
已知且异号→**替换为 `internalsignature.GeminiSkipThoughtSignatureValidator`**
（仓内既有降级模式，保留 part 结构）；未知且非 functionCall part→同样替换
（覆盖重启后 KV 恢复的旧签名）；未知且 functionCall part→保留（客户端回显的
工具签名，剥离有 400 风险=协议固有面，非代理增量）；④挂钩=execute/stream/
tokens 四处 `sanitizeAntigravityGeminiRequestSignaturesDoc` 调用点旁（执行器
go:559 的 sanitize 行走模式可参照）。剥离缺失签名的降级=特性前形态，可接
受。**未决项仅一个**：流式 body 保留度 recon（30 分钟）；实施+构建+swap 预算
半天，建议挂 12-01 月维或下一空闲窗。
