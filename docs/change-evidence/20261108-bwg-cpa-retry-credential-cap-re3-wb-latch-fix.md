# BWG CPA 单请求凭据上限再评审 1→3（2026-11-08）

## 授权

- 用户本会话常驻授权："按你的推荐/建议连续自动修复和优化，直至完成所有任务"。
- 本变更即 [2026-11-06 收紧台账](20261106-bwg-cpa-single-credential-retry-cap.md)
  "未覆盖边界"所要求的再评审：以 2026-11-06/07 两次 workbuddy 车道落锁事故的
  取证为新生产证据，单独评审 `max-retry-credentials` 与 lane 契约。

## 触发证据（新，全只读探针）

- 两次 lane 落锁（均 429 + capacity 形状 + body 携带 OpenAI 风格 billing 码 →
  admission DURABLE billing 检查 → `retry_window_review_required` 人工锁）：
  - 2026-11-06 16:57:16Z（`deepseek-v4.1-flash-sg` 触发）；
  - 2026-11-07 12:56:24Z（`deepseek-v4.1-flash` 触发，随后 10 次 403）。
  11-07 14:11/14:17Z 人工释放后车道即刻恢复——两次均为瞬态上游拒绝，非池级
  真耗尽。
- 决定性反证：12:56:05–22Z 同模型 4 连成功（journal `upstream_result ...
  successful=true`），:24 单凭据 429 即落锁——**池没有空，一个凭据的拒绝成了
  整条车道的响应**。
- 机制链（代码已核）：宿主 `routing.retry.max-retry-credentials=1` +
  `request-retry=0` 使每请求只试 1 个凭据，walk 结束原样浮出最后上游错误
  （conductor 执行循环）；workbuddy 插件源码不含
  `insufficient_quota`/`billing_hard_limit_reached`/`enforced_spend_limit_reached`
  （billing body 为上游原文透传）；admission 对 DURABLE 车道 body 中的 billing
  码落 lane 级 durable hold（有意设计：billing 拒绝须 operator 处置）。两层
  各自合理、复合放大：**单凭据抽样被当作池级判决**。
- 连坐机制（用户以面板低用量凭据佐证）：429 → 宿主账本级递增冷却
  （credential-scoped across models，2026-09-20 设计行为），被 walk 到的拒绝
  凭据逐个被冷、与单凭据用量无关。
- 请求内 failover 缺失：cap=1 下 INTL→CN 双宿只在跨请求轮转中生效，而 lane
  锁把跨请求轮转一并掐死。

## 变更

### 远端（bwg，运行态）

- `/opt/cliproxyapi/config.yaml` `routing.retry.max-retry-credentials: 1 -> 3`
  （`request-retry=0` 不变）。config.yaml 为**文件级 bind-mount**
  （`config.yaml -> /CLIProxyAPI/config.yaml`），故用同 inode 原地编辑
  （`r+b`，inode 570917 不变），避免 sed -i/临时文件换 inode 导致容器读旧文件。
- 备份：`/opt/cliproxyapi/backups/config.yaml.pre-maxretry-20261107T173054Z`
  （SHA-256 前缀 `8c02f6e39abf6487` → `360657da02ec98e8`，diff 恰一行）。
- 热重载收据：`config_reload.go:137 max-retry-credentials: 1 -> 3` +
  `config successfully reloaded`（21 clients 重载）；容器**零重启**
  （StartedAt 2026-11-07T14:17:16Z 不变、RestartCount=0）；reload 后 error 计
  数 0。
- 受控实弹：经 admission(8318) 全链路对受影响模型 `deepseek-v4.1-flash` 单发
  PASS（finish_reason=stop）。

### 仓库契约同步（使远端值可持久，不被下次 `-Apply` 打回）

- `scripts/remote/cpa_policy.py`：策略 pin `1 -> 3`（评注保留 11-06 历史 +
  11-08 再评审依据）；wb `max_inflight` 评注补 cap=3 语义（busy 形状现可轮换
  sibling，inflight=1 上游安全界不变）。
- `scripts/remote/cpa-guardrail-apply.sh`：apply 前置白名单 `(0, 1)` →
  `(0, 1, 3)`；强制回写 `= 1` → `= 3`。
- `scripts/remote/cpa-acceptance.py`：合成验收 fixture `1 -> 3`。
- 测试与文案：`tests/test_cpa_policy_runtime.py`（断言 ×2、fixture）、
  `tests/script_validation_support.py`（fixture）、
  `tests/cpa_catalog_expectations.py` 与 `tests/test_cpa_guardrails_script.py`
  （oc alias 注释改述为"有界遍历"）。
- `docs/runbooks/cpa-workbuddy-plugin.md`：§1/§7/现状段/§4 封顶段共 4 处改为
  cap=3 语义并挂本台账。
- **有意不动**：`scripts/remote/cpa-admission.py:92` 的历史注释（11-06 实测
  依据）——admission 投影"动一字节即须 reviewed deploy"，为注释改动触发
  admission 重部署不成比例；该注释对 2026-11-06 时点仍准确。
- `tests/test_cpa_updater_apply.py` 的 `assertNotIn("max-retry-credentials")`
  保持有效：updater 部署路径仍不得触碰该键。

