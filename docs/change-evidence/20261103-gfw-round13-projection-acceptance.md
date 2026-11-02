# 2026-11-03 GFW 第 13 轮审查投影与实战验收（bwg + zz）

## 范围

- 代码基线：`7ba6d1f`（第 13 轮 GFW 专项审查修复，13 文件 +319/-95）；
  投影批次的仓内增量（zz 前置模板对齐 + 原子写）随收口提交推送。
- 真实远端写入：bwg 与 zz 各两次（merged-sub 重发布、zz 前置 Apply）；无内核/服务升级。
- 全程只读探针先行（drift 双机 NONE、acceptance 双机 PASS baseline、
  #7180 大上传实测双机），写入均为幂等 lane `-Apply`，备份 + 回滚 + 就绪门在位。

## 变更与判据

### 1. merged-sub 重发布（bwg + zz）

- 背景：第 13 轮审查 P2——节点名 `__PREFIX__` 占位符三重替换失效（`a91597c`），
  双机在役订阅节点名为字面 `__PREFIX__-*`。
- 判据：`MERGED_SUB_APPLIED`；发布后服务端解码核对节点名前缀
  bwg=`#bwg-*`（7/7）、zz=`#zz-*`（7/7）；`merged_sub_http=200 served=7`。
- 备份：`/var/backups/merged-sub.MbUif3`（bwg）、`/var/backups/merged-sub.c1RB2H`（zz）。
- 回滚入口：lane 自带回滚（verify 失败自动还原 previous）；人工恢复用备份目录。

### 2. zz XHTTP 前置 Apply（仅 zz）

- 背景：第 13 轮新增的 `conf_current` 模板字节比对报 drift——在役
  `vps-launcher-zz-xhttp-front.conf` 由共享模板（bwg 车道）投影，而 zz 变体
  车道模板缺 HSTS 行与 404 品牌注释（变体抽取时的历史分叉），且文件头
  provenance 指向 bwg 脚本名。
- 处置：zz 变体模板逐字对齐在役已验证状态（HSTS 保留、品牌 404 注释），写入
  改为兄弟临时文件 + mv（与 bwg 车道同款原子写）；`-Apply` 一次收敛
  （xray restart + nginx reload，就绪门 6 次退避）。
- 判据：`ZZ_XHTTP_APPLIED`；复跑 dry-run `zz_xhttp=applied`（noop 短路命中）；
  在役文件头 `Managed by ... zz_xhttp_nginx_front.ps1` + HSTS 计数 1；
  acceptance zz `xhttp-tls PASS`；drift `xhttp_front=applied`。
- 备份：`/var/backups/zz-xhttp-front.Rk1CAA`。
- 回滚入口：lane 自带回滚（备份还原 inbound.json + previous.conf +
  xray/nginx 复验）；人工恢复用备份目录。

### 3. Xray #7180 大上传实测（双机，只读，未改任何配置）

- 假设：XHTTP stream-up + nginx h1 upstream + 服务端 padding（默认
  `scStreamUpServerSecs "20-80"`）在大上传时截断（上游 #7180）。
- 方法：服务端临时 receiver（0.0.0.0 随机端口）+ 临时 xray 客户端（socks→
  vless xhttp-tls 经 10094 前置，mode=auto，alpn h2）+ 8MB 随机体
  `--limit-rate 60k`（~136s，跨越 padding 时间窗）；直连对照组同尺寸限速。
- 结果：bwg 隧道 `RECV=8388608 EXPECT=8388608`；zz 隧道（目标公网 IP
  hairpin）同全量；两机直连对照全量。**#7180 截断在 26.10.31 现役栈不复现**
  （上游 #7180 已 closed 2026-10-16，26.10.31 疑含传输层修复）。
- 决策：`scStreamUpServerSecs: -1` 修改**不实施**（无证据不写远端；保留
  padding 机制对 CDN 空闲掐流的防护语义）。
- 探针方法论（复用注意）：隧道 egress 目标必须用主机公网 IP
  （bwg=直接绑卡、zz=NAT hairpin 均可达）；loopback/私网目标经隧道会
  被服务端出站链吞掉（双机一致，服务端无响应，属探针假象，与生产流量无关）。

## 终态 receipt（2026-11-03）

- drift：bwg `HARDENING_DRIFT=NONE`（merged_sub ok nodes=7 fetch ok、
  xhttp_front applied）；zz 同（含 `singbox_google_ipv4=ok`、`cdn_uuid=match`、
  `tls_expiry=ok` 新审计段）。
- acceptance：bwg PASS×8（含新增 `reality-xray-alt` 实战首跑）；
  zz front Apply 后复跑 PASS×8。
- 门禁：`run_gates.ps1` 全绿（pytest 1050 passed / 18 skipped +
  ruff/format/mypy）；zz 模板对齐后 `test_zz_parity_lanes` +
  `test_herestring_escaping` 13 passed + 载荷 `bash -n` 通过。

## 风险与残留

- vasma 全量重装会重建 14 文件与前置 vhost：按 runbook 重放 zz 前置 Apply 与
  merged-sub 重发布即可（lane 幂等）。
- 订阅节点名变更对客户端无影响（`#` 后为显示名）；已导入设备按原 token 拉取。
- 遗留：zz NAT 内网地址（10.5.69.2）为本机唯一 v4 全局地址，探针须走公网
  hairpin；无需处置。
