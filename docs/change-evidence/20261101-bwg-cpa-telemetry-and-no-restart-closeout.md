# BWG CPA 遥测收口与“不再重启”纪律（2026-11-01）

## 范围与授权

用户要求对 BWG CPA 的多 OAuth/API-key 渠道做封号、限流、降智、吐字速度和缓存
审查；随后明确授权“按推荐连续自动修复”；在四轮 admission 重启切断在途
WorkBuddy/DeepSeek 会话后，明确要求**停止部署迭代、不再重启**，收口验收，并
把在途请求拒绝规则补进将来的部署脚本。

随后用户再次明确授权连续自动修复，并在 2026-11-01 22:17 CST 暂停其它会话；
本次最终投影只在请求锁排他、四条 lane 排空后执行。前述“不再重启”是投影前的
历史边界，不能覆盖这次明确的恢复授权。

本记录不含提示词、回答、凭据、token、完整 capability path 或未脱敏 client key。
没有新增、删除、轮换账号或 key；没有提高并发/重试或清除冷却。

## 已完成的代码与仓内验证

- `scripts/cpa_natural_traffic_audit.py`：修复半开一分钟窗口；将完成时长拆成
  排队、上游首输出、解码三个阶段；增加真实 token/cache/cost 聚合、显式
  `--origin` 的结构化输出分类，以及单通道、有界分块的 SSH 源码传输，避免
  Windows `CreateProcess` 参数上限和跨渠道 temp-file 竞态。旧的“完成时
  聚集=准入突发”读法不再成立。
- `scripts/remote/cpa-admission.py`：`sse_text_finish` 仅从终态 Responses
  `output` 中是否存在非空 `output_text` 分类 `stop/empty_text/reasoning_only`；
  同时允许数值型 provider-reported `usage_cost_usd`，不记录正文、模型生成 ID、
  工具参数或 URL。该字段是观测，不进入准入判定。
- `scripts/remote/cpa-health.py`：非流式语义探针输出 `output_state`，区分正文、
  空正文、reasoning-only、工具输出，供后续受控质量检查而不把隐藏推理误读为空答。
- `scripts/cpa_admission_risk_audit.py`：新增 `--health-snapshot`，对四条已审查
  lane 的实时 healthz 状态、RPM、hold/cooldown 做离线可复核校验。
- `outputs/deploy-admission-text-finish-20261101.ps1`：删除基于 healthz 空闲快照
  的“drain”路径；正常/强制重启均在 `MUTATED=1` 之前先取得
  `/run/cpa-admission/requests.lock` 的排他、非阻塞 `flock`，再在该锁内要求四条
  lane 的 `inflight`/`pending` 都存在且为 `0`。字段缺失、在途请求或 healthz
  失败分别 `REFUSE`/退出 `75`；`-ForceRestart` 也在远端访问前明确拒绝。
  这是对四轮误判的机制修正；最终投影前先通过 `DRY_RUN_IDLE_LOCK_OK` 和
  `DRY_RUN_OK`，随后以 `REQUEST_GUARD_EXCLUSIVE_OK`、`ADMISSION_IDLE_OK`
  和备份回滚事务完成部署。
- focused gate：完整 admission 测试、自然流量审计、health doctor、风险审计和
  updater apply 已通过，目标切片的传输与 deploy 门槛断言均通过。完整 suite
  为 **995 passed, 13 skipped, 479 subtests passed**；Bandit、Ruff、format、
  mypy 均通过。此前一次完整 suite 的 pytest 已通过，但 format 阶段发现并行
  工作流正在改写同一审计文件；该次不作为最终收口凭据，待文件稳定后整轮重跑。
- 部署事务曾在活动流量期间安全返回 `DRY_RUN_BUSY live_traffic_active=true
  no_swap=true`，一次完整 Apply 也因 `admission_drain_timeout` 触发
  `ROLLBACK_VERIFIED`；在用户暂停其它会话、请求锁空闲后，最终 dry-run 返回
  `ADMISSION_INTEGRITY_OK`、`DRY_RUN_IDLE_LOCK_OK`、`DRY_RUN_OK`，才进入真实
  Apply。
