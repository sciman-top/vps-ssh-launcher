# B 受控实战验收：4/6/12/13 并发真实长流的 429 分层定界

日期：2026-10-02。本地时间 Asia/Shanghai（UTC+08:00）。
执行窗口：02:19–02:26 UTC。远端：`fq.sciman.top:8443`，nginx `limit_conn cpa_cc 12`（已投影）。

## 结论（推翻并修正了 A 之前的归因）

**429 的真正来源不是 nginx 连接层，而是 admission 的 `chatgpt-oauth` lane 队列上界。**
`nginx limit_conn cpa_cc` 从 6 提到 12（乃至当时工作区正在准备的 20）**都不解决该 429**，
因为 nginx 连接层在 N≤13 的真实并发下**从未触发**（`limit_conn=REJECTED` 恒为 0）。

| 并发 N | `limit_conn=REJECTED` | admission `reason=busy` 429 | 成功数 | 结论 |
|---|---|---|---|---|
| 4 | 0 | 0 | 4/4 | ✅ 全通过（用户报告的场景） |
| 6 | 0 | 1 | 5/6 | 边界（第 6 条已触顶） |
| 12 | 0 | 6 | 5/12 | ❌ 大量 429 |
| 13 | 0 | 6 | 5/13 | ❌ 大量 429（另有 2 条 limit_req REJECTED） |

## 实验方法

- 端点：`POST https://fq.sciman.top:8443/<prefix>/v1/responses`
  （`--resolve` 到 127.0.0.1，走完整 nginx → admission → CPA 链路）。
- 模型：`gpt-6.1-sol`（属 `chatgpt-oauth` lane）。
- 载荷：`{"input":"Count slowly from 1 to 40, one number per line.","max_output_tokens":300}`。
- 每轮开始前记录 access.log 行号，结束后按该偏移切出段并统计。
- 脚本：`outputs/b-concurrency-probe.sh`。

## 证据

### N=4（用户报告的场景）

```
[1]http=200 t=4.07  [4]http=200 t=7.61  [3]http=200 t=10.70  [2]http=200 t=11.31
limit_conn: 6 PASSED, 0 REJECTED    status: 全 200
```
⇒ **12 预算下，4 并发全通过。**

### N=12（阈值）

```
[2]429:0.58 [1]429:0.69 [12]429:0.77 [6]429:0.88 [5]429:0.99 [11]429:1.07 [9]429:1.18
[3]200:8.47 [4]200:19.04 [7]200:27.96 [8]200:36.50 [10]200:43.21
limit_conn: 15 PASSED, 0 REJECTED
upstream_status: 7×200, 8×429
```
⇒ 429 全部来自 admission，**连接层 0 拒绝**。

### N=13（连接层越界测试）

```
limit_conn: 12 PASSED, 2 "-", 0 REJECTED
limit_req: 10 DELAYED, 2 PASSED, 2 REJECTED
upstream_status: 6×200, 6×429, 2×"-"
```
⇒ 即使 13 并发，**连接层仍 0 拒绝**；`limit_req DELAYED` 把请求串行化，
把 nginx 内的瞬时连接数压在 12 以下。

### admission journal 直接证据（N=12 窗口）

```
INFO lane_reject lane=chatgpt-oauth model=gpt-6.1-sol reason=busy retry_after=10 waited_ms=0
INFO lane_reject lane=chatgpt-oauth model=gpt-6-luna  reason=busy retry_after=10 waited_ms=0
INFO upstream_result lane=chatgpt-oauth model=gpt-6.1-sol status=200 capacity=false waited_ms=180
INFO upstream_result lane=chatgpt-oauth model=gpt-6.1-sol status=200 capacity=false waited_ms=8235
INFO upstream_result lane=chatgpt-oauth model=gpt-6.1-sol status=200 capacity=false waited_ms=93354
```

- `reason=busy` **15 条，`cooldown` / `queue_timeout` 0 条**（近 15 分钟）。
- 成功请求 `waited_ms`：180 / 8235 / 18704 / 27582 / 36021 / 42930 / 69127 / … / **93354**。

## 机制

`cpa-admission` 的 `chatgpt-oauth` lane 容量 = `max_inflight 2 + max_pending 4 = 6`。

- 前 6 条：2 条立即执行，4 条进 pending 队列**排队等待**（实测等 5–93 秒）。
- **第 7 条起：`reason=busy` 立即拒绝**（`waited_ms=0`），带 `retry_after=10`。
- 该 429 的 nginx 侧形态：`status=429 upstream_status=429 bytes=203
  upstream_time≈0.001 limit_conn=PASSED retry_after=seconds`
  —— 即「已到 CPA/admission，被 lane 容量拒」，与连接层拒绝
  （`upstream_status=- limit_conn=REJECTED`）**形态完全不同**。

## 为什么之前归因到 nginx

`nginx limit_conn cpa_cc` 的旧值 **6** 与 admission OAuth lane 容量 **6** 数值巧合相同，
且两者都会在「并发数」上先于上游触顶。2026-10-02 早期只抓到 `limit_conn=REJECTED`
的少数样本（6 条，多为投影前），因而把主因归给了 nginx 连接预算。

**B 实验用 N=4/6/12/13 的梯度定界后，两者被清晰分离**：nginx 层在 N≤13 下 0 拒绝，
而 admission 层在 N≥6 起持续 `busy`。

## 证据边界

- 本次为**合成压测**（自发请求），消耗了 OAuth lane 配额；
  结论限于「并发 → lane 容量拒绝」这一因果，不代表长期稳定性。
- 未对 provider 侧配额/风控做证明。
- 连接预算提升（6→12，工作区在准备的 20）**对本次 429 无效**；
  它只提高 nginx 层阈值，不改变 admission 6 槽上界。
- 未宣称 `natural_live_accepted`。

## 后续方向（待决，未执行）

1. **提高 admission OAuth lane 容量**（`max_inflight` 2→3/4，或 `max_pending` 4→8）：
   直接针对 429 真来源。代价是对上游 OAuth 账号并发压力上升（可能触发上游容量失败）。
2. **客户端侧减少并行**：确认 desktop 是否对单会话发出多个并行 `responses` 请求
   （流式 + 重试 + 探针），若是则修客户端的并行度更安全。
3. **保持 nginx 预算 ≥ admission lane 容量**：避免连接层先于 admission 拒绝
   （这是 6→12 的有效部分，但非根因）。
   **注意**：`limit_conn` 若小于 lane 容量，会掩盖 admission 的真实排队行为；
   若远大于，则 nginx 不再是有效闸门。
