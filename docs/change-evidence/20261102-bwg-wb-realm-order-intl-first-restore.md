# BWG WB realm 服务顺序第二次翻转 INTL-first（2026-11-02 11:21 北京）

**性质**: 真实远端写入（auth 文件改名 ×4）· 脱敏留档
**用户授权**: "车道顺序再次改为 INTL→CN→oc / 继续"
**关联**: 20261101-bwg-wb-realm-order-intl-first.md（同型事务 4fe1c98 与回滚 77fcb5e 的全周期记录、机制依据与 §4 证伪实验）

## 1. 事务（/run/vps-ssh-launcher-maintenance.lock 内，03:21:46Z）

与 2026-11-01 4fe1c98 同型：4 × `mv workbuddy-<uuid>.json → workbuddy-tier2-<uuid>.json`
（内容不动，RENAME 语义），备份 `/root/cpa-wb-realm-order-backup-20261102T032146Z/`
（4 文件副本 + pre-sha256.txt，0700）。watcher 4×`RENAME`+4×`CREATE` 逐条增量处理，
零重启。未触碰 config.yaml / route manifest / admission / 插件；本次 sha 仅作时点
留痕（插件周期重写 auth 文件，跨小时对账必漂移——11-01 台账 §6 教训）。

## 2. 验收证据

- **粘性落点**: 改名后 4s（11:21:50）conductor `auth_file=workbuddy-intl-0faaff85….json`
  （排序后首个 INTL）；该账户当刻带瞬态 6004，跳过冷却轮换由后续候选接续（预期自愈）。
- **doctor 断言**: `git diff --check` 干净；本地三分支 fixture（现态绿/legacy 名
  DRIFT/单边 PARTIAL）全过；远端路由段读回 `workbuddy-realm-order=INTL_THEN_CN`、
  `SECTION_EXIT=0`；全量 strict doctor `DOCTOR_CONTRACT_OK`（含
  `admission-integrity=OK`，并行会话 admission 批次投影已在其自身事务中收口）。
- **本提交**: doctor 断言翻转 + 本台账。

## 3. 语义与速度结论（承 11-01 台账 §4，不变）

本次顺序变更达成用户指定的优先级语义（INTL 免费额度先耗、CN 积分溢出兜底、
oc 终兜底）。吐字快慢分流系上游侧逐请求状态，与账户 realm 无关（11-01 双向
自然实验钉死：同池同账户 25min 内慢流占比 73%→9% 随坏窗自愈）——本变更**不是**
吐字速度的修复杠杆，CN/INTL 服务顺序与速度无关。

## 4. 回滚

维护锁内 `mv workbuddy-tier2-<uuid>.json → workbuddy-<uuid>.json`（备份目录核对
时点 sha），doctor 断言按 77fcb5e 版本还原。Git 回滚不能替代远端恢复。
