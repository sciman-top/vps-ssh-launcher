# bwg/zz nginx 指纹收敛与 XHTTP 前置（2026-10-31）

## 范围

- 主机：bwg 先做并复验，再对 zz 执行；两次都单机串行，未并行触发。
- 变更：`scripts/nginx_tls_stealth.ps1`（两台）与
  `scripts/xhttp_nginx_front.ps1`（仅 bwg，zz 无 XHTTP lane）。
- 未改动：UUID、REALITY 密钥、Hysteria2 口令、证书、订阅地址、CPA 配置与端口。

## 依据

- `Server: nginx/<版本>` 是主动探测可直接读取的版本指纹。
- 33387/35622 对任意 SNI 都回本域名证书；正常 vhost 会拒绝未知 SNI，回错名字的证书
  反而是 REALITY 的放大特征，也让主机成为到伪装站的一般转发器。
- 10094 前置前对任意路径返回无 `Server`/`Content-Type`、零 body 的 `HTTP/2 404`；
  Xray XHTTP 官方文档要求“将 XHTTP 隐藏在真正的 Nginx、Caddy 后面以减少指纹特征”。

## 执行与证据

- bwg `nginx_tls_stealth.ps1 -Apply`：首次 Apply 在 `nginx -s reload` 后立刻探测，
  命中旧 worker 被判 `STEALTH_VERIFY_FAILED` 并 `ROLLBACK_VERIFIED`
  （backup `/var/backups/nginx-stealth.8Yw68u`）——回滚链路得到实测。加入 6 秒重试窗口后
  重新 Apply 成功（backup `/var/backups/nginx-stealth.hz51nl`），读回
  `unknown_sni_rejected=1/1`、`conf_file=match`、`stealth=applied`。
- bwg `xhttp_nginx_front.ps1 -Apply`：`XHTTP_FRONT_APPLIED`
  （backup `/var/backups/xhttp-front.TE0BiN`）。
- zz `nginx_tls_stealth.ps1 -Apply`：`NGINX_STEALTH_APPLIED`
  （backup `/var/backups/nginx-stealth.VNwAI4`）。
- 维护状态文件：`/var/lib/vps-ssh-launcher/maintenance-status/{nginx-stealth,xhttp-front}.status`。

## 复验

- bwg 外部表现：10094 `/` → 200 伪装页、`/wp-login.php` → nginx 404、未知 SNI 0 证书；
  33387 → 200（`Server: nginx` 无版本）、`/s/default/` → 403、8443 → 404、28317 → 401；
  443 REALITY 回落 → 200。
- bwg：`HARDENING_DRIFT=NONE`、`ACCEPTANCE_RESULT=PASS`
  （reality-fallback / reality-xray(-public) / xhttp-reality / xhttp-tls / hysteria2-base(-public) / hysteria2-obfs(-public)）。
- zz：`HARDENING_DRIFT=NONE`（`reality_target=ok`、`stealth=applied`、`xhttp_front=not-applicable`）、
  `ACCEPTANCE_RESULT=PASS`（reality-singbox(-public) / hysteria2 两条）。
- 仓库门禁：`855+ passed`、bandit/ruff/format/mypy 全绿。

## 自查发现并修正的一条回归

第一版前置把公开端口直接交给 nginx，并删掉了 vasma 的 dokodemo。事后核对
`/etc/v2ray-agent/install.sh`（1442/728 行）发现两处后果：

- vasma 用 `jq -r '.inbounds[1].port'` 取订阅端口；删除后读回 `null`，任何一次订阅刷新
  都会把 10094 节点发布成空端口。
- vasma 菜单重配会重建 `0.0.0.0:10094` 的 dokodemo，与 nginx 抢端口，xray 会因绑定失败
  起不来——这不是静默回退，而是停机。

改为“公开端口留在 `inbounds[1]` 的 dokodemo、转发到 `127.0.0.1:15994` 的 nginx、内层
明文”后：`inbounds[1].port` 读回 `10094`，监听为 `*:10094 → 127.0.0.1:15994 →
127.0.0.1:45988`，vasma 重配最多退回裸监听（门禁报 `xhttp-front`），不再抢端口。
迁移首次 Apply 因 nginx graceful reload 仍持有 10094 而失败并 `ROLLBACK_VERIFIED`
（backup `/var/backups/xhttp-front.slfkfk`）；迁移路径改为 restart nginx 释放端口后成功
（backup `/var/backups/xhttp-front.9iBeTX`）。

## 回滚

- 任一台恢复：把 backup 目录里的 `previous.conf` 放回
  `/etc/nginx/conf.d/vps-launcher-stealth.conf`（或删除该文件）后 `nginx -t && systemctl reload nginx`。
- bwg 的 XHTTP 前置恢复（最新备份 `/var/backups/xhttp-front.9iBeTX`）：把
  `xray/inbound.json` 放回 `14_VLESS_XHTTP_TLS_inbounds.json`，删除
  `vps-launcher-xhttp-front.conf`，`xray run -test` → `nginx -t` → 先
  `systemctl restart xray`（释放公开端口）→ 再 `systemctl reload nginx`。顺序反过来会因
  端口占用失败，脚本内置的 `rollback()` 已按此顺序执行。
- vasma 重装后按 `docs/runbooks/proxy-camouflage-lanes.md` 重跑两条 lane；
  漂移门禁会以 `nginx-stealth` / `xhttp-front` 报出回归。

## 边界

## 客户端侧实战验收（2026-10-31，补齐 live_accepted）

从操作者 Windows 主机（国内运营商路径，非 VPS 本机）用真实客户端跑通全部车道：

- 客户端：Xray-core `v26.10.29 windows/amd64`（对官方 `.dgst` 校验 SHA256 一致）、
  sing-box `v1.14.4 windows/amd64`（对 GitHub release asset `digest` 校验 SHA256 一致）。
- 先做网络层：`fq.sciman.top` 的 443/21470/10094/33387 与 `zz.sciman.top` 的 22752/12378
  全部 TCP 可达；`https://fq.sciman.top/`、`:10094/`、`:33387/` 均返回 200 伪装页。
- bwg：`vision-tcp443`、`xhttp-reality21470`、`xhttp-tls10094`、`hysteria2-base(443/udp)`、
  `hysteria2-obfs(8444/udp, salamander)` 全部 204。
- zz：`reality-singbox(22752)`、`hysteria2-base(443/udp)`、`hysteria2-obfs(8444/udp)` 全部 204。
- 凭据只经 SSH 读入内存并写入临时客户端配置，未打印；跑完后已把配置文件就地清空
  （临时目录中的客户端二进制由系统清理）。

这层证据说明“此刻从国内路径用真实客户端能连上每一条车道”，仍不是对长期可用性的承诺：
GFW/运营商策略会变，后续仍以 `proxy_hardening_drift.ps1` 与 `proxy_lane_acceptance.ps1`
作为日常判据。
