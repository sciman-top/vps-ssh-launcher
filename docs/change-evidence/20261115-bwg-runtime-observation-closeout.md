# 2026-11-15 BWG 运行态独立观察项收口

## 范围与目标

本切片处理 BWG 维护状态中的两个独立观察项：

- `cpa-update` 曾为 `failed/1`，随后按现有 updater 契约执行无候选
  `--apply`，状态收敛为预期的 `deferred/77`；
- `kernel-sing-box` 保留了 2026-11-13 的历史 `busy/75`，需要在不清理
  锁、不强杀进程、不改变 pin 的前提下完成一次单机串行复核。

本次没有重新投影本地 Python/PowerShell 模块，也没有轮换凭据、改变出口、
提高重试或并发。远端唯一持久化变化是 sing-box wrapper 按既有合同更新了
`kernel-sing-box.status` 账本。

## 执行与证据

### `host_loaded`

- 严格 CPA doctor：`EXIT=0`、`DOCTOR_CONTRACT_OK`；投影 drift 全部
  `MATCH`，`health=OK`、`health_runtime=OK`、`policy=OK`、
  `admission=OK`、`compose=OK`、`nginx-syntax=OK`。
- doctor 心跳：

  ```text
  heartbeat=cpa-update result=deferred exit_code=77 age_hours=0
  heartbeat=monthly-maintenance result=success exit_code=0 age_hours=15
  heartbeat=kernel-xray result=success exit_code=0 age_hours=43
  heartbeat=kernel-sing-box result=success exit_code=0 age_hours=0
  heartbeat=v2ray-agent-update result=success exit_code=0 age_hours=43
  heartbeat=renewtls result=success exit_code=0 age_hours=8
  ```

- sing-box 只读 preflight：维护锁 `free`；wrapper pin、运行版本
  `v1.14.4`、二进制 SHA-256 与 vasma SHA-256 均匹配；service `active`；
  `sing-box check -c` 为 `OK`；新 Google IPv4 route fragment 存在，旧
  fragment 缺失，合并配置中 IPv4 resolve 规则数量为 `1`。
- 单机串行执行：

  ```text
  bash /etc/v2ray-agent/auto_update_singbox.sh
  ```

  返回 `EXIT=0`。远端状态账本为 `result=success exit_code=0`，日志明确
  `pinned sing-box version and hash already match; skip reinstall`，因此
  本次没有换芯、没有重启 sing-box、没有触碰配置内容。

### `controlled replay`

- 真实 `vps-maint inventory --run-integration --profile bwg`：`EXIT=0`，
  profile reachable，sing-box/xray 配置检查均 `ok`。
- `proxy_hardening_drift.ps1 -Profile bwg`：`EXIT=0`，
  `HARDENING_DRIFT=NONE`。
- `proxy_lane_acceptance.ps1 -Profile bwg`：`EXIT=0`，8 条受控车道均
  `RESULT=PASS`，最终 `ACCEPTANCE_RESULT=PASS`。

## 回滚与边界

- 本次没有需要恢复的二进制、配置或服务变更；wrapper 的现有备份与失败
  回滚路径保持可用。
- 不删除历史 `busy` 状态文件、不清理维护锁、不强杀未知进程。状态账本由
  wrapper 原子更新，重复执行仍受共享锁和 pin 校验约束。
- `cpa-update deferred/77` 表示无候选更新时的 fork/bind-mount canary
  延迟，不代表 CPA 已升级；`DOCTOR_CONTRACT_OK` 和受控车道通过也不等于
  provider 账户长期安全、自然流量接受或长期 QoS。
- 本记录证明的是当前主机加载与一次受控重放；自然/长期 `live_accepted`
  仍需独立的低频、真实业务窗口证据。
