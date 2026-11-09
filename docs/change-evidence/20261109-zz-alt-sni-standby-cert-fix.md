# zz 备用订阅入口证书失配修复（2026-11-09）

**性质**：一轮 GFW 抗封锁专项复查 + 一处真实缺口修复 + 受控远端写入
**授权**：用户要求"全面深度审查 bwg/zz 上的 v2ray-agent / vasma、VLESS + REALITY +
Vision、Hysteria2、XHTTP、CDN 是否存在问题，能否修复/优化，必要时修复本项目代码"。
**范围**：只读审查两台主机全部车道与仓内对应脚本；实质写入仅 zz 的一次幂等
`reality_alt_sni_lane.ps1 -Apply`，未轮换 UUID、REALITY 密钥、shortId、Hysteria2
认证口令或 obfs 口令，未改 CPA 与账号状态。

## 1. 审查结论先行

两台主机当前状态本身是健康的：`HARDENING_DRIFT=NONE`（bwg/zz，修复前后各一次），
Xray 26.10.31、sing-box 1.14.4、nginx 1.31.8，内核 `6.8.0-149` 已含
CVE-2026-98432 修复，MPTCP 在运行时与持久化两侧都关闭；伪装根无 stock 页、
无模板残留、品牌 404 生效；REALITY 无空 shortId、Vision flow 在位、回落目标为本域
伪装 vhost；XHTTP 10094 有 nginx 前置且未知 SNI 拒绝；Hysteria2 base 有 masquerade、
obfs 车道独立口令；CDN 源站证书覆盖 `cdn.sciman.top` / `zzcdn.sciman.top` 且边缘 200；
firewall 为独立 `inet vps_launcher_guard` 默认 drop + UDP 20000–20999 跳跃。

但**订阅恢复链上有一个真实的、一直存在的坏点**，是本轮唯一的功能性缺口。

## 2. 缺口：zz 的第二直连订阅入口从未通过主机名校验

`docs/runbooks/proxy-client-failover.md` 把"主 SNI 被精确封锁"时的动作定义为
"改用第二直连入口"，zz 的该入口是 `https://static-zz.sciman.top:12378/s/merged/<token>`。

实测（从 Windows 与从主机 loopback 双向验证）：

```text
https://static-zz.sciman.top:12378/...  =>  curl -k 200   curl（校验证书）SEC_E_WRONG_PRINCIPAL / 000
https://static-zz.sciman.top:35622/...  =>  200
```

原因不是证书过期，而是**该端口根本没有这个 server_name**：`static-zz.sciman.top`
只被 `reality_alt_sni_lane.ps1` 投影到 REALITY 回落端口（35622），而 12378 上是
vasma 的单域证书 `zz.sciman.top`（SAN 只有 `DNS:zz.sciman.top`）。主机侧同一现象：

```text
zz.sciman.top:12378          subject=CN = zz.sciman.top    DNS:zz.sciman.top
static-zz.sciman.top:12378   subject=CN = zz.sciman.top    DNS:zz.sciman.top   <- 失配
static-zz.sciman.top:35622   subject=CN = *.sciman.top     DNS:*.sciman.top, DNS:sciman.top
```

为何一直没被发现：门禁与车道都用 `curl -k` 只验"HTTP 200"，`-k` 恰好跳过唯一
会失败的那一步。这是**验证方式与恢复场景不匹配**，不是单纯配置笔误——所以修复
同时补了验证强度。

## 3. 修复

### 3.1 `scripts/reality_alt_sni_lane.ps1`

- 在把 `-AltName` 追加到 REALITY `serverNames` 之外，额外在订阅端口生成
  `/etc/nginx/conf.d/vps-launcher-alt-sni-sub.conf`：同一张通配证书、同一静态根、
  只 `try_files`。不新增反向代理，因此不扩大凭据面（`/s/merged/` 是静态文件）。
- 订阅端口从 `subscribe.conf` 的 `listen ... ssl` 读，若与回落端口相同则不生成
  （bwg 的 33387 即是这种情形，避免重复 server）。
- 幂等短路条件从"回落 vhost + serverNames"扩展为"再加上订阅端口 vhost"，因此已
  应用过的旧主机不会因为短路而永远补不上这一层。
- 新增验收判据 `alt_sni_standby_handshake=ok`：用
  `openssl x509 -checkhost <alt>` 判定，而不是"握手成功"。失败即 `exit 8`，
  走原有 EXIT trap 回滚；回滚会删除本次新建的 standby vhost（有备份则还原）。
- 备份、`nginx -t`、`xray run -test`、HUP/INT/TERM 路由均沿用原实现。

