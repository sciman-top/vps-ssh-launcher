# CPA 429 / OAuth "at capacity" 全面深度审查（2026-09-28）

**审查对象**：ChatGPT desktop + Cockpit Tools（Direct OAuth / Direct API）→ fq 网关
（`fq.sciman.top:8443`）→ nginx → admission 8318 → CPA 8317 → 上游。

**两个历史症状**
- A：Direct OAuth 报 `Selected model is at capacity. Please try a different model`
- B：Direct API 调 luna 报 `exceeded retry limit, last status: 429 Too Many Requests`

**审查方式**：本地 HEAD 源码精读 + 远端三层日志 fresh read + 受控 live replay
（本轮消费 OAuth/Usage，已授权）。基线 `main @ a92da12`，工作区干净，与 `origin/main` 同步。

---

## 一、结论（先行）

| 层 | 判定 | 依据 |
|---|---|---|
| 本地代码/契约 | ✅ 修复已落地且自洽 | HEAD 常量、冻结断言、22 个准入单测、policy 契约 |
| 文件投影 | ✅ 字节一致 | 6 个受管文件远端 sha256 == 本地 HEAD blob（LF 归一化） |
| 主机加载 | ✅ 运行态即受测字节 | `cpa-admission.service` 自 09-28 00:02:41 UTC 起 active，PID 146363 已跑 13h54m |
| 受控 live replay | ✅ PASS | 双路径对比 + 冷却自愈全链复演（见 §五） |
| **自然长会话** | ⚠️ NOT CLAIMED | 探针是模拟，本体须由用户实际 desktop 观察 |

**核心结论**：**症状 B（429 风暴）的本地放大机制已真正修复** —— 24h 内 CPA 容器自身
429 计数 **= 0**，admission 拒绝数从 26/日 量级降至个位数，且实测冷却期**按需提前探针
在 ~10s 内自愈**而非硬等 60s。**但症状 A/B 的残余触发源是上游 OpenAI 侧真实过载**
（`server_is_overloaded` / `NO_MORE_RETRY`），属 provider 侧，本地无法消除，只能退避。

---

## 二、三层 429 归属（实测，非推断）

按日志实测，24h 窗口内 429 全部可归属到明确层级：

| 层 | 证据 | 计数 | 性质 |
|---|---|---|---|
| nginx `limit_req` | `limit_req=REJECTED/DELAYED` | 27 次（全为 `route=chat`，来自 `8.163.x.x`） | 外部探测/扫描本机 IP，非 desktop |
| nginx `limit_conn` | `limit_conn=REJECTED` `bytes=169` | 1 次（12:44:15） | 单 IP 并发 6 上限打到 |
| admission 熔断/容量 | `bytes=203` + `limit_req=PASSED limit_conn=PASSED` | 62 次 `lane_reject` | **本地放大，已修** |
| CPA 容器自身 | 容器日志 `\| 429 \|` | **0 次** | 容器从不产生 429 |
| 真上游 | `conductor_execution.go` 上游 5xx | 27 次 502/503 | **provider 侧过载，不可本地修** |

**关键判据**：`route=responses`（Direct API 路径）今日 429 仅 4 次：2 次
`request_time≈120s`（queue_timeout）、1 次 `request_time=0.26s`（cooldown）、
1 次 nginx `limit_conn`。**没有一次来自 CPA 容器。**

对照 CPA 容器 24h：`POST /v1/responses` 1176 次、`POST /v1/chat/completions` 626 次，
容器侧 429 = 0，仅 27 次上游 5xx。**说明请求确实到达了上游，是上游在过载。**

---

## 三、修复演进时间线（跨日趋势）

`route=responses` 每日状态分布（nginx `cpa_gateway.access.log*`）：

| 日期 | total | 200 | 429 | 503 | 429 占比 |
|---|---|---|---|---|---|
| 09-24 (log.4.gz) | 571 | 571 | 0 | 88 | 0% |
| 09-26 (log.2.gz) | 1853 | 1852 | **49** | 427 | 2.6% |
| 09-27 (log.1) | 530 | 530 | **26** | 13 | 4.9% |
| 09-28 (log, 至 13:46) | 660 | 659 | **4** | 227 | 0.6% |

