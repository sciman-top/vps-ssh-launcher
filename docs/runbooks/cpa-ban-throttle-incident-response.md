# CPA 封号 / 限流 / 降智应急响应（bwg）

本页是 OAuth（ChatGPT Plus）、第三方中转或官方 API 通道出现疑似风控信号时的
处置入口。设计哲学是 fail-closed：先停止可疑流量，再归因；任何"规避检测"类
操作（state 注入、UA 伪装调整、身份混淆、第二账号轮换）都已明确否决，不在
可选动作里。

## 信号识别（先 doctor，再日志）

一次 strict doctor 覆盖大部分归因面：

```powershell
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg
```

按段读数：

| 段 | 关注信号 | 含义 |
|---|---|---|
| `==gateway-statuses==` | 24h 状态分布、`five_xx_local_vs_upstream`、`client_503_retry_pattern`、499 耗时簇 | 区分上游过载、本地冷却与客户端紧重试放大 |
| `==oauth-monitor==` | `ACTION_REQUIRED`（≤22h）、`FAIL_EXPIRED`、`FAIL_REFRESH_SIGNAL`（`invalid_grant`/`refresh_token_reused`） | OAuth 生命周期问题，转 [OAuth 失效恢复](cpa-oauth-failure-recovery.md) |
| `==cooldown-state==` | `active_cooldown` vs 滞留 | 正常退避必须等待；滞留口径见 [冷却恢复](cpa-stale-cooldown-recovery.md) |
| `==model-substitution==` | WARN 计数上升 | 上游静默换模型（降智的间接信号，仅观测） |
| `==oauth-quarantine==` | `oauth_quarantine=none` / `active` / `UNAVAILABLE` / `UNMARKED_OAUTH_BLOCK` / `INVALID_MARKER` / `INCONSISTENT` | `active` 是已声明的可逆隔离；`none` 正常；`UNAVAILABLE` 表示 config 不可读；其余三种 doctor fail-closed |

补充取证（只读）：

```bash
docker logs cli-proxy-api --since 24h | grep -ciE 'invalid_encrypted_content|thinking_signature_invalid|capacity|server_is_overloaded'
```

容器名是 `cli-proxy-api`；grep `cliproxy|cpa` 匹配为空是无效证据（docker logs
空跑）。看 `X-Codex-Routing-Hint` 头相关问题前，先确认它是 2026-09-25
（v7.3.17）之后唯一新增的 OAuth lane 变量。

## 分级处置

### L1 线程级错误（thinking_signature_invalid / encrypted_content item_id mismatch）

已定案为线程级问题（上游已知类），不是封号信号：

1. 弃用出错的会话线程，新开对话即恢复；不重试、不跨账号搬运对话。
2. 加密历史线程不要经过会改写 ID 的中间层（new_api 等）。

### L2 单模型 403/429/503 窗口

1. 确认 doctor `client_503_retry_pattern`：本地紧重试无视 Retry-After 时，
   先收敛客户端节奏（错峰、降频），不要改服务端冷却参数——对过载上游加压
   只会更糟（2026-09-21 已裁定 `transient-error-cooldown-seconds=60` 保持）。
2. 按既有分流顺序把流量让给下一通道：deepseek（官方）→ glm-5.3-flash →
   luna 仅交互式低并发 → sol/terra 仅非敏感备用。
3. 单凭据风暴是用户决策面：24h 零限流是常态基线，降量与错峰优先于任何
   服务端闸门。

### L3 疑似风控信号（turn-state 312 / capacity 持续 / 目录异常塌缩）

历史定案口径（2026-09-22）：

1. **先上硬门**：`-QuarantineOAuthLuna` 把两个 Luna 别名从可路由目录移除，
   使已持有公共 key 的消费者也无法继续触达唯一 OAuth 账号（详见下节）。仅停
   定时任务和探针只覆盖本仓自己的自动化，覆盖不了外部消费者。
2. 立即停止该通道的全部自动化（定时 gate、周期巡检），静默期不循环重登、
   不连点检测。
3. 静默期归因只做只读探测；CPA 不分类 encrypted_content、无任何 per-key/QPS
   限流面，结构上不能监控 312——不要试图从 CPA 侧"看清"它。
4. 恢复判别：capacity 类错误消失后，人工发一次新会话请求确认；确认恢复后
   再逐个恢复自动化，一次一个变量。确认后先 `-RestoreOAuthLuna`，再按原节奏
   逐个放回消费者。
