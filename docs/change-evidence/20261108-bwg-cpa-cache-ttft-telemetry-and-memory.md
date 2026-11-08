# 2026-11-08 BWG CPA：缓存/TTFT 遥测、policy 探针按需加载与内存归因

## 授权与触发

- 用户当次要求：全面深度审查 bwg 上 CPA 的多 OAuth（OpenAI/GPT、Antigravity/Gemini、
  WorkBuddy CN+INTL、Claude/Anthropic）与多 API key 渠道的封号/限流/降智风控，
  优化吐字缓慢与缓存命中率，必要时修复本项目代码；可参考官方文档、社区反馈与
  上游最新源码。
- 后续追加：确认 Antigravity/Gemini 与 WorkBuddy 同期也出现大量封号；要求执行
  投影与受控实战验收。

## 只读审查结论（先证据，后改动）

### 运行态基线（`cpa_bwg_guardrails.ps1 -Profile bwg`，24h）

- 三 OAuth 车道 `lane-hold=OPEN`；`oauth_quarantine=none`；`workbuddy-version=0.9.88`。
- 24h 状态：200 1397 / 404 621 / 429 2 / 503 1 / 401 4（401 全部为 loopback 健康探测）。
- `admission_protocol_reasons`：completed 3371 / http_error 1377（其中 1376 为
  passthrough 的 `/v1/files` 404）/ capacity 32 / client_disconnect 38。
- 延迟：`waited_ms` p50 0–2ms、p90 2–18ms；`upstream_header_ms` p50 2.6s/p90 9.1s；
  `first_text_ms` p50 6.5s/p90 20.2s。**网关排队不是首字延迟来源**。

### 缓存命中率（24h，按模型聚合 `usage_cached`/`usage_in`）

| 车道/模型 | 请求 | 命中率 |
|---|---|---|
| `antigravity-oauth/gemini-3.8-flash-high` | 724 | 49.3% |
| `chatgpt-oauth/gpt-6-luna` | 176 | 45.1% |
| `chatgpt-oauth/gpt-6.1-sol` | 130 | 48.3% |
| `chatgpt-oauth/gpt-6.1-terra` | 324 | 49.0% |
| `workbuddy-oauth/deepseek-v4.1-flash` | 215 | 97.6% |
| `workbuddy-oauth/deepseek-v4.1-flash-sg` | 424 | 99.1% |
| `zhipu-coding-plan/glm-5.3-flash` | 448 | 49.4% |

结论：命中率已在合理区间，**剩余杠杆在客户端上下文压缩**，不在网关。

### 内存归因（一次性 pprof，只读诊断）

- 临时在 `observability.pprof` 启用 loopback pprof，`nsenter -t <pid> -n` 读取
  heap；诊断后**立即恢复原配置**，`config.yaml` 复原 SHA-256 前缀
  `360657da02ec98e8` 与基线一致，6060 端口回落到拒绝连接。
- 结果：CPA Go **live heap 139 MB**，累计分配 **67 GB**，热点在 translator 的
  `ojson` 分片重建与插件 CGO 回调（`openai_claude_response.go`）。
- admission 进程：**RSS 724 MiB / HWM 832 MiB、仅 2–7 线程**，而 3h 内只处理
  ~1140 请求 —— 不是流量驱动，而是 190 KB 模块在验证时整份读入并长期驻留。
- 风险形态：容器 `mem_limit=512m` 已用到 84%（432/512），而容器内存记账落在
  dockerd 的服务 cgroup；一次大翻译爆量可触发 cgroup OOM，**连带杀掉持有全部
  lane 持久熔断状态的 admission**。

## 变更（仓库 + 远端）

### 1. admission 缓存/TTFT 遥测（commit `aaa9f6c`）

`scripts/remote/cpa-admission.py`：

- 新增 `ModelTelemetry`：1h 滚动窗口、序列上限 64、仅统计
  `protocol_result=completed` 的生成；`cached_tokens`/`uncached_input_tokens`/
  `cache_hit_ratio`（provider 不回报时为 `null`，**不是 0**）与
  `first_text_ms` 的 p50/p90/max。
- `/healthz` 暴露 `telemetry.models.<alias>`。**纯观测，不参与任何准入决策**。
- 测试：`tests/test_cpa_admission_telemetry.py` 7 例（形状、诚实性、边界）。

### 2. policy 探针按需加载 + 只用标准库

`scripts/remote/cpa_policy.py`：

- 校验在 `RLIMIT_AS`（320 MiB）受限的短命子进程中执行，父进程不驻留大对象；
  输出该次校验的峰值 RSS。
- `cpa_policy.py check <path>`：一次调用完成校验 + `ast` 契约检查，**不依赖 ruff**
  （宿主机无 pip、无 ruff，2026-11-08 实测）。
- 探针规则清单移入 `cpa_provider_routes.json` 的 `probe_lint_rules`（单一真源）。
- doctor 新增 `policy-probe` 段；旧投影无 `check` 动词时报 `SUPERSEDED`，不判 drift。

### 3. 测试夹具退出挂起（同 commit `5565ed2`）

`tests/test_cpa_admission.py`、`tests/test_cpa_glm_error_forwarding.py`：
所有 `serve_forever` 线程改为 daemon，loopback upstream 增加
`daemon_threads=True` 与 `block_on_close=False`。

- 修复前实测：打印 `303 passed` 后进程不退出，需人工 kill（`run_gates.ps1` 因此
  EXIT=1，且此前会话遗留多个 21:38 起的僵尸 pytest）。
- 修复后：307 passed / **104.7s** / exit 0。

