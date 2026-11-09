# 代理伪装与备用传输 lane

本 runbook 记录 BWG/ZZ 上已经审查过的可选投影入口（当前清单见"当前入口"
一节，新增车道时随节更新，不在此处钉计数）。它们只处理
nginx 静态伪装页（含 stock `50x.html` 与模板制作残留清理）、REALITY shortId 列表、
额外的 Hysteria2 obfs 入站、Hysteria2 base 的 masquerade、nginx 指纹、XHTTP TLS
前置（含 zz 变体）、CDN 回落与跨机证书同步、聚合订阅分发与 REALITY 的第二主机名；
另以独立 nftables 表建立默认拒绝防火墙和 UDP 端口跳跃；不修改
v2ray-agent/vasma 上游源码，不轮换 Reality 密钥、UUID、Hysteria2 认证密码或证书。

## 当前入口

- `scripts/nginx_camouflage_site.ps1`：把 vasma 已生成的 `/usr/share/nginx/html/en`
  伪装页提升到静态根目录。默认只读；`-Apply` 备份整个静态根、运行 `nginx -t`
  并验证根 `index.html` hash。
- `scripts/reality_shortid_harden.ps1`：从 Xray 和 sing-box 的源配置中移除空
  REALITY shortId，保留至少一个非空 shortId；Apply 后执行配置检查、重载服务并
  复验源配置。vasma 重新生成配置后需要重新读取该状态。
- `scripts/reality_alt_sni_lane.ps1`：给在线 REALITY 入站追加第二个 `serverNames`
  主机名（默认 bwg `static.sciman.top`），并由 conf.d 的独立 vhost 用同一张通配符
  证书终止 TLS。主 SNI 仍排第一，已发布的节点和客户端不受影响。前提是该名字已有
  DNS A 记录指向本机，脚本对未解析或解析到别处直接拒绝。Apply 备份配置、`nginx -t`
  与 `xray -test` 通过后才 reload/restart，并用带新 SNI 的真实握手取一次出口 IP
  作为验收，失败自动回滚。它只扛“精确主机名被 SNI 封锁”：IP 封锁仍靠 CDN 车道，
  整段域名封锁需要另有一个独立注册域，脚本不解决。
- `scripts/hysteria2_obfs_lane.ps1`：在主机现有 Hysteria2 之外增加独立 UDP
  obfs 入站，默认端口 `8444`（zz 原生车道与 bwg 双核加装后的 Hysteria2 均适用；
  presence-gated，无 hysteria2 的主机自动 `not_applicable`）。认证口令沿用 base
  Hysteria2 用户，salamander 使用独立的 CSPRNG v2 口令；首次 Apply 自动生成并保存到
  `%APPDATA%\vps-ssh-launcher\<profile>-hysteria2-obfs-password-v2.txt`。Apply 会备份源片段
  和 merged 配置，运行 sing-box merge/check、重启服务并确认基础 `443` 与 obfs
  端口都在监听。dry-run 不创建、不改写也不修改口令文件 ACL；Apply 先在目标目录生成
  私有临时件并完成 ACL 校验，远端成功后才原子替换正式文件，远端失败则回收临时件。
  远端成功但本地发布仍失败时会保留该私有暂存件并在错误中给出路径，避免现场已换新口令
  而本机没有可恢复副本；修复本地问题后可由操作者核对并重新投影。
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
  TLS-XHTTP 订阅节点固定发布 `mode=packet-up`，REALITY-XHTTP 保持 `mode=auto`，服务端
  仍为 `mode:auto` 以兼容旧客户端。原因是 Xray Go 1.27/H2 的 XHTTP `stream-one` 仍有
  未合并的连接关闭修复（上游 #6797/#7296）；2026-11-09 同机重复探测中，zz 的
  `stream-one` 多次出现连续超时，而两机 `packet-up` 各 `10/10` 成功。该选择牺牲少量
  请求聚合效率换取独立故障域的稳定性，不代表 XHTTP 已通过中国运营商自然流量验收。
  drift 门禁同时要求受管 XHTTP 服务端入站仍为 `mode:auto`；服务端漂移会报
  `xhttp_front=server-mode`，不会继续把固定客户端模式当作有效契约。

