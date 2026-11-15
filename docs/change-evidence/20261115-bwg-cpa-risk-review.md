# bwg CPA 风控/限流/降智/性能 增量复审（2026-11-15）

**性质**：只读审查 + 仓库文档增量。**本轮零远端写入**：未改 config、未增删/轮换凭据、
未清冷却、未重启服务、未变更并发或重试预算。

**授权**：用户当次请求"全面深度审查…防范封号、限流、降智风险／风控，优化吐字缓慢、
缓存命中率；如有必要就修复/优化本项目代码"。

**前置**：[2026-11-08 交接书 §8](20261108-bwg-cpa-handoff.md) 已就同一封号潮做过一轮
外部情报 × 配置面普查（结论：本栈无账号级信号、代码零改动）。本页只记录**增量证据**
与**本轮新发现**，不重复推导既有结论。

---

## 1. 证据分层与采集口径

| 项 | 值 | 层级 |
|---|---|---|
| 采集窗口 | 2026-11-13 13:46 → 2026-11-15 10:02 (+08)，容器日志另取 168h | — |
| 连接器 | `ssh_tool.py --profile bwg run`（只读命令） | `host_loaded` |
| doctor | `cpa_bwg_guardrails.ps1 -Profile bwg` → `DOCTOR_CONTRACT_OK` | `host_loaded` |
| admission | `/healthz` ok；四 lane 全部 `cooldown_active=false`、`hold_reason=null` | `host_loaded` |
| 容器 | `cli-proxy-api-fork:v8.0.61-gemini-raw`、`Up`、`restart=0`、StartedAt 2026-11-15T01:34:25Z | `host_loaded` |

**本轮结论可以外推的范围**：只覆盖上述窗口与这台主机。上游容量、账号政策与社区叙事
都是外部事实，本页不用它们证明本栈健康或中招。

---

## 2. 封号面：24h 内零账号级信号

`docker logs --since 24h` 逐码计数（容器 2026-11-15T01:34Z 重启，窗口完整）：

| 信号 | 24h 计数 | 判读 |
|---|---:|---|
| `6008` Credits exhausted | 0 | 日额度窗口已复位 |
| `6004` 模型级限流 | 0 | — |
| `14003` 模型繁忙 | 0 | — |
| `11140` | 0 | — |
| `TOS_VIOLATION` / `PERMISSION_DENIED` | 0 / 0 | 无 Google 吊销形态 |
| `invalid_grant` | 0 | 无刷新令牌失效 |
| `permission_error` | 0 | 无 oc 订阅类错误 |

凭据态（`/v0/management/auth-files` 只读，未打印任何 token）：

- 20 份凭据全部 `disabled=false`、`status=active`、`cooldowns=[]`；
- Antigravity 2 份的访问令牌在 `2026-11-15T01:42Z` 完成刷新（文件 mtime 自证），
  说明刷新链健康，不是"看似在役实则过期"；
- WorkBuddy 14 份（7 CN `www.codebuddy.cn` + 7 INTL `www.codebuddy.ai`）全部在役；
- Codex 1 份：`X-Codex-Plan-Type=plus`，**主窗（300 min）已用 6%**，
  **次窗（10080 min）已用 3%**，`X-Codex-Credits-Balance=0`（Plus 计划无 credits 属正常）。

**因此本轮不触发任何账号动作**：不加号、不换号、不轮换、不清冷却、不重启。

---

## 3. 限流面：6004 车轮胎面被双 realm 共享放大（本轮新发现）

### 3.1 7 天分布（容器日志 168h）

| 日期 | `6004` | `6008` |
|---|---:|---:|
| 11-10 | 4 | 2 |
| 11-11 | 51 | 9 |
| 11-12 | 15 | 0 |
| 11-13 | 24 | 35 |
| 11-14 / 11-15 | 0 | 0 |

修复更正：医生台的 44 条 `6008` **全部来自 INTL 七份凭据**（0faa 6 / 1610 7 / b915 5 /
ca64 9 / ec2b 7 / e99b 7 / f8f4 3），CN 侧 0 条。这与
[2026-11-13 台账](20261113-bwg-cpa-sol-terra-oauth-retirement.md) 里"14 凭据全灭"的更正
一致，并进一步定位到 realm。