### 4. 顺带修正一个时序敏感断言

`test_claude_risk_proxy_*`：`Retry-After` 是整数秒，原比较式带小数窗口，实测
`60 >= 60.0125` 偶发失败。改为容忍 1 秒时钟粒度，仍拒绝真实少报。

### 5. 文档

- `cpa-gateway.md`：实测命中率表、三条读法、v8.0.60 相关修复、pprof 网络命名空间
  注意事项、policy 探针不依赖外部 linter。
- `cpa-ban-throttle-incident-response.md`：2026-11 三线封号窗判读（OpenAI /
  Antigravity / WorkBuddy），社区口径与**本栈实测信号的分离**。
- 新增 `cpa-context-speed-budget.md`：上下文长度 vs 吐字速度实测拆解与已否决方案。
- 凭据数纠正：WorkBuddy 实为 14 份（7 CN / 7 INTL），原文档写 3 份。

## 远端事务回执

### admission 部署（`outputs/deploy-admission-telemetry-20261108.ps1`）

- 先 `-DryRun`：`DRY_RUN_OK baseline=57be26b5… candidate=dd025ad9… idle=true no_swap=true`。
- 实跑：`REQUEST_GUARD_EXCLUSIVE_OK` → `ADMISSION_IDLE_OK` → 原子轮换 source+pin →
  `ADMISSION_INTEGRITY_OK` → `TELEMETRY_CONTRACT_OK` → `ADMISSION_REGRESSION_GUARD_OK`
  （编码体仍 415 且 `upstream_contacted=false`）→
  `DEPLOY_OK pin=dd025ad9… backup=…/20261108T064507…-admission-telemetry-20261108`。
- 只重启 admission（06:45:51Z），**未重启 CPA 容器**。

### guardrail 投影（阻塞 drain 事务）

`-Apply` 在本环境持续有流量时以 `cpa_apply_requires_idle_lanes` 拒绝（非阻塞锁，
按设计不切在途流），因此使用等价的**排他锁 + 等待 drain** 事务：

- 首跑：三文件 `MATCH`，但因当时远端 `cpa_policy.py` 仍是旧版（回滚还原）导致
  `check` 无此动词而失败 → **事务自动回滚**（`ROLLBACK_VERIFIED`），无残留。
- 修正探针为纯标准库后重跑：`PROJECTION_HASH_VERIFIED`、`POLICY_OK`、
  `PROJECTED_CONTRACT_OK`、`PROJECTION_OK`，备份
  `…/20261108T070705…-guardrail-projection-20261108`。

### strict doctor（提交后复核）

`DOCTOR_CONTRACT_OK`，EXIT=0：14 项投影全部 `MATCH`、
`policy-probe=OK peak_rss_bytes=20480`、`semantic-policy=OK`、
三 OAuth 车道 `OPEN`、`pnpm`/nginx/compose/admission 全 OK。

## 验收（受控实战）

- `cpa-health.py readiness` → `HEALTH_OK`（RC 0）。
- `cpa-health.py generation` → `HEALTH_OK`（RC 0）：经 admission 全链路的真实生成。
- `cpa-health.py cache-canary` → sample 1 `hit_ratio=0.0000`、sample 2
  **`hit_ratio=0.9956`**（与既有 GLM 基线逐位一致）。
- 线上遥测实际出数（1h 窗口）：6 个模型有样本，例如
  `deepseek-v4.1-flash` 55 样本 / 命中 0.9983 / `first_text_ms` p50 3723ms；
  `gpt-6.1-terra` 54 样本 / 0.4879 / p50 7083ms。
- 资源：admission RSS **724 MiB → 384 MiB**；容器 **432 MiB → 202 MiB**（39.4%）。

## 分层证据

- `repo_verified`：pytest 1157 passed / 18 skipped（588 subtests）；bandit、ruff、
  ruff-format、mypy 全部干净（见本轮 `run_gates.ps1` 回执）。
- `filesystem_projected`：14 项投影 `MATCH`（HEAD blob 锚定）。
- `host_loaded`：admission pin 轮换 + 服务重启回执；`/healthz` 遥测段真实存在。
- `live_accepted`（受控）：readiness / generation / cache-canary 三项 RC 0 且有数值。

## 回滚

- admission：`cp /opt/cliproxyapi/backups/20261108T064507.224942923Z-admission-telemetry-20261108/cpa-admission.py`
  与同目录 `cpa-admission.sha256` 回原位 → `systemctl restart cpa-admission`。
- guardrail 三文件：从
  `/opt/cliproxyapi/backups/20261108T070705.949239518Z-guardrail-projection-20261108/`
  复制回 `/opt/cliproxyapi/`。
- 仓库：`git revert 5565ed2 aaa9f6c`。
- `scripts/remote/cpa-oom-isolation.conf` **未部署**，只是评审过的候选 drop-in；
  启用需独立窗口（重启 dockerd），回滚为删除该 drop-in 后 `daemon-reload` + 重启 docker。

## 未覆盖边界

- 不证明上游长期不封号；不改变任何账号、凭据、路由或条款面。
- 三线封号窗的社区口径均为转述，非官方公告；本栈仅证明**本窗口内无账号级信号**。
- 自然流量与受控单发不构成速率/额度契约。
- 容器内存记账的根因（dockerd cgroup 汇总）已定位，但**未部署**隔离 drop-in，
  故 OOM 连带风险仍存在，只是 admission 的绝对占用已降一半。
- pprof 为一次性诊断，未常驻；`observability.pprof` 恢复为原值。
