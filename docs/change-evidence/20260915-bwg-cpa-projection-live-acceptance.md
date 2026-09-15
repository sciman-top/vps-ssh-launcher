# BWG CPA 投影实战验收（2026-09-15）

## 范围与授权

- 目标：仅本机私有配置中的 `bwg` profile。
- 未连接、未修改 `zz`；未并行处理其他 VPS。
- 本次执行使用当前工作树中的 guardrails、health、updater 和 Vasma wrapper 源。
- 未输出密码、私钥、token、Authorization 或随机公网路径。

## CPA 投影

执行顺序为严格只读 doctor -> 单机 `-Apply` -> 独立 doctor/readback -> 生成 smoke。

- Apply 时间：2026-09-15 15:37 UTC 附近。
- CPA 备份：`/root/cpa-guardrails-backup-20260915T153715Z`。
- Apply 返回：`GUARDRAILS_APPLIED`，本地 readiness `READY_STATUS=200`。
- 投影后的本地文件 hash 与规范化仓库源一致：
  - `cpa-health.py`: `9fda5ca2933b3a8a7a1ff0698694db6a5d90c309144e54245f4acecfec94ca5b`
  - `auto-update.sh`: `f4682077c95c797d9d02b283f08f976a0ee766eb0b2b4507a3716a0570aa4504`
  - `cpa-gateway-filter.conf`: `8e9b785fc6faac280be2cb39153564c24f88307e3de57a0dca7db307699b16ce`
  - `cpa-gateway-jail.conf`: `7d737264f354e34686bd655a1c505a616e52f2315c25515385fe445d661ea302`
- Apply 后 doctor：`DOCTOR_CONTRACT_OK`。
- 容器运行中，CPA 仍为 `127.0.0.1:8317`，Nginx 仍为 `0.0.0.0:8443`；公网路径探针为合法路径未认证 `401`、裸路径 `404`、错误路径 `404`。
- `request-retry=0`、随机路径数量为 1、public listener 数量为 1、loopback proxy 数量为 1；Compose/Nginx/updater 语法均通过。

## 生成与上游边界

本地 `cpa-health.py generation` 被设计为所有 distinct route 都必须返回精确 `OK` 且 `finish_reason=stop`。

- 第一次 smoke：Luna/GLM 成功；Sol/Terra/Astra 返回 `HTTP 502`，因此返回 `UPSTREAM_UNAVAILABLE`。
- 用户随后报告 Sol/Terra 已返回 `200`；重新执行后，当前受控请求结果为：Luna `ok`、Sol 超时、Terra 超时、Astra `ok`、GLM `ok`。
- 因此本次结论是 `repo_verified + filesystem_projected + host_loaded`，不是所有 provider 路由的 `live_accepted`。HTTP 200 或用户侧状态观察不替代完整内容与结束原因验收。

## Google/Vasma 入口

- Google 只读探针通过：Xray active、IPv4 drop-in 和路由标记存在、Xray 配置测试通过；未执行 Google `-Apply`。
- Vasma 只读探针确认当前只维护 Xray，cron 为 `20 14 * * 5`，Xray wrapper 存在且 `bash -n` 通过，sing-box wrapper 当前缺失；未擅自切换内核。
- 为实战验证 wrapper/cron 事务，执行了单机 `-Kernel xray -Apply`，没有运行 vasma 内核升级：
  - 备份目录：`/var/backups/v2ray-agent-maint.oWwLeS`
  - 返回 `APPLY_BACKUP_DIR`，cron 和 wrapper 回读通过，Xray active。
  - 远端 wrapper 内容与当前源码生成内容一致，仅 heredoc 文件末尾少一个额外换行字节；内容差异不在脚本正文。

## 回滚入口与残余风险

- CPA：使用 `/root/cpa-guardrails-backup-20260915T153715Z` 按本仓 guardrails 的 `restore_all` 事务恢复；失败时看 `ROLLBACK_FAILED`。
- Vasma：使用 `/var/backups/v2ray-agent-maint.oWwLeS` 恢复两个 wrapper 与 crontab；本仓脚本失败时会输出 `ROLLBACK_VERIFIED` 或 `ROLLBACK_FAILED`。
- Sol/Terra 的中转延迟/不可用仍是当前未闭环的外部状态；没有修改 provider、relay、模型路由或 `zz` 配置来掩盖它。
