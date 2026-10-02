# BWG 网关每 IP 连接预算修复（6 → 12）

日期：2026-10-02。本地时间 Asia/Shanghai（UTC+08:00）。

## 结论

用户报告「同时执行 4 个会话任务时就报错 429」。归因结果：**该 429 由公网 nginx 的
`limit_conn cpa_cc` 发出，不是 CPA admission 冷却，也不是上游容量**。旧值 `6`
恰好等于 OAuth lane 的持有连接上限（`max_inflight 2 + max_pending 4 = 6`），
单个 lane 即可吃满整个 IP 的连接预算。本次将该值上调为 `12`。

## 证据链（全部只读采集）

### 1. 429 的形态定位到 nginx 连接层

`/var/log/nginx/cpa_gateway.access.log` 全量 267 条中仅 1 条 429：

```
183.236.101.21 method=POST route=responses status=429 request_time=0.692
upstream_status=- upstream_time=- bytes=169
limit_req=PASSED limit_conn=REJECTED retry_after=absent
```

三个字段共同定界：`limit_conn=REJECTED`（连接数超限）、`upstream_status=-`
（从未到达上游）、`request_time=0.692`（本地秒回）。`/var/log/nginx/error.log`
同一时刻（01:35:38 UTC）有对应行：

```
[error] 282768#282768: *15348 limiting connections by zone "cpa_cc",
client: 183.236.101.21, server: fq.sciman.top
```

### 2. 并发重建证明 4 个会话足以越界

按 `request_time` 反推每条请求的起止时刻并重建并发曲线：

- 01:35:38 UTC 瞬间有 **7 条连接同时在开**，第 7 条被拒（配置上限 6）。
- 观测窗口内峰值并发 = **7**，**≥6 并发的累计时长 = 30.4 秒**。
- 队列实例：采集时 `healthz` 为 `inflight=2, pending=3`，`ss` 显示
  **6 条 established** 连到 `127.0.0.1:8318`。

即：被 admission 排队的请求在等待期间**持续占用 nginx 连接**，因此
「4 个长流会话（12–44s）+ 一次重试/`/models` 探针」= 7 条 > 6。

### 3. 结构性冲突的算术

| lane | max_inflight | max_pending | 持有连接上限 |
|---|---|---|---|
| chatgpt-oauth | 2 | 4 | **6** |
| zhipu-coding-plan | 3 | 4 | 7 |
| deepseek-official | 3 | 4 | 7 |

- nginx 每 IP 预算（旧）= `6`
- OAuth lane 单独 = `6` ⇒ **一个 lane 即可吃满 IP 预算**，次要 lane 与
  `/v1/models` 探针无任何余量。
- 三 lane 理论合计 = `20`，故旧契约在结构上无法与 admission 自洽。

### 4. 排除项

- **VPS admission 无拒绝**：`journalctl -u cpa-admission --since "3 hours ago"`
  中 `lane_reject` 计数为 **0**；`healthz` 三 lane 均 `cooldown_remaining=0`、
  `half_open_probe=false`。
- **上游无异常**：同窗口 nginx 记录 `status=200` 381 条、`5xx=0`；
  `upstream_status` 与 `upstream_time` 正常随上游响应。
- **strict doctor 全绿**：本次改动前 `DOCTOR_CONTRACT_OK`，9 项
  `projection-drift` 全 MATCH。

### 5. 本机侧放大器（已另行修复）

本机 Direct API sidecar 的 `request-retry` 曾被上游生成器写回 `1`
（契约值 `0`），会产生同秒突发重试（01:24–01:25 观测到 10/13/9/10 条/秒），
每轮重试都会在首条仍持连时再开一条，加速触及连接上限。该回退已由本机
sidecar 的 v1.3.65 本地补丁（在二进制入口强制 `RequestRetry=0` 并桥接
`maxAccountConcurrency`）消除。

## 变更内容

`scripts/cpa_bwg_guardrails.ps1`（三处契约点同步）：

- doctor 断言与输出：`limit_conn cpa_cc 6;` → `limit_conn cpa_cc 12;`，
  `gateway-per-ip-concurrency=6` → `=12`。
- `-Apply` 投影的 `required` 指令清单同步为 `12`。
- `-Apply` 的 `ensure_nginx_directive` 锚点同步为 `12`。

`test_scripts.py`：doctor 契约测试新增对 `limit_conn cpa_cc 12;` 与
`gateway-per-ip-concurrency=12` 的断言，并说明取值依据。

`docs/runbooks/cpa-gateway.md`：记录新值与「连接预算必须大于单 lane 持有上限」
的判据，以及三 lane 合计 20 的收敛说明。

## 取值理由（12）

- 必须 > 单 lane 持有上限（6），否则一个 lane 独占 IP 预算。
- 覆盖 OAuth lane 的 6 + 一条次要 lane 的余量 + `/v1/models` 探针。
- 仍远低于三 lane 理论合计 20，保留「单 IP 突发与本地端口耗尽保护」语义，
  不构成对全部 lane 理论上限的解锁。
- `limit_req` 维度未改动：`rate=10r/s burst=10` 继续约束请求速率。

## 验证

- 受控测试：`pytest test_scripts.py -k "nginx_directive or doctor or gateway or
  guardrail"` → **12 passed, 25 subtests passed**。
- 全量：`pytest test_scripts.py test_cpa_admission.py` → 见下方结果行。
- 投影后复验：strict doctor `DOCTOR_CONTRACT_OK`、
  `gateway-per-ip-concurrency=12`、`nginx-syntax=OK`。
- 行为复验判据：4 并发长流期间 `cpa_gateway.access.log` 不再出现
  `limit_conn=REJECTED`；`limit_markers` 中 `PASSED/PASSED` 全程成立。

## 证据边界

- 本次未对 OAuth lane 发压测请求；连接预算的行为级结论来自
  **已发生的自然流量重建**（7 并发实测）与配置算术，不是合成压测。
- `natural_live_accepted`：未宣称。4 并发自然会话的长期稳定性需在真实使用
  窗口继续观察；若届时仍出现 `limit_conn=REJECTED`，说明预算或 lane 持有上限
  仍需继续调整，应带新的并发重建证据复审。
- 不做 provider 侧配额或风控变化的证明。

## 回滚

- 代码回滚：还原 `scripts/cpa_bwg_guardrails.ps1`、`test_scripts.py`、
  `docs/runbooks/cpa-gateway.md` 的本次改动。
- 远端回滚：使用本次 `cpa-guardrails-backup-*` 目录按 guardrail 事务恢复
  nginx 配置；不使用 Git 代替远端恢复。