- Codex Desktop 于 21:53:46 CST 重启后，磁盘 `config.toml` 仍为
  `codex_local_access` 0/2、`fq_sciman_top` 0/0；15 分钟 WorkBuddy/DeepSeek
  自然流量 72/72 completed、零格式损坏。随后三次带锁 dry-run 全部为
  `DRY_RUN_BUSY live_traffic_active=true no_swap=true`，只读快照也反复看到
  ChatGPT 或 WorkBuddy 在途请求；故未投影、未重启。这里不把“看到一次 0/0”
  当空闲证明。

## 线上事实（`host_loaded` / `controlled_live`）

- **最终投影与验收（2026-11-01 22:20–22:45 CST）**：用户暂停其它会话后，
  请求锁和四条 lane 均排空。admission 事务以 `REQUEST_GUARD_EXCLUSIVE_OK`、
  `ADMISSION_IDLE_OK` 完成，目标 pin 为
  `00490835362119d6ae5a63ef10ae8ec493e8f885173eb6b629d5c261dd4d0d74`，备份落在
  `/opt/cliproxyapi/backups/20261101T142007.630107461Z-admission-text-finish-20261101`。
  随后的完整 guardrail Apply 输出 `GUARDRAILS_APPLIED`、`READY_STATUS=200`，
  备份目录为 `/root/cpa-guardrails-backup-20261101T144307.219098856Z`；
  容器启动时间更新为 `2026-11-01T14:43:10Z`。
- Apply 后 strict doctor 返回 `DOCTOR_CONTRACT_OK`，`admission-integrity=OK`、
  `admission-health=OK`，全部 projection `MATCH`；路由验收仍为
  `workbuddy-realm-order=CN_THEN_INTL`、`deepseek-provider-order=WORKBUDDY_THEN_OPENCODE`、
  OpenCode priority `-1`，未改车道顺序。
- 投影后受控 DeepSeek Responses SSE 单发：HTTP 200、`text/event-stream`、
  `elapsed_s=1.501`、9 个事件、0 个 malformed、终态 `response.completed`、
  `failed=false`；client key 只在远端进程内读取。
- 投影后 1 小时自然流量审计退出码为 `0`，读取 459 条 header log、406 条配对
  请求；WorkBuddy/DeepSeek 为 `requests=269`、`completed=267`、
  `client_disconnect=1`、`upstream_error=1`、`capacity=0`、
  `cache_hit_ratio=0.9977`，审计 `warnings=[]`。该窗口不证明长期账号安全或
  中国网络持续可用；`text_finish_reports=0` 表示本窗口没有可分类的该字段样本。
- 投影前运行的 admission 源哈希为
  `e06d8bfebd5d078d2d3f7e2e236da2f0bf8e273c75af4b6bbc411dbdfdf48b28`，
  服务 `active`，`ActiveEnterTimestamp=2026-11-01 12:17:41 UTC`；该状态已被
  上文最终投影段取代。
- 2026-11-01 11:40Z strict doctor 曾为 `DOCTOR_CONTRACT_OK`；最近一次 post-close
  healthz 快照显示四 lane 同时非 hold，RPM `24/30/18/18`，这是快照时点事实，
  不证明上游账号健康或长期可用。
- 随后另一个并行工作流在 20:27 CST 提交 `4fe1c98`，为 WorkBuddy 调整 CN/INTL
  服务顺序并更新 doctor 契约；该提交不是最终投影的重启证据。最终运行容器已经
  由本次排他事务加载目标 admission 与健康检查源码。
- 用户报告 19:36/19:45/19:57/20:17 四轮重启切断 **5** 个在途 deepseek 会话。
  此前部署侧另有聚合读数 2/2/6/4，来自不同的在途统计口径；两者不可相加，
  也不用于削弱用户所见。根因是 healthz 的空闲快照不能替代请求锁排他证明。
- 投影前只读 doctor（`2026-11-01T13:40:53Z`）曾报告 ChatGPT/WorkBuddy 各
  `write_ahead_inflight=1`，并以旧 pin 差异显示 `admission-integrity=FAIL`；该
  历史状态已由最终排他投影和 post-Apply doctor 取代。
