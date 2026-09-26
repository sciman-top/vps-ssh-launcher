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

1. 立即停止该通道的全部自动化（定时 gate、周期巡检），静默期不循环重登、
   不连点检测。
2. 静默期归因只做只读探测；CPA 不分类 encrypted_content、无任何 per-key/QPS
   限流面，结构上不能监控 312——不要试图从 CPA 侧"看清"它。
3. 恢复判别：capacity 类错误消失后，人工发一次新会话请求确认；确认恢复后
   再逐个恢复自动化，一次一个变量。
4. device-login 刷新放在恢复确认之后，并顺带重置 OAuth 刷新节奏。
5. 若判定账号已不可用，走 [OAuth 失效恢复](cpa-oauth-failure-recovery.md) 的
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

**轮换前**（按顺序）：
1. 确认**所有消费者**（qq-codex-bot、本地工具等）已停止使用旧 key 或暂停请求。
2. 轮换服务端 CPA public key（在 `config.yaml` 的 `api-keys` 里）并执行 `-Apply`。
3. 轮换完成后 **10 分钟内**，通过 doctor 观察 `==gateway-statuses-current-log-24h==` 中的 `"401"` 计数：
   ```bash
   # 远端取最近 5 分钟 401 数
   docker logs cli-proxy-api --since 5m 2>&1 | grep -c '401' || true
   ```
   若 401 计数在 5 分钟内超过 15，则旧 key 仍在使用，立即停止消费者并等待封禁解除。

**自伤封禁解封**（非自动化，单次人工）：
```bash
fail2ban-client set cpa-gateway unbanip <client-ip>
fail2ban-client get cpa-gateway banip   # 验证已解封
```

---

## L3 风控静默期非 OAuth 质量探针

L3 静默期需要确认非 OAuth 路由健康时，**必须使用 `CPA_HEALTH_NO_OAUTH=1`**，
否则 `generation-all` 仍会将 Luna 路由纳入矩阵，触碰唯一 ChatGPT Plus 账号：

```bash
# 安全：不触碰 OAuth lane，逐行输出各路由 GENERATION 结果
CPA_HEALTH_NO_OAUTH=1 python3 /opt/cliproxyapi/cpa-health.py generation-all

# 如需 quality-canary（语义验证），同样需要加该变量
CPA_HEALTH_NO_OAUTH=1 python3 /opt/cliproxyapi/cpa-health.py quality-canary

# 危险（默认）：Luna 在册时会出现在矩阵里
# python3 /opt/cliproxyapi/cpa-health.py generation-all   ← L3 静默期禁止
```

`readiness` 和 `generation`（单目标 `glm-5.3-flash`）路径本来就不发 OAuth 请求，
只有显式矩阵模式（`generation-all` / `quality-canary` / `quality-eval`）受该开关影响。

---

## 已知限制（2026-09-26 深度审查）

- **上游 `Retry-After` 不参与 CPA 冷却**：`transient-error-cooldown-seconds` 固定
  60s，doctor 只把上游 `Retry-After` 分类成 `absent` / `seconds` / `other`
  记录（`retry_after_classes`），该数值不进入任何冷却决策。若上游返回
  `Retry-After: 600` 这类大值，CPA 仍会在 60s 后重试，可能在限流窗口内反复
  加压。这是已知限制，不是可随手调的参数：2026-09-21 裁定的"60s 保持"针对的
  是"客户端紧重试"，与本条不是同一个问题。要真正尊重上游 `Retry-After` 必须先
  确认 CPA 是否提供对应能力，再单独评审；本页不擅自改冷却参数。
  **可操作的缓解**：doctor `==gateway-statuses-current-log-24h==` 的
  `retry_after_classes=seconds` 且 `five_xx_local_vs_upstream` 为 `upstream` 桶上升时，
  人工延长静默期（不等 60s 恢复，先暂停该通道 `Retry-After` 对应的完整窗口）。
- **fail2ban 24h 封禁对良性 401 风暴过重**：`cpa-gateway` jail 为
  `maxretry=20` / `findtime=600` / `bantime=86400`。密钥轮换窗口内客户端用旧
  key 高速重试，可能在 10 分钟内累计 20 次 401，导致该 IP 被自伤封禁 24h（不是
  provider 封号）。**预防**见上文"密钥轮换前操作清单"；解封走远端
  `fail2ban-client`，不自动化。如需降低自伤风险，可评估 `bantime=3600`（与
  OAuth 冷却窗口对齐），但同时会降低对真实 brute-force 攻击者的封禁持续时间。
- **OAuth lane 静默期只有纪律约束，没有硬门**：定时门已用 `readiness`（零生成）
  且默认生成目标是 `glm-5.3-flash`，但 `generation-all` / `quality-canary` /
  `quality-eval` / `CPA_HEALTH_ALL_ROUTES=1` 在 Luna 在册时仍会打 OAuth lane。
  L3 静默期若要跑质量探针，**必须显式设置 `CPA_HEALTH_NO_OAUTH=1`**（命令见
  上文"L3 风控静默期非 OAuth 质量探针"）。
- **无聚合/账号级闸门**：入口限流是 per-IP（`$binary_remote_addr`），对唯一
  ChatGPT Plus 账号没有聚合上限；客户端 semaphore 由 `qq-codex-bot` 侧自律，
  本仓无法验证或强制。边界见 README "CPA 流量分配与账号暴露边界"。

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
