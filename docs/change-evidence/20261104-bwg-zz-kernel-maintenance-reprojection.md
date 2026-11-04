# 2026-11-04 BWG/ZZ 内核安全更新与月度维护重投影

## 范围

- 真实远端写入：逐台重投影月度维护 wrapper/cron，安装
  `linux-image-generic 6.8.0-147.147`，随后逐台重启并复验。
- 未修改代理协议、UUID、REALITY 密钥/shortId、Hysteria2 口令、证书、
  Cloudflare token、CPA/OAuth 凭据或代理并发策略。
- 代码真源：`ca9256c`（内核元包跟踪与 Hysteria2 证书校验）、`022dd27`
  （zz 受控自动重启策略的显式迁移）。

## 初始问题

- bwg/zz 均运行 `6.8.0-107-generic`，Ubuntu 安全仓库候选为
  `6.8.0-147.147`。
- 两台主机均未安装内核元包，普通 `apt-get upgrade --with-new-pkgs` 无法跟随
  ABI 包名变化，月初维护连续显示无可升级。
- zz 原月度 cron 指向 vasma 原生脚本并带
  `AUTO_REBOOT_ON_MAINT=1 AUTO_REBOOT_DELAY_MIN=15`；简单重投影会丢失该
  既有控制语义。
- Hysteria2 验收客户端曾设置 `insecure=true`，会掩盖证书链或 SNI 错误。

## bwg

1. 重投影：
   `system_maintenance_cron.ps1 -Profile bwg -Apply`
   - 备份：`/var/backups/v2ray-agent-maint.Ndo8jS`
   - cron：`0 14 1 * * root /bin/bash /usr/local/sbin/monthly-maintenance.sh`
   - 重启策略：不自动重启。
   - 幂等重投影备份：`/var/backups/v2ray-agent-maint.cy2r1p`。
2. 维护事务：
   `/bin/bash /usr/local/sbin/monthly-maintenance.sh`
   - 退出码 0；内核元包安装、通用升级、Docker/代理服务复验通过。
   - `KERNEL_REBOOT_REQUIRED=pending running=6.8.0-107-generic installed=6.8.0-147-generic`
   - `REBOOT_REQUIRED=pending age_days=0`
3. 受控重启后：
   - 运行内核 `6.8.0-147-generic`，`/run/reboot-required` 消失。
   - Xray、sing-box、nginx、Docker、fail2ban 均 active；三个配置测试均 PASS。
   - `HARDENING_DRIFT=NONE`。
   - 完整代理验收 PASS：REALITY Vision/主备 SNI/回落、XHTTP REALITY、
     XHTTP TLS、CDN WS、Hysteria2 base/obfs（含证书校验）。
   - 首次冷启动验收曾出现 REALITY 三路 `000`；服务、监听与配置均正常，
     定向客户端复测为 204，随后完整验收 PASS。未改配置或重启服务。

## zz

1. 重投影：
   `system_maintenance_cron.ps1 -Profile zz -PreserveAutoReboot -Apply`
   - 备份：`/var/backups/v2ray-agent-maint.NjIOeV`
   - cron：
     `0 14 1 * * root AUTO_REBOOT_ON_MAINT=1 AUTO_REBOOT_DELAY_MIN=15 /bin/bash /usr/local/sbin/monthly-maintenance.sh`
   - 自动重启仅在维护与代理健康检查全部成功、且系统存在重启要求时调度；
     延迟限制为 1..60 分钟。
   - 幂等重投影备份：`/var/backups/v2ray-agent-maint.u8ZgLF`。
2. 维护事务：
   `/bin/bash /usr/local/sbin/monthly-maintenance.sh`
   - 退出码 0；内核元包安装、通用升级与代理服务复验通过。
   - `KERNEL_REBOOT_REQUIRED=pending running=6.8.0-107-generic installed=6.8.0-147-generic`
   - `REBOOT_REQUIRED=pending age_days=0`
3. 受控重启后：
   - 运行内核 `6.8.0-147-generic`，`/run/reboot-required` 消失。
   - Xray、sing-box、nginx、Docker、fail2ban 均 active；三个配置测试均 PASS。
   - `HARDENING_DRIFT=NONE`。
   - 完整代理验收 PASS，并额外通过 UDP `20001 -> 8444` 端口跳跃。

## 公共状态

- 两机 `net.mptcp.enabled=0`，Xray 仍为最新 `26.10.31`、sing-box `1.14.4`。
- 两机均保留旧 `6.8.0-107-generic` 与新 `6.8.0-147-generic` 内核包，后者正在运行。
- 四个公网伪装/订阅入口从当前本地网络均返回 HTTP 200。
- 当前证据属于 `host_loaded` 与受控握手；不等同于中国运营商长期自然流量、
  QoS 或整域封锁验收。

## 回滚

- 月度 wrapper/cron：恢复对应 `v2ray-agent-maint.*` 备份目录中的
  `maintenance-wrapper`、`cron-file`、`logrotate-file` 与 `crontab`。
- 内核：旧内核包和 `/boot` 文件仍保留；若新内核异常，在对应主机 GRUB 选择
  `6.8.0-107-generic`，单机重启并复验服务、端口和代理链路。
- 本次未修改代理配置；代理链路无需随月度维护回滚。
