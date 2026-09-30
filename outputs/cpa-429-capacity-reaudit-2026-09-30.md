# CPA 429 / "at capacity" 复审（2026-09-30 22:40，v8.0.4 + 11 裸名目录）

**审查对象**：ChatGPT desktop + Cockpit Tools（Direct OAuth / Direct API）→
`fq.sciman.top:8443` → nginx → admission 8318 → CPA 8317 → 上游。

**两个历史症状**
- A：Direct OAuth 报 `Selected model is at capacity. Please try a different model`
- B：Direct API 调 luna 报 `exceeded retry limit, last status: 429 Too Many Requests`

**基线**：`main @ d42ce17`（工作区仅两个未跟踪 outputs 文件）；远端容器
`v8.0.4`（`restart=0`）；本轮消费 OAuth/Usage（用户显式授权）。
**前一轮**：[`cpa-429-oauth-capacity-audit-2026-09-28.md`](cpa-429-oauth-capacity-audit-2026-09-28.md)（v8.0.2 时代）。

---

## 一、结论（先行）

| 层 | 判定 | 依据 |
|---|---|---|
| 本地代码/契约 | ✅ 修复在位且自洽 | `transport_failure` 不推进阶梯、探针 RA=10s、`downstream_gone`、0 traceback |
| 文件投影 | ✅ 字节一致 | 9/9 `drift=… MATCH`（含 admission 三件套） |
| 主机加载 | ✅ 运行态即受测字节 | 容器 14:38:32Z 重建后 `restart=0`；admission 与 CPA 同批重启 |
| 目录契约 | ✅ | `MODEL_IDS_UNKNOWN=none`；`MODEL_IDS` = 11 裸名（= 清单） |
| 受控 fixture 验收 | ✅ `ACCEPTANCE_RESULT=PASS` | 过载 1 次上游 → 冷却 **0 次**上游 → 62s 同进程自愈 200 |
| 公网真实验收 | ✅ 双模型 200 | `gpt-6-luna` / `gpt-6.1-sol` 流式 200，`response.completed` |
| 症状 B 的**用户路径** | ✅ 0 次 429 | `route=responses`（用户 IP）24h 内 429 = **0** |
| 症状 A | ⚠️ 上游侧，本地不可消除 | 网关侧 `at capacity` 字面量 0 次；上游 `server_is_overloaded` 13 次 |

**核心结论**：**症状 B 的 429 在用户路径上已消失**——24h 内全部 38 个 429 都来自
**单一客户端** `8.163.x.x`（= `D:\CODE\qq-codex-bot` 的部署主机 `8.163.41.198`，
即操作者自己的 QQ 机器人，**不是第三方也不是 desktop**）请求**已退役裸名
`gpt-6-sol`**，命中**共享** OAuth lane 后触发上游容量失败并打开 lane 冷却。
**catalog11（`d42ce17`，14:38Z 投影）把 `gpt-6-sol` 移出 lane 后，`lane_reject` 归零。**
症状 A 是上游 OpenAI `server_is_overloaded` 的客户端文案，本地三层只能「不放大 + 快速自愈」。

---

## 二、症状 B 的归因链（实测，非推断）

### 2.1 全部 429 的形状与来源

24h（当前 access log，00:00→14:45Z）状态分布：
`200×404, 400×18, 404×75, 401×17, 502×13, 503×6, 429×38, 499×3`

38 个 429 的**全部**特征（逐条统计）：

| 维度 | 值 |
|---|---|
| `route` | `chat` × 38（`responses` **0**） |
| 客户端 | `8.163.x.x` × 38（单一 /16） |
| 响应体 | `bytes=203`，`upstream_time=0.002`（本地快速失败） |
| `Retry-After` | `seconds`（有值） |
| nginx 限流 | 无 `REJECTED`（`PASSED/PASSED 371`、`DELAYED/PASSED 145`） |

→ 形态即 admission 冷却返回，**不是** nginx 限流、**不是** 上游透传。

### 2.2 admission journal 交叉验证

```
lane_reject 24h = 39    ← 33 reason=half_open_probe + 6 reason=cooldown
  全部 model=gpt-6-sol  ← 无一条落在 gpt-6-luna / gpt-6.1-sol
capacity=true 24h = 23
transport_failure=true = 2
Traceback = 0            ← BrokenPipe/ConnectionError 修复生效
```

上游结果按模型拆：

