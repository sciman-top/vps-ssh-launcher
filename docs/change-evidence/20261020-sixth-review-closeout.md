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

## 补充：模拟/受控实战验收闭环（同日第二批，用户二次授权）

审查收口后用户复核提问"投影/模拟/受控实战"，逐层补齐并实测：

### P2-1 验收 harness 实弹（bwg，六场景 committed fixture）

- 首轮实弹即抓到 harness 两处真实漂移：①fixture 配置仍声明 muse-spark
  （codex-api-key 第二条 + opencode-zen 条目），而 10/18 opencode.ai 退役
  （c894a02）已把 muse 移出 cpa-health 合同集 → 目录 unknown → readiness
  exit 20；②updater `--confirm-pending` 闭环（10/7 加入）使 transient 场景
  遗留 pending 文件挡住 success 场景。
- 修复：fixture 配置移除 muse 两处、deepseek 改由 workbuddy-fixture 车道
  声明（生产由插件贡献、fixture 不加载插件）；readiness 失败断言携带
  verdict+live_catalog 诊断；transient 场景断言 pending 存在后按运营者等价
  清理；driver 将 `/var/lib/vps-ssh-launcher` 绑定挂载隔离进 fixture。
- **生产面污染发现与清除**：隔离修复前的 fixture 运行把 pending
  （version=v0.0.2 假 digest，可证伪残留）与 unverified 心跳写进了生产
  `/var/lib/vps-ssh-launcher/maintenance-status/`——已删残留 pending 并以真实
  `auto-update.sh --check`（CANDIDATE current=v8.0.34 target=v8.0.34、
  BACKUP_HEALTH ok、success exit 0）恢复心跳真值；该 `--check` 同时是环境
  变量可覆盖改造后新 updater 的首次生产执行（默认字面量行为不变）。
- 终轮判据（bwg，一次性命名空间，v8.0.34 本地钉定镜像 + 新投影 updater）：
  `ACCEPTANCE_FIXTURE_PASS`、`CLEANUP_OK`，四场景
  start_fail=1/model_exposure=1/transient=10/success=0 全符合期望码，
  traffic-guard env 覆盖与 fixture 本地锁全路径实弹生效。

### zz renewtls reload 分支受控 drill

- touch 证书文件使 mtime(18:53:22) 新于 sing-box 进程启动(10-13 01:30:18)
  → 新鲜度守卫判定 stale → wrapper exit 0 走 drift 分支：恰一次 nginx
  reload + sing-box restart（ActiveEnterTimestamp → 18:53:23），
  **`RENEWTLS_LOADED_CERT_VERIFIED` 生产首次出现**，applied.sha 与证书 sha
  一致（089aa0fc），双服务 active。
- restoration：复跑 wrapper → steady 零重启（uptime 不变、VERIFIED 计数
  不增）、hysteria2 UDP 监听在位、exit 0。
- 11-11 首次真实续期风险由此提前消解：reload/restart 分支已有生产实弹证据。

### 其余实测抽查

- doctor `==cpa-policy==` 脱敏段实弹：输出仅结构键行，`DOCTOR_CONTRACT_OK`
  复确认。
- `vps-maint plan --profile bwg --live-inventory --run-integration` 全流程
  PASS（真实 inventory：xray pin noop/docker present/deferred 按策略，
  exit 0）——批次 1 maintenance_cli 改动的真实流程回归。
- 全量门禁复跑：717 passed + 347 subtests 全绿。

