# 2026-11-06 bwg CPA 深度审查：OAuth 池分散、请求压缩、配置残行

## 范围

- 唯一生产写入：bwg CPA guardrail `-Apply` + admission 事务
  `outputs/deploy-admission-workbuddy-inflight-2-20261106.ps1`；未操作 `zz`。
- 未修改或轮换任何账号、OAuth token、API key、订阅地址、IP 或 UA。
- CPA 镜像版本不变（`v8.0.55`）；未重启 `cli-proxy-api`。
- Claude/Anthropic OAuth lane 按用户决策保持现状（0 凭据、空转），未新增未删除。

## 变更前的实测基线

- `/v1/responses` 2419 次 200：TTFB p50 30ms、p99 6.87s；`request_time − TTFB`
  平均 9.63s、最大 75s 是生成期静默空档。
- `/v1/chat/completions` 129 次 200：TTFB p50 3.25s、p90 12.7s。
- 缓存：`cache-canary` 对 `glm-5.3` 与 `glm-5.3-flash` 第二次采样
  `hit_ratio=0.9956`；缓存命中本身不是慢的根因。
- 保留的 Codex/Responses 请求体 1.09–2.78 MB。
- WorkBuddy lane 一分钟 `rpm_window_used=15/18`，同时 `max_inflight=1`。

## 变更与判据

1. **配置残行**：`config.yaml` 末尾有截断的外来写入
   （`scheduler_priority_reclaim: true` 后跟半行 `ity_reclaim: true`）。
   yaml 把它折叠进标量，文件仍可解析、所有策略检查都通过。已删除残行；
   `-Apply` 现在在解析前拒绝该形状（重复键后缀 + 零缩进），因为
   `safe_dump` 是唯一允许的写入方，不可能产生它。
2. **`routing.strategy` `fill-first` → `round-robin`**：WorkBuddy lane 不是单账号，
   `auth/` 有 8 个 consumer OAuth 凭据，`fill-first` 把每一轮压在第一个就绪凭据上。
   session affinity 不变，单会话仍粘同一凭据，prompt-cache 局部性不受影响。
3. **`workbuddy-oauth` `max_inflight` 1 → 2**：单飞把 8 个账号串在一个槽后；
   2 与 ChatGPT/Antigravity/Claude 共享订阅 lane 已复核的口径一致。
4. **`upstream.codex.upstream-request-compression` 开启**（1024 字节下限）：
   直击首字节前数 MB 的上传耗时；下限让健康/探针走明文路径。
5. **`cache-canary` 增加 `responses` 协议**：覆盖更繁忙的 Codex 路径，
   从 `usage.input_tokens_details.cached_tokens` 读取缓存命中。

admission 源码、配置与完整性 pin 必须同批次落地：运行时在二者不一致时拒绝启动，
而 `-Apply` 按设计拒绝 pin 轮换，所以走专用事务（drain 请求锁 → 备份 →
三者一起换 → 只重启 admission → 复验 6004 有界 park 未被削弱）。

## 证据

- 本地全量门禁：`1124 passed, 18 skipped, 584 subtests passed`；
  bandit、`ruff check`、`ruff format --check`、mypy 全部通过。
- 投影事务：`GUARDRAILS_APPLIED`；admission 事务 `DEPLOY_OK`，
  `pin=e7a4c6cc…`，备份 `/opt/cliproxyapi/backups/20261106T144849….Z-admission-workbuddy-inflight-2-20261106`。
- 严格 doctor：`DOCTOR_CONTRACT_OK`，`admission-integrity=OK`、
  `admission-health=OK`，全部 12 条 `projection-drift` 为 `MATCH`。
- 线上复验：`strategy: round-robin`、`upstream-request-compression: True`、
  四条 lane `max_inflight` 为 `2/3/2/2`，`config.yaml` 240 行无残行。
- `cache-canary` 双协议：`HEALTH_OK`，第二次采样 `hit_ratio` 0.9956（chat）
  与 0.9959（responses）。
- 延迟（nginx 只读采样，200 响应）：部署前 `n=3511` p50 9.37s / p90 31.46s；
  部署后 `n=136` p50 4.31s / p90 14.71s。
- WorkBuddy lane 复验：`waited_ms=1–6`，20 分钟内零排队拒绝；
  请求体缓存命中达 `usage_cached=98304` 对 `usage_in=267`。

## 备份与回滚

- admission：`/opt/cliproxyapi/backups/20261106T144849.605068006Z-admission-workbuddy-inflight-2-20261106`
  （含 `cpa-admission.py`、`cpa-admission.json`、`cpa-admission.sha256`），
  事务失败会自动回滚三者并复验健康。
- guardrail：`-Apply` 自带逐文件备份；恢复按
  `docs/runbooks/cpa-manual-rollback.md`，不接受仅以 Git revert 代替远端恢复。
- 配置残行修复前的原文件：`/opt/cliproxyapi/config.yaml.bak-20261106-pretruncfix`。

## 边界与残留

- 23:04–23:07 观测到 OpenAI 侧真实容量事件
  （`server_is_overloaded`，`retry-after` 316–321s）。同窗口该 lane 历史为
  475 成功 / 74 失败，admission `cooldown_active=false`，与本次改动无关。
- 同期两个既有渠道故障与本次改动无关：opencode `403 code 1010
  insufficient_quota`、input.codes `502`。
- 模型名变体替换告警 `model_substitution=WARN_SUBSTITUTION_ELEVATED`
  （`gemini-3.8-flash-n <== gemini-3.8-flash-high` 862 次）属既有已知现象。
- 延迟样本 n=136 只覆盖部署后约 13 分钟，不能替代长期稳定性验收；
  自然流量与缓存命中趋势仍需后续观察。
- 未处理：Claude lane 空转（用户决策保持现状）。
