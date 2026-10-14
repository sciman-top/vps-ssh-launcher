# 2026-10-15 bwg antigravity lane 假 429（系统指令指纹）与 sensitive-words 修复

> 唯一台账：合并同夜双窗口两份记录（原 sensitive-words-fix 台账已并入本文件
> 后删除）；证据边界口径以 0704299 校正版为准。

## 现象

- desktop（codex responses 协议）切 `gemini-3.8-flash-high` 后持续
  `exceeded retry limit, last status: 429`：北京 00:48 首发，01:15、01:22
  并发簇，01:35–01:40 演变为**连 2.1–2.6KB 小请求也被上游 300–800ms 打回**；
  01:37:12 起 CPA 凭据进入 `reason=quota` 冷却，后续请求本地 3ms 快败。
- 上游 429 响应体恒为裸 `RESOURCE_EXHAUSTED`（"Resource has been exhausted
  (e.g. check quota)."），无 RetryInfo/reset deadline。缺少这些字段本身
  不能排除真实配额或容量问题；本次分类依赖提示词单变量对照与混淆后恢复。
- IDE 配额面板 Gemini 每周 100%/5h 99% 剩余（与 429 并存，不作裁决依据）。

## 根因与定位

- 上游诊断依据 router-for-me/CLIProxyAPI#5848（collaborator 回复）：Google
  antigravity 上游对**系统指令文本做内容过滤**，Codex 系提示词中的特定
  词/词组被伪装成 429 拒绝；官方支持 `antigravity.sensitive-words`
  零宽混淆，且明示"不能当凭据配额耗尽证据"。此为 CPA 项目维护者解释，
  结合本机单变量对照支持本次操作根因；不能表述为 Google 官方对内部
  WAF 实现的确认。
- 实弹二分（全部经 8318 responses wire）：触发最小对 =
  `"You are Codex, a coding agent based on GPT-5."` → 429（0.2-0.5s 上游
  快拒）；同句式 Codex→Astra 或 GPT-5→GLM-5 或任一词单独出现 → 200。
  即 **OpenAI 客户端指纹（Codex+GPT 共现）触发**。排除项（全部实测
  200）：responses 流式线别、15 个真实工具、40KB 工具描述、全顶层字段、
  真实小 developer 条目、常规英文词
  （api/proxy/quota/ban/credential/bypass/ssh）合成句。
- 转储实证：5 份 429 请求的 `input[0]` 均为 developer 消息
  `"You are Codex, a coding agent based on GPT-5. ..."`；同一时刻 A/B
  单发——无 codex 文本探针 200、带该指纹句探针 429。转储 `zw=0` 证明
  v8.0.29 默认未启用零宽混淆，字面量原样送达过滤器。
- 社区佐证：#5751（closed completed，多人复现）；PR #6280/#5790/#5827
  仍 open、未合并，其默认混淆或告警不构成生产 v8.0.29 已有功能。官方
  配额页（docs.cloud.google.com/gemini/docs/quotas）只覆盖企业 Code
  Assist，消费级 AI Pro antigravity 限额无官方数字。

## 归因复盘（勿再引用）

- 已推翻：Google 配额耗尽（裸 429 无 reset 字段 + 面板非裁决依据）、
  会话历史累积/开新会话（新会话同死）、CPA 翻译 bug 空 body（转储 API
  REQUEST 段 `Body:<empty>` 是显示约定非实证，S1/T7 全过否决）、输入
  token 阈值 ~1-2 万（**内容混杂的伪相关**：合成对照文本不含指纹故全过；
  含指纹的 49K-token 请求在混淆后 200）。
- 本窗口中途另有两轮误判：上游瞬时 429（被 01:15 起持续复现推翻）、
  并发/速率窗口（被 01:35 起串行小请求连续 429 推翻；admission
  `inflight=2` 维持未动）。
