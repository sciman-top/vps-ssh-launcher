# BWG admission 超长 Retry-After 修复证据

## 范围与结论

- 范围：仅 BWG；未操作 ZZ、凭据轮换、外置仓源码或构建产物。
- 根因：`scripts/remote/cpa-admission.py` 在收到上游 OAuth `Retry-After` 时，
  将原值写入 `_server_not_before`，而 `retry_after_max_seconds=86400` 只约束了
  `open_until`。2026-10-01 19:40:58 UTC 的上游 429 携带约 210568 秒退避，导致
  admission 后续直接返回同等时长的 `lane_reject/cooldown` 429；19:53:39 UTC
  的受控 Luna 请求在 admission 被拦截，未再到达上游。
- 修复：`d711c1d` 将 provider 值限制为
  `min(max(1, retry_after), self._retry_after_max)`；上限内仍尊重上游窗口，
  不提前 half-open probe。

## 验证

- `test_cpa_admission.py`：39 个测试收集并通过；新增边界用例覆盖 100000 秒值
  被限制到 86400 秒。
- BWG `-Apply`：2026-10-01 20:06:46 UTC 生成远端备份并重启
  `cpa-admission.service`；`MainPID=279646`、`NRestarts=0`。
- `repo_verified`：远端 `/opt/cliproxyapi/cpa-admission.py` SHA-256
  `2183d13caac7ad756cda116702387605c16e75244930dc58848e14772ea58f81`，与
  本地 LF-normalized source 一致；远端 `POLICY_OK`、Python syntax check 通过。
- `host_loaded`：admission 监听 `127.0.0.1:8318`，CPA 监听
  `127.0.0.1:8317`，Nginx 公网入口仍为 `8443`；health 读回
  `retry_after_max_seconds=86400`、`pending=0`、`cooldown_remaining=0`。
- 投影后 20:09:57 UTC 的新一条上游 Luna 429 在 Nginx 中记录为
  `upstream_status=429`，admission journal 记录 `capacity=true`、无
  `lane_reject`；这证明它是上游容量响应，不是本地 cooldown 放大。
- Cockpit 1.3.65 更新后曾再次生成错误的本机 provider gateway 配置
  （`request-retry=1`、`maxAccountConcurrency=0`）。已备份并重新投影为
  `request-retry=0`、`stream-bootstrap-buffering=false`、
  `maxAccountConcurrency=3`、`accountConcurrencyWaitMs=120000`；10909
  sidecar PID=8056，`/v1/models` 只作 liveness 读回 200。

## 证据边界

- `controlled_live_replay`：修复前的单次 Luna 请求复现了 admission cooldown 429，
  并与远端日志相关联；修复后没有重复生成请求以规避上游当前容量响应。
- `natural_live_accepted`：未宣称。Direct OAuth 当前仍是官方容量边界，且本次不消费
  或修改 OAuth 账号；自然长期稳定性需在真实低频使用窗口继续观察。

## 回滚

- 代码回滚：仅回滚提交 `d711c1d`。
- BWG 回滚：使用本次 `cpa-guardrails-backup-*` 目录按 guardrail 事务恢复，
  不使用 Git 代替远端恢复。
- 本机 provider gateway 回滚：使用本次带 UTC 时间戳的 `config.json.bak-cpa-repair-*`
  与 `manifest.json.bak-cpa-repair-*`，随后按 sidecar 正式启动路径重新加载。