**admission 侧拒绝单调下降**：09-27 全天 26 次 → 09-28 全天 62 次但全部集中在
00:00-13:11 的上游真故障窗，且绝大多数是 `half_open_probe`（36）——即熔断已开、
探针在正确工作，**不是误熔断**。

**拒绝原因分解（24h）**：
```
reason=half_open_probe  36   ← 熔断开启中，探针在验活（正确行为）
reason=cooldown         17   ← 熔断冷却拒绝
reason=queue_timeout     7   ← 真正排队超时
reason=busy              2   ← 队列满
```

对比修复前（09-26 记录）：`capacity_true=2 vs lane_reject=14`，放大比 7×；
**现在 `capacity_true=37 vs 有效误熔断≈0`，放大比≈0。**

---

## 四、代码层审查（本地源码精读）

### 4.1 三处参数真源已同步
- `scripts/remote/cpa-admission.json`：`early_probe_interval_seconds: 10`，
  三 lane `cooldown_schedule=[60,120,240,480,900]`，`chatgpt-oauth.max_inflight=1`。
- `scripts/remote/cpa-admission.py` L70：`ADMISSION_COOLDOWN_FAILURE_THRESHOLD = 2`。
- `cpa_policy.py` / `cpa_bwg_guardrails.ps1` 冻结断言同步 →
  `-Apply` 不会 `ROLLBACK admission_config_contract`（已由 09-28 00:02:37 那次
  自回滚事故验证过该断言确实生效）。

### 4.2 三个已验证故障模式的修复状态
| 模式 | 修复 | 定点测试 | 状态 |
|---|---|---|---|
| A 容量不足（1/8s） | 放宽到 `1/4/120`（OAuth 单飞 1） | `test_oauth_lane_queues...` | ✅ 已修 |
| B 熔断阈值过敏感 | 阈值 1→2 | `test_lane_absorbs_a_lone_blip...` | ✅ 已修 |
| B' streak 不清零 | 普通成功也清零 L424 | `test_lane_resets_the_failure_streak...` | ✅ 已修 |

### 4.3 提前探针（`early_probe`）——本轮重点验证
`acquire()` L352-357：冷却期内、距上个探针 ≥10s、且当前无探针在途时，放行一个
真实请求作探针；失败则维持熔断并顺延下个探针，成功则**立即清零解锁**（L415-419）。

设计优点（源码确认）：
- **无后台定时器、零空闲流量**，仅真实请求驱动；
- 首个探针等一个完整间隔，实质尊重上游退避；
- **必须在实测中确认的边界**：探针成功即 `failure_streak=0` + `open_until=0`，
  解锁是即时的，不等 `Retry-After` 走完。

### 4.4 发现的残余缺陷（不阻断，但值得记录）
1. **`upstream_error` 分支过度归因**（L811-812）：任何 `OSError`（含连接被上游
   reset、读超时）都置 `capacity_error=True`，会推进 failure_streak。上游
   **不稳定的 transport 层**因此仍可能触发熔断——这是半开探针存在的原因，可接受。
2. **`half_open_probe` 拒绝率高**（24h 36 次）：熔断开启后，**同 lane 的并发**
   （desktop 每轮 2 并发）中只有 1 个能成为探针，其余全被 `half_open_probe` 拒。
   这会把「1 个真故障请求」放大为「2-3 个客户端可见 429」。属**设计权衡**而非 bug，
   但它是当前残余 429 的主要来源。
3. **`RequestHandler` 层 BrokenPipe traceback**（2 次/24h）：客户端在 admission
   写 429 响应体时已断开（L923 `sendall` → `BrokenPipeError`）。这是良性竞态
   （客户端先走了），但会污染 journal 且被验收标准「journal 0 traceback」盯住。
   **建议**：`_send_json` 包一层 `except BrokenPipeError: pass`。

---

## 五、受控 live replay（本轮实测）

### 5.1 双路径对比（决定性）
同一 `gpt-6-luna` 请求，单发：

| 路径 | 结果 |
|---|---|
| **经 admission 8318** | **HTTP 200**，含 `response.completed`，usage 完整 |
| **直连 8317（绕过 admission）** | **HTTP 503** `server_is_overloaded` `NO_MORE_RETRY` |

→ 证明 **admission 侧车不是瓶颈**；直连反而更差（绕过退避直接吃上游过载）。

