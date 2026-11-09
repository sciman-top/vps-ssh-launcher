# bwg CPA：dead fail2ban jail 与 antigravity 403 误锁（2026-11-09）

**性质**：真实远端写入（admission 源码换装 + guardrail 投影）· 脱敏留档
**用户授权**："授权按你的推荐/建议连续自动修复和优化，直至完成所有任务。"
**前置**：全量门禁 `1202 passed / 18 skipped / 595 subtests passed`，bandit / ruff / format / mypy 全绿（`.tmp_gates_20261109j.log`，GATES_EXIT=0）。

## 1. 缺陷一：fail2ban jail 长期失效（本轮最重要）

### 1.1 现象与证明

`fail2ban-regex` 对本机真实日志的匹配数为 **0**：

```
Failregex: 0 total
Lines: 1702 lines, 0 ignored, 0 matched, 1702 missed
```

即 `cpa-gateway` jail **从未能封禁任何凭据爆破源**；而 strict doctor 全程报
`fail2ban-contract=OK`。

### 1.2 根因（两者叠加）

1. **日志格式前移了锚点**。`cpa_safe` 的契约是"新字段一律追加到末尾"
   （`cpa-guardrail-apply.sh:1848` 注释明示），但追加位置落在
   `auth_status=$cpa_auth_status` **之后**：
   - `route=$cpa_route_class` 于 2026-09-15 插入到 `status=` 之前；
   - `upstream_header_time=$upstream_header_time` 于 2026-10-28 追加到
     `auth_status=` 之后。
   而 filter 的正则已钉死为 `... auth_status=(401|403)\s*$`，行尾锚点从此永不匹配。
2. **doctor 用"源码字面量"代替"行为"**。旧断言是
   `grep -Fq 'failregex = ^<HOST> ... \s*$'`——它只能发现"文件被改过"，
   永远无法发现"表达式已匹配不上 nginx 真实写出的日志"。这正是本次失效
   被绿灯掩盖一个多月的机制。

### 1.3 修复

- `scripts/remote/cpa-fail2ban-filter.conf`：终止条件改为
  `(?:\s|$)`，`route=` 保持可选（`cpa_safe443` 与 pre-route 形态兼容）。
- `scripts/remote/cpa-guardrail-doctor.sh`：新增 `fail2ban-regex-behaviour`
  ——用两条探针记录（401/403）跑真实 `fail2ban-regex`，必须命中 2 条，
  否则 `mark_fail`（**不降级为 warn**）。
- `tests/test_cpa_guardrails_script.py`：纯 Python 复现 fail2ban 替换规则
  （无需 daemon/bash），断言 401/403 命中、200 与本地 429 不命中，并锁定
  `auth_status` 在 `upstream_header_time` 之前的字段序。

### 1.4 重新武装前的封禁面评估（安全前置）

启用一个此前 inert 的 jail 等于引入新的封禁行为，故先对 53 份轮转日志回放
`maxretry=20 / findtime=600` 滑动窗口：

| 来源 | 401/403 总数 | 任意 600s 窗口峰值 | 判定 |
|---|---|---|---|
| `127.0.0.1` | 2588 | 29 | `ignoreip` 豁免（doctor 断言） |
| `183.236.101.21` | 229 | **15** | 真实客户端（200 × 105636），低于 20 |
| `8.163.41.198` | 259 | **7** | 真实客户端（200 × 10405），低于 20 |
| `144.34.229.116` | 65 | 10 | 本机公网 IP，低于 20 |

结论：无真实客户端处于可封窗口内；`ignoreip` 已覆盖最关键的回环自伤面。
（这是"当前无自伤"，不是"永不误封"：runbook 既有条目"fail2ban 递增封禁
仍可能误伤良性 401 风暴"继续有效。）

## 2. 缺陷二：antigravity 403 误锁整条车道

### 2.1 证明

修复前实测（`oauth_risk_signal` 直接调用）：

```
antigravity-oauth html403  -> ('authentication_required', None)   # 应 None
antigravity-oauth plain403 -> ('authentication_required', None)   # 应 None
chatgpt-oauth     html403  -> (None, None)                        # 既有正确行为
```

### 2.2 根因

2026-10-31 的 `565a2c6` 只给 chatgpt/workbuddy 加了 edge-denial 豁免；
antigravity 的分派（`2cbebaa1`，2026-10-15）位于该守卫**之上**，因此
任何非 JSON 的 403 都直接落 `authentication_required`。Antigravity 经 Google
边缘访问，HTML/纯文本 403 恰恰是"边缘拦截"最可能的形态——一次误判即
删除一条健康的 Google AI Pro 车道直到人工释放。