- `scripts/cdn_fallback_lane.ps1`：在 bwg 上新增 Cloudflare 兜底车道。客户端连
  `cdn.sciman.top:2087`（任意播边缘），CF 以 Full/strict 回源 `2087`，源站 nginx 用通配符
  证书终止 TLS 并把随机路径 `/l7kcDNws` 反代到 `127.0.0.1:45989` 的 VLESS+WS 入站，其余
  路径回落伪装页。DNS 记录由 `-Apply` 通过 acme.sh 里的 CF token 建/改成橙云；token 只有
  DNS 权限，改不了 SSL 模式，所以首次应用要确认 zone 是 Full/Full (strict)。该车道不进
  vasma 订阅，客户端链接另存为 `%APPDATA%\vps-ssh-launcher\cdn-lane-client.txt`。Xray 26
  已把 WebSocket 标记为弃用（尚未移除）；迁移到 XHTTP 需先单独验证 CF 的 SSE/长连接行为，
  触发条件是 Xray 正式移除 WS，或该车道实测速率不可接受。
  2026-10-31 实测（`cdnx.sciman.top:2096` 临时拓扑，已清理）：同一边缘端口伪装页 `200`，
  而 XHTTP `mode=auto` 与 `mode=packet-up` 均为 `000`——**XHTTP 不能走 Cloudflare**，
  原因是 CF 会缓冲/重整请求体，破坏 XHTTP 的流式 POST + 长 GET 时序。另外 CF 只代理
  `443/2053/2083/2087/2096/8443`，其它端口边缘直接 `530`。因此 CDN 侧只保留这一条 WS
  车道，不加第二条传输，也不加第二个边缘端口（用户 2026-10-31 决策）。
  无 CF API token 时，橙云判定不再采用“解析结果不等于源站 IP”这一弱条件；脚本读取
  Cloudflare 官方 `ips-v4`，要求所有 A 记录均落在其发布网段内。网段获取失败记为
  `unverified`，非 CF 地址记为 `not-proxied`，两者都不会被报告为健康。
- `scripts/cdn_origin_cert_sync.ps1`：zz 的 acme 账号没有 Cloudflare DNS token，
  无法自行签发通配证书，CDN 回源继续使用 bwg `*.sciman.top` 的副本。该脚本从 bwg
  读取证书/私钥，校验证书覆盖 `zzcdn.sciman.top` 且公私钥匹配，备份 zz 旧文件后
  原子替换、`nginx -t`、reload，并用 `openssl s_client` 证明 2087 实际返回新叶子；
  默认只读，`-Apply` 才写；先比对 bwg/zz 的 DER 叶子与 zz 实际服务叶子，一致时不
  传输私钥。bwg 的 `*.sciman.top` 由
  `/etc/cron.d/vps-launcher-v2ray-agent-renewtls` 每日 01:30 自动续期，zz 的副本不在
  该 cron 管辖内，因此本机任务 `VPS-SshLauncher-ZZ-CertSync` 每日 20:10 以 `-Apply`
  运行；`tls-expiry` 仍会在 14 天内到期时报警。私钥只出现在 base64 远程载荷中，
  输出只有 SHA-256 指纹。
- `scripts/firewall_baseline.ps1`：维护独立的 `inet vps_launcher_guard` 表，不改写
  vasma/Docker 规则。INPUT 默认 drop，只放行当前公开 TCP/UDP 服务、SSH、loopback、
  ICMP 和 DHCP；PREROUTING 将 UDP `20000-20999` 重定向到 obfs `8444`，OUTPUT 对主机自身
  发往本机公网地址的跃点探针做同路径 hairpin。systemd oneshot 负责开机重放，drift
  比较完整规则文件，并把 `nft -j` 实时 INPUT 规则与契约逐条比对；除 policy drop 外，
  TCP/UDP 端口集合、规则顺序和任何额外 accept 规则也必须完全一致，避免“规则文件没变、
  但 live 表多放行 22 端口”仍报 applied。

所有入口都使用 `/run/vps-ssh-launcher-maintenance.lock`，Apply 前先备份，失败时
尝试恢复并输出 `ROLLBACK_VERIFIED` 或 `ROLLBACK_FAILED`。不应并行执行它们，也不应
把 read-only 输出解释为客户端活体验收。

