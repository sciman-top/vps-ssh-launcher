# 2026-10-01 BWG CPA admission 半开探针队列修复与回放证据

## 目标与范围

- 目标：消除 BWG CPA admission 在半开探针进行时把正常请求立即转成快速 `429` 的放大路径，并确认 Direct API 经过 `10909 -> fq.sciman.top -> BWG admission -> CPA` 的最小真实链路。
- 范围：仅 `bwg`；保留公网 Nginx、8443、随机路径、现有凭据与 OAuth 文件；不触碰 `zz`，不轮换 key，不增加上游重试，不做压力测试。
- Direct OAuth（14185）本轮只做存活检查，未消费 OAuth 生成请求；因此本文不把 Direct API 结果外推为 Direct OAuth 的自然会话验收。

## 修复

提交：`1ffcd45`（`修复 admission 半开探针期间的 429 放大`）。

修改集：

- `scripts/remote/cpa-admission.py`：半开探针在途时复用已有有界 FIFO pending 队列；保持 `max_pending=4`、`queue_timeout_seconds=120`；仅队列满或排队预算耗尽才返回 `busy` / `queue_timeout` 429。
- `test_cpa_admission.py`：覆盖探针成功后排队请求继续入场，以及探针失败后按正常 queue timeout 结束。
- `docs/runbooks/cpa-gateway.md`：同步 admission 行为和错误归因说明。

该修复不提高 OAuth 并发、不改变模型路由、不增加 CPA 或客户端重试；它只移除“一个长探针导致一批毫秒级 429”的本地放大点。

## 仓库验证

- `python -m pytest -q test_cpa_admission.py`：`37 passed`。
- `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1 -Profile Full`：`242 passed, 1 skipped, 260 subtests passed`。
- Bandit 通过（仅保留既有 `B507` `nosec` warning）；Ruff、格式检查和 mypy 均通过。
- `git diff --check` 通过。

## BWG 投影与运行态

- 入口：`scripts/cpa_bwg_guardrails.ps1 -Profile bwg -Apply`。
- 备份：`/root/cpa-guardrails-backup-20261001T133257.946659289Z`。
- 远端 admission 源 SHA-256：
  `1703addbec40b776f51f5187fd2df67328646038ca80d027c03857f47d803cc7`。
- `cpa-admission.service`：`active/running`、`NRestarts=0`、`MainPID=264252`，启动时间 `2026-10-01 13:33:02 UTC`。
- CPA：`eceasy/cli-proxy-api:v8.0.8`，容器运行且重启计数为 0。
- 最后一次 strict doctor（`2026-10-01 13:51:57 UTC`）：`DOCTOR_CONTRACT_OK`；`cpa-admission.py`、配置、unit、Nginx 投影全部 `MATCH`；`request-retry=0`；`stream-bootstrap-buffering=false`；8317/8318 仍为 loopback；8443 仍为唯一公网入口；随机路径及 401/404/404 合同通过。

## 受控 Direct API 回放

每个模型仅一次请求，`attempt=1`、自动重试为 0；通过条件包含 HTTP 200、非空 SSE、`response.completed`，而不是只看 HTTP 状态。

| 模型 | 本地 10909 结果 | BWG admission / Nginx 同窗 | 结论 |
| --- | --- | --- | --- |
| `gpt-6-luna` | HTTP 200；首个 data `874ms`；总耗时 `4628ms`；`response.completed`；无 error/incomplete | journal `2026-10-01 13:45:26.665Z`：`status=200 capacity=false waited_ms=0`；Nginx `request_time=4.426s`、`upstream_status=200`、`limit_req=PASSED`、`limit_conn=PASSED` | PASS |
| `gpt-6.1-sol` | HTTP 200；36 个 data 事件；TTFB `4.44s`；总耗时 `14.81s`；`response.completed`；无 error/incomplete | journal `2026-10-01 13:49:46.463Z`：`status=200 capacity=false waited_ms=0`；Nginx `request_time=14.600s`、`upstream_status=200`、`upstream_header_time=4.218s` | PASS |

14185 存活检查返回预期 `401`，证明 Direct OAuth listener 仍在服务；本轮没有把它作为 OAuth 生成验收。

## 半开探针放大检查

从服务启动后的新鲜 journal 窗口（`2026-10-01 13:32:50 UTC` 起）筛选 `lane_reject`、`half_open_probe`、`queue_timeout`、`reason=busy`、`downstream_gone` 和 `lane_probe`：

- 没有出现新的 `lane_reject`、`half_open_probe` 快速拒绝、`queue_timeout`、`busy` 或 `downstream_gone`。
- 出现的 `lane_probe` 均随后得到上游结果，说明探针仍工作，但没有再把并发到达请求直接放大成毫秒级 429。
- 最后一次 doctor 的最近 1 小时统计没有 429；24 小时累计 `429=242` 是包含部署前历史的窗口，不能拿来证明修复后仍有同样数量的新故障。

## 仍存在的上游边界

同一新鲜窗口仍记录到真实 provider 容量波动：

- `13:45:42.600Z`：`gpt-6-luna status=503 capacity=true retry_after=present`；Nginx 同窗 `503`、`request_time=0.212s`、`upstream_status=503`。
- `13:47:44.079Z`：再次出现 `503 capacity=true retry_after=present`；随后 `lane_probe` 成功恢复。

这些是上游返回的容量失败，不能由本地 admission、Nginx 或重试策略保证永久为零。当前修复已消除本地半开探针快速 429 放大，但不能承诺 provider 永不 429/503，也不能把一次受控回放提升为自然 Desktop `live_accepted`。

## 证据分层与停止点

| 层级 | 结果 |
| --- | --- |
| `repo_verified` | PASS |
| `filesystem_projected` | PASS（备份存在，doctor 投影全 MATCH） |
| `host_loaded` | PASS（MainPID、源码哈希、健康与监听状态一致） |
| `controlled_live_replay` | PASS（Luna、Sol 各一次，Direct API） |
| `natural_live_accepted` | 未宣称；仍需用户自然 Desktop 会话中的新鲜同窗证据 |

回滚入口是上述备份目录中的 admission 文件；需要另行取得远端写入授权后，仅恢复 `cpa-admission.py` 并按既有 doctor/服务复验流程操作。本轮不执行回滚。

