# BWG admission 冷却期按需提前探针 — 部署与验收证据

- 日期:2026-09-26(UTC 部署 16:02)
- 提交:`578ea4b`(admission 冷却期按需提前探针);投影由 `scripts/cpa_bwg_guardrails.ps1 -Apply` 执行
- 风险分级:真实远端写入(-Apply + 服务重启),带备份与健康回滚

## 背景与根因

2026-09-26 当日三轮修复(SSE 流式 `dcc42c6`、容量放宽 `dfa6565`、熔断阈值 `4f9e541`)后,desktop 仍偶发
`exceeded retry limit, last status: 429`。只读取证(`cpa_gateway.access.log` + `cpa-admission` journal):

- 残余 429 全部来自 admission 熔断冷却拒绝(`lane_reject reason=cooldown`),非 nginx limit_req(当日
  48 次全为 127.0.0.1 本机巡检,一次性)、非 CPA(容器 26h 零 429)。
- 触发链:上游 0.6s 内并发在途的两个 503(`capacity=true`),第二条携带上游 `Retry-After: 60` →
  `release()` 按设计立即整 lane 冷却 60s;desktop 约 7s 一轮的紧重试全撞冷却窗(每发都被正确告知
  剩余 Retry-After 但客户端不读),重试耗尽即弹窗。
- 实测上游 blip 约 6s 即自愈(15:28:12 两发 503 → 15:28:18 在途请求 200),但冷却只有半开探针
  成功才解锁,黑名单硬等满 60s。当日 responses 路由 200×1245 / 429×41 / 5xx×430,上游全天不稳
  是"有时弹出"的来源。

## 变更内容

- `cpa-admission.json`:新增顶层 `early_probe_interval_seconds: 10`。
- `cpa-admission.py`:冷却期内按需放行单个探针请求(`Lease(reason="early_probe", probe=True)`):
  - 无后台定时器、零空闲流量;仅在真实客户端请求到达时触发;
  - 首个探针等待一个完整间隔(冷却开启时 `next_probe_at = now + interval`),实质尊重上游退避;
  - 探针失败维持熔断(沿用既有 retry_after/streak 延长语义)并顺延下次探针;探针成功立即清零解锁;
  - journal 新增 `lane_probe lane=… model=…` 行,`/healthz` snapshot 新增 `early_probe_in` 字段。
- `cpa_policy.py`:`_admission_config_issues` 对该键做 fail-closed 正数校验(缺失/非正数即 issue)。
- `test_cpa_admission.py`:新增探针状态机全链测试(拒绝窗、探针放行、探针期并发维持拒绝、
  失败保持熔断、成功立即解锁)与配置负例;目录契约测试钉住 `== 10`。

## 验证(本地)

- `pytest test_cpa_admission.py`:17 passed。
- full gates(`scripts/run_gates.ps1`,含并行会话 `35abd6f` 门禁分层版):218 passed, 1 skipped,
  234 subtests passed;bandit/ruff/format/mypy 全绿。

## 部署回执

- 投影 5 文件 `PROJECTION_HASH_VERIFIED`(cpa-admission.py/.json、cpa_policy.py、
  cpa_provider_routes.json、systemd unit),附全量 10 文件 sha256 清单(含 config.yaml、
  nginx conf、fail2ban filter/jail)。
- 重启:`cpa-admission` enable+restart,`READY_STATUS=200`,`GUARDRAILS_APPLIED`;
  备份目录 `/root/cpa-guardrails-backup-20260926T160208.468402004Z`(回滚入口)。
- 严格 doctor(默认只读模式):20 段全输出,无 FAIL/WARN;`==projection-drift==` 绿
  (提交先于 -Apply,远端 == HEAD blob)。

## 部署后验收(生产)

- 服务 `active`(16:02:12 UTC 起),journal 自重启起 ERROR/traceback 计数 0。
- `/healthz`:三 lane 均含新字段 `early_probe_in`,全部 `cooldown_remaining=0`、
  `failure_streak=0`;验收窗口内 chatgpt-oauth `inflight=2` 为真实 desktop 流量且连续 200。
- luna 定向单发探针(本机 loopback → 8318 → CPA → 上游,单请求 max_tokens=16):
  `STATUS=200 elapsed=7.04s finish_reason=stop`。

## 受控实战验收补轮(2026-09-27)

生产无法人为召唤上游 503,故用已部署同版脚本(HEAD blob,投影哈希已核)对合成上游
(127.0.0.1:8317,压缩间隔 2s)完整复演 15:28 故障形状:

- 场景 A(误熔断提前解锁):上游 503+`Retry-After: 60` → 冷却窗内 429(携带 Retry-After 55-60)
  → 一个间隔后探针命中健康上游 → **200 立即解锁** → 后续请求正常放行;journal
  `lane_probe=1`。对照生产行为:解锁时刻从 +60s 提前到 ~+interval。
- 场景 B(真故障保护不削弱):上游连 503 → 探针失败(上游错误原样透传)→ 熔断维持、
  下个间隔再探 → 命中恢复后 200 解锁;`lane_probe=2`。证明探针永不把 lane 放行到仍故障的上游。

`ACCEPTANCE_RESULT=PASS`(双场景 9 步断言全 OK,响应码/Retry-After 透传/解锁时点/journal 计数全符)。

## 判定

- `ACCEPTANCE_RESULT=PASS`(本地 gates + 投影哈希 + doctor + 生产探针全绿)。
- 预期行为变化:上游瞬断类误熔断的用户可见 429 窗从"整段 Retry-After(通常 60s)"缩短为
  最长约一个探针间隔(~10s);真实持续故障下行为与此前一致(探针失败维持熔断)。
