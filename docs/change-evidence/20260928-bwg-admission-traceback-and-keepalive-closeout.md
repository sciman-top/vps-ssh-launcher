# BWG admission 良性 traceback 收口与 keep-alive 断开竞态修复（2026-09-28 第二轮）

## 范围与授权

- 代码基线：`edb5ae2` → 本次新增提交 `aa7fe58`（收口 admission keep-alive 断开竞态）。
- 目标：仅处理 `bwg` 的 admission 代码投影；`zz` 未访问、未读取、未修改。
- 延续本日既有 BWG-only 授权（`执行 -Apply`）；本次未轮换凭据、未轮换公网随机路径、未消费原始 Usage queue、未重启主机。
- SSH 使用严格 host-key checking；没有执行 apt、内核升级或系统维护写入。
- 证据不包含密码、私钥、token、订阅地址、随机公网路径、公共 IP、请求体或响应正文。

## 问题定位：第 4 个独立缺陷

第一轮（`edb5ae2`）定位并修复了三个缺陷：`_send_json` 客户端已离开时的写失败、半开探针 `Retry-After` 误报 1 秒、传输层失败误推进熔断阶梯。Apply 后 journal 仍残留在 `14:18:15` 的两条 `Traceback`，需要归因。

追查结论与最初假设**不同**：残余 traceback **不在** `upstream_error → _send_json(503)` 写路径上。帧栈唯一指向：

```
http/server.py:415  in handle_one_request
    self.raw_requestline = self.rfile.readline(65537)
ConnectionResetError: [WinError 10054]   (Linux: ConnectionResetError / BrokenPipe)
```

即 `BaseHTTPRequestHandler.handle()` 在 `close_connection=False` 时**循环复用连接**，每次迭代第一句是**无保护的** `rfile.readline()` 读下一请求行；`http.server` 在此处**只捕获 `TimeoutError`**。客户端在两次请求之间 RST 时，异常直接穿出 stdlib 循环，被 `socketserver.process_request_thread` 打印成完整 handler traceback。

这已用**纯标准库最小复现**证实（不涉及本仓代码）：任意 HTTP/1.1 keep-alive handler，响应完成后客户端 RST，即产生同一条 traceback。因此这是 CPython `http.server` 的通用缺口，不是本仓逻辑错误。它发生在响应**已完整发送之后**，对正确性、lane 租约和熔断**零影响**，但会污染 journal，且与真实 handler 崩溃**无法区分**——正是本次审计要消除的噪声。

## 修复

`scripts/remote/cpa-admission.py`：`Handler` 覆盖 `handle_one_request()`，仅吞掉请求行读取阶段的 `ConnectionError`（覆盖 `ConnectionResetError`/`BrokenPipeError`/`ConnectionAbortedError`），置 `close_connection=True` 并记 `downstream_gone stage=request_line`，其余全部 `super()` 委托。

该覆盖是 stdlib 行为的**严格超集**：它不改变任何 stdlib 已处理的分支，只补上 stdlib 未捕获的那一类。

`test_cpa_admission.py`：新增 `test_keep_alive_connection_reset_after_a_response_is_absorbed`。该测试**在修复前失败**（`handle_error` 被触发），**在修复后通过**，已双向验证。

`docs/runbooks/cpa-gateway.md`：`downstream_gone` 归因补充 `stage=request_line` 形态。

## 本地门禁

- `test_cpa_admission.py`：25 passed。
- Full gate（`scripts/run_gates.ps1` 默认 profile）：`227 passed, 1 skipped, 237 subtests passed`；`lint:ruff`/`lint:format`/`type:mypy` 全部通过。
- 唯一 2 个失败为**存量沙箱环境失败**：`test_cpa_prune_backups_keeps_newest_backup_dirs` 与 `test_v2ray_agent_script_updater_only_replaces_management_script` 通过 `bash -n` 调用 `wsl.exe`，被沙箱 Program Blacklist 拦截后产生非 UTF-8 输出。已在干净树上复现同样失败，与本变更无关。
- `git diff --check` 干净。

## 投影与 host loaded 复验

- 执行入口：`scripts/cpa_bwg_guardrails.ps1 -Profile bwg -Apply`（第二次投影）。
- 结果：`GUARDRAILS_APPLIED`，受管文件全部 `PROJECTION_HASH_VERIFIED`，`READY_STATUS=200`，`HEALTH_OK`，catalog 15 模型。
- 远端 `cpa-admission.py` sha256 = `db88cf6e0a98f93744b4e193606a627f1b126b39a0a6b16cf502723c25249d6d`，与本地 `HEAD:scripts/remote/cpa-admission.py` blob（LF 归一化）**逐字节一致**。
- 远端文件 mtime `14:34:04Z`，服务 `ActiveEnterTimestamp=14:34:07Z`，`MainPID=164492`。