### 2.3 修复

`scripts/remote/cpa-admission.py`：antigravity 分派前套用同一
`edge_denial_body` 守卫。**双向**保持语义：

| 输入 | 修复后 | 说明 |
|---|---|---|
| HTML / 纯文本 403 | `(None, None)` | 传输层证据，不锁账号 |
| 空 body 403 | `authentication_required` | 未知证据保守锁 |
| `PERMISSION_DENIED` + `TOS_VIOLATION` | `account_restricted` | 真实吊销仍上报 |
| 401 | `authentication_required` | 不变 |

## 3. 部署与回执

### 3.1 admission 源码换装（`outputs/deploy-admission-edge-denial-20261109.ps1`）

```
DRY_RUN_OK baseline=e0ad23c9… candidate=213d92c2… idle=true no_swap=true
REQUEST_GUARD_EXCLUSIVE_OK → ADMISSION_IDLE_OK → ADMISSION_INTEGRITY_OK
→ EDGE_DENIAL_CONTRACT_OK
DEPLOY_OK pin=213d92c2ad729357ad7ca456f85b70b35390d21990097f9c88b3d58a316fe606
backup=/opt/cliproxyapi/backups/20261109T141119.791091833Z-admission-edgedenial-20261109
```

换装后活体合同断言（部署脚本内置，失败即回滚）：
`EDGE_DENIAL_CONTRACT_OK`（HTML 403 不锁 / TOS 仍锁 / 兄弟车道未回归）。

### 3.2 guardrail 投影（`-Apply`）

```
PROJECTION_HASH_VERIFIED ×12（含 /etc/fail2ban/filter.d/cpa-gateway.conf 60973f0d…）
GUARDRAILS_APPLIED → APPLY_EXIT=0
BACKUP_DIR=/root/cpa-guardrails-backup-20261109T141610.390341949Z
```

### 3.3 验收（`live_accepted`）

| 项 | 结果 |
|---|---|
| `fail2ban-regex` 对真实日志 | **0 → 166 matched**（1980 行） |
| `fail2ban-regex-behaviour` | `OK`（探针命中 2 条） |
| `fail2ban-contract` | `OK` |
| 当前封禁名单 | 空 |
| strict doctor | **`DOCTOR_CONTRACT_OK`**，EXIT=0 |
| 四条 lane | 全 `hold=None / cooldown=0` |
| `cpa-admission` | `active`，`NRestarts=0` |
| 部署后 5 分钟自然流量 | 27 × `completed`，0 失败 |

### 3.4 归档缺陷附带结论（`fail2ban` 安全余量）

修复后 `Total banned` 仍为 0：现有真实客户端的 401 峰值（7 / 15）显著低于
`maxretry=20`，jail 重新武装但未造成任何误封。

## 4. 未覆盖边界

- 本次不改变 `maxretry / findtime / bantime` 契约，不自动化解封；
  "良性 401 风暴仍可能触发自伤封禁"的既有风险维持，解封仍为人工
  `fail2ban-client set cpa-gateway unbanip <ip>`。
- `upstream_header_time` 必须继续保持在 `cpa_safe` 末尾；若未来再追加字段，
  新正则因不锚定行尾而天然兼容，但 `auth_status` 若被移动到末尾之前，
  测试会先失败（已在测试中锁定字段序）。
- 本台账不构成对上游长期封号、额度或质量的验收；账号侧姿态零改动
  （未新增/轮换账号、未清冷却、未调并发）。

## 5. 回滚

- admission：`cp -a /opt/cliproxyapi/backups/20261109T141119.791091833Z-admission-edgedenial-20261109/{cpa-admission.py,cpa-admission.sha256}` 回原位 →
  `systemctl restart cpa-admission`。
- fail2ban/doctor：`cp -a /root/cpa-guardrails-backup-20261109T141610.390341949Z/` 内对应文件回原位 →
  `fail2ban-client reload --restart cpa-gateway`；或 revert 本提交后重跑 `-Apply`。
- Git 回滚不能代替远端恢复。

## 6. 投影与活体验收补强（2026-11-09 22:1x，用户质询后补齐）

用户追问"需要投影，模拟/受控实战验收？"——前文 §3.3 的 `live_accepted` 只覆盖
"部署脚本内置断言 + 自然流量"，**不包含** daemon 端到端与真实 HTTP 封禁回路。
本节补齐四项，并且**全部在隔离面执行，生产零改动**。

### 6.1 投影（逐字节，非"跑过一次 wrapper"）

本地 LF 归一化 SHA 与远端实文件逐一相等：

