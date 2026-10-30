# 代理伪装与备用传输 lane

本 runbook 记录 BWG/ZZ 上已经审查过的三个可选投影入口。它们只处理
nginx 静态伪装页、REALITY shortId 列表和额外的 Hysteria2 obfs 入站；不修改
v2ray-agent/vasma 上游源码，不轮换 Reality 密钥、UUID、Hysteria2 密码或证书。

## 当前入口

- `scripts/nginx_camouflage_site.ps1`：把 vasma 已生成的 `/usr/share/nginx/html/en`
  伪装页提升到静态根目录。默认只读；`-Apply` 备份整个静态根、运行 `nginx -t`
  并验证根 `index.html` hash。
- `scripts/reality_shortid_harden.ps1`：从 Xray 和 sing-box 的源配置中移除空
  REALITY shortId，保留至少一个非空 shortId；Apply 后执行配置检查、重载服务并
  复验源配置。vasma 重新生成配置后需要重新读取该状态。
- `scripts/hysteria2_obfs_lane.ps1`：在 ZZ 的现有 Hysteria2 之外增加独立 UDP
  obfs 入站，默认端口 `8444`。必须显式提供 `-ObfsPassword`；Apply 会备份源片段
  和 merged 配置，运行 sing-box merge/check、重启服务并确认基础 `443` 与 obfs
  端口都在监听。

所有入口都使用 `/run/vps-ssh-launcher-maintenance.lock`，Apply 前先备份，失败时
尝试恢复并输出 `ROLLBACK_VERIFIED` 或 `ROLLBACK_FAILED`。不应并行执行它们，也不应
把 read-only 输出解释为客户端活体验收。

## 投影前预演

```powershell
$env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = "1"
$target = "$env:APPDATA\vps-ssh-launcher\target.json"

pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\nginx_camouflage_site.ps1 `
  -Config $target -Profile bwg
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\reality_shortid_harden.ps1 `
  -Config $target -Profile bwg
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\hysteria2_obfs_lane.ps1 `
  -Config $target -Profile zz
```

预演应显示 `camouflage=applied`、`shortid_hardening=clean` 和
`obfs_lane=already_configured`；若状态不同，先记录输出和备份路径，再单机 Apply。

## 受控验收

每台主机 Apply 后重新执行 inventory，确认服务状态、配置检查和监听端口；再从授权
客户端执行对应的 REALITY 或 Hysteria2 握手。HTTP `200`、服务 active、配置 hash 或
源片段存在都只能证明低层加载状态，不能替代 `live_accepted`。

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\nginx_camouflage_site.ps1 `
  -Config $target -Profile bwg -Apply
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\reality_shortid_harden.ps1 `
  -Config $target -Profile bwg -Apply
```

ZZ 的 Hysteria2 obfs lane 只有在确实需要备用 UDP 路径时才 Apply；现有 lane 和
订阅保持不变。