5. device-login 刷新放在恢复确认之后，并顺带重置 OAuth 刷新节奏。
6. 若判定账号已不可用，走 [OAuth 失效恢复](cpa-oauth-failure-recovery.md) 的
   凭据处置；`-DeactivateOAuthLuna` 是唯一的凭据销毁入口，属最后手段。

### L4 确认封号

1. 停止该通道流量（客户端 preset 已有跨族逃生与 Retry-After 尊重，不要重复
   建设服务端限流来"补偿"）。
2. 记录脱敏证据（`docs/change-evidence/`），包含 doctor 输出摘要与时间线，
   不含凭据、完整请求或随机路径。
3. 是否重新接入（新账号或官方 API key）是用户决策；接入前先更新
   `cpa_provider_routes.json` 路由与排除清单，再 `-Apply`。

## 密钥轮换前操作清单（防 fail2ban 自伤封禁）

> **背景**：`cpa-gateway` jail 为 `maxretry=20 / findtime=600 / bantime=86400`。
> 密钥轮换窗口内客户端用旧 key 高速重试，可能在 10 分钟内累计 20 次 401，
> 触发自伤封禁 24h。这不是 provider 封号，但对使用方完全不可用。
>
> **回环豁免是不变量**：jail 固定带 `ignoreip = 127.0.0.1/8 ::1`，而 nginx 正是
> 经 `127.0.0.1:8317` 访问 CPA。若这行丢失，doctor 自己每次运行产生的未认证
> 401 就会累积到 `maxretry` 并封掉回环，**网关会整体失联**。因此 strict doctor
> 以 `fail2ban-ban-scope=loopback_exempt` 断言该行与三个阈值，且 guardrails
> 在读取投影源时就对缺失的 `ignoreip` 直接 `throw`（fail-closed），不等到
> 远端生效才发现。

**推荐流程：双 key 窗口（迁移期零 401）**

自伤封禁只在"旧 key 已被吊销、但仍有消费者在用"时才可能发生。只要迁移期间
**新旧 key 同时有效**，掉队消费者拿到的仍是 200，不产生任何 401：

1. 在 `config.yaml` 的 `api-keys` 里**追加**新 key（保留旧 key），执行 `-Apply`。
   两个 key 同时有效。
2. 逐个消费者切到新 key。此阶段无需停任何消费者，也不必担心 401。
3. 全部切换完成后，**移除**旧 key 并执行 `-Apply`。
4. 吊销后按下面的"掉队探测"确认没有遗留消费者。

**掉队探测（吊销后）**

```bash
# 最近 5 分钟 401 计数
docker logs cli-proxy-api --since 5m 2>&1 | grep -c '401' || true
```

- 若 401 计数在 5 分钟内超过 15，说明仍有消费者在用旧 key：立即把旧 key 加回
  `api-keys`（或让该消费者切换），不要靠等待封禁解除。
- **CPA v7.3.17 不提供客户端 key 归属**：`GET /v0/management/api-key-usage`
  虽然存在，但按 **provider + provider key** 分桶（实测为 ai.input.im /
  codex-ciii / deepseek / http-bridge-8003 / zhipu-plan 各一条），**不是**按
  调用方公共 key 分组；`/usage-queue` 的记录里虽有 `api_key` 字段，但该端点是
  破坏性读取（会弹出记录），不适合做常规观测。因此本页不提供"按消费者区分
  key 使用"的自动读数——这正是 2026-09-26 审查中"双 key 过渡"一项**仍未闭环**
  的原因。
- 若必须在不加回旧 key 的情况下探测掉队消费者，可**在受控窗口内**临时放宽
  jail（人工、有界、可回滚），而不是缩短 `bantime`：

  ```bash
  # 备份 -> 提高 maxretry -> 探测 -> 立即恢复
  cp -a /etc/fail2ban/jail.d/cpa-gateway.conf /root/cpa-jail-before-rotation.conf
  # 将 maxretry 临时改为 200，reload，探测窗口结束后必须改回 20
  fail2ban-client reload cpa-gateway
  # ...探测...
  cp -a /root/cpa-jail-before-rotation.conf /etc/fail2ban/jail.d/cpa-gateway.conf
  fail2ban-client reload cpa-gateway
  ```

  这只是把"自伤"的概率降到可接受，不是安全增强：窗口内对真实 brute-force 的
  容忍度也同时被放大，因此必须人工在场、窗口尽量短，并在结束后确认
  `maxretry=20` 已恢复。

**自伤封禁解封**（非自动化，单次人工）：
```bash
fail2ban-client set cpa-gateway unbanip <client-ip>
fail2ban-client get cpa-gateway banip   # 验证已解封
```

