# 2026-11-23 bwg：Antigravity 第三例 TOS 善后与 sig-iso 执行器侧签名凭据隔离部署

**性质**：真实远端写入 + 事故证据留档（redaction-first；账号一律 sha8 代号）。
上游事件与第三例 TOS 命中（`b7177976` = sha8 `b8be7576`，403 TOS_VIOLATION，
2026-11-23 01:09:20Z）见
[20261122 台账](20261122-bwg-antigravity-tos-second-hit-and-repeat-reject-guard.md)；
本台账覆盖：用户删除该凭据、防封研究裁决、sig-iso fork 换代部署与验收。

## 善后与池状态

- 用户经管理面板手动删除 `b7177976`（未走申诉）。删除后池探针：antigravity
  6 号全部 `disabled=false`，lane `OPEN` 无 hold/冷却，`tos-pause-window=2h`、
  `per-credential-daily-cap=150`、`sensitive-words=[gpt,openai,codex]` 全部在位。
- doctor `credential_ban_signals_7d=0`、`oauth_antigravity_refresh_failures_7d=0`，
  6 号凭据 token 刷新正常（refresh_age≈0.2h）。

## 防封研究裁决（同日完成；出处与证据强度见研究记录）

社区/上游一手证据（CLIProxyAPI #7768/#7846/#7562/#7636/#7552/#7668/#7614/#7701/#7296、
antigravity.google/terms、Gemini Prohibited-Use Policy）归纳的封号信号面：
agent 身份句裸 429（多人 A/B 复现）、裸 429=滥用早期信号（两份独立实测 +
撤回自证）、42.7% 封号无 429 前兆（内容直判路径）、同标记 payload 轮换横扫
加速连坐、单出口凭据集中度（28–29/IP 全灭 vs 1–2/IP 存活 69.6%，n=86 单源）、
跨账号 thoughtSignature 重放（#7846，Tink 密文含 cloud_project_number）。
**结构性底座不变**：第三方 OAuth 接入本身即违约（条款原文），技术缓解只降
检出概率；申诉表单 `forms.gle/hGzM9MEUv2azZsrb9`，复犯即永久。

裁决（agent 依 runbook 边界自动执行）：

| 项 | 裁决 | 依据 |
| --- | --- | --- |
| 执行器侧签名凭据隔离（sig-iso） | **采纳并部署**（见下） | #7846 一手机制证据；11-22 台账挂账的 P2 残余面 |
| 扩 sensitive-words/patterns 重写 | 拒绝 | runbook 2026-10-22 边界"不扩展混淆" |
| `request-scoped-errors` 裸 429 `action: stop` | 不加 | `max-retry-credentials=1` + repeat-reject guard 已实现同语义；叠加规则可能与 fork bare-429 语义互扰（键在 v8.1.1 存在，留待实测需要时再评） |
| 上游 dev `b8e3bc6` 序列化对齐 | 只跟踪 | 未发布、无公开 A/B；2003 行改动不建议直追 dev |
| #7701 prompt-guard 插件 | 拒绝 | 未合并第三方件，供应链面 |
| 换出口 / IP 分散 / 新号轮换 | 拒绝 | 置顶纪律（主动换出口本身是被识别的滥用形态） |
| CountTokens in-band 200 错误体吞错（#7920） | 记录待复核 | 诊断面非封号成因；base 6eb8554f 无 in-band 检查 |

## sig-iso 设计与实现（fork overlay 第五叠层）

- **收集**（3 个响应面，上游 200 后）：非流式 `rawResponseBody`、claude 非流式
  SSE 循环、流式逐事件（紧邻 `replayAccumulator.ObserveSSELine`）——把响应中
  全部 `thoughtSignature` 按**首发凭据**入索引：进程内
  `sha256(sig)[:16] → {authID, seenAt}`，TTL 1h、上限 2 万、带锁、首源优先
  不覆盖、哨兵值不入索引。recon 结论：流式面有逐事件聚合点，无需等聚合 body。
