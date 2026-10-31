# 2026-10-31 BWG admission 启动 stale write-ahead 标记自动恢复（latch 根治）

## 授权与边界

- 用户出示当日双车道 403 事故（见 `20261031-bwg-two-lane-unverified-latch-release.md`，
  同款固有竞态第三次发生）后明确要求"彻底修复和优化"，并批准计划模式产出的
  修复方案（启动清 stale 标记 + 90s 有界复核窗 + reviewed deploy）。
- 仅触碰 `scripts/remote/cpa-admission.py` 的启动加载语义、integrity pin 同
  commit 轮换、测试与文档口径；不改真实风险理由（`authentication_required`/
  `account_restricted`/`retry_window_review_required`/`state_write_failed`）
  的 latch 契约，不改 `-Apply` 的"拒绝轮换 admission 代码"门，不新增常驻
  watcher/cron。

## 修复设计（commit c535a7a）

- `LaneState` 启动加载遇 `hold_reason=unverified_request`：判定为 stale
  write-ahead 标记（语义=有请求在途、结果未知，非风险判决）→ 置 null + 90s
  有界复核窗；窗内请求立即快败 `stale_write_ahead_cooldown` 并携带精确剩余
  Retry-After（不进 FIFO 队列），窗口过后自然由下一发请求完成上游复核。
  落盘即时重写 null，/healthz 不可达时盘上不再残留 hold。
- 真实风险理由照旧 latch、照旧要求人工恢复（对应测试全部保持绿）。
- 并行会话在本会话基础改动之上做了 acquire 路径精化（独立
  `_stale_write_ahead_until` + 快败 lease），双方在同一共享工作树双向吸收后
  以合并态提交；测试参数化覆盖全部四条 durable 车道。
- pin 随合并源同 commit 轮换至 `07c8dca4…`（并行会话首次重生成时缺尾换行，
  被 `test_cpa_guardrails_pin_is_head_anchored_and_matches_admission_script`
  的 `endswith("\n")` 断言抓住，已在 c535a7a 修正）。

## 门禁与部署

- full gates 全绿：pytest 889 passed（含 4 车道 stale-marker 用例与全部既有
  restart-latch 契约用例）、bandit、ruff check、ruff format 78 文件、mypy 78
  文件零问题。同工作树并行会话的 CDN 在途件（未提交）不属于投影源，未进入
  本提交。
- **部署事实**：本会话准备好 reviewed deploy 事务
  （`outputs/deploy-admission-stale-marker-20261031.sh`，基线/暂存 sha 断言
  + drain + 备份 + 原子替换 + integrity 预演模板），执行前基线核验发现远端
  `cpa-admission.py` 与 pin 已被并行会话收敛至 `07c8dca4`（03:45:23Z 起服务
  跑新代码，此前 02:52/02:59/03:05 三次迭代重启），与本地 HEAD blob 三方
  逐字节一致——事务脚本未执行、留档。
- 本会话随后执行 `-Apply` 收敛（`GUARDRAILS_APPLIED`，同步 doctor 内嵌 pin
  值等六件投影），strict doctor：`admission-integrity=OK`、
  `projection-drift MATCH`、`lane-hold=OK`、`DOCTOR_CONTRACT_OK`。

## 验收

- 8318 回环单发一次：`deepseek-v4.1-flash` → **HTTP 200 / 0.93s /
  finish=stop**（key 远端解析未回显）。
- healthz 四车道 hold 全 null；journal 自新代码上线无 ERROR/Traceback，无
  `lane_reject reason=operator_review_required`。
- 行为级终验锚：此后任意部署重启若竞态命中，journal 应出现
  `stale_write_ahead_cleared cooldown_s=90` 且 90s 内车道自动恢复，客户端在
  窗内收到 429+精确 retry_after（非 403）。

## 边界与回滚

- 修复不证明上游账号长期状态；`unverified_request` 的 fail-closed 精神保留
  （有界窗 + 即时落盘重写 + 真实理由照旧人工恢复）。
- 回滚：`git revert c535a7a` → 重走 reviewed deploy 事务轮换回旧源与旧 pin
  （部署备份与事务模板均在）；仅调窗时长则改
  `STALE_WRITE_AHEAD_COOLDOWN_SECONDS` 一行 + pin 轮换。
