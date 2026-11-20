# 2026-11-20 ECS 中继 SSH 管理恢复与 fail2ban 加固（受控实战验收）

关联 runbook：`docs/runbooks/frp-aliyun-home-relay.md`（拓扑：受控客户端 → ECS:52443 → frps ← BWG frpc → BWG:127.0.0.1:443）。
本页记录一次**真实远端写入**（ECS 装 fail2ban）与本机代理分流修复的脱敏收据。敏感值（密码、REALITY 参数细节）不入库。

## 背景与根因

- 2026-11-20 下午发现：单位网出口对 ECS:22 的 SSH 全部失败（TCP 建连后 banner 阶段双向 RST），
  sshd 日志显示同签名失败**最早 2026-11-18 17:07 即已存在**；家宽出口同期 root 登录成功。
- 服务器侧四件套（sshd 状态 / 22 监听 / fail2ban / hosts.deny）+ nftables 全部正常，
  经控制台云助手两批只读命令证实；`bwg → ECS:22` 的 banner 探测亦正常。
- **根因 = 单位网 DPI 对出站 SSH:22 双向注入 RST**，非服务器故障。ECS 为国内地址，
  本机代理链 `geoip:cn → direct` 使 socks5 链也回落到单位网出口，故两条本地路径同死。

## 变更

1. **本机 ag-split 分流器**（`D:\TOOL\v2rayN\ag-split\`，非仓库管理面）：
   - `config.json` routing 头部新增精确规则 `{ip:[ECS公网地址], port:"22", outboundTag:clean}`，
     SSH 经 clean 车道（阿里云出口）回连 ECS:22；备份 `config.json.bak-20261120-p2`。
   - 同日早前：clean 出站 REALITY shortId 同步为服务端现值（备份 `config.json.bak-20261120`），
     `ag-split-core.exe` 由 Xray 26.7.28 换装 26.11.9（与 v2rayN bin 同哈希，
     备份 `ag-split-core.exe.bak-26.7.28-20261120`）——旧内核无法与 11-16 后服务端参数协商
     是 clean 车道 000 的根因（同参数 26.11.9→204 / 26.7.28→000 隔离证实）。
2. **ECS fail2ban**（真实远端写入，经 ssh_tool + 新恢复的 SSH 通道）：
   - `apt install fail2ban`（Ubuntu 24.04，systemd journal 后端）。
   - 新增 `/etc/fail2ban/jail.local`：`[sshd] enabled`，`bantime=1h findtime=10m maxretry=5`，
     `ignoreip = 127.0.0.1/8 + 单位网出口 + bwg出口 + 家宽出口`（三个自有出口防误伤）。
   - 未修改 sshd 任何配置；root 密码登录保持现状（家宽登录习惯不变）。

## 验收证据

- **SSH 管理通道 live_accepted**：`ssh_tool --profile aliyun` 经 10810→clean→ECS:22 执行命令回显
  `SSH_VIA_SPLIT_OK` + hostname；随后三批只读/写入命令全程稳定。
- **clean 车道回归**：`google/generate_204` 经 10810 = 204（socks5 与 http 双协议）；默认出口仍为 bwg。
- **fail2ban 受控实战验收（ACCEPTANCE_RESULT=PASS）**：`fail2ban-client set sshd banip 192.0.2.1` →
  `status` 显示 Banned IP list 含 192.0.2.1 且 `nft list ruleset` 出现对应 element（fail2ban 走纯
  nftables 动作，iptables 视图为空属预期）→ `unbanip` 后 `Currently banned: 0`、nft 清洁。
- **shortId 实战确认**：SSH 全程经 REALITY 隧道以现行 shortId 握手成功（a3fd 开头现值有效；
  旧值 6ba8 开头已弃用）。
- **告警链路（本机，非远端）**：watchdog 探针已启用（`run-ensure-split-hidden.vbs` 去
  `-NoEgressProbe`）；新增用户会话任务 `AgSplitLaneAlert`（3 分钟，Interactive）+
  `lane-alert-watch.ps1`，合成失败块 → 气泡 + 游标落盘、真实全绿日志 → 零误报（受控模拟）。

## 回滚

- ag-split：`config.json.bak-20261120-p2`（或 -20261120）复制回 `config.json`，
  `Stop-ScheduledTask AgSplitEgress; Start-ScheduledTask AgSplitEgress`。
- fail2ban：`systemctl disable --now fail2ban && apt remove -y fail2ban`（jail.local 随包或手删）。
- 告警中继：`Unregister-ScheduledTask AgSplitLaneAlert` + 删 `lane-alert-watch.ps1` /
  `run-lane-alert-hidden.vbs`。

## 遗留与边界

- ECS 实例公网带宽 3Mbps（按固定带宽）为两条车道共同吞吐上限，实测 ~3.2-3.3Mbps；提速需升配（用户决策）。
- 云安全中心 1 条中危告警「可疑 ECS 在非常用地登录 · 异常登录」（11-18 17:32，与换出口登录时间线吻合，
  benign 可能性大）；是否标记已处理由用户判定。
- auth.log 累计 1202 次 `Failed password`（公网爆破噪音）；fail2ban 上线后由其压制。
- 52443 安全组仅放行单位网出口 /32：切换家庭网络前须先在控制台放行新出口，否则整条链路中断。
- `ssh_tool --profile aliyun` 目前每次需 `--allow-unknown-host-key`（TOFU 未落盘，三次验证）。
