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

## 补充：挂账事项执行（同日第三批，用户授权"继续执行剩余事项"）

### CPA #6692 规则退役（bwg，提交 d045748）

- 退役条件核验（v8.0.34 tag 源码逐点）：`isClaudePreservedThinkingModel` 精确覆盖
  lane 三族（claude-opus-5-5/sonnet-5-5/haiku-5-5），executor 原生注入
  `thinking.block_binding.prefix_mismatch_behavior=drop_block`，
  `applyBetaHeader` 对携带 block_binding 的 body 自动补 binding beta 头，
  `dropUnsupportedClaudeAssistantPrefill` 对全部 major>=5 opus/sonnet/haiku
  原生剥离尾随 assistant——binding override 与 prefill filter 两段规则退役；
  zen-strict fields 与 fallbacks 过滤按 runbook 保留。cpa-health 双探针保留，
  注释改为守护原生路径的结果级回归防线；runtime 测试新增
  block_binding/messages.-1/betas 复活否证断言。
- 投影：`-Apply` `GUARDRAILS_APPLIED`，doctor `DOCTOR_CONTRACT_OK`、
  `drift=cpa_policy.py MATCH`、`drift=cpa-health.py MATCH`。
- 受控实战层被无关账号信号阻塞：admission 重启自检时按持久化状态闩锁
  claude-oauth 车道 `authentication_required`——CPA 侧凭据刷新返回
  `invalid_grant: account_on_hold`（claude.ai/restricted）、请求侧
  `OAuth access token has been revoked`（Anthropic 账号 num01pro@gmail.com
  被置于 restricted；doctor `oauth_claude_refresh_failures_7d=11` 表明刷新
  失败已积累数日，非本次变更引起；payload 规则不参与 OAuth refresh）。车道
  fail-closed（`operator_review_required`）按设计可见。**挂账：claude lane
  处置（申诉/放弃/重登）权属用户；重登后保留的行为探针即自动完成退役的
  行为级验收。**另取证：公网 /v1/messages 面的 403 需钉
  `User-Agent: cpa-bwg-gateway/1.0`（10/16 CF 1010 处置延续有效）。

### zz sing-box 1.14.2 → 1.14.3（受控实弹升级）

- 官方 release 资产取证：sing-box-1.14.3-linux-amd64 二进制 sha256
  `768d31fa7f68debc067bb4626539498a1e8751d36a7b99e1a54bee6a23815f93`。
- 投影前 fail-closed 拦截一次：`-VasmaSha256` 误用 bwg pin（fca0ad30）被
  `vasma SHA-256 mismatch ... exit 13` 拒绝——取证发现 **zz 的 install.sh 已于
  2026-10-19 15:35 UTC 换为 sciman-v2ray-agent fork HEAD 版**（v3.5.25+fork
  补丁，sha `15551f809c54e95314cbb07e7b8ca38c4668d3feb9439cee5167573134223976`，
  与本地 checkout b4d72b7 的 install.sh 字节级一致；diff 上游 5c5e2b72 仅
  isValidUUID/fodder URL/UUID 回退等已知 fork 特性，101 行）。旧 pin
  a5f2b2c5(v3.5.12 基线) 若不跟进，10-24 周五周更将 exit 13 拒跑。投影改用
  fork HEAD sha 后成功。
- 受控触发（手动单发 wrapper，唯一远程命令规则）：10 秒完成，status
  `result=success exit_code=0`；升级后 sing-box version 1.14.3、二进制 sha 与
  pin 一致、`sing-box check -c` OK、google_ipv4 片段 10 域
  （domain_suffix + action resolve + strategy ipv4_only）完好、UDP 监听在位、
  `PRUNE scope=core_backups policy=keep_8`。
- 基线变化入档：双机 vasma 从此分叉——bwg=上游原版 fca0ad30、zz=fork 版
  15551f80；"刷 vasma 须同步 pin"纪律的三处锚点（bwg wrapper/updater
  SOURCE_REF、zz wrapper）各自独立。

### 巡检自动化重建

