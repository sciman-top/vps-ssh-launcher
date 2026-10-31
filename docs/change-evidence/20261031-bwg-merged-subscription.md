# bwg 合并订阅（6 节点一个 URL，2026-10-31）

问题：vasma 订阅只含它自己管理的 4 个节点（3×VLESS + Hysteria2 base）。Hysteria2
obfs（UDP 8444）与 Cloudflare 车道（2087）为手工车道，每台设备要单独导入，换机或
重置后容易漏配。

## 变更

- 新增 `scripts/merged_subscription_lane.ps1`（bwg，默认只读，`-Apply` 才写）。
  从在线配置读取 REALITY 端口/UUID/publicKey/shortId、XHTTP 路径、Hysteria2 base 与
  obfs 口令、CDN 车道端口与路径，合成 6 条分享链接的 base64 列表。
- 写入共享静态根 `/usr/share/nginx/html/s/merged/<token>`（0644），由 33387 与 2087
  两个现成 vhost 的 `location /` 服务：**不改 nginx、不碰 vasma、不动订阅盐**。
- `<token>` 由工作站生成（32 hex），保存在 `%APPDATA%\vps-ssh-launcher\merged-sub-token.txt`；
  完整 URL 写入 `merged-subscription-url.txt`。车道本身不打印 URL。
- 漂移门禁新增 `merged-sub`：解码节点数必须为 6，且列表 UUID 必须等于在线 REALITY
  入站 UUID；vasma 轮换凭据后旧列表会立即被抓出来。
- 幂等：列表内容逐字节比较，未变化时 `merged_sub=applied` 且不重写。

## 验收

| 检查 | 结果 |
| --- | --- |
| 节点数 | `merged_sub_nodes=6` |
| 直连入口 `https://fq.sciman.top:33387/s/merged/<token>` | `200`，解码 6 节点 |
| CDN 入口 `https://cdn.sciman.top:2087/s/merged/<token>` | `200`，解码 6 节点 |
| 漂移门禁 | `merged_sub=ok nodes=6`，`HARDENING_DRIFT=NONE` |
| 备份 | `/var/backups/merged-sub.2n0Se0` |

## 回滚

删除 `/usr/share/nginx/html/s/merged/<token>` 与
`/var/lib/vps-ssh-launcher/merged-sub.token`（或从上面备份目录恢复），客户端改回用
vasma 订阅 + 手工节点。门禁此后会报 `merged-sub` 漂移，直到文件恢复或功能下线。

## 边界

- 该 URL 携带全部凭据，等同订阅盐的敏感级别；只落本地文件，不进仓库、不进收据。
- 静态列表在凭据轮换时不会自动更新，需重跑 Apply；漂移门禁负责发现失效。
- 未生成 clashMeta / sing-box 专用格式；v2rayN 的 base64 列表够用，其它客户端可
  按需再扩展。