SSH 通道断开时远程 Bash 会收到 `HUP`；事务型入口把它与 `INT`/`TERM` 同等处理，
在回滚入口马上解除全部 signal trap，写入和复验成功后再解除一次，避免回滚重入或
“已成功但后被挂断又回滚”。CDN 门禁比较完整渲染模板，而不是只看 HSTS/404 等少数
幸存行，因此丢失 `default_server`、`ssl_reject_handshake`、流式超时或 WS Upgrade
指令都会变成 `cdn-fallback` 漂移。

## 车道与订阅汇总

| 主机 | 车道 | 公开端口 | 在订阅中 | 对应漂移族 |
| --- | --- | --- | --- | --- |
| bwg | VLESS+REALITY+Vision | TCP 443 | 是 | reality-shortid / reality-target / nginx-stealth |
| bwg | VLESS+REALITY+XHTTP | TCP 21470 | 是 | 同上 + xray-port-collision |
| bwg | VLESS+TLS+XHTTP（nginx 前置） | TCP 10094（dokodemo→15994→45988） | 是 | xhttp-front |
| bwg | Hysteria2 base | UDP 443 | 是 | hysteria2-masquerade |
| bwg | Hysteria2 obfs | UDP 8444 | 否（手工配置） | hysteria2-obfs |
| bwg | CDN VLESS+WS（Cloudflare） | TCP 2087（CF 边缘，回源 2087） | 否（手工配置） | cdn-fallback |
| bwg | 备用 SNI（`static.sciman.top`，复用 443/21470 入站） | 同端口，仅换 SNI | 是（`reality-vision-alt` 节点） | reality-alt-sni |
| bwg | 合并订阅（7 节点） | 静态文件 `/s/merged/<token>`（33387 与 2087 均可取） | 是（替代手工导入） | merged-sub |
| zz | VLESS+REALITY+Vision（xray） | TCP 443 | 是 | reality-shortid / reality-target / nginx-stealth |
| zz | VLESS+REALITY+XHTTP（xray） | TCP 21470 | 是 | 同上 + xray-port-collision |
| zz | VLESS+TLS+XHTTP（nginx 前置） | TCP 10094（dokodemo→15994→45988） | 是 | xhttp-front |
| zz | Hysteria2 base | UDP 443 | 是 | hysteria2-masquerade |
| zz | Hysteria2 obfs | UDP 8444 | 否（手工配置） | hysteria2-obfs |
| zz | CDN VLESS+WS（Cloudflare） | TCP 2087（CF 边缘，回源 2087） | 否（手工配置） | cdn-fallback |
| zz | 备用 SNI（`static-zz.sciman.top`，复用 443/21470 入站） | 同端口，仅换 SNI | 是（`zz-reality-vision-alt` 节点） | reality-alt-sni |
| zz | 合并订阅（7 节点） | 静态文件 `/s/merged/<token>`（12378 与 2087 均可取） | 是（替代手工导入） | merged-sub |

订阅发布由 vasma 托管（菜单 `7→6→y`，salt 复用、URL 不变）；bwg 现为
3×VLESS + 1×Hysteria2 共 4 个核心车道，zz 也已完成同构（含 2026-11-01 补齐的
第二 SNI）。公开 XHTTP 端口必须留在
xray 的 dokodemo 上：vasma 用 `inbounds[1].port` 取订阅端口，nginx 直占公开端口
会在菜单重配时与 xray 抢端口。

## 合并订阅（按主机生成 6/7 节点一个 URL）

vasma 只发布它自己管理的入站，所以 Hysteria2 obfs（8444）和 Cloudflare 车道永远
不在它的订阅里；不使用合并订阅时才需要额外手工导入。`scripts/merged_subscription_lane.ps1`
从在线配置读凭据，把主机当前的全部活动车道合成一份 base64 列表，写到共享静态根
`/usr/share/nginx/html/s/merged/<token>`，由现成 vhost 的 `location /` 直接服务，
不改 vasma 的 nginx 文件、不动订阅盐：

- 直连入口：`https://fq.sciman.top:33387/s/merged/<token>`
- CDN 入口：`https://cdn.sciman.top:2087/s/merged/<token>`（源站 IP 被封时仍可取）
- 第二直连入口：`https://static.sciman.top:33387/s/merged/<token>`（精确封锁主 SNI 时仍可取）

