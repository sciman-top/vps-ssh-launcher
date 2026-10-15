# 2026-10-15 BWG admission 边界修复与验收

## 范围与实现

- 用户授权连续完成审查建议的修复、优化、投影及验收；仅处理 BWG。
- 实现提交 `785c38982b665c095dd94d5c899d32f8d9a571e0`。
- `auto` 及 thinking suffix 在 CPA 调用前拒绝，返回 `400 explicit_model_required`。
  Interactions 的 `models/` 前缀和模型后缀纳入同一 lane；当前公网仍按既有 `/v1` 白名单开放。
- SSE 恢复必须有本协议终态；Chat Completions 必须观察到 `[DONE]`，
  单个 choice 的 finish_reason 不足以证明整条流完成。缺终态、截断或后续失败不能清熔断。
- Antigravity 首次明确容量拒绝即进入退避；HTTP 429/503 头先锁存默认等待，
  Google 结构化等待和 HTTP Retry-After 继续取最长窗口，保留持久风险停流。
- 不增加并发、队列或重试，不轮换凭据，不清冷却，不改模型选择，不操作 ZZ。

## 本地证明

- 独立只读 reviewer 提出的多 choice、Interactions 兼容终态及 Gemini 合法 finishReason
  已对照 CPA v8.0.29 源码核实并处理；不把社区协议猜测直接当作运行事实。
- 最终实现已有完整门禁 exit 0：build、pytest、Bandit、Ruff check/format、mypy。
  pytest `621 passed, 1 skipped, 301 subtests passed`；真实 SSH integration 默认跳过。
- 续接后针对当前同一 admission SHA 再验：`34 passed, 88 deselected`，exit 0。
  命令：`python -m pytest -q --override-ini addopts='' tests/test_cpa_admission.py
  -k 'antigravity or sse_recovery or completed_sse or auto_and_interactions or unframed or terminal or choice'`。

## 远端投影与异常收尾

- 实际备份：`/root/cpa-admission-boundaries-backup-20261015T055146Z`。
  manifest、旧/新两个文件、keep hashes、风险状态观察副本和事务脚本均保留在 root 私有目录。
- 原服务有真实 provider Retry-After，部署先等待退避结束，未靠重启清零。
- 事务在 `2026-10-15T08:12:55Z` 开始（北京时间 16:12:55），08:12:57Z 完成。
  maintenance flock、精确 Nginx UID 新连接 fence、全 lane 无请求/冷却且 socket 排空后，
  仅替换 admission 与 pin并重启 admission。保留风险文件及配置；不恢复整张 iptables 表。
- 回执：`BACKUP_STAGE_AND_GENERATION=PASS`、`ALL_LANES_IDLE_NO_BACKOFF_ZERO_SOCKETS=PASS`、
  `ADMISSION_INTEGRITY_OK`、`LOADED_HEALTH=PASS`、`INSTALLED_AND_KEEP_HASHES=PASS`、
  `TRANSACTION_RESULT=PASS`、`FENCE_CLEANUP_EXIT=0`。
- 两个等待进程先后尝试部署：第一次成功，第二次被 generation guard 拒绝，因此包装服务
  最终 failed，原定 fixture 未运行。续接后已单独补验；未重复部署。
  fresh read 确认等待进程为零、精确 fence 不存在；已清理失败的临时 service 状态，
  其 `LoadState=not-found / ActiveState=inactive / SubState=dead`，保留失败日志。

部署后 SHA-256（LF）：

| 文件 | SHA-256 |
| --- | --- |
| `/opt/cliproxyapi/cpa-admission.py` | `5af934a2e6d3880dd019864d2794d9256e9c75003d553950f26861f2b911884b` |
| `/etc/vps-ssh-launcher/cpa-admission.sha256` | `90525357800d113237b8702ff809545fa96eb7c066a5621a458fa388fd0447e3` |

pin 内容等于 admission SHA。config.yaml、policy、admission.json、provider manifest、unit 的
keep hashes 全部匹配。CPA 当前 v8.0.29，restart=0；本次 admission 事务不改 CPA/Nginx。

## 当前版本模拟与一次真实请求

- 线上同 SHA 源码在 BWG 隔离临时目录使用假上游运行；生产风险文件未改。
  `AUTO_AND_INTERACTIONS_FENCE=PASS`、`TRUNCATED_SSE_DOES_NOT_HEAL=PASS`、
  `FIRST_REFUSAL_AND_RESTART_BACKOFF=PASS`、`CONTROLLED_FIXTURE_RESULT=PASS`，
  `REAL_PROVIDER_CALLS=0`，exit 0。
- 真实受控请求先等 lane 空闲；第一次检查因有在途业务请求而停止，未发送 provider 请求。
  随后只读等待空闲后发出一次请求，无 provider 重试。
- 路由：公网 TLS `fq.sciman.top:8443` → Nginx → admission → CPA；
  精确 `gpt-6.1-sol`，固定短探针，stream=true，reasoning=low，max_output_tokens=1024。
- HTTP 200，2.98 秒；`SSE_TERMINAL_SUCCESS=True`、`SSE_SCAN_COMPLETE=True`、
  `SSE_PROTOCOL_ERROR=False`、`SSE_CAPACITY_SCOPE=None`，关联 admission trace
  `capacity=false successful=true`；`SINGLE_REAL_REQUEST_RESULT=PASS retries=0`，exit 0。
- 部署后真实 journal 的一次有界采样还记录了 Sol 32 次、当前 Luna 3 次、旧 Luna 3 次
  `status=200 capacity=false successful=true`；Sol 另有 1 次 200 但 successful=false，
  说明 HTTP 成功与完成证据已分离。此计数不证明质量或长期可用性。

## Doctor、回滚和验收边界

- fresh strict doctor 的 admission 完整性、加载、权限、policy、模型 lane、路由及 TLS 均通过；
  12 个投影中 11 个 MATCH。整体 `DOCTOR_CONTRACT_FAILED` 的唯一投影失败是
  `auto-update.sh`，归属并行的长期维护任务，本切片不覆盖该脚本。
- 此时 Antigravity 无 hold、无 cooldown、无写失败；正常业务仍有在途请求，未为收口中断。
- 回滚预演：`timeout 30s bash <backup>/transaction.sh --check`；实际回滚为
  `timeout --signal=TERM --kill-after=20s 180s bash <backup>/transaction.sh --rollback`。
  仅在当前 generation 匹配、fence 排空且无活跃风险时执行；风险文件始终不重置。
  旧版本会恢复已修复的四个缺口，回滚后必须按旧 generation 重新核验。
- `repo_verified`、`filesystem_projected`、`host_loaded`、远端模拟和一次受控实战已有证据。
  自然 Desktop Direct OAuth 走官方链路，本轮网关变更不能证明该链路恢复。
  不承诺永久无 capacity/429，也不以此次成功证明不封号或无质量变化。
- Antigravity 官方 FAQ 将第三方访问列为条款风险；保留 OAuth 只是当前用户选择。
  退避、固定出口、限并发及少量请求只能降低放大效应，不提供安全速率或账号保证：
  <https://antigravity.google/docs/faq/>。