- 每日 22:05 双机维护面巡检（automation-03c376a6）以 2026-10-20 实测基线
  重建：双机 root crontab 空；bwg cron.d=4（kernel-update/monthly-maintenance/
  renewtls/v2ray-agent-update）、zz cron.d=3（kernel-update/monthly-maintenance/
  renewtls）；maintenance-status 新鲜度阈值（每日<48h/周<9d/月<34d）；
  claude 车道 hold 与 11-11 续期观察列为已知挂账核对项。只读纪律内置于
  prompt。

### 模拟/受控实战补齐（claude 退役后，同日第四批）

- claude-free 六场景 committed fixture 于 bwg 重跑：`ACCEPTANCE_FIXTURE_PASS`
  + `CLEANUP_OK`，四场景 1/1/10/0 全符——claude 车道/claude-messages 阶段
  移除后的 harness 实弹复通。
- 8318 admission 前门受控单发：glm-5.3-flash 200（模型回显正确），
  healthz 车道恰为 4 条（claude-oauth 消失）+ traffic_guard 在位——重启后
  4 车道 admission 转发实弹验证。

### outputs/ 第二轮退役（提交 d2373b0）

- 254→46（删 208 个一次性会话产物）：分类规则=全仓零功能性引用+改动≤
  2026-10-17 冷却+对应事务已有 change-evidence 收据（git 历史可追回）。
  保留=冷却不足（10-18 起 canary 部署脚本+10-19 入库的 22 件 20261016 组，
  2026-10-22 后可复评）、方法附录家族、runbook/测试/代码功能性引用 7 件、
  claude 车道未决域 backing 2 件。验证=KEEP 清单程序化重建（208=254−46）+
  抽查引用仅备案性提及+DELETE 名对 runbooks/tests/scripts 机械扫描零命中。

### claude-oauth 车道退役（用户指令，提交 f3b7e68/93173a2/7b957a3）

- 背景账号信号：Anthropic 于 2026-10-20 01:19 暂停 num01pro@gmail.com（异常
  活动，审核约 10 天）；CPA 凭据刷新返回 `invalid_grant: account_on_hold`，
  请求侧 `OAuth access token has been revoked`。用户已在 CPA 侧删除凭据并
  指令清除 claude 模型目录。
- 仓侧退役：manifest 移除 claude-oauth oauth_route；admission.json 移除
  claude-oauth 车道（health/policy 期望集全 manifest 驱动自动收缩，目录
  13→10）；doctor 车道矩阵/==claude-lane== 段（改写为复活否证守卫：
  manifest 重现路由即 mark_fail）/claude-risk 权限块容缺/posture 集合同步；
  fixture 移除 claude 车道与 claude-messages 阶段；catalog expectations
  tombstone 化；admission 风控机制测试改用合成车道（原始 JSON+retired lane
  重过 load_config）保持代码覆盖；#6692 双探针保留（车道级触发，inert）。
- 连锁修复两处（事务 fail-closed 当场抓到）：①apply 模板内嵌 admission
  车道矩阵漏同步（首轮 -Apply `ROLLBACK admission_config_contract`，
  ROLLBACK_VERIFIED 投影未生效）；②apply 的 requests.payload 块从幂等追加
  改为 manifest 派生整体替换（部署 config 残留 3 条 claude fallbacks 孤儿
  规则被 semantic_policy 拒绝；新块镜像 policy 派生，漂移由 semantic_policy
  门兜底），对应测试改钉替换+幂等+陈旧清除语义。
- 验收：`GUARDRAILS_APPLIED`（备份
  /root/cpa-guardrails-backup-20261020T011219.851891128Z）；严格 doctor
  `DOCTOR_CONTRACT_OK` + `claude_lane=RETIRED no_oauth_route` +
  `oauth_claude_monitor=ABSENT_OPTIONAL`（凭据文件已删）+ drift MATCH；
  目录实读 `claude_ids=[]`（total 10）；glm-5.3-flash 单发 200 无回归。
- 保留面：guardrails `-QuarantineClaudeOAuth/-RestoreClaudeOAuth` 开关
  （未来重挂的用户工具，路由重现会先被 doctor 复活守卫拦截）、cpa-health
  binding/prefill 探针（车道级触发 inert）、claude 相关 runbook 与
  upstream-issue-claude-replay-400.md 草稿。重挂路径=申诉通过或新账号经
  审查事务恢复 manifest 路由+admission 车道+catalog expectations+测试案例。

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

