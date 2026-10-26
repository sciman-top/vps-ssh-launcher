# BWG CPA admission 与 updater 完整性收口（2026-10-26）

## 目标与边界

在保留现有 OAuth 凭据、API-key 槽位和账号选择的前提下，部署已审查的
admission 代际更新与 updater 回执修复。没有读取或修改完整 OAuth auth JSON，
没有清除冷却、提高并发或重试、轮换身份/IP/UA，也没有发送 OAuth 生成探测。

提交基线：`815682a73f088b1ac93b30cb036377e8b74a061a`
（`fix: fail closed on CPA route and receipt drift`），`main` 与 `origin/main` 同步。

## 本地门禁

命令：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1
```

结果：exit 0；`802 passed, 1 skipped, 397 subtests passed`；Bandit、Ruff、格式与
mypy 均通过。唯一跳过项是真实 SSH pytest 集成；BWG 远端 doctor 和受控部署另行执行。

## 远端状态与投影事务

部署前 strict doctor（`2026-10-26T13:49:24Z`）发现三个明确漂移：

- `auto-update.sh`：远端 `65443e71700d1277e45229611ec7b3bf0bfa2c1874be3084e4a776ea7e90fc20`，
  仓库候选 `889f1104e2c034b355c7b32070be43b01fbb012b5a4c5cde7cc369c915a33a1f`。
- `cpa-admission.py`：远端及旧 pin `d5625df7377f14d2de4f533691219f688d98c7a10b28f3e2a9beb08e30d16025`，
  仓库候选 `0b2c88abe82d00a100178635838e82016a4ff2d4f7a2cc5bbab7a4bcdd9b1db2`。
- `/etc/vps-ssh-launcher/cpa-admission.sha256` 与仓库新 pin 不同。

普通 `-Apply` 按设计在写入前拒绝 admission pin 跨代更新；因此改走仓库已有的
reviewed admission transaction，再窄化投影 updater：

1. `outputs/redeploy-admission-disconnect-20261025.ps1 -DryRun -ExpectedLivePin d562…`
   通过：候选语法、旧代码/pin、四条 lane hold 检查均符合预期。
2. 同一受审查驱动部署 admission 源码与 pin，使用共享维护锁和入口维护窗口，
   等待请求排空，备份、原子替换并重启 admission 服务。回执：
   `ADMISSION_IDLE_OK`、`ADMISSION_INTEGRITY_OK`、`DEPLOY_OK`、
   `ADMISSION_WINDOW_PASS`、`INGRESS_RESTORED`。备份：
   `/opt/cliproxyapi/backups/20261026T140307.508379672Z-admission-disconnect/`；
   Nginx 窗口备份：
   `/opt/cliproxyapi/backups/20261026T140304.630155293Z-admission-ingress-window/`。
3. 在同一维护锁下原子投影 updater，`bash -n`、SHA、`700 root:root` 均通过，
   不重启 CPA 或 admission。备份：
   `/opt/cliproxyapi/backups/20261026T140421.066969816Z-auto-update-lane-holds/`。

中途一次 `-Apply` 尝试在 pin 检查阶段 fail-closed 退出，未创建 guardrails 备份、
未改远端文件；随后采用窄事务，因此没有清理原有 guardrails 备份。
按用户确认的 keep-8 策略重新预检 `/root/cpa-guardrails-backup-*`，远端共有 8 份，
均在保留集合内，没有超额候选；无需删除备份，也没有为清理而重启 CPA。

## 部署后验收

再次 strict doctor：exit 0、`DOCTOR_CONTRACT_OK`、`admission-integrity=OK`；
`auto-update.sh`、`cpa-admission.py`、`cpa-admission.sha256` 全部 `MATCH`。
CPA 容器仍运行 v8.0.43 固定 digest，`restart=0`；chatgpt、antigravity、
workbuddy OAuth lane 均 `OPEN`，无 hold。WorkBuddy CN/INTL 模型清单仍 pinned，
`account_max_concurrent=1`。API-key 路由清点为 4 个既有槽位：ai.input.im、OpenCode、
HTTP bridge、Zhipu；没有删除或替换路由。

完成一条受控、非 OAuth API-key 流式请求：通过 `127.0.0.1:8318` 调用
`gpt-6-astra`（ai.input.im），`stream=true`、`max_tokens=4`、单次不重试，
返回 HTTP 200、5 个 SSE data frame、`finish_reason=stop` 和 `[DONE]`。
本次端到端测得 headers/首字节 15.0 秒，首个文本 33.3 秒，总耗时 33.4 秒；
未记录提示词正文、回答正文或任何密钥。该单样本只证明这条 API-key 路由的
一次完整流式调用，不代表其它 API-key、OAuth 或长期延迟表现。

OAuth 验收继续依靠自然流量。本次没有对 GPT、Antigravity 或 WorkBuddy 发起
合成生成请求。

## 已识别的残余风险与证据边界

- doctor 的最近保留日志报告 7 日窗口有 239 次模型替换警告，均为
  `gemini-3.8-flash-high` → `gemini-3.8-flash-n`。doctor 明确标记
  `WARN_SUBSTITUTION_ELEVATED`；日志只覆盖当前容器保留窗口。没有改写别名或
  关闭 OAuth 路由，因为目前没有足够证据证明安全替代映射。
- API-key slot 3 仍使用未加密 HTTP 上游。清单明确固定该既有槽位；没有可验证的
  HTTPS 地址可供迁移，故此风险仍在。
- doctor 的 24 小时聚合显示 22 次 Zhipu 上游 capacity 429、5 次 WorkBuddy 500，
  以及 ChatGPT OAuth 的 incomplete/inspection-limit 结果。它们是容量或协议证据，
  不能单独证明封号、账号资格或某一 WorkBuddy realm 的因果。CN/INTL 在当前
  admission 日志里仍无法分开归因。
- 本地网关可以做排队、并发、回执和协议保护，不能保证供应商不会限流、降级或停用账号。
  Google Antigravity 官方说明明确表示，消费账号通过第三方工具访问违反其条款，
  可能导致暂停或终止；官方建议 Gemini Enterprise 或 AI Studio API key。
  WorkBuddy AUP 将未经书面许可的互操作组件列为受限使用，并禁止违反第三方条款的自动化。
  OpenAI 个人条款禁止分享账号凭据、规避速率限制或绕过保护措施。以上政策风险
  不会因本次代码修复而消失；本次按用户要求保留现有渠道。
- 先前缓存样本 `4096/4114 = 99.56%` 来自 GLM API-key 路由，接近该样本的
  可复用前缀上限；不能外推到 OAuth 或 ai.input.im。OpenAI API 文档建议按相同
  模型和完全一致的稳定前缀衡量 `cached_tokens`；Gemini API 的缓存规则也不证明
  Antigravity OAuth 可使用 Gemini API 缓存。没有基于单样本改动缓存键或请求体。
- 吐字时延与缓存的当前可观测性不足：admission 日志有 `waited_ms`、SSE 字节数与
  协议结果，但没有上游响应头、首个文本或缓存 token 统计。网关已通过 `read1()`
  增量转发 SSE，且 Codex bootstrap buffering 关闭。受控样本 headers/首字节约 15 秒、
  首个文本约 33 秒；但该样本未与 admission 的 `waited_ms` 逐请求关联，不能把总耗时
  归因到排队或上游的具体阶段，也不能推断长期分布。没有证据支持再改本地缓冲、并发
  或请求前缀；后续应结合自然请求分段计时和渠道可提供的 usage/cache 字段再决定调参。

## 回滚

- Admission：使用 admission 备份中的 `cpa-admission.py` 与
  `cpa-admission.sha256`，在共享维护锁下恢复；运行
  `/usr/local/libexec/cpa-admission-integrity-check`，重启 admission，复验
  `/healthz` 与部署前 hold 状态。失败时不得用 Git 回滚替代远端恢复。
- Updater：在共享维护锁下恢复
  `20261026T140421.066969816Z-auto-update-lane-holds/auto-update.sh`，核对旧 SHA、
  `bash -n`、属主与权限；无需重启服务。
- Nginx 维护窗口已经恢复，receipt 为 `INGRESS_RESTORED`。

## 官方依据

- [Google Antigravity FAQ](https://www.antigravity.google/docs/faq)
- [Google Antigravity Terms of Service](https://www.antigravity.google/terms)
- [Tencent WorkBuddy Acceptable Use Policy](https://www.workbuddy.ai/document/acceptable-use-policy)
- [OpenAI Terms of Use](https://openai.com/policies/row-terms-of-use/)
- [OpenAI Sign in with ChatGPT Terms](https://openai.com/policies/sign-in-with-chatgpt-terms/)
- [OpenAI Prompt Caching](https://developers.openai.com/api/docs/guides/prompt-caching)
- [OpenAI Prompt Cache Diagnostics](https://developers.openai.com/api/docs/guides/prompt-caching/diagnostics)
- [Gemini API Context Caching](https://ai.google.dev/gemini-api/docs/caching)
