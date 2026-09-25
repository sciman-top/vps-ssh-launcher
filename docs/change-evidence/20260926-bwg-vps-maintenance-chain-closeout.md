# BWG VPS 维护链路投影与受控验收收口（2026-09-26）

## 范围与基线

- 目标：将提交 `1adf8cc`（`完善 VPS 维护链路的版本与回滚门禁`）的维护链路收口到
  `bwg`，并完成可逆的受控复验。
- 范围：只处理 `bwg`；未连接、未读取或修改 `zz`。SSH 继续使用严格 host-key
  校验。证据不包含密码、私钥、token、订阅地址、随机公网路径、公共 IP 或请求正文。
- 远端身份指纹仍为 `sha256:817c4e61…bd879d3f`；本次没有主机重启。

## 仓库变更

- `vasma_kernel_update_cron.ps1`：Xray 版本与 SHA-256 pin、vasma hash/菜单锚点、
  systemd/OpenRC 服务处理、锁忙退出 75、cron 读失败闭锁，以及 sing-box 持久化
  source fragment 和完整配置备份/恢复。
- `system_maintenance_cron.ps1`：锁忙/cron 失败闭锁、服务后端适配、apt 后 Xray、
  sing-box、Nginx、fail2ban 复验；不自动重启主机。
- `google_ipv4_routing.ps1`：委托脚本精确 hash、备份/回滚、Xray 配置测试与
  `ROLLBACK_VERIFIED` 门禁。
- `scripts/remote/cpa-auto-update.sh`：镜像清理保护运行镜像及所有保留备份镜像，
  失败时闭锁清理。
- `inventory.py`、Xray adapter、README/runbook 同步远端状态和回滚契约。

## 远端投影

### Xray 周更 wrapper

- 以当前远端 Xray `26.3.27` 及已读回 SHA-256 pin 重投影；事务备份：
  `/var/backups/v2ray-agent-maint.QtV8hw`。
- `/etc/cron.d/vps-launcher-kernel-update` 在位，wrapper `bash -n` 通过。

### 月度维护 wrapper

- 事务备份：`/var/backups/v2ray-agent-maint.kMCqft`。
- `/etc/cron.d/vps-launcher-monthly-maintenance` 在位，wrapper syntax、logrotate
  配置和服务后置检查通过；没有执行 apt，也没有自动重启主机。

### Google IPv4 routing

- 使用远端委托脚本精确 SHA-256 `8e0fb95e…33f74ef`；事务备份目录由脚本输出并
  保留在主机。
- Apply 输出 `APPLY_VERIFIED`；Xray 配置测试和 active 状态复验通过，公网出口值
  未写入本证据。

### CPA guardrails

- 严格 doctor 发现的唯一漂移是 `/opt/cliproxyapi/auto-update.sh`；使用
  `cpa_bwg_guardrails.ps1 -Profile bwg -Apply` 备份优先投影，备份：
  `/root/cpa-guardrails-backup-20260925T170221.458771659Z`。
- 六个受管文件均输出 `PROJECTION_HASH_VERIFIED`；updater 远端 hash 为
  `9c209549…f3041f9ab2f`，与当前仓库 HEAD 一致。Apply 内 readiness=200、
  `HEALTH_OK`、目录契约通过，事务以 0 退出，未触发回滚。
- provider env 只按清单槽位编码进远端事务，未输出内容；未消费 usage queue，未
  轮换 OAuth，未发送模型 generation。

## 受控实战验收

- Xray wrapper：手动 replay 命中“版本与 pin 已匹配，跳过安装”路径，退出 0；
  Xray active、配置检查 OK、版本和 hash 保持 pin。
- 月度维护锁忙负路径：持有同一 flock 后运行 wrapper，得到
  `CONTROLLED_LOCK_BUSY_EXIT=75` 与 `LOCK_BUSY_ACCEPTED`；没有执行 apt。
- Google routing：真实 Apply 后 `APPLY_VERIFIED`，Xray 配置/服务复验通过。
- CPA updater：`bash /opt/cliproxyapi/auto-update.sh --check` 退出 0，输出当前
  `v7.3.17` 无新候选与 `BACKUP_HEALTH status=ok`；该模式不重启服务、不清理镜像、
  不发送 provider generation。
- CPA strict doctor：最终返回 `DOCTOR_CONTRACT_OK`；六项 projection drift 全为
  `MATCH`，CPA running/restart=0，loopback 8317、公网 8443、路由 401/404/404、
  `request-retry=0`、冷却持久化关闭、Nginx/fail2ban/logrotate/Compose 与策略
  门禁全绿。

## 分层验收结论

| 层级 | 结果 | 依据 |
|---|---|---|
| `repo_verified` | PASS | commit `1adf8cc`；full gate 已通过：185 passed、1 skipped、208 subtests；Bandit、Ruff、mypy、`git diff --check` 通过 |
| `filesystem_projected` | PASS | BWG Xray、月度维护、Google routing、CPA 投影均有备份、hash/readback 和回滚边界 |
| `host_loaded` | PASS | fresh inventory reachable；strict doctor=`DOCTOR_CONTRACT_OK`；Xray/Nginx/fail2ban active |
| `controlled_live_replay` | PASS | Xray skip path、月度锁忙 75、Google Apply、CPA updater `--check` 与 readiness 均通过 |
| `provider_generation_replay` | NOT EXECUTED | 本次维护切片不为制造证据发送 generation，避免增加 provider/OAuth 风控信号 |
| `natural_live_accepted` | NOT CLAIMED | 仍需真实用户在正常业务窗口观察稳定性、上游限流和模型语义；本证据不把 200、目录或健康门外推为账号状态 |

## 残余观察与回滚

- OAuth 监控当前为 `WARN_RENEWAL_WINDOW`（剩余约 67.6 小时，近 7 天刷新失败数为
  0）；未自动轮换凭据。进入 ACTION_REQUIRED 窗口前按既有人工续期 runbook 处理。
- 最近 24 小时仍有上游侧 502/503/429 与客户端 499；doctor 已按
  `upstream_status`、耗时和脱敏 Retry-After 类别分桶。这是观测信号，不是封号、
  限流或降智结论。
- sing-box 当前 inactive，本次没有切换或运行 sing-box wrapper；其旧配置迁移仍
  是人工前置步骤。
- slot-3 的明文 HTTP 是用户明确保留的精确例外，继续单独报告传输风险。
- 回滚只使用本次事务对应的远端备份目录，恢复后重跑同一 strict doctor；Git 回滚
  不能替代远端恢复。
