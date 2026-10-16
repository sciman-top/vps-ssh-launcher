# 2026-10-16 bwg CPA claude 路由 binding/prefill payload 规则部署

## 背景与归因（只读取证）

Codex Desktop 0.163.0-alpha.10.2 经 `codex_local_access` → CPA zen claude 路由
（`claude_api_key_routes`/`opencode-zen-claude`，claude-haiku-5-5）当晚 22:17-22:34
两线程连发两族上游 400：

1. `messages.1.content.N: Invalid 'signature' in 'thinking' block. The block is
   bound to a different conversation`（含 "tools list differs" 与 "content before
   this block was not present" 两种变体）；
2. `This model does not support assistant message prefill`。

根因：Anthropic 对重放 thinking 块做绑定校验（签名绑定创建时会话前缀+tools 清单），
且 haiku-5-5 拒绝尾部 assistant prefill；Codex 的重连/继续回合重放历史必然触发
（web_search 服务端块往返非字节保真、重连回合 0 工具集、assistant 结尾重发）。
转储：`/opt/cliproxyapi/auth/logs/error-v1-responses-2026-10-16T22*.log`（5 份滚动，
已被后续转储轮转）；本地 rollout
`~/.codex/sessions/2026/10/16/rollout-*01a14513/01a1451c*.jsonl`。

上游 CLIProxyAPI v8.0.30/31 均未修：`claudeModelRejectsAssistantPrefill` 家族清单
无 haiku；无自动 binding-controls 注入（v8.0.31 的 per-stream pin 为响应方向性能
优化，commit 7a511e4，与此无关）。

## 修复（配置级，按模型名钉死）

`requests.payload` 规则引擎（policy `EXPECTED_PAYLOAD_TOOL_RULES`，远端 config.yaml
文本级插入），仅作用于 `_CLAUDE_ROUTE_MODEL_NAMES`：

- override（exist 门 `thinking.type`）：注入
  `thinking.block_binding.prefix_mismatch_behavior=drop_block` +
  `betas=["thinking-binding-controls-2026-08-01"]`（claude 执行器
  `extractAndRemoveBetas` 自动转 anthropic-beta 头）；
- 条件 filter（match `messages.@reverse.0.role=="assistant"`）：删除
  `messages.-1`（sjson 文档支持负索引删除；gjson 读无负索引，匹配必须走 @reverse）。
- OAuth claude lane、codex 路由、glm/deepseek/muse 全部不触碰。

## 部署与验证事务

1. 部署前探针（8317 回环，转储真实 3840 字符签名块，内容不出 VPS）：
   无缓解 400（复现生产错误）/ 自带逃生门 200 "OK" / 基线 200 / prefill 形状 400。
2. commit b24fd68 → push → 快照
   `config.manual-snapshot-20261016T153526-config-claude-binding-rules.yaml` →
   文本插入（与 policy 派生逐字段断言一致）→ 静默窗重启。
3. 首轮探针抓到两个真缺陷并当轮修复（commit c8eabc1）：
   - 裸请求被注入缺 `thinking.type` 的 thinking 对象 → 上游 400（exist 门修复）；
     影响窗口 ~6 分钟，容器日志实证零真实流量命中（仅本会话探针）。
   - 匹配条件 `messages.-1.role` 永不命中（gjson 读无负索引）→ 改
     `messages.@reverse.0.role`。
4. 终态四象限探针全 200：绑定形状（真实陈旧块）/ prefill 形状 / 裸请求基线 /
   adaptive thinking 基线。
5. `cpa_bwg_guardrails.ps1 -Apply` 投影新 cpa_policy.py/cpa-health.py；严格 doctor：
   `semantic-policy=OK`、投影 drift 12/12 MATCH、`DOCTOR_CONTRACT_OK`。
6. full gates 624 passed +1 skipped（integration opt-in）+ bandit/ruff/format/mypy 全绿。

## 行为回归网

cpa-health 新增 `_claude_payload_rule_eval`：每次 claude 路由 generation 检查附带
两个微型探针（binding/prefill，max_tokens 64），规则失效即 400 → gate 20，结构策略
钉死之外补执行面回归。

## 回滚

- config：还原快照 `config.manual-snapshot-20261016T153526-config-claude-binding-rules.yaml`
  + `docker restart cli-proxy-api`；
- 或 revert c8eabc1/b24fd68 后 `-Apply` 重投影；
- policy fail-closed：规则缺失/篡改即 doctor 语义策略 FAIL。

## 遗留

- 断流重连回合若被 prefill 过滤删成空历史（continue-as-first-turn 形状）会失败
  单回合，不伤会话（上游对空 messages 本就拒绝）。
- drop_block 丢弃失配块 = 该回合损失部分旧推理上下文，换取请求成功。
- 上游 issue（haiku 入 prefill 家族 + 原生 binding controls）待提；合并后规则按
  muse 先例退役。