`<token>` 由本机生成并保存在 `%APPDATA%\vps-ssh-launcher\merged-sub-token.txt`，
完整 URL（三入口各一行）落在同目录 `merged-subscription-url.txt`（含全部凭据，勿外发）。
主入口受 **IP 封锁** 影响时，CDN 入口通常仍可取；若只是域名或端口的精确封锁，第二直连
入口是本地恢复路径，三者不是同一个故障域的替代品。应用：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\merged_subscription_lane.ps1 -Profile bwg -Apply
```

vasma 重装或菜单重配可能轮换 UUID / Hysteria2 口令，旧列表会静默失效，因此漂移门禁
的 `merged-sub` 校验准确的节点数（设备异结构变化的 6/7 节点，不放宽为“至少 6”）、
UUID 和第二 SNI 节点；`merged-sub-fetch` 再从直连回环下载一次，并验证 Hysteria2 URL
的认证口令没有在 URI 导入中丢失。两者任一失败都重跑上面的 Apply。节点标签只用 ASCII
的 `-reality-vision` / `-xhttp-reality` / `-hy2-obfs` 等格式，避免中文客户端订阅
解析器改写 fragment 后误报或漏报。

## zz 同构 bwg（2026-10-31）

zz 原本只有 sing-box（REALITY 22752 + Hysteria2 443/8444）。为与 bwg 同构补齐了：

- `xray` 与 bwg 同版本（两台现为 26.10.31，binary sha `cc4b84d3…`）安装在
  `/etc/v2ray-agent/xray`，服务启用；
  REALITY Vision 走 `dokodemo 443 → 127.0.0.1:45987`，`target=zz.sciman.top:35622`。
- XHTTP REALITY 在 `21470`（路径 `/zzxHTTP`），XHTTP TLS 在 `10094`，内层
  `127.0.0.1:45988`，由 `scripts/zz_xhttp_nginx_front.ps1` 管理
  （`vps-launcher-zz-xhttp-front.conf`，dokodemo tag `zz-xhttp-front`）。
- sing-box 只保留 Hysteria2 443/8444；原 REALITY 入站已移出并备份。
- vasma 订阅由 `scripts/zz_subscription_republish.ps1` 原地重写（salt 文件名不变，
  URL 不变）：同时更新 `subscribe_local/default/<email>` 和客户端实际下载的
  `subscribe/default/<md5(email+salt)>`，REALITY 节点从 22752 指向 443，并补回
  nginx 前置后的 XHTTP TLS 节点（10094）。只写本地文件会让 URL 一直返回旧节点：
  2026-11-01 审查发现线上仍是 10 月 4 日的 2 节点列表（REALITY 指向退役的 22752），
  修复后 `-Apply` 输出 `zz_sub_nodes=5 tls_node=1`、`zz_sub_http=200`。vasma 菜单
  重配后需重跑；`clashMeta` / `sing-box` 两种订阅仍由 vasma 自己生成，本车道不重写。
- CDN 车道：`zzcdn.sciman.top:2087`，回源证书用从 bwg 同步的通配证书
  `/etc/v2ray-agent/tls/cdn.zz.sciman.top.crt`。

**三级子域陷阱**：Cloudflare 通用证书只覆盖 `*.sciman.top`（一层），所以
`cdn.zz.sciman.top` 在边缘就会握手失败（schannel `SEC_E_ILLEGAL_MESSAGE`），与回源
无关。zz 的 CDN 主机名必须是二级子域（当前用 `zzcdn.sciman.top`）。另注意 CF 源站
加密模式为「完全（严格）」，源站证书必须覆盖该主机名，故不能用只含 `zz.sciman.top`
的单域证书。

zz 的合并订阅写入 `merged-subscription-url-zz.txt`，直连入口
`https://zz.sciman.top:12378/s/merged/<token>`，CDN 入口
`https://zzcdn.sciman.top:2087/s/merged/<token>`，第二直连入口
`https://static-zz.sciman.top:12378/s/merged/<token>`。

### zz 备用 SNI 补齐（2026-11-01）

