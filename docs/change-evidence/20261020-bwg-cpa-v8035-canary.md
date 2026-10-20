# BWG CPA v8.0.35 canary 换装台账（2026-10-20）

**性质**: 真实远端写入（CPA 镜像 canary 事务 ×1）· 脱敏留档
**授权**: 用户当次指令"自主在 bwg VPS 上直接更新/升级 CPA 及其插件等，连续执行修复和优化"（2026-10-20，与 10-19 同指令口径）
**关联**: 20261020-sixth-review-closeout.md（claude 车道退役，同日先行）、20261019-bwg-cpa-v8034-canary-workbuddy-0960.md（v8.0.34 基线）、提交 d045748（#6692 规则退役）

## 1. 前态与风险取证

- 生产 v8.0.34@`439eb95f` + workbuddy 插件 `0.9.60-local@3bca8a39`；升级前 strict doctor
  `DOCTOR_CONTRACT_OK`（13:4x，心跳五项 success、TLS 82 天、磁盘 26G 可用）。
- 今日 01:19 Anthropic 暂停 num01pro 账号、用户指令退役 claude-oauth 车道（见
  sixth-review-closeout §claude-oauth 车道退役）；01:12Z 容器重启=凭据移除后重启，
  预检时已归因为干净 stop/start，非本事务相关。
- v8.0.35 风险面（官方 release notes + compare v8.0.34...v8.0.35，六 commits/32 文件）：
  无 OAuth 凭据路径改动。Gemini/Antigravity schema 清洗器剥离 readOnly/writeOnly
  （#6778 关闭，antonigravity lane 翻译面，需探针）；Claude web search 结果缓存接
  Home KV（gate 于 Home mode，本栈未启用）；Codex remote-compaction v2 仅 Kimi/Meta
  Responses 路由（非在役）；v8 SIWC 管理面凭据绑定 + legacy callback/symlink 拒收
  （未使用，auth 目录无 symlink）。
- 源码核对口径：外置仓对象库上限 v8.0.31（`describe --tags v8.0.35` 失败），按
  cpa-ban-throttle runbook 纪律未授权不 fetch，以官方 release notes 与 GitHub
  compare 为替代证据；生产版本以本台账与容器横幅为准。

## 2. 事务

- 脚本 `outputs/deploy-cpa-v8035-canary-20261020.sh`（v8034 模板复刻，digest 双锁
  `439eb95f…`→`114ea95c…`，空闲断言、pre-generation 门、备份回滚、exit 契约不变）。
  远端 `/opt/cliproxyapi/canary-v8035.sh` sha256 `48efe50c…ddba` 与本地逐字节一致。
- 13:52:03Z `ADMISSION_IDLE_OK` → `PRE_GENERATION OK` → 13:52:08Z `RECREATED` →
  13:52:13Z **exit 0**: v8.0.34@`439eb95f` → v8.0.35@`114ea95c`（tag commit
  `ae3ee3b`，BuiltAt 2026-10-19T19:03:14Z），restarts=0。备份
  `/opt/cliproxyapi/backups/20261020T135206.415542391Z-from-v8.0.34-canary-v8.0.35`。
- 容器日志：v8.0.35 横幅；`plugin registered plugin_id=workbuddy version=0.9.60`；
  `login_region=intl applied`；启动后 error/panic 扫描零命中。

## 3. 验收

- generation-all：4/5 通道 200/stop——gpt-6-astra 4.0s、glm-5.3-flash 3.3s、
  gpt-5.6-terra 3.1s、glm-5.3 1.9s（吐字正常）；`gpt-6.1-sol-input` 404
  `optional_route_unavailable` 为 ai.input.im 通道侧已知偶发（先例 ~8h 自愈），
  非回归，判据同 v8.0.34 验收。
- cache-canary 双轮四样本：首样本 0（GLM 上游 10h TTL 过期，文档化良性），随后连续
  三样本 `hit_ratio=0.9956`——与历史基线逐位一致，缓存无损。
- OAuth lane 回环单发 ×2：gemini-3.8-flash-high 200/2.8s/stop（上游回显
  `gemini-3.8-flash-n`，已知观察级 modelVersion 命名差异，按既定口径继续观测）；
  deepseek-v4.1-flash 200/1.3s/stop（workbuddy 车道，selected 模式在役）。
  chatgpt-oauth 按纪律不另加探针（每日 timer 冒烟覆盖；posture
  WAIT_UPSTREAM_WINDOW hold=none）。
- claude-haiku-5-5 单发 400 `unknown provider for model` = 当日 claude 车道退役后
  的正确契约（manifest/admission 已移除该路由），非回归；doctor 复活否证守卫在位。
- 终验 strict doctor（14:08）：`DOCTOR_CONTRACT_OK`；三 lane
  chatgpt=WAIT_UPSTREAM_WINDOW（hold_reason=none）/antigravity=OPEN/workbuddy=OPEN；
  `oauth_claude_files=0`（退役生效）；model-substitution 7d=1（观察级，单对）；
  TLS 82 天；error-dump 权限 OK。
- `gpt-6.1-sol` 暂离 catalog：chatgpt 上游 503 容量窗（24h 内 sol capacity ×8，
  均早于升级）触发凭据冷却的已知放大模式，doctor 容错放行，等待上游容量自愈。

## 4. 插件与优化项核查

- workbuddy 插件：上游最新 release 仍为 v0.8.72（workbuddy VERSION=0.9.60，10-19），
  本地 `0.9.60-local`（selected-account + account-failover + conversation-headers
  三补丁）即最新基座，**无需重建**；PR #43 仍未合并 → conversation-headers 补丁
  保持在役。
- #6692 payload 规则退役：同日并行会话已完成（d045748，上游 v8.0.34 原生路径逐点
  核验 + 复活否证测试）；本次实测生产 config `requests.payload override/filter`
  均为空，退役在产核实。binding/prefill 双探针转守护原生路径（claude 车道退役后
  车道级触发 inert）。
- 缓存/吐字：0.9956 基线无损；generation 时延带 1.9–4.0s 与 10-19 验收同带；
  streaming keepalive 15s 与 pre-FT policy pin 未动，v8.0.35 无相关面变更。

## 5. 遗留与移交

1. 回滚入口：备份目录内 compose.yml + 本地 v8.0.34 镜像（`439eb95f` 在库），
   事务 rollback 路径与 v8034 同型（restore compose → compose up → readiness）。
2. 看护点：sol 容量窗自愈与 catalog 回归（每日 timer 冒烟）；sol-input 404 自愈；
   gemini 命名差异继续观测（勿以改名消警）；`canary-v8035.sh` 与单发探针脚本留作
   事务记录（与 v8033/v8034 先例一致）。
3. updater timer 72h 成熟门在 v8.0.35 在役后收敛为 no-op，无需人工干预。
