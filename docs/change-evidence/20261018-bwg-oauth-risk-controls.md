# 2026-10-18 BWG OAuth 风控修复部署

## 授权与范围

- 用户选择保留第三方客户端兼容性、修复 fallback 与监控，并明确批准当前维护窗口。
- 代码提交 `e83e982`；定向事务提交 `1ac5db3`，入口为
  `outputs/deploy-admission-oauth-risk-20261018.ps1 -Profile bwg`。
- BWG only，strict SSH host keys；未访问 ZZ，未改 OAuth 凭据、公网 capability path、
  Nginx/Fail2ban 或 admission 冷却状态文件。完整性 pin 是代码哈希，不是账号凭据。

## 部署经过与保护

1. 首次 `scripts/cpa_bwg_guardrails.ps1 -Apply` 返回 exit 1：
   `REFUSE admission_integrity_pin_mismatch`。该入口只允许当前 admission 版本重投影；
   拒绝发生在备份与服务变更之前，未解除该保护或重复执行 bulk apply。
2. 复用既有 modelstreak 定向事务结构，固定旧 admission/pin/policy/admission JSON 与
   config.yaml 哈希，先暂存、校验、验证候选 policy，再备份与预演 rollback。
3. 独立复核指出旧模板回滚会因单项写入失败而短路；本次两条 restore 路径已逐项保护，
   累计失败并比较恢复文件，每项服务重启分别尝试。故障注入第一项 cp 失败后，确认
   后续 pin/policy/config 仍恢复、两个 restart 均尝试，最终报告 `ROLLBACK_FAILED`，
   演练判据 `ROLLBACK_FAILURE_INJECTION=PASS`。
4. PowerShell/Bash/内嵌 Python 语法检查、候选配置 fixture 与最小变更范围演练通过。
   候选仅增加三个 Claude OAuth fallback filter；已有 payload 规则和 requests 其他项保留。
5. 执行时四条 lane 无 inflight/pending；输出 `CANDIDATE_POLICY=OK`、
   `ROLLBACK_DRY_RUN_OK`、`ADMISSION_INTEGRITY_OK`、`SEMANTIC_POLICY=OK`、
   `CPA_READINESS=OK`、`GENERATION_FIELDS=ok`、`DEPLOY_OK`、`DRIVER_RESULT=PASS`，exit 0。
   CPA 与 admission 各执行一次重启；本次定向事务未裁剪任何历史备份。

## 备份与回滚

- 备份：`/root/cpa-admission-oauth-risk-backup-20261017T162639Z`
  （路径时间为 UTC，对应北京时间 2026-10-18）。
- 包含旧 admission、pin、policy、admission JSON、config.yaml、backup.sha256 与 rollback.sh。
- 事后再次执行 `bash <backup>/rollback.sh --dry-run`，exit 0、`ROLLBACK_DRY_RUN_OK`。
  实际回滚会恢复 admission/pin/policy/config 并重启 CPA 与 admission，须在维护窗口运行。
- config.yaml 为保持现有文件 inode 使用原位写入；掉电或 SIGKILL 不在 EXIT trap 保证范围，
  此时仍需从备份人工恢复。未把可捕获失败回滚等同于崩溃原子性。
- wrapper hard timeout 只限制本机等待，不证明远端已停止。若超时，先只读检查维护锁、
  服务与文件哈希，不重跑未知状态的事务。

## 远端读回与运行状态

- 新 admission / pin：`e2735c1d8b0e9ab3e1bfe1f7647d32bcd1a0ba604bd3286fb0f9661cf9cb3549`。
- 新 policy：`c312cca5af35f3e1a261ec19be772d429dd47727295b2d3b2ea86586fd6f7513`。
- 新 config.yaml：`e7c7f16889cc96c02b33e00c5bdea77e2d1a66b6023596e2ade13812651f4c50`。
- admission PID `1063617 -> 1071742`；service active。CPA running，启动时间
  `2026-10-17T16:28:09.953506366Z`（北京时间 2026-10-18 00:28:09）。
  镜像仍为 v8.0.31 固定 digest，未升级镜像。
- 直接读回 `CLAUDE_FALLBACK_FILTERS=PASS count=3 protocol=claude`，覆盖
  opus/sonnet/haiku-5-5；`stabilize-device-profile=true` 保留，未新增禁用 cloak 配置。
- strict doctor 于北京时间 2026-10-18 00:28:59 开始，exit 0、`DOCTOR_CONTRACT_OK`。
  semantic-policy、admission-health、integrity、端口与公网路径契约通过；全部投影 MATCH。
- 模型目录仍为 14 名，unknown=none；Claude 三名、Antigravity 一名均开放。
- 新 monitor 读到 Claude/Antigravity 各一份凭据、expiry 已知，均输出
  `METADATA_OK_NOT_PROVIDER_ACCEPTANCE`。Antigravity refresh age=unknown；刷新失败日志
  未按 provider 归属，输出 `not_provider_attributed`，不虚报完整监控覆盖。

## 验证与验收层级

- 复用代码切片完整门禁：644 passed、1 skipped、309 subtests；Bandit/Ruff/mypy 通过。
  后续 apply/doctor 回归：35 passed、72 subtests；追加目标类型检查通过。
- 定向事务经过独立复核、语法检查、候选 fixture、变更范围及回滚故障注入。
- 已验证 `repo_verified -> filesystem_projected -> host_loaded`。未主动发送生成请求，
  未完成此次变更的 controlled_live_replay 或 natural_live_accepted。
- 禁止 fallback 字段只控制该请求机制；不证明上游权重、内部调度、质量、资格或账号安全。
  监控与退避不能消除第三方 OAuth 的条款风险，不提供免封/免限流保证。
- 证据不含凭据内容、账号标识、OAuth 文件名、完整 capability path 或登录 URL。