| 模型 | 结果分布 |
|---|---|
| `gpt-6-luna` | **200×60（capacity=false）+ 200×1（capacity=true）** |
| `gpt-6-sol` | 200×334、400×11、502×13(capacity=true)、503×7(capacity=true)、502×2 |

→ **luna 自身 61 次里 60 次干净成功**；容量失败集中在 `gpt-6-sol`。

### 2.3 机制：退役名污染共享 lane

`gpt-6-sol` 当时仍在 `chatgpt-oauth` lane 的 models 里。第三方客户端持续用
chat completions 打它 → 上游对这条已不被 OAuth 账号服务的名字回 502/503
（带容量标记）→ 连续 2 次即打开**整条 lane** 的冷却 → 冷却/半开探针窗内的并发
请求拿到 `429 + Retry-After`。第三方客户端自己重试 → 命中同一冷却窗 → 它看到的
就是 `exceeded retry limit, last status: 429 Too Many Requests`。

### 2.4 catalog11 之后的实测（决定性）

`d42ce17` 把 `gpt-6-sol` 从 lane 移除（lane models 收敛为 `gpt-6-luna` / `gpt-6.1-sol`），
14:38Z 投影 + 重启。之后：

```
lane_reject（>=14:38Z）= 0
gpt-6-sol    → lane=passthrough  status=400 capacity=false   ← 不再进 lane
gpt-5.6-terra→ lane=passthrough  status=400 capacity=false
gpt-6-luna   → lane=chatgpt-oauth status=200 capacity=false
gpt-6.1-sol  → lane=chatgpt-oauth status=200 capacity=false
```

→ **退役名不再能打开共享 lane 的冷却**，这是本轮相对 09-28 审计的关键增量。

---

## 三、症状 A 的归因（与上游/社区一致）

- **网关侧 `at capacity` 字面量 24h = 0 次**（nginx access log 与 CPA 容器日志均为 0）。
  admission 的 `capacity_markers` 里配了它，只是用来**识别**，从未命中。
