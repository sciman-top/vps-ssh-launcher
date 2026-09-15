# BWG CPA updater 投影（CRLF 收口与可观测性加固）

## 目标

将 commit `56ae9ad` 的仓库版 updater 投影到 `/opt/cliproxyapi/auto-update.sh`：发布元数据获取失败写 `METADATA_FETCH_FAILED` 日志、`prune_images` 的 `kept` 改为实际保留计数，使 2026-09-21 04:24 UTC timer 腿运行新代码。未触碰 ZZ、随机入口路径、OAuth/凭据、client key 与限流阈值。

## 变更

- 2026-09-15 通过 `cpa_bwg_guardrails.ps1 -Profile bwg -Apply` 单机投影，过程 `READY_STATUS=200`；重启期间首次 readiness 探测出现一次 `curl (56) connection reset` 瞬态，随后 200，属容器重启预期。
- 远端 `auto-update.sh` sha256 `f4682077c95c797d9d02b283f08f976a0ee766eb0b2b4507a3716a0570aa4504` 与本地源一致；`config.yaml` 与 `/etc/nginx/conf.d/cpa-gateway.conf` 投影前后哈希不变（幂等投影）。
- 投影备份：`/root/cpa-guardrails-backup-20260915T145603Z`。

## 验证

- 投影前严格 doctor：`DOCTOR_CONTRACT_OK`（timer enabled/active，next 2026-09-21 04:24 UTC，上次 result=success）。
- 投影后严格 doctor 复验：`DOCTOR_CONTRACT_OK`；容器 running v7.2.158、restart=0；loopback 8317、公网 8443、随机路径 401/404 语义、`request-retry=0` 等契约全绿。
- 真机 `--check`（2026-09-15T14:56:40Z）exit 0：`CANDIDATE current=v7.2.158 target=v7.2.158 soak=72h`、`BACKUP_HEALTH status=ok backups=4`；新 `SELECTION` 失败分支经 `bash -n` 与离线验证，未在真机注入故障。
- 本地：full gate `120 passed, 1 skipped, 77 subtests passed`；Bandit、Ruff check/format、mypy 通过（commit `56ae9ad`）。

## 残余风险与边界

`prune_images` 的 kept 实数改动位于成功更新后的清理路径，需下次成熟更新（不早于 9-21 timer 腿）在既有备份/回滚边界内实测，本次未手工触发版本更新。投影前 doctor 的 24h 窗口累计 `503=223`、`502=38`，仍属上游过载信号，与本次投影无关，不作为封号或降智证明。
