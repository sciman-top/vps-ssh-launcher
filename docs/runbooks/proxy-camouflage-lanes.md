# 代理伪装与备用传输 lane

本 runbook 记录 BWG/ZZ 上已经审查过的四个可选投影入口。它们只处理
nginx 静态伪装页、REALITY shortId 列表、额外的 Hysteria2 obfs 入站和 BWG/ZZ
Hysteria2 base 的 masquerade；不修改
v2ray-agent/vasma 上游源码，不轮换 Reality 密钥、UUID、Hysteria2 密码或证书。

## 当前入口

- `scripts/nginx_camouflage_site.ps1`：把 vasma 已生成的 `/usr/share/nginx/html/en`
  伪装页提升到静态根目录。默认只读；`-Apply` 备份整个静态根、运行 `nginx -t`
  并验证根 `index.html` hash。
- `scripts/reality_shortid_harden.ps1`：从 Xray 和 sing-box 的源配置中移除空
  REALITY shortId，保留至少一个非空 shortId；Apply 后执行配置检查、重载服务并
  复验源配置。vasma 重新生成配置后需要重新读取该状态。
- `scripts/hysteria2_obfs_lane.ps1`：在主机现有 Hysteria2 之外增加独立 UDP
  obfs 入站，默认端口 `8444`（zz 原生车道与 bwg 双核加装后的 Hysteria2 均适用；
  presence-gated，无 hysteria2 的主机自动 `not_applicable`）。必须显式提供
  `-ObfsPassword`；Apply 会备份源片段
  和 merged 配置，运行 sing-box merge/check、重启服务并确认基础 `443` 与 obfs
  端口都在监听。
- `scripts/hysteria2_masquerade_lane.ps1`：用于 BWG/ZZ，把 Hysteria2 base `443`
  的未认证探测代理到各自现有的 nginx 伪装 vhost，并投影 `97_` info 日志片段；
  默认目标分别是 BWG `fq.sciman.top:33387` 与 ZZ `zz.sciman.top:12378`。
  obfs 车道不做 masquerade，因为 salamander 会在协议层之前拒绝未混淆报文。

所有入口都使用 `/run/vps-ssh-launcher-maintenance.lock`，Apply 前先备份，失败时
尝试恢复并输出 `ROLLBACK_VERIFIED` 或 `ROLLBACK_FAILED`。不应并行执行它们，也不应
把 read-only 输出解释为客户端活体验收。

## 漂移门禁（vasma 重装后必查）

vasma 重装或菜单里重新配置协议时，`initXrayConfig`/`initSingBoxConfig`/`nginxBlog`
会重新生成 06/07 源配置和静态根，把空 shortId 与默认页装回去。此时 Xray/sing-box
服务照常 active、订阅照常可用，**没有任何告警**，只有主动探测面变宽了。

```powershell
pwsh -NoProfile -File .\scripts\proxy_hardening_drift.ps1 -Profile bwg   # 或 zz
```

判据是最后一行 `HARDENING_DRIFT=NONE`（退出 0）；出现
`HARDENING_DRIFT=camouflage,reality-shortid,reality-target,xray-port-collision,hysteria2-obfs,hysteria2-masquerade`
的任一子集即退出 3，
按列出的 lane 名重跑对应 `-Apply`。该脚本只读，不获取锁、不重启服务，可安全放进
日常巡检。`hysteria2-masquerade` 会同时校验唯一的 UDP 443 base inbound、精确的
profile 默认 HTTPS 目标和 `rewrite_host=true`；若曾用 `-MasqueradeUrl` 指定自定义目标，
巡检时也必须传同一个 `-MasqueradeUrl`，否则会按默认目标报告漂移。

`reality-target` 校验 Xray 与 sing-box 两侧 REALITY 的回落目标：`target`/`dest` 的
主机名必须等于 `-MasqueradeUrl` 的主机名，`serverNames`/`server_name` 也必须包含它。
vasma 全量重装会按 `realityServerName:realityDomainPort` 重写这两个字段，若在
“是否使用本域名作为 Reality 目标域名”处答 `n`，它会挑一个随机公网站点，443 上就会用
别人的证书回应 `fq.sciman.top`，既是成人可辨的 REALITY 特征，也让主机变成该站点的
转发器。出现该漂移时重跑一次 vasma 并答 `y`（目标端口即订阅/伪装 vhost 端口），或把
两个来源文件的 `target`/`serverNames` 改回本域名后 `xray run -test` 并重启。端口不做
断言：bwg 回落 `33387`、zz 回落 `35622`，只要主机名一致即可。

