# v2ray-agent 管理脚本自动更新 runbook（BWG）

本流程只更新 mack-a/v2ray-agent 的 `/etc/v2ray-agent/install.sh` 管理脚本，
用于长期维护链的脚本层更新。它不执行安装器、不调用菜单 17、不更新 Xray 或
sing-box 二进制，不修改代理配置，也不主动重启代理服务。

## 边界

- 只允许 `bwg`；不连接 `zz`。
- 源仓库固定为 `https://github.com/mack-a/v2ray-agent`，commit 和
  `install.sh` SHA-256 由 `scripts/remote/v2ray-agent-source-pin.json` 管理；远端 URL
  使用该 commit 的 raw 路径，不跟随可变 `master`。
- 上游刷新是显式的仓库变更：先读取官方 commit/raw 文件、审查结构和 hash，更新 manifest
  与 updater，再重新投影；无人值守任务不会自行追踪未来分支头。
- 远端 updater 与 Xray、CPA、月度维护共用
  `/run/vps-ssh-launcher-maintenance.lock`，锁忙立即退出 75，不排队。
- 候选必须通过 HTTPS 下载、大小范围、`bash -n`、版本标记和菜单锚点校验。
- 只允许原子替换管理脚本；候选失败或替换后服务/Xray 配置复验失败时恢复备份。
- 不把成功下载、脚本 hash、服务 active 或 Xray 配置 OK 解释为 provider 质量、
  账号限流或自然用户验收。

## 投影前读盘

先执行只读探针，保存以下事实：

1. `/etc/v2ray-agent/install.sh` 存在、不是符号链接、权限和 owner 正常。
2. 当前脚本 SHA-256、大小、`bash -n` 和版本标记。
3. Xray、Nginx、fail2ban 当前服务状态；Xray `run -test -confdir` 通过。
4. 现有 `/etc/cron.d` 维护行与最近备份目录。

当前 hash 必须作为 `-InstallSha256` 传给 Apply，防止投影期间带外替换了安装器。

## 投影

```powershell
$env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = "1"
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\v2ray_agent_script_update_cron.ps1 `
  -Profile bwg -InstallSha256 <fresh-install.sh-sha256> -Apply
```

Apply 会先备份已有 updater 和 cron，再原子投影：

- `/usr/local/sbin/vps-launcher-v2ray-agent-update.sh`
- `/etc/cron.d/vps-launcher-v2ray-agent-update`

成功应看到 `RUNTIME_VERIFY_OK`、`APPLY_BACKUP_DIR`、`UPDATER_PROJECTED`。失败应看到
`ROLLBACK_VERIFIED`；若出现 `ROLLBACK_FAILED`，立即停止后续维护，使用备份目录人工
恢复并重新执行只读探针。

RenewTLS 迁移由 `scripts/v2ray_agent_renewtls_cron.ps1 -Profile bwg -Apply` 单独完成，
投影 `/usr/local/sbin/vps-launcher-v2ray-agent-renewtls.sh` 和
`/etc/cron.d/vps-launcher-v2ray-agent-renewtls`，移除 root crontab 中旧的
`/etc/v2ray-agent/install.sh RenewTLS` 行。wrapper 也使用共享锁；锁忙返回 75，避免
证书任务与系统/核心/脚本维护并行。

## 受控验收

按以下顺序执行，每一步只使用 BWG：

1. **静态与加载**：远端 `bash -n` updater，cron 文件语法和权限正确，远端脚本 hash
   与仓库投影 hash 一致。
2. **无变化回放**：运行
   `bash /usr/local/sbin/vps-launcher-v2ray-agent-update.sh --check`；应得到
   `CHECK_NO_CHANGE` 或 `CHECK_UPDATE_AVAILABLE`，且 Xray/Nginx/fail2ban 与 Xray
   配置复验通过。
3. **Apply 无变化回放**：候选 hash 与当前脚本一致时运行 `--apply`；应得到
   `NO_CHANGE` 和 `SERVICES_OK`，不应出现 service restart 或配置文件写入。
4. **锁忙负路径**：持有共享 flock 后运行 updater；应立即返回 75，且不改动
   `install.sh` 或 cron。
5. **最终只读门禁**：重新执行 inventory、Xray 配置测试、服务状态和项目相应 doctor。

本流程不强制发送 provider generation；如另有明确的自然流量验收窗口，应把它单独
记录为 `natural_live_accepted`，不能用本 updater 的 `--check` 替代。

## 回滚

Apply 输出的 `/var/backups/v2ray-agent-script-update-deploy.*` 保存投影前 updater
和 cron；自动更新运行时的 `/var/backups/v2ray-agent-script-update.*` 保存旧
`install.sh` 及两份 hash。恢复后至少执行：

```bash
bash -n /etc/v2ray-agent/install.sh
/etc/v2ray-agent/xray/xray run -test -confdir /etc/v2ray-agent/xray/conf
systemctl is-active xray nginx fail2ban
```

若服务本身已经不 active，先按代理服务专用恢复方案处理；不要把本 runbook 变成
无审查的 `install.sh` 重装器。`auto_install.py --execute` 仍是独立的高风险入口，
必须提供 fresh 安装器 hash、备份和人工恢复计划。

## 已知供应链限制

固定官方 commit 和候选 SHA-256 可避免无人值守任务被分支头漂移带偏，但它不是签名验证，
也不替代完整的人工源码审查。若上游脚本结构变化、commit 未进入 manifest，或候选 hash
不匹配，updater 应闭锁并保留现状，等待人工审查后再调整 pin。管理脚本更新成功也不
证明代理账号质量、provider 限流、封号风险或自然用户验收。
