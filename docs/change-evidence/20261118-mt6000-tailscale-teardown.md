# HG-03 · MT6000 Tailscale 三面拆除(真实远端写入证据)

日期: 2026-11-18 | 执行: HOME 会话(用户批准"拆") | 影响面: HOME 主路由 MT6000(角色名,IP 不记录) + HOME Windows 主机 + Tailscale console
关联: 单位网死路裁决与 clone 单车道审查(回执 `OFFICE-REPLY-20261118.md`,仓根未跟踪);R2 bringup 回执(HG03-work kit,Git 外);clone 主链路(H-frp 基建)零影响

## 背景与裁决

- 单位网 UDP+国际 TCP 全封 → fq 车道递归死锁,用户裁决弃 overlay(详见回执 §1-§2);HOME 侧审查实测确认 clone 单车道为唯一可行形态。
- overlay 终止后保留的 Tailscale 配置=纯维护面/安全面(gl-mt6000 key expiry 已 Disable=永不过期的活凭据),用户裁决全面拆除。
- 预检断案: 路由器 11-18 午前重启(实证 up 8:41)→ rc.local 钩子竞态缺陷实锤——开机 +30s `tailscale set --advertise-routes` 静默失败(`>/dev/null 2>&1` 吞错)而 tailscaled 靠 state 文件自启重连,live prefs `AdvertiseRoutes=null`。挂账项"重启后复验 /32"就此结案: 钩子不可靠,R2 路由事实已死,拆除无功能损失。
- Windows 主机二进制用户已自行卸载(无服务/进程/适配器/NRPT=0)。

## 变更清单

1. **HOME Windows**: 删 `%LOCALAPPDATA%\Tailscale` 残留(3 个日志文件,0.7KB,无 tailscaled.state/节点私钥——已随卸载清除)。
2. **MT6000(SSH key 通道,自动化公钥=11-18 R2 bringup 所装)**:
   - 备份 `/etc/rc.local` → `/root/rc.local.bak-teardown-20261118`(327B,写入前);
   - 摘除 rc.local 2 行钩子(`(sleep 30; …advertise-routes…)` + `# hg03-r2 bwg32 reassert`),gl_util/remount_ubifs/drop_caches/`exit 0` 完整;
   - `tailscale logout`(state 由节点私钥缩成 208B 登出态,随后删除,节点身份彻底清);
   - `uci set tailscale.settings.enabled='0'` + commit;`/etc/init.d/tailscale stop && disable`;
   - firewall zone `tailscale0` 保留(死配置,不动 fw3);`/etc/tailscale/` 仅剩 derpmap 缓存。
3. **console(用户手动)**: Remove gl-mt6000 / sciman-home / sciman-office 三设备;Keys 页零 auth key 零 API token(截图实证);tailnet 账号保留,零设备即无攻击面。

## 验收(ACCEPTANCE_RESULT=PASS)

- 运行态: 零 tailscaled 进程、tailscale0 接口消失、`tailscale status`=无法连接、释放 ~57MB;stop 后 Go 进程优雅退出较慢(数十秒),复检确认退净。
- **开机链静态三证**(各独立挡住 boot 自启): `/etc/rc.d/` 无 tailscale symlink + `/etc/init.d/tailscale` disabled + uci `enabled='0'`。
- 金丝雀: LAN 0ms / 公网 13ms / baidu 200 / xray→BWG 204——家庭网络与 clone 车道零影响。
- 剩余实战项: **受控重启验收待用户择时**(断网 1-3 分钟)——本机 boot 行为是唯一未实证路径(同日钩子竞态即发生于 boot 期),重启后补验零残留+LAN 恢复。

## 回滚

`/root/rc.local.bak-teardown-20261118` + `/root/uci-tailscale-backup-2026-11-18-0009.txt` + `/root/ts-prefs-backup-2026-11-18-0006.json` + R2-BRINGUP 报告(HG03-work kit): tailscale 重登录 + console 重批 /32 即复活 R2。

## 投影件

判否(R5): teardown=回归设备默认态,无重建/漂移重放需求,不造重放脚本;仓内 `git grep -i tailscale` 零引用,无过期投影需修。

## 观察项(交 HOME)

- 下次路由器重启(受控或自然)后补一次零残留复验,闭掉最后实战项;
- BWG vnstat 实测与 OFFICE 估算有量级出入(11-18 实测全日累计 ~1.6GB vs 估算 24GB/天),以 vnstat 账为准,套餐配额确认降为低优先;
- 新装宽带 B 计划(独立于中转 ECS 的冗余路径)维持待办。

## 安全备注

- 本文件不含任何 IP/凭据;SSH 经由自动化公钥通道执行。
- bwg/zz/aliyun 三台 root 密码轮换挂账不变(OFFICE transcript 泄漏事件,见同日 aliyun 调优证据);轮换不破坏离机备份(ECS 备份走 authorized_keys 密钥认证,已核实)。