bwg 的 REALITY 入站同时接受 `fq.sciman.top` 与 `static.sciman.top`，zz 之前只有单一
`zz.sciman.top`：一旦 GFW 精确封锁该主机名，zz 的 5 条直连车道同时失效，只剩 CDN。
2026-11-01 已补上 zz 的第二 SNI：先在 Cloudflare 给 `static-zz.sciman.top` 建一条
灰云（DNS-only）A 记录指向 zz 公网 IP，再 `reality_alt_sni_lane.ps1 -Profile zz -Apply`
（REALITY 握手 + 真实客户端出口取一次作验收），随后 `merged_subscription_lane.ps1
-Profile zz -Apply` 重新发布，使 `zz-reality-vision-alt` 节点进入 6→7 节点合并订阅。
这与 bwg 的 `static.sciman.top` 是对称结构；两个名字都在同一张通配证书覆盖范围内。

## 漂移门禁（vasma 重装后必查）

vasma 重装或菜单里重新配置协议时，`initXrayConfig`/`initSingBoxConfig`/`nginxBlog`
会重新生成 06/07 源配置和静态根，把空 shortId 与默认页装回去。此时 Xray/sing-box
服务照常 active、订阅照常可用，**没有任何告警**，只有主动探测面变宽了。

```powershell
pwsh -NoProfile -File .\scripts\proxy_hardening_drift.ps1 -Profile bwg   # 或 zz
```

判据是最后一行 `HARDENING_DRIFT=NONE`（退出 0）；出现
`HARDENING_DRIFT=camouflage,reality-shortid,reality-target,reality-flow,reality-alt-sni,nginx-stealth,xhttp-front,xray-port-collision,hysteria2-obfs,hysteria2-masquerade,cdn-fallback,merged-sub,merged-sub-fetch,vasma-sub,tls-expiry,stale-firewall,firewall-baseline,host-security`
的任一子集即退出 3，
按列出的 lane 名重跑对应 `-Apply`。该脚本只读，不获取锁、不重启服务，可安全放进
日常巡检；它已接入 `scripts/vps_maintenance.ps1` 的 observe 路径，因此
`VPS-SshLauncher-BWG-Observe` 每日 20:00 会跑一次漂移门禁和 loopback 车道验收，
漂移或验收非 PASS 会让任务以非零退出，并在
`%LOCALAPPDATA%\vps-ssh-launcher\maintenance-runs\*.log` 留下 `HARDENING_DRIFT=`
与 `ACCEPTANCE_RESULT=` 判据行。`-Apply`/`-AutoApply` 路径不跑这两项，避免给计划内的
远端写入叠加额外耗时。`hysteria2-masquerade` 会同时校验唯一的 UDP 443 base inbound、精确的
profile 默认 HTTPS 目标和 `rewrite_host=true`；若曾用 `-MasqueradeUrl` 指定自定义目标，
巡检时也必须传同一个 `-MasqueradeUrl`，否则会按默认目标报告漂移。

`host-security` 校验当前主机仍处于 Xray v26.10.31 的 MPTCP 应用层缓解：
`net.mptcp.enabled=0` 必须同时存在于运行时和 sysctl 持久化配置，且 Xray 版本不得低于
`26.10.31`。在 Ubuntu 24.04 的 `6.8.0-*` 内核上，`CVE-2026-98432` 未到
`6.8.0-149` 时会输出 `kernel_cve_2026_98432=vulnerable` 并使门禁失败。该漏洞是
IPv4 TCP 的 use-after-free，内核团队评分为 9.8；关闭 MPTCP 只缩小暴露面，不能替代
内核补丁。当前没有可盲重放的恢复动作：Ubuntu 发布修复包后，必须按单机维护流程更新
内核、受控重启并复验，不能由 `proxy_hardening_lane_recover.ps1` 自动重启服务。

zz 另有 `VPS-SshLauncher-ZZ-Drift` 每日 20:30 运行同一门禁，日志写
`%LOCALAPPDATA%\vps-ssh-launcher\maintenance-runs\drift-zz-*.log`，退出码即任务结果。
任务带 `-Apply` 时只把具名漂移交给 `proxy_hardening_lane_recover.ps1`，后者复用已
有硬化入口的锁、备份和回滚，并在最后重跑门禁；健康主机只输出
`RECOVERY_RESULT=NONE`，不会重启服务。`reality-target`、`reality-flow`、
`xray-port-collision`、`cdn-fallback`、`tls-expiry` 仍需人工决策，自动入口不会盲改。它当前是 Interactive
登录态任务（登出时不运行）；提权后可改为 S4U，与 BWG 的
`VPS-SshLauncher-BWG-Observe` 对齐。

