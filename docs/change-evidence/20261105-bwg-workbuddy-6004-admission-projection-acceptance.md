# 2026-11-05 BWG WorkBuddy 6004 准入修复投影与受控验收

## 范围与目标

- 目标：让 WorkBuddy `6004` 在 CPA admission 层按 **模型级** 处理，尊重可信
  reset 时间；没有 reset 时仅镜像插件默认约 60 秒的短 park，不把模型级信号
  升级成整个 WorkBuddy lane 的持久停流。
- 仅操作 `bwg`；没有操作 `zz`。
- 保留现有 WorkBuddy CN/INTL 账号、OAuth/API key、`scheduler_mode=credits`、
  `account_max_concurrent=1`、`identity_switch_on_429=false`、请求重试和冷却
  策略。没有轮换、删除、刷新或复制凭据，没有清 cooldown、提高并发/RPM/重试，
  也没有主动制造 6004。

## 仓库变更

- `6189182 fix(cpa): mirror WorkBuddy 6004 reset at admission`：新增模型级
  6004 reset 镜像逻辑及定向回归。
- `2218966 ops(cpa): add reviewed WorkBuddy 6004 admission deploy`：新增有维护锁、
  请求排空、备份、原子替换、失败回滚和离线守卫的专用部署事务。
- 本地修复证明：原有 admission 回归 `292 passed`；6004 定向测试、
  `py_compile`、`git diff --check` 均通过。

## 生产事务与回滚

- 远端 dry-run：`ADMISSION_IDLE_OK`、`ADMISSION_INTEGRITY_OK`、`DRY_RUN_OK`，
  `no_swap=true`。
- 实际事务：`REQUEST_GUARD_EXCLUSIVE_OK`、`ADMISSION_IDLE_OK`、
  `ADMISSION_INTEGRITY_OK`、`WORKBUDDY_6004_ADMISSION_GUARD_OK`、
  `ADMISSION_REGRESSION_GUARD_OK`、`DEPLOY_OK`。
- 新 admission SHA-256：
  `1894719c9af3224d80e17c87f209712742e0fe7f8e5e6b00f9071aa3ba308b8a`。
- 远端备份：
  `/opt/cliproxyapi/backups/20261104T171418.092579196Z-admission-workbuddy-6004-20261105`。
- 事务只重启 `cpa-admission`；CPA 容器保持 `restart=0`，未改动 CPA 配置、
  provider 路由、Nginx 或其他 provider。
- 回滚入口：在同一维护锁和请求排空边界内恢复上述备份的
  `cpa-admission.py` 与 integrity pin，重启 admission，随后运行严格 doctor；
  不用 Git revert 代替远端恢复。

## 投影后验证

- 严格 doctor：`DOCTOR_CONTRACT_OK`。
- admission integrity：`OK`；`cpa-admission.py`、配置、systemd unit、
  integrity checker/pin 与仓库 HEAD 的 `projection-drift` 均为 `MATCH`。
- WorkBuddy 运行策略读回：`0.9.85`、`scheduler_mode=credits`、
  `account_max_concurrent=1`、`identity_switch_on_429=false`、CN/INTL model
  pins 保持；`workbuddy-oauth` 为 `OPEN`，`hold_reason=none`。
- 公网入口认证目录探针：通过 8443 随机 capability path，HTTP `200`，目录
  `11` 个模型，`deepseek-v4.1-flash` 存在。凭据、域名和 capability path 未写入
  收据。

## 单发受控实战

- 请求前先等待 `workbuddy-oauth` 排空；若存在 hold/cooldown 会直接 DEFER。
- 通过 8443 公网入口发送一次 `deepseek-v4.1-flash` Responses SSE，客户端无
  自动重试、无跨账号续打。
- 结果：HTTP `200`、SSE 事件 `20`、`response.completed=true`、文本匹配、
  无 capacity marker、无 6004 marker，判定 `CONTROLLED_WORKBUDDY=PASS`。
- 请求后只读复核：`hold=none`、`cooldown=false`，最终
  `inflight=0,pending=0`，`ADMISSION_HEALTH=OK`。

## 结论边界

- 本轮已达到：`repo_verified`、`filesystem_projected`、`host_loaded` 和一次
  `controlled_live` WorkBuddy 请求。
- 受控单发证明当前入口、admission 规则、CPA/插件链路能完成一次请求；不证明
  WorkBuddy 上游共享限流窗口已经消失，不证明所有账号额度/资格或长期账号安全，
  也不证明中国运营商网络的长期自然流量稳定性。
- 既有日志中的其他 lane 容量、客户端断开或历史错误仍按各自信号处理；不会因为
  6004 发生在多个账号上就自动清理 cooldown、换号、换 IP 或增加并发。
