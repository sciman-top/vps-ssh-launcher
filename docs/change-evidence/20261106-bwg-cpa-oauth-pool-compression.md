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
2. **`routing.strategy` `fill-first` → `round-robin`**：让 CPA 原生调度的
   API-key / OAuth 候选按轮转分摊，而不是固定打第一个就绪凭据。session
   affinity 不变，单会话仍粘同一凭据，prompt-cache 局部性不受影响。
   **注意**：WorkBuddy 走插件 scheduler，不消费本项，下文第 3 条更正说明了这点。
3. **`workbuddy-oauth` `max_inflight` 实验 1 → 2，随后回退为 1（本文件同日更正）**：
   提升到 2 的假设是「WorkBuddy 也吃 `routing.strategy` 的轮转」，**该假设错误**。
   WorkBuddy 是插件调度通道，插件两个 picker（`pickPriorityAuth` /
   `pickActiveAuth`）只按候选顺序与耗尽缓存选择，**都不感知 in-flight**；
   `account_max_concurrent=1` 在执行期才检查。于是 lane 给 2 个槽时，两个并发
   请求被派给同一个首个候选，第二个在 ~1ms 内 `credential busy` 失败，而
   `max-retry-credentials=1` 禁止换 sibling → 客户端收到 500。
   受控实测：并发对得到 `1×200 + 1×500`；3h 内 50 次 WorkBuddy 上游失败中
   10 次正是该形状。回退后同一并发对：`200/1.71s + 200/3.13s`（串行排队）
   与 `200/1.62s + 200/3.14s`，两个模型均无双失败。
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
- 同日更正轮：admission 事务 `DEPLOY_OK pin=1703ba4f…`，
  备份 `/opt/cliproxyapi/backups/20261106T160531….Z-admission-workbuddy-inflight-1-revert-20261106`。
- 严格 doctor：`DOCTOR_CONTRACT_OK`，`admission-integrity=OK`、
  `admission-health=OK`，全部 12 条 `projection-drift` 为 `MATCH`。
- 线上复验：`strategy: round-robin`、`upstream-request-compression: True`、
  四条 lane `max_inflight` 为 `2/3/2/1`（WorkBuddy 已回退为 1），
  `config.yaml` 240 行无残行。
- 并发受控验收（回退后）：`deepseek-v4.1-flash-sg` 并发对
  `200/1.71s + 200/3.13s`；`hy4free` `200/1.62s + 200/3.14s`。
  回退前同一形状为 `1×200 + 1×500 credential-busy`。
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
- **插件调度器不感知 in-flight** 是本轮发现的上游设计约束，不是本仓可配置项：
  两个 picker 均只看候选顺序与耗尽缓存。若未来要把 WorkBuddy lane 提到 2，
  前提是插件 picker 能观测忙碌凭据（或上游支持单账号并发 >1 且有证据）。
  在此之前 1 是唯一自洽的下界。
- 同日 16:15 观测到 `deepseek-v4.1-flash` 处于 `model_cooldown`
  （`retry_after≈237s`，源自更早的 6004）。这是既有模型级冷却，与并发回退无关；
  复验改用未冷却的 `-sg` 与 `hy4free`。
- 未处理：Claude lane 空转（用户决策保持现状）。
