# 2026-10-13 BWG admission 冷却与长流风控修复

## 范围和事实

用户授权连续修复、优化与验证。仅 BWG；无 OAuth 生成探针，无凭据轮换，
无并发或重试上调，无 CPA/Nginx/Cockpit/Desktop 重启。

部署前 fresh doctor 为 `DOCTOR_CONTRACT_OK`，但 24h journal 仍有 35 次 OAuth
容量事件（200:23、429:2、502:1、503:9），17 次 lane 拒绝/队列退出。
北京时间 10 月 13 日 03:30 仍有 Luna 容量事件，不能宣布上游症状彻底消失。
本机侧车日志最新记录约 78h 前，Direct API 当前指向公网 FQ，空日志不是恢复。

## 修复

- `LaneState.acquire`：模型显式 Retry-After 优先于 lane early/half-open probe。
  旧版离线复现：Luna 剩余 3589 秒退避仍被 `early_probe` 放行；新版拒绝。
- SSE 逐事件解析代替整流前 256 KiB 截断；单事件内存有界，超大事件不能证明
  成功恢复。保持原样转发，支持跨 chunk、多行 data、固定长度 SSE；正文讨论
  rate limit 不触发熔断。认证/model_not_found 负分类不被 bare 503 覆盖。
- doctor 不再输出 management.secret-key 摘要；不修改真实密钥。
- triage 空观测明确 `current health is unverified`，不再暗示当前没有故障。

## 验证

- 主修复完整门禁：563 passed、1 skipped（真实 SSH integration）、269 subtests；
  compileall、bandit、ruff check/format、mypy 均通过，exit 0。
- 独立只读复核发现固定长度 SSE 与负分类边界；最终工作区已修复并 fresh-read
  确认，未发现新增可执行回归。新增 10 项定向用例通过。
- 最终 admission/guardrail 回归：107 passed、10 subtests，exit 0；受影响 ruff、
  mypy、bandit 和 diff check 通过。不是以早期完整门禁冒充最终全套重跑。
- 本地和远端隔离 net namespace 假上游回放：MODEL_RETRY_AFTER=PASS、
  LATE_STREAM_MODEL_BREAKER=PASS、CONTROLLED_REPLAY=PASS、REAL_PROVIDER_CALLS=0。
  两次超过 256 KiB 的 Luna 错误流后，第三次 Luna 被拦截，Sol 放行；假上游共 3 调用。

## 远端事务

提交 `4e83cb6` 后，复用已审阅代际事务，仅原子替换 admission 源和 pin。
维护锁、旧源/配置/策略哈希、stage 编译校验、lane 无流量/无活动冷却、8318 无
已建立连接检查均通过。`rollback.sh --dry-run` 通过后才替换和重启 admission。

- 旧源 SHA256：`5273f59e5b07cdaa13df1c66cb95bdb212905d94df9c906e3e813bee5b0218e0`。
- 新源 SHA256：`fbaeb6314c083c410059aa7c3cf84c5046234666a4fe8e00b7613ffcb552748c`。
- 备份：`/root/cpa-admission-stream-risk-backup-20261013T000827Z`。
- 回滚入口：该目录 `rollback.sh`；先 `--dry-run`，正式回滚需空闲窗口，随后
  同步仓库旧源/pin 并重新 doctor，不能只回滚 Git。
- admission 于 2026-10-13 08:09:29 Asia/Shanghai 启动，PID 757500。
- CPA 仍是 v8.0.27，启动时间保持 2026-10-12 23:29:13 UTC，未重启。
- 配置/策略 SHA256 保持部署前值；stage 已清理，未使用普通 -Apply 跨代旋转 pin。
- 08:10 后 doctor：全部投影 MATCH、ADMISSION_INTEGRITY_OK、POLICY_OK、
  DOCTOR_CONTRACT_OK；输出已无 management.secret-key。
- 部署后自然 journal 样本：gpt-6-astra 7 次 200/capacity=false，无拒绝/传输错误；
  这不是 Luna/Sol OAuth 验收。

## 剩余边界

`repo_verified`、`filesystem_projected`、`host_loaded`、隔离受控回放已完成。
`natural_live_accepted`（OAuth 高峰期/用户 Desktop 会话）未宣称。

OAuth 当前未过期，剩余约 47.4h，进入 WARN_RENEWAL_WINDOW；24h 提前刷新策略仍在，
保留日志中 7d refresh failures=0，仅为有界样本，不自动重登/轮换。
model substitution warning=0 也不是模型质量/无降智证明。
固定长度 SSE 仍保留原来的非 heartbeat 读取生命周期，断开检测最坏受上游读超时
约束；本次不扩展为无证据的传输生命周期重写。

官方 API rate-limit 文档说明失败请求也计入限额，嵌套重试会放大请求，需尊重有效
Retry-After：https://developers.openai.com/api/docs/guides/rate-limits 。此 API 指南
不能推定 OAuth 的具体额度。CPA v8.0.27 selector.go 只读核实其自身也会生成
model_cooldown / Retry-After，故该头不必然来自官方原始响应。
