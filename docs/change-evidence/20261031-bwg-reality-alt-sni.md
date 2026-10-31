# BWG REALITY 第二主机名(2026-10-31)

## 依据

六条车道的评审结论是不删:每条对应不同的传输特征或外层,删任一条都会减少故障
切换维度。评审同时指出真正的单点是 **SNI**:5 条车道共用 `fq.sciman.top`,
精确主机名封锁会一次带走其中 5 条,只剩 CDN 车道。

先验证了另一条思路并**否决**:把 REALITY dest 换成外部知名站点(bwg 与外部目标
分别跑通一次端到端),Xray 26.10.29 在启动时明确告警
`REALITY: Choosing "www.microsoft.com" as the target will increase the likelihood
of your server's IP being blocked by the GFW`。这条会抬高 IP 被封概率,与目标相反,
未采用。

## 改动

- DNS:`static.sciman.top` A → `144.34.229.116`(仅 DNS,未代理;沿用 acme.sh 里
  已有的 CF token,未新建凭据)。
- nginx:`/etc/nginx/conf.d/vps-launcher-alt-sni.conf`,在 REALITY 回落端口 `33387`
  用同一张通配符证书服务该名字,其余路径回落伪装站。
- xray:`07_VLESS_vision_reality_inbounds.json` 与 `12_VLESS_XHTTP_inbounds.json`
  的 `serverNames` **追加**该名字;主 SNI 仍在第一位,已发布节点零改动。
- 合并订阅:`merged_subscription_lane.ps1` 在检测到第二个 `serverNames` 时额外
  发布 `bwg-reality-vision-alt` 节点,客户端可在主名字被封时直接切换。
- 漂移门禁:新增 `reality-alt-sni`,以 `/etc/v2ray-agent/.vps-launcher-alt-sni`
  标记区分"未应用"与"已回退"。

## 证据

- 应用前 dry-run:`reality_inbounds=2`、`alt_dns=144.34.229.116` 与本机公网 IP 一致。
- 应用:`alt_sni_handshake=ok`、`alt_sni_lane=ok egress=144.34.229.116`(带新 SNI 的
  真实 REALITY 客户端取到出口),`ALT_SNI_APPLIED`。
- 漂移:`reality_alt_sni=ok missing=2/0`、`alt_vhost=ok`、`HARDENING_DRIFT=NONE`。
- 回归:`ACCEPTANCE_RESULT=PASS`,reality / xhttp-reality / xhttp-tls / hy2-base /
  hy2-obfs 的 loopback 与公网名路径全部 PASS。
- 订阅:`merged_sub_nodes=7`、`merged_sub_http=200 served=7`,含 `static.sciman.top:443`。

## 边界

只覆盖"精确主机名被 SNI 封锁"。IP 封锁仍由 CDN 车道承接;整段 `sciman.top` 域名
封锁需要另有一个独立注册域,本次未做(需要域名与 DNS 归属方决定)。

## 回滚

```powershell
# 恢复配置(脚本自带备份目录 /etc/v2ray-agent/xray/conf/backup-alt-sni-<stamp>)
sudo cp -a <backup>/07_*.json <backup>/12_*.json /etc/v2ray-agent/xray/conf/
sudo rm -f /etc/nginx/conf.d/vps-launcher-alt-sni.conf /etc/v2ray-agent/.vps-launcher-alt-sni
sudo nginx -t && sudo systemctl reload nginx && sudo systemctl restart xray
```

DNS 记录随后在 Cloudflare 删除 `static.sciman.top`,并重跑
`merged_subscription_lane.ps1 -Apply` 撤回多余节点。