### 3.2 机理（读码 + 日志双证）

`cpa-admission.json` 的 `workbuddy-oauth` 是一条 lane，`models` 同时含
`deepseek-v4.1-flash`（CN+INTL 共享裸名）与 `deepseek-v4.1-flash-sg`（INTL 专属）。
cooldown 阶梯是 `60/120/240/480/900`，封顶 **900 s**。

而 `6004` 在上游是**每（凭据 × 模型）**的窗口，reset 实测到次日（日志样本
`resets at 2026-11-13 19:33:50 UTC+8`）。admission 按**模型名**判 lane，把 6004 封顶为
300 s（`WORKBUDDY_6004_MAX_COOLDOWN_SECONDS`）后，CN 侧的一次 6004 实际是**用一个
远短于上游窗口的本地压制，换取后续请求打到另一个 realm 的凭据**。

这**不是缺陷**——它是 2026-11-06 有意的设计，代价与收益在
[cpa-workbuddy-plugin](cpa-workbuddy-plugin.md) §4 已登记；本节只是把它标注为
**本栈当前最主要的"共享账号面扩散"来源**，供后续决策时定量引用：
11-11~11-13 共 90 次 6004 中，INTL 凭据被跨 realm 打到（见 §3.1 的 6008 分布同期）。

### 3.3 处置建议（**不进代码，属用户决策**）

- 可选项 A：把 CN/INTL 拆成两条 lane（`models` 不重名 → 各自独立 inflight/冷却）。
  代价：裸名 `deepseek-v4.1-flash` 的 CN-first 契约与跨 realm 兜底都会变，需改
  `cpa-admission.json` + `cpa_provider_routes.json` 的 `plugin_routes` +
  `alias-multi-slot-pooled` 声明与全套测试，属**契约变更**。
- 可选项 B：保持现状（当前 `scheduler_mode=credits` + 每凭据 inflight 1 已经是
  有界回退）。
- **本轮不做**：没有官方阈值可依，且 6004 属"频率限制、可恢复"官方口径，
  不是账号受限证据。按 R5 不做无证据的预防性改造。

---

## 4. 缓存面：整体 88.9%，但两个 gpt 模型存在一次性前缀

### 4.1 48h 逐（lane, model）命中

| lane / model | 请求 | 命中率 | 零缓存请求 | 平均输入 |
|---|---:|---:|---:|---:|
| zhipu / `glm-5.3-flash` | 387 | **98.6%** | 6 | 195,689 |
| workbuddy / `deepseek-v4.1-flash-sg` | 348 | **97.7%** | 1 | 342,370 |
| chatgpt / `gpt-6.1-terra` | 65 | 93.0% | 22 | 77,805 |
| workbuddy / `deepseek-v4.1-flash` | 64 | 91.3% | 4 | 11,853 |
| chatgpt / `gpt-6.1-sol` | 91 | **82.1%** | **84** | 5,011 |
| chatgpt / `gpt-6-luna` | 23 | **12.0%** | **14** | 93,706 |

### 4.2 两个低值不是网关缺陷

- `gpt-6.1-sol` 的 84/91 零缓存请求是**同一形态的探活**：`route=chat`（非 SSE）、
  `usage_in=53`、`usage_out=16`，约**每 19 分钟一发**（107 个 `route=chat` 请求
  覆盖 41 小时，中位间隔 1133 s）。这些请求本身不产生缓存，却作为分母把命中率
  拉低；真实对话轮次的命中率显著更高。
- `gpt-6-luna` 的 12% 由**同一会话内 3 次冷重启**造成（见 §4.3），不是稳态。

**结论**："优化缓存命中率"在网关侧的收益上界不足 10%，而任何改写前缀的动作
都会把 90%+ 打成 0（[cpa-context-speed-budget](cpa-context-speed-budget.md) §4 的
否决不因本轮证据改变）。

### 4.3 本轮新发现：大上下文会话的冷重启

同一 `client_request_hash` 的会话序列里，出现"已建立 24 万 token 前缀后，
下一轮 `usage_cached=0`"的形态：

```
chatgpt/gpt-6-luna  hash=03fa1f58bf31
  07:12  in=  95,495  cached=  3,712   (gpt-6.1-terra, 换模型)
  07:13  in= 240,767  cached=      0   ← 冷重启
  07:13  in=  99,902  cached= 95,360   (gpt-6.1-sol, 换模型)
  07:14  in= 246,463  cached=  6,016   ← 再次冷重启
```