`vasma-sub` 解码 `/etc/v2ray-agent/subscribe/default/*`（客户端真正下载的那一份），
要求 TCP REALITY 节点端口等于在线 dokodemo 的公开端口，并在存在 nginx XHTTP 前置时
要求列表里出现 `security=tls&type=xhttp` 节点。它专门拦“本地文件改对了、对外 URL
仍是旧列表”的半发布状态；zz 用 `zz_subscription_republish.ps1 -Apply` 修复。

`merged-sub-fetch` 是 `merged-sub` 的补充而非重复：前者验文件中的凭据和节点完整性，
后者真实请求 `/s/merged/<token>`，避免 URL 200 但客户端导入不了、或 Hy2
URL 的 userinfo 被截断而仅靠节点数仍判绿。直连回环被封不会让门禁从主机外部探测失败；
公网、中国网络的切换仍由 `proxy_lane_acceptance.ps1 -PublicName` 和真实客户端负责。

`tls-expiry` 从 `nginx -T` 收集所有 `ssl_certificate`，14 天内到期即漂移。它覆盖
REALITY 回落站、XHTTP 前置、CDN 回源和 Hysteria2 共用的证书。zz 的 CDN 回源证书
`/etc/v2ray-agent/tls/cdn.zz.sciman.top.crt` 是从 bwg 同步的通配证书副本，zz 本机的
`renewtls` 只续 `zz.sciman.top`，所以 bwg 通配证书续期后要把新的 crt/key 同步到 zz；
`tls-expiry` 只提前报警，不会自动跨机复制。

`reality-target` 校验 Xray 与 sing-box 两侧 REALITY 的回落目标：`target`/`dest` 的
主机名必须等于 `-MasqueradeUrl` 的主机名，`serverNames`/`server_name` 也必须包含它。
vasma 全量重装会按 `realityServerName:realityDomainPort` 重写这两个字段，若在
“是否使用本域名作为 Reality 目标域名”处答 `n`，它会挑一个随机公网站点，443 上就会用
别人的证书回应 `fq.sciman.top`，既是成人可辨的 REALITY 特征，也让主机变成该站点的
转发器。出现该漂移时重跑一次 vasma 并答 `y`（目标端口即订阅/伪装 vhost 端口），或把
两个来源文件的 `target`/`serverNames` 改回本域名后 `xray run -test` 并重启。端口不做
断言：bwg 回落 `33387`、zz 回落 `35622`，只要主机名一致即可。

`reality-flow` 校验 TCP REALITY 入站每个 user/client 仍带 `flow: xtls-rprx-vision`。
Vision 是 **per-user 的 VLESS 字段**，不是传输层设置：vasma 菜单在 Vision 提示处答 `n`
会留下一个能正常认证、但内层握手退化成普通 TLS 的入站，而这正是上游所说的
“TLS-in-TLS 已经被针对”的特征。两核都要查（xray `settings.clients[].flow`、sing-box
`users[].flow`）。这是 operator decision，`proxy_hardening_lane_recover.ps1` 不盲重放：
重新走 vasma 并在 Vision 提示处答 `y`，或直接在两个源文件里补回字段后 `xray run -test`
再重启。`merged-sub` 同时比对已发布列表里 Vision 节点的 `flow` 与在线入站的实际值，
所以旧版本硬编码 `flow=xtls-rprx-vision` 渲染出来的列表会显形为漂移，而不是静默保留
一个客户端一发包就失败的节点。

REALITY 目标站当前为本域伪装 vhost（bwg `fq.sciman.top:33387`）：同 IP、同证书、
回落页与真实站点完全一致，主动探测者拿不到可区分响应。若未来必须更换 dest，按
官方 README 的判据选：TLS 1.3 + H2、无重定向、离服务器近、非被墙 SNI（被墙域名
作为 serverNames 会在 ClientHello 阶段就被 RST）、支持 OCSP；注意 mldsa65 需要
回落目标证书 >3500 字节。改 dest 属双核联动写操作，须走单机 apply + 验收批次。