- 投影前近 1 小时 707 条真实 journal 聚合：ChatGPT Luna 缓存命中 `0.931`、排队 p90
  `4 ms`、首输出 p90 `1.77 s`；WorkBuddy 缓存命中 `0.999`、排队 p90
  `14.9 s`、首输出 p90 `6.74 s`；GLM-Flash 缓存命中 `0.972`、首输出 p90
  `9.22 s`。Windows 本机 SQLite 无新行，`cpa_failure_triage.py` 明确报告
  本机健康未验证，不能据此宣称本机零 429。
- 投影前 13:17–13:24Z 的 WorkBuddy/DeepSeek 只读日志中，65 个 completed
  请求中有 63 个 `sse_text_finish=reasoning_only`、2 个 `stop`；这是线上
  旧 admission 已部署字段的结构性观测。它不等于“降智”结论，也不能从自然
  日志区分客户端最终渲染、推理预算和上游生成策略；应作为正文缺失待查信号，
  后续只用用户授权的固定模型/预算受控样本复核。本轮没有据此改并发或重试。
- 受控 GLM Responses 缓存探针：两发同前缀（`session_id` 固定），样本 1 输入
  `617`、缓存 `0`，样本 2 输入 `617`、缓存 `576`，第二发命中 `0.9335`；
  均为 `status=completed`、正文 `OK`。这是**单次样本**，不是长期平均。
- 对 OpenCode 的直连缓存探针在 VPS 侧收到 HTTP `403` 文本 `error code: 1010`
  （WAF/传输拒绝），不是缓存 0 命中；按照风险边界未换 IP、UA 或反复探测。

## 风险结论

- **封号**：账号级风险仍是高；ChatGPT/Google/WorkBuddy 消费级 OAuth 及第三方
  逆向/网关路径不能由本仓保证合规或免封。当前控制是单账号/单凭据有界并发、
  零 HTTP 重试、尊重 `Retry-After`、持久 hold、零敏感探针；这些降低扩散，不是
  封号豁免。Claude OAuth 仍为 0 凭据退役墓碑。
- **限流**：24h ChatGPT 容量事件仍集中于唯一 Plus 凭据；WorkBuddy 为多账号
  池但仍按单 lane 单飞与 per-account 闸保护。缓存命中高不提供额外账号或额度；
  不通过提高 `inflight`/RPM/重试或轮换出口验证边界。
- **降智**：只以固定请求模型的协议终态和真实输出形状观测；名称回显
  `gemini-3.8-flash-n <== gemini-3.8-flash-high` 是已知名称变体，不单独证明降智。
  `reasoning_only` 只表示**没有可见正文**，不证明模型质量。
- **延迟**：WorkBuddy 的主要慢点是单凭据排队；ChatGPT 的尾延迟来自单 Plus 账号
  共享容量；GLM/Antigravity 的慢主要是上游首输出/生成。提高并发可能在容量窗
  放大风险，本轮不调整。
- **缓存**：真实命中率已很高。稳定 session affinity、稳定前缀/工具定义是主杠杆；
  只有可证的同会话前缀漂移或完全缺失 usage 时，才考虑 `prompt_cache_key`
  或官方上游缓存配置，且不能影响无关账号分发。
- **成本**：零或缺失 `usage_cost_usd` 不等于免费容量或无限额度。只有 provider
  明确上报并由受控探针确认的数值，才能纳入成本预算；当前证据不足。

## 当前缺口与回滚

- 最新 admission 与 `cpa-health.py` 已 host-loaded，源码和 pin 均由 doctor
  `projection-drift=... MATCH` 复核；自然流量窗口的 `cost_reports=0` 仍表示
  没有 provider 报告可计费金额，不能据此推断免费或额度。
- 本次远端变更有两级备份：admission 文本事务备份和完整 guardrail 事务备份，均
  已在成功验收后保留。Git 回滚不能代替远端备份恢复；若需回滚，应按对应备份和
  事务脚本恢复，并重新跑 doctor 与受控 SSE。
- 证据边界：本轮已达到 `repo_verified`、`filesystem_projected`、`host_loaded`
  和单发 `controlled_live`；仍不声称长期账号安全、真实账单、中国网络持续可用、
  自然流量全部质量或本机客户端最终健康。
