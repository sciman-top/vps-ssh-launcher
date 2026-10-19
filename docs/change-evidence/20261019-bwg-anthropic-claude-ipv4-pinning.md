# 2026-10-19 bwg Xray 中继面 anthropic/claude 域钉定 IPv4 收据

## 授权与背景

- 用户当次明确指令：按前期 IPv6 风控评估的推荐连续自动修复直至完成（评估结论=官方层面对 IPv6 无歧视；风控层面风险变量是"同账号双出口身份/出口地理一致性/隧道段不可控"，建议钉 IPv4）。
- 变更目标：Xray 中继面（本机专用浏览器→claude.ai 等 Web 路径）此前对 anthropic/claude 域走 freedom `UseIP` + Go happy-eyeballs（v6 优先，10/1 收据明载该行为），与"IP 钉死"纪律冲突且形成同账号 v4(API)/v6(Web) 双身份。将其并入既有 ForceIPv4 出站。
- 探查源：Anthropic 官方 IP 文档（inbound v4 `160.79.104.0/23` + v6 `2607:6bc0::/48`，双栈一等公民）、claude-code#20240（AAAA 存在性）、linux.do 封号模型社区共识（数据中心 IP、IP 频换两大维度）、经代理 DoH DNS 快照（api.anthropic.com/claude.ai/console.anthropic.com = `160.79.104.10` + `2607:6bc0::10`；googleapis 族全双栈）。

## 探针事实（2026-10-19 只读，决定改动集）

- CPA 容器面（claude-oauth/antigravity/codex/workbuddy 全部 lane）**事实 v4-only，零改动**：docker bridge `EnableIPv6=false`，容器内 `/proc/net/if_inet6` 仅 ::1。此前"容器大概率 v4-only"的推断已实证。
- bwg 宿主双栈：v6 = IT7 自有 SIT 隧道（接口 `ipv6net`，/64 前缀 `2607:8700:5500:50c7::`，与 v4 出口 `144.34.229.*` 同属 IT7 LA，地理一致；隧道非原生、MTU 1480）。
- 机制定位：`google_ipv4_routing.ps1` 是 Xray 配置层事务，只作用于中继面；真正持有域名清单的是远端生成器 `/etc/v2ray-agent/apply-google-ipv4-routing-config.sh`（每个 xray 启动经 systemd dropin `ExecStartPre` 重放，幂等 cmp+自备份），`reapply-google-ipv4-routing.sh`（225B）只是委派器。
- 生成器旧 sha `f84d8321ce09…5bc6a2`；reapply sha `8e0fb95ebaa7…f74ef`。

## 变更（bwg，两个事务，全程持 `/run/vps-ssh-launcher-maintenance.lock`）

1. 事务 A（生成器原位升级）：sha 前置断言（旧值不匹配即 exit 40）→ 备份至 `/var/backups/google-ipv4-generator-20261019T141037.042219888Z` → staged 副本上 sed 单点插入（`domain:ytimg.com` 行后追加 `"domain:anthropic.com"`、`"domain:claude.ai"` 两行）→ `bash -n` → staged diff 逐行核验（恰 3 行）→ 原子 `mv` → 写后 sha 复验（失败自动还原）。新生成器 sha `92cbbbb6a524…d0f25778`。
2. 事务 B（既有 wrapper 事务）：`google_ipv4_routing.ps1 -Profile bwg -Apply -RemoteApplySha256 8e0fb95e…`（reapply 未动，pin 不变）——wrapper 自持锁、备份四工件（dropin/09_routing/98_outbound/reapply，`/var/backups/google-ipv4-routing-20261019T141053.840804465Z`）、运行 reapply（生成器 cmp 检出变化→自备份→安装新 09_routing.json）→ `xray run -test` → 重启 xray → 后验（逐文件 marker、服务 active、config test），失败即 restore_known_state。`APPLY_VERIFIED`，备份修剪 keep_8。
3. zz 零改动：宿主无全局 IPv6（事实 v4-only），claude 路径天然 v4，无双机对称性损失；双机域清单自此 bwg=12 域 / zz=10 域，差异见"观察项"。
4. 仓侧零代码改动：wrapper marker 集（`gemini|google_ipv4_out|ForceIPv4|googleapis|gstatic`）仍全覆盖，无测试断言 10 域清单；生成器为 remote-era 工件不入投影清单，sha 与 sed 命令以本收据为准（R8 可追溯）。

## 验收（apply 后独立复验）

- `09_routing.json` 实读：恰 12 域（10 Google + `domain:anthropic.com` + `domain:claude.ai`）→ `google_ipv4_out`（freedom ForceIPv4）；`GOOGLE_LIST_INTACT`、`ANTHROPIC_CLAUDE_PRESENT`。
- 服务 active、`xray run -test` = Configuration OK、版本 26.3.27 未变。
- 本地经中继功能实测：`api.anthropic.com/v1/models` => 401（存活、鉴权门正常）、`claude.ai/robots.txt` => 200、`gemini.google.com/robots.txt` => 200（Google 钉定零回归）。
- 出口复验：v4=`144.34.229.*`、v6=`2607:8700:5500:50c7::2`（钉定后 anthropic/claude 流量不再走 v6 路径，机制同 7 月以来 Google 域 ForceIPv4 同源）。
- 判据行：**ANTHROPIC_IPV4_PINNING_APPLIED**。

## 回滚

- 配置回滚：`/var/backups/google-ipv4-routing-20261019T141053.840804465Z/` 四工件拷回 + 重启 xray；或反向 sed 删除两行后重跑 wrapper `-Apply`。
- 生成器回滚：`/var/backups/google-ipv4-generator-20261019T141037.042219888Z/` 拷回（sha 复验 `f84d8321…`）。
- Git 回滚仅涉本收据文档，不能代替远端恢复。

## 观察项与风险边界

- 双机域清单自此不对称（bwg 12 域 / zz 10 域）：zz 宿主 v4-only 使对称钉定无意义；未来若 zz 获得原生 v6，需把 anthropic 两域同步进 zz 片段（契约 wrapper 的"精确 10 域断言"届时同步扩展）。
- 钉 v4 的代价：anthropic/claude 中继流量失去 happy-eyeballs 对 v6 故障的自动逃逸；v4 出口段（IT7 LA）若被上游拉黑，须评估换出口或临时恢复 v6（接受双身份风险）。SIT 隧道本就不适合作稳定备胎，该代价可接受。
- 容器面持续成立的前提是 docker bridge 保持 `EnableIPv6=false`；若未来为其他需求启用容器 IPv6，claude-oauth/antigravity lane 将出现双栈直连，届时需在 compose/daemon 层钉 v4 或把目标域走 proxy outbound。
- CPA 容器内无 curl，容器族验证手段为 `/proc/net/if_inet6` + `docker network inspect`；`statsig.anthropic.com` 无 AAAA（claude-code#20240），其遥测本就走 v4。
- 本地裸 DNS 不可信（AAAA 全剥、多域 A 重写同址），DNS 取证一律经代理 DoH（本次 dns.google/resolve）。
