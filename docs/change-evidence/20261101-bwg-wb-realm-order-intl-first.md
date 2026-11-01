# BWG WB realm 服务顺序 INTL-first 改名事务（2026-11-01 20:10 北京）

**性质**: 真实远端写入（auth 文件改名 ×4）· 脱敏留档
**用户授权**: "DeepSeek 模型的调用应是优先 workbuddy intl，其次是 workbuddy cn，最后才是 opencode"
**关联**: 20261101-bwg-cpa-deepseek-route-priority-projection.md（同晨 route manifest 事务）、20261101-bwg-deepseek-lane-priority-reserve.md

## 1. 机制依据

wb 插件 0.9.82 的 `pickActiveAuth`（active_auth.go）为粘性单选：锁定当前账户直到
exhausted/disabled/消失，失效后取候选序第一个未耗尽者；候选序来自 host 的
`os.ReadDir` 字典序。原文件名序 `workbuddy-<uuid>.json`（CN）< `workbuddy-intl-<uuid>.json`，
故顺序为 CN→INTL。最小杠杆 = 将 4 个 CN 文件改名 `workbuddy-tier2-<uuid>.json`
（`t` > `i`，排序后 INTL 在前；`-intl-` 分类器不受影响）。watcher 已实证支持
`fsnotify.Rename`（authOps 含 Rename；RENAME+CREATE 逐条增量处理），无需重启。
插件 config 无顺序旋钮（scheduler_mode=credits 只管启用），面板钉选不持久，
补丁重构建属过度设计——均排除。

## 2. 远端事务（/run/vps-ssh-launcher-maintenance.lock 内）

- 旧名 → 新名（内容 sha256 前后一致，仅改名）：
  - `workbuddy-297fa82d…json` → `workbuddy-tier2-297fa82d…json`（148e61e8…）
  - `workbuddy-35a358ae…json` → `workbuddy-tier2-35a358ae…json`（ee62006c…）
  - `workbuddy-b08fc269…json` → `workbuddy-tier2-b08fc269…json`（6ae000d2…）
  - `workbuddy-eb624ce3…json` → `workbuddy-tier2-eb624ce3…json`（dad32413…）
- 备份: `/root/cpa-wb-realm-order-backup-20261101T121035Z/`（4 文件副本 + pre-sha256.txt，0700）
- watcher 日志: 4 × `RENAME`（旧名）+ 4 × `CREATE`（新名）逐条增量处理，无 error/warn
- 未修改 config.yaml、cpa_provider_routes.json、admission、插件、并发/RPM/重试；未重启 CPA

## 3. 验收证据

- 粘性落点: 改名后 7s（20:10:42）conductor 日志 `auth_file=workbuddy-intl-0faaff85….json`
  （排序后首个 INTL 文件）；后续 15min 内 7 个 INTL 文件全部被触达（6004/冷却轮换）
- strict doctor（流式执行已翻转断言的新版模板）: `DOCTOR_CONTRACT_OK`，exit=0：
  `workbuddy-realm-counts=cn:4,intl:7,other:0`、`workbuddy-realm-order=INTL_THEN_CN`、
  `deepseek-live-opencode-priority=-1`、`deepseek-provider-order=WORKBUDDY_THEN_OPENCODE`、
  `admission-integrity=OK`（并行 admission 批次已在其自身事务中落产）
- 仓内契约: doctor 断言 `max(cn)<min(intl)`→`min(cn)>max(intl)`，标签
  `CN_THEN_INTL`→`INTL_THEN_CN`（本提交）；全仓无其他断言引用

## 4. ⚠️ 归因证伪（必须与顺序变更一并阅读）

本次变更的动因假设"wb 慢吐字群体≈CN 账户"被本事务的自然实验**证伪**：

- 改名后 12min 窗（`outputs/cpa-journal-post-reorder-20261101.txt`）n=75（流式 62）:
  慢流(<60 tok/s) 占流式 **61%** vs 改名前 2h 窗 59.5%——纯 INTL 服务下慢/快双模式依旧；
- 触达面覆盖全部 7 个 INTL 账户仍双模式贯穿；TTFB p50 反而 4.30s→5.46s（晚间峰值）。
- 结论: 吐字慢/快分流系**上游侧逐请求状态**（晚间负载 / 6004 节流窗 / 容量窗），
  与账户 realm 无关。顺序变更达成用户指定的优先级语义，但**不是**吐字速度的修复杠杆。
- 早段 2h 窗分析（当日记忆 wb-cn-slow-stream-evidence）的 CN 归因作废，以本节为准。

## 5. 回滚

维护锁内 `mv workbuddy-tier2-<uuid>.json → workbuddy-<uuid>.json`（备份目录可核对
sha256），并 revert 本提交恢复 doctor 断言；改回后顺序为 CN→INTL。Git 回滚不能替代
远端恢复。

## 6. 回滚执行记录（2026-11-01 21:17 北京，用户决策）

§4 证伪结论成立后，用户指示顺序改回 CN→INTL→oc。维护锁内执行：

- 4 × `mv workbuddy-tier2-<uuid>.json → workbuddy-<uuid>.json`；watcher 逐条
  `RENAME`+`CREATE` 增量处理，零重启；排序恢复 CN 在前。
- doctor 断言经 `git checkout 4fe1c98~1 -- scripts/remote/cpa-guardrail-doctor.sh`
  字节级还原（diff=0）；路由段远端读回 `workbuddy-realm-order=CN_THEN_INTL`、
  `SECTION_EXIT=0`（同窗全量 strict doctor 的 admission-integrity 红点属并行会话
  text-finish 批次迭代，不在本写集）。
- **sha 对账说明**：4 个文件中 2 个（297fa82d、35a358ae）内容相对
  `20261101T121035Z` 备份已漂移——插件对 auth 文件的周期性重写（token keepalive/
  状态持久化）所致（该 WRITE 事件流在改名前数小时即存在），非改名副作用；改名
  操作本身不动内容。b08fc269、eb624ce3 两者 sha 与备份一致。
- **粘性落点**：改名还原后 1s 内选户即落首个 CN 文件
  （`workbuddy-297fa82d…json`，conductor 21:17:50）；该账户当刻带 6004 模型级
  限流（2026-11-02 17:49 UTC+8 重置，系白天 CN 优先服务所耗），跳过冷却轮换后
  仍由 INTL 健康账户接续，属回滚后预期瞬态。
- 回滚后 `workbuddy-<hex-uuid>.json` 命名与 CN-first 契约重新自洽，§模拟验收中的
  "新 CN 登录 DRIFT" 路径仅在 INTL-first 命名下成立，现态不再适用。
