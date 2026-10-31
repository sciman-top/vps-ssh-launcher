# 代理伪装与备用传输 lane

本 runbook 记录 BWG/ZZ 上已经审查过的六个可选投影入口。它们只处理
nginx 静态伪装页（含 stock `50x.html` 清理）、REALITY shortId 列表、额外的
Hysteria2 obfs 入站、Hysteria2 base 的 masquerade、nginx 指纹与 XHTTP TLS 前置；
不修改
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
- `scripts/nginx_tls_stealth.ps1`：`server_tokens off` 去掉 `Server: nginx/<版本>`，
  并给每个 REALITY 回落端口加 `ssl_reject_handshake on` 的 `default_server`，让未知 SNI
  握手失败而不是回本域名证书。端口从在线 REALITY 配置推导（bwg `33387`、zz `35622`），
  只 `nginx -s reload`，不重启服务；Apply 后必须同时验证真 SNI 仍可用、未知 SNI 被拒、
  `Server` 头无版本，任一不满足即回滚。
- `scripts/xhttp_nginx_front.ps1`：把 XHTTP TLS 车道从裸 Xray 监听改为
  `dokodemo 0.0.0.0:<公开端口> → 127.0.0.1:15994(nginx) → 127.0.0.1:<inbound>`。公开端口必须
  留在 Xray 的 `inbounds[1]` 上：vasma 用 `inbounds[1].port` 取订阅端口，菜单重配还会重写
  这个 dokodemo；若 nginx 直接占用公开端口，重配时两者抢端口会把 xray 启不起来。nginx 在
  回环端口用同一证书终止 TLS、未知路径回落伪装页、未知 SNI 拒绝，`location ^~ <path>` 反代
  到内层（内层降为 `security: none`）。客户端订阅字段不变。生成物由单一模板渲染并与远端
  文件逐字节比对，模板变化会报 `xhttp_front_template=stale` 并按 drift 重写；只有入站 JSON
  真的变化（或 xray 不在跑）才 restart xray，纯模板修正只 reload nginx。前置不再转发
  `X-Real-IP` / `X-Forwarded-For`：内层入站没有 `sockopt.trustedXForwardedFor`，转发会被
  Xray 忽略并给每个请求写一条 warning 到 `error.log`。从旧版“nginx 直占公开端口”迁移时
  还要 restart 一次 nginx 才能真正释放端口。

所有入口都使用 `/run/vps-ssh-launcher-maintenance.lock`，Apply 前先备份，失败时
尝试恢复并输出 `ROLLBACK_VERIFIED` 或 `ROLLBACK_FAILED`。不应并行执行它们，也不应
把 read-only 输出解释为客户端活体验收。

## 车道与订阅汇总

| 主机 | 车道 | 公开端口 | 在订阅中 | 对应漂移族 |
| --- | --- | --- | --- | --- |
| bwg | VLESS+REALITY+Vision | TCP 443 | 是 | reality-shortid / reality-target / nginx-stealth |
| bwg | VLESS+REALITY+XHTTP | TCP 21470 | 是 | 同上 + xray-port-collision |
| bwg | VLESS+TLS+XHTTP（nginx 前置） | TCP 10094（dokodemo→15994→45988） | 是 | xhttp-front |
| bwg | Hysteria2 base | UDP 443 | 是 | hysteria2-masquerade |
| bwg | Hysteria2 obfs | UDP 8444 | 否（手工配置） | hysteria2-obfs |
| zz | VLESS+REALITY（sing-box） | TCP 22752 | 是 | reality-shortid / reality-target / nginx-stealth |
| zz | Hysteria2 base | UDP 443 | 是 | hysteria2-masquerade |
| zz | Hysteria2 obfs | UDP 8444 | 否（手工配置） | hysteria2-obfs |

订阅发布由 vasma 托管（菜单 `7→6→y`，salt 复用、URL 不变）；bwg 现为
3×VLESS + 1×Hysteria2 共 4 节点，zz 单 Reality 车道。公开 XHTTP 端口必须留在
xray 的 dokodemo 上：vasma 用 `inbounds[1].port` 取订阅端口，nginx 直占公开端口
会在菜单重配时与 xray 抢端口。

## 漂移门禁（vasma 重装后必查）

vasma 重装或菜单里重新配置协议时，`initXrayConfig`/`initSingBoxConfig`/`nginxBlog`
会重新生成 06/07 源配置和静态根，把空 shortId 与默认页装回去。此时 Xray/sing-box
服务照常 active、订阅照常可用，**没有任何告警**，只有主动探测面变宽了。

```powershell
pwsh -NoProfile -File .\scripts\proxy_hardening_drift.ps1 -Profile bwg   # 或 zz
```

判据是最后一行 `HARDENING_DRIFT=NONE`（退出 0）；出现
`HARDENING_DRIFT=camouflage,reality-shortid,reality-target,nginx-stealth,xhttp-front,xray-port-collision,hysteria2-obfs,hysteria2-masquerade`
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

REALITY 目标站当前为本域伪装 vhost（bwg `fq.sciman.top:33387`）：同 IP、同证书、
回落页与真实站点完全一致，主动探测者拿不到可区分响应。若未来必须更换 dest，按
官方 README 的判据选：TLS 1.3 + H2、无重定向、离服务器近、非被墙 SNI（被墙域名
作为 serverNames 会在 ClientHello 阶段就被 RST）、支持 OCSP；注意 mldsa65 需要
回落目标证书 >3500 字节。改 dest 属双核联动写操作，须走单机 apply + 验收批次。

