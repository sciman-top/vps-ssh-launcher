# 代理内核人工回滚（xray / sing-box）

wrapper 的自动回滚只覆盖升级事务内检测到的失败（版本/SHA-256/配置测试/
服务复验任一不过 → 从 `/var/backups/v2ray-agent-core-update.XXXXXX`
恢复二进制并报告 `ROLLBACK_VERIFIED`）。本页处理事务之外的人工回滚：升级
复验通过、cron 也报成功，但事后发现连接质量、路由或兼容性问题。

以下命令以 systemd 为例；OpenRC 主机将 `systemctl is-active/restart/start`
替换为对应的 `rc-service <unit> status/restart/start`，并保持同样的配置测试和
端口复验顺序。

## 前置

- 取共享锁（`exec 9>/run/vps-ssh-launcher-maintenance.lock; flock -n 9`）；
  zz 是例外：与原生月度脚本共用旧锁 `/run/v2ray-agent-maint.lock`。
- 找到上次 `-Apply` 输出的 `APPLY_BACKUP_DIR`（默认
  `/var/backups/v2ray-agent-core-update.<10位随机>`，目录内是升级前二进制）。
- 逐台纪律：单机执行、第二条 SSH 复验、确认联网正常后才动下一台。

## xray 回滚步骤

1. 记录现状（证据）：

   ```bash
   /etc/v2ray-agent/xray/xray version | head -1
   sha256sum /etc/v2ray-agent/xray/xray
   systemctl is-active xray
   ```

2. 恢复备份二进制并重启：

   ```bash
   systemctl stop xray
   cp -a /var/backups/v2ray-agent-core-update.<dir>/xray /etc/v2ray-agent/xray/xray
   systemctl start xray
   ```

3. 复验（第二条 SSH）：`xray run -test -confdir /etc/v2ray-agent/xray/conf`
   通过、`systemctl is-active xray` active、`ss -ltn` 端口在位、代理连通。
4. 收尾：把 wrapper 的 pin 改回旧版本号 + 旧二进制 SHA-256（重新
   `-Apply` 投影 wrapper），否则下个周五 cron 会把刚回滚的版本又升上去。

## sing-box 回滚步骤

1. 记录现状：`/etc/v2ray-agent/sing-box/sing-box version`、
   `systemctl is-active sing-box`、`sha256sum` 同上。
2. 恢复备份二进制与配置目录：

   ```bash
   systemctl stop sing-box
   cp -a /var/backups/v2ray-agent-core-update.<dir>/sing-box \
     /etc/v2ray-agent/sing-box/sing-box
   rm -rf /etc/v2ray-agent/sing-box/conf
   cp -a /var/backups/v2ray-agent-core-update.<dir>/conf \
     /etc/v2ray-agent/sing-box/conf
   systemctl start sing-box
   ```

   新 wrapper 的 `APPLY_BACKUP_DIR` 包含 `conf/`；如果旧备份没有该目录，先
   停止服务并从单独的配置备份恢复，禁止用空目录覆盖现网配置。
3. **配置是源片段与合并产物**：vasma 的 sing-box 安装/升级菜单会经
   `singBoxMergeConfig` 删除并重建 `conf/config.json`。恢复后先按
   [sing-box 配置迁移](singbox-core-update-migration.md) 的反向方向确认旧格式，
   再用旧二进制重建并检查：

   ```bash
   /etc/v2ray-agent/sing-box/sing-box merge config.json \
     -C /etc/v2ray-agent/sing-box/conf/config/ \
     -D /etc/v2ray-agent/sing-box/conf/
   /etc/v2ray-agent/sing-box/sing-box check \
     -c /etc/v2ray-agent/sing-box/conf/config.json
   ```

   不能只回滚 merged `config.json`；`conf/config/` 下的源片段才是下次 vasma
   合并的输入。
4. `ipv4_only` 路由规则检查：当前 wrapper 缺失时会写入并持久保留
   `/etc/v2ray-agent/sing-box/conf/config/99_vps_ssh_launcher_ipv4_only.json`，
   然后重新 merge。确认该源片段存在、SHA-256 已记录，且 merged 配置包含
   `{"action":"resolve","strategy":"ipv4_only"}`；事务外手工操作后也按同样
   顺序重新 merge 和 check。
5. 复验同 xray 第 3 步；收尾同样把 pin 改回旧版本并重投影 wrapper。

## wrapper 重投影纪律

- 回滚后立即把 `-Version`/`-InstalledSha256`（`-Sha256` 仍是兼容别名）pin 重置为
  回滚后实际运行的版本，并重新读取 `-VasmaSha256`，再执行：
  `vasma_kernel_update_cron.ps1 -Profile <profile> -Kernel <xray|sing-box>
  -Version <old-version> -InstalledSha256 <old-binary-sha256>
  -VasmaSha256 <current-vasma-sha256> -Apply`；否则 cron 的"latest 漂移
  UNVERIFIED"或重装逻辑会覆盖人工回滚。
- **zz 禁用当前仓库模板重投影**（模板锁路径硬编码为统一新锁，会破坏 zz 与
  原生月度脚本的互斥），且 zz 旧 wrapper 无锚点预检/无 pin 强制，人工步骤
  是唯一防线。
- vasma 菜单手动操作（sing-box 安装/升级或配置菜单）可能重建合并配置——任何
  事务外手动操作之后都按第 sing-box 步骤 4 重新 merge、check 并复查 `ipv4_only`
  源片段。

## 禁止

- 不用 vasma 菜单"再升一次"来修复坏升级：先回滚到已知好版本，再规划变更。
- 不跳过收尾的 pin 重投影：只回滚二进制会让周五 cron 自动升回坏版本。
- 不并行对两台主机执行回滚。
