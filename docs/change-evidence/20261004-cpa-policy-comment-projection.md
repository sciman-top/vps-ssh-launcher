# 2026-10-04 CPA cpa_policy 注释修复投影与 vasma 探针验收回执

## 目的

全链审查产出两项 Low 修复并完成远端收敛（真实远端写入记录）：

- F1（`5c3d1b8`）：`scripts/remote/cpa_policy.py` OAuth lane 注释与
  `EXPECTED_ADMISSION_MAX_INFLIGHT`（chatgpt-oauth=2）矛盾，修正为指向实测依据。
  纯注释变更，行为零变化；`cpa_policy.py` 属投影源，需 `-Apply` 收敛部署侧。
- F2（`3b58ef4`）：`scripts/vasma_kernel_update_cron.ps1` 只读探针新增
  `==kernel-update-log==` 段，把内核周更 UNVERIFIED（上游 latest≠pin）与回滚
  标记从远端日志提升到本地探针可见。该脚本本地生成远端命令，无需投影。

## 变更面（-Apply 实际写入）

- `/opt/cliproxyapi/cpa_policy.py`：投影更新为新注释版（sha256
  `d6c91365…66ccc1`，与 HEAD blob 一致）；其余投影源同字节重投影。
- 容器 `docker restart cli-proxy-api` 与 `cpa-admission.service` restart
  属 -Apply 事务既有步骤；CPA 镜像、config.yaml 语义零变更。
- 备份（回滚入口）：`/root/cpa-guardrails-backup-20261004T075758.572139705Z`。

## 验证时序

1. doctor pre：`DOCTOR_CONTRACT_FAILED`，唯一 FAIL =
   `projection-drift-cpa_policy.py`（HEAD 已领先部署侧的预期中间态）；
   其余全绿——`luna_state=available`、`MODEL_IDS` 10 名含 OAuth 三名、
   oauth-monitor 仅预期性 WARN（约 40h 后到期，CPA 到期前 24h 自动刷新，
   7 天零刷新失败信号）。
2. `-Apply`：`GUARDRILS_APPLIED`，无回滚；`==catalog_summary==` 13 ID 终态保持
   （`has_bare_luna/has_ai_input_im_bare_gpt61_sol` 等全部符合清单预期）。
3. doctor post：`DOCTOR_CONTRACT_OK`；9 项 projection-drift 全 MATCH；
   `MODEL_IDS` 13 名满编；`semantic-policy=OK`、`admission-health=OK`；
   `luna_state=available`；timer 3h 前触发。
4. F2 实机验收：vasma 只读探针 `==kernel-update-log==` 段在 bwg 正常输出
   （xray 日志 8 条结构化标记；singbox 日志缺失如实报 `missing`）。
5. 5.6-luna 生成级单发验收（重置窗后既定动作）：loopback 8317 单请求
   HTTP 200 / 1.30s / `finish=stop` / content=`OK` → LIVE_ACCEPTED。

## 已知遗留（交接并行审计会话）

- `==kernel-update-log==` 过滤模式未含 `skip reinstall` 字面量：周更最常见
  结局「pin 已是当前版」（INFO 行）不显示，最近 8 条会全是 start。
  一行修复位于 `scripts/vasma_kernel_update_cron.ps1` 探针 grep 模式 +
  `test_scripts.py` 对应断言，待并行会话提交后并入，避免混入其在途改动。

## 回滚

`cp -a /root/cpa-guardrails-backup-20261004T075758.572139705Z/cpa_policy.py
/opt/cliproxyapi/cpa_policy.py && docker restart cli-proxy-api`（注释回退，
行为等价）；或 `git revert 5c3d1b8` 后重新 `-Apply`。F2 无远端状态。