- 01:21–01:31（北京）同形状批量 200 的窗口存在间歇性差异，其内部原因
  未证实，不推断上游评分或累积触发规则。若复发：先核对实际
  systemInstruction 中目标词是否已混淆，再结合上游错误、冷却状态和
  单变量对照分类；不能仅凭零宽字符数或 "Resets in" 字段作唯一裁决。

## 修复（应急止血 → canonical 正式化）

第一次变更（历史记录，已被正式两词配置替代；01:44，假 429 台账窗口）：

- 备份 `/root/config.yaml.backup-20261014T174459Z`（sha256 07809c24，
  与变更前一致）；追加顶层 `antigravity:` 五词（"You are Codex" 等，
  文件此前无该顶层键）；`docker restart` 后 client load 正常。
  该顶层五词配置已由 01:56 canonical 重写移除，不能作为当前 apply
  或回滚步骤。

正式修复（canonical 两词；01:56；仓库 2171b5e + 16ac338）：

- `cpa_policy.py` 钉
  `oauth.providers.antigravity.sensitive-words=["gpt","openai"]`
  （matcher 大小写不敏感，"gpt" 覆盖 GPT-*/ChatGPT 变体）；夹具与
  负向测试同步；apply 补齐 canonical 生成（保留其他 provider 字段、
  对非 mapping 输入在候选写入前拒绝）；full gates PASS
  （build/test/bandit/ruff/format/mypy；577 passed、1 skipped、
  286 subtests；mypy 62 source files）。
- 远端事务：备份 `/root/cpa-config-backup-sensitive-words-20261015.yaml`
  → 精确插行 → yaml+policy 双验 → 原子写 0600 → 静默重启（01:56:47）
  → healthz OK → `-Apply` 投影新 policy（`GUARDRAILS_APPLIED`）。
- 源码核对（官方 v8.0.29 临时源码；外置参考仓仍为只读 v8.0.13）：
  canonical 两词配置作用于转换后的系统指令文本，匹配大小写不敏感，
  并在首字符后插入 U+200B。

## 验收

- 修复后重放：致命句 → **200/4.73s**；真实 Codex 提示词 9877 字符段 →
  **200/2.88s**。
- 本窗口复核：desktop 形状 300,805 字节（developer 指令 165,684 字符
  含指纹句 + 15 工具，≈49,245 input tokens）→ 200/completed，证伪
  "-high 档 1-2 万 token 阈值、desktop 结构性不可用"；其与修复前
  125KB 真实请求 429 的大小与内容均不同，不能称为唯一变量 A/B——
  支持排除硬阈值，不单独定根因；根因依据为词项替换对照、混淆后重放
  与自然验收。小体积探针（~600B）存在尺寸混淆（阈值假设下也会 200）。
- 自然流量：修复窗口后网关 route=responses 连续 20/20 全 200（含
  729KB/53.7s 长流）、上游 429 计数 0、无新错误转储；用户 desktop
  真机恢复可用（natural 验收，2026-10-15 确认）。
- 严格 doctor：projection-drift 11 项 MATCH（含新 cpa_policy.py）、
  `POLICY_OK`、`semantic-policy=OK`、`==antigravity-lane== OK`、
  `DOCTOR_CONTRACT_OK`。生产 fresh read：两词 canonical 配置有效、
  顶层旧键不存在，policy 与本仓 LF 源 SHA256 一致
  （`62066a6a…61568`），policy exit 0；`request-retry=0`、
  `max-retry-credentials=1`，冷却开启。
- 边界：日志中两次受控 200 出现在 01:52（最后一次 apply 容器启动
  01:56:47 之前），不作为持续成功证明；持续成功以自然验收与后续流量
  为准。零宽注入只作用转换后的系统指令文本，保留指令结构但改变字节
  与分词；当前恢复验收不证明所有任务的模型行为完全等价。

## 回滚与看护

- 历史备份（第一次变更时点，仅当需要回到 01:44 前无混淆基线）：
  `/root/config.yaml.backup-20261014T174459Z`（注意会同时回到该时点的
  其他配置状态）。
