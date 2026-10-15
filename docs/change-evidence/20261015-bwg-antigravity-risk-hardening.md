# 2026-10-15 BWG Antigravity OAuth 退避与风险停流部署证据

## 授权、目标与范围

- 用户明确要求保留现有 OAuth 通道，连续执行已建议的修复和优化。
- 源码基线 `26c17b4`；实现提交 `2cbebaa17e450a55505dec2f1b54792abe9b0791`。
- 只处理 `bwg`。保留 OAuth 凭据、模型 `gemini-3.8-flash-high`、并发 2、
  pending 4、queue 120 秒。未修改 CPA 容器、Nginx 配置或账号排除表。
- 远端只替换 admission Python、systemd unit、策略校验脚本（仅注释变化）和完整性 pin；
  首次初始化不含凭据的风险状态文件。已有状态不重置。

## 修复与依据

- Antigravity 等完整个退避窗口，不执行 10 秒提前半开；超过一天的上游明确等待不截短。
- HTTP `Retry-After` 与 Google `RetryInfo.retryDelay`、`ErrorInfo.metadata.quotaResetDelay`
  取最长等待；覆盖 HTTP 错误体和 SSE。认证/权限状态或明确停用信号进入人工复核停流。
- HTTP 认证/权限响应头与 SSE 风险事件即时锁存，不等待 EOF；其它在途成功不清人工 hold。
- 每次上游调用前预写 `unverified_request`，全部在途调用结束后保存最终状态；
  崩溃或请求后保存失败时重启要求人工复核。首次预写失败则拒绝上游调用。
- mode 0700 状态目录、mode 0600 文件、owner nobody，原子替换；缺失/无效状态拒绝启动。
- Responses、Chat Completions、Messages、legacy Completions、Gemini 原生入口共享预算。
- 不增加主动 OAuth 巡检；保留日常质量矩阵排除 OAuth 的已有默认值。
- `sensitive-words` 保留原兼容值，仅纠正注释；混淆降低封号风险无已验证证据。

