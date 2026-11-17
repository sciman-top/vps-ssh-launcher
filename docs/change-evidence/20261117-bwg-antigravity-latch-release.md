# 2026-11-17 BWG antigravity-oauth 车道 operator-review latch 释放

## 授权与边界

- 用户出示 ChatGPT desktop 截图：三模型（sonnet-5-5 / hydrogen-xhigh /
  flash-high）全部 `stream disconnected ... Shared upstream lane
  antigravity-oauth requires operator review`。AskUserQuestion 授权"先验证
  后释放（推荐）"后用户改选"暂时无法认证，先释放，让 3.8 Flash 先可用"。
- 仅触碰 `/var/lib/cpa-admission/antigravity-risk.json` 的 `hold_reason`
  字段与 admission 一次受控重启；不改凭据、不改 config.yaml/compose、不动
  `not_before`、不动 TOS 池停配置。

## 信号与根因（先只读探针，再处置）

- 客户端面：三模型全 403 `operator_review_required`（本地 lane 拒绝，
  `retry_after=300`）。
- 落锁点：Nov 17 00:51:44 UTC，desktop 当日首个 antigravity 请求
  `claude-sonnet-5-5-high`（affinity 新绑定）路由至唯一可供该模型的凭据
  mamo***@gmail.com，上游回 `403 PERMISSION_DENIED` reason=
  **`VALIDATION_REQUIRED`**（"Verify your account to continue."，domain=
  cloudcode-pa.googleapis.com，带 `validation_url`，CPA logger 截断全串）。
  admission `lane_hold_latched reason=authentication_required
  source=response_signal`，整道持久 hold。
- **非 TOS**：容器日志（v8.0.75）`TOS_VIOLATION`=0；VALIDATION_REQUIRED 是
  账号验证挑战非 TOS 撤销；仅 mamo 一个 auth_file 中招，sc\*\*\*×2 与
  agi.phys 凭据刷新正常。TOS 池停 knob（`pre-tos-pause` 快照 01:00 UTC）晚
  于落锁 8 分钟且只写 TOS 路径，与本事件无关。
- 状态读数：`antigravity-risk.json` = `{"hold_reason":
  "authentication_required", "not_before": 0, "version": 1}`（mtime
  00:51 UTC）；healthz 该道 `inflight=0 pending=0`。
- flash-high 被锁打回属 lane 级锁的连带（池内另 3 凭据健康可供）。

## 处置（单事务，10-31 先例流程）

脚本 `outputs/run-antigravity-latch-release-20261117.ps1`：

1. 全道 idle 确认（5 道 inflight/pending 全 0）。
2. 备份 `/root/antigravity-risk-backup-20261117T011123Z.json`（73B，
   sha256_16=20c0e39e6fbb1d65，owner 65534:65534 mode 0600，逐字节一致）。
3. `systemctl stop cpa-admission` → python 断言 `version=1 not_before=0
   hold_reason=authentication_required` 后置 `hold_reason=null`（保
   owner/mode，tmp+replace）→ `systemctl start cpa-admission`；断言失败则
   自动还原备份并重启。
4. phase-4 即时 healthz 探针在 admission 完成监听前打到空响应（traceback
   属时序噪声）；权威复核为 phase-5：90s stale write-ahead 窗过后
   `STABLE_ALL_NULL`，无任何车道被重启竞态落锁。

## 验收

- healthz：`antigravity-oauth hold=None strict_backoff=true
  failure_generation=0`，五道 hold 全 null。
- 8318 回环**仅 flash-high 单发**：HTTP 200 / SSE terminal / TEXT='PONG'，
  journal `upstream_result status=200 successful=true`。
- hydrogen/sonnet **刻意不探**：mamo 挑战未解除前任一上游调用 403 即整道
  再落锁。

## 边界与回滚

- mamo 验证挑战未解除：hydrogen/sonnet 持续不可用（单点承重），客户端须钉
  flash-high，勿让 desktop 自动重试 hydrogen/sonnet，否则整道再落锁
  （flash 陪葬）。
- 账号侧待办（仅用户可做）：以 mamo\*\*\* 身份完成 Google 验证（本地
  Antigravity IDE 切该账号即弹验证入口）；若流程出现 account disabled /
  TOS 字样停手上报。
- 回滚（需重新置锁时）：恢复备份文件（保 owner/mode）+ 重启 admission。
- affinity 的 sonnet→mamo 绑定仍在（无害，验证完成后自然复用）。
