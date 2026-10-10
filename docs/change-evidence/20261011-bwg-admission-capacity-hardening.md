# 2026-10-11 BWG CPA admission 容量分层与冷却状态收紧

## 目标与范围

本次只处理 `bwg` 的 CPA admission 运行层，保留公网 Nginx、随机 capability path、严格 SSH host key、现有 OAuth 凭据和 `zz` 隔离。变更范围是 admission 源文件及其完整性 pin；`cpa-admission.json`、`cpa_policy.py`、CPA 镜像和 Nginx 配置均未改动。

## 本地变更

- 提交 `d4435d3`（`收紧 admission 容量分层与冷却状态`）。
- 同时出现模型级和账号级容量标记时按 lane scope 处理，避免共享 OAuth 账号仍被兄弟模型继续消耗。
- `healthz.cooldown_active` 改为按 monotonic deadline 判断，过期后即报告非 active，避免恢复流程对已过期冷却误发 reset probe。
- 新 admission SHA256：`5273f59e5b07cdaa13df1c66cb95bdb212905d94df9c906e3e813bee5b0218e0`。

## 远端事务

使用 `outputs/deploy-admission-modelstreak-20261010.ps1` 的窄事务，先把旧代预期更新为当时远端的 `1ae590d8…`，新代更新为 `5273f59e…`；保留的 config/policy hash 未变。

事务收据（UTC 2026-10-10 17:27）如下：

- preflight：service `active`，三条 lane `inflight=0,pending=0`，旧 admission 与 pin 均为 `1ae590d8…`。
- staged `sha256` 与目标一致；远端 `py_compile` 通过。
- maintenance lock 获取成功；`LANES_IDLE=ok`。
- 备份目录：`/root/cpa-admission-modelstreak-backup-20261010T172705Z`。
- `ROLLBACK_DRY_RUN_OK`。
- 原子替换后 admission 与 pin 均为 `5273f59e…`；`ADMISSION_INTEGRITY_OK`、`SEMANTIC_POLICY=OK`、`GENERATION_FIELDS=ok`。
- 重启后 PID 为 `619311`，healthz readiness 通过，临时 staging 已清理。

## 写后验证

fresh doctor（`outputs/doctor-20261011-review.txt`）返回 `DOCTOR_CONTRACT_OK`，并确认：

- CPA `eceasy/cli-proxy-api:v8.0.21` 及其 digest 未变，容器运行正常。
- `admission-integrity=OK`、`admission-service=enabled-active`、`admission-health=OK`。
- admission、config、policy、systemd unit、integrity check/drop-in、Nginx 和 fail2ban 投影均 `MATCH`；`identity-confuse=ABSENT`。
- `request-retry: 0`、OAuth `max_inflight=2`、其它 shared lane `max_inflight=3`、`max_pending=4`、queue timeout `120s`、bounded `Retry-After` 和随机路径等既有护栏保持有效。
- 当前 24h admission journal：`upstream_results=773`，lane rejects `3`（`cooldown=1, queue_timeout=2`），capacity events `26`（`200=19, 503=7`），全部来自 `chatgpt-oauth`；未观察到新的本地 model cooldown reject。
- 修正后的只读 journal probe（`outputs/probe-admission-journal-20261011.ps1`）读回 48h：`MODEL_COOLDOWN_REJECTS=0`、`MODEL_PROBES=0`、`LANE_COOLDOWN_REJECTS=5`、`QUEUE_TIMEOUT_REJECTS=2`、`CAPACITY_TRUE=61`；healthz 当前 OAuth lane 为 `inflight=0,pending=0,cooldown_active=false,cooldown_scope=none,model_cooldowns={}`，新代 admission 与 pin 匹配。
- 本机 Cockpit failure triage（当前只读数据库，48h，`scripts/cpa_failure_triage.py`）为 `rows=306`：`upstream_capacity=38`、`client_error=20`、`slow_success=1`、`healthy=247`，`local_gate=0`、`admission_queue_timeout=0`、`admission_fast_reject=0`。慢成功样本为 `62687ms`，符合上游生成耗时而非本地 admission 等待。

## 当前代受控 replay

使用 `outputs/accept-admission-mixed-20261011.ps1` 在远端 throwaway network namespace 内启动 fake upstream 和当前/上一代 admission。fake upstream 对 Luna 返回同时包含 `server_is_overloaded` 与 `Selected model is at capacity` 的 HTTP 503，并带 `Retry-After: 600`；Sol 返回正常 200。live CPA、OAuth、生产 journal 和公网路径均未参与。

- 上一代 `1ae590d8…`：混合标记被当作 model scope，Luna 后续为 `429 model_cooldown`，Sol 仍为 `200`。
- 当前代 `5273f59e…`：混合标记被当作 lane scope，Luna/Sol 后续均为 `429 cooldown`，healthz 为 `cooldown_scope=lane`。
- `ASSERT_FAILURES=0`、`AB_RESULT=PASS`；replay 后 production service 仍 `active`、healthz `ok`，三条 lane 均 idle，临时目录和进程均清理。
- 收据：`outputs/accept-admission-mixed-20261011.txt`。

## 根因与验收边界

保留的失败样本仍是上游/共享 OAuth 容量或 auth availability（503/Retry-After 等），不是本地 admission 把健康响应错误判成容量。收紧后的 admission 会减少账号级容量窗口内的兄弟模型穿透，并在模型级明确标记时只隔离该模型；它不能把上游真实容量不足变成可用，也不会通过增加并发、堆叠重试、清除冷却或轮换凭据制造绿色结果。

本次证据已达到：`repo_verified`、`filesystem_projected`、`host_loaded`、`controlled_live_replay`。没有执行真实 Desktop 自然会话，因此 `natural_live_accepted` 仍未声明；“Selected model is at capacity”或 `429` 是否在新的自然低频请求中消失，需要后续由用户在 `fq.sciman.top` 的实际 Desktop/Cockpit 会话取得新鲜 receipt 后单独判断。

## 回滚

在维护锁下执行：

```text
bash /root/cpa-admission-modelstreak-backup-20261010T172705Z/rollback.sh --dry-run
bash /root/cpa-admission-modelstreak-backup-20261010T172705Z/rollback.sh
```

回滚只恢复 admission 源与 pin 并重启 admission service；本次未改动 CPA 镜像、凭据、Nginx 或其它 lane 配置。
