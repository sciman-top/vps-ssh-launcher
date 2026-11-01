# bwg / zz 防封锁车道复验与恢复性修复（2026-11-02）

用户授权对 bwg 与 zz 的 vasma / VLESS+REALITY+Vision / XHTTP / Hysteria2 /
Cloudflare / 订阅分发做深度只读审查，并在有必要时修复本项目代码。本批对两台
主机逐台操作，先 bwg 后 zz；没有改 UUID、shortId、REALITY 密钥、OAuth/API-key、
CPA lane、Hysteria2 认证口令或 Cloudflare 凭据。

## 初始证据与真实缺口

- 修复前两台均 `HARDENING_DRIFT=NONE`，Xray 26.10.31、sing-box 1.14.4，
  `proxy_lane_acceptance.ps1` 的 loopback 与公开名路径均为 `ACCEPTANCE_RESULT=PASS`；
  此结果只证明主机侧受控握手。
- 原有 `merged-sub` 只看 7 条前缀和首个 UUID，没有发现实际 GET、Hy2 userinfo
  或新节点兼容问题。现有静态订阅虽可下载，却没有真正验证客户端可导入的订阅。
- XHTTP 前端旧模板没有品牌化 404；并且 `render_conf > target` 的 shell 重定向会在
  渲染失败前截断现有 nginx 配置，失败后的回滚又因覆盖相同路径而失去原始内容。
  本批复现后从失败前备份恢复了 bwg / zz 的前端，再完成修复。
- `zz_xhttp_nginx_front.ps1` 与统一前端曾各自生成 15994 `default_server`；统一
  Apply 因端口冲突回滚，后续改为由统一前端接管 zz，旧入口只做幂等退役。

## 代码与客户端面

- `xhttp_nginx_front.ps1`：未授权请求返回同站 `404.html` 与 HSTS；渲染先写
  sibling 临时文件并校验内容后再原子替换；支持 bwg / zz 的既有前端文件名；
  检测 loopback 后端错位和“nginx 终止 TLS、Xray 仍期待 TLS”的混用树。
- `cdn_fallback_lane.ps1`：Cloudflare 回源未知路径同样返回品牌化 404 与 HSTS；
  nginx 模板先原子渲染后替换，健康路径也会检测模板漂移。
- `merged_subscription_lane.ps1`：保留 7 节点生成逻辑；发布后真实 GET 订阅；节点名
  仅用 ASCII；写入三个入口 URL（直连、Cloudflare、第二直连）；Apply 仍先备份后写入。
- `proxy_hardening_drift.ps1`：新增 `merged-sub-fetch`，校验真实 GET 的 200、精确节点数
  与 Hy2 URL userinfo；`merged-sub` 的节点数按在线架构计算并检测第二 SNI 节点。
- 客户端切换顺序与订阅入口记录在 [代理客户端切换手册](../runbooks/proxy-client-failover.md)。

## 双机验收

| 检查 | bwg | zz |
| --- | --- | --- |
| 合并订阅 | 7 节点；直连 / CDN / 第二直连均 200 | 7 节点；直连 / CDN / 第二直连均 200 |
| 漂移 | `HARDENING_DRIFT=NONE` | `HARDENING_DRIFT=NONE` |
| 真实隧道 | `ACCEPTANCE_RESULT=PASS` | `ACCEPTANCE_RESULT=PASS` |
| XHTTP 未授权 404 | 10094 HSTS + 品牌页；21470 回落品牌页 | 10094 HSTS + 品牌页；21470 回落品牌页 |
| 证书同步 | 通配证书有效 | CDN 证书与通配证书 DER hash 一致 |

## 最终投影与复验（2026-11-02 追加）

- bwg / zz 均逐台执行 vasma updater 与 RenewTLS wrapper 的 `-Apply`：
  两台都得到 `RUNTIME_VERIFY_OK`、`UPDATER_PROJECTED` 与
  `RENEWTLS_LOCKED_PROJECTED`；updater SHA-256 一致，RenewTLS 按 profile
  使用各自构建产物。原 cron 仍保留 root `cron.d` 的 01:30 / 14:40 行。
- 订阅 Apply 后三入口各 200、7 节点；`merged_sub_current=1`。XHTTP 与 CDN
  Apply 后返回 `xhttp_front=applied` / `cdn_lane=applied`；最后两台的公网名
  真实握手仍为 `ACCEPTANCE_RESULT=PASS`。
- Cloudflare 回源证书预演确认源站、zz 的 CDN 副本和实际服务叶子的 DER hash
  一致，`pair=ok`；RenewTLS 状态文件与两台实际网关证书 hash 一致。
- 远端可逆回滚证明：已检查两台最终 XHTTP 备份同时包含 `previous.conf` 与
  `xray/inbound.json`。受控临时目录测试恢复了变更前配置并输出
  `ROLLBACK_VERIFIED`；该测试只改动临时文件，没有停服或改动生产流量。
- vasma 官方 release 当前没有标记为“最新”的 GitHub release；固定 commit
  `876ed197` / `v3.5.33` 的 install.sh 远端 hash 与 manifest 一致。Xray
  `v26.10.31` 和 sing-box `v1.14.4` 与远端运行版本相同，因此本批不执行
  无新版本的强制内核重装。

## 继续审查后追加的健壮性修正

- 巡检增加双 XHTTP 管理前端文件的冲突判据；`merged-sub` 在异常树中 fail-closed，
  不再把共享 15994 的单车道误计成两个节点。
- 纯模板修改拆成带维护锁的模板替换路径，不再为没有变化的 Xray inbound 做完整
  事务重写；只有真实后端错位或证书/监听异常才进入完整受控回滚事务。
- CPA `-Apply` 的 idle-lane 预检改用独立 heredoc 定界符，避免预检脚本遮蔽后续
  配置投影；bwg CPA strict doctor 已重新为 `DOCTOR_CONTRACT_OK`。此次 CPA 代码
  改动属于项目代码/客户端请求面，未改动账号、OAuth 凭据或 CPA 运行并发策略。

本轮配置备份包括 `/var/backups/xhttp-front.*`、`/var/backups/cdn-lane.*` 和
`/var/backups/merged-sub.*`；恢复以这些变更前备份为准，不以 Git 回滚替代。
其中 bwg / zz 的最终 XHTTP 备份目录分别是 `/var/backups/xhttp-front.aYa9wc`
和 `/var/backups/xhttp-front.a97Dw5`。

## 风险与未验收项

- 10094 的第二 SNI 当前是精确拒绝，不是该主机的真实站点；这是有意限制，避免为
  修好一个探测面而扩出未经注册、未经客户端验收的额外 TLS 站。21470 的 REALITY
  第二 SNI 已由真实握手覆盖。
- 21470 REALITY 属非 443 端口，Xray 官方启动日志给出 IP 被封风险告警；CDN 的
  WebSocket 已被官方标为弃用，但 Cloudflare 对 XHTTP 的既有实测不支持，保留它们
  作为不同故障域的退路，不能视为长期不会被识别的保证。
- 本批仍是主机侧与公网名的受控验收；没有覆盖真实中国运营商、长期 QoS、整段域名
  封锁或 IP 封锁下的客户端体验。独立注册域仍需要用户提供域名与 DNS 决策。
