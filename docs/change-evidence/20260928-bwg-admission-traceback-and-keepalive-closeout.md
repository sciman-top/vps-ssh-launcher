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

`14:18:18` 重启后的进程 traceback 计数 = **0**（自 `14:00:00` 起 journal 内 traceback 总数恰为 2，时间戳全部为 `14:18:15`，早于 `14:18:18` 的重启，属**修复前**旧进程）。修复后同样场景在隔离夹具中不再产生 traceback，且 keep-alive 连续两次请求仍正常返回 `200`。

## 分层判定

| 层级 | 结果 | 依据 |
|---|---|---|
| `repo_verified` | PASS | `aa7fe58`；Full gate `227 passed, 1 skipped, 237 subtests passed`（仅 2 个存量沙箱失败）；lint/type/format 全绿；测试已双向验证（修复前必失败） |
| `filesystem_projected` | PASS | backup-first apply、`PROJECTION_HASH_VERIFIED`、事务结果 |
| `host_loaded` | PASS | 远端 sha256 与 HEAD blob 逐字节一致；post-apply `DOCTOR_CONTRACT_OK` 全 MATCH；重启后 traceback=0 |
| `controlled_live_replay` | PASS | 部署字节夹具：客户端 RST + keep-alive 循环，traceback=0；正常 keep-alive 两连请求仍 200 |
| `natural_live_accepted` | NOT CLAIMED | 受控夹具不等同于用户真实 Desktop 自然会话 |

## 回滚与残余边界

- 远端回滚只使用本次事务备份目录，恢复后重新执行同一 strict doctor；Git 回滚不能替代远端恢复。
- provider 长期配额、账号风控、自然 Desktop 稳定性不由本次受控验证证明。
- 第一轮 `edb5ae2` 与本轮 `aa7fe58` 均**尚未推送到 `origin/main`**；远端仓库同步仍是独立收口事项。
