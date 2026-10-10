# 2026-10-10 BWG admission per-model streak on lane-scoped refusals（modelstreak）

## 背景与依据

2026-10-10 晚间本机 ChatGPT desktop 持续报 "Selected model is at capacity"。
取证（`outputs/probe-journal-tail-20261010.txt`、`probe-marker-shape-20261010.txt`、
`probe-journal-recent-20261010.txt`、`probe-sol-now-20261010.txt`）确认上游 shed 与模型相关：
同一 OAuth 凭证下午至晚间对 `gpt-6-luna` 的请求持续拒绝（error dump 标记
`server_is_overloaded`+`auth_unavailable`，账号级文本），而 `gpt-6.1-sol` 同时段大量成功；
19:36 CST 起 sol 也被间歇拒绝（上游窗口扩大）。桌面端文案是客户端对失败的统一转译，
不代表上游返回了模型级标记。

在"账号级信号 + 混合流量"形态下，admission 的 lane streak 被健康兄弟模型的
成功不断复位，被拒模型的每个请求都穿透到上游慢失败（另有 `queue_timeout
waited_ms=120000` 的排队等死样本）。本次变更给 lane-scope 容量拒绝叠加
per-model 连击：同一模型连续 2 次容量失败（无论 marker 归到 lane 还是 model
档）即进入既有模型级冷却（60s 阶梯起步、探针复测、代际校验），该模型客户端
转为本地快失败；兄弟模型与 lane 逻辑完全不变。

## 变更内容

- `scripts/remote/cpa-admission.py`：`release()` 的 lane-scope 容量分支在
  `lease.model` 非空时调用 `_record_model_capacity(lease.model, None, now)`
  （不伪造模型级 Retry-After，账号退避仍由 lane `server_not_before` 承载）；
  lane 探针成功分支按既有 consecutive 契约清除该模型的 streak。acquire()、
  healthz、journal 格式零改动——模型级冷却的拒绝/探针/快照路径全部复用
  2026-10-10 上午已验收的 model-scope 机制。
- `scripts/remote/cpa-admission-integrity-pin.txt`：同步旋转至
  `1ae590d821bb6fa94275ae0e5bf0b85d9394021f6e3eda253efa45f2bf8cd21d`。
- `cpa-admission.json` / `cpa_policy.py` / doctor 契约零改动。

## 验证

- 单测：`tests/test_cpa_admission.py` 新增
  `test_lane_scoped_failures_build_a_per_model_streak`（混合流量核心场景：
  sol 成功清 lane streak 但不清 luna 连击，luna 第 2 次失败即被模型级秒拒）
  与 `test_lane_probe_success_clears_the_probing_models_streak`；全套
  80 passed。契约测试（risk_audit 55 + guardrails）76 passed。
- full gate：`outputs/gate-20261010-modelstreak.txt` —
  550 passed + 1 skipped + 269 subtests；bandit/ruff/format/mypy clean。
- 受控 A/B（`outputs/accept-modelstreak-20261010.sh` / `.txt`，netns 内
  假上游，OLD=115147Z 备份、NEW=部署文件）：混合序列
  `luna→sol→luna→luna→sol`，`ASSERT_FAILURES=0; AB_RESULT=PASS`。
  OLD：luna 上游命中 3（全部穿透慢失败）；NEW：命中 2，第 3 发
  `429 model_cooldown` 本地秒拒，sol 两代均 200，healthz
  `cooldown_scope=model model_cooldowns={'gpt-6-luna': 60}` 且 lane
  `cooldown_remaining=0`。

## 部署过程（如实记录，含并行会话窗口）

- 本会话按 `outputs/deploy-admission-modelstreak-20261010.ps1` 执行事务：
  preflight（当时 OLD_ADMISSION/PIN=183fdd1f）→ staging（sha 校验、
  py_compile）→ step4 事务在 pin 前置检查被 **fail-closed 拒绝**
  （"REFUSE pin is not the old generation"）。
- 取证（`outputs/probe-forensics-20261010.txt`、`probe-parallel-backup-20261010.txt`）
  确认：一个并行维护会话在 11:51:47Z 已用同等工作树内容完成了同一旋转
  （备份 `/root/cpa-admission-modelscope-backup-20261010T115147Z`，其
  `.old` 侧经核验恰为上一代 183fdd1f；`/tmp/cpa-policy-modelscope.log`
  11:53Z），服务 11:53:04Z 重启。本会话事务的拒绝是护栏的正确行为，
  stage 目录残留已清理。
- 写后状态核验（`probe-deploy-state-20261010.txt`）：远端
  `cpa-admission.py` = `1ae590d8…`（与本地 HEAD 工作树逐字节一致）、
  pin 一致、服务 active（PID 599265）、integrity ExecStartPre 通过、
  healthz 三 lane 正常。
- fresh doctor：`outputs/doctor-20261010-modelstreak-after.txt`（写后收据）。

## 风险与回滚

- 回滚：`bash /root/cpa-admission-modelscope-backup-20261010T115147Z/rollback.sh`
  （恢复 183fdd1f admission + 旧 pin 并重启；`--dry-run` 已验证）。
- 行为边界：模型级冷却叠加后，被 park 模型在冷却窗内的本地 429 文案
  经客户端转译后仍显示 "at capacity" 类提示，变化是失败速度（慢穿透 →
  快失败）与对上游账号的无谓穿透减少；不能使上游真实容量不足的模型变为可用。
- 并行会话窗口教训：本会话 preflight 与事务之间数十秒内 pin 被并行事务
  旋转，pin 前置检查的 fail-closed 拒绝避免了一次盲写。后续 admission 类
  部署事务应把"preflight → 事务"间的代际漂移视为常态，REFUSE 后先取证
  再决策（本次即如此执行）。