13 个多轮会话中 3 个出现冷重启、共 5 次。候选成因（**未拆分，不作定论**）：

1. `session-affinity-ttl = 1h` 到期后重新选凭据（跨凭据缓存不共享）；
2. 客户端在同一 `hash` 里切换模型（terra/sol/luna 交替）；
3. 上游自身的 prompt-cache 驱逐。

**判读代价**：每次 24 万 token 冷重启，按单账号口径就是 ~24 万未命中输入
+ 一次完整 prefill。这正是"吐字突然变慢"的典型来源，且它**发生在网关之外**。

### 4.4 可做的（不改前缀）

诊断建议：下一个大上下文会话前发两发同前缀探针（同会话、同模型、间隔 < 1 h），
若两发都是零缓存，则问题在客户端前缀构造（时间戳 / 随机 id / 工具集重排）；
若首发有缓存后续为 0，则问题更可能在 TTL 或上游驱逐。**该探针需要新写一个只读
工具，本轮未实施**（见 §7 的未完成项）。

---

## 5. 吐字面：admission 排队不是瓶颈，上下文长度是

48h `first_text_ms`（仅含真正产出可见文本的轮次）：

| lane | n | p50 | p90 |
|---|---:|---:|---:|
| workbuddy-oauth | 38 | 3.5 s | 7.0 s |
| chatgpt-oauth | 28 | 3.1 s | 5.5 s |
| zhipu-coding-plan | 29 | 9.3 s | **30.5 s** |

`/healthz` 1h 遥测（同一结论）：`gpt-6.1-sol` 2.6 s、`gpt-6-luna` 3.0 s p50 / 7.6 s p90、
`deepseek-v4.1-flash` 2.1 s p50 / 7.0 s p90。admission 侧 `waited_ms` 观测到
个位数毫秒（p90 未超 30 ms 量级）。

**因此"吐字慢"的杠杆仍是 §4.4 的上下文与前缀稳定性，不是网关参数。**

---

## 6. 本轮结论（可直接复用）

1. **封号**：本栈 24h 零账号级信号；凭据 20/20 在役、冷却为空。社区封号潮
   **不得**外推为本栈已中招，**也不得**据此做预防性换号/清冷却/提并发。
2. **限流**：6004 的扩散来自"CN/INTL 双 realm 共享 `deepseek-v4.1-flash`"这一
   已登记设计；要收敛就必须拆 lane（契约变更，用户决策），否则保持现状。
3. **降智**：doctor `model_substitution` 的已审查变体对
   `gemini-3.8-flash-n <== gemini-3.8-flash-high` 与 `hy4-free <== hy4free`
   是上游回显基础名的稳定形态（覆盖 100%），**不是降级证据**；未出现新的未知对。
4. **缓存**：整体 88.9%；两个低值已逐条归因到探活人口与冷重启，**网关侧无可安全改进**。
5. **吐字**：瓶颈在上下文长度与前缀稳定性，不在 admission。
6. **代码**：本轮**未发现需要修复的本仓缺陷**，因此按 R5 不改代码。

---

## 7. 未完成项与建议（按收益/风险排序）

| 优先级 | 项 | 归属 | 需要什么 |
|---|---|---|---|
| P1 | 冷重启归因探针（§4.4） | 本仓新工具（只读） | 用户点头：新增一个诊断脚本 + 测试 |
| P2 | CN/INTL 拆 lane（§3.3 选项 A） | 契约变更 | 用户裁决；影响裸名路由与兜底语义 |
| P3 | 探活人口从 `gpt-6.1-sol` 分流（§4.2） | 客户端/Cockpit 配置 | 先确认是哪个消费方在打 `route=chat` 的 53/16 |
| — | 账号面动作 | 用户 | 无信号则不做 |

---

## 8. 回滚入口

本轮只有本文件是新增内容，回滚 = 删除本文件；无远端写入，无备份恢复需求。
---

## 9. 本轮实施（2026-11-15，用户授权连续修复）

用户授权"按推荐/建议连续自动修复和优化"。本轮落地两项，**远端零写入**：
只改仓库代码与文档；插件配置投影在代码提交后另行受控执行。

