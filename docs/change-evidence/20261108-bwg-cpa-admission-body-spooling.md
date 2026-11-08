# BWG CPA admission 大请求体内存修复（2026-11-08）

## 目标与边界

本次只处理 admission 进程在排队请求上保留完整 HTTP body 的内存放大问题。
没有修改 OAuth/API key、账号文件、IP/UA、WorkBuddy realm 顺序、并发上限、RPM、
重试、冷却或 CPA 容器镜像。

## 根因证据

只读检查发现 admission 当前配置 `max_body_bytes=33554432`（32 MiB），每条 lane
`max_pending=4`；旧代码在取得 lane 资格前已经把整个 body 读入 `bytes`，等待 FIFO
时继续由 handler 持有。四条 lane 的 pending 与在途请求叠加时，body 可以占用数百
MiB。

2026-11-08 远端只读采样：

- admission 进程在自然流量后曾达到 `VmHWM=672.0M`（systemd service 记录），
  随后重启前的 `/proc/<pid>/smaps_rollup` 为 `Anonymous=532108 kB`、
  `VmRSS=543224 kB`，并有多段约 40 MiB 的匿名 `rw-p` 映射；采样时
  `/healthz` 的 lane `inflight/pending` 已为 0，说明高水位来自已释放但仍被分配器保留
  的请求体内存。
- 早先低竞争 A/B 曾排除 `MALLOC_ARENA_MAX`、`cpa_policy` 导入和 `_read_exact` 读放大；
  后来的同步屏障压力测试（见下文）补足了 arena 竞争证据，body 在 FIFO 等待期间被
  持有仍是独立的内存放大边界。

同一工作树的独立提交 `a22e9c9` 随后用同步屏障压力测试证明了 glibc arena 竞争，
并已在本次 body-spooling 事务前部署 `MALLOC_ARENA_MAX=2`。因此下面的低 RSS 基线
不能归因给单一改动；arena cap 处理已释放的分配器碎片，body spool 处理排队期间的新
大 body 常驻，两者是不同边界的修复。

## 修复

`scripts/remote/cpa-admission.py` 增加
`ADMISSION_REQUEST_BODY_MEMORY_LIMIT_BYTES=2 MiB`。lane-bound 请求超过该阈值时，
body 写入 `tempfile.TemporaryFile(mode="w+b")`，原始 `Content-Length` 保留，
`http.client` 直接从 readable file 流向 CPA；不会重写 payload，也不会让 admission
再次构造一份完整 body。正常小请求和 passthrough 请求维持原内存路径。所有拒绝、
异常和上游清理路径都关闭临时文件。

## 本地证明（`repo_verified`）

- 新增大 body 端到端回归：上游收到的字节与输入完全相同。
- `tests/test_cpa_admission.py`：`297 passed`。
- admission/guardrail/policy 组合：`370 passed, 93 subtests passed`。
- `ruff check`、`ruff format --check`、`git diff --check` 通过。
- 代码 pin：`5763ad947bdc412242022ea7e71811201c124213a4fee08e83ca3d5acfe26455`。

## BWG 投影与回滚（`host_loaded`）

使用既有排它 drain 事务
`outputs/deploy-admission-telemetry-20261108.ps1`（传入旧 pin
`dd025ad90c8432159cc4dcf1849b3ddba600f23e874fa6e4c6021a7ac34451a0`）：

- `DRY_RUN_OK`，维护锁与 lane `inflight/pending=0` 通过；
- `ADMISSION_INTEGRITY_OK`、`TELEMETRY_CONTRACT_OK`、编码拒绝回归守卫通过；
- `DEPLOY_OK pin=5763ad947bdc412242022ea7e71811201c124213a4fee08e83ca3d5acfe26455`；
- 备份：`/opt/cliproxyapi/backups/20261108T083248.166615296Z-admission-telemetry-20261108`；
- 投影后 `systemd`：`active/running`、`NRestarts=0`，远端 live source 与 pin
  内容一致；进程初始 `VmRSS=46136 kB`、`VmHWM=54784 kB`；
- 投影后 `/healthz` 返回 `status=ok`，retired reader 为 0。
- 该时点 live unit 同时带有已独立验收的 `MALLOC_ARENA_MAX=2`；本事务未修改 unit。

回滚入口：在共享 `/run/vps-ssh-launcher-maintenance.lock` 与
`/run/cpa-admission/requests.lock` 排它 drain 内，将备份目录中的
`cpa-admission.py` 与 `cpa-admission.sha256` 原子恢复，运行 integrity check，
重启 `cpa-admission`，再复验 `/healthz`、`NRestarts` 与 pin。Git 回滚不能代替
远端恢复。

## 运行态观察（`live_observed`，非长期验收）

投影后约一小时自然日志审计：674 条记录；WorkBuddy lane 63 completed/35
client_disconnect、无 401/403 账号信号；ChatGPT `gpt-6-luna` 281 completed/5
capacity；各 lane admission queue p50 0–3 ms（`gpt-6.1-sol` 有 22.8 s p90，属
上游/请求并发窗口的观察，不因本次修复调整限流）。WorkBuddy cache ratio 仍按
`cached/(cached+usage_in)` 计算为约 0.989，未出现大于 1 的指标。

这些数据证明投影后短窗运行和内存基线恢复，不证明长期封号安全、供应商配额、跨
运营商网络质量或自然用户长期接受度；继续按 strict doctor、自然流量和备份回滚
证据分层观察。
