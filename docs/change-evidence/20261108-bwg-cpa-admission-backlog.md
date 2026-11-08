# BWG CPA admission accept backlog 修复（2026-11-08）

## 目标与边界

本次只修复 admission TCP listener 使用 Python `socketserver` 默认五深 backlog，
导致短时并发在进入 Handler 前排队的问题。没有修改 OAuth/API key、账号文件、
模型路由、lane `max_inflight`/`max_pending`、RPM、重试或冷却。

## 受控证据

远端原状态为 `LISTEN 0 5`。使用 admission 自身会在入口拒绝的
`Content-Encoding: zstd` 请求做 burst probe；请求在未接触 CPA、账号或配额前返回
`415 upstream_contacted=false`。结果：

| 并发 | 原吞吐 | 原 p99 | 新吞吐 | 新 p99 |
|---:|---:|---:|---:|---:|
| 8 | 57 req/s | 1060 ms | 550 req/s | 22 ms |
| 16 | 58 req/s | 1071 ms | 731 req/s | 28 ms |
| 32 | 29 req/s，1 次 reset | 1240 ms | 575 req/s，无 reset | 46 ms |

RSS 在各级并发保持约 64.6 MB。性能差异来自内核 accept 队列，不是上游生成
速度或 OAuth 限额。

## 修复与证明（`repo_verified`）

- `Server.request_queue_size = 128`，仍是有界队列；lane admission 继续负责真实
  上游工作量。
- 新增回归断言：值大于 stdlib 默认 5 且不超过 256。
- commit `1076574b`；`tests/test_cpa_admission_telemetry.py` 与大 body spool
  回归 focused 测试通过，ruff 与格式检查通过。

## BWG 投影（`host_loaded`）

使用 `outputs/deploy-admission-backlog-20261108.ps1` 的排它 drain 事务，从 live
pin `5763ad947bdc412242022ea7e71811201c124213a4fee08e83ca3d5acfe26455` 换到
`f48f9d1cd7204986d177ca536300da9cc43f611ef2daab80034306a7a74a7843`。

- 备份：`/opt/cliproxyapi/backups/20261108T084617.889146712Z-admission-backlog-20261108`；
- integrity check、415 回归守卫通过；
- 读回 `LISTEN 0 128`，`NRestarts=0`，`/healthz status=ok`，live source hash
  为 `f48f9d1c…`；
- 与同窗的 `MALLOC_ARENA_MAX=2`、大 body spool、CN-first 变更保持独立，未更改
  账号和上游策略。

回滚入口：在 `/run/vps-ssh-launcher-maintenance.lock` 与
`/run/cpa-admission/requests.lock` 排它 drain 内，恢复备份目录中的
`cpa-admission.py` 与 `cpa-admission.sha256`，重启 admission，确认 integrity、
`LISTEN`、`/healthz` 和 lane 状态；Git 回滚不能代替远端恢复。

## 证据边界

该修复证明短时连接 burst 不再被五深 accept 队列吞吐限制，并不改变供应商配额、
封号判定、长期网络质量或上游首字延迟；后者仍由自然流量审计和 provider 侧指标
单独观察。
