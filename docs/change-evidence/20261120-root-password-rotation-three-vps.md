# 2026-11-20 三台 VPS root 密码统一轮换（HOME 维护窗口，受控实战验收）

关联：`HG03-work/root-rotation-20261120/`（Git 外凭据与 OFFICE 同步包）；
`HG03-work/OFFICE-REPLY-20261118.md` §6 安全债（三台 root 密码曾在 OFFICE 会话 transcript 出现，
掩码过滤缺陷所致）。本页记录一次**真实远端写入**的脱敏收据；新旧密码不入库。

## 背景与决策

- OFFICE 回执明确建议"三台 root 密码统一轮换，时点=下次 HOME 维护窗口"；用户授权本轮连续执行。
- 轮换前预检（只读）：三台 profile 直连正常（bwg / zz / aliyun，均 root 密码认证）；
  `/etc/cron.d`、`/etc/crontab`、`/var/spool/cron` 全域 grep `sshpass` 零命中——无自动化依赖 SSH 密码。
  bwg→aliyun 离机备份为 key 认证（hg03-ali-backup-pull）；clone 车道 frp 为 token；CPA 链与
  QQ bot 栈均不依赖 SSH root 密码——轮换影响面=两台电脑的 `target.json`，无在役任务风险。

## 变更

1. 三台 root 密码统一轮换（顺序 bwg → zz → aliyun，前一台完整验证后才进下一台）：
   - 新密码 `secrets` 生成 32 字符 URL-safe；`chpasswd` 自 SSH stdin 喂入，**密码不落远端 argv**，
     全程不进 transcript（旧密码由 target.json 读取，新密码只写 HG03-work 凭据文件）。
   - 工具脚本：`HG03-work/rotate-root-20261120.py`（paramiko；先落凭据记录再改密，崩溃可恢复）。
2. HOME 侧 `%APPDATA%\vps-ssh-launcher\target.json` 三 profile 同步更新（逐台原子替换）。
3. OFFICE 同步包：`HG03-work/root-rotation-20261120/credentials-20261120.json`（新旧密码 +
   verified 标记）与 `OFFICE-SYNC-README.md`（OFFICE 机 target.json / VPS.txt / vm-diag.py 更新指引）。
4. 顺带收口 OFFICE 回执 §8 两项遗留：console `sciman-office` 死条目已于 11-18 TS 三面拆除时随
   console 设备+key 全清闭环；MT6000 `advertise-routes=/32` 复验项因 TS 已整体拆除失去对象，闭环。

## 验收证据

- 逐台 `chpasswd` rc=0；**全新连接以新密码验证 `whoami=root` 全过**（12:59:03Z / 12:59:19Z / 12:59:32Z）。
- launcher 正链路端到端复核：`ssh_tool --profile {bwg,zz,aliyun} run` 各回 `E2E_OK`（新 target.json 生效）。
- 实证收获：bwg 与 aliyun 旧口令为**同值复用**（12 字符同值），zz 独立（13 字符）——复用风险随本轮消除。
- BWG vnstat 只读读数：11 月至今 24.49 GiB（rx 12.84 / tx 11.64），月末估算 ~142 GiB，
  avg 1.12 Mbit/s——远低于搬瓦工任一档配额，配额风险=低；套餐准确数字仍以用户 KiwiVM 面板为准。

## 回滚

- 凭据记录 `credentials-20261120.json` 各台保存 `old_password`；需要时以新密码登录 `chpasswd`
  写回旧值即可（旧值仅限安全事件取证，平时不得复用）。
- HOME target.json 由本轮脚本原子更新，无备份文件残留（改前值即记录中的 `old_password`）。

## 遗留与边界

- **OFFICE 侧 SSH（bwg/zz/aliyun profile）在本轮后为旧密码，需按 `OFFICE-SYNC-README.md` 同步后恢复**；
  同步动作在 OFFICE 机执行（用户搬运或下一 OFFICE 会话完成）。
- BWH 套餐月配额数字需用户面板确认（非阻塞，读数已远低于最保守估计）。
- OFFICE 回执 §0/§1 提速杠杆（家宽公网 IPv4 / VM 带宽升配）仍为用户决策项，本轮未动。