## UDP/QUIC 阻断预案

两条 Hysteria2 车道（UDP 443 base + UDP 8444 obfs）对“UDP 被区域性封锁/限速”的
防线分层：

1. **端口级封锁**（只封 443/udp）：8444 obfs 备用车道不受影响，客户端切 obfs 节点。
2. **UDP 全封或 QoS**：回退 TCP 车道（bwg 443 REALITY Vision / 21470 REALITY
   XHTTP / 10094 XHTTP TLS；zz 22752 Reality），订阅全量可用。
3. **端口跳跃（port hopping）评估**：Hysteria 官方对“UDP 单端口被封”的推荐对策是
   服务端把一个 UDP 端口段 DNAT/REDIRECT 到 443（`iptables -t nat -A PREROUTING
   -i <if> -p udp --dport 20000:50000 -j REDIRECT --to-ports 443`，持久化由
   nftables/netfilter-persistent 承担），客户端 sing-box 出站配 `server_ports` 端口
   段与 `hop_interval`（官方默认 30s，15–45s 随机区间更抗周期识别；全 UDP 被封时
   跳跃无效，官方替代是 Mimic/Fake TCP，与 obfs 互斥）。**当前不实施**：尚无本机
   UDP 被封锁的实测证据；实施会新增防火墙持久化面与客户端手工配置（obfs 同样
   不进订阅），且需与 docker 的 nat 链共存预演。触发条件：实测 443/udp 或
   8444/udp 出现区域性封锁、obfs 车道实测劣化时，作为独立批次立项（第 7 条硬化
   车道 + drift 检查 + 逐台 apply）。

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
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\nginx_tls_stealth.ps1 `
  -Config $target -Profile bwg
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\xhttp_nginx_front.ps1 `
  -Config $target -Profile bwg
```

预演应显示 `camouflage=applied`、`shortid_hardening=clean` 和
`obfs_lane=already_configured`、`stealth=applied`、`xhttp_front=applied`；若状态不同，
先记录输出和备份路径，再单机 Apply。

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
`-ObfsPassword` 从本机 `%APPDATA%\vps-ssh-launcher\` 下的 obfs 口令文件读取
（文件名见 20261030 台账），不要把口令写进命令历史或脚本。
同一轮还会输出 `reality-fallback`（未认证探测是否仍返回伪装页 hash）、
`xhttp-reality`（21470）与 `xhttp-tls`（10094，经 dokodemo 包装端口拨号）三条 lane，
因此订阅里的 3 个 VLESS 节点都会被真实握手覆盖，而不是只验 Vision 那一条。
它仍属于从主机发起的受控验收，不等于中国网络下真实客户端或运营商的自然验收。
ZZ 的 Reality 公开端口当前为 `22752`，因此该参数应传
`-PublicName zz.sciman.top -RealityPublicPort 22752`，不能沿用 BWG 的 `443`。

ZZ 的 Hysteria2 obfs lane 已于 2026-10-30 应用并纳入漂移门禁（base 存在时缺装即
报 `hysteria2-obfs` 漂移），现有 lane 和订阅保持不变。

## 验收判定的 N/A 语义

`proxy_lane_acceptance.ps1` 的 verdict 遵循两级 N/A：真正按主机架构缺席的可选
车道（如 zz 无 XHTTP、无 sing-box 的主机无 Hysteria2）记 `RESULT=N/A` 且不降级，
单机可到 `PASS`；适用于本机但无法探测的车道（REALITY 存在但伪装根缺失、
sing-box 在而 base inbound 缺失）记 `RESULT=N/A` 但触发 `PARTIAL`，使半缺失
状态显形。`FAIL` 恒为真实握手失败或源不可读。

## 已收敛与被放弃的指纹

- 裸 XHTTP 监听、nginx 版本头、未知 SNI 回本域名证书三项已由上面两个 lane 收敛；bwg 的
  `10094` 现为 `/`→伪装页 200、其他路径→nginx 404、未知 SNI 拒绝，隧道仍由验收覆盖。
- 伪装根的 stock `50x.html` 已纳入 camouflage 车道清理与漂移门禁：`-Apply` 在整根
  备份后移除（被 error_page 引用则保留并声明），vasma/nginx 重装带回即报
  `camouflage` 漂移。根目录另有约 60MB 站点模板制作残留（`html*.zip`、`psd/`、
  `__MACOSX/` 等），不属于 stock 指纹但同样不是真实站点形态；清理属用户授权的
  独立远端写项，车道不擅动用户内容。
- 8443（CPA 网关）与 28317（CPAMC 管理面板）只做了 `server_tokens off`，没有加未知 SNI
  拒绝：它们可能被按 IP 字面量访问，拒绝未知 SNI 会误伤这类客户端。
- REALITY 的 `mldsa65` 保持关闭：官方要求回落目标证书 >3500 字节且支持 X25519MLKEM768，
  当前证书链 3393 字节、OpenSSL 3.0 无 ML-KEM，强行启用反而成了新指纹。
- Hysteria2 的 obfs 口令与认证口令同值、`8444/udp` 属常见默认端口、base 车道
  `up/down_mbps 100/50` 为显式上限。这些改动会破坏现有客户端配置或收紧带宽，未经单独
  授权不做。
