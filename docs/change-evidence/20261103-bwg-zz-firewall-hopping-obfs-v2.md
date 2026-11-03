# 2026-11-03 bwg / zz 防火墙、端口跳跃与 obfs 密钥分离

## 范围

- 真实远端写入：bwg、zz 各执行一次 Hysteria2 obfs v2 迁移、合并订阅重发布、
  `firewall_baseline.ps1 -Apply`，并按 bwg→复验→zz 顺序逐台完成。
- 没有修改 Xray/sing-box 版本、监听端口、Reality 密钥、UUID、base Hysteria2
  认证口令、证书、Cloudflare token、CPA/OAuth 凭据或并发策略。

## 初始证据与问题

- 双机 `proxy_hardening_drift.ps1` 初始为 `HARDENING_DRIFT=NONE`，所有既有 lane
  的受控验收为 PASS；这只是主机侧基线，不是中国运营商自然流量验收。
- nftables `INPUT` 原为 `policy accept`，zz UFW inactive。当前公开监听均为已知
  服务，但任何后续误绑定都会直接暴露。
- 双机 Hysteria2 obfs 入站的 `users[0].password` 与 `obfs.password` 同值，
  属不必要的协议层密钥复用。
- bwg 的直连公网 IP 从主机自身访问 20001 不完成 PREROUTING hairpin；外部
  zz 客户端访问同一目标可以完成真实 Hysteria2 会话。

## 代码与主机变更

### 1. Hysteria2 v2 密钥分离

- 新增基于 CSPRNG 的独立 obfs 口令，认证口令改为沿用 base Hysteria2 用户。
- 新本地文件：`<profile>-hysteria2-obfs-password-v2.txt`，不在输出中回显秘密。
- bwg 备份：`/var/backups/hysteria2-obfs.xS354F`
- zz 备份：`/var/backups/hysteria2-obfs.95XtCc`
- 双机 Apply 后，base 443 与 obfs 8444 均保持监听；运行时片段验证认证口令
  来自 base、obfs 口令独立且非空。

### 2. 合并订阅

- `*-hy2-obfs` 链接加入 `mport=20000-20999`；认证与 obfs 凭据保持分离。
- bwg 最终备份：`/var/backups/merged-sub.PqY1mG`
- zz 最终备份：`/var/backups/merged-sub.JeE4DL`
- 双机实际 GET 均为 `merged_sub_http=200 served=7`，凭据导入检查通过。

### 3. 防火墙与端口跳跃

- 新脚本 `scripts/firewall_baseline.ps1` 只管理独立
  `inet vps_launcher_guard` 表，不改写 vasma/Docker 规则。
- INPUT 默认 drop；仅供当前公开服务端口、SSH、loopback、ICMP、DHCP 通过。
- PREROUTING 将 UDP `20000-20999` REDIRECT 到 obfs `8444`；OUTPUT 为同机公网
  hairpin 提供同路径规则，但 bwg 的直接绑定 IP 仍不保证主机自测成功。
- systemd oneshot `vps-launcher-firewall.service` 已 enable + active，并完成
  同机 restart 重放测试。
- bwg 最终备份：`/var/backups/firewall-baseline.N2ZLXW`
- zz 最终备份：`/var/backups/firewall-baseline.klKhyO`

## 验收 receipt

- 双机最终 drift：`HARDENING_DRIFT=NONE`，含 `firewall_baseline=applied`。
- 双机 `proxy_lane_acceptance.ps1`：`ACCEPTANCE_RESULT=PASS`。
- zz：`hysteria2-obfs-hop-public RESULT=PASS port=20001`。
- bwg：由 zz 发起外部 Hysteria2 客户端经 `fq.sciman.top:20001` 获取出口；
  `CROSS_HOST_HOP=PASS`，bwg PREROUTING counter 增加。
- 未列入 allowlist 的临时 TCP 29999：loopback 200，公网连接超时/000；
  临时服务已停止，双机端口清理。
- 防火墙 oneshot restart 后，新 SSH 会话、Xray、nginx、sing-box 和全部代理
  lane 仍正常。
- 全量门禁：`1074 passed, 18 skipped, 571 subtests passed`；bandit、Ruff、
  format、mypy 全绿。跳过项为显式 integration opt-in 与 Windows Git Bash
  缺少 `flock`。

## 回滚

- Hysteria2：从对应 `hysteria2-obfs.*` 备份恢复源片段与 merged config，
  运行 `sing-box merge/check`、重启 sing-box，并复核 base 443/obfs 8444。
- 订阅：从对应 `merged-sub.*` 备份恢复发布文件，再验证三入口 GET 与导入。
- 防火墙：从对应 `firewall-baseline.*` 恢复 `rules.nft`、unit 与 live table；
  首轮无表时删除 `inet vps_launcher_guard` 即可恢复原 accept 状态。
- 若需彻底撤销 hop，删除 managed firewall 表/服务并重发布不含 `mport` 的订阅；
  不要删除固定 8444 obfs 入站，除非同时迁移所有客户端。

## 风险与未覆盖

- 端口跳跃只处理单端口/UDP 特征限制；UDP 整体封锁或长期 QoS 时仍回退
  Vision、XHTTP 和 Cloudflare 车道，不能与 Mimic 混用。
- bwg 的主机内公网 IP hairpin 失败是已知网络路径边界，不能据此否定外部客户端
  已验证的 hop；日常自动验收只在 zz 做主机内 hop，bwg 需要跨主机探测。
- 没有中国运营商真实终端下的长期自然流量数据；本轮 PASS 不证明不会被后续
  封锁、不会 QoS、不会出现 IP/域名级风控。
