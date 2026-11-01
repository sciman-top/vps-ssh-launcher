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
  `/root/workbuddy-auth-pre-reserve-20261101T072011Z/`、`config.manual-snapshot-20261101T074520Z-pre-global-reserve.yaml`。
- 日志：config_reload.go:148（accepted）、events.go:141 增量处理、conductor_execution.go:2038 +
  gin_logger [07b07248] 同请求双行、usage_config.go:375 `reserve_credits=400 applied`。
- 参考仓 CLIProxyAPI 已同日 ff 更新至 250d5981（2026-10-31 main）。

## 7. 续：受控实战验收与护栏武装语义（同日补充）

**验收结论**：T1 PASS（§3）；T2 的跳闸演练暴露护栏**武装语义缺口**——护栏链路本身
（config → loadedReserveCredits → cachedCreditsScore → guardSkipRemain → pick withhold）源码级
正确且 `reserve_credits=400 applied` 回执在手，但运行时**断粮**：

- 插件今日 12:46/13:09 两次重注册（凌晨 04:46Z rate-guard 落产所致）清空内存 `accountCache`；
- 此后零面板轮询（12:42 后用户未开面板）→ `cachedCreditsScore=-1` → guard 按设计
  "cannot judge what it cannot see" fail-open；
- **错误驱动武装被掐死**：`reconcileAfterExecutorError` 首行 `if !lifecycleEnabled() { return }`，
  本部署 `lifecycle_auto: false`（10-18 显式关闭，防 lifecycle 自动禁删账号）；
- **checkin 任务无法重建空缓存**（09:00/21:00 tick）：credits 刷新全是 Load-Modify-Store 模式
  （checkin.go:156-163/176-184），空缓存下 Load 落空即 no-op，且尾部 reconcile 亦 lifecycle 门控；
- **intl 凭据唯一武装源 = 面板打开**（/credits 端点 → cachedAccountDetails 无条件 Store）；
  CN 同样依赖面板（checkin 刷新亦 Load-gated）。
- 演练实录：480 窗（intl 应跳过）零失败样本不可判；3000 全护栏窗三探针照常 wb 车道 200、
  零 veto 日志 → 实锤断粮（非链路损坏）。并发压测被否决：#339 并行请求触发账号限制为实测案例。
- 免责核对：密钥面全部未触碰（探针用 access.api-keys[0]；管理 secret-key 为 bcrypt 哈希不可逆，
  面板明文钥仅存用户浏览器）。
- 终态回执：`2026/11/01 16:12:11 workbuddy: reserve_credits=400 applied`。

**遗留决策（移交用户）**：武装修复三选一——A) 日常保持面板开启/定期打开（零改动，武装随开随得）；
B) 用户明文管理密钥交给 VPS 侧 cron 定期轮询 /credits（自主武装，密钥落盘面扩大）；
C) 本地补丁：pick 路径 guard 配置在位但缓存 miss 时异步 singleflight 触发一次
cachedAccountDetails 自武装（治本，但需远端重建 .so + 重注册，且有既有 patch 退役管理面）。
D) 上游报 issue 等官方修复。