### 9.1 P1 已交付：`scripts/cpa_cache_reset_probe.py`

只读归因工具，按 `client_request_hash` 回溯会话，把冷重启分到
`rebind` / `model_switch` / `lane_switch` / `cache_eviction` / `unattributed`。
12 项测试（`tests/test_cpa_cache_reset_probe.py`）。真实 48h 日志结果：

```
turns=5363 turns_with_usage=1033 conversations=16 multi_turn_conversations=11
cold_conversations=7 restarts=13 restart_lost_tokens=2304731
cache_hit_ratio=0.9697 cache_eligible_turns=862 eligible_cache_hit_ratio=0.97
mechanisms=cache_eviction:4,model_switch:6,unattributed:3
```

**更正 §4.3**：上一节把冷重启主要归因为 `session-affinity-ttl` 重选凭据。
探针证明主力是**客户端会话内换模型**（6/13），`cache_eviction` 只有 4 次，
`rebind` 0 次（20 份凭据的 `instance_hash` 全为 `-`，无法证实重绑）。
网关侧对本类延迟无责。

新增 `eligible_cache_hit_ratio`：剔除探活/标题类小请求后的命中率。全体口径
把 82 次 53-token 探活计入分母，才把 `gpt-6.1-sol` 读成 82%。两个口径同时输出
是本次的判读契约。

### 9.2 P2 已决策并落地：`hy4free` 归 INTL 独用（方案 X）

**外部更正**：用户提供 WorkBuddy 面板截图显示 `Hy4` 与 `Hy3` 在 INTL 侧同样标注
`Free now`（0.00x）。此前"7 天 0 次 INTL 调用"被误读为"INTL 不可用"——**该推断错误**，
日志缺省只说明未调用。

**关键技术事实**：两个 realm 的上游 id 相同（`upstream served model "hy4-free" for
requested model "hy4free"`，137/137）。`models_cn`/`models_intl` 是按 realm 过滤
**候选凭据**的白名单，不是模型声明，因此**无法**拆出可独立冷却的 `hy4free-sg`；
可选动作只有"归属收窄"。

**落地**：

- `cpa_workbuddy_model_pins.ps1`：`ModelsCn` 默认 `deepseek-v4.1-flash,hy4free` →
  `deepseek-v4.1-flash`；`ModelsIntl` 不变。
- `cpa_provider_routes.json`：`plugin_routes[cn].models` 去掉 `hy4free`；
  `pooled_aliases` 移除 `hy4free`（只剩 intl 一个持有者，保留会触发
  `pooled-alias-not-duplicate` fail）。
- `docs/runbooks/cpa-workbuddy-plugin.md` §2.1 增补 2026-11-15 更正段。
- 新测试 `tests/test_cpa_workbuddy_realm_pins.py`：钉定默认值、realm 不相交性、
  manifest 与默认值一致、`hy4free` 不再是 pooled alias、`flash` 仍是。

**理由**：`hy4free` 是零成本回退。让 CN 的 10 份凭据只承载付费主力，免费回退留在
INTL 的 7 份凭据上，符合 `cpa-workbuddy-plugin.md:196`"行为特征 > 账号数量，
归因面干净"。

**代价（必须如实记录）**：CN 侧不再能以 `hy4free` 兜底。这是**行为收缩**，不是
纯粹优化。若 CN 主力频繁撞 6004 且需要免费回退，应回滚本条。

### 9.3 预演回执（远端零写入）

```
WORKBUDDY_MODEL_PINS_DRY_RUN current_cn=deepseek-v4.1-flash,hy4free
target_cn=deepseek-v4.1-flash current_intl=deepseek-v4.1-flash,deepseek-v4.1-flash-sg,hy4free
target_intl=deepseek-v4.1-flash,deepseek-v4.1-flash-sg,hy4free
```

只改 `models_cn`，`models_intl` 落点不变。`-Apply` 须在代码门禁通过后单独执行。

### 9.4 回滚

仓库：撤销本提交。远端（若已 `-Apply`）：按 pins 脚本输出的备份目录还原
`config.yaml`，或反向运行 `-ModelsCn "deepseek-v4.1-flash,hy4free"` 重投影。
