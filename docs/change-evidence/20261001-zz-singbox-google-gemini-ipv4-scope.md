# zz sing-box Google/Gemini IPv4 强制收窄为作用域路由收据（2026-10-01）

## 授权与背景

- 用户当次明确指令：bwg 与 zz 都应**只对 Google/Gemini 特定路由**强制 IPv4（可参考官方文档与社区最佳实践），按推荐连续执行直至完成。
- 现状检测（当次只读探针）：
  - bwg（xray）已精确达标，**未做任何改动**：`conf/09_routing.json` 显式 10 域名（`gemini.google.com`、`google.com`、`googleapis.com`、`googleapis.cn`、`gstatic.com`、`googleusercontent.com`、`googlevideo.com`、`ggpht.com`、`youtube.com`、`ytimg.com`）→ `google_ipv4_out`（freedom `ForceIPv4`）；默认出站 `z_direct_outbound`=`UseIP`（非强制）；Gemini Web/API 域名被 `google.com`/`googleapis.com` 后缀覆盖。
  - zz（sing-box 1.14.2）为全局 catch-all：`route.rules=[sniff, {action:resolve, strategy:ipv4_only}]`（9/25 周更 wrapper 注入），全量域名被强制 A 记录解析；系统本身无全局 IPv6（事实 v4-only 主机）。
- 语法佐证：sing-box 官方 route rule 文档确认 `domain_suffix` 条件可与 `action: resolve` + `strategy` 在同一规则内组合（AND 语义）。

## 变更（zz，单次远端事务）

域名集与 bwg 逐字节一致（`domain:` ≡ sing-box `domain_suffix`），保持双机同语义：

1. `conf/config.json`：移除无域名条件的 resolve+ipv4_only catch-all，注入作用域规则（jq 改写，候选文件双断言后原子替换）。
2. 新增持久片段 `conf/config/99_vps_ssh_launcher_google_ipv4_only.json`（644 root）——vasma 周更重建 config 时经片段 merge 保持规则（现 config 中 sniff 规则即来自片段 merge，证明重建走片段）。
3. 周更 wrapper `/etc/v2ray-agent/auto_update_singbox.sh` 第 54 行自愈载荷同步作用域化（perl `\Q\E` 单锚点补丁，锚点唯一性预检 count=1，bash -n 通过）；其前置 any() 检查天然匹配作用域规则，**不会再回注全局 catch-all**。
4. 全程持有 `/run/v2ray-agent-maint.lock`（与周更 wrapper、原生月度脚本同一互斥锁，锁设计未动）。

## 验收（apply 后独立复验）

- `sing-box check -c` = **CHECK_OK**；服务 active；监听基线恢复（UDP:443 + TCP:22752，SOCKETS=2）。
- 活动规则 `[{"action":"sniff","timeout":"1s"},{"action":"resolve","strategy":"ipv4_only","domain_suffix":[10 域名]}]`；catch-all 计数=0、作用域规则计数=1（断言通过）。
- 周更自愈前置检查对作用域规则返回 `true`（`ENSURE_WOULD_SKIP`）；wrapper 内 grep 仅命中作用域载荷。
- 主机出口 `curl -4` = 200。
- 判据行：**SCOPED_GOOGLE_IPV4_APPLIED**。

## 回滚

备份 `/var/backups/singbox-google-scope-20261001T012029`（`config.json`、`auto_update_singbox.sh`、`fragment.missing`）。恢复三件至原位后 `systemctl restart sing-box` 即回到 9/25 以来形态（apply 脚本内置同款自动回滚，本次未触发）。

## 观察项与风险边界

- 非 Google 双栈域名改走 Go dialer happy-eyeballs（首连至多 ~300ms 家族惩罚，失败家族快速让位）——与 bwg 现状完全一致（bwg 无 catch-all resolve）。
- 若 vasma fork 周更重建 config 时忽略片段目录（低概率），周五 cron 自愈会写回**作用域版**规则（收敛态不变，不再是全局）。
- 今晚北京 22:00 zz 原生月度维护照常，22:15 受控自动重启；sing-box 为 systemd 自启，重启后配置即本收据形态。
- bwg 侧今日零改动；其 Google/Gemini 路由由 `google_ipv4_routing.ps1` 管辖（check 模式 marker 集含 gemini，2026-09-15 修 backtick 后可用）。
