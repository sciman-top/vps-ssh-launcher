# BWG deepseek 车道主备固化 + intl 积分护栏（2026-11-01）

**性质**: 真实远端写入（生产 config.yaml + 2 个 workbuddy 凭据文档）· 脱敏留档
**授权**: 用户当前指令"按推荐连续自动修复，主用 workbuddy、备用 opencode"
**执行**: 只读取证 → 快照 → 内容匹配单点编辑 → 热加载验证 → 行为实证

## 1. 背景与裁定

- deepseek-v4.1-flash 双宿两车道：workbuddy 插件（3 OAuth）+ opencode（`api-keys.openai-compatibility` 组 slot2）。10-31 起实测会话亲和绑定 100% 落 opencode（ID 序巧合），workbuddy 只接残余。
- 用户裁定：**主用 workbuddy，备用 opencode**（配合未来暂停 opencode 订阅的终态）。
- 同日裁定不拆双宿（见记忆 deepseek-dualhome-lane-topology）；intl 免费额度 11-01 全天未翻转（哨点 A 静默，仅 6004）。

## 2. 变更序列（UTC）

1. **T1 主备固化**：快照 `config.manual-snapshot-20261101T071912Z-pre-opencode-priority.yaml`（0600）后，
   向 `api-keys.openai-compatibility` 组内 opencode 条目（`base-url: https://opencode.ai/zen/go/v1`）
   `disable-cooling` 行后插入单行 `priority: -1`（该组 sharedKeyFields 白名单字段，config_v8.go:384）。
   语义：workbuddy 凭据缺省 priority=0 > opencode=-1，选择器最高可用优先级层裁决
   （sdk/cliproxy/auth/selector.go `highestPriorityAuths`，数值大者优先，负值合法）。
   热加载回读：`config_reload.go:148 config successfully reloaded`，10 clients / 6 auth entries 无丢失。
2. **T2 intl 积分护栏**：快照目录 `/root/workbuddy-auth-pre-reserve-20261101T072011Z/`（0700/0600）后，
   向两个 intl 凭据文档顶层加 `"reserve_credits": 400`（storedAuth 已知字段，main.go:471，
   per-credential 覆盖语义：缓存剩余积分 ≤400 即在调度 pick 中跳过；缺省继承、0=停用）。
   插件增量处理无错误；字段经 15:27/15:28/15:29Z 多轮插件回写（token/state）后复核存活 400/400。

## 3. 行为验收（ACCEPTANCE_RESULT=PASS）

单发无会话探针（`/v1/chat/completions`，deepseek-v4.1-flash，max_tokens=16，access.api-keys[0]）：

- 探针 1（15:28:41Z+8）：直连 200，`pong`，`usage.credit=0` —— workbuddy 可用时直连成功。
- 探针 3（15:30:32Z+8）：**同一请求 `07b07248` 先 `upstream execution failed: provider=workbuddy
  auth_file=workbuddy-intl-0faaff85…`（6004 瞬窗）→ 200 708ms 成功**，响应
  `usage.opencode.billing_source=go` —— workbuddy 冷却窗内自动降层 opencode。
- 探针 2（15:29:2x）：200 空 content —— workbuddy 服务、reasoning 吃尽 max_tokens 的截断形态
  （探针 3 同参数重现 content="p"/reasoning_tokens=14），非 #35 空流、非故障。
- 判读：主用 workbuddy（可用即直连）、备用 opencode（6004 瞬窗桥接）= 目标裁决语义。

## 4. 回滚

- T1：从 071912Z 快照语义级合并（仅回退 `priority: -1` 单行，勿整文件覆盖——其间有自然回写）。
- T2：删除两个 intl 文档顶层 `reserve_credits` 键（或整值改 0=显式停用）；快照目录 072011Z。
- 均为 config/凭据文档热加载生效，无需重启容器。

## 5. 观察项与未来时点

- reserve=400 的触发点在 intl 真实积分消耗出现后才会显形（哨点 A 翻转时看面板已用 + 调度跳过日志）。
- 既有 opencode 亲和会话按设计继续 replay（亲和跨优先级层），随会话自然churn迁移到 workbuddy；
  新会话/无会话请求立即走新裁决。
- 6004 窗口内 deepseek 由 opencode 桥接为预期形态；workbuddy 全量接管后 6004 频度将高于双宿期
  （rate guard 18 在位，带顶 19 仍命中才议 20）。
- 暂停 opencode 订阅日 checklist（未来执行）：先核哨点 A → disable opencode 条目（或靠冷却自愈）→
  头 24h 盯 6004/rate guard → reserve 届时语义从防漏损闸变急停阀（触发即损失容量，慎用）。
- 判道命令：`docker logs cli-proxy-api --since 24h | grep session-affinity | grep deepseek | grep -o "auth=[^ ]*" | sort | uniq -c`；
  探针车道归属看响应 `usage.opencode` 块（opencode）vs `usage.credit`（workbuddy）。

## 6. 证据锚点

- 快照：`/opt/cliproxyapi/config.manual-snapshot-20261101T071912Z-pre-opencode-priority.yaml`、
  `/root/workbuddy-auth-pre-reserve-20261101T072011Z/`。
- 日志：config_reload.go:148（accepted）、events.go:141 增量处理、conductor_execution.go:2038 +
  gin_logger [07b07248] 同请求双行。
- 参考仓 CLIProxyAPI 已同日 ff 更新至 250d5981（2026-10-31 main）。
