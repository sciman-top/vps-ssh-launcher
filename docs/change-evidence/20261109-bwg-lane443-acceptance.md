# 443 车道（Desktop 实际路径）验收补口（2026-11-09）

**性质**：只读活体验收 · 脱敏留档
**触发**：用户追问"是否还有未做的验收"。核对后发现我此前**只验了 8443 车道**，
而 Desktop 实际走的是 443 车道。这是真实缺口，不是重复工作。

## 1. 两条车道为什么不能互相代表

| 项 | 8443 车道 | 443 车道 |
|---|---|---|
| nginx 配置 | `cpa-gateway.conf` | `subscribe.conf`（v2ray-agent 生成） |
| 监听 | `0.0.0.0:8443` | `0.0.0.0:33387`（xray REALITY target 回指） |
| capability 路径 | `59b202b53a2f0066` | `fc3003d5715fbdf6` |
| 前置链 | 公网直连 nginx | `fq.sciman.top:443` → xray dokodemo-door → VLESS REALITY → nginx |
| 上游 | `127.0.0.1:8318` | `127.0.0.1:8318` |

两者共用 admission/CPA，但**入口路径与配置来源完全不同**（443 车道由 v2ray-agent
的 install.sh 生成，重装会连带删掉 CPA location）。所以"8443 通"不能推出
"Desktop 通"。本次实测 `PREFIX_SHARED_BETWEEN_LANES=no`，进一步确认两条车道
各持独立能力路径。

## 2. 一个探针假阴性（已修，值得记住）

首版探针直连 `https://127.0.0.1:33387/`，四车道全部 `transport:URLError`。
原因不是网关故障：

- `subscribe.conf` 的 server 块是 **name-based vhost**（`server_name fq.sciman.top`），
  且**没有 default_server**；
- 裸 IP 握手被拒：`OpenSSL error:0A000458: tlsv1 unrecognized name`；
- 用 `curl --resolve fq.sciman.top:33387:127.0.0.1` 直接返回 **200**，证明服务正常。

修法：探针把 `fq.sciman.top` 解析钉到 loopback 并**保留 SNI**，而不是关掉校验。
这与本轮另外两处假阴性（单点采样、SSE 拼接）同属一类问题——**先怀疑自己的断言，
再怀疑系统**。

## 3. 活体验收结果

`python3` 单发、每车道一次、不重试、不打印正文：

```
PREFIX_SHARED_BETWEEN_LANES=no (expected: the lanes are independent)
lane443_antigravity=200 t=2.50s terminal=1 text_len=4 pong=True
lane443_workbuddy  =200 t=1.71s terminal=1 text_len=4 pong=True
lane443_codex      =200 t=1.19s terminal=1 text_len=4 pong=True
lane443_zhipu      =200 t=1.59s terminal=1 text_len=4 pong=True
LANE443_ACCEPTANCE=PASS
```

判据：HTTP 200 + SSE 终态事件存在 + 文本可拼出 PONG。`text_len=4` 是 `PONG`
被 SSE 切块后原样拼接的长度。

## 4. 落点

- 脚本：`outputs/accept-lane443-20261109.ps1`（含 SNI 钉定与原因注释）。
- 与既有验收的分工：`accept-fork-public-20261109.ps1` 管 8443 车道；
  本脚本管 443 车道；两者都只证明受控单发，不等于长期稳定性。

## 5. 边界

- 未修改任何 nginx 配置、未重启服务、未触碰账号。
- 443 车道的已知残余（REALITY 目标使 nginx 看到 `$remote_addr` 为 VPS 自身，
  per-IP 限流退化为全局桶）不在本次范围。
- `subscribe.conf` 由 v2ray-agent 生成，重装会删 CPA location —— doctor 的
  `random-route-count` 会 fail-closed，需按 runbook 重建；本次仅验证未改动。
