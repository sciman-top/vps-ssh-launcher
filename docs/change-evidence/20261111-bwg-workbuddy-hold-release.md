# 2026-11-11 bwg workbuddy 车道 hold 释放与受控验收

## 事务边界

- 写入面：仅运行态 `/var/lib/cpa-admission/workbuddy-risk.json`（原子置 `hold_reason=null`）+ 仅重启 `cpa-admission`；config/路由/池/凭据零触碰。
- 脚本：`outputs/release-workbuddy-hold-20261111-authrequired.ps1`（克隆 2026-11-07 drain 契约，仅改 `EXPECTED_REASON='authentication_required'`）。
- 授权：用户当次会话明确授权（"授权按你的推荐/建议连续自动修复"）。

## UTC 时间线（journal/容器日志均为 UTC 换算，容器日志原始 CST）

- 04:45:56 `lane_hold_latched lane=workbuddy-oauth reason=authentication_required`（INTL 6008 credits 烧穿 + CN 6004 日窗 + oc 兜底 403 permission_error 误触发认证锁；详见同日会话诊断）。
- 09:20:27 CN 凭据 `297fa82d` flash 6004 窗上游声明重置。
- 09:26:31 只读预检：UTC≥09:22 ✓、`hold_reason=authentication_required` 未漂移 ✓、四车道 inflight=0/pending=0 ✓。
- 09:26:44–48 释放事务 receipt：
  - `HOLD_PREFLIGHT_OK reason=authentication_required not_before=1794372356.247673`
  - `RELEASE_BACKUP state=/root/workbuddy-risk-backup-20261111T092644.321622067Z.json`（sha256 `40fc7982…`）
  - `REQUEST_GUARD_EXCLUSIVE_OK` / `ADMISSION_IDLE_OK`
  - `LANE_HOLD_DELTA=none` / `ADMISSION_INTEGRITY_OK`
  - `RELEASE_OK lane=workbuddy-oauth cleared=authentication_required`
- 09:27:01 受控实战探针（8318 回环单发，key 仅远端变量、输出掩码 `agt_gw***`）：HTTP **500** `capacity=true protocol_result=capacity`。
  - 核心日志归因：本请求 3 个候选全为 INTL（`e99bc89f`/`ec2bceab`/`f8f41f32`）各回 6004 模型级频率限制；`max-retry-credentials=3` 预算耗尽，**未触达已重置的 CN 候选**。
  - admission 按容量类落 ≤300s 模型冷却（半开探针自动续试）；**未复锁**（终态为 capacity 类非认证类）。
- 09:3x strict doctor 只读：`oauth_lane=workbuddy-oauth posture=WAIT_UPSTREAM_WINDOW hold_reason=none`（锁已清除的权威判据行）；`workbuddy-realm-counts=cn:7,intl:7`、`realm-order=CN_THEN_INTL`、`deepseek-provider-order=WORKBUDDY_THEN_OPENCODE` 契约完整。

## doctor 终态与漂移定性

`DOCTOR_CONTRACT_FAILED`（exit 1），FAIL 全部来自投影漂移族，与本事务写入面无交集：

- `auto-update.sh` / `cpa-health.py` / `cpa_health_runtime.py(LIVE_MISSING)` / `cpa-admission-integrity-check` 四文件 deployed≠HEAD 期望；
- `==admission-integrity==` FAIL 为同一漂移的派生（部署版检查器哈希 ≠ 仓库 pin，事务内行为级检查已过 `ADMISSION_INTEGRITY_OK`）；
- 时间线：四文件均为并行会话 11-10 17:15:31Z / 22:29Z apply 集成员，先于本事务存在；事务只执行未改动。按提交时间线判为并行会话在途窗，留其收口对账；本事务不回滚任何东西。

## 恢复判据与后续

- 车道已恢复可调度：下一请求的合格候选含已重置 CN 凭据；INTL 侧 6004/6008 混合态待其各自窗口/日额度重置。
- 释放前评估：全候选死时释放会经同一 oc 403 路径立即复锁，故事务锚定首个 CN reset 之后执行。
- 红线遵守：未 re-login、未清冷却、探针单发、未动 config/路由/池/并发。
- git 收口（本文件 + 释放脚本）留交互窗口显式路径提交。