## UDP/QUIC 阻断预案

两条 Hysteria2 车道（UDP 443 base + UDP 8444 obfs）对“UDP 被区域性封锁/限速”的
防线分层：

1. **端口级封锁**（只封 443/udp）：8444 obfs 备用车道不受影响，客户端切 obfs 节点。
2. **UDP 全封或 QoS**：回退 TCP 车道（bwg 与 zz 均有 443 REALITY Vision、21470
   REALITY XHTTP、10094 XHTTP TLS），订阅全量可用。
3. **端口跳跃（port hopping）**：`firewall_baseline.ps1` 已把 UDP
   `20000-20999` REDIRECT 到 obfs `8444`；合并订阅的 `*-hy2-obfs` 节点带
   v2rayN 兼容字段 `mport=20000-20999`，同时保留可连接的单一主端口 `8444`。
   v2rayN 当前不解析带逗号的 authority 多端口，使用该形式会整条跳过 obfs 节点。
   它只对“固定 UDP 端口/单端口封锁”有帮助；UDP 整体封锁时仍回退 TCP，且不会与
   Mimic 混用。firewall 规则、systemd 持久化和 live policy 都由
   `firewall-baseline` drift 审计。

### Hysteria2 新混淆与客户端边界

Hysteria2 官方和 sing-box 1.14.4 已支持实验性的 `gecko` 混淆，它会在 salamander
基础上随机分片和填充 QUIC 握手包。现网继续使用兼容面更广的 `salamander`：切换
`gecko` 会改变协议层，所有客户端必须同步升级并支持该类型；在真实客户端矩阵验证前
不应拿生产订阅做实验。若未来观察到“QUIC 握手特征被针对性阻断”，再单独建立第三
obfs 车道并按单机 Apply、负向探测和客户端导入流程验收。

### Cloudflare 端口与地域边界

