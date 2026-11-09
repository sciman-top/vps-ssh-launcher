# 代理客户端切换与订阅恢复

这条手册只描述客户端动作。服务端节点、端口与验收边界以
[伪装车道运维手册](proxy-camouflage-lanes.md)为准。凭据只保存在本机
`%APPDATA%\vps-ssh-launcher\`，不要把订阅 URL 或完整节点链接粘贴到仓库、日志或
公开聊天中。

## 订阅入口不是同一个故障域

每台主机发布 7 个节点。对应的合并订阅有三条取用入口，完整列表保存在
`merged-subscription-url.txt`（zz 为 `merged-subscription-url-zz.txt`）：

| 主机 | 直连 | Cloudflare | 第二直连 |
| --- | --- | --- | --- |
| bwg | `https://fq.sciman.top:33387/s/merged/<token>` | `https://cdn.sciman.top:2087/s/merged/<token>` | `https://static.sciman.top:33387/s/merged/<token>` |
| zz | `https://zz.sciman.top:12378/s/merged/<token>` | `https://zzcdn.sciman.top:2087/s/merged/<token>` | `https://static-zz.sciman.top:12378/s/merged/<token>` |

三条入口都必须通过主机名校验。2026-11-09 之前 zz 的第二直连入口只做 `curl -k`
验证是绿的，实际原因是 `12378` 上没有任何 vhost 服务 `static-zz.sciman.top`，
客户端拿到的是 `zz.sciman.top` 的单域证书；该名字现在由
`reality_alt_sni_lane.ps1` 一并投影到订阅端口，两边 `-Apply` 后用
`curl -sS -o /dev/null -w '%{http_code}'`（不加 `-k`）应得到 200。

按故障类型取列表：

| 现象 | 先试 | 仍失败时 |
| --- | --- | --- |
| 主 SNI 或被直连域名的精确封锁 | 第二直连入口 | Cloudflare 入口 |
| bwg 或 zz 的单个公网 IP 不可达 | 另一台主机的合并订阅 | Cloudflare 入口 |
| TCP 443 被 QoS 或阻断 | 订阅中的 Hysteria2 base / obfs | XHTTP 21470 / TLS 10094 / CDN |
| UDP 443 或 8444 被阻 | 先刷新订阅并试 `*-hy2-obfs` 的 20000–20999 跳跃入口 | UDP 整体被封时改 Vision / XHTTP / CDN；跳跃不能穿透 UDP 全封 |
| `sciman.top` 整段域名被阻 | Cloudflare 入口 | 独立注册域与新的主机名需另立项 |

## 节点优先级与含义

| 顺序 | 节点 | 何时切换 |
| --- | --- | --- |
| 1 | `*-reality-vision` | 日常默认；单 TCP 443，伪装站回落 |
| 2 | `*-reality-vision-alt` | 主 SNI 的精确封锁；只替换 SNI，不绕过 IP 封锁 |
| 3 | `*-xhttp-reality` | 多连接表现异常或 443 被定向；REALITY 在 21470 |
| 4 | `*-xhttp-tls` | 需要普通 TLS 握手面；经 nginx 前端 10094 |
| 5 | `*-hy2-obfs` | TCP 受限、UDP 8444 尚可用；订阅和文件名均不依赖显示标签缓存旧值 |
| 6 | `*-hy2-base` | UDP 443 可用，性能优先 |
| 7 | `*-cdn-ws` | 仅剩 Cloudflare 边缘可达时；不是低延迟主路径 |

WebSocket 仍是 Cloudflare 的兼容车道，Xray 26 已提示该传输弃用；不要在未经实测前把
它作为唯一入口。

## v2rayN 的切换步骤

1. 先刷新订阅；入口失败时改用本表的另一入口，而不是在已失效的 URL 上反复重试。
2. 对 7 个节点跑一次真实延迟/连接测试。状态“服务器正常”不等于隧道能取到
   `204`，出现 `000`、握手失败或反复重连时立刻换节点。
3. 切换顺序遵循上表：先换 SNI 或传输，再换主机，最后才切 Cloudflare。
4. 长时间连接切换网络后卡住，先断开旧会话再连接新节点。UDP 平台失败时先切 TCP
   Vision/XHTTP，不要重复压测已受限的 UDP。

## 恢复与回滚

1. 合并订阅节点数不足、Hy2 标签指纹与在线列表不同，或 URL 打不开：在
   `bwg` 上运行 `scripts/proxy_hardening_drift.ps1 -Profile bwg`；按
   `merged-sub` / `merged-sub-fetch` 的修复提示重新执行
   `merged_subscription_lane.ps1 -Apply`。
2. 只更换了入口的返回结果，原订阅内容仍一致时，不需要重启 xray、nginx 或
   sing-box。
3. 若一次 Apply 失败，使用输出的 `/var/backups/merged-sub.*` 恢复合并订阅；不要
   轮换 UUID、Hysteria2 口令或删除客户端配置来掩盖问题。
4. 需要判断“服务端能用”还是“客户端真能用”时，保留两类证据：主机侧
   `ACCEPTANCE_RESULT=PASS`，以及从实际客户端得到的连接成功与出口结果。前者不能代替
   后者。