- 生产配置回滚：恢复 `/root/cpa-config-backup-sensitive-words-20261015.yaml`
  并重启 CPA，会回到该备份时点的配置。若要撤销正式两词兼容措施，
  还须同步回退 `2171b5e` 的 policy 切片和 `16ac338` 的 apply 生成切片；
  只 revert 前者再 apply 仍会写回两词，只恢复远端配置则可能被当前
  policy 拒绝。Git 回滚不能代替远端恢复，此处记录回滚依赖，本次未
  执行回滚。
- 看护：若未来再现"desktop 429 但小请求过"，先按指纹词假设重放二分
  （本地熔断 429=0.00s 快失败勿误读为上游结果；连发触发冷却阶梯后读
  8318/healthz 判 lane 状态）；新增触发词需改钉值列表并过全套门禁。
  凭据冷却（`reason=quota`）为 CPA 保护行为，勿禁用。诊断纪律：
  单变量一发一窗，burst 会把 lane 打进冷却（本晚实证）。

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
  修复和投影；01:57 提交原 sensitive-words-fix 台账 `02ad576`。
  两个会话均参与修复，不把目标客户端恢复归为查证归属的 Codex 会话。
- Codex 收口切片：`cpa-guardrail-apply.sh` 原来没有生成两词配置，
  从缺字段的旧配置执行 apply 会被新 policy 拒绝。现补齐 canonical
  `oauth.providers.antigravity.sensitive-words=["gpt","openai"]`，
  对非 mapping 输入在候选写入前拒绝，并保留其他 provider 与字段。
  回归执行真实模板片段，覆盖缺字段、旧值、字段保留和非法结构
  （16ac338）。
- 02:02 严格只读 doctor 退出 0，`POLICY_OK`、`antigravity_lane=OK`、
  `DOCTOR_CONTRACT_OK`。生产已加载正式 policy，该收口切片无需重新
  apply 或重启生产；用户确认的 ChatGPT desktop FQ direct Gemini
  自然验收继续有效。
- 仓库验证：全套 build、pytest（577 passed、1 skipped、286 subtests
  passed）、bandit 和 ruff check 通过；format 首轮发现测试排版差异，
  格式化后重验通过，并续跑完整 mypy（62 source files，无问题）。新
  回归格式化后重验 1 passed、6 subtests passed，`git diff --check`
  通过。真实 SSH integration 按默认规则跳过；生产检查为只读 doctor。

## 本次深审复核边界

- 在线重新核对 #5848 与 collaborator 回复：现有 `sensitive-words`
  是项目支持的兼容措施；#6280、#5790、#5827 仍 open、未合并，不能
  把这些 PR 的默认混淆或告警当作生产 v8.0.29 已有功能。若上游未来
  合并 #6280，应核对新版本的实际混淆行为与配置兼容性后再决定是否
  保留本词表。
- 外置参考仓仍为只读 v8.0.13；当前实现以官方 v8.0.29 临时源码为准。
  核对 v8 path 映射、Responses instructions/developer 转换、流式与
  非流式执行器及 matcher，确认 canonical 两词配置作用于系统指令文本，
  匹配大小写不敏感，并在首字符后插入 U+200B。
- 生产 fresh read：两词 canonical 配置有效，顶层旧键不存在，
  policy 与本仓 LF 源 SHA256 均为
  `62066a6ad1c24ba4c46c71da84028fd45f21586d120802569b406b9361b61568`；
  policy exit 0。`request-retry=0`、`max-retry-credentials=1`，冷却开启。
- 自然客户端验收来自用户先前确认；日志中的两次受控 200 出现在
  01:52，最后一次 apply 的容器启动为 01:56:47。本次查询最后启动后
  未匹配到 Gemini upstream_result，不把空样本当作持续成功证明。
- 历史台账已校正不同尺寸请求的 A/B 口径、缺 reset 字段的推断、
  未证实的 WAF 评分解释和"语义无损"保证。功能链审查未发现本次
  兼容修复的新增阻断缺陷；本结论不覆盖所有未来 429 或全任务输出质量。