- CPA 容器 24h 实际容量标记：`server_is_overloaded` × **13**；容器自身 429 = **0**。
- 社区与 Codex 源码核对（[iqilian](https://iqilian.com/learn/codex-model-at-capacity/)）：
  `Selected model is at capacity. Please try a different model.` 是 **Codex 的固定文案**，
  对应服务端错误码 `server_is_overloaded`（HTTP 503 或流中错误事件），
  **Codex 对它不自动重试**；它不是额度耗尽、也不是网络问题。
- [CLIProxyAPI #5586](https://github.com/router-for-me/CLIProxyAPI/issues/5586)：
  单账号 + CPA 下大量用户同时报告同一文案，**归因为上游过载/风控**；
  有用户实测**换出口节点（LA→CHI）显著改善**，而本 VPS 出口正在 LA。

**结论**：症状 A 与 B 是**同一上游根因的两种客户端呈现**；本地能做的是
「不放大 + 快速自愈」，做不到「不出现」。

---

## 四、受控实战验收（本轮）

### 4.1 隔离 fixture 过载周期（未触生产 OAuth）

`unshare --mount --net --fork` + bind-mount 到 `/opt/cliproxyapi`，fixture 带
`FIXTURE_ONLY` 与独立 netns（harness 双重自检）；清单 sha `6fb56aa3…` = 已部署。

```
{"stage":"overload","status":200,"overload":true,"upstream_calls":1}
{"stage":"cooldown","status":503,"upstream_calls":0}
{"stage":"waiting","seconds":62}
{"stage":"recovered","status":200,"completed":true,"upstream_calls":1,"same_process":true}
{"stage":"actual_health","exit":0,"result":"HEALTH_OK"}
ACCEPTANCE_RESULT=PASS
```

- 过载**只打 1 次上游**（无放大）；冷却期 **0 次**上游调用；
- 62s 后**同进程**自愈 200（非重启）；
- `bare_diag` = 11 裸名目录；`sol_diag` = `200 gpt-6.1-sol stop`；
- 四个 updater 场景（start_fail / model_exposure / transient / success）行为符合预期。
- 结束后按准确路径删除，远端无残留 fixture 目录与进程。

### 4.2 公网真实流式（8443→nginx→admission→CPA→OAuth）

```
gpt-6-luna  : status=200 headers_ms=1151 first_event_ms=1152 total_ms=11421 events=20 completed=true
gpt-6.1-sol : status=200 headers_ms=558  first_event_ms=558  total_ms=10126 events=20 completed=true
```

key 与随机路径**只在远端读取、不回传**。

---

## 五、残余边界与建议

### 5.1 不可修（provider 侧）
- 上游 `server_is_overloaded` 是订阅账号共享额度的真实过载，本地无解。
- 社区实测**出口区域影响显著**（LA 最差）。若症状 A 持续，可考虑评估出口区域。

### 5.2 需要决策（本地，非本次改动）
1. **操作者自己的 bot 消费者 `8.163.x.x`**（`D:\CODE\qq-codex-bot`，
   部署主机 `8.163.41.198`）：它持有效 key，24h 内持续请求
   **已退役裸名**（`gpt-6-sol` 416 次、`gpt-5.6-terra` 161 次）。现在拿到 400 而非 429，
   但它的 **sol/luna 两套 preset 因此不可用**。退役名迁移在 `qq-codex-bot` 侧执行
   （`gpt-6-sol` → `gpt-6.1-sol`），**必须投影到该 VPS 才会生效**——仓库改动本身不改运行态。
   注意 `gpt-6.1-sol` 与 `gpt-6-luna` 都是本机 CPA 的 **OAuth lane 名**，
   即 bot 的 sol/luna 两套 preset 会与 desktop 共用唯一 ChatGPT Plus 账号
   （lane `max_inflight=2`）；这是 2026-09-30 操作者确认保留的设计。
2. **桌面 sidecar 目录仍陈旧**：`codex_model_providers.json` 的 `fq.sciman.top`
   条目仍是 15 个**旧**裸名（含 6 个已退役：`gpt-6-sol`、`gpt-5.6-terra`、
   `gpt-6-sol-input`、`gpt-6-sol-cii`、`deepseek-v4-pro`、`gpt-5.6-luna`），
   且缺 `gpt-6.1-sol-input` / `gpt-6.1-sol-91`。**桌面仍能选中已失效的名字。**
   投影发生在 Cockpit 启动时 → 需重启 Cockpit 才刷新。
3. **Direct OAuth 的 local-access sidecar 被硬编码 `stream-bootstrap-buffering: true`**
   （上游 issue #2658 附带疑问）：该开关把响应头推迟到上游产出首个生成 token（实测约 +10s），
   单凭据池下没有可换凭据，收益为零。**这是本地链路里唯一仍可改善的性能项**，
   但需上游支持关闭方式（手改配置每次启动被覆盖）。
4. **Cockpit sidecar SSE Flush 补丁仍必需**：上游 PR
   [#2659](https://github.com/jlcodes99/cockpit-tools/pull/2659) 仍 **open**，
   官方 v1.3.62 / v1.3.63 实测**仍未修**；每次 Cockpit 更新都会覆盖本机补丁，
   需用 `PROBE_ASSERT=1` 探针（exit 0/1）检测后一键重打。

### 5.3 判据纪律（沿用）
- 目录条数**不可**作验收判据：`/v1/models` 随上游可用性增减；判据是
  「目录 ⊆ 清单」+ `MODEL_IDS_UNKNOWN=none` + `config.yaml` sha 前后比对。
- `429` 必须**分层归因**（nginx 限流 / admission 冷却 / CPA / 上游），
  且必须看 journal 的 `reason=` 与 `model=`，不能只看 nginx 状态码。

---

## 六、证据索引

- 本地：`main @ d42ce17`（clean）
- 本轮 doctor：`outputs/doctor-review-20260930-2240.txt`（`DOCTOR_CONTRACT_OK` / `EXIT=0`）
- 上一轮 doctor：`outputs/doctor-postapply-catalog11-20260930.txt`
- fixture 验收：本轮 `ACCEPTANCE_RESULT=PASS`（与 `outputs/acceptance-full-20260930.txt` 同判据）
- 公网探针：`outputs/live_public_acceptance_20260930.py`
- 远端：`journalctl -u cpa-admission`、`/var/log/nginx/cpa_gateway.access.log`、
  `docker logs cli-proxy-api`（24h）
- 上游：CLIProxyAPI #4327 / #5529 / #5586；cockpit-tools #2658 / PR #2659

**未消费项**：usage queue 未消费；凭据未轮换；公网随机路径未轮换；`zz` 未访问。
本轮 OAuth generation 消费 = 2 次公网受控请求（luna / 6.1-sol）。

**IP 已按 /16 掩码**。本文件不含凭据。
