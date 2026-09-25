# 2026-09-26 BWG 系统维护真实执行与 wrapper 优化

## 范围与授权

- 目标：仅 `bwg`；不连接 `zz`。
- 动作：立即真实执行已经投影的系统维护 wrapper，不等待 cron 时间；随后修复一个
  可复现的 apt 依赖过渡缺口并再次真实执行。
- 连接：项目 SSH 入口，严格 host-key 校验；未输出凭据、IP、token、订阅地址或
  Authorization。

## 执行前读盘

2026-09-25T17:57:18Z 的独立 SSH 预检：

- 根盘 40G，已用约 15%，可用约 33G；内存、Swap 正常。
- `xray`、`nginx`、`fail2ban` active；CPA running，restart=0。
- `/run/reboot-required` 不存在。
- `apt-get -s upgrade` 显示 `linux-firmware` 被 kept back；`full-upgrade` 模拟显示
  需要安装拆分的 firmware 依赖，且没有待删除包。

## 第一次真实维护

直接运行 `/usr/local/sbin/monthly-maintenance.sh`，不等待 cron：

- `apt-get update`：成功。
- `apt-get upgrade`：成功，但 `linux-firmware` 保持 kept back。
- `apt-get autoremove --purge`：成功，0 个包可移除。
- `apt-get autoclean`：成功。
- `journalctl --vacuum-time=30d`：执行成功，本次释放 0B。
- Docker 容器、Xray 配置、Nginx 配置和托管服务复验通过；wrapper exit 0。

## 代码优化与投影

原因是 Ubuntu 的 `linux-firmware` 元包升级需要新增拆分依赖，普通
`apt-get upgrade` 会将它保留。仓库 wrapper 改为：

```bash
apt-get upgrade --with-new-pkgs
```

该选项允许安装必要的新依赖，但仍不启用 `full-upgrade` 的删除行为。同步增加了
静态门禁断言。仓库测试与完整门禁结果：

- `test_scripts.py`：60 passed，178 subtests。
- full gate：187 passed，1 skipped，214 subtests；Bandit、Ruff、mypy 全部通过。

2026-09-25T18:00Z 通过 `system_maintenance_cron.ps1 -Profile bwg -Apply` 投影：

- 远端备份：`/var/backups/v2ray-agent-maint.UEoG6I`
- wrapper 语法通过，cron 仍为 `/etc/cron.d/vps-launcher-monthly-maintenance`，
  每月 1 日 14:00 UTC。
- 投影动作只更新 wrapper、cron 和 logrotate，未在投影阶段执行 apt。

## 第二次真实维护

2026-09-25T18:00:38Z 再次直接运行 wrapper，exit 0：

- `apt-get update`：成功。
- `apt-get upgrade --with-new-pkgs`：成功安装 `linux-firmware` 及其 18 个拆分
  依赖包，触发 initramfs 生成。
- `apt-get autoremove --purge`：0 upgraded、0 to remove、0 not upgraded。
- `apt-get autoclean`：成功。
- `journalctl --vacuum-time=30d`：执行成功，本次释放 0B。
- apt 报告无需重启容器；dbus、systemd-logind、unattended-upgrades 的服务重启
  按 needrestart 策略延后；本项目未主动重启这些服务，也未重启主机。

## 独立复验

2026-09-25T18:01:44Z 第二条 SSH 复验：

- `apt-get -s upgrade`：`0 upgraded ... 0 not upgraded`。
- `apt-get -s autoremove --purge`：0 个可移除包。
- `dpkg --audit`：空。
- `reboot_required=no`；`systemctl --failed` 无失败单元。
- `xray`、`nginx`、`fail2ban` active；Xray 配置测试和 `nginx -t` 通过。
- CPA running，restart=0；strict doctor 在 18:02:09Z 返回 `DOCTOR_CONTRACT_OK`。

## 验收边界与回滚

- `repo_verified`：PASS。
- `filesystem_projected`：PASS，wrapper/cron/readback 与仓库新模板一致。
- `host_loaded`：PASS，BWG inventory 可达且 fingerprint 未漂移。
- `controlled_live_replay`：PASS，系统维护两次真实运行均 exit 0，第二次完成
  firmware 依赖升级。
- `natural_live_accepted`：未宣称；系统维护成功不等于 provider 质量或自然流量验收。
- 远端 wrapper/cron/logrotate 回滚使用上述 `/var/backups/v2ray-agent-maint.UEoG6I`；
  package 状态回滚依赖 apt/dpkg 自身历史，不执行主机重启回滚。

## 未处理项

CPA OAuth 仍处于非阻断续期窗口（doctor 约 66 小时剩余，刷新失败为 0）；按照
OAuth runbook 等待自动刷新，不自动登出、删除凭据或启动 device-login。