Cloudflare 官方代理默认支持 2087，但“中国网络（China Network）”只支持 80/443。
现有 CDN 备用入口使用 `:2087`，因此主机侧受控握手、Cloudflare 边缘 200 和 WebSocket
隧道通过，仍不能证明中国运营商网络中的长期可用性。若要提高中国大陆场景覆盖，需要
把客户端入口迁到 Cloudflare 的 443，并引入 Origin Rule/Worker/Tunnel 一类能解耦
“边缘端口”和“源站端口”的机制；这会新增 Cloudflare 配置权限和新的故障域，本轮不做
无证据的网关重构，真实客户端回归仍是验收条件。

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
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\firewall_baseline.ps1 `
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
  -ObfsPasswordFile "$env:APPDATA\vps-ssh-launcher\bwg-hysteria2-obfs-password-v2.txt"
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\proxy_lane_acceptance.ps1 `
  -Config $target -Profile zz -PublicName zz.sciman.top -RealityPublicPort 443 `
  -ObfsPasswordFile "$env:APPDATA\vps-ssh-launcher\zz-hysteria2-obfs-password-v2.txt"
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
`-ObfsPasswordFile` 从本机 `%APPDATA%\vps-ssh-launcher\` 下的 profile 专用 v2 文件读取
独立 obfs 口令；没有本机口令文件时，脚本仍会从在线配置提取凭据完成受控握手。
不要把口令写进命令历史或脚本。
同一轮还会输出 `reality-fallback`（未认证探测是否仍返回伪装页 hash）、
`xhttp-reality`（21470）与 `xhttp-tls`（10094，经 dokodemo 包装端口拨号）三条 lane，
因此订阅里的 3 个 VLESS 节点都会被真实握手覆盖，而不是只验 Vision 那一条。
obfs 的 204 成功只证明链路正常；如果正确口令失败，脚本会再用无混淆和故意错误
口令的客户端对照，排除“客户端根本没走 obfs”或“错误口令仍被接纳”的假阳性。
端口跳跃的 `hysteria2-obfs-hop-public` 在 zz 上由主机自身验证；bwg 的直连公网 IP
不支持主机自 hairpin，脚本不把它误报为失败，bwg 必须由另一台主机（例如 zz）作为
外部客户端验收，并用 `firewall_baseline` 的 PREROUTING counter 证明规则命中。
无混淆探测得到 curl 97（UDP/QUIC 可达但无完整响应）属于 `N/A/PARTIAL`，DNS
失败与超时仍按失败处理。
它仍属于从主机发起的受控验收，不等于中国网络下真实客户端或运营商的自然验收。
ZZ 的 Reality 公开端口当前为 `443`，因此该参数应传
`-PublicName zz.sciman.top -RealityPublicPort 443`。旧的 sing-box `22752` 仅保留在迁移
历史中，不能再作为当前验收端口。

ZZ 的 Hysteria2 obfs lane 已纳入漂移门禁（base 存在时缺装即报
`hysteria2-obfs` 漂移）。门禁同时比较源片段和
sing-box 合并后的运行配置：UDP 8444 有监听但运行态丢失 salamander 或口令与源片段
不一致时，输出 `runtime-missing` / `runtime-password-drift`，不会把“端口开着”
当作 “obfs 保护仍生效”。

## 停用服务的防火墙残留

zz 的 ArgoX 临时 tunnel 已于 2026-11-01 停用，但 `/etc/iptables/rules.v4` 和
live iptables 里仍有具名的 `ArgoX PORT` ACCEPT 行（30000–30010、8080）。现在
`firewall_baseline.ps1` 的独立 INPUT 链已默认 DROP，这些旧 ACCEPT 行不会再让
端口公开可达；hygiene lane 仍用于清理失效规则和避免以后切换后端时重新暴露。
`proxy_hardening_drift.ps1` 的 `stale-firewall` 只匹配这些具名行，不推断任意
ACCEPT 规则可删。

```powershell
pwsh -NoProfile -File .\scripts\stale_firewall_hygiene.ps1 -Profile zz
pwsh -NoProfile -File .\scripts\stale_firewall_hygiene.ps1 -Profile zz -Apply
```

入口先确认 `argo` inactive 且 30000–30010/8080 无监听，备份 live 规则与持久化文件，
只删除观察到的具名规则，再用持久化后端写入并在两层复验。不会整表重写、关闭 SSH、
改动 vasma 的 443/8444/21470/10094 规则或主动重启代理服务。回滚目录会打印在
`APPLY_BACKUP_DIR`。

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
  `camouflage` 漂移。
- 站点模板制作残留（`*.psd`、`*.zip`、`psd/`、`__MACOSX/`、`._*`、`.DS_Store`、
  `Thumbs.db`、`Dockerfile`、`README.md`、`manifest.json`、`precache-manifest.*`、
  `*.worker.js`）也属于可辨识的“手工拼装伪装站”特征：主动探测者请求
  `/img/pro.psd`、`/html4.zip`、`/README.md` 之类会直接命中原始素材。camouflage 车道
  在整根备份后把这些**未被任何服务页引用**的残留一并清除（`/s/` 合并订阅目录被
  `--exclude-dir=s` 保护；任何被 `*.html` 或 `en/` 引用的项保留并在
  `residue_kept_referenced` 声明）；漂移门禁输出 `camouflage=residue file=<path>` 并报
  `camouflage` 漂移，vasma 重装重新拉取模板后带回即被巡检发现。2026-11-01 已在 bwg
  （`img/pro.psd` + 5 个 `Thumbs.db`）与 zz（`__MACOSX/` 整树、`psd/`、4 个 `html*.zip`、
  `README.md`、`Dockerfile`、`manifest.json`、2 个 `precache-manifest`、2 个
  `*.worker.js`、`img/pro.psd`、5 个 `Thumbs.db`）应用。
- 8443（CPA 网关）与 28317（CPAMC 管理面板）只做了 `server_tokens off`，没有加未知 SNI
  拒绝：它们可能被按 IP 字面量访问，拒绝未知 SNI 会误伤这类客户端。
- REALITY 的 `mldsa65` 保持关闭：官方要求回落目标证书 >3500 字节且支持 X25519MLKEM768，
  当前证书链 3393 字节、OpenSSL 3.0 无 ML-KEM，强行启用反而成了新指纹。
- Hysteria2 的 obfs 口令已与认证口令分离，固定入口 `8444/udp` 仍保留；固定入口是
  base lane 的控制面和回滚入口，跳跃只改变客户端可选的目标端口，不改变服务端监听。
  base 车道 `up/down_mbps 100/50` 仍是显式上限，未在本次变更中调整。