| 文件 | SHA-256（前后一致） |
|---|---|
| `cpa-fail2ban-filter.conf` → `/etc/fail2ban/filter.d/cpa-gateway.conf` | `60973f0d…fd050ea` |
| `cpa-fail2ban-jail.conf` → `/etc/fail2ban/jail.d/cpa-gateway.conf` | `417c174d…06c9cf6` |
| `cpa-admission.py` → `/opt/cliproxyapi/cpa-admission.py` | `213d92c2…6fe606` |
| `cpa-admission.json` → `/opt/cliproxyapi/cpa-admission.json` | `f3a45d75…b58716` |
| `cpa-admission.service` | `11948c3d…143260` |
| `cpa-admission-integrity-pin.txt` → `/etc/vps-ssh-launcher/cpa-admission.sha256` | `a1cfcb87…621863` |

strict doctor `==projection-drift==` 12 项全 `MATCH`，`DOCTOR_CONTRACT_OK`。

> **覆盖缺口（如实记录，未修）**：`==projection-drift==` 只覆盖 12 个受管文件，
> **不含 fail2ban filter/jail**，尽管 `-Apply` 确实投影了它们
> （`PROJECTION_HASH_VERIFIED /etc/fail2ban/filter.d/cpa-gateway.conf`）。
> 即 fail2ban 两文件目前只有 apply 时的写入校验，没有后续漂移门禁。
> 本次以 §6.1 哈希表人工核验替代，未擅自扩 doctor 覆盖面（属独立契约变更）。

### 6.2 fail2ban daemon 端到端（受控演练，隔离 jail）

用**同一个生产 filter 文件**建独立 jail（`cpa-gateway-drill`，独立日志、
`maxretry=2 / bantime=60`），演练后自动拆除：

| 阶段 | 结果 |
|---|---|
| 基线（无日志） | `failed=0 / banned=0` |
| 写入 1 条 `status=200` | `failed=0 / banned=0`（不误计） |
| 写入 2 条 `status=401` | `Total failed=2` → `Currently banned=1` → `203.0.113.77` |
| 生产 jail | `Currently banned=0`，全程未受影响 |

结论：新正则不仅在离线 `fail2ban-regex` 上匹配，**daemon 真的会按它计数并封禁**。

### 6.3 admission edge-denial 真实 HTTP 回路（受控演练，netns 隔离）

用 `ip netns` 建独立网络命名空间：命名空间内 8317/8318 与生产同名端口互不冲突，
故可加载**未经修改的生产 config.yaml 与 cpa-admission.json**（`load_config` 对
端口的硬钉定因此不被绕过——这一点本身也验证了那些钉定是有效的）。命名空间内起
假上游（可配 403 body）+ 生产 admission 源码，走真实 HTTP：

| 用例 | 上游 body | `hold_before` | HTTP | `hold_after` | 判定 |
|---|---|---|---|---|---|
| A | HTML 403（边缘页） | `None` | 403 | **`None`** | ✅ 不锁 |
| B | `PERMISSION_DENIED` + `TOS_VIOLATION` | `None` | 403 | **`account_restricted`** | ✅ 仍锁 |
| C | 空 403 body | `None` | 403 | **`authentication_required`** | ✅ 保守锁 |

演练期间 `prod_ag_hold=None`，生产守护进程未受影响。这证明修复在**真实请求路径**
上双向成立，而非仅 `oauth_risk_signal()` 单函数层面。

### 6.4 公网全链受控单发（nginx → admission → CPA → 上游）

`--resolve` 锁 SNI 到 127.0.0.1，经公网域名 + 16-hex 能力路径，每车道单发：

```
public_antigravity http=200 t=2.86s pong=1
public_workbuddy   http=200 t=1.59s pong=1
public_codex       http=200 t=7.47s pong=1
public_zhipu       http=200 t=2.00s pong=1
```

### 6.5 手尾（全部为空）

- 四车道 `hold=None / cooldown=0`；
- `Currently banned: 0`、`Total banned: 0`；
- `cpa-admission` `active`、`NRestarts=0`；
- 演练残留：`ip netns` 0 个、`/tmp` 临时目录 0 个、`jail.d` drill 文件 0 个；
- 生产 jail 已加载修正后正则（`fail2ban-client get cpa-gateway failregex` 回读一致）。

### 6.6 仍然**未**覆盖的边界

- 公网单发是**每车道一次**的受控探针，不是长期自然流量验收，不证明上游封号/额度/质量。
- fail2ban 需**真实 20 次 401/600s** 才会在生产触发；本次用隔离 jail 验证机制，
  **没有**也不应在生产制造 401 风暴。
- daemon/drill 均未验证"封禁后客户端恢复"路径（解封仍为人工操作，按 runbook）。