- **剥离**（4 个挂钩点 = 既有 `sanitizeAntigravityGeminiRequestSignaturesDoc`
  调用点旁，execute ×2 / stream / countTokens，**无条件运行**——上游 sanitize
  被 `antigravityUsesReasoningReplayCache` 门控，非 replay 模型原本无任何签名
  消毒）：已知且异凭据 → 替换为
  `internalsignature.GeminiSkipThoughtSignatureValidator`
  （= `skip_thought_signature_validator`，gemini_validation.go:84，保留 part
  结构）；未知且非 functionCall part → 同样替换（覆盖重启后 KV/缓存恢复的旧
  签名）；未知且 functionCall part → 保留（客户端回显工具签名，剥离有 400
  风险 = 协议固有残余面）；已知同凭据 / 哨兵值 → 不动。兼容
  `request.contents` 与顶层 `contents`（countTokens）两种包络。
- **语义边界**：哨兵替换的降级 = 特性前形态（只损 thinking 连续性，不致
  400）；重启清索引（进程态）；replay 账本恢复面已由 crossauth
  （sessionKey `auth:<hash8>:` 前缀）按凭据隔离，本层不重复覆盖；
  **translator trailing-signature 缓存（model+msgID 键、无凭据维度）与客户端
  会话历史回带两个 rebind 面由本层封口**——即 11-22 台账"升级后体检"节的
  残余面与 P2 待办在本代闭环。debug 行
  `isolated %d request thought signature(s)`。
- 测试（`-race` 全绿，`ok ... 1.951s`）：同凭据保留/异凭据隔离、未知
  functionCall 保留与非 functionCall 替换、哨兵透传、TTL 过期、嵌套响应收集、
  顶层 contents 包络、2 万条上限逐出。

## 构建与部署证据（2026-11-23 UTC）

- **预检**（只读探针）：runtime binary `b4fedb2d`（= 账面）、image label
  base `e2f9c436` / tag v8.1.1 全一致；四代 overlay tarball sha 与
  /root/cpa-v8111-build/ 上代构建残留逐字节一致（gemini-raw `9d929922`、
  agy `27fdb3a4`、guard `ab33343f`、crossauth `afb028ad`）——**无基线回退**。
- **构建**（`outputs/build-cpa-v811-sigiso-20261123.ps1`，DryRun + apply）：
  五 overlay 叠加（gemini-raw → agy-effort → guard → crossauth → sigiso，
  sigiso = 新 `antigravity_signature_auth_isolation.go(+_test)` + exec/stream
  整文件挂钩版 + tokens 整文件挂钩版）；路径逃逸校验、8 文件效果 sha、
  `go vet` + `go test -race` 全绿 → binary
  `8b593f384b5b7a245932ac3a06d439fbbdfa7a64a5651b600dc9c3f164c5970c`
  （Commit 串 `6eb8554f+gemini-raw+agy-effort+daily-cap+crossauth-replay+sig-iso`）。
- **镜像**（`outputs/build-fork-image-sigiso-20261123.ps1`，新 tag 不覆盖旧
  标签）：`cli-proxy-api-fork:v8.1.1-gemini-raw-daily-cap-authiso-sigiso`
  id `sha256:2fe58d9f…`，label base/fork-binary/upstream-tag 全验证。
- **swap**（`outputs/cpa-v8111-sigiso-swap-transaction-20261123.ps1`，DryRun +
  apply 一次通过）：drain 锁 + 备份
  `/opt/cliproxyapi/backups/20261123T050405.819357795Z-swap-v8111-to-sigiso/`
  （compose/config/双插件/auth 清单+disabled 映射 pre/post）→
  `IMAGE_ID_READBACK_OK` → `START_LOG_CLEAN`（version v8.1.1，workbuddy
  0.21.30，intl closed_dark）→ `RUNTIME_BINARY_OK 8b593f38` → readiness OK →
  `AUTH_BYTES_UNCHANGED`（token 刷新 safe，disabled 集不变）→
  `LANE_STATE_DELTA=none` → `SWAP_OK`。