官方依据：[Antigravity 条款](https://antigravity.google/terms)、
[FAQ](https://antigravity.google/docs/faq/)、[计划](https://antigravity.google/docs/plans/)、
[Gemini API 限流](https://ai.google.dev/gemini-api/docs/rate-limits)、
[错误排查](https://ai.google.dev/gemini-api/docs/troubleshooting)。不同产品的配额不能混用。
社区问题 [CPA #1803](https://github.com/router-for-me/CLIProxyAPI/issues/1803)、
[#1814](https://github.com/router-for-me/CLIProxyAPI/issues/1814)、
[#5848](https://github.com/router-for-me/CLIProxyAPI/issues/5848) 仅作症状样本，不推断检测机制。
已登记外置参考 `D:/CODE/external/CLIProxyAPI` 仅只读：v8.0.28，
revision `0d18d3b365be000eeaee7de4615f9da596b2059d`，MIT；查阅 Google 等待解析及 API 路由。
未在外置根执行构建、测试、更新或写入。

## 本地验证与独立复核

- `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1`：exit 0。
- `592 passed, 1 skipped, 286 subtests passed`；跳过的是默认 opt-in 的真实 SSH integration test。
- compileall、Bandit（runtime/scripts）、Ruff check/format、mypy 全部通过；`git diff --check` 通过。
- 风险用例覆盖完整等待、跨重启、坏状态、写盘失败、多个协议、SSE 未结束及客户端断开后停流。
- 独立只读复核指出的 SSE 延迟锁存、请求后写盘失败重启、部署/手工回滚竞态均已修复；
  最终指定事务项复核无阻断。附带清理边界也已修正：iptables 查询错误不再当作规则不存在。
- 部署 PowerShell AST 与 Bash `-n` 通过；回滚在真实写入前执行 `--check` 预演通过。

## 远端交易与加载

- 2026-10-15 UTC 受控事务，经共享 SSH 入口、maintenance flock 和远端 wall-clock timeout 执行。
- fresh 核实 Nginx worker UID 唯一、direct admission proxy 两处、无 admission upstream keepalive pool。
- 临时 iptables 规则只拒绝 Nginx 到 loopback admission 的 NEW 连接；既有连接先排空。
  所有 lane inflight/pending、冷却和模型冷却为零，且排除 TIME-WAIT 后的目标 socket 清零才停服。
- 回执：`STAGE_HASHES_AND_COMPILE=PASS`、
  `ALL_LANES_IDLE_NO_ACTIVE_BACKOFF_AND_ZERO_UPSTREAM_SOCKETS=PASS`、
  `ROLLBACK_PREFLIGHT=PASS`、`ADMISSION_INTEGRITY_OK`、`POLICY_OK`、
  `DEPLOYED_HASHES_AND_HEALTH=PASS`、`DEPLOYMENT_RESULT=PASS`、`DRAIN_CLEANUP=PASS`；exit 0。
- 新服务 `strict_backoff=true`、`state_persisted=true`、`state_write_failed=false`。
- 随后 strict doctor：`DOCTOR_CONTRACT_OK`，exit 0；12 项投影 `MATCH`；
  `admission-service=enabled-active`、`antigravity-risk-state-permissions=OK`、
  `antigravity_lane=OK`、TLS/管理边界/Nginx 契约通过。CPA 仍为 v8.0.29，restart=0。

远端逐文件 SHA-256 回读一致（LF）：

| 文件 | SHA-256 |
| --- | --- |
| `/opt/cliproxyapi/cpa-admission.py` | `21ee9804803cc35170404a71325213477f999e28cb02b78deb11dfeafe93607e` |
| `/etc/systemd/system/cpa-admission.service` | `886f82046b135dc0cad752424602ac99e9c808f93936d0f747762c43f46577e1` |
| `/opt/cliproxyapi/cpa_policy.py` | `ad63938a4df560ada09b1c9eab6e4db7645ce48dc1477758e2fb3276f7ad5b50` |
| `/etc/vps-ssh-launcher/cpa-admission.sha256` | `62db3eec4badf175c9810c2d3af1ed3ee98239f7866a9b725e09149e69032334` |

## 一次受控真实调用

- 经 `127.0.0.1:8318/v1/chat/completions` admission，请求精确模型 `gemini-3.8-flash-high`，
  prompt `Reply with exactly OK.`，非流式，max_tokens 1024；只调用一次，无重试。
- `TEMPORARY_DRAIN_RULES=ABSENT`；HTTP 200，2.04 秒，精确返回 `OK`，finish_reason=`stop`。
- returned_model=`gemini-3.8-flash-n`；usage prompt=6 / completion=63 / total=69。
- 调用后 inflight=0、hold_reason=null、cooldown_remaining=0、state_write_failed=false；
  磁盘状态 `{version:1, not_before:0, hold_reason:null}`。`SINGLE_GENERATION_RESULT=PASS`，exit 0。
- key 只在远端内存读取并用于请求头；证据不包含账号、凭据或业务正文，仅记录固定探针摘要。

## 备份与回滚

- 精确旧文件、脱敏 manifest、可执行回滚脚本保留在
  `/root/cpa-antigravity-risk-backup-20261015T035726Z`。未触发回滚。
- 预演：`bash /root/cpa-antigravity-risk-backup-20261015T035726Z/rollback.sh --check`。
- 实际回滚：`timeout --signal=TERM --kill-after=20s 180s bash /root/cpa-antigravity-risk-backup-20261015T035726Z/rollback.sh`。
  需获维护授权并先停止调用方；脚本自建连接 fence、排空并再次核对风险状态。
  有 active risk/hold 时拒绝回到不识别该状态的旧版，不删除或重置风险文件。
- 回滚只恢复上述四个旧文件，不恢复整张 iptables 表；失败须 fresh read 服务和精确临时规则。
  回滚后新源码 doctor 的哈希预期不再适用，须按恢复的版本核验，再决定源码同步。

## 结论与未证明边界

- `repo_verified`、`filesystem_projected`、`host_loaded` 已有本轮证据；
  受控真实调用通过，OAuth 通道保留且当前可用。
- 本轮不以 HTTP 200 或固定 IP 推断账号安全。第三方 OAuth 的官方条款风险仍存在，
  付费或本次退避修复不提供免责或不封号保证。
- doctor 观察到保留日志 7 天模型替换告警 28 次（已有状态，非严格门禁）；
  `flash-high` 返回 `flash-n` 也不能单独证明降智。未做高频 OAuth 质量矩阵，
  未证明长期质量、真实配额恢复或桌面自然使用的 `live_accepted`。