## 部署字节证据

全历史 journal 内 `Traceback` 总数 = **4**，时间戳仅 `08:09:20` 与 `14:18:15`，**全部早于** `14:34:07` 的重启；部署后进程 traceback = **0**。

## 受控实战回放（最终部署字节）

回放从 BWG 本机经公网 `8443` TLS 随机路径进入 Nginx，再经 admission `8318` 到 CPA `8317`。回放窗口内同时存在真实 Desktop 流量（`inflight=1`、`pending=2`），属混合流量场景。

| 步骤 | 路径 | 结果 |
|---|---|---|
| T1 | 公网 `8443` → nginx → admission → CPA | **HTTP 200**，776 bytes，3 个 SSE 帧，含 `[DONE]`，响应体内 error/capacity 标记 = **0** |
| T2 | admission `8318` 直连 | **HTTP 200**，776 bytes |
| T3 | CPA `8317` 直连（绕过 admission） | **HTTP 200**，证明上游当时健康，admission 非瓶颈 |
| T4 | 完成后 keep-alive 连接 **RST**（缺陷路径） | `RST_SENT_after_completed_response` |
| T5 | 回放后 journal 判定 | `tracebacks_since_restart=`**0**；`downstream_gone=3` |

T1/T2 的 TTFB 为 `25.8s` / `28.8s`，原因是 OAuth lane 固定 `max_inflight=1`，回放请求排在 2 个真实 Desktop 请求之后——这是既有单飞设计的预期代价，非本次变更引入，且远在 `queue_timeout_seconds=120` 预算内。

journal 捕获到**新代码路径实时生效**的关键行：

```
2026-09-28 15:37:50,330 INFO downstream_gone stage=request_line
```

即 RST 被干净吸收、记为新日志行、**未产生任何 traceback**。同窗口另见真实 Desktop 断连被 `retired_upstream_reader resident=1` + `downstream_disconnect` 正常收尾，`retired_readers 0 / total 2` 无泄漏。

`downstream_gone` 全量分解：`stage=request_line` 2 次（本轮新增路径）、`status=429` 1 次（第一轮路径）——两条新路径均已在真实部署上生效。

## 分层判定

| 层级 | 结果 | 依据 |
|---|---|---|
| `repo_verified` | PASS | `aa7fe58`；Full gate `227 passed, 1 skipped, 237 subtests passed`（仅 2 个存量沙箱失败）；lint/type/format 全绿；测试已双向验证（修复前必失败） |
| `filesystem_projected` | PASS | backup-first apply、`PROJECTION_HASH_VERIFIED`、事务结果 |
| `host_loaded` | PASS | fresh strict doctor = **`DOCTOR_CONTRACT_OK`**，9 项 `projection-drift` 全 `MATCH`，`nginx-syntax=OK`，`admission-service=enabled-active`，`admission-health=OK`，端口仅 loopback（8317/8318）+ 公网 8443，`request-retry=0`、`save-cooldown-status=false`；远端 `cpa-admission.py` sha256 与 HEAD blob 逐字节一致 |
| `controlled_live_replay` | PASS | 公网全链 200 + admission 直连 200 + 上游直连 200 + 完成后 RST 吸收（0 traceback，新路径实时命中） |
| `natural_live_accepted` | NOT CLAIMED | 回放为受控探针；症状 A 的自然消失需用户在正常业务窗口观察 |

## 回滚与残余边界

- 远端回滚只使用本次事务备份目录，恢复后重新执行同一 strict doctor；Git 回滚不能替代远端恢复。
- provider 长期配额、账号风控、自然 Desktop 稳定性不由本次受控验证证明。
- `edb5ae2`、`aa7fe58`、`b7f4a15` **均已推送到 `origin/main`**（`git branch -r --contains HEAD` 命中 `origin/main`，`origin/main..HEAD` 为空）。
- 观测到的上游压力仍在：doctor 24h 窗口内 `503=243`，其中 `fast_upstream_lt_0_5s=206`；单一客户端 `/16` 段 `503=231`、重试间隔中位数 `1.0s`，并有 `codex/gpt-6-luna` 的 `auth_unavailable` 保留样本 1 条。这些属**上游账号侧**现象，admission 已按契约快速失败并冷却，不是本仓缺陷。
