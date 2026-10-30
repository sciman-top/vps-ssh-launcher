# BWG/ZZ 代理抗封锁硬化与受控验收（2026-10-30）

## 范围

用户授权：按推荐连续修复优化至完成，逐台执行，任一时刻至少一台保持可连通。
本轮真实远端写入只有三类，均不改上游 vasma：

1. nginx 伪装页提升（bwg、zz）
2. REALITY 空 shortId 剥离（bwg xray、zz sing-box）
3. zz 新增 Hysteria2 obfs 备用入站（UDP 8444）

无 CPA 配置、无 OAuth 凭据、无证书、无 Reality 密钥/UUID/Hysteria2 密码轮换。

## 变更与证据

| 项 | bwg | zz |
| --- | --- | --- |
| 伪装页根 index | 896B 默认页 → 5905B 企业站 | 同左 |
| 备份 | `/var/backups/nginx-html-camouflage.20261030T151512Z` | `/var/backups/nginx-camouflage.CxJJ9X` |
| REALITY shortId | `[""]` → `["6ba85179e30d4fc2"]` | 同左 |
| 备份 | `/var/backups/reality-shortid.K0LnmY` | `/var/backups/reality-shortid.3q7fAj` |
| Hysteria2 obfs | 不适用（无 hy2 栈） | UDP 8444 salamander |
| 备份 | — | `/var/backups/hysteria2-obfs.Iw62l8` |

## 受控验收（真实握手，非服务状态）

`scripts/proxy_lane_acceptance.ps1` 在本机用已部署二进制发起客户端握手，经隧道取
`gstatic/generate_204`，判据行 `ACCEPTANCE_RESULT=PASS`：

```text
bwg: LANE=reality-xray RESULT=PASS port=45987 sid=6ba85179e30d4fc2
     LANE=hysteria2-base RESULT=N/A   (Xray-only host)
     ACCEPTANCE_RESULT=PASS
zz:  LANE=reality-singbox RESULT=PASS port=22752 sid=6ba85179e30d4fc2
     LANE=hysteria2-base RESULT=PASS port=443
     LANE=hysteria2-obfs RESULT=PASS port=8444 obfs=salamander
     ACCEPTANCE_RESULT=PASS
```

未认证探测（模拟主动探测）现在得到伪装站：bwg 443 → 200/5905B，zz 35622 → 200/5905B，
而不再是默认 "Welcome to nginx!" 页。

## 投影与持久性

- 三脚本只读预演均为幂等终态：`camouflage=applied`、`shortid_hardening=clean`、
  `obfs_lane=already_configured`。
- zz 的内核周更 wrapper 以 `-C $SINGBOX_SOURCE_DIR/` 合并整个源目录，因此
  `98_vps_ssh_launcher_hysteria2_obfs.json` 周更后仍生效。
- vasma 重装会重写 `06/07` 源配置并可能还原空 shortId/默认页；届时按
  `docs/runbooks/proxy-camouflage-lanes.md` 重跑前两者即可。

## 明确未做

- `mldsa65`：实测无 `pqv` 的客户端被 reset，不向后兼容，会断未重拉订阅的设备。
- bwg REALITY dest 改外部站：该 dest 是 ChatGPT 桌面端 CPA 443 车道的回落命脉。
- bwg 新建 sing-box/Hysteria2 栈：属新建服务而非优化，需单独授权。

## 回滚

文件级变更回滚用上表备份目录（伪装页整根备份、shortId 与 obfs 源配置备份）。
obfs lane 回滚：删除
`/etc/v2ray-agent/sing-box/conf/config/98_vps_ssh_launcher_hysteria2_obfs.json`，
重新 `sing-box merge` 后重启 `sing-box`。Git 回滚只撤销仓库文件，不代替远端恢复。

## 证据边界

本收据为 `host_loaded` + 本机回环受控握手；**不等于** `live_accepted`。真实运营商/
GFW 封锁下的长期可用性需从授权客户端在真实网络观察，未在本轮证明。
