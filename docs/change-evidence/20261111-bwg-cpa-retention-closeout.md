# bwg CPA 清理保留收口（2026-11-11）

**触发**：用户询问 CPA/VPS 是否保留过多备份和镜像，并授权按建议连续修复、
验证。本文只记录 bwg 的 CPA 清理与镜像保护；其他并行 CPA 路由改动不在此切片。

## 1. 修改与根因

- 日常 updater 无新版本也在本地维护阶段清理。手动事件备份家族受 168h 年龄
  门槛和 keep-8 约束，磁盘可用低于 8 GiB 时收紧为 keep-4。BuildKit 只在
  `/opt/cliproxyapi` 可用低于 4 GiB 时清理 24h 以上记录。
- 钉定 `golang:1.26.10-bookworm`、源码树和 Go 缓存须共享一次七天保护窗口，
  两者都有摘要有效的源码 tar 与 OCI 工具链归档后才可释放。新归档立即受保护，
  时钟回拨不能缩短既有窗口；损坏、链接、摘要不符或路径不精确一律保留。
- 原先最新八个目录混有手工事务备份，无 `image-id`，使镜像清理持续延期。现在
  只把最新八个 `*-from-v*` 自动更新备份纳入回滚镜像集合；手工目录不能替换
  这八个名额，无法识别的回滚记录仍会 fail-closed 延期。
- 旧模拟镜像 `bwg-local/cpa:v8.0.40-pr6784-sim-v2` 没有可复现来源，不自动删除。
  不执行 `system/image/volume prune` 或 `--all`；未使用的 volume 也不属于隐式
  清理目标。

## 2. 代码与测试

- 提交 `1923cd5`：`fix(cpa): isolate verified image retention from manual backups`。
  修改 `scripts/remote/cpa-auto-update.sh`、清理 runbook 以及两个清理测试。
- 当前清理切片聚焦验证：`26 passed, 1 skipped, 17 subtests passed`；跳过项是
  当前 Windows 无符号链接创建权限。`bash -n`、暂存 diff check、ruff
  check/format 与 mypy 均通过。
- 同一轮完整门禁为 `1284 passed, 21 skipped, 627 subtests passed`，另一项
  `test_ci_meta.py::test_shared_remote_command_transport_contract` 失败；重跑
  仍在并行共享传输改动上得到“chunked transfer should use one SSH invocation,
  got 5”，不属于本文件改动，不能据此宣称全部门禁通过。

## 3. 部署与受控实机验收

- `cpa_bwg_guardrails.ps1 -Profile bwg -Apply` 首轮在写入前以 exit 75 拒绝，
  原因为活跃 lane 不空闲，未改动或重启服务。随后空闲窗口完成事务，
  `GUARDRAILS_APPLIED` 与逐文件投影哈希校验成功；本次来自该事务的 guardrail
  备份保留清理为 `removed=3 policy=keep_8`。CPA 就绪为 `HEALTH_OK`。
- 部署更新器 LF 归一化摘要为
  `ec3fcf1309ebf05f75aa3f1f8cecd2043a4ac71bf1b758d39a578183ff2039bd`；
  用真实备份目录运行保护选取逻辑得到 `VERIFIED_RETENTION_RC=0 COUNT=8`。
- 最终 observe 为 `DOCTOR_CONTRACT_OBSERVE_OK`，容器 RUNNING、healthz 200，
  CPA 清理 heartbeat success；当前备份 61 个、约 1.04 GiB。
- 实际 `--apply` no-update 路径 `APPLY_RC=0`：候选版本 `v8.0.61`，SOAK
  `remaining_hours=66.3`；孤儿容器 removed=0；自动更新备份 keep-8、removed=0；
  admission keep=22、plugin keep=9、workbuddy keep=7（family keep-4，但 168h
  年龄门槛仍生效）；源码/工具链 `retained=recent_rebuild_window`；镜像
  `candidates=0 removed=0`。容器 `RUNNING=true`、healthz `200`。
- 镜像候选为 0 的原因是可核查的：现存官方 `v8.0.61`、`v8.0.59` 仍被最新
  回滚记录保护；运行 fork 镜像受保护；模拟镜像不在自动删除仓库集合。Golang
  镜像和源码/Go 缓存当前未释放，因为它们只有约 3.5h 的有效期，必须继续保留
  到 168h 门槛。这不是“清理仍延期”，而是明确的保留策略。

## 4. 空间与恢复边界

- 只读盘点时磁盘约为 18.5 GiB 可用；本次检查约为 24.9 GiB 可用、34% 使用。
  该变化来自本切片以外也在进行的清理动作，不能单独归因于本次镜像删除。
  Docker 的“可回收”是共享层/引用保护估算，不等于可安全逐项删除的空间。
- 已有源码 tar 与工具链 OCI 归档，但当前尚未对归档做全新恢复演练。归档是
  可恢复材料，不等于已恢复成功。归档创建约 3.5h，尚无七天自然 timer 到期
  回执，故不能宣称 Golang 镜像已可在无人干预下自动回归。

## 5. 观测与证据边界

- `DOCTOR_CONTRACT_OBSERVE_OK`，投影和语义策略通过，CPA 清理 heartbeat 成功。
  本轮观察到 `catalog_oauth_missing=gpt-6-luna` 以及 Antigravity 多凭据提示，
  均为同时进行中的 lane/凭据运行态，不作为本清理变更的通过证据。
- 代码已提交，测试和受控回执已完成；尚未得到下一次自然 timer 触发时的清理
  回执，也未得到满七天后的自动清理回执。真实 provider 账号行为、自然负载
  长期质量与未来七天保护期结束时的回收结果仍需独立观察。
- 回滚：本地撤销 `1923cd5`；远端按
  `20261111T141140.312329248Z` 的事务备份恢复，镜像删除由保留下来的回滚记录、
  源码/工具链归档或正常 `docker pull` 提供恢复材料。