### 3.2 `scripts/merged_subscription_lane.ps1`

- 发布前对第三入口做一次真实 GET，用系统 CA 库（`--cacert`），不用 `-k`，
  输出 `merged_sub_standby_verify=verified|unverified`。失败时打印
  `merged_sub_standby_failed host=... port=... http=...`，但**不**把车道判成失败：
  merged 列表本身仍由直连与 CDN 入口保证。

### 3.3 `scripts/proxy_hardening_drift.ps1`

- 新增 `alt_standby_vhost` 判据：回落端口与订阅端口不同时，要求 standby vhost
  在位且 `-checkhost` 通过；相同则报 `not-needed same-as-fallback`。
- 新增 `merged_sub_standby` 判据：用 `--cacert` 复核第三入口；失败归入
  `reality-alt-sni` 漂移（不新开漂移族），因此 `proxy_hardening_lane_recover.ps1`
  可直接重放。

### 3.4 测试与文档

- `tests/test_reality_alt_sni_lane.py`：standby vhost 生成、订阅端口推导、
  `-checkhost` 判据、回滚删除、声明顺序。
- `tests/test_merged_subscription_lane.py`：`--cacert` 而非 `-k`、判据行、
  bwg/zz 两个 Standby 取值。
- `tests/test_proxy_hardening_drift.py`：两个新判据与"同端口不需 standby"的分支。
- `docs/runbooks/proxy-camouflage-lanes.md` 与 `proxy-client-failover.md`：
  记录缺口成因、修复与"必须做主机名校验"的约束。

## 4. 远端写入与证据

仅 zz 一次（bwg 无需改动，其回落端口即订阅端口）：

```text
alt_sni_standby_vhost=/etc/nginx/conf.d/vps-launcher-alt-sni-sub.conf port=12378
nginx: configuration file /etc/nginx/nginx.conf test is successful
alt_sni_handshake=ok name=static-zz.sciman.top port=35622
alt_sni_standby_handshake=ok name=static-zz.sciman.top port=12378
alt_sni_lane=ok
APPLY_BACKUP_DIR=/etc/v2ray-agent/xray/conf/backup-alt-sni-20261109T151929Z
ALT_SNI_APPLIED
```

修复后：

- `merged_subscription_lane.ps1 -Profile zz`：`merged_sub_standby_verify=verified`；
  6 条已发布入口从公网（Windows，不加 `-k`）全部 200：
  bwg `fq/static/cdn`、zz `zz/zzcdn/static-zz`。
- 漂移门禁：bwg `HARDENING_DRIFT=NONE`（`alt_standby_vhost=not-needed`、
  `merged_sub_standby=verified`）；zz `HARDENING_DRIFT=NONE`
  （`alt_standby_vhost=ok port=12378`、`merged_sub_standby=verified`）。

## 5. 本轮审查确认"不修"的项（有证据）

| 项 | 证据 | 结论 |
|---|---|---|
| REALITY `limitFallbackUpload/Download` | `xray tls ping fq.sciman.top:33387`：cert chain 3393 B、无 X25519MLKEM768 | 官方明确"本身即可疑指纹、面板/脚本应随机化"，回落目标为本域 vhost 时无端口转发滥用面；**不加** |
| REALITY `mldsa65` | 同上，cert < 3500 B 且无 ML-KEM | 强开会变成新指纹，维持关闭 |
| `spiderX` | 车道探针用 `/`，vasma 节点不带该字段 | 客户端可选字段，subscription 不写不构成失配 |
| Hysteria2 `ignore_client_bandwidth` | 两台均未设；`up/down_mbps 100/50` | 显式限速是既有决策，未在无容量证据下改动 |
| Hysteria2 `gecko` 混淆 | sing-box 1.14.4 支持但客户端矩阵未验 | 维持被广泛兼容的 `salamander` |
| UDP 8429/BT 等 vasma 跳端口 | 未在任何一台上启用 | 无现状可改 |
| Cloudflare `2087` 中国大陆覆盖 | CF China Network 仅 80/443 | 换 443 需 Origin Rule/Worker，属新故障域，未做 |
| Xray 26.11.9（Chrome 155 指纹） | 现网 26.10.31 已含 MPTCP 缓解且门禁全绿 | 不因"有新版"盲升，须单机灰度 |

## 6. TLS cipher 面：已在第二轮收敛（原为记录项）

第一轮只记录未改；用户随后授权连续修复，第二轮已收敛。收敛前的观测：

```text
fq.sciman.top:33387        3 suites (GCM only)
static.sciman.top:33387    5 suites (+2 CBC)
cdn.sciman.top:2087        5 suites (+2 CBC)
fq.sciman.top:10094        5 suites (+2 CBC)
static-zz.sciman.top:12378 5 suites (+2 CBC)
```

