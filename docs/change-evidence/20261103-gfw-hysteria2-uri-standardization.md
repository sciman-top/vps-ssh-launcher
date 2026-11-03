# 2026-11-03 Hysteria2 端口跳跃订阅兼容收敛（bwg + zz）

## 范围

- 审查对象：bwg / zz 的 vasma、VLESS+REALITY+Vision、REALITY/TLS XHTTP、
  Hysteria2 base/obfs、Cloudflare WebSocket、订阅分发和 nftables 端口跳跃。
- 真实远端写入：先按 authority 格式投影 bwg/zz，随后依据 v2rayN 实机结果回退为
  `mport` 兼容格式并再次投影 bwg/zz。
- 未修改：Xray/sing-box/nginx 版本、UUID、REALITY 密钥与 shortId、Hysteria2
  认证口令、obfs 口令、证书、Cloudflare token、CPA/OAuth 凭据或并发策略。

## 初始证据与问题

- 双机只读漂移基线为 `HARDENING_DRIFT=NONE`，现有所有车道受控握手 PASS。
- 现网合并订阅中的 `*-hy2-obfs` 使用私有查询字段 `mport=20000-20999`：
  bwg、zz 均为 `hop_authority=no`、`hop_mport=yes`。
- 第一轮按 Hysteria2 官方 authority 多端口格式发布后，v2rayN 实际只显示
  12/14 个节点。OCR 与订阅源对照确认两台均缺少 `*-hy2-obfs`。
- v2rayN 当前 `Hysteria2Fmt` 只从 `mport` 查询字段读取端口范围，并把
  authority 中的端口交给 `.NET Uri.Port`；`8444,20000-20999` 导致整条 URI
  被跳过。

## 代码修复

- `scripts/merged_subscription_lane.ps1` 使用单一数字主端口 `8444` 加
  `mport=20000-20999`。
- `scripts/zz_subscription_republish.ps1` 使用相同兼容格式，并移除 Hysteria1
  风格的 `peer=` / `insecure=0`，保留官方 `sni=`。
- `scripts/proxy_hardening_drift.ps1` 要求单一端口且存在 `mport` 范围；带逗号的
  authority 多端口会被判为 `obfs-hop`。
- 新增行为级测试：实际执行合并订阅生成器、zz 渲染器和漂移 URI 校验器，锁定
  v2rayN 可导入格式，不再只做源码字符串断言。

## 远端投影

### bwg

- 命令：`merged_subscription_lane.ps1 -Profile bwg -Apply`
- 结果：`merged_sub_nodes=7`、HTTP 200、7 节点、URI 校验 `ok`
- authority 试验备份：`/var/backups/merged-sub.y5qCG4`
- 最终兼容投影备份：`/var/backups/merged-sub.9Zl4nD`

### zz

- 命令：`zz_subscription_republish.ps1 -Profile zz -Apply`
- 结果：`zz_sub_nodes=5 tls_node=1`、HTTP 200、5 节点
- authority 试验备份：`/var/backups/zz-sub.60kvU0`
- 最终兼容投影备份：`/var/backups/zz-sub.BzL2Ak`
- 命令：`merged_subscription_lane.ps1 -Profile zz -Apply`
- 结果：`merged_sub_nodes=7`、HTTP 200、7 节点、URI 校验 `ok`
- authority 试验备份：`/var/backups/merged-sub.erU36a`
- 最终兼容投影备份：`/var/backups/merged-sub.PS0dGV`

首次 zz 预演发现 `HY2_HOP_RANGE` 位于 facts Python 进程而非独立 render 进程，
在写入前以 `NameError` 失败；修复作用域并增加执行级测试后重新预演和应用。

## 验收

- bwg / zz 最终 `HARDENING_DRIFT=NONE`。
- 兼容格式回退后重新发布，双机合并订阅节点数均为 7，v2rayN 应恢复
  `bwg-hy2-obfs` / `zz-hy2-obfs`。
- bwg `proxy_lane_acceptance.ps1`：REALITY 主/备 SNI、回落、XHTTP REALITY、
  XHTTP TLS、CDN WS、Hysteria2 base/obfs 全部 PASS。
- zz 同范围全部 PASS，并额外包含
  `hysteria2-obfs-hop-public RESULT=PASS port=20001`。
- 两机 Xray `26.10.31`、sing-box `1.14.4`、nginx `1.31.8`；
  `net.mptcp.enabled=0`，当前未被 Xray 紧急公告中的 MPTCP 暴露面直接命中。

## 回滚

- bwg 合并订阅：从 `/var/backups/merged-sub.9Zl4nD/previous` 恢复。
- zz 兼容订阅：从 `/var/backups/zz-sub.BzL2Ak` 恢复 `previous` 与 `served`。
- zz 合并订阅：从 `/var/backups/merged-sub.PS0dGV/previous` 恢复。
- 本次没有服务重启或监听变化；无需回滚 Xray/sing-box/nginx 配置。

## 风险与边界

- `mport` 是 v2rayN 当前的兼容扩展，不属于 Hysteria2 官方 URI；Mihomo 等只按
  authority 解析的客户端会把该节点当作固定 8444 节点，而不会启用跳跃。本轮以
  用户实际使用的 v2rayN 能完整导入 7 节点为优先。
- 受控主机与公网名握手不等于中国运营商长期自然流量、抗 QoS 或抗后续封锁验收。
- Cloudflare WebSocket 仍具有 Xray 官方指出的 HTTP/1.1 ALPN 特征；当前因其是
  XHTTP 实测不可用后的独立故障域退路而保留。
- Xray #7180 的 XHTTP stream-up/nginx HTTP/1.1 截断问题在当前 `26.10.31` 栈的
  8 MiB 慢速实传中未复现，因此不启用会削弱 padding 语义的
  `scStreamUpServerSecs:-1`。
