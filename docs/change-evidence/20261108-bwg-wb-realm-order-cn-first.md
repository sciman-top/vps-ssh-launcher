# BWG WB realm 服务顺序第三次翻转 CN-first（2026-11-08 15:44 北京）

**性质**: 真实远端写入（auth 文件改名 ×7，含一次同锁自纠）· 脱敏留档
**用户授权**: "在bwg VPS CPA上，workbuddy DeepSeek车道顺序修改为 CN→INTL。"
**关联**: [20261102-bwg-wb-realm-order-intl-first-restore.md](20261102-bwg-wb-realm-order-intl-first-restore.md)（同型事务机制依据）、[20261107-bwg-cpa-v8059-window.md](20261107-bwg-cpa-v8059-window.md)（`tier2-` 命名由来）、cpa-gateway runbook 车道顺序段

## 1. 机制依据（承 11-01/11-02，不变）

wb 插件 `pickActiveAuth` 粘性单选，候选序 = host `os.ReadDir` 字典序；watcher 实证支持
`fsnotify.Rename`（RENAME+CREATE 增量处理），零重启。CN-first 契约命名 = 裸名
`workbuddy-<uuid>.json`（hex 首字符 < `i`，天然排在 `workbuddy-intl-*` 前；11-01 台账 §6
已证与 CN-first 契约自洽）；INTL 保留 `workbuddy-intl-` 标记。`tier2-` 前缀无脚本消费
（全仓仅 doctor 注释引用），纯排序杠杆。本次动机：INTL 共享桶当日 4/7 耗尽、CN 积分池
6/7 健康（余 2200–3078），CN 先耗积分、INTL 共享桶转兜底。

## 2. 远端事务（/run/vps-ssh-launcher-maintenance.lock 内）

- **域名断言**: 事务前只读探针 + 事务内复验双重复核，7/7 `auth.domain=www.codebuddy.cn`
  `auth.region=cn`，7/7 intl 侧 `www.codebuddy.ai`/`intl`，全部 `disabled=false`。
- **备份**: `/root/cpa-wb-realm-order-backup-20261108T074448Z/`（7 文件副本 + pre-sha256.txt，
  0700/600）。
- **改名**: 15:44:49 首搏 `workbuddy-tier2-<uuid>.json` →（见 §5 偏差）裸 uuid 名；
  15:45:52 同锁内自纠 → `workbuddy-<uuid>.json` 契约名 ×7。内容 sha256 前后逐字节一致，
  仅改名；未触碰 config.yaml / route manifest / admission / 插件 / 其他 provider 文件；
  零重启。
- **watcher**: 两轮各 7×RENAME+7×CREATE 逐条 "processing incrementally"，无 error/warn
  （15:44:49–50 与 15:45:52 两窗，docker logs cli-proxy-api）。

## 3. 验收证据

- **粘性落点**: 单发探针 `deepseek-v4.1-flash` → `pong`（15:47:41 POST 200 1.827s），
  同秒 `workbuddy-297fa82d….json` 发生 WRITE+CREATE（选中即回写状态，新版 0.9.88 日志
  形制；旧 conductor `auth_file=` 行已不存在）= 排序后首个 CN 文件，CN-first 生效。
- **strict doctor**（流式执行已翻转断言的新版模板）: `workbuddy-realm-counts=cn:7,intl:7,other:0`、
  `workbuddy-realm-order=CN_THEN_INTL`、`deepseek-manifest-opencode-priority=-1`、
  `deepseek-live-opencode-priority=-1`、`deepseek-provider-order=WORKBUDDY_THEN_OPENCODE`、
  projection-drift 零 FAIL、`DOCTOR_CONTRACT_OK`、wrapper exit=0。
- **仓内契约**: doctor 断言 `min(cn)>max(intl)`→`max(cn)<min(intl)`，标签
  `INTL_THEN_CN`→`CN_THEN_INTL`，注释更新（本提交）；本地三分支 fixture
  （现态裸名绿 / legacy-tier2 名 DRIFT / 单边 PARTIAL）全过；`git diff --check` 干净；
  `bash -n` 通过；`test_cpa_guardrails_script.py`+`test_maintenance_reliability.py`
  61 passed / 114 subtests passed。全仓无其他断言引用旧标签。

## 4. 观察项（非缺陷，非本事务回归）

- 改名重注册使 7 份 CN 凭据丢失 credits snapshot，插件日志
  "no credits snapshot — arming async, fail-open until then"（issue #70）。同态消息在
  改名前数小时即对 `workbuddy-intl-e99bc89f` 持续出现（6h 内 88 次）为既有常态；
  武装唯一源=面板打开，下次开面板即重武装。fail-open 期间额度耗尽由上游 429/6008
  冷却机制承接（既有 Machinery）。
- 并行会话在同一 worktree 有未提交的 `cpa-admission.service`（MALLOC_ARENA_MAX=2）
  改动，属其写集，本事务未触碰；其doctor 投影哈希锚 HEAD blob 不受影响。

## 5. 过程偏差记录（诚实留痕）

- **首搏命名模板笔误**: `mv` 目标 `${f#workbuddy-tier2-}` 只剥 `workbuddy-tier2-`，
  产出裸 uuid 名而非 `workbuddy-<uuid>.json`——doctor 的 `workbuddy-*.json` glob 将
  失明（假 PARTIAL 绿风险）。15:45:52 同锁内自纠：目标从备份目录文件名精确推导、
  uuid 正则限定、改名前后 sha 复验，7/7 修正。
- **一次 post 计数误报**: 自纠脚本 `shopt -s nullglob` 与 `ls -1` 无参组合把整个
  auth 目录计入 `tier2=20` 误报 REFUSE；经全量目录审计（14 workbuddy + antigravity×2
  + codex×1 + quarantine/logs）证伪为脚本自伤，目录实际为精确目标态。
- 教训: 空洞 glob 展开下 `ls -1` 无参会列全目录；改名类事务的目标名应整名字面构造
  并先断言 glob 可见性，而非仅 sha 对账。

## 6. 回滚

维护锁内 `mv workbuddy-<uuid>.json → workbuddy-tier2-<uuid>.json` ×7（备份目录核对
sha256），doctor 断言按本提交的父版本还原（`git revert` 本提交）。Git 回滚不能替代
远端恢复。
