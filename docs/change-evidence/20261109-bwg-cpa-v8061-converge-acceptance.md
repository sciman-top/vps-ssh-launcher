# bwg CPA v8.0.61 fork converge 验收与 doctor 收口回执 — 2026-11-09

**性质**: 换芯事务验收台账（事务由并行会话执行，本回执为独立复验 + doctor 收口）
**前序**: [20261108-bwg-cpa-v8061-exit126-crashloop-repair.md](20261108-bwg-cpa-v8061-exit126-crashloop-repair.md)
（exec 位事故修复与事务脚本补丁——本次成功的前提）

## 1. 事务执行（并行会话，2026-11-08T16:18:42–16:19:03Z）

工具链：`outputs/run-cpa-v8061-converge-20261108.ps1`（参数化基线 sha）→
`outputs/cpa-v8061-converge-transaction-20261108.sh`（含执行位补丁）。事务日志
`outputs/.tmp-converge.log` 摘录：

- `ENTRY_STATE compose=new plugin=new binary=old` → 备份
  `/opt/cliproxyapi/backups/20261108T161841.256612745Z-converge-v8.0.61-wb0989-gemini-raw`
- `DRAIN_OK`（16:18:42Z，午夜低流量窗口一次获锁）→ 显式重启 16:18:59Z
- `PLUGIN_REGISTERED version=0.9.89`；版本行
  `CLIProxyAPI Version: v8.0.61, Commit: 681ce41b+gemini-raw, BuiltAt: 2026-11-08T14:56:39Z`
- `RUNTIME_BINARY sha=31f2f9d9… host_and_container` → `READINESS_OK`（HEALTH_OK）→
  `AUTH_BYTES_UNCHANGED` → `LANE_HOLD_DELTA=none` → `restarts=0` → **`SWAP_OK`**

生产终态：镜像 `v8.0.61@fc714b43` + **fork gemini-raw 二进制（`31f2f9d9`）live** +
WorkBuddy 插件 0.9.89（`f2c678ff`）；config.yaml/auths 字节不变（sha `269579af` 基线）。

## 2. 独立复验（本会话，16:25:19Z）

| 项 | 结果 |
|---|---|
| 容器 | `restarts=0 running`，started 16:18:59Z |
| 二进制 | host 与 container sha 一致 `31f2f9d9`；mode **700**（执行位补丁生效） |
| readiness | `cpa-health.py readiness` → **HEALTH_OK**（直探 8317） |
| 车道单发 | `gemini-3.8-flash-high` 200 "ok"（回显 `gemini-3.8-flash-n` = 已定案良性命名替换）；`deepseek-v4.1-flash` 回归 200 "ok" |

## 3. doctor 收口（strict doctor）

- 00:20（+08）并行会话首跑 FAIL：`==workbuddy-version== MISMATCH expected=0.9.88
  got=0.9.89` → `DOCTOR_CONTRACT_FAILED`。归因=时序 artifact：doctor 内嵌检查由
  wrapper 运行时从 `scripts/remote/cpa-guardrail-workbuddy-version-check.sh`
  现场填充（占位符 `__CPA_WORKBUDDY_VERSION_CHECK_B64__`），而该文件的
  `EXPECTED=0.9.89` bump 落盘于 00:21——**跑在 bump 前一分钟**。
- 00:21 bump 已由并行会话完成（tests 同步断言 0.9.89，二者同批未提交，归其会话收口）。
- 本会话 00:3x 重跑 strict doctor（`scripts/cpa_bwg_guardrails.ps1` 默认档，
  只读）：`workbuddy-version=0.9.89` → **`DOCTOR_CONTRACT_OK`**，exit 0。

## 4. 边界与回滚

- worktree 中并行会话的未提交批次（`cpa-admission.py`、`cpa-guardrail-doctor.sh`、
  tests 等 M 文件）归其会话收口提交；本回执不折叠他人改动。
- 回滚：converge 备份目录 + 脚本 rollback 段（已补执行位）；深度回滚见事故回执 §6。
- 15:03 两次中止尝试与 mode-600 还原链路的完整归因见事故回执 §2/§4，不再重复。
