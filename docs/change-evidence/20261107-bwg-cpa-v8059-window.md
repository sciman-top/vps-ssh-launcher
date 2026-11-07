# BWG CPA v8.0.58 → v8.0.59 换芯 + WorkBuddy 0.9.88 收编 + wb 裸 429 修复（2026-11-07）

## 目标与边界

用户指令升级 bwg 的 CPA 至
[v8.0.59](https://github.com/router-for-me/CLIProxyAPI/releases/tag/v8.0.59)
与插件最新版，防范 OpenAI/Antigravity/WorkBuddy(CN+INTL)/Claude 多 OAuth
账号的封号、限流、降智与吐字/缓存回归。本次只变更 bwg。没有新增、删除、
轮换、隔离或导出任何 OAuth/API-key 凭据；凭据文件仅做改名（见第四节），
字节不变。

## 前窗：wb 裸 429 车道锁事故与 admission 修复

换芯探针发现 workbuddy-oauth 于 12:56:24Z 被
`retry_window_review_required` 落锁（request da10ea93…）：单次上游 429
（无业务码、无 Retry-After、capacity=true）落进通用兜底，把整条车道闸死，
真实客户端每 10-20s 被 `operator_review_required` 拒绝。直探 8317 单发
200/PONG 证实为瞬态（与 6008 同族"未知形状→重锁"缺陷的频限变体）。

修复（commit c429272，pin fcfc5e9d→57be26b5）：wb 通用限频族（裸
429/6003 信封、无引用账号码的 rate_limit_error 包装）不再落运营审查锁，
改走既有容量梯子（自愈+逐级封顶，与 6004/14003 同待遇）；账号持久族
（6008 计费、11140/auth/受限）保持专用信号。部署与持扣释放合并为单一
事务（`outputs/deploy-admission-bare429-transient-20261107.ps1`，-DryRun
全绿后 apply）：drain→精确 reason 断言→备份（admission/pin/config/
risk.json）→原子换装+置 null→单次重启。回执 `HOLD_RELEASED`、
`BARE429_CONTRACT_OK`（部署代分类合约）、`LANE_HOLD_DELTA=none`、
`RELEASE_OK`、`DEPLOY_OK pin=57be26b5…`。 rollback 会保守回挂 hold 备份。

测试面：`tests/test_cpa_admission.py` 六处旧钉更新（6003/包装族改瞬态
语义，capacity_scope 模型/车道分级不变）+ 新增 2026-11-07 事故钉
（`test_workbuddy_generic_rate_limit_family_never_latches_operator_hold`、
代理路径 `persists_no_hold`）。聚焦 313+84 绿；全量 GATES_RC=0
（1150 passed, 18 skipped, 587 subtests）。

## 版本审查（v8.0.58..v8.0.59，11 commits，77 files，+5573/−278）

- 无 pluginabi/ABI 变更，在役插件兼容性由隔离加载实测证实。
- 正向：claude 会话中段 system 项保位（prompt cache）、codex ToolSearch
  延迟工具保 cache 前缀、antigravity/gemini 多段 chunk reasoning+text
  保全（防降智）、claude OAuth 授权状态/scopes 对齐、codex refresh
  防重放、management 凭据列表暴露 oauth_provider。
- 本栈无消费面的增量不参与流量；release 无 breaking、GLIBC 2.17 基线
  不变（隔离加载 ldd 仅 libc 实证）。

## 插件：WorkBuddy 0.9.87 → 0.9.88（cpa-multi-plugins v0.8.96，issue #77）

6004 模型级限频窗并入冷却清单/清除动作/面板卡片；`model_ratelimit.go`
注册表仅加 NotedAt 元数据，"never shorten an active window" 语义保留，
scheduler.pick 与 catalog 过滤零改动——纯观察面升级。字节取证：zip SHA
`db2b641e…`、workbuddy.so SHA `6b2b2284…`（8,857,440 字节；本地下载与
bwg 侧 curl 双向一致）、`0.9.88`×3、无 `0.9.87` 残留、
`filterModelRateLimitedModels`/`model_rate_limit`/`cooldowns/clear` 在。

## T1 隔离加载与合成验收

- stage（`outputs/cpa-v8059-stage-isolated-load-20261107.ps1`）：tag 拉取
  回读 RepoDigest=`sha256:40dda818…`（OCI index，非 amd64 叶子——沿用
  v8.0.58 勘误口径，Docker Hub API 独立核对一致）；v8.0.59@c5251b5
  （BuiltAt 2026-11-07T04:45:01Z，binary sha 6891aa84…）加载在役 0.9.87
  `.so`：`ISOLATED_PLUGIN_LOAD=PASS`，护栏项 applied，零 panic/fatal。
- 合成验收（`outputs/cpa-v8059-acceptance-20261107.ps1`）：八场景全过、
  `ACCEPTANCE_RESULT=PASS`、`FIXTURE_CLEANED=YES`。
- 0988 隔离加载（`outputs/cpa-v8059-workbuddy-0988-official-isolated-…`）：
  官方 .so × v8.0.59 宿主 `ISOLATED_OFFICIAL_0988_LOAD=PASS`，
  `version=0.9.88`，零 panic。

## T2 生产事务

1. **宿主换芯**（`outputs/run-cpa-v8059-swap-20261107.ps1`，config 基线
   bccb92e3 当次新鲜探针）：`PRE_HOLDS_CLEAN`（wb 已 OPEN）→ `DRAIN_OK`
   → `DIGEST_READBACK=OK` → `RESTART_APPLIED` →
   `PLUGIN_REGISTERED version=0.9.87` → `CLIProxyAPI Version: v8.0.59` →
   `START_LOG_CLEAN` → `PLUGIN_AND_CONFIG_UNCHANGED` → `READINESS_OK` →
   `AUTH_BYTES_UNCHANGED` → `LANE_HOLD_DELTA=none` → `restarts=0` →
   `SWAP_OK v8.0.58->v8.0.59`，事务内部零回滚。
2. **插件换装**（`outputs/deploy-workbuddy-0988-official-20261107.ps1`，
   先 -DryRun staging 全绿）：入口 503 门→drain→备份→原子换装→单次
   restart。回执 `PLUGIN_REGISTERED version=0.9.88 sha=6b2b2284…`、
   `READINESS_OK`、`LANE_HOLD_DELTA=none`、`AUTH_BYTES_UNCHANGED`、
   `REFUSE_PLUGIN_ONLY_VIOLATION`（config/compose 未动）、
   `MAINTENANCE_GATE_OFF`、`restarts=0`、`DEPLOY_OK`。
3. **guardrails 投影**（`scripts/cpa_bwg_guardrails.ps1 -Apply`，doctor
   版本门禁钉随 commit 4176af2 同步 0.9.87→0.9.88）：`GUARDRAILS_APPLIED`，
   13 项投影 MATCH。

## 窗内发现与处置：wb 凭据 legacy 裸名破坏 INTL→CN 顺序

doctor 首跑 `workbuddy-realm-order=DRIFT`（`min(cn)>max(intl)` 不成立）：
3 个 CN 凭据以 legacy 裸名 `workbuddy-<uuid>.json` 存在（面板导入默认
命名；tier2 前缀是历窗手工顺序杠杆），字典序排在全部 `workbuddy-intl-*`
之前，破坏 2026-11-02 用户钦定的 INTL 日额先耗、CN 积分溢出在后的顺序。
与本窗两次事务无关（均 `AUTH_BYTES_UNCHANGED`）。

处置（`outputs/rename-workbuddy-legacy-cn-tier2-20261107.ps1`）：改名前
逐文件断言 `auth.domain == www.codebuddy.cn`（Global 账号误名会被
fail-closed 拒绝），仅 mv 改名保字节，drain+idle 下执行。首次 apply 因
plan.txt 单列 bug fail-closed 回滚（`ROLLBACK_OK`，零改名），修复后
`ORDER_RESTORED cn=7 intl=7 INTL_THEN_CN`、`RENAME_OK`。冗余首试备份目录
已删，成功备份保留（`…142540…-workbuddy-tier2-rename`）。

## 升级后验收

- strict doctor（投影后重跑）：`DOCTOR_CONTRACT_OK`；
  `workbuddy-version=0.9.88`；三 OAuth 车道 `OPEN hold_reason=none`；
  `workbuddy-realm-order=INTL_THEN_CN`；`MODEL_IDS_UNKNOWN=none`；
  `MODEL_CONTEXT_DRIFT=none`；`model_substitution=OK`（7d 零告警）；
  `gateway_tls_cert_days_left=64`。
- `proxy_hardening_drift.ps1 -Profile bwg`：`HARDENING_DRIFT=NONE`。
- 车道单发（`outputs/run-lane-singles-v8059-20261107.ps1`，经 8318 生产
  链）：首轮 ag/zhipu 200/completed，wb 撞上 14:27:56Z 上游 429（当日
  第三次上游抖动；**新 admission 未落车道锁**，容量梯子接管，零
  `operator_review_required`/`lane_hold_latched`）；冷却后补跑全脚本
  三车道 200/terminal/PONG，正式回执
  `LANE_SINGLES_V8059_ACCEPTANCE=PASS`——"释放后 hold+新
  admission+v8.0.59+0.9.88"全链实证。
- 自然流量审计（`cpa_natural_traffic_audit.py --ssh-profile bwg --hours 1`，
  54 records）：`chatgpt-oauth/gpt-6.1-terra` 41 natural、40 completed、
  cache_hit_ratio 0.97（0.97-0.99 基线带内）、sse_bytes/s p50≈19.5KB/s、
  output_tokens/s p50=59.4 p90=159.5、queue p50=2ms、零客户端断连——
  缓存命中与吐字无回归；ag 的 tiny_sse WARN 为本窗自有探针，非自然流量。
- 换芯后容器日志：0 panic、0 error、0 "superseded" warn。
- **config.yaml 字节漂移核实**：swap 钉定 bccb92e3 → doctor 时
  8c02f6e3，键路径结构 diff（swap 备份 vs 现网）零增删键、零值变化——
  纯格式化重写，语义等价，"面板重序列化丢手工键"陷阱排除。

## 回滚

- admission：从 `…141143…-admission-bare429-transient-20261107` 恢复
  admission.py/pin/config（回滚路径内置，含保守回挂 hold）。
- CPA：同维护锁/drain 内从 `…141219…-from-v8.0.58-to-v8.0.59` 恢复
  compose 镜像行（v8.0.58@060f3522 本地镜像在）并
  `docker compose up -d --pull never`。
- 插件：从 `…141354…-plugin-0988-official` 恢复 workbuddy.so 并 restart
  （事务内置 rollback，验收失败自动执行）。
- wb 凭据名：从 `…142540…-workbuddy-tier2-rename` 按计划反向 mv。
- Git 回滚不能替代远端恢复；OAuth token 运行期自然刷新，不回放凭据
  快照。

## 证据边界

已达 `repo_verified`、`filesystem_projected`、`host_loaded`、
`controlled_simulation`（ACCEPTANCE_RESULT=PASS）与换芯后短窗自然
`live_observed`。不声称长期封号风险为零、模型未降智、wb 上游已恢复
稳定（当日三次 429/500 抖动均为瞬态、未再落锁）、或流式质量长窗已
建立。wb token 轮换仍须用户本人 re-login；claude lane 开关权属用户
（`claude-risk.json` 的 authentication_required 为 2026-10-19 惰性
残留，claude 不在 admission 车道面，未处置）。每日 updater 在役后回到
无候选路径；下次 catalog 变更须同步 `cpa-acceptance.py` fixture。
