# BWG admission 投影防回退护栏

## 触发与根因

- 2026-10-10 01:12 UTC 的 fresh strict doctor 发现
  `/opt/cliproxyapi/cpa-admission.py` 从已验收的 HEAD 哈希
  `3eec9fcf0966759bd76ed2e48e066320bed0695701477fce49fc91cfd8eb09a4`
  回退到旧候选 `5e590c0615aca877d7932c2d7cfe9d4f3e5a8c966acdadf780e47a6cc276a6fd`。
- 远端文件的 mtime/ctime 为 17:59:50 UTC；同一时段 SSH journal 出现来自维护客户端的
  连续短会话，`/tmp/cpa-*` 和新的 `cpa-guardrails-backup-*` 同时出现，说明是旧的完整
  guardrail apply 晚到覆盖了正确投影。Docker 镜像、容器挂载和 auto-update 只改镜像
  声明，不能解释该 host-side Python 文件变化。

## 修复

1. 在维护锁下用窄事务把 `/opt/cliproxyapi/cpa-admission.py` 恢复到 HEAD 哈希，保留漂移前
   备份 `/root/cpa-admission-floatfix-backup-20261010T012445Z/`，保持 0755，重启并复验
   `8318/healthz`。
2. 安装独立 root-owned admission integrity guard：
   - `/usr/local/libexec/cpa-admission-integrity-check`
   - `/etc/systemd/system/cpa-admission.service.d/10-integrity.conf`
   - `/etc/vps-ssh-launcher/cpa-admission.sha256`
3. systemd 在每次 admission 启动前校验文件哈希。旧候选再次晚到时，重启会 fail closed，
   原有 guardrail apply 的 backup/rollback 会恢复上一个已钉住版本；旧脚本不会覆盖该
   drop-in。正常的未来 admission 代码变更必须在同一受审查事务中更新 pin。

## 受控结果

- guard 安装事务：`INTEGRITY_GUARD_DEPLOYED`、`ADMISSION_INTEGRITY_OK`、维护锁释放，
  `cpa-admission.service` active，`healthz` 正常。
- 源文件、checker、drop-in 和 pin 均纳入本仓库投影清单；strict doctor 还会检查 drop-in
  是否已加载以及 pin 是否等于当前 HEAD 值。

## 回滚

在 admission lane 空闲时，使用远端 guard 安装事务留下的 backup 恢复 checker、drop-in、pin，
`systemctl daemon-reload` 后重启 `cpa-admission.service`，再运行 strict doctor。Git 回滚不能
代替远端文件和 systemd drop-in 的恢复。
