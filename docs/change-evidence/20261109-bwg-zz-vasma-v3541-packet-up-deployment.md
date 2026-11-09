# BWG/ZZ vasma v3.5.41、TLS-XHTTP packet-up 与验收器稳定性收口 — 2026-11-09

**性质**：双机 vasma/updater/kernel wrapper 投影、订阅重发布、有限验收器修复
与受控代理车道验收。

**授权边界**：用户明确授权按建议连续自动修复、投影和受控实战验收，并在
BWG 验收后确认其客户端联网正常。本事务未轮换 UUID、REALITY 密钥、
Hysteria2 认证口令、obfs 口令或证书，也未修改 CPA、provider 凭据和账户状态。

## 1. 结论

- BWG 与 ZZ 的 vasma install 已从 v3.5.33 升级到 v3.5.41。
- 双机 updater、xray/sing-box 两个 kernel wrapper、cron 和订阅均已重新投影。
- vasma 自有订阅与 merged 订阅中的 TLS-XHTTP 节点统一发布
  `mode=packet-up`；REALITY-XHTTP 保持 `mode=auto`。
- 两机当前 `HARDENING_DRIFT=NONE`。
- BWG 受控验收为 `ACCEPTANCE_RESULT=PASS`，用户确认客户端联网正常。
- ZZ 在最终验收器版本上连续 3 轮达到 12/12 lane PASS。
- 中国运营商长期自然流量、持续封锁对抗和跨地域吞吐仍未由本回执证明。

## 2. 仓库修复

源码提交：

```text
fac7d2dc10a8c111f1ed7b16ee0c67eaae2fade3 fix(proxy): 稳定双机订阅与车道验收
```

修改文件：

- `scripts/zz_subscription_republish.ps1`
- `scripts/proxy_lane_acceptance.ps1`
- `tests/test_zz_parity_lanes.py`
- `tests/test_proxy_lane_acceptance.py`
- `README.md`
- `docs/runbooks/proxy-camouflage-lanes.md`
- `docs/runbooks/v2ray-agent-script-update.md`

### 2.1 订阅重发布

历史文件名 `zz_subscription_republish.ps1` 保持不变，以兼容既有恢复入口；
脚本现接受 `-Profile bwg|zz`，按 profile 生成：

- 节点名前缀；
- 状态文件名；
- 输出判据；
- 备份目录名。

