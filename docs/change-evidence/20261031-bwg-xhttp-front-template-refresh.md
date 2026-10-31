# bwg XHTTP 前置模板刷新：逐字节幂等 + 去掉逐请求告警（2026-10-31）

本轮属 2026-10-31 代理车道深度审查的代码收口。远端只对 `bwg` 做过一次
`xhttp_nginx_front.ps1 -Apply`（备份优先、失败回滚），`zz` 为 `not_applicable`。
不含凭据、订阅地址或完整握手回显。

## 1. 发现：模板修正会被“已应用”短路吞掉

- 旧版“已应用”判据只看入站形态（wrapper 指向 `15994`、内层 `security: none`）和伪装页
  响应，不比对 nginx 生成物内容。任何模板级修正（头、超时、TLS 指令）在已前置的主机上
  都不会落盘，`-Apply` 也只会打印 `xhttp_front=applied` 后退出。
- 证据：改模板后先跑只读预演，远端仍报 `xhttp_front_state=applied` 且伪装页正常；若不
  换判据，这次修复会静默无效。

## 2. 发现：每个 XHTTP 请求写一条 Xray warning

- 前置向 `127.0.0.1:45988` 转发 `X-Real-IP` / `X-Forwarded-For`，而内层 XHTTP 入站
  `security: none` 且未配 `sockopt.trustedXForwardedFor`，Xray 忽略这两个头并逐请求告警：
  `received "X-Forwarded-For" ... "sockopt.trustedXForwardedFor" is not configured`。
- 证据：`/etc/v2ray-agent/xray/error.log` 中该类告警 56 条，全部来自受控验收探针
  （每轮 6 条），`error.log` 共 5331 行。真实客户端走 `10094` 时同样每请求一条。

## 3. 改动（repo）

- `scripts/xhttp_nginx_front.ps1`：nginx 生成物改为单一 `render_conf()` 模板，`conf_current()`
  用 `cmp` 与远端文件逐字节比对；不一致时报 `xhttp_front_template=stale` 并按 drift 走
  Apply，`lane_ready` 也复用同一判据。
- 同文件：模板不再转发 `X-Real-IP` / `X-Forwarded-For`，保留 `Host`、`Connection ""` 与
  `proxy_buffering off` / `proxy_request_buffering off` / 3600s 超时等 XHTTP 必需项。
- 同文件：仅当 xray 入站 JSON 真变化（或 xray 未运行）才 restart xray，纯模板修正只
  reload nginx，输出 `xray_restart=skipped(unchanged inbound)`。
- `tests/test_xhttp_nginx_front.py`：补模板单一来源、逐字节幂等判据、不转发客户端 IP 头、
  模板变更跳过 xray 重启四条契约；另将 5 个在 HEAD 上已不符合当前 ruff 格式的测试文件做
  机械格式化（纯格式，无语义变化）。

## 4. 远端执行与验收

| 步骤 | 命令 | 结果 |
| --- | --- | --- |
| 预演（改前） | `scripts/xhttp_nginx_front.ps1 -Profile bwg` | `xhttp_front_template=stale`、`xhttp_front=drift` |
| 应用 | 同命令 `-Apply` | `XHTTP_FRONT_APPLIED`，备份 `/var/backups/xhttp-front.2LGizL`，`xray_restart=skipped(unchanged inbound)` |
| 预演（改后） | 同命令不带 `-Apply` | `xhttp_front=applied`（幂等） |
| 漂移门禁 | `scripts/proxy_hardening_drift.ps1 -Profile bwg` | `HARDENING_DRIFT=NONE` |
| 车道验收 | `scripts/proxy_lane_acceptance.ps1 -Profile bwg -PublicName fq.sciman.top -RealityPublicPort 443` | `ACCEPTANCE_RESULT=PASS`（reality-xray / -public / fallback / xhttp-reality / xhttp-tls / hysteria2 base+obfs 全 PASS） |
| 告警复验 | 验收后再读 `error.log` | 该类告警仍为 56 条、总行数仍 5331，未新增 |
| 客户端栈 | 从中国网络主机直连 | TCP 443/21470/10094/8443/33387 全部可连；443/33387/8443/10094 TLS1.3 + ALPN h2，证书 `*.sciman.top` 有效至 2027-01-11 |

## 5. 边界

- 只改 bwg 的 `vps-launcher-xhttp-front.conf`；xray 入站 JSON、REALITY/UUID/证书、Hysteria2
  与订阅均未触碰。回滚：`cp -a /var/backups/xhttp-front.2LGizL/previous.conf
  /etc/nginx/conf.d/vps-launcher-xhttp-front.conf` 后 `nginx -s reload`。
- 同轮工作树里存在另一并发批次的 CPA 改动（`scripts/remote/cpa_provider_routes.json` 等），
  本批未读取、未修改、未提交。
- 从中国网络的直连探测只证明 TCP/TLS 层未被拦，不等于运营商侧的长期自然验收；UDP/QUIC
  车道本轮未做中国侧客户端实测。