- **验收（8318 铁律，过 admission）**：`gemini-3.8-flash-high` 200
  （served `gemini-3.8-flash-n`，已知复核对）2.8s 非空；`gemini-4-neon-xhigh`
  200（served `gemini-4-neon`）3.5s 非空；探后全 lane 归位 idle。启动行
  Commit 串含 `+sig-iso`（BuiltAt 04:59:28Z）。
- **账面**：`scripts/remote/fork-binary.sha256` → `8b593f38`（本提交）。
  doctor 的 runtime-binary 比较源是 **git HEAD blob**
  （`Get-HeadBlobText`，"single committed source" 契约），故该 lane 在本提交
  落 HEAD 前保持 FAIL=预期姿势，提交后转 OK。

## doctor 姿态与本窗口已知的两项预存投影债

- 本窗口 doctor：container/auth-permissions/policy/admission-integrity/
  nginx-guardrails 全绿，`antigravity-lane=OK`（7 allowed + haiku excluded），
  六凭据 METADATA_OK，`credential_ban_signals_7d=0`。
- **预存 FAIL（非本切片造成，归部署车道）**：`auto-update.sh` 与
  `cpa_provider_routes.json` 远端投影落后仓库 HEAD——远端 mtime
  2026-11-22 14:42:30Z（上次投影事件），而两文件的 HEAD 内容分别来自
  `9e6d53c`（11-23 04:45Z，updater fork-tag 解析）与 `4cdd915`（11-23 03:51Z，
  neon context pin）——即并行会话已提交未投影的增量。处置=由部署车道按
  受审查 apply 重投影，本切片不越权代投（避免与部署窗口竞态）。
- 观察项（ anomalous，不复现）：两次 doctor 启动在 lib 的 ssh_tool 路径检查
  处报出 `EntryPointPath=pwsh` 瞬态异常，同命令即时重跑即过；时间窗与本机
  并行会话的树操作窗口重叠，未定位到稳定成因，留观察。

## 回滚

- 镜像级：compose image 改回
  `cli-proxy-api-fork:v8.1.1-gemini-raw-daily-cap-authiso`
  （id `sha256:b40bc82f…`，binary `b4fedb2d`）+ force-recreate（备份目录含
  原 compose）。二进制级回滚 = 用备份目录内记录值重放本 swap 事务（参数对
  调）。fork 代码级回滚 = 重建时去掉 sigiso 叠层（事务脚本 fifth-overlay
  段独立可删）。
- 行为级：sig-iso 为纯请求侧消毒，无配置旋钮；异常时最先看
  `isolated %d request thought signature(s)` debug 行与上游 4xx 形状是否
  回归特性前形态。

## 投影闭环与受控实战验收（同日晚间补充，用户追问"投影/受控实战"后授权执行）

### 投影（guardrails -Apply 全覆盖）

- 首跑 `REFUSE env BASE_URL_6 must match the approved slot 6 route`，fail-closed
  回滚干净（ROLLBACK_VERIFIED）：本地 `providers.env`（mtime 11-19）缺 slot 6
  两行——zcode-start-plan 是 11-21 `91983f3` 并仓的新槽，本地 env 落后于投影契约。
  **修复=从生产 config 读取 slot 6 权威 key 经 base64 捕获回写本地 env，全程不落
  transcript**（`outputs/sync-slot6-env-20261123.ps1`；备份
  `providers.env.bak-sigiso-slot6-20261123`；生产条目字段名是 `keys` 非 `api-keys`）。
- 复跑两次 `GUARDRAILS_APPLIED`（第一次后台跑完但输出缓冲到退出才落盘，曾误判
  "静默死"；第二次前台超时转后台跑完），远端净效果：`auto-update.sh`=ff2cf5b9、
  `cpa_provider_routes.json`=55c05f4d、`cpa_policy.py`/`cpa-admission.py`
  全部 MATCH，**两项投影债清零**；config.yaml 重写为 7075297b，antigravity 节
  `per-credential-daily-cap: 150`、`sensitive-words: [gpt,openai,codex]` +
  `sensitive-words-replace` 映射、`tos-pause-window: 2h` 全部健在（文件为权威源）。
  `payload.replace` 旧规则随本次 -Apply 清除（并行会话计划内）。
