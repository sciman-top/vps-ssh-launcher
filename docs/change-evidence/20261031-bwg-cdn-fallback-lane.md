# bwg Cloudflare 兜底车道（2026-10-31）

动机：其余车道全部依赖 bwg 自己的地址。zz 若退役，bwg 的 IP 或四个端口被封就没有任何
可用退路。本车道是唯一“客户端只连任意播边缘”的入口：客户端 → Cloudflare:2087 →
（Full strict 回源）→ 源站 nginx:2087 → `127.0.0.1:45989` VLESS+WS。源站 IP 被墙时，
CF 仍可从境外回源。

## 变更

| 项 | 内容 |
| --- | --- |
| DNS | `cdn.sciman.top` A→源站，`proxied=true`（Cloudflare，令牌来自 acme.sh 的 `SAVED_CF_Token`，仅有 DNS 权限） |
| 源站 nginx | `/etc/nginx/conf.d/vps-launcher-cdn.conf`：2087 未知 SNI 拒绝、真 SNI 用通配符证书、`^~ /l7kcDNws` 反代、其余回落伪装页 |
| Xray | `/etc/v2ray-agent/xray/conf/15_vps_ssh_launcher_cdn_ws.json`：`vps-launcher-cdn-ws`，loopback 45989，VLESS+WS，`security: none`，复用既有 VLESS UUID（未新建凭据） |
| 客户端 | 不进 vasma 订阅；链接写入 `%APPDATA%\vps-ssh-launcher\cdn-lane-client.txt`（不打印、不提交） |
| 门禁 | `proxy_hardening_drift.ps1` 新增 `cdn-fallback`：conf/inbound/监听/`cdn.sciman.top` 必须解析到 CF 而非源站（`not-proxied` 即漂移）；zz 报 `not-applicable` |
| 每日巡检 | 既有 observe 路径自动覆盖（漂移门禁 + 车道验收） |

## 验收证据

| 证据层 | 命令 | 结果 |
| --- | --- | --- |
| DNS | `Resolve-DnsName cdn.sciman.top` | `104.21.70.202` / `172.67.139.71`（Cloudflare） |
| CF→源站 | `curl --noproxy '*' https://cdn.sciman.top:2087/` | `200`，`remote_ip=172.67.139.71`，TLS 0.69s（伪装页） |
| 中国侧传输 | `.NET ClientWebSocket` → `wss://cdn.sciman.top:2087/l7kcDNws` | `Open`，1227ms（CF→源站→Xray 升级成功） |
| 完整隧道 | 源站临时 Xray 客户端 → `cdn.sciman.top:2087`（经 CF）+ SOCKS → gstatic 204 | `cdn_vless_ws_http=204` |
| 漂移门禁 | `proxy_hardening_drift.ps1 -Profile bwg` | `cdn_conf=ok cdn_inbound=ok cdn_listen=up cdn_dns=proxied`，`HARDENING_DRIFT=NONE` |
| 现有车道 | `proxy_lane_acceptance.ps1 -Profile bwg …` | `ACCEPTANCE_RESULT=PASS`（五条原车道不受影响） |

## 回滚

- 源站：备份 `/var/backups/cdn-lane.3A3deo`；删除
  `/etc/nginx/conf.d/vps-launcher-cdn.conf` 与
  `/etc/v2ray-agent/xray/conf/15_vps_ssh_launcher_cdn_ws.json`，`nginx -t`、
  `xray run -test` 后 `systemctl reload nginx && systemctl restart xray`。
- DNS：删除或改回 DNS-only 的 `cdn.sciman.top`（记录不可变更安全）。
- 门禁会因 `cdn-fallback` 报漂移，直到上述任一动作完成，避免“删了但以为还在”。

## 边界与遗留

- Xray 26.10.29 提示 WebSocket 已弃用（`might be removed`），尚未移除。XHTTP over CF 的
  SSE/长连接行为未在本批验证，未用它替换；触发条件见 runbook。
- 该车道未写入 vasma 订阅：vasma 只汇总自己的入站。手工节点需保留
  `alpn=http/1.1`（经 CF 的 WS 不能协商 h2）。
- 中国侧只验证了 CF 边缘可达与 WebSocket 升级，完整 204 由源站经 CF 完成；未在
  v2rayN 里做逐节点长时间实测。
- 同批发现并修复 zz 的 `50x.html` 伪装残留（备份
  `/var/backups/nginx-camouflage.VZqtdE`），zz 漂移门禁回到 `NONE`。

## 追加实测：XHTTP 不能走 Cloudflare（同日晚）

临时拓扑：`cdnx.sciman.top:2096`（CF 支持的端口）→ 源站 nginx → `127.0.0.1:45991`
XHTTP 入站。结果：

| 检查 | 结果 |
| --- | --- |
| 同端口伪装页 | `200`（CF→源站正常） |
| XHTTP `mode=auto` | `000` |
| XHTTP `mode=packet-up` | `000` |
| 对照：WS 车道经同一 CF 边缘 | `204` |

结论：CF 缓冲/重整请求体，XHTTP 的流式 POST + 长 GET 时序无法通过，因此
“Xray+XHTTP 套 CDN”不成立；CDN 侧只保留一条 WS 车道。另确认 CF 只代理
`443/2053/2083/2087/2096/8443`，首次误用 2088 时边缘返回 `530`。
临时 DNS 记录、nginx vhost、临时进程均已清理，清理后 `HARDENING_DRIFT=NONE`、
`cdn_edge=200`。
