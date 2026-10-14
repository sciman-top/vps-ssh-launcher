# BWG Antigravity sensitive-words 修复（desktop gemini 429 闭环）

## 依据

- 2026-10-15 凌晨：desktop codex 经 fq.sciman.top 切 `gemini-3.8-flash-high`
  必 429 `RESOURCE_EXHAUSTED`（新会话/旧会话同样死）；IDE 配额面板
  Gemini 每周 100%/5h 99% 剩余。
- 上游定论 router-for-me/CLIProxyAPI#5848（collaborator 确认）：Google
  antigravity 上游对**系统指令文本做内容过滤**，Codex 系提示词中的特定
  词/词组被伪装成 429 拒绝；官方支持 `antigravity.sensitive-words`
  零宽混淆，且明示"不能当凭据配额耗尽证据"。

## 定位（实弹二分，全部经 8318 responses wire）

- 触发最小对：`"You are Codex, a coding agent based on GPT-5."` → 429
  （0.2-0.5s 上游快拒）；同句式 Codex→Astra 或 GPT-5→GLM-5 或任一词
  单独出现 → 200。即 **OpenAI 客户端指纹（Codex+GPT 共现）触发**。
- 排除项（全部实测 200）：responses 流式线别、15 个真实工具、40KB 工具
  描述、全顶层字段、真实小 developer 条目、常规英文词
  （api/proxy/quota/ban/credential/bypass/ssh）合成句。
- 曾经的错误归因（勿再引用）：Google 配额耗尽、会话历史累积、CPA
  翻译 bug 空 body、输入 token 阈值——前三轮被面板/新会话/对照推翻，
  第四轮"阈值"是内容混杂的伪相关（合成文本不含指纹故全过）。

## 修复

- 仓库（2171b5e）：`cpa_policy.py` 钉
  `oauth.providers.antigravity.sensitive-words=["gpt","openai"]`
  （matcher 大小写不敏感，"gpt" 覆盖 GPT-*/ChatGPT 变体）；两处夹具
  与负向测试同步；full gates PASS（build/test/bandit/ruff/format/mypy）。
- 远端事务：备份 `/root/cpa-config-backup-sensitive-words-20261015.yaml`
  → 精确插行 → yaml+policy 双验 → 原子写 0600 → 静默重启 → healthz OK；
  随后 `-Apply` 投影新 policy（`GUARDRAILS_APPLIED`）。

## 验收

- 修复后重放：致命句 → **200/4.73s**；真实 Codex 提示词 9877 字符段 →
  **200/2.88s**。
- 用户 desktop 真机恢复可用（natural 验收，用户 2026-10-15 确认）。
- 严格 doctor：projection-drift 11 项 MATCH（含新 cpa_policy.py）、
  `POLICY_OK`、`semantic-policy=OK`、`==antigravity-lane== OK`、
  `DOCTOR_CONTRACT_OK`。

## 回滚与看护

- 回滚：恢复上述备份 + `docker restart`，或仓库 revert 2171b5e 后重投影。
- 看护：若未来再现"desktop 429 但小请求过"，先按指纹词假设重放二分
  （本地熔断 429=0.00s 快失败勿误读为上游结果；连发触发冷却阶梯后读
  8318/healthz 判 lane 状态）；新增触发词需改钉值列表并过全套门禁。
  诊断纪律：单变量一发一窗，burst 会把 lane 打进冷却（本晚实证）。

## 修复归属核对与配置生成收口

- 归属依据：只读核对本机 ZCode `v2/tasks-index.sqlite` 的任务标题及
  `cli/rollout/model-io-sess_*.jsonl` 的实际工具调用；Git 共用身份本身
  不能区分执行会话。以下时间均为北京时间 2026-10-15。
- ZCode **ChatGPT Desktop调用CPA API触发Gemini 429限流**
  （`sess_2ba4c892-c4c2-4d09-8965-d4edd67eea40`，GLM-5.3-Flash）：
  首次加入五词混淆配置并重启 CPA；01:46 提交 `c6ef2c1`，01:52
  提交判决实验台账 `6cde0fb`。
- ZCode **CPA代理登录Gemini OAuth风控防范**
  （`sess_d70261ce-3776-47de-875e-e37d05fba4f1`，GLM-5.3）：
  01:51 提交正式两词 policy `2171b5e`，随后执行 canonical 配置备份、
  修复和投影；01:57 提交本台账 `02ad576`。两个会话均参与修复，
  不把目标客户端恢复归为查证归属的 Codex 会话。
- Codex 收口切片：`cpa-guardrail-apply.sh` 原来没有生成两词配置，
  从缺字段的旧配置执行 apply 会被新 policy 拒绝。现补齐 canonical
  `oauth.providers.antigravity.sensitive-words=["gpt","openai"]`，
  对非 mapping 输入在候选写入前拒绝，并保留其他 provider 与字段。
  回归执行真实模板片段，覆盖缺字段、旧值、字段保留和非法结构。
- 02:02 本次严格只读 doctor 退出 0，`POLICY_OK`、
  `antigravity_lane=OK`、`DOCTOR_CONTRACT_OK`。生产已由上述会话加载
  正式 policy，本收口切片无需重新 apply 或重启生产；此前用户确认的
  ChatGPT desktop FQ direct Gemini 自然验收继续有效。
- 仓库验证：全套 build、pytest（577 passed、1 skipped、286 subtests
  passed）、bandit 和 ruff check 通过；format 首轮发现本次测试排版
  差异，格式化后重验通过，并续跑完整 mypy（62 source files，无问题）。
  新回归格式化后重验 1 passed、6 subtests passed，`git diff --check`
  通过。真实 SSH integration 按默认规则跳过；上述生产检查为只读 doctor。
