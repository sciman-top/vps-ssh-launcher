# 2026-10-13 BWG CPA v8.0.27 canary 升级证据

## 目标与范围

- 目标：仅将 `bwg` VPS 上的 CLIProxyAPI 从 v8.0.26 升级到官方 v8.0.27。
- 官方 release：[router-for-me/CLIProxyAPI v8.0.27](https://github.com/router-for-me/CLIProxyAPI/releases/tag/v8.0.27)（Latest，非 pre-release），本次固定使用镜像
  `eceasy/cli-proxy-api:v8.0.27@sha256:b7cf456bf6ef7f6489f18144e7cc166dbb8b2e5f46d6ee8881a321aaea563730`。
- 升级前镜像为
  `eceasy/cli-proxy-api:v8.0.26@sha256:ee21b44f2b0df44a88016b4672f021fd0c82e5633137f0a979e7b01fe06553b2`。
- digest 双信源逐字节一致：本地经 Xray 代理查询 Docker Hub registry（`Docker-Content-Digest`
  header）+ VPS 直连 `docker pull`（按 digest 拉取成功）回报。
- 升级事务零配置改动；仅改动 Compose 的 CPA 镜像行。不触碰 `zz`、OAuth/API 凭据、Nginx、
  admission、路由清单、并发、重试或冷却策略。

## 上游变更与风控决策

v8.0.26 → v8.0.27 共两项提交，已用 tarball 全量 diff（`~/.zcode/tmp/cpa-v8027`，只读消费）
逐文件核实，共 20 个文件变化，全部落在 keepalive 接线与文档：

- `519d590`：feat(streaming) `send-keepalive-before-ft`。新增 `requests.streaming.keepalive-seconds`
  与 `requests.streaming.send-keepalive-before-ft` 两个配置键；心跳形态为纯下游 SSE 注释
  （`sdk_config.go:105` ": keep-alive\n\n"）与 WebSocket Ping，不产生任何上游流量。全部
  运行时改动严格门控：`keepAliveC == nil`（默认关闭）时 `ExecuteStreamWithPreFTKeepAlive`
  直接走与 v8.0.26 相同的同步路径（`stream_forwarder.go`、openai/claude/gemini/images
  handlers 同构）。本栈未设这些键，升级后行为与 v8.0.26 逐字节一致。
- `bf93416`：README 文档（sub2api 致谢列表），无代码变化。
- 冷却、配额、OAuth、affinity、缓存、请求体上限上游零改动。`session-affinity-rebind-on`
  延续 v8.0.25 周期决策不启用（缓存近上限、单 OAuth 凭据）。

现有 `session-affinity=true`、1h TTL、`stream-bootstrap-buffering=false`、`request-retry=0`、
OAuth `max_inflight=2` 与 admission 队列上限均保持原值。

## 仓库切片

- 本周期 `scripts/remote/*.py` 零改动；夹具上传副本经远端 `sha256sum -c` 校验与仓库 HEAD
  一致后 `py_compile` 通过。
- v8027 驱动脚本由 v8026 家族锚点替换生成（diff 仅版本/摘要/日期锚点行，事务语义逐字节
  同构），规范化 SHA-256：
  - `outputs/cpa-v8027-canary.sh` `4b6d304ce09ed7eb3da99f5aae8503f2a3b6ae8025f6323570d994476b2e11be`
  - `outputs/cpa-v8027-fixture.sh` `dfbc3a9aadcdce2d8f534361af3aaa168282166130cfbb5af99c484f0e0a6a54`
  - `outputs/cpa-v8027-probes.sh` `6fe07102b6e7839edb9b29dcfe374ec520447414fafd21fed6fa594690951fcc`
  - `outputs/deploy-cpa-v8027-canary.ps1` `8fdcb11f53c80f1de9b4a59537dec8d5fd10e692b47f671d034be2ab8e011d95`
  - `outputs/deploy-cpa-v8027-fixture.ps1` `cd959d28a857e8c3fc8c773c17e147942b4a6023b79a37e12e66cec44226847f`
  - `outputs/deploy-cpa-v8027-probes.ps1` `afd2e61c4906abb7394e8b9466de6263fff7b0f1a852ad3e61f79fbc0032de7e`
- 生成过程两处自伤，均已修正并留痕：
  1. sed 多表达式顺序缺陷：先全局 `v8.0.26→v8.0.27` 会把回填的 EXPECTED_CUR 锚点再次命中；
     先做共锚 digest 替换、from-版本锚点最后回填后复核通过。
  2. fixture deploy 包装的窄模式 sed 未命中 `/tmp/cpa-acceptance-v8026.py` 上传路径（不含
     `cpa-v8026` 字面量），首次 fixture 运行在隔离步骤之前即失败（`install: cannot stat`）；
     未触生产。泄漏的 fixture root（含生产 config.yaml 副本）已从远端 /tmp 清除，上传路径
     修正后重跑 PASS。同轮一并清除了上一周期遗留的 `/tmp/cpa-accept-v8026-run.*` 临时文件。
  3. canary 脚本 OK 日志行的 digest 锚点未跟随替换（`digest=sha256:ee21b44f…` 陈旧文本），
     属日志缺陷：事务实际按 `$TARGET` digest 拉取且 post 容器读回 `repo_digests` 与目标
     逐字节一致为权威证明。本地脚本产物已修正（上述哈希为修正后值）。

## 前置证据

`outputs/doctor-20261013-pre-v8027.txt`（2026-10-13T06:2xZ 本地留存）返回 `DOCTOR_CONTRACT_OK`：

- v8.0.26@ee21b44f 容器 running、restart=0；全部投影 `MATCH`；admission integrity OK；
  `POLICY_OK`/`semantic-policy=OK`；`MODEL_IDS_UNKNOWN=none`；冷却态 `none`；磁盘可用 30G。
- 24h 状态面：200×2878、503×13（上游侧，慢/中速通道为主）、429×16（admission 既有形态）、
  capacity 事件 35 次同量级；OAuth 单文件 48.6h 续期窗口 WARN（7d 零刷新失败），按既有
  纪律未人工干预、未制造 OAuth 探针。

## 隔离 fixture 验收

`outputs/fixture-v8027.txt` 使用目标 v8.0.27 digest 提取二进制（sha 前缀 `3536d7febce60425`），
在 disposable mount/net namespace 中运行，未接触生产容器或真实 provider：

- overload：HTTP 200 + capacity marker + 1 次合成上游调用；cooldown：HTTP 503 + 0 次调用；
  62 秒后同进程恢复 HTTP 200、`completed=true`；`actual_health` 为 `HEALTH_OK`。
- updater 四场景全部契约符合：`start_fail` 与 `model_exposure` exit 1 + 恢复旧 Compose +
  rollback 日志；`transient` exit 10 + `UNVERIFIED` 日志；`success` exit 0；各场景
  pre-state 均 attempt=1 即 `HEALTH_OK`。
- 终态：`ACCEPTANCE_RESULT=PASS`、`ACCEPTANCE_EXIT=0`、`CLEANUP_OK`（泄漏扫描零命中）。

## 生产升级事务

收据见 `outputs/cpa-v8027-canary-run.log`：

- 23:08:24Z 经 `bwg` profile 开始，精确确认当前 v8.0.26 digest。
- 前置与后置 generation 均 `HEALTH_OK`；备份目录为
  `/opt/cliproxyapi/backups/20261012T230826.550022584Z-from-v8.0.26-canary-v8027`，权限 700。
- 23:08:33Z 更新完成，退出码 0；未触发回滚，也未 prune 旧镜像。

## 升级后验证

生产读回（`outputs/post-v8027-probes.txt`）：容器 `v8.0.27@b7cf456b` running、restarts=0、
started=23:08:28Z，`repo_digests` 与目标一致。

quality-canary（`CPA_HEALTH_NO_OAUTH=1`，预算 7 个非 OAuth 请求/轮）单轮全过：

- `gpt-6-astra`、`gpt-6.1-sol-input`、`glm-5.3-flash`、`deepseek-v4.1-flash`、
  `gpt-5.6-terra`、`glm-5.3`、`muse-spark-1.3-contributor` 全部 HTTP 200、`finish=stop`，
  延迟 0.89–2.9s。上一周期两轮 120s 超时的 muse 通道本轮 2.9s 恢复，支持当时
  "通道侧涨落、非升级回归"的归因。

cache-canary：首样本冷启动 `0/4114`，第二样本 `4096/4114`，命中率 `0.9956`，与既有基线
逐位一致；未改动 `support-prompt-cache-key` 或 session-affinity 配置。

`outputs/doctor-20261013-post-v8027.txt`（2026-10-13T07:1xZ 本地留存）返回
`DOCTOR_CONTRACT_OK`：容器 `v8.0.27@b7cf456b` running、restart=0；全部投影 `MATCH`；
admission integrity OK；`POLICY_OK`/`semantic-policy=OK`；`MODEL_IDS_UNKNOWN=none`；
`model_substitution_warnings_7d=0`；冷却态 `none`；OAuth 续期窗口 WARN（同前置，自愈观察）。

## 本地门禁

`outputs/gates-20261013-v8027.txt`：build、bandit（含 scripts）、ruff check、ruff format、
mypy 全部通过；pytest `558 passed, 1 skipped, 269 subtests passed`（唯一跳过项为仓库约定的
真实 SSH integration），门禁退出码 0。（首跑 lint:format 拒绝了两处测试夹具新行的格式，
`ruff format` 后重跑全绿。）

## 回滚入口

备份和 v8.0.26 digest 旧镜像已保留。发生本地 readiness/doctor 失败时，按
`docs/runbooks/cpa-manual-rollback.md` 从上述备份恢复 Compose，再使用旧 digest 执行
`docker compose up -d --pull never`，随后重新运行严格 doctor；本次未需要回滚。

## 分层结论

| 层级 | 结论 |
| --- | --- |
| `repo_verified` | 官方 release、tarball 全量 diff、双信源 digest、fixture PASS、全门禁通过；驱动脚本与上传副本均有本地收据与哈希核对。 |
| `filesystem_projected` | 生产受管文件零改动，post doctor 全部投影 `MATCH`；Compose 镜像行已按 canary 原子替换。 |
| `host_loaded` | v8.0.27 目标 digest running、restart=0、generation/readiness 与 strict doctor 通过。 |
| `controlled_live_replay` | 非 OAuth quality-canary 7/7 路 200/stop（含 muse 恢复）；GLM cache-canary 命中率 0.9956 与基线逐位一致。 |
| `natural_live_accepted` | **未宣称**。延续本轮纪律：未制造 OAuth/桌面自然会话；OAuth 依赖真实流量与 updater timer；muse 恢复亦以自然流量/下次 doctor 观察为准。 |
