# 2026-11-16 BWG xray own-domain 路由试修与回滚（诊断性触碰）

## 背景与目标

落盘机侧 Cockpit 对 CPA 上游拉取模型持续 `PROVIDER_MODELS_NETWORK_FAILED`。排障
定位到：本机隧道把自家 REALITY SNI 域名（`fq.sciman.top`）作为**代理目标**时全部
超时，而 raw IP 与异域名（`static.sciman.top`，同机同端口）均正常。当时假设根因是
BWG xray conf 里 own-domain 兜底规则的 `inboundTag` 失配（规则引用的 tag
`dokodemo-in` 在合并配置中不存在），遂在授权下做一次断言保护的最小修复试验。

本切片**只涉及 BWG xray conf 的诊断性触碰并当次回滚**；没有轮换 REALITY
密钥、UUID、shortId、证书，没有触碰订阅、guard 表、nginx、CPA admission 与
sing-box。

## 执行与证据

- 备份：`/etc/v2ray-agent/xray/conf/backup-own-domain-route-20261116T0847Z/`
  （含 `07_VLESS_vision_reality_inbounds.json` 与 `00_log.json` 触碰前副本）。
- 试验一：`07_VLESS_vision_reality_inbounds.json` routing rule 1 删除
  `inboundTag` 字段（使 own-domain → `z_direct_outbound` 全局匹配）；
  python 校验全部 11 个 conf JSON 通过；`systemctl restart xray`。
- 试验二（诊断）：`00_log.json` 临时开启 access log 到 `/tmp`，重启后单发
  三对照探测，读取 access log 后关闭并复原。
- **关键发现（access log 铁证）**：`fq.sciman.top` 目标请求在 BWG **零入站
  记录**（dokodemo 转发与 VLESS 入站均无），同窗口 `static.sciman.top:8443`
  与 raw IP 探测正常进入并返回 401。即丢弃发生在**本机客户端**（v2rayN core
  对"目标域名 == 出站 REALITY serverName"的防自环处理），而非 BWG 侧；
  服务端规则修复对该流量无效。
- 回滚：两处 conf 均从备份目录字节级复原；复核 `RULE0` 原样、`access: none`、
  xray `active`、443/45987 监听恢复。
- 复验：`proxy_hardening_drift.ps1 -Profile bwg` → `HARDENING_DRIFT=NONE`。

## 运营结论（影响本机消费者，不涉及服务端）

- BWG 8443 网关（`/59b202b53a2f0066/v1`，证书 SAN `*.sciman.top`）经隧道实测
  可达：本机 Cockpit/Codex 类消费者的 CPA 入口改用
  `https://static.sciman.top:8443/59b202b53a2f0066/v1`；`fq.sciman.top` 作为
  隧道目标在本机所有车道不可用（客户端自环保护）。
- 直连车道全景（2026-11-16 实测）：BWG TCP 8 个放行端口、UDP hy2 两端口、
  ZZ 6 端口、CF 边缘 443/2087 全部不可达——IP 层封锁，唯 ECS frp 中继车道
  健康。远端与服务端无需任何变更。

## 回滚与边界

- 触碰的 conf 均已字节级复原；备份目录保留供追溯，不删除。
- `systemctl restart xray` 造成在用隧道秒级闪断两次（试验与回滚各一）；
  v2rayN 自动重连，无持续影响。
- 本记录证明的是一次受控诊断触碰与复原；自然流量 `live_accepted` 仍需独立
  低频真实业务窗口证据。