---

## L3 风控静默期：OAuth lane 硬门与质量探针

### 可逆隔离（服务端硬门）

`-QuarantineOAuthLuna` 把两个 Luna 别名从可路由目录中移除，使**任何**持有公共
key 的消费者（不限于本仓自动化）都无法再触达唯一 ChatGPT Plus 账号：

```powershell
# 隔离：停 OAuth lane 流量，保留凭据与刷新
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -QuarantineOAuthLuna
# 恢复：必须独立显式执行，不会因 timer、探针或客户端重试自动解除
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -RestoreOAuthLuna
```

契约要点：

- 机制是 `oauth-excluded-models.codex` 加 `/opt/cliproxyapi/oauth-quarantine.json`
  状态标记，与 `-Apply` 计算排除清单用的是同一条路径，只是方向相反。
- 事务开始前严格确认**恰好一个**活跃 Codex OAuth 凭据；拓扑不符即 `REFUSE`，
  不发生任何写入。全程持有
  `/run/vps-ssh-launcher-maintenance.lock`，与 updater、`-Apply`、系统维护互斥。
- **不读、不复制、不删除、不回放**凭据 JSON；后台 token 刷新继续运行，slot 不会
  因静默期过期；**不**调用 `reset-quota`，**不**清理冷却状态。输出
  `OAUTH_CREDENTIAL_RETAINED=yes` 与 `QUOTA_STATE_RESET=no` 作为证据行。
- 隔离期间 strict doctor 报 `oauth_quarantine=active` 且视为契约成立（不误报
  OAuth 故障）；发现被阻断但无标记时报 `UNMARKED_OAUTH_BLOCK` 并 fail-closed。
- 隔离期间 `-Apply` 会拒绝执行（`REFUSE OAuth lane quarantine is active`），
  防止例行投影把排除清单改回原样、静默撤销风控决定。
- 隔离与恢复都是单事务：备份 config.yaml 与标记、`docker restart`、目录契约
  校验、`cpa_policy.py` 语义复验，任一步失败即 `restore_all` 回滚并报
  `ROLLBACK_VERIFIED` / `ROLLBACK_FAILED`。
- 这是**流量闸门**，不是凭据处置，也不是"重置风控"；销毁凭据仍只有
  `-DeactivateOAuthLuna`。

### 非 OAuth 质量探针

`generation-all` / `quality-canary` / `quality-eval` **默认已经排除** OAuth lane，
因此静默期可以直接跑：

```bash
# 默认即为非 OAuth 矩阵，逐行输出各路由 GENERATION 结果
python3 /opt/cliproxyapi/cpa-health.py generation-all

# 需要语义验证时同样默认安全
python3 /opt/cliproxyapi/cpa-health.py quality-canary

# 仅当已确认事件状态不是 L3、且本次确实需要复核 OAuth lane 时才回选
CPA_HEALTH_INCLUDE_OAUTH=1 python3 /opt/cliproxyapi/cpa-health.py generation-all

# CPA_HEALTH_NO_OAUTH=1 仍是优先的硬抑制，即使同时写了 INCLUDE
CPA_HEALTH_NO_OAUTH=1 python3 /opt/cliproxyapi/cpa-health.py generation-all
```

每次运行会输出一行 `PROBE_BUDGET`，给出 `models=`、`cases_per_model=`、
`planned_generation_requests=` 与 `oauth_lane=excluded:<原因>`，用来在执行前
确认这次运行的计划请求量。`readiness` 和 `generation`（单目标
`glm-5.3-flash`）路径本来就不发 OAuth 请求，不受该开关影响。

**注意**：探针开关只约束探针。静默期要挡住外部消费者必须用
`-QuarantineOAuthLuna`。

---

## 已知限制（2026-09-26 深度审查）

