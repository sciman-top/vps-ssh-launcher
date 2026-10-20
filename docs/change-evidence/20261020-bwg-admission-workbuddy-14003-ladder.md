# BWG admission workbuddy 14003 走冷却阶梯（去 operator hold 预置）台账（2026-10-20）

**性质**: 真实远端写入（`-Apply` 投影 `cpa-admission.py` + integrity pin 轮换 + cpa-admission 重启）· 脱敏留档
**授权**: 用户"确认做结构优化"（2026-10-20，针对当晚两次 14003 波均卷入人工释放的 toil）
**关联**: 20261020-bwg-cpa-v8035-canary.md §6（事件与两次人工恢复）、runbook cpa-workbuddy-plugin.md §4（14003 行同日两次更新）

## 1. 变更内容（scripts/remote/cpa-admission.py 头部位，~1921-1946）

- 原行为：workbuddy-oauth 的 429/503/529 一律在头部位预置
  `retry_window_review_required`（429 且无 `Retry-After` 时）——codebuddy 的 429
  从不带该头，故任何晚高峰容量波都必然落 operator hold，需人工按 cpa-gateway
  §人工恢复释放（当晚两次）。
- 新行为：workbuddy-oauth 分支只 `record_risk(None, 阶梯首档≈1260s)`——车道冷却
  ~21 分钟后自然放行，客户端期间见 429+冷却窗而非 403；**账号型 body 信号的 hold
  兜底不变**（`rate_limit_error`/6003/6004/11140/认证类仍经 stream-signal 路径
  置锁，非 SSE JSON 错误体同覆盖）。claude-oauth 分支逐字节保留（#6700 保守语义）。
- 依据：14003 官方归模型侧拥塞，当晚两波分别 ~31 分钟与 ~9 分钟自愈；社区
  "14003=出口 IP 聚合"为非官方观测，冷却阶梯+单请求节奏即兜底，且 403 硬锁对
  拥塞型信号属过度保护。

## 2. reviewed deploy（integrity pin 轮换）

- `cpa-admission-integrity-pin.txt`：`0207d7b4…ad4d` → `b0e7ec31…1803`
  （=本事务后 cpa-admission.py 的 sha256，full gates 的 head-anchored pin 测试
  强制同 commit 轮换）。
- 测试：新增 `test_workbuddy_model_busy_wave_cools_without_operator_hold`
  （钉"14003 信号层不持锁 + record_risk(None,1260) 持久化后 restart 读回
  hold_reason=None 且 cooldown_active=True"）；admission 套件 181 绿；full gates
  （build→test→hotspot→lint→type）全绿。

## 3. 部署与验证

- **`-Apply` 首次执行被完整性门 fail-closed 拒绝**（"REFUSE
  admission_integrity_pin_mismatch … rotate admission code with a reviewed
  deploy transaction, not with -Apply"）——10/10 admission 防护设计正确拦截；
  随后按插件换装同型纪律走 reviewed deploy 事务
  `outputs/deploy-admission-14003-ladder-20261020.sh`（sha256 `143b7072…`）：
  维护锁 → 基线 sha 断言（live==旧 pin）→ 暂存件 sha 断言 → 空闲断言（首跑因
  chatgpt 在途流量 fail-closed 拒绝一次，零变更；空闲后重跑）→ 双文件备份 →
  原子替换（保 755/644 root 权属）→ `/usr/local/libexec/cpa-admission-integrity-check`
  手工预演 → 重启 → healthz 4 lane+hold 断言。15:28:43Z exit 0，备份
  `backups/20261020T152843.127703146Z-admission-14003-ladder`。
- 代码件经 7 段 base64 分块上传（本机 32KB 命令行限制），远端 sha256+py_compile
  双验后才入事务。
- 事后 `-Apply` 通过完整性门并收敛（`GUARDRAILS_APPLIED`）；strict doctor：
  `admission-integrity=OK`、`workbuddy posture=OPEN hold_reason=none`、
  `projection-drift MATCH`、`DOCTOR_CONTRACT_OK`。
- 行为验收：单元层已钉（上述新测试）；真实 429 波不可控再现，自然验收留观察——
  下次晚高峰 14003 波应表现为"429+冷却窗 ~21 分钟后自愈"，若再现 403 operator
  review 即为账号型信号（按既有流程处置）。
- 回滚：git revert 本事务（pin+脚本成对回滚）后重新 `-Apply`；或直接用备份目录
  内两文件按同事务流程反向换装。
