# HG-03 · aliyun VM bbr/buffer 调优 + BWG vnstat 配额监控(真实远端写入证据)

日期: 2026-11-18 | 执行: OFFICE 会话(用户连续授权) | 影响面: aliyun 中转 VM(角色名,IP 不记录)+ BWG
关联: clone 主链路(H-frp 基建);HOME 清单移交件 `HOME-OPTIMIZATION-CHECKLIST-20261118.md`(HG03-work,Git 外)

## 变更 1:aliyun VM sysctl 调优(bbr + fq + 大 TCP 窗口)

- 动机:VM 出向拥塞控制原为 cubic,rmem/wmem_max=208KB 默认;clone 车道(VM 为唯一国际出口)升带宽后跨洋大 BDP 需 ~1.2MB 窗口,3Mbps 现状下无感、升级后决定性。
- 写入:`/etc/sysctl.conf` 追加带 `HG-03 VM tuning` 标记块(default_qdisc=fq、tcp_congestion_control=bbr、rmem/wmem_max=16MiB、tcp_rmem/tcp_wmem 上限 16MiB);`modprobe tcp_bbr` + `/etc/modules-load.d/bbr.conf` 持久化。
- 备份与回滚:`/etc/sysctl.conf.bak-hg03-20261118`(写入前);回滚=删标记块或还原 .bak + `sysctl -p`。
- 验收(ACCEPTANCE_RESULT=PASS):
  - 全局默认 `sysctl -n`=bbr;`default_qdisc`=fq;
  - **受控实战判据**:VM 本机新建 socket(VM→BWG:443 与跳板 SSH 会话)`ss -ti` 显示 `bbr:(bw:…,pacing_gain:…)` 状态机参数=真实挂载 ✓;
  - 无回归:OFFICE 侧 clone 链 25MB=3.19Mbps/59.8s(基线 3.05/63.1,差异=噪声)、204=0.53s;
  - 旧连接按 socket 生命周期保留 cubic,随 frpc 重连/自然老化转换(预期内,不处理)。

## 变更 2:BWG vnstat 配额监控

- 动机:BWG eth0 流量 run-rate≈24GB/天(开机 11.8 天收发 283GB,CPA docker 车道为大头),月估 ~720GB,套餐配额未知——需要可见的日/月流量账。
- 写入:`apt-get install vnstat`(服务 active,数据落 /var/lib/vnstat)。
- 验收(ACCEPTANCE_RESULT=PASS):`vnstat --oneline` 当日 eth0 收 472MiB/发 473MiB,均速 2.48Mbit/s,记账正常。
- 回滚:`apt remove vnstat`。

## 投影件

- `scripts/remote/vm-sysctl-hg03-tuning.sh`:上述 VM sysctl 变更的幂等重放脚本(带备份/标记块判重/验证输出),供 VM 重建或漂移后重放;本次为事后回填投影。

## 观察项(交 HOME)

- BWG 套餐月配额数需确认(run-rate ~720GB/月,升 VM 带宽后必撞,必要时换大配额档);
- frps 用户态转发 CPU 在升带宽后的表现(唯一剩余 >3M 瓶颈候选);
- `check-new-relea` 60% CPU 自旋(Ubuntu 升级检查器,无害观察)。

## 安全备注

- OFFICE 会话 transcript 曾出现 bwg/zz/aliyun 三台 root 密码(掩码过滤缺陷),建议 HOME 维护窗口统一轮换;本文件不含任何 IP/凭据。
- OFFICE 机 target.json 新增 aliyun profile(host/port/user/password/socks5=127.0.0.1:10810),仅存 OFFICE 本机,不入 Git。
