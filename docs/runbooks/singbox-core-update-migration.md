# sing-box 内核升级前的配置迁移（1.12+）

## 适用场景

sing-box lane 的首次 pin 升级（当前部署基线 < 1.12，目标 ≥ 1.12）之前，必须先人工
迁移远端 sing-box 配置。`vasma_kernel_update_cron.ps1 -Kernel sing-box -Apply` 投影的
wrapper 会 fail-closed 拦截不兼容组合，这是安全网，不是缺陷；本文是绕过该拦截前的
前置人工步骤。

## 背景

- sing-box 1.11 弃用 legacy special outbounds（`block`/`dns` 等），1.13 移除。
- sing-box 1.12 弃用 legacy `geoip`/`geosite` 规则（改 rule-set `.srs`），并重构 DNS
  配置；1.12 移除 legacy TUN 地址字段。
- 上游 v2ray-agent 已于 2026-08-31（7b151c9，fork 已同步）把生成的 sing-box 配置
  迁到 1.14 格式；旧版脚本生成的存量配置不会自动迁移。

## 症状

wrapper 日志 `/etc/v2ray-agent/crontab_singbox_update.log` 出现
`sing-box check -c` 失败 → 恢复旧二进制 → `ROLLBACK_VERIFIED`，退出码 11。版本与
SHA-256 验证已通过时仍回滚，说明是配置不兼容而非下载问题。

## 迁移步骤（人工、单机、备份优先）

1. 只读探针：记录当前版本
   `/etc/v2ray-agent/sing-box/sing-box version`、服务状态
   `systemctl is-active sing-box`，并用当前二进制跑
   `sing-box check -c /etc/v2ray-agent/sing-box/conf/config.json` 确认现状干净。
2. 备份配置目录：
   `cp -a /etc/v2ray-agent/sing-box/conf /var/backups/sing-box-conf-$(date -u +%Y%m%dT%H%M%SZ)`。
3. 下载目标版本二进制到 `/tmp`（不改服务），对**候选配置**逐项迁移并用新二进制
   验证：`/tmp/sing-box check -c /tmp/config.candidate.json`。迁移项按官方文档
   <https://sing-box.sagernet.org/migration/>：
   - legacy special outbounds → rule actions；
   - legacy `geoip`/`geosite` 规则 → remote rule-set（`.srs`）；
   - DNS 配置按 1.12 重构后的 `servers`/`rules` 结构重写；
   - TUN 地址字段如使用旧名，改为新字段。
   现网配置含 `action: resolve, strategy: ipv4_only` 的分流规则（wrapper 的
   `ensure_ipv4_only_route` 也可能补插），迁移后保留其语义。
4. 候选配置通过新二进制 `check` 后，替换远端配置文件（保留备份），再执行
   `-Kernel sing-box -Apply` 的 pin 升级；wrapper 会安装新二进制、重启并按
   版本 + SHA-256 + `check` 复验。
5. 复验：第二条 SSH 命令确认 `systemctl is-active sing-box`、监听端口
   （`ss -ltn`）与代理连通性；只有确认联网正常后才处理下一台。

## 禁止

- 不跳过第 3 步直接 pin 新内核：wrapper 会回滚，浪费一次变更窗口。
- 不在迁移窗口并行执行其他维护（共享 `/run/vps-ssh-launcher-maintenance.lock`
  互斥会拒绝，但仍应保持单事务）。
- 不把本文步骤自动化为无人值守流程：配置迁移含语义判断，必须人工复核。
