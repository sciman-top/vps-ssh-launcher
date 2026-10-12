# 2026-10-12 BWG CPA v8.0.26 canary 升级证据

## 目标与范围

- 目标：仅将 `bwg` VPS 上的 CLIProxyAPI 从 v8.0.25 升级到官方 v8.0.26。
- 官方 release：[router-for-me/CLIProxyAPI v8.0.26](https://github.com/router-for-me/CLIProxyAPI/releases/tag/v8.0.26)（2026-10-12 13:46 UTC 发布，Latest，非 pre-release），本次固定使用镜像
  `eceasy/cli-proxy-api:v8.0.26@sha256:ee21b44f2b0df44a88016b4672f021fd0c82e5633137f0a979e7b01fe06553b2`。
- 升级前镜像为
  `eceasy/cli-proxy-api:v8.0.25@sha256:7f2735c1ab92b087ca43d1342dc139a6b7ece939731b0de2a9a3eee6eb45aab4`。
- digest 双信源逐字节一致：本地经 Xray 代理查询 Docker Hub registry（manifest 字节 sha256 即 header 值）+ VPS 直连 `docker pull` 回报。
- 仅改动 Compose 的 CPA 镜像行；不触碰 `zz`、OAuth/API 凭据、Nginx、admission、路由清单、并发、重试或冷却策略。

## 上游变更与风控决策

v8.0.25 → v8.0.26 共五项提交，已逐条对照 release 页与上游源码（tarball 只读消费于
`~/.zcode/tmp/cpa-v8026`，revision `a27799e`，仅 grep 取证）：

- `1e37b52`：websocket 会话跨 config reload 与 auth 移除存活——正向稳定性修复，本栈执行器路径无行为变化。
- `dd7b433`：新增 `server.max-request-body-bytes` 请求体上限。**默认 ≤0 即禁用**
  （`internal/config/sdk_config.go`："Values <= 0 disable the limit (default)"；
  `internal/api/server_middleware.go:47` 对 `<=0` 直接跳过）。本仓未设该键，公网面
  另有 Nginx body-buffer/限流护栏，因此行为零变化，不新增配置写入。
- `fdbc610`：plugin auth 多凭据支持——本栈无 plugin auth，惰性。
- `77cf88b` + `a27799e`：test-only golden 修复。
- 冷却/配额/流式/affinity 上游零改动；`session-affinity-rebind-on` 延续同日 v8.0.25
  周期决策不启用（缓存近上限、单 OAuth 凭据）。

现有 `session-affinity=true`、1h TTL、`stream-bootstrap-buffering=false`、`request-retry=0`、OAuth `max_inflight=2` 和 admission 队列上限均保持原值，零配置写入。

## 仓库切片

- 本周期 `scripts/remote/*.py` 零改动：夹具上传副本经远端 `sha256sum -c` 校验与仓库
  HEAD 一致（`cpa-acceptance.py` `4e9093ee13778a55216b3e210561b0b27566355d75a97b7cd6144d6620bc9a1d`、
  `cpa-update-acceptance.py` `3e6ffc742599d75315b523adeeca6a38f7f8dff64606b6aa7310fd5f5848f718`）。
- 补交 v8.0.25 周期未入库的驱动脚本（规范化 SHA-256）：
  - `outputs/cpa-v8026-canary.sh` `b6c3bbac4339f11112e0b861fc44144ff416a271c42e1d26bb122d9ddecbb55d`（与
    v8025 版 diff 仅版本锚点行，事务语义逐字节同构）；
  - `outputs/cpa-v8026-fixture.sh` `06d2d0322b8e2d86e56dcfa1fbcdd2c62eea0dceaf1e63e16154641af6cc5d5e` 与
    `outputs/deploy-cpa-v8026-fixture.ps1` `5e47f806d2313909302eaf6eb0c70cd4d084abb77128aca9afae2b43f27aa575`
    （脚本上传 b64+sha 校验+py_compile；`unshare -m -n` bind-mount 隔离；按 PASS 字面行
    判定不依赖 `$?`；`/opt/cliproxyapi` 绝对路径进程=泄漏即 fail）；
  - `outputs/cpa-v8026-probes.sh` `358fb6bb66a58d4b4c2c1d52a4bc81c969967a734575e81dcedd4b472d64afe3` +
    `deploy-cpa-v8026-probes.ps1` `665b095dfb485f766d2671fab50e2dc1bc9f51aac2e0e001bc695e6df97b93bf`
    （容器读回 + quality/cache canary，`CPA_HEALTH_NO_OAUTH=1`）；
  - `outputs/cpa-v8026-cache-probe.sh` `cb0b7cd5fa8dc8fafd0ed70c4992f546e81cec813e6b091d61862b56a6a9cace`。
- 首次 probes 运行在容器 `RepoDigests` 读取上误用容器对象模板（该字段属 image 对象），
  `set -e` 下先于任何探针请求中止；修正为经 `.Config.Image` 二次 inspect 后重跑，未消耗探针预算。

## 前置证据

`outputs/doctor-20261012-pre-v8026.txt`（2026-10-12T14:58:47Z）返回 `DOCTOR_CONTRACT_OK`：

- v8.0.25@7f2735c1 容器 running、restart=0；全部投影 `MATCH`；admission integrity OK；
  `POLICY_OK`/`semantic-policy=OK`；`MODEL_IDS_UNKNOWN=none`；冷却态 `none`；磁盘可用 30G。
- OAuth 单文件 56.6h 续期窗口（7d 零刷新失败），未制造 OAuth 探针。

## 隔离 fixture 验收

`outputs/fixture-v8026.txt` 使用目标 v8.0.26 digest 提取二进制（sha 前缀
`6969e3cdb2152018`），在 disposable mount/net namespace 中运行，未接触生产容器或真实 provider：

- overload：HTTP 200 + capacity marker + 1 次合成上游调用；cooldown：HTTP 503 + 0 次调用；
  62 秒后同进程恢复 HTTP 200、`completed=true`；`actual_health` 为 `HEALTH_OK`。
- updater 四场景全部契约符合：`start_fail` 与 `model_exposure` exit 1 + 恢复旧 Compose +
  rollback 日志；`transient` exit 10 + `UNVERIFIED` 日志；`success` exit 0；各场景
  pre-state 均 attempt=1 即 `HEALTH_OK`。
- 终态：`ACCEPTANCE_RESULT=PASS`、`ACCEPTANCE_EXIT=0`、`CLEANUP_OK`（泄漏扫描零命中）。

## 生产升级事务

收据见 `outputs/cpa-v8026-canary-run.log`：

- 15:11:31Z 经 `bwg` profile 开始，精确确认当前 v8.0.25 digest。
- 前置与后置 generation 均 `HEALTH_OK`；备份目录为
  `/opt/cliproxyapi/backups/20261012T151133.366231399Z-from-v8.0.25-canary-v8026`，权限 700。
- 15:11:37Z `OK: updated v8.0.25 -> v8.0.26`，退出码 0；未触发回滚，也未 prune 旧镜像。

## 升级后验证

生产读回（`outputs/post-v8026-probes.txt`）：容器 `v8.0.26@ee21b44f` running、
restarts=0、started=15:11:35Z，repo_digests 与目标一致。

quality-canary（`CPA_HEALTH_NO_OAUTH=1`，预算 7 个非 OAuth 请求/轮）共两次全量尝试：

- 两轮中 `gpt-6-astra`、`gpt-6.1-sol-input`、`glm-5.3-flash`、`deepseek-v4.1-flash`、
  `gpt-5.6-terra`、`glm-5.3` 全部 HTTP 200、`finish=stop`（astra 18.6s→5.2s，其余
  0.65–16.5s），健康通道无回归。
- `muse-spark-1.3-contributor` 两轮均 120s `error_class=network` 超时 → 契约化
  `UPSTREAM_UNAVAILABLE`（exit 10，本地契约失败为零）。按“通道涨落勿当升级回归排查、
  不重复探针”纪律，两轮后停止，未第三次加压。
- muse 通道侧归因依据：升级事务内 generation 门双通过（15:11Z 该通道可用）；post
  doctor error dumps 保留 3 个非 OAuth overload 事件（15:14Z 两条 + 09:55Z 升级前一
  条），24h `five_xx` 含 `502/slow_upstream`×1；同 fixture 族 deepseek 亚秒 200；
  v8.0.26 变更集不触执行器出站路径。muse 走自然流量与下次 doctor 观察恢复，不加探针。

cache-canary 单独补跑（独立模式，2 个 GLM 请求）：首样本冷启动 `0/4114`，第二样本
`4096/4114`，命中率 `0.9956`，与既有基线一致；因此未改动 `support-prompt-cache-key`
或 session-affinity 配置。

`outputs/doctor-20261012-post-v8026.txt`（2026-10-12T15:26:04Z）返回 `DOCTOR_CONTRACT_OK`：

- 容器 `v8.0.26@ee21b44f` running、restart=0；loopback 8317/8318、公网 8443、随机路径、
  admission integrity、Nginx 443 lane、权限与语法全过；全部投影 `MATCH`。
- `POLICY_OK`、`semantic-policy=OK`、`MODEL_IDS_UNKNOWN=none`（`gpt-6-luna` 回归客户端
  目录满编）、`request-retry=0`、`model_substitution_warnings_7d=0`、冷却态 `none`。
- admission journal 24h：capacity 事件 34 次（升级前 43 次同量级）、lane reject 7 次
  全为 `chatgpt-oauth` 既有形态；OAuth 56.1h 续期窗口 WARN（7d 零刷新失败），未制造
  OAuth 探针。

## 本地门禁

`outputs/gates-20261012-v8026.txt`：

- build、bandit（含 `scripts`）、ruff check、ruff format、mypy 全部通过。
- pytest：`558 passed, 1 skipped, 269 subtests passed`；唯一跳过项是仓库约定的真实 SSH
  integration。另有一条 `test_cpa_guardrails_script` reader 线程 GBK 解码 warning
  （既有 pwsh 子进程编码环境噪声，测试通过，与本切片无关）。
- 门禁退出码为 0。

## 回滚入口

备份和 v8.0.25 digest 旧镜像已保留。发生本地 readiness/doctor 失败时，按
`docs/runbooks/cpa-manual-rollback.md` 从上述备份恢复 Compose，再使用旧 digest 执行
`docker compose up -d --pull never`，随后重新运行严格 doctor；本次未需要回滚。

## 分层结论

| 层级 | 结论 |
| --- | --- |
| `repo_verified` | 官方 release 页、双信源 digest、fixture PASS、全门禁通过；驱动脚本与上传副本均有本地收据与哈希核对。 |
| `filesystem_projected` | 生产受管文件零改动，post doctor 全部投影 `MATCH`；Compose 镜像行已按 canary 原子替换。 |
| `host_loaded` | v8.0.26 目标 digest running、restart=0、generation/readiness 与 strict doctor 通过。 |
| `controlled_live_replay` | 非 OAuth quality-canary 两轮 6/7 路 200/stop，muse 通道侧不可用（契约 exit 10）；GLM cache-canary 命中率 0.9956 与基线逐位一致。 |
| `natural_live_accepted` | **未宣称**。延续本轮纪律：未制造 OAuth/桌面自然会话；OAuth 依赖真实流量与 updater timer（10/13 04:07Z 起 luna 冒烟跑在 v8.0.26 上）；muse 通道恢复亦以自然流量/doctor 观察为准。 |
