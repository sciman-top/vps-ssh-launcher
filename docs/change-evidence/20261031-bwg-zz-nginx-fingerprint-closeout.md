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
  （backup `/var/backups/xhttp-front.TE0BiN`）；读回
  `nginx_owns_public=1 inner_listening=1`、`xhttp_front=applied`。
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

## 回滚

- 任一台恢复：把 backup 目录里的 `previous.conf` 放回
  `/etc/nginx/conf.d/vps-launcher-stealth.conf`（或删除该文件）后 `nginx -t && systemctl reload nginx`。
- bwg 的 XHTTP 前置恢复：`cp /var/backups/xhttp-front.TE0BiN/xray/inbound.json` 回
  `14_VLESS_XHTTP_TLS_inbounds.json`，删除 `vps-launcher-xhttp-front.conf`，
  `xray run -test` → `nginx -t` → `systemctl restart xray` → `systemctl reload nginx`。
- vasma 重装后按 `docs/runbooks/proxy-camouflage-lanes.md` 重跑两条 lane；
  漂移门禁会以 `nginx-stealth` / `xhttp-front` 报出回归。

## 边界

以上是主机发起的受控验收与外部探测，不等于中国网络下真实客户端的自然验收。
