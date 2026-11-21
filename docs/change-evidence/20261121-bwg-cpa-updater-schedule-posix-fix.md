# 2026-11-21 BWG CPA updater-schedule POSIX 兼容修复

## 目标与范围

- 目标：消除 strict doctor 中 `updater-schedule=UNVERIFIED reason=readout_failed`
  的可观测盲区，使 schedule、预算、timeout、compose 与容器状态真正读回。
- 仅变更 `scripts/remote/cpa-updater-schedule-check.sh` 与回归测试；不修改
  CPA config、OAuth 凭据、API key、模型目录、admission 契约或路由。
- 官方外置 `CLIProxyAPI` checkout 为 read-only reference；本轮只 `fetch`
  origin/main 并核对最新 tag 为 `v8.0.80`（`f5c5681a`），没有更新其脏工作树。

## 根因

doctor 通过 `sh -c "$UPDATER_SCHEDULE_CHECK --status"` 执行该 checker，而 checker
声明 `/bin/sh`。`budgets_every()` 使用 Bash-only 的 `local -a seen=''` 与
`[[ ]]`，在 dash 下直接报：

```text
sh: 134: Syntax error: word unexpected (expecting ")")
```

因此远端 timer 本身成功、`TimeoutStartSec=40min` 也正确，但 doctor 只能降级为
`readout_failed`。这不是 schedule 漂移，而是观测器不能解析。

## 本地修复与门禁

- 提交：`32f9486 fix(cpa): keep updater schedule checker POSIX compatible`。
- `budgets_every()` 改用 POSIX 字符串去重，语义仍拒绝缺失、非数值与重复预算键。
- 测试禁止该 `/bin/sh` checker 出现 `local -a` 和 `[[ ` 命令形态；正则字符类
  `[[:space:]]` 仍允许。
- `sh -n` 通过。
- focused：2 passed, 9 subtests passed。
- Full：`scripts/run_gates.ps1 -RunParallel`，`1817 passed, 30 skipped,
  892 subtests passed in 94.82s`；catalog、build、bandit、ruff check/format、
  mypy 均通过。

## BWG 远端投影

执行：

```powershell
$env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = "1"
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -Apply
```

关键回执：

- `BACKUP_DIR=/root/cpa-guardrails-backup-20261121T085957.819546761Z`
- `INTL_AUTH_READBACK disabled=7 of=7`
- 17/17 `PROJECTION_HASH_VERIFIED`
- `UPDATER_TIMER_READBACK on_calendar=*-*-* 04:00:00 randomized_delay=300`
- `READY_STATUS=200`
- `GUARDRAILS_APPLIED`

事务期间 admission 与 CPA 容器按既有 guardrails 路径重启；过渡时有一次
connection reset / admission 拒连，随后 `HEALTH_OK`。容器启动时间变为
`2026-11-21T09:00:06Z`，镜像保持 `cli-proxy-api-fork:v8.0.80-gemini-raw`
与原 image digest。

## host_loaded 复核

投影后 strict doctor 结果：

- `updater-schedule-budget-declared=exact`
- `updater-schedule-budget-largest=600s`
- `updater-schedule-budget-full-path=1170s`
- `updater-schedule-timeout-start=2400s`
- `updater-schedule-timeout-source=property`
- `updater-schedule=OK anchors=4 slots=04:00,04:10,04:20,04:30 --status`
- 17/17 projection-drift `MATCH`
- `admission-integrity=OK`
- `DOCTOR_CONTRACT_OK`

同轮运行态读数：CPA `v8.0.80-gemini-raw`、WorkBuddy CN `0.21.29`、INTL
`disabled_closed_dark_20261117`；chatgpt、antigravity、workbuddy CN/INTL lane
均 OPEN；Claude 为 retired/inert tombstone。7d `credential_ban_signals_7d=0`，
Antigravity 刷新失败 0。

## controlled_live 复核

投影后只对非 OAuth 的 Zhipu API-key lane 做最小受控验收，未发送任何 OAuth
生成请求：

- `cpa-health.py cache-canary glm-5.3-flash chat`：sample 1
  `input_tokens=4114 cache_read_tokens=0 hit_ratio=0.0000`；sample 2
  `input_tokens=4114 cache_read_tokens=4096 hit_ratio=0.9956`；
  `HEALTH_OK`。
- 经 `127.0.0.1:8318/v1/responses` 单发流式 SSE
  （`glm-5.3-flash`）：`PROBE_HTTP_TIME=200 1.400743`、
  `PROBE_EVENTS=61`、`PROBE_COMPLETED_OK`、正文精确返回 `PONG`。
- admission journal 归因：
  `lane=zhipu-coding-plan model=glm-5.3-flash status=200 capacity=false
  retry_after=absent`。

## 当前已接受风险

- 24h 自然流量审计仍报告 WorkBuddy CN `admit_peak_60s=19`，处于社区
  `14003` 观测上界；`requests_per_minute=18` 是 2026-11-01 后用户保留的值，
  本轮不改。峰值可能包含旁路 rate guard 的恢复探针；处置方向是客户端降低
  突发，不是预防性换号、清冷却或降低已裁决参数。
- Antigravity 7d `model_substitution` 335 次、99% 覆盖，但只有两个凭据哈希；
  已知 high-to-base 名称变体属复核警告，不能据此断言池级降智。
- 本轮只做 host_loaded 与自然流量观察，没有新增 OAuth 生成探针；不证明账号
  长期风控安全或质量不降级。

## 回滚

1. `git revert 32f9486`。
2. 从 `/root/cpa-guardrails-backup-20261121T085957.819546761Z` 按事务清单恢复
   受影响投影文件，重启受影响服务。
3. 重跑 strict doctor；预期恢复旧 `updater-schedule=UNVERIFIED
   reason=readout_failed` 形态但其余契约应回到基线。
4. Git 回滚不能替代远端恢复。
