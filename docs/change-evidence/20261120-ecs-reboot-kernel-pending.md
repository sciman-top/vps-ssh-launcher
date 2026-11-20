# HG-03 · ECS 重启收口（内核待重启清账 + 跨重启持久性实证）

日期: 2026-11-20 | 执行: HOME 会话(用户授权连续修复) | 影响面: aliyun 中转 VM(角色名,IP 不记录)
关联: `20261120-root-password-rotation-three-vps.md`(同窗口)、`20261118-aliyun-vm-bbr-tuning-bwg-vnstat.md`(调优投影)、clone 主链路(H-frp 基建)

## 背景与动机

- `/var/run/reboot-required` 在位(System restart required):前期安全更新已装内核待生效;18 个待升级包中仅 1 security,unattended-upgrades enabled 已兜底常规面,补齐动作=重启而非升级。
- 复审(11-20)全链健康:drift 门禁 HARDENING_DRIFT=NONE、frps/frpc v0.72.0=上游最新(11-04 发布)、fail2ban 双机在役——本窗口唯一待办即重启。

## 重启前安全检查(全部通过才动手)

- frps / fail2ban / docker 三服务 `systemctl is-enabled`=enabled(开机自启);
- caddy / astrbot / napcat 三容器 RestartPolicy=**unless-stopped**(重启后必然自动回归);
- 基线:frps 双 listener(控制口+映射口)在位。

## 执行与验收(ACCEPTANCE_RESULT=PASS)

- `reboot` 后 **45 秒 SSH 回归**(轮询实证,uptime 0 min)。
- **跨重启持久性实证(本次核心价值)**:`tcp_congestion_control=bbr`、`default_qdisc=fq`、`HG-03 VM tuning` 标记块完整存活,`ss -ti` 9 条活 socket 挂 `bbr:(...)` 状态机——`scripts/remote/vm-sysctl-hg03-tuning.sh` 投影契约经真实重启验证,无重放欠账。
- frps/fail2ban/docker 全部自动回归:双 listener、sshd jail、三容器 22 秒内回 healthy(内存 852/1612MB 常态)。
- **BWG frpc 自动回连实证**(journalctl):13:43:09 i/o timeout → 13:43:23 connection refused(ECS 引导中)→ 13:43:42 `login to server success` + `[relay-52443] start proxy success`——**33 秒自愈,零人工**,`loginFailExit=false` 设计兑现。
- `reboot-required` 标记清除;clone 链服务端形态与重启前逐项一致。

## 同窗口收口

- BWG 套餐配额经用户 KiwiVM 截图确认:2000GB/月(已用 282.48GB,2026-12-08 重置)=充足,配额挂账销项。

## 剩余项(均非 HOME 可执行)

- OFFICE 机 target.json 同步(材料在 HG03-work\root-rotation-20261120\;同步前 OFFICE 侧 SSH 三台断,不影响上网车道);
- 云安全中心 1 条中危告警(11-18)用户控制台标记处置;
- electerm 2 条加密书签用户 UI 内手动更新;
- 11-21 sid 轮换窗口执行清单须含"同步更新 Office clone 节点 sid"(手动节点不随订阅走)。

## 安全备注

本文件不含 IP/凭据;重启经 ssh_tool(轮换后新凭据+TOFU 落盘通道)执行;fail2ban ignoreip 三自有出口白名单未受重启影响(jail 复活即证)。