- **运维坑位两条**：①后台（重定向+无窗）pwsh 跑 guardrails 两度在 ~52-70s 静默
  退出，形状与 SSH "Authentication timeout" 吻合（前台同命令稳定）；后续 guardrails
  长事务一律前台跑。②`/v0/management/config` 读视图在 -Apply 后对
  `per-credential-daily-cap`/`sensitive-words`/`signature-cache-enabled` 三键返回
  absent（文件与 policy 语义校验均确认在位）——管理读视图序列化怪癖，观察项，
  判读一律以文件 + cpa_policy 为准。
- **updater 心跳清零**：doctor 曾报 `heartbeat=cpa-update result=failed
  exit_code=1 age_hours=5`——04:02Z 旧版 updater 在 fork 标签上解析失败（`9e6d53c`
  的修复动因），失败先于本切片。投影后手动触发一次
  `cliproxyapi-update.service`：新脚本跑通（disk hygiene + HEALTH_OK +
  "no newer mature release; current=v8.1.1 readiness verified"，容器未动），
  心跳转 `result=success exit_code=0`。
- **strict doctor 终态：`DOCTOR_CONTRACT_OK`**（runtime-binary MATCH、全部心跳
  success、投影零漂移、`semantic-policy=OK`、`antigravity_lane=OK`、
  `credential_ban_signals_7d=0`）。

### sig-iso 受控实战验收（8318 客户端路径，Gemini 原生格式）

| 探针 | 载荷 | 结果 |
| --- | --- | --- |
| A（控制组） | 纯文本 generateContent | 200 / 2.8s / 非空 |
| B（实弹组） | 同载荷 + model 轮 unknown `thoughtSignature`（非 functionCall part） | 200 / 4.9s / 非空 |

- `signature-cache-enabled` 走默认 true（管理视图 absent=默认值生效），前置签名
  校验跳过，bogus 签名可达消毒层；Gemini→antigravity 对称路径不进 replay 账本，
  消毒层为该签名的唯一处置点。
- 部署以来全量日志签名族上游错误
  （`thinking_signature_invalid|thought_signature_invalid` 家族）= **0**；探后
  六 lane 全部归位 idle。
- **断言强度如实声明**：`isolated %d request thought signature(s)` debug 行在
  info 日志级不可见，B 的 200 与"哨兵替换已发生"是强一致而非直证（直证=单测 +
  二进制 sha 链）；已知异凭据分支（rebind E2E）由 `-race` 单测覆盖，生产多凭据
  换乘演练（cap=1 / 管理 API 停用换乘）留月维窗口可选执行——日间执行会镜像
  ≤600s flash 模型窗，不值得为边际保障打断在产流量。
- 挂钩点在真实生产路径的活性由三重证据支撑：7 处挂钩源码核验 → 容器 binary
  sha=8b593f38（Commit 串含 `+sig-iso`）→ 全量真实流量（含当日 workbuddy/
  chatgpt/antigravity 在产请求）零签名族错误。

## 后续

- 11-22 台账"12-01 窗口"待办中的 translator 残余面已由本代执行器侧过滤
  封口，该窗口只剩上游跟进观察。
- `request-scoped-errors` 裸 429 stop 语义如未来实测显示 repeat-reject
  阈值不足，再按 reviewed deploy 评估。
- #7920 CountTokens in-band 错误体吞错：挂上游跟进，出现真实诊断损失再开案。
- 可选（月维窗口）：sig-iso 多凭据换乘 E2E 演练（cap=1 或管理 API 停用换乘），
  覆盖"已知异凭据→替换"分支的生产直证。
- 管理读视图对 fork 旋钮键的序列化怪癖：下次上游/fork 换代时复核是否为
  v8 schema 迁移行为，届时决定是否向 fork 报 issue。
