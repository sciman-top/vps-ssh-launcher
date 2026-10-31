# zz 与 bwg 同构：xray REALITY 443 + XHTTP + CDN + 合并订阅（2026-10-31）

目标：把 bwg 的代理形态复刻到 zz，仅域名/端口按 zz 现状调整。全程备份优先、
逐项验证；变更前快照 `/var/backups/pre-zz-parity-20261031T101738Z`。

## 变更

| 项 | 内容 |
| --- | --- |
| xray | 安装 26.10.29（与 bwg 同版本），迁移 REALITY 到 xray，`dokodemo 443 → 127.0.0.1:45987` |
| XHTTP | REALITY `21470`（`/zzxHTTP`）；TLS `10094` → nginx `15994` → `127.0.0.1:45988` |
| sing-box | 仅保留 Hysteria2 443/8444；REALITY 入站移出（备份 `/var/backups/zz-sb-reality-removal-20261031T102314Z`） |
| 订阅 | `zz_subscription_republish.ps1` 原地重写 salt 文件，REALITY 节点 22752 → 443 |
| CDN | `zzcdn.sciman.top:2087`（CF 代理），WS 路径 `/zzl7kcDNws`，回源用通配证书 |
| 合并订阅 | 6 节点，`zz.sciman.top:12378`（直连）+ `zzcdn.sciman.top:2087`（CDN） |

## 验收

| 检查 | 结果 |
| --- | --- |
| 漂移门禁 zz | `HARDENING_DRIFT=NONE`（xhttp-front / cdn-fallback / merged-sub 均 ok） |
| 车道验收 zz | `ACCEPTANCE_RESULT=PASS`：reality-xray、fallback、public:443、xhttp-reality:21470、hy2 base+obfs |
| XHTTP TLS | 经 `10094` 的 VLESS 客户端实测 `204` |
| CDN 隧道 | 经 CF 的 VLESS+WS 实测 `204`；边缘 `zzcdn.sciman.top:2087` 返回 200 |
| 合并订阅 | 直连与 CDN 两个入口均 200，各 6 节点 |
| 订阅节点 | vless 443/21470/10094 + hysteria2 443/8444 + vless zzcdn:2087 |
| 自然验收（客户端） | 操作者在 v2rayN 中导入 `merged-subscription-url-zz.txt`，6 个节点逐个实测均可用（中国网络、真实客户端） |

前五项由 `proxy_lane_acceptance.ps1` 与主机侧客户端完成，属于「主机发起」的受控验收；
最后一行是操作者在真实客户端上的自然验收，也就是本仓库真值分层里的 `live_accepted`。

## 踩坑记录

- **三级子域**：CF 通用证书只覆盖 `*.sciman.top`，`cdn.zz.sciman.top` 在边缘握手失败；
  改用二级 `zzcdn.sciman.top` 后正常。
- **严格回源**：zone 是「完全（严格）」，源站必须持有覆盖该主机名的证书；zz 原有单域
  证书不覆盖子域，故从 bwg 同步通配证书到 `/etc/v2ray-agent/tls/cdn.zz.sciman.top.crt`。
- **pipefail + set -e**：`ls` 无匹配、`grep` 无匹配都会让赋值返回非零并中断脚本；
  车道里已加 `|| true` 包裹。
- 中途一次证书切换顺序错误导致 nginx 配置校验失败，已从备份恢复并复验（见
  `/var/backups/zz-tls-wildcard-20261031T103926Z`）。

## 回滚

- xray：`systemctl disable --now xray`，恢复 `/var/backups/pre-zz-parity-20261031T101738Z/xray-conf`，
  把 sing-box REALITY 片段放回 `conf/config/` 后 `sing-box merge`。
- 订阅：从 `/var/backups/zz-sub.*/previous` 恢复 salt 文件。
- CDN：删除 `/etc/nginx/conf.d/vps-launcher-cdn.conf`、`15_vps_ssh_launcher_cdn_ws.json`，
  `nginx -t && systemctl reload nginx && systemctl restart xray`；DNS 记录可保留。
