# BWG CPA 遥测收口与“不再重启”纪律（2026-11-01）

## 范围与授权

用户要求对 BWG CPA 的多 OAuth/API-key 渠道做封号、限流、降智、吐字速度和缓存
审查；随后明确授权“按推荐连续自动修复”；在四轮 admission 重启切断在途
WorkBuddy/DeepSeek 会话后，明确要求**停止部署迭代、不再重启**，收口验收，并
把在途请求拒绝规则补进将来的部署脚本。

本记录不含提示词、回答、凭据、token、完整 capability path 或未脱敏 client key。
没有新增、删除、轮换账号或 key；没有提高并发/重试或清除冷却。

## 已完成的代码与仓内验证

- `scripts/cpa_natural_traffic_audit.py`：修复半开一分钟窗口；将完成时长拆成
  排队、上游首输出、解码三个阶段；增加真实 token/cache/cost 聚合、显式
  `--origin` 的结构化输出分类，以及有界分块 SSH 上传，避免 Windows
  `CreateProcess` 参数上限。旧的“完成时聚集=准入突发”读法不再成立。
- `scripts/remote/cpa-admission.py`：`sse_text_finish` 仅从终态 Responses
  `output` 中是否存在非空 `output_text` 分类 `stop/empty_text/reasoning_only`；
  同时允许数值型 provider-reported `usage_cost_usd`，不记录正文、模型生成 ID、
  工具参数或 URL。该字段是观测，不进入准入判定。
- `scripts/remote/cpa-health.py`：非流式语义探针输出 `output_state`，区分正文、
  空正文、reasoning-only、工具输出，供后续受控质量检查而不把隐藏推理误读为空答。
- `scripts/cpa_admission_risk_audit.py`：新增 `--health-snapshot`，对四条已审查
  lane 的实时 healthz 状态、RPM、hold/cooldown 做离线可复核校验。
- `outputs/deploy-admission-text-finish-20261101.ps1`：删除基于 healthz 空闲快照
  的“drain”路径；正常/强制重启均在 `MUTATED=1` 之前要求
  `/run/cpa-admission/requests.lock` 的排他 `flock`，`-ForceRestart` 明确拒绝。
  这是对四轮误判的机制修正；本轮不再使用该脚本部署。
- focused gate：完整 admission 测试、自然流量审计、health doctor、风险审计和
  updater apply 已通过；在写入文档的最后一轮新增 **107 passed, 50 subtests
  passed**，Ruff、mypy 与 `git diff --check` 通过。完整 suite 尚未作为本轮停止
  重启后的最终门禁运行。

## 线上事实（`host_loaded` / `controlled_live`）

- 最后运行的 admission 源哈希为
  `e06d8bfebd5d078d2d3f7e2e236da2f0bf8e273c75af4b6bbc411dbdfdf48b28`，
  服务 `active`，`ActiveEnterTimestamp=2026-11-01 12:17:41 UTC`；之后**未再
  重启**。仓库后续源码与 pin 只留在本地，不宣称已部署。
- 2026-11-01 11:40Z strict doctor 曾为 `DOCTOR_CONTRACT_OK`；最近一次 post-close
  healthz 快照显示四 lane 同时非 hold，RPM `24/30/18/18`，这是快照时点事实，
  不证明上游账号健康或长期可用。
- 随后另一个并行工作流在 20:27 CST 提交 `4fe1c98`，为 WorkBuddy 调整 CN/INTL
  服务顺序并更新 doctor 契约；该提交不是本轮的线上重启证据。当前运行容器的
  admission 源仍应以上述 `e06d8bf…` 哈希和 ActiveEnterTimestamp 为准；
  新契约只有在用户明确会话空闲后，由请求锁排他事务投影才可 host-loaded。
- 用户报告 19:36/19:45/19:57/20:17 四轮重启切断 **5** 个在途 deepseek 会话。
  此前部署侧另有聚合读数 2/2/6/4，来自不同的在途统计口径；两者不可相加，
  也不用于削弱用户所见。根因是 healthz 的空闲快照不能替代请求锁排他证明。
- 近 1 小时 707 条真实 journal 聚合：ChatGPT Luna 缓存命中 `0.931`、排队 p90
  `4 ms`、首输出 p90 `1.77 s`；WorkBuddy 缓存命中 `0.999`、排队 p90
  `14.9 s`、首输出 p90 `6.74 s`；GLM-Flash 缓存命中 `0.972`、首输出 p90
  `9.22 s`。Windows 本机 SQLite 无新行，`cpa_failure_triage.py` 明确报告
  本机健康未验证，不能据此宣称本机零 429。
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

- 实际线上 admission 没有最新 `usage_cost_usd` 和 `output_state` 代码；未部署，
  不能宣称已 host-loaded。若未来发布，必须等待用户明确报告会话空闲，并在脚本
  取得请求锁排他所有权后执行；`healthz` 只是附加观测，不能单独放行。
- 本次无远端文件/配置变更，无新远端回滚对象。先前四轮已生成的 admission 备份
  保留在 `/opt/cliproxyapi/backups/*-admission-text-finish-20261101`；
  Git 回滚不得代替远端备份恢复。
- 证据边界：`repo_verified` + static admission health snapshot + read-only
  natural traffic/cache observation；不声称长期账号安全、真实账单、自然流量
  全部质量或本机客户端最终健康。