## 21470 双绑定退役（vasma 重装后必查）

bwg 的 `02_VLESS_TCP_inbounds.json` 与 `12_VLESS_XHTTP_inbounds.json` 曾同时
监听 `21470`。内核按四元组随机分发，导致 XHTTP 客户端偶发命中 TLS inbound。
若漂移门禁输出 `xray-port-collision`，先备份 `02` 文件并改名为
`02_VLESS_TCP_inbounds.json.disabled`，执行 `xray run -test`、单次重启，再用
XHTTP 客户端连续探测；确认 `21470` 仅一个监听后，按 vasma 订阅刷新流程移除旧
VLESSTCP 节点。完整备份、迁移和验收证据见
[20261031 bwg lane review closeout](../change-evidence/20261031-bwg-lane-review-closeout.md)。

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
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\hysteria2_masquerade_lane.ps1 `
  -Config $target -Profile bwg
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\hysteria2_masquerade_lane.ps1 `
  -Config $target -Profile zz
```

预演应显示 `camouflage=applied`、`shortid_hardening=clean` 和
`obfs_lane=already_configured`；若状态不同，先记录输出和备份路径，再单机 Apply。

## 受控验收

每台主机 Apply 后重新执行 inventory，确认服务状态、配置检查和监听端口；再从授权
客户端执行对应的 REALITY 或 Hysteria2 握手。HTTP `200`、服务 active、配置 hash 或
源片段存在都只能证明低层加载状态，不能替代 `live_accepted`。

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\proxy_lane_acceptance.ps1 `
  -Config $target -Profile bwg -PublicName fq.sciman.top -RealityPublicPort 443 `
  -ObfsPassword '<obfs-password>'
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\nginx_camouflage_site.ps1 `
  -Config $target -Profile bwg -Apply
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\reality_shortid_harden.ps1 `
  -Config $target -Profile bwg -Apply
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\hysteria2_masquerade_lane.ps1 `
  -Config $target -Profile bwg -Apply
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\hysteria2_masquerade_lane.ps1 `
  -Config $target -Profile zz -Apply
```

`proxy_lane_acceptance.ps1` 的 `-PublicName` 与 `-RealityPublicPort` 会额外通过公网 DNS 名和明确的公开 Reality 端口建立
Reality、Hysteria2 base、Hysteria2 obfs 客户端连接；未提供时只做 loopback 检查。
同一轮还会输出 `reality-fallback`（未认证探测是否仍返回伪装页 hash）、
`xhttp-reality`（21470）与 `xhttp-tls`（10094，经 dokodemo 包装端口拨号）三条 lane，
因此订阅里的 3 个 VLESS 节点都会被真实握手覆盖，而不是只验 Vision 那一条。
它仍属于从主机发起的受控验收，不等于中国网络下真实客户端或运营商的自然验收。
ZZ 的 Reality 公开端口当前为 `22752`，因此该参数应传
`-PublicName zz.sciman.top -RealityPublicPort 22752`，不能沿用 BWG 的 `443`。

ZZ 的 Hysteria2 obfs lane 只有在确实需要备用 UDP 路径时才 Apply；现有 lane 和
订阅保持不变。

## 已知残余指纹（尚未自动修复）

- 10094 的 XHTTP TLS 车道是裸 Xray 监听：任意路径返回无 `Server`/`Content-Type` 头的空
  `HTTP/2 404`，与同域名 nginx 的 404 不一致。XHTTP 官方文档要求“将 XHTTP 隐藏在真正的
  Nginx、Caddy 后面以减少指纹特征”；若要收敛，应让 nginx 在 10094 终止 TLS、把
  `location ^~ /nzcaxHTTP` 反代到 127.0.0.1 的 XHTTP inbound，并把该 inbound 改为
  `security: none`。客户端订阅字段不变，但需要改动 vasma 生成的 `14_` 文件，属远端写入。
- nginx 仍输出 `Server: nginx/1.31.7`；在 http 块加 `server_tokens off;` 即可。
- 33387（含 8443/28317）对任意 SNI 都回 `*.sciman.top` 证书；给那几个端口加
  `ssl_reject_handshake on;` 的 default_server 会更接近严格站点，同时避免被当作到伪装站
  的一般转发器。
