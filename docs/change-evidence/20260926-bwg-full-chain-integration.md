# BWG 全链维护与 v2ray-agent 更新链验收

## 范围与判据

- 目标主机：`bwg`，严格 host-key 校验；本次不连接 `zz`，不推送 Git。
- 远端写入：RenewTLS 调度迁移、v2ray-agent updater 调度投影，以及已授权的月度系统维护和
  pinned updater 实跑。
- 明确不执行：OAuth/凭据消费、路径轮换、实际 `RenewTLS` 证书申请、Xray/sing-box 核心
  更新、主机重启。
- 证据按 `repo_verified -> filesystem_projected -> host_loaded -> controlled_live_replay`
  分层；自然用户流量验收单独记录为未完成。

## 源固定与兼容性

`mack-a/v2ray-agent` 采用仓库内的显式 pin：

- commit：`5c5e2b72a394356fb1d53ed05785d407b8743758`
- 版本标记：`v3.5.25`
- `install.sh` SHA-256：`fca0ad30d335b05b4e99fc5de848aeaff6c32d4b97f01ae84497dfad2978bfeb`
- 清单：[scripts/remote/v2ray-agent-source-pin.json](../../scripts/remote/v2ray-agent-source-pin.json)

远端 updater 已从可变 `master` 改为该 commit 的 raw URL，并在下载后拒绝任何不匹配的
候选 hash。当前 BWG 的 vasma、Xray、sing-box 兼容边界保持原设计：管理脚本 updater
不调用菜单；Xray 周更仍由单独的 vasma wrapper 负责，当前只读状态为 Xray `26.3.27`
且配置测试通过。

## 远端投影

### RenewTLS 调度迁移

成功 Apply 的备份目录为 `/var/backups/v2ray-agent-renewtls-deploy.XFlurl`，读回结果：

- `/usr/local/sbin/vps-launcher-v2ray-agent-renewtls.sh`：`755 root:root`，SHA-256
  `7e174e8b425a5a189f62ef77ec693cd3b5493a9885d42de31c0116ecb296ec30`。
- `/etc/cron.d/vps-launcher-v2ray-agent-renewtls`：`644 root:root`，内容为
  `30 1 * * * root /bin/bash /usr/local/sbin/vps-launcher-v2ray-agent-renewtls.sh`。
- root crontab 中旧的 `/etc/v2ray-agent/install.sh RenewTLS` 行已不存在。
- wrapper `bash -n` 通过，并使用 `/run/vps-ssh-launcher-maintenance.lock`。

预演阶段发现了 `verify_runtime` 的判断缺陷；该次投影停止后读回仍是原 root crontab 和
未投影文件状态，修复后才执行成功 Apply。后续受控锁忙回放持有共享锁运行 wrapper，
返回 `75` 并追加 `DEFERRED_BUSY`，没有进入 `install.sh RenewTLS`。

### v2ray-agent updater 调度

成功 Apply 的备份目录为 `/var/backups/v2ray-agent-script-update-deploy.23cxVX`，读回结果：

- `/usr/local/sbin/vps-launcher-v2ray-agent-update.sh`：`755 root:root`，SHA-256
  `75bad698f43fc7ac9c0da9004c3fef5d4cc93fc0941a0076bc78798a6c41bdad`。
- `/etc/cron.d/vps-launcher-v2ray-agent-update`：`40 14 * * 5 root ... --apply`。
- 远端脚本的 `SOURCE_REF`、`EXPECTED_CANDIDATE_SHA` 与仓库 pin 一致，`bash -n` 通过。

第一次投影因旧的 `master` 锚点仍在投影脚本中而闭锁，并报告
`ROLLBACK_VERIFIED`（备份目录 `/var/backups/v2ray-agent-script-update-deploy.jx7I4q`）；
删除旧锚点后重新 Apply 成功。

## 真实受控执行

### 单独 updater 回放

在 BWG 上按严格 host-key 连接执行：

1. `--check`：候选 hash 与当前脚本相同，结果 `CHECK_NO_CHANGE`、`SERVICES_OK`，退出码 0。
2. `--apply`：结果 `NO_CHANGE`、`SERVICES_OK`，前后脚本 SHA-256 均为
   `fca0ad30d335b05b4e99fc5de848aeaff6c32d4b97f01ae84497dfad2978bfeb`，退出码 0。
3. 运行日志记录的 source ref 是固定 commit，没有服务重启或脚本替换。

### 统一全链入口

入口：[scripts/bwg_full_maintenance.ps1](../../scripts/bwg_full_maintenance.ps1)

真实运行命令为：

```powershell
$env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = "1"
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\bwg_full_maintenance.ps1 `
  -Mode RunNow -RunIntegration
```

运行摘要和日志：

- `C:\Users\sciman\AppData\Local\vps-ssh-launcher\full-maintenance-runs\20260925T184546Z-bwg-runnow.json`
- `C:\Users\sciman\AppData\Local\vps-ssh-launcher\full-maintenance-runs\20260925T184546Z-bwg-runnow.log`

所有步骤均为 `PASS`：控制面 plan、CPA doctor（pre/post）、v2ray-agent read、RenewTLS
read、vasma/Xray read、系统维护 read、IPv4 read、系统维护 RunNow、updater RunNow、
最终 inventory。远端日志和最后读回显示：

- `monthly-maintenance.sh` 完成 apt 更新、清理、journal vacuum；Docker 容器复验运行，
  Xray 配置与 Nginx 语法通过。
- `dpkg --audit` 为空，`apt-get -s upgrade` 为 `0 upgraded, 0 newly installed,
  0 to remove and 0 not upgraded`，无 `/run/reboot-required`。
- `xray`、`nginx`、`fail2ban`、`docker` 均为 `active`；Xray `run -test` 为
  `Configuration OK`。
- CPA doctor 返回 `DOCTOR_CONTRACT_OK`；本次没有写入 CPA 凭据或 OAuth 状态。

## 验收状态

| 层级 | 状态 | 证据 |
| --- | --- | --- |
| `repo_verified` | PASS | 完整门禁 `189 passed, 1 skipped, 222 subtests passed`；Bandit、Ruff、format、mypy 及 PowerShell/Bash/diff 检查通过 |
| `filesystem_projected` | PASS | pin、RenewTLS 锁 wrapper、updater wrapper/cron 已写入仓库并通过投影门禁 |
| `host_loaded` | PASS | 远端 hash、cron、服务、Xray 配置、包状态和重启标志读回一致 |
| `controlled_live_replay` | PASS | updater check/apply 无变化回放、RenewTLS 锁忙返回 75、统一 RunNow |
| `natural_live_accepted` | NOT CLAIMED | 未用 provider 生成、HTTP 200、doctor 或脚本成功替代真实用户窗口 |

本次结果证明更新链在当前 pinned 版本和当前主机状态下可控、可回滚、不会因“无变化更新”
主动重启代理；不能保证未来上游脚本、provider 账号策略或自然流量永远没有故障。上游
版本刷新必须更新 pin、重新审查、重新投影并重复本 runbook。
