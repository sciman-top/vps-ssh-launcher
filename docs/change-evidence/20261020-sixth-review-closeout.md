# 2026-10-20 第六轮全链审查收口（批次 1-5）收据

## 授权与范围

- 用户当次明确指令：按第六轮审查报告的推荐连续自动修复直至完成。
- 仓库侧：本地工具八项修复 + 远端投影面修复 + 测试补强 + runbook 同步（提交
  `12b5292` 批次 1 与 `4d63e75` 批次 2-3；full gates 717 passed + 347 subtests）。
- 远端侧：bwg guardrails 投影收敛 + zz renewtls wrapper 收敛（本收据）。
- 未执行（维持用户决策挂账）：sing-box 1.14.2→1.14.3 升级、CPA #6692 规则退役、
  outputs/ 退役轮、本地巡检自动化重建。

## bwg guardrails 投影收敛

- 只读探针（apply 前）：`auto-update.sh` drift MISMATCH（repo 前进，预期）、
  `cpa-update-timeout.conf` LIVE_MISSING、`updater_timeout=20min` 在位、
  `DOCTOR_CONTRACT_FAILED`（漂移可见性按设计生效）。
- `-Apply`：`PROJECTION_HASH_VERIFIED` ×13（含新
  `/etc/systemd/system/cliproxyapi-update.service.d/cpa-update-timeout.conf`）、
  `BACKUP_DIR=/root/cpa-guardrails-backup-20261019T181244.571581151Z`、
  `GUARDRAILS_APPLIED`、PRUNE keep_8 removed=1。cpa-admission 按事务语义重启一次，
  healthz 200 + 公网 401 探针过；cpa-admission.py 字节未变（pin 未旋转）。
- apply 后严格 doctor：`DOCTOR_CONTRACT_OK`，drift 13×MATCH（含 conf），
  `updater_timeout=20min` / `updater_stop_timeout=4min` 双值过新阈值门禁。
- drop-in 去重：10/19 激活事务遗留的 `20-maintenance-timeout.conf` 与新投影
  `cpa-update-timeout.conf` 逐字节相同（cmp 断言通过后删除），备份
  `/var/backups/cliproxyapi-dropin-dedup-20261019`，daemon-reload 后双值保持
  20min/4min（`DEDUP_OK`）。
- unattended-upgrades 取证（审查建议项）：bwg 已启用（`APT::Periodic::
  Unattended-Upgrade "1"`），security pocket 自动升级作为月度窗口间兜底在位。

## zz renewtls wrapper 收敛

- 只读探针：wrapper/cron.d 在位、`renewtls_log_age_days=0`、无 legacy root cron、
  `RUNTIME_VERIFY_OK`。
- `-Apply`：备份 `/var/backups/v2ray-agent-renewtls-deploy.o3Dojq`、新 wrapper sha
  `0a939aeb584ee0adff3e0fd57e4d50ed301cbac3aa31f4b7bb347b0dd14d94b5`、
  `RENEWTLS_LOCKED_PROJECTED`。
- 受控验收（手动单发 wrapper）：exit 0、1 秒完成、`renewtls.status
  result=success exit_code=0`；acme `--renew` 无 --force 跳过 + vasma 兜底
  no-op（"剩余天数:84/证书有效"）；`renewtls-applied.sha`(089aa0fc…) 与现役证书
  文件 sha 一致 → steady 零重启路径；sing-box ActiveEnterTimestamp 保持
  2026-10-13 01:30:18 UTC（本次运行 18:15:35Z，零重启实证）；`nginx -t` OK。
  新核心新鲜度守卫在现役主机上判定 fresh（证书 mtime 01:30:16 ≤ 进程启动
  01:30:18），首次生产数据点与 fixture 语义一致。
- bwg 段零漂移：模板 bwg 渲染 HEAD==工作版逐字节一致（pwsh 重建复证），
  本轮未触碰 bwg renewtls 面上任何文件。

## 验证与边界

- full `run_gates.ps1` exit 0：717 passed、347 subtests、1 真实 SSH integration
  按默认跳过；bandit/ruff check/format/mypy 全过。
- 本收据不声明：CPA 镜像更新、插件更新、内核/xray/sing-box 升级、任何生成探针
  或自然流量验收。zz 首次真实续期观察点（~2026-11-11，
  `RENEWWTLS_LOADED_CERT_VERIFIED` + acme 版本/Le_RenewalDays 取证）不变。
- 回滚：bwg=guardrails 备份目录（keep-8 内）+ drop-in 去重备份；zz=renewtls
  部署备份 o3Dojq（wrapper/cron/crontab 三件 + verify_rollback_state）。Git
  回滚不能代替远端恢复。