## 与 11-06 台账的对冲（再评审核心）

- 11-06 顾虑：6004 波及下同请求扇出放大与账号暴露。现值 3 重新引入**有界**
  扇出（每失败请求至多 3 个凭据，vs cap=0 的全池遍历）。
- 11-06 时点后的缓解层：admission 模型级 300s 停车（6004 波峰主接触面被
  park 吸收，扇出主要发生在波缘）；插件 (credential, model) fast-fail 注册表
  （已拒绝 pair 立即跳过）；per-credential 递增冷却。
- 残余暴露如实陈述：波缘每失败请求触达 ≤3 账号；若 INTL 全桶拒绝且 pick 未
  到 tier2，billing body 仍可浮出并落锁——该形态属池级真信号，落锁语义成立。
- 请求内 INTL→CN failover：walk 每 attempt 经 selection 走插件 pick
  （conductor_selection.go SchedulerPick），插件对 exhausted/cooling 候选随机
  回退，cap=3 下有到达 tier2 的实际概率（非保证）。

## 分层验证

### `repo_verified`

- full gates（触及投影 runtime，按 C 节 full closeout；逐阶段收据）：
  - `build`：安装后导入检查 PASS（run_gates 日志）。
  - `test`：`pytest -q` 全量 **1151 passed, 18 skipped, 587 subtests passed
    (309.96s)**——skip 均为已知环境门控（real-SSH integration、Git Bash 无
    flock）。受影响子集先行验证：test_cpa_policy_runtime /
    test_cpa_guardrails_script / test_cpa_updater_apply 共 107 passed +
    140 subtests。
  - `hotspot:bandit`（product）与 `hotspot:bandit-scripts`（scripts 树，
    Medium+）：EXIT 0（nosec 首处告警为既有噪音）。
  - `lint:ruff`：All checks passed。
  - `lint:format`：初跑发现 **2 个非本切片文件的既有格式漂移**
    （tests/test_cpa_admission.py 由 c429272、tests/test_cpa_updater_apply.py
    由 6e4ef80 于 11-07 窗口带入，`git diff HEAD` 证实非本次改动），在途
    `ruff format` 修复后全树复检 EXIT 0；两文件随后重跑 pytest 佐证
    （纯空白变更）。
  - `type:mypy`：Success, no issues found in 40 source files。
- **run_gates.ps1 执行偏离说明**：脚本在 test 阶段 pytest 解释器关停处挂死
  （summary 已完整输出后 python 进程不退出，零 CPU； detachment/无 TTY 环境
  artifact，非测试失败——同一工作树前台全量 5 分钟正常收尾）。终止挂死进程
  后，其余四阶段以脚本内同款命令逐一直跑取证。首跑曾卡 60+ 分钟即同因。
- `git diff --check` 通过（提交前复核）。

### `filesystem_projected`

- guardrails `-Apply` 后 13 个受管文件 `PROJECTION_HASH_VERIFIED`（锚 HEAD
  blob），strict doctor 投影 drift 全 `MATCH`。

### `host_loaded`

- watcher 热重载收据（上文）+ config 编辑阶段容器零重启。
- guardrails `-Apply` 再投影（切片入库 8e51aca 后执行，投影源锚定 HEAD）：
  `GUARDRAILS_APPLIED`、`READY_STATUS=200`、`APPLY_EXIT=0`，13 个受管文件全部
  `PROJECTION_HASH_VERIFIED`（含部署侧 `/opt/cliproxyapi/cpa_policy.py`）。
  事务按设计受控重启了 cpa-admission（20:05:59Z）与 CPA 容器（20:05:57Z）；
  重启后生效值复核仍为 3（config 第 40 行）。
- strict doctor（wrapper 只读模式，`EXIT=0`）：**`DOCTOR_CONTRACT_OK`**，doctor
  config dump 显示 `max-retry-credentials: 3` 与新 pin 一致（零漂移），
  `MODEL_CONTEXT_DRIFT=none`、`admission=OK`、`compose=OK`、nginx syntax OK。

### `live_accepted`（受控）

- 8318 全链路单发 PASS（上文）。
- **观察项（未闭合，不可合成）**：下一次上游 billing/6004 抖动窗口零落锁。
  错误转储仅存留数分钟，落锁窗口须当场跑
  `scripts/cpa_error_dump_forensics.py --ssh-profile bwg` 抓 body 原文，为
  挂账 B（admission billing-hold 契约修订）与 C（插件 OpenAI 风格 quota 码
  分类，循 #80 先例）供证。

## 回滚

- 运行态：`cp /opt/cliproxyapi/backups/config.yaml.pre-maxretry-20261107T173054Z
  /opt/cliproxyapi/config.yaml`（原地覆盖，watcher 自动热重载回 1）；或 mgmt
  API `PUT /max-retry-credentials`。
- 仓库：revert 本切片；`cpa-guardrail-apply.sh` 前置白名单仍接受 0/1，回滚后
  apply 事务可正常执行。

## 未覆盖边界

自然流量与受控单发不证明上游长期不封号、额度独立性或共享桶拓扑；不构成对
admission 契约（B）或插件分类（C）的变更；claude 车道既有急停
（authentication_required）保持不动；转储未捕获 billing body 原文，确切 billing
码仍属待证。