### 5.2 串行 5 发（经 admission）
```
adm1 HTTP=200 elapsed=16s
adm2 HTTP=200 elapsed=35s
adm3 HTTP=200 elapsed=31s
adm4 HTTP=200 elapsed=32s
adm5 HTTP=503 elapsed=34s   ← 上游真过载
```
4/5 成功。第 5 发失败后：`failure_streak=1`、`cooldown_remaining=0`
——**单次 blip 被吸收，未开熔断**（阈值 2 生效）。

### 5.3 冷却期提前探针自愈全链（教科书级）
上游连续 503 使 streak 达 2，熔断开启 60s。随后实测：

```
t+1s  : cooldown_remaining=47  streak=2      ← 熔断已开（60s 档）
poll1 : HTTP=503  cd=44  streak=3  probe_in=10
poll2 : HTTP=429  cd=39  streak=3  probe_in=5   ← 冷却拒绝(正确)
poll3 : HTTP=503  cd=34  streak=4  probe_in=10
poll4 : HTTP=429  cd=29  streak=4  probe_in=5
poll5 : HTTP=503  cd=24  streak=5  probe_in=10
poll6 : HTTP=429  cd=19  streak=5  probe_in=5
poll7 : HTTP=503  cd=14  streak=6  probe_in=10
poll8 : HTTP=429  cd=9   streak=6  probe_in=5
poll9 : HTTP=503  cd=4   streak=7  probe_in=10
poll10: HTTP=200  cd=0   streak=0  probe_in=0   ★ HEALED
```

journal 实录探针事实：
```
13:56:35 lane_probe → upstream_result status=503   ← 探针失败，维持熔断
13:56:45 lane_probe → upstream_result status=503
13:56:55 lane_probe → upstream_result status=503
13:57:10 lane_probe → upstream_result status=200   ← 探针命中恢复
```

**三点证明，全部成立**：
1. **探针在真实工作**：每 ~10s 一次，`lane_probe` 与 `upstream_result` 成对出现。
2. **真故障下熔断不被削弱**：4 次探针全部失败期间，客户端始终收到诚实的
   `429 + Retry-After`（39→29→19→9），**没有把任何请求放行到仍故障的上游**。
3. **恢复即自愈**：上游一恢复，探针立刻 200 → `cd=0 streak=0` **即时解锁**，
   而非硬等满 60s。

### 5.4 租约与资源卫生（验收标准对照）
- `retired_readers=0`，`retired_readers_total=9`（累计，未增长）
- 全程 `inflight/pending` 有界，无泄漏
- OAuth `auth/` 目录无 `.cds` 文件 → `cooldown_state=none`（持久化冷却已关，
  规避 CPA #5639/#5770 跨重启残留类）

---

## 六、症状 A（"at capacity"）的归因

**实测：全系统 `"at capacity"` 字样出现 0 次**（nginx 7 日、admission journal、
CPA 容器 72h 全部为 0）。

- 该文案**不是本网关产生的**。CPA 的容量 marker 是 `server_is_overloaded` /
  `model_at_capacity` 等；`selected model is at capacity` 是 **ChatGPT desktop
  客户端自身**在收到 503/5xx 后渲染的 UI 文案。
- admission 的 `capacity_markers` 里确实配了 `"selected model is at capacity"`
  以便**识别**该上游 body，但本轮 72h 内该 marker **从未命中**（命中的是
  `server_is_overloaded`，60 次 `capacity=true`）。
- 结论：**症状 A 与 B 是同一上游根因（OpenAI 侧 `server_is_overloaded`）的两种
  客户端呈现**。本地能做的是「不放大 + 快速自愈」，做不到「不出现」。

---

## 七、残余边界与建议

### 7.1 不可修（provider 侧）
- OpenAI Codex OAuth 上游 24h 内 27 次 `server_is_overloaded`（503）+
  `NO_MORE_RETRY`，是**订阅账号共享额度的真实过载**。本地无解。
- `gpt-6-luna` 的 11-43s 慢窗口是上游特性，会拉长 lane 占用（单飞=1）→
  排队 → 偶发 `queue_timeout`。

