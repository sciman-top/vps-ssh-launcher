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

**遗留决策（用户裁决 2026-11-01）**：选 D+A——上游 issue 已提交
<https://github.com/mmqz/cpa-multi-plugins/issues/70>（完整根因链+复现+建议修法，脱敏）；
日常武装依赖用户开面板习惯（打开即武装全部账户）。C（本地自武装补丁）与
B（明文密钥 cron）被否决：C 因今晚生产车道刚稳定+新增第5补丁管理面，B 因密钥落盘面扩大。

## 8. 续：带燃料跳闸演练——T2 端到端验收 PASS（同日再补）

16:29 用户开面板（账号面板逐卡刷新）→ 三账户 /credits 全部 200 入缓存
（CN 932ms、INTL 各 5.3s 分页慢）→ 武装完成。16:38 复跑 3000 全护栏窗：

- `16:38:27 workbuddy: reserve_credits=3000 applied`（回执）；
- 探针 **HTTP 500 `oauth_pool_unavailable: No eligible WorkBuddy account; cooling or
  exhausted accounts are not retried`**（reject 合同：veto 不桥接 opencode，硬停）；
- **`16:38:33 workbuddy: scheduler pick rejected - withheld veto=2 cooling=0 disabled=0`**
  （veto=2 而非 3：当刻一个 wb 账户处 6004 模型冷却，被 host 预过滤，插件对其可见的
  2 个候选全部护栏否决——与 host availableAuthsForSelector 预过滤语义一致）；
- 16:39:44 回 `reserve_credits=400 applied`，恢复探针 200 `pong`/workbuddy 车道。
- **如实记录**：演练窗口 ~77 秒（16:38:27→16:39:44）为用户可见的 deepseek 不可用窗，
  用户 Codex 客户端当场命中一次 "stream disconnected… Upstream status=500"（重试即恢复）——
  该表现与 reject 合同一致，属受控演练的预期代价，非新故障。
- **T2 端到端验收 PASS**：config → loadedReserveCredits → accountCache（燃料）→
  cachedCreditsScore → guardSkipCredential → pick withhold → veto reject，全链实测走通；
  fail-open 断粮态（§7）与 fail-closed 燃料态（本节）均实证，护栏行为完全刻画。

## 9. 续：新增 2 个 INTL 账户与护栏地板冲突（同日三补）

用户 16:5x 新增两个 INTL 凭据：`workbuddy-intl-e99bc89f-…`（sciman.phys，trial，池 350/2 包）、
`workbuddy-intl-ec2bceab-…`（agi.sciman，free，池 190/4 包）。

- **冲突**：全局 `reserve_credits: 400` 地板 > 两新账户剩余（350/190），武装态下
  `guardSkipCredential` 必然跳过它们（16:53 面板刷新后缓存有燃料，跳过即时生效）
  ——新增容量被护栏锁死。
- **修复**：快照 `/root/workbuddy-auth-pre-reserve0-20261101T090741Z/`（0700/0600）后，
  向两份新凭据文档顶层写 `"reserve_credits": 0`（CHANGELOG 语义：0=该凭据显式停用护栏）。
  增量处理确认、字段读回 0/0。**生效条件=缓存 re-Store**：用户在面板点"刷新数据"或两卡
  "刷新"后，Store 带入 override=0 → 释放。翻转暴露面（override 0=用满到真耗尽，
  exhausted-defer 兜底）已向用户说明，可随时改数字调地板。
- 原三账户地板不变：CN 2488/400、intl 470/400（各 70 credit 漏损预算）。
- 零插件重注册伴随（config reload 不触发 re-register，武装未被清）。

### 同窗流中断报错的取证结论（未决，观察项）

用户 Codex 客户端两次 "stream disconnected before completion: upstream stream closed
before a terminal event (last event: response.in_progress)"。取证：CPA 全量日志零
"terminal event/disconnected"；40min 内 workbuddy 仅 2 败（1× 上游 `server_is_overloaded`
Retry-After 327s 容量窗 + 1× admission busy fail-fast）；opencode 车道 3h 零失败；
`/v1/responses` 3h 仅 1 个非 200（=16:38:36 演练 reject）。**排除**：今日 config 变更
（priority/reserve 均为 pick 层，机制上不可能切断已建立流）。**候选**：客户端→网关链路
抖动（晚间高峰）或上游中途收流且插件未判截断。网关日志未见可判痕迹。**处置**：观察项，
复发时按精确时间戳拉窗口日志定位。