收敛详情见第 8 节。

## 8. 第二轮（用户授权连续修复）

### 8.1 TLS 1.2 cipher 面收敛

新增 `scripts/lib/proxy_tls_contract.ps1`，列表与 vasma 写入 `subscribe.conf` 的
内容**逐字节一致**，并提供带正则校验的 `Get-ProxyTlsCipherSuite`。四个 lane
（CDN、XHTTP 前置、zz XHTTP 前置、alt-SNI 的两个 vhost）统一渲染该列表。
`reality_alt_sni_lane.ps1` 的幂等短路现在同时要求两个 vhost 带 cipher 行，
否则"名字已在但 cipher 未投影"的主机会被永久短路。漂移门禁新增
`==tls-cipher-surface==` 逐文件比对，异常归入 `nginx-stealth`。

收敛后实测（双机一致）：

```text
fq/static:33387 · cdn:2087 · fq/zz:10094 · static-zz:12378/35622 · zzcdn:2087
  → AES128-GCM-SHA256 / AES256-GCM-SHA384 / CHACHA20-POLY1305（3 套件，无 CBC）
```

远端写入逐台（bwg 先、zz 后），三个 lane `-Apply` 全部成功，未重启 xray
（inbound 未变，只有 nginx 模板变更）。备份：bwg `xhttp-front.pYhxK5` /
`cdn-lane.FRCthE` / `backup-alt-sni-20261109T154814Z`；zz `xhttp-front.z3N3vc` /
`cdn-lane.OfcEWm` / `backup-alt-sni-20261109T154957Z`。

### 8.2 验收器误报：探针未固定 IPv4（真实缺陷）

收敛后逐机验收，zz 的失败车道**在轮次间轮转**（第 1 轮 3 条、第 2 轮另外 3 条，
均为 `curl-exit-35`）。逐层定位：

| 检查 | 结果 |
| --- | --- |
| 服务端 TLS 握手 30 次 × 5 个端口 | 150/150 成功 |
| 服务端 HTTP 取订阅 20 次 × 4 个入口 | 80/80 成功 |
| 按验收器精确时序手写探针 3 轮 | 3/3 一次成功 |
| 主机直连 `www.gstatic.com/generate_204` | 20/20 成功 |
| 主机 `openssl s_client www.gstatic.com:443` | **0/20** |
| `curl -4` 强制 IPv4 | 10/10 成功 |
| zz 全局 IPv6 地址 / 默认路由 | **均无** |
| `getaddrinfo www.gstatic.com` | v4 之后仍返回 8 条 AAAA |

根因：zz 是 NAT 主机，没有全局 IPv6 地址也没有 v6 默认路由，但解析仍返回 AAAA；
验收器启动的临时 Xray 客户端用默认 `freedom` 策略，可能选到不可达的 v6 目标，
表现为 `curl-exit-35`。这与代理车道无关，是**探针把主机网络属性误判成车道失败**。

修复：四处 Xray 探针出站（reality / reality-alt / reality-public / cdn-ws）统一加
`"sockopt": {"domainStrategy": "UseIPv4"}`。Hysteria2 探针拨本机回环或本机公网 IP，
不解析外部域名，不需改动。

修复后：zz 连续 3 轮 `ACCEPTANCE_RESULT=PASS`（12/12 无 FAIL），bwg 连续 2 轮
`ACCEPTANCE_RESULT=PASS`（11/11）。

### 8.3 已撤回的假设

首次定位时曾把 `-TimeoutSeconds` 默认值从 12 提到 30，并据此推测失败是"冷启动
超时"。**该假设被证伪**：提高后仍出现 `curl-exit-35`（SSL 错误 35 与超时 28
是不同错误码）。默认值 30 作为独立改进保留（冷启动确实可能超过 12 秒），但它对
本次缺陷的因果解释已撤回，不作为结论。

## 9. 回滚

- zz 远端：`cp -a /etc/v2ray-agent/xray/conf/backup-alt-sni-20261109T151929Z/` 还原
  两个 xray conf；`rm -f /etc/nginx/conf.d/vps-launcher-alt-sni-sub.conf`；
  `nginx -t && systemctl reload nginx`。marker 与回落 vhost 不受影响。
- 本地代码：`git revert <本提交>`；回滚后 `merged-subscription-url-zz.txt` 会回到
  `static-zz.sciman.top:12378`，该 URL 在旧 vhost 下仍会失配——回滚只用于撤销代码，
  不是恢复可用性的手段。
