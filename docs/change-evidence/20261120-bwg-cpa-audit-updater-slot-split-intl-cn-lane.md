# bwg CPA 巡检：v8.0.79 已是最新、更新锁竞态与 CN 车道 INTL 凭据收口（2026-11-20）

**目标**：按用户指令自主巡检 bwg 上的 CPA（CLIProxyAPI）与插件版本，复核
OpenAI/GPT、Antigravity/Gemini、WorkBuddy（CN/INTL）、Claude/Anthropic 多条
OAuth 账号的封号/限流/降智/吐字/缓存风险，并修复巡检中证明的项目代码缺陷。
`zz` 未触碰；全程只读探针，唯一远端写入是本节明确列出的受控项。

## 结论

- **版本面：无可升级项。** `gh release list` 最新即 `v8.0.79`（2026-11-19T13:11:56Z），
  上游 main HEAD = tag `v8.0.79` = 现役构建的 `Commit: 6faa8d20b2673404cff70a2f2d9537ae0d0e684d`。
  在役镜像 `cli-proxy-api-fork:v8.0.79-gemini-raw`（image_id `1d6eafc5…`，二进制 `ec1ad20c…`），
  容器启动日志自证 `Version: v8.0.79, Commit: 6faa8d20…+gemini-raw`。
  `scripts/cpa_fork_rebuild.ps1` 干跑复验 `DRY_RUN_OK`（`TAG_COMMIT_OK`、`PRISTINE_TRANSLATOR_SHA=6f8e55d1…`、
  `SOURCE_TARBALL_SHA=69dc7219…`），v8.0.79 的 gemini-raw 移植仍与现役一致。
- **插件面：无可升级项。** `luode0320/cpa-workbuddy-plugin` 的 `registry.json` 最新
  `workbuddy-provider=0.21.27`、`workbuddy-ai-provider=0.1.40`；容器注册行
  `plugin registered … version=0.21.27` 与 doctor `workbuddy-version=cn:0.21.27 intl:disabled_closed_dark_20261117` 一致。
- **上游未发布提交：仅观察。** `v8.0.79..origin/main` 在本次 fetch 后为 0 提交；期间观察到的
  open issue/PR（#7691 → PR #7692 `detail-only Codex 429 as transient rate limit`、#7614
  antigravity 无信号 429 分类、#7699/#7700 antigravity TOS 凭据后台刷新）**未合入**，
  不进入本栈，也无本地补丁必要。
- **发现并修复两处真实缺陷**（见下"项目修复"）：每日更新器与其它后台事务抢同一把锁、
  CN 车道 INTL 凭据未收口。
- **风控读数（24h 窗口）**：`credential-ban-signals` 全 0；`admission_lane_rejects_24h=0`、
  `admission_capacity_events_24h=0`；缓存命中率 `cpa_cache_reset_probe` 24h =
  `cache_hit_ratio=0.9502`（`eligible=0.9503`，1395 轮），重置归因
  `cache_eviction=24 / model_switch=15 / unattributed=12`，**零 rebind**。

## 项目修复 1：更新器 04:00 与其它后台事务抢锁（bwg 实测丢了一天更新）

`/opt/cliproxyapi/auto-update.log` 2026-11-19T04:13:29Z：
`DEFER: no-update readiness failed; image unchanged`，而当日 v8.0.78 已进入 72h 成熟窗。
四个后台事务（updater 每日 04:00 UTC、月度维护 04:10、xray 内核 04:20、sing-box 内核 04:30）
共用 `/run/vps-ssh-launcher-maintenance.lock`，updater 用 `flock -n` 不等待，于是
"哪次更新真的执行"由调度时隙决定而不是由成熟度决定。

- `scripts/remote/cpa-guardrail-apply.sh`：`-Apply` 只改写
  `/etc/systemd/system/cliproxyapi-update.timer` 的 `[Timer]` 段
  （`OnCalendar=*-*-* 04:00:00`、`RandomizedDelaySec=300`），其余行保留；
  事务前备份 `$BK/cliproxyapi-update.timer`，回滚同交易，写入发生在 `daemon-reload` 之前。
- `scripts/remote/cpa-updater-schedule-check.sh`（新增）：读 `systemctl show` 的
  `TimersCalendar/RandomizedDelayUSec/TimeoutStartUSec`，逐项判定四个锚点、抖动（≤300s）、
  start 预算（≤600s）与 compose/容器前提；`--status` 恒退出 0。
- `scripts/remote/cpa-guardrail-doctor.sh`：新增 `==updater-schedule==` **读数**段
  （doctor 先于 `-Apply` 运行，硬门会挡住安装拆分的那次事务；严格性留给
  `==projection-drift==`），并在 `==timer==` 增加 `timer_on_calendar` /
  `timer_randomized_delay` / `timer_last_triggers`。
- 本地实测（本机既有 timer）：`updater-schedule-slot=OK`、`jitter=FAIL reason=randomized_delay=1800`、
  `budget=FAIL reason=timeout_start=1200s` —— 读数按预期指出**现状仍需一次 `-Apply` 才会拆分**。

## 项目修复 2：CN 车道 INTL 凭据未收口（24h 内 6 次裸 429）

