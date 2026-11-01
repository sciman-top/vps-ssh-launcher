# 2026-11-01 zz 备用 SNI 补齐 + 双机伪装根残留清化

用户授权的连续修复批次，承接 2026-11-01 深度审查（当时的判定：两台代理车道
`HARDENING_DRIFT=NONE`、`ACCEPTANCE_RESULT=PASS`，剩余风险在主机可观测面与
外部前提）。两组受控远端写入：zz 补第二 SNI，以及 bwg/zz 伪装根模板残留清化。
两项都由本仓脚本执行，备份优先、`nginx -t`/`xray run -test` 门禁、失败回滚。

## A. zz 备用 SNI（`static-zz.sciman.top`）

### 前提
bwg 的 REALITY 入站接受 `fq.sciman.top` + `static.sciman.top`；zz 之前只有单一
`zz.sciman.top`，精确主机名封锁会一次性带走 5 条直连车道。`reality_alt_sni_lane.ps1`
早已就绪，zz 默认名 `static-zz.sciman.top`，但当时该名字**无 A 记录**（`static.sciman.top`
指向 bwg 的 IP，无法复用），所以此前是 `reality_alt_sni=not-applied`。

### DNS
- 通过 bwg 的 acme Cloudflare DNS token（只有 DNS 权限）在 `sciman.top` 建一条
  **灰云** A 记录：`static-zz.sciman.top → 38.244.39.84`（TTL 120）。
- 复核：`1.1.1.1` 解析到 `38.244.39.84`，与 zz 公网 IP 一致。

### 应用与验收
- `reality_alt_sni_lane.ps1 -Profile zz`（只读预演）：2 个 REALITY 入站，
  `public_ip == alt_dns == 38.244.39.84`，计划 `add-server-name,write-vhost,
  restart-xray,reload-nginx,verify-lane`。
- `-Apply`：`nginx -t` ok；`alt_sni_handshake=ok name=static-zz.sciman.top
  port=35622`；真实 REALITY 客户端 `alt_sni_lane=ok egress=38.244.39.84`；
  备份 `/etc/v2ray-agent/xray/conf/backup-alt-sni-20261101T092517Z`。
- `merged_subscription_lane.ps1 -Profile zz -Apply`：`merged_sub_nodes=7
  http=200 served=7`，`zz-reality-vision-alt` 节点进入合并订阅
  （`merged-subscription-url-zz.txt`）。
- 门禁复核：`reality_alt_sni=ok missing=2/0`、`alt_vhost=ok`、
  `HARDENING_DRIFT=NONE`。

回滚：删 DNS A 记录；`reality_alt_sni_lane.ps1` 从备份恢复 Xray conf 与删除
`vps-launcher-alt-sni.conf`、标记；重跑合并订阅 Apply。

## B. 伪装根模板残留清化（bwg + zz）

### 代码变更
`scripts/nginx_camouflage_site.ps1` 在 stock `50x.html` 清理之外，新增对模板制作
残留的识别与清化；`scripts/proxy_hardening_drift.ps1` 新增 `camouflage=residue`
漂移判据。判据：
- 目标：`*.psd`、`*.zip`、`psd/`、`__MACOSX/`、`._*`、`.DS_Store`、`Thumbs.db`、
  `Dockerfile`、`README.md`、`manifest.json`、`precache-manifest.*`、`*.worker.js`。
- 保护：`/s/` 合并订阅目录 `--exclude-dir=s`；任何被 `*.html` 或 `en/` 引用的项保留
  并列入 `residue_kept_referenced`。PSD 与缩略图缓存视为永不 serving，直接清理。
- 脚本自身不做字符串匹配误删：`kshop16.xml`（vasma 模板 XML）不在目标集合内，保留。

### bwg
- 只读预演：`residue_removable= img/nav/Thumbs.db img/pro.psd img/Thumbs.db
  images/Thumbs.db en/img/Thumbs.db en/images/Thumbs.db`（6 项；顶层 extras 已于
  2026-10-31 手工隔离，本轮清到嵌套的 `img/pro.psd` 与 Thumbs.db）。
- `-Apply`：`RESIDUE_REMOVED` 6 项，`nginx -t` ok，`ROOT_INDEX_SHA` 未变，
  `CAMOUFLAGE_APPLIED`。行为：`/img/pro.psd`、`/img/Thumbs.db`、`/en/images/Thumbs.db`
  均 404；`/` 仍返回伪装页。

### zz
- 只读预演列出完整集合：`__MACOSX/` 整树、`psd/*.psd`（5）、`html3/4/5/6.zip`、
  `README.md`、`Dockerfile`、`manifest.json`、2×`precache-manifest`、2×`*.worker.js`、
  `img/pro.psd`、多级 `Thumbs.db`。
- 首次 `-Apply`：`rm -f` 对目录 `__MACOSX` 失败（`Is a directory`）→ 自动
  `ROLLBACK_VERIFIED`，未造成残留；据此修正为目录用 `rm -rf`、文件/符号链接用
  `rm -f`（先 `-d` 判定）。
- 第二次 `-Apply`：全部 `RESIDUE_REMOVED`，`nginx -t` ok，`CAMOUFLAGE_APPLIED`。
  web root 大小 87 MB → 13 MB。
- 行为：`/__MACOSX/`、`/README.md`、`/Dockerfile`、`/html4.zip`、`/psd/index.psd`、
  `/manifest.json`、`/img/pro.psd` 均 404；`/` 仍返回 `<title>geili Forging</title>`。

### 双机验收
- `proxy_hardening_drift.ps1`：bwg / zz 均 `HARDENING_DRIFT=NONE`。
- `proxy_lane_acceptance.ps1`（含公网真实握手）：bwg / zz 均 `ACCEPTANCE_RESULT=PASS`。
- 仓库：`pytest` 972 passed / 13 skipped（flock 在 Git Bash 缺失）；`ruff check` 通过。

## 限制
- 这仍是主机侧受控验收，不等于中国网络真实客户端/运营商的长期可用性。
- 备用 SNI 只扛“精确主机名 SNI 封锁”；IP 封锁仍靠 CDN 车道，整段域名封锁需独立域。
- Hysteria2 obfs 口令与认证口令同值、REALITY `mldsa65` 关闭等既有取舍未改，属会破坏
  客户端配置、需单独授权的收紧项。

## 回滚
- 代码：还原 `scripts/nginx_camouflage_site.ps1`、`scripts/proxy_hardening_drift.ps1`
  与两个测试；已部署的清理不会自行恢复，需按 `/var/backups/nginx-camouflage.*`
  回拷。
- 远端：每台 camouflage `-Apply` 的 `APPLY_BACKUP_DIR` 是整根备份，`cp -a` 回拷即恢复；
  zz 的 alt-SNI 按 A 节回滚。