同一 UUID、REALITY 密钥、shortId、Hysteria2 口令和已发布 URL 保持不变。
TLS-XHTTP 节点固定为 `mode=packet-up`，避免当前 Xray Go 1.27/H2 的
`stream-one` 连接关闭缺陷。相关上游跟踪为
[Xray-core #6797](https://github.com/XTLS/Xray-core/issues/6797) 和
[#7296](https://github.com/XTLS/Xray-core/pull/7296)。

### 2.2 验收器修复

原始验收器有两个可证实缺陷：

1. `probe_via_socks_detail` 在 `$(...)` 子 shell 内更新 `probe_last_exit`，
   父 shell 永远读回 0，导致失败被错误标记为 `curl-exit-0`。
2. 改造后的 detail 探针直接调用时，第二次失败命中
   `[ "$attempt" -eq 1 ] && sleep 2` 的返回码 1；在 `set -e` 下会提前退出，
   丢失车道结果。

修复后，所有 Xray 车道：

- 等待真实 SOCKS listener，而非固定 `sleep 3`；
- 保留有限两次探测和 curl 真实回码；
- 把客户端输出收敛为非敏感类别，如 `timeout`、`reality-rejected`、
  `tls-failed`、`peer-close`；
- 每条车道完成后主动回收客户端，避免探针进程累积。

持久失败仍为 FAIL，未通过放宽断言或无限重试掩盖。

## 3. BWG 投影与验收

### 3.1 远端写入

- vasma install：v3.5.33 -> v3.5.41。
- updater 与 cron：重新投影，幂等复验为 `NO_CHANGE`。
- xray/sing-box kernel wrapper：重新投影，pin 命中时短路，不重装内核。
- vasma 自有订阅：重发布为 TLS-XHTTP `packet-up`。
- merged 订阅：重发布为 TLS-XHTTP `packet-up`。

### 3.2 备份

```text
/var/backups/v2ray-agent-script-update-deploy.E5QAau
/var/backups/v2ray-agent-script-update.aLZSW1
/var/backups/v2ray-agent-maint.6mSOeA
/var/backups/v2ray-agent-maint.MJpMLl
/var/backups/vasma-sub-bwg.dCSIts
/var/backups/merged-sub.CbN1RS
```

### 3.3 验收

- 完整受控验收：`ACCEPTANCE_RESULT=PASS`。
- 最终只读漂移门禁：`HARDENING_DRIFT=NONE`。
- 用户确认 BWG 客户端联网正常。
- 这一确认不等于 BWG 所有传输、所有中国运营商和长期连接均已自然验收。

## 4. ZZ 投影与验收

### 4.1 远端写入

- vasma install：v3.5.33 -> v3.5.41。
- updater 与 cron：重新投影。
- xray wrapper 保持 `MENU_UPDATE_ALLOWED="0"`，pin 命中时只短路，不驱动
  vasma 菜单重配。
- sing-box wrapper：重新投影。
- vasma 自有订阅：5 节点，TLS-XHTTP `packet-up`，HTTP 200。
- merged 订阅：7 节点，TLS-XHTTP `packet-up`，HTTP 200。

### 4.2 备份

```text
/var/backups/v2ray-agent-script-update-deploy.Dmq61x
/var/backups/v2ray-agent-script-update.zYPqgx
/var/backups/v2ray-agent-maint.dDqMHU
/var/backups/v2ray-agent-maint.wwlKkT
/var/backups/vasma-sub-zz.jIrrmq
/var/backups/merged-sub.h4ebL5
```

### 4.3 初始波动与收敛

部署后的原始验收并非一次稳定通过，失败集合在四次运行中发生变化：

| 运行 | 失败车道 |
| --- | --- |
| 1 | `reality-xray-alt`、`xhttp-reality`、`xhttp-tls`、`cdn-ws` |
| 2 | `reality-xray`、`reality-xray-alt` |
| 3 | 无，12/12 PASS |
| 4 | `reality-xray`、`reality-xray-public`、`xhttp-reality`、`cdn-ws` |

同轮中 Hysteria2 和 REALITY 回落页持续通过，失败又集中在验收器启动的
临时 Xray 客户端族。该分布不支持“某个固定服务端 lane 持续坏死”的解释，
但足以证明原验收器不能稳定区分真实断链、客户端启动和 curl 状态丢失。

修复后，最终脚本连续 3 轮均为：

```text
reality-xray PASS
reality-xray-alt PASS
reality-fallback PASS
reality-xray-public PASS
xhttp-reality PASS
xhttp-tls PASS
cdn-ws PASS
hysteria2-base PASS
hysteria2-base-public PASS
hysteria2-obfs PASS
hysteria2-obfs-public PASS
hysteria2-obfs-hop-public PASS
ACCEPTANCE_RESULT=PASS
```

最终只读漂移门禁：

```text
HARDENING_DRIFT=NONE
```

同次漂移结果还确认：

- REALITY Vision/alt-SNI/回落目标在位；
- XHTTP nginx 前置 `15994 -> 10094` 在位；
- CDN 边缘 200，源站 WS 和证书匹配；
- merged 订阅 7 节点，vasma 订阅 HTTP 200；
- Hysteria2 base/obfs/port hopping 合约在位；
- 防火墙基线在 live 状态，无旧防火墙残留；
- MPTCP 关闭，内核 `6.8.0-149-generic` 已含 CVE 修复；
- updater/install 合约在位。

## 5. 当前版本与 pin

双机复核一致：

```text
Xray 26.10.31
sing-box 1.14.4
/etc/v2ray-agent/install.sh sha256=
1c8bc09ee798ab8f825269e58fa3478d5c124f6aa0a5108cb5411bf0f9867a9b
xray active
sing-box active
nginx active
```

当前 Xray v26.11.9 已于 2026-11-09 发布，包含 Chrome 155+ 指纹默认更新；
本事务没有仅因“有新版本”而升级。v26.10.31 已包含 MPTCP 应用层缓解，
且在验收器修复后双机代理路径稳定。同日新版本若后续纳入，应按单机备份、
灰度、verification 和回滚流程另行处理。

## 6. 仓库门禁

最终全量门禁：

```text
1204 passed, 18 skipped, 595 subtests passed in 279.96s
bandit: PASS
ruff check: PASS
ruff format --check: PASS
mypy: PASS
```

跳过项为：

- 真实 SSH integration 默认 opt-in；
- 当前 Windows Bash 不可用的 17 个 `flock` 场景。

本次另有真实 SSH 只读检查和两台主机上的受控验收，未把 skipped 项外推为
未执行的真实主机证明。

## 7. 安全与未证明边界

- 未修改或输出真实 `target.json`、密码、私钥、订阅 token 或认证材料。
- 未轮换代理凭据或证书。
- 未加入第二条治理系统，也未修改 vasma 上游源码。
- 受控失败重测证明的是当前主机、当前网络和当前客户端组合。
- CDN 的 `2087` 入口不覆盖 Cloudflare 中国网络仅支持 80/443 的边界。
- XHTTP 不通过 Cloudflare；CDN 仍保留 VLESS+WS 独立故障域。
- Hysteria2 `gecko` 未启用；在真实客户端矩阵通过前继续使用兼容面更广的
  `salamander`。

## 8. 回滚

本地源代码回滚：

```text
git revert fac7d2dc10a8c111f1ed7b16ee0c67eaae2fade3
```

远端回滚必须先停止新的 Apply，并按 `profile` 使用上面的备份目录。不得把
整个备份目录无差别覆盖到运行树：

- updater/install：恢复对应备份文件后，复验版本、cron、wrapper pin 和服务；
- kernel wrapper：恢复同主机同内核对应的单个 wrapper，保留另一核；
- vasma 自有订阅与 merged 订阅：分别恢复 `previous`/旧 served 文件，
  再验证原 URL HTTP 200、节点数和客户端导入；
- 若 vasma install 本身需回退，必须同时恢复 updater 与两个 wrapper，
  避免脚本版本和 wrapper anchor 混用。

任何远端回滚后都必须重新执行：

```text
proxy_hardening_drift.ps1
proxy_lane_acceptance.ps1
```

并以 `HARDENING_DRIFT=NONE` 和 `ACCEPTANCE_RESULT=PASS` 作为恢复完成判据。