- **上游 `Retry-After` 不参与 CPA 冷却**：`transient-error-cooldown-seconds` 固定
  60s，doctor 只把上游 `Retry-After` 分类成 `absent` / `seconds` / `other`
  记录（`retry_after_classes`），该数值不进入任何冷却决策。若上游返回
  `Retry-After: 600` 这类大值，CPA 仍会在 60s 后重试，可能在限流窗口内反复
  加压。这是已知限制，不是可随手调的参数：2026-09-21 裁定的"60s 保持"针对的
  是"客户端紧重试"，与本条不是同一个问题。要真正尊重上游 `Retry-After` 必须先
  确认 CPA 是否提供对应能力，再单独评审；本页不擅自改冷却参数。
  **2026-09-26 实测核对（v7.3.17）**：管理面只暴露 `request-retry` 与
  `max-retry-interval`（实测两者均为 `0`；`max-retry-interval` 只在
  `request-retry > 0` 时才有意义，而本部署固定 `request-retry=0`），**没有任何
  端点或配置键把上游 `Retry-After` 映射到冷却时长**。因此这一项在当前版本
  无法通过配置收口，只能靠人工延长静默期，或引入 CPA 之前的独立策略层。
  **可操作的缓解**：doctor `==gateway-statuses-current-log-24h==` 的
  `retry_after_classes=seconds` 且 `five_xx_local_vs_upstream` 为 `upstream` 桶上升时，
  人工延长静默期（不等 60s 恢复，先暂停该通道 `Retry-After` 对应的完整窗口）。
- **fail2ban 24h 封禁对良性 401 风暴过重**：`cpa-gateway` jail 为
  `maxretry=20` / `findtime=600` / `bantime=86400`。密钥轮换窗口内客户端用旧
  key 高速重试，可能在 10 分钟内累计 20 次 401，导致该 IP 被自伤封禁 24h（不是
  provider 封号）。**预防**见上文"密钥轮换前操作清单"的双 key 窗口流程（迁移期
  零 401）；解封走远端 `fail2ban-client`，不自动化。如需降低自伤风险，可评估
  `bantime=3600`（与 OAuth 冷却窗口对齐），但同时会降低对真实 brute-force
  攻击者的封禁持续时间。
- **无聚合/账号级速率闸门（仍开放，已确认上游无此能力）**：入口限流是 per-IP
  （`$binary_remote_addr`），对唯一 ChatGPT Plus 账号没有聚合速率/并发上限；
  `-QuarantineOAuthLuna` 是二值闸门（全开/全关），不提供按来源配额的排队。
  客户端 semaphore 由 `qq-codex-bot` 侧自律，本仓无法验证或强制。边界见
  README "CPA 流量分配与账号暴露边界"。
  **2026-09-26 实测核对（v7.3.17）**：CPA 管理面**没有**任何 per-key/per-account
  的 QPS/RPM/并发预算键（只有 `quota-exceeded.switch-project` 这类"配额耗尽后的
  路由行为"开关，以及 `POST /reset-quota`）。因此聚合闸门只能建在 CPA 之前的
  策略层，或继续依赖客户端自律。要收敛此项需先在 shadow 模式记录 OAuth 的
  并发、429/403/capacity 分布，再用真实观测校准阈值，不复制全局阈值。
- **OAuth lane 静默期已有硬门（2026-09-26 收口）**：`-QuarantineOAuthLuna` 把
  Luna 别名从可路由目录移除，隔离外部消费者；`generation-all` /
  `quality-canary` / `quality-eval` 也已改为默认排除 OAuth lane，只有
  `CPA_HEALTH_INCLUDE_OAUTH=1` 才回选。两者是互补的：探针开关管本仓自动化，
  `-QuarantineOAuthLuna` 才管外部消费者。隔离不销毁凭据、不重置配额/冷却。
- **本地限流 429 已带明确 `Retry-After`（2026-09-26 收口）**：被本地
  `limit_req`/`limit_conn` 拒绝的响应带 `Retry-After: 1`，由
  `map "$limit_req_status:$limit_conn_status"` 守卫，只在真正被本地拒绝时出现；
  `200`/`401`/`404` 与上游透传的 `429`/`5xx` 不受影响。strict doctor 以
  `safe-throttle-retry-after=OK` 与 `throttle-retry-after-map-count=1` 冻结。
  受控压测方法与实测读数见
  [cpa-gateway.md](cpa-gateway.md) 的"入口限流的实际作用"。注意客户端仍应按
  自身退避策略处理：`1` 是下界而非预测。

## 禁止

- 不做对抗性规避（state 注入、UA/cloaking 调整、identity-confuse、第二账号
  轮换）——全部已裁定不采纳。
- 不把 `-DeactivateOAuthLuna` 当作"重置风控"的常规手段：它不可逆地删除全部
  可刷新 OAuth 物料。
- 不在事件窗口调整 nginx 并发/限流参数来"吸收"风控信号：入口阈值不是
  provider 配额，历史上（2026-09-13）已因此误伤独立通道。

## 边界

本页只覆盖 CPA 网关侧的账号风险处置。VPS 系统层（IP 信誉、端口扫描、
fail2ban 误封）不在此页：fail2ban 解封走远端 `fail2ban-client`，不自动化。