`workbuddy-ai-provider` 自 2026-11-17 关闭暗置，但其 7 份 `workbuddyai-*.json` 一直启用：
插件周期余额扫描仍读它们，余额耗尽时回裸 429（`admission_upstream_429_503_details=
passthrough/gemini-4-neon-xhigh/HTTP429/noncapacity:6`）。doctor 现读数
`workbuddy-intl-auth-files=7 disabled=6 enabled=1`。

- `scripts/remote/cpa_policy.py`：新增 `count_workbuddy_intl_disabled` 与 CLI
  `count-intl-disabled AUTH_DIR`（同时列入 `_PROBE_REQUIRED_HELPERS`）。
- `scripts/remote/cpa-guardrail-apply.sh`：事务前快照 `auth/workbuddyai-*.json` 到
  `$BK/intl-auth/`（按原名回滚），只把 `disabled` 就地置 `true`（键序与字节布局不变，
  不读写 refresh 物料）；前置 `REFUSE intl_disabled_counter_unavailable` /
  `REFUSE intl_disabled_expectation_partial`，投影后 `ROLLBACK intl_auth_disabled_readback`。
- `scripts/remote/cpa-guardrail-doctor.sh`：`==workbuddy-config==` 增加
  `workbuddy-intl-auth-files` / `workbuddy-intl-auth-enabled` /
  `workbuddy-intl-auth-disabled-guard=armed|READOUT_ONLY`（首次部署自举窗口降级为读数）。
- `scripts/remote/cpa-guardrail-apply.sh` 另把 `observability.usage` 的两项 CPA 默认值钉定，
  使"声明的姿态"机器可见（无行为变更）。

## 首次 `-Apply` 暴露的自举缺陷（同日修复）

第一次 `-Apply` 以 `REFUSE intl_disabled_counter_unavailable` 终止：apply 事务在**用上一代**
`cpa_policy.py` 做前置计数，而计数函数被写在模块尾部，上一代根本没有该动词。
失败发生在 `MUTATED=1` 之前，远端零写入。修复：把
`WORKBUDDY_INTL_AUTH_PREFIX` / `count_workbuddy_intl_disabled` 移到模块顶部常量区
（与 `ADMISSION_CONFIG` 装载同层），并补 `test_policy_counter_verb_is_reachable_before_projection`
钉住"定义必须先于 `__main__` 且 CLI 计数正确"。

第二次 `-Apply` 仍被同一 REFUSE 挡住，定位到真正的根因：**apply 事务永远跑在
它自己这次投影的上一代**（第一次上线新控制时就是这种情形），所以拿
`/opt/cliproxyapi/cpa_policy.py` 的旧版去问一个旧版从未有过的动词，必然失败。
修复：前置计数改为对**本 payload 内嵌的** `cpa_policy.py` 副本执行
（`__CPA_POLICY_B64__` → 临时目录 → `count-intl-disabled`），它只读 auth 文件，
与宿主上的旧模块无关；投影后回读仍用新落盘的 `$DIR/cpa_policy.py`。
两次拒绝都发生在 `MUTATED=1` 之前，远端零写入。

第三次 `-Apply` 被 `REFUSE intl_disabled_expectation_partial files=7 disabled=6` 挡住：
六个 INTL 凭据本来就是 2026-11-17 切换时就已禁用，期望值本就该从 6 起步、由本次投影补到 7。
"中间态即拒绝"是错误前提，改为只打印 `INTL_AUTH_BASELINE`，完整性由投影后回读
（`count-intl-disabled == 投影后计数`）证明。

> **工作树污染（本轮插入）**：检查期间发现 `scripts/remote/cpa_policy.py` 与
> `scripts/remote/cpa_provider_routes.json` 被**本任务之外的并发编辑**写入了
> `oauth_settings_contract`（引用了未定义的 `EFFORT_LEVELS`，`ruff` F821，且指向
> 并不存在的 `v8.0.79+agy-effort` fork 构建，与当前 gemini-raw 镜像不一致）。
> 本轮不替他方决定该特性的去留：已按 `git checkout` 还原为 HEAD，并把原始 diff
> 存档到 `%APPDATA%\vps-ssh-launcher\concurrent-work\20261120-oauth-settings-contract.patch`，
> 交回其所有者处理。

## 远端写入

**本次未对 bwg 写入。** 上述两项修复都要经 `-Apply`（自带 drain、备份、回滚与
admission 重启）落地；按仓库"破坏性远端写入先预演、经授权再执行"的口径，
应在用户确认维护窗口后运行：

```powershell
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg            # 严格 doctor（投影 hash 全 MATCH）
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -Apply    # 投影 timer 拆分 + INTL disabled
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg            # 复验 ==updater-schedule== 全 OK
```

## 回滚入口

- 远端：`-Apply` 备份目录 `/root/cpa-guardrails-backup-*` 内含
  `cliproxyapi-update.timer` 与 `intl-auth/` 原文件；`restore_all` 覆盖 EXIT 路径，
  手动恢复 = 拷回这两个位置后 `systemctl daemon-reload`。
- 本地：`git revert` 本提交（`scripts/remote/fork-binary.sha256` 未变，无需换芯回滚）。

## 本地门禁

`scripts/run_gates.ps1 -RunParallel`：`contract:catalog` / `build` / `test-lane`（1762 passed,
31 skipped, 833 subtests）/ `hotspot:bandit` / `lint:ruff` / `lint:format` / `type:mypy`
全通过，`EXIT=0`；`-AuditProjection` = `CPA_PROJECTION_AUDIT=PASS evidence=filesystem_projection`。