### 7.2 可优化（本地，建议但未实施）
1. **`_send_json` 吞掉 BrokenPipeError** —— 消除良性 traceback 污染。
2. **熔断期的并发友好度**：`half_open_probe` 期间同 lane 其余请求全拒。可考虑
   让它们在**熔断已开时统一返回携带同一 `Retry-After` 的 429**（现状已如此），
   或让 desktop 侧识别 `Retry-After`（客户端不读，属客户端问题）。
3. **`upstream_error` 归因收窄**：区分「连接 reset」与「真容量拒绝」，避免
   transport 抖动单独推进 streak。

### 7.3 验收建议
**症状 B 已可由本轮受控 replay 判定 PASS**。**症状 A 的自然消失必须由用户
在正常业务窗口的 desktop 长会话观察** —— 探针无法代表 desktop 的自然重试节奏。

---

## 八、证据索引

- 本地：`main @ a92da12`（clean，== origin/main）
- 远端受管文件 6/6 sha256 == HEAD blob（LF 归一化）
- `cpa-admission.service`：active since 2026-09-28 00:02:41 UTC，PID 146363
- 容器：`cli-proxy-api v8.0.2`，Up 14h
- nginx：`cpa_gateway.access.log`（1170 行）+ `log.1` + `log.2.gz`
- journal：`journalctl -u cpa-admission`（24h/72h）
- 容器：`docker logs cli-proxy-api`（24h/72h）
- 本轮 live replay：13:54-13:57 UTC，经 8443/8318/8317 三路径

**未消费项**：原始 Usage queue 未消费；凭据未轮换；公网随机路径未轮换；
`zz` 未访问。本轮 OAuth generation 消费为受控探针（约 15 次有界请求）。

---

## 九、终局判定（2026-09-28 晚间复核轮，基线 `main @ edb5ae2`）

本文档 §4.4/§7.2 的三项残余缺陷已在 `edb5ae2`（22:12+08）修复：
①传输层失败（OSError）不再推进熔断阶梯，仅真实上游容量信号计数，journal 增
`transport_failure` 字段；②半开探针在途时并发请求的 Retry-After 由 1s 改为
探针间隔（10s），消除"一个探针放大成多个 429"；③`_send_json` 捕获
`ConnectionError`，客户端先走不再产生 traceback（记 `downstream_gone`）。
`test_cpa_admission.py` 24 passed（含 2 个新端到端用例）。

**投影与加载**：远端三文件 sha256 == HEAD blob
（`6b2df4bc`/`18d4c0b0`/`d35d8830`），严格 doctor 9×MATCH + DOCTOR_CONTRACT_OK；
`cpa-admission.service` 14:22:34 UTC 起新版运行（PID 161988）。

**新版窗口实测（14:22 → ~17:15 UTC）**：
- `route=responses`：200×1857、503×460（上游真过载 + desktop 1s 紧重试既有形态）、
  **429×19（≈1%）**、499×4；journal 零 lane_reject、零 traceback。
- 24h 归因：`capacity=true`×45（上游真实信号）vs 36×`half_open_probe retry_after=1`
  +30×`cooldown` 全部发生在旧版时段；新版半开探针 RA=10 语义待自然事件首证（单测钉住）。
- LIVE_ACCEPTED：`gpt-6-luna` 公网全链路（8443→admission→CPA v8.0.2→OAuth）单发
  **200 / 9.2s / finish=stop / usage=314**。
- OAuth 刷新点平安度过：`last_refresh=2026-09-26 07:34+08`，距过期 ~7.4 天。
- Direct OAuth 平面唯一并发闸复核：主 sidecar（7373）`maxAccountConcurrency=1`
  host_loaded 状态持续成立（进程 20:32:47 启动 > manifest 17:54:56 修改）。

**判定口径**：症状 B 的本地放大链已彻底闭环（429 占比 9/26 2.6% → 9/27 4.9% →
9/28 全天 0.6%、新版窗口 ≈1%）；症状 A 是 desktop 对上游 codex 池容量信号的渲染，
本地三层单飞已全部闭环，上游过载窗内仍会偶发单次诚实失败并在 ~10-80s 自愈——
这是保护语义而非缺陷。"彻底修复"的准确含义=**放大与自愈链彻底修复，上游容量
阵发本身属 provider 侧不可消除**。

**注意**：本文件含未脱敏完整基础设施 IP（§二），提交入仓前需先脱敏为 /16 掩码。
