# 2026-10-07 ZZ kernel-sing-box wrapper 收敛证据

## 目标与范围

- 目标：完成双机 wrapper 投影批次的 `zz` 侧收口（用户确认 bwg 正常后
  授权）。`zz` 唯一真实漂移项：kernel-sing-box wrapper 仍为 2026-10-01
  契约版（sha `40c75cec…`，7705 字节，无 status 写入）。
- 范围：**仅 `zz` 的 kernel-sing-box wrapper 投影**。月度维护（原生
  `auto_system_maint.sh` + 受控重启，刻意保留）、google_ipv4（10/1 已
  收口）、script-update 通道（zz 从未部署，非缺陷不扩面）零改动。

## 前置基线（只读）

- `zz` 可达；sing-box `1.14.2`（stable 最新，10/7 定案零漂移）、二进制
  sha `fc9c6e6a…`、服务 active。
- vasma `v3.5.12`（sha `a5f2b2c5…`，与 bwg 的 v3.5.25 不同属既状），
  锚点 `anchors:present`；探针渲染 `syntax-ok`。
- 周更日志 9/11→10/2 连续正常（10/2 `current v1.14.2 equals
  vasma-visible latest; skip reinstall`）——旧版功能正常，仅缺 status。
- google IPv4 契约在位：`conf/config/99_vps_ssh_launcher_google_ipv4.json`
  片段 + 新 wrapper 渲染保留同款 jq 十域断言；月度 cron.d 为原生路径。

## 变更明细

- `vasma_kernel_update_cron.ps1 -Profile zz -Kernel sing-box -Apply
  -Version 1.14.2 -InstalledSha256 fc9c6e6a… -VasmaSha256 a5f2b2c5…`
  （schedule 默认 `20 14 * * 5` 与现役一致）。
- 部署结果：`/etc/v2ray-agent/auto_update_singbox.sh` → 2026-10-07
  15:06、16018 字节、sha `6afba286…`；`TARGET_VERSION` 由渲染器归一化
  为 `v1.14.2`（与版本读取形态一致）；pin 原样传递零升级。
- 备份：`/var/backups/v2ray-agent-maint.fU01am`。

## 后置复验

- cron.d `20 14 * * 5 … auto_update_singbox.sh` 原样保留。
- `write_status` 含 `mkdir -m 700 -p`（fail-open）：周五 14:20 UTC 首跑
  自建 `/var/lib/vps-ssh-launcher/maintenance-status/` 并落
  `kernel-sing-box.status`。
- sing-box 服务 active；apply 内建运行时校验通过（退出 0）。
- 注：`sing-box check -c` 对旧格式配置属已知硬失败路径
  （fail-closed 设计），不作为本机健康判据；以服务态与周更日志为准。

## 边界与回滚

- 不升级 sing-box/vasma（pin 未动）；不触碰月度原生路径与 google
  片段。
- 回滚：`fU01am` 备份目录 + 重跑 wrapper `-Apply` 传回旧参数即可。
- 双机批次至此收口：bwg（4703f7b 证据）+ zz（本文件）。
