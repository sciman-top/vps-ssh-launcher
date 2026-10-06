# 2026-10-07 BWG CPA v8.0.16 canary 升级证据

## 目标与范围

- 目标：将 `bwg` 的 CPA 从 v8.0.15 升级到 v8.0.16（发布于 2026-10-05
  21:46 UTC，未满 updater 72h 双源成熟期；用户当次明确指令直接升级）。
- 范围：**仅 `compose.yml` 镜像声明**。`config.yaml`、OAuth 凭据、Nginx、
  admission、路由清单零改动。不触碰 `zz`，不轮换 key。
- 用户同步要求的风控与缓存复核纳入验收（见下）。

## 上游变更评估（v8.0.15...v8.0.16，commit a2976eb）

- 对本栈相关项：`fix(codex)` Claude web search 翻译支持 action sources、
  payload overrides 后记录 reasoning effort；`fix(openai)` responses 流在
  finish reason 后 clean EOF 正常收尾、responses compat 模式保留未知
  encrypted content（桌面 Codex 走的正是 responses wire）；`fix(registry)`
  home build 禁用 Devin 目录。
- 缓存相关：`feat(claude)` explicit prompt cache options——本栈无 claude
  lane，不触达、不启用。
- 风控相关：无限流、封号、路由头、OAuth 刷新语义变更；无 breaking
  change、无 config schema 变更。

## 事务前置

- digest 双信源交叉验证：本机（走 Xray 代理）与 VPS（直连）对
  `registry-1.docker.io` 查询 `v8.0.16` manifest 均返回
  `sha256:71c86af15412f16e5bca222dd656faaa0f01c9946cffa281e8ace85ed3f5f7b9`。
- 4 个 payload（acceptance/update-acceptance/fixture-run/canary）分块
  base64 整传，md5 双端一致（`88781636…`/`4377ab1e…`/`d42ea3fd…`/
  `af581601…`），`bash -n` 通过。

## Fixture 模拟验收（生产零接触）

`outputs/cpa-v8016-fixture-run.sh`：v8.0.16 实体二进制（sha256 前缀
`280fac12e76d1851`，digest-pinned `docker create` + `docker cp` 抠出）在
`unshare --mount --net --fork` 私有 namespace 内运行 repo 源
`cpa-acceptance.py` + `cpa-update-acceptance.py`（含 10/5 已知的
`cpa_provider_routes.json` 组装依赖）：

- 目录契约（含墓碑 fail-closed）→ overload（`200 overload=true
  upstream_calls=1`）→ cooldown（`503 upstream_calls=0`）→ 62s recovered
  （`200 completed=true same_process=true`）→ actual_health `HEALTH_OK`。
- updater 四场景：`start_fail` exit=1+还原、`model_exposure` exit=1+回滚、
  `transient` exit=10+`UNVERIFIED:` 不重试、`success` exit=0。
  `ACCEPTANCE_RESULT=PASS`，`CLEANUP_OK` 无进程泄漏。
- 转录 `/tmp/cpa-v8016-fixture.log`（远端，不入库）。

## 升级事务

`outputs/cpa-v8016-canary.sh`（远端 `/root/cpa-v8016-canary.sh`，md5
`af581601876d395792860685bec01384`），保留 `cpa-auto-update.sh` 全部本地
安全栅栏：flock 共享维护锁 → compose 现值精确钉校验（`v8.0.15@ebc2ffc1…`）
→ error-dump 收权 → 前置 generation 门 → 备份根健康 → 备份 compose →
digest-pinned pull（fixture 已预拉取镜像）→ 原子单行替换 →
`docker compose config --quiet && up -d --pull never` → 后置 generation 门
→ 任一失败自动回滚。不 prune。

- 结果：`18:08:21Z CANARY_START` → `18:08:28Z OK: updated v8.0.15 ->
  v8.0.16`，7 秒完成，前后 generation 门均 `HEALTH_OK`，回滚未触发。
- 备份：`backups/20261006T180824.720290561Z-from-v8.0.15-canary-v8016/`。
- 回滚集：`docker images` 确认 `ebc2ffc1`（v8.0.15）、`6ce96259`（v8.0.13）
  等全部保留（digest 引用 tag 显示 `<none>` 属预期），共 17 个镜像未 prune。

## 验证与风控复核

- **post doctor**：`DOCTOR_CONTRACT_OK`、容器 `v8.0.16@71c86af1`
  `restart=0`、**13 ID 全在册**（`MODEL_IDS_UNKNOWN=none`）、9×投影 MATCH、
  `oauth_monitor=OK`、`cooldown_state=none`、`luna_state=available`。
- **updater 自洽**（实跑 `--check`）：`CANDIDATE current=v8.0.16
  target=v8.0.16 soak=72h`，`BACKUP_HEALTH status=ok backups=25`。
- **quality-canary**（版本变动既定触发项；OAuth lane 默认豁免）：slot1
  `gpt-6-astra` 200/3.5s、`deepseek-v4.1-flash` 200/1.5s、
  `gpt-6.1-sol-input` 200/46.6s（慢但 stop，ai.input.im 已知慢窗）、
  `glm-5.3-flash` 200/4.1s、`deepseek-flash` 200/1.0s；唯一 502 =
  `gpt-6-astra-ciii`（slot2 CIII 上游慢性故障，与 v8.0.15 期同型，非本次
  回归），exit 10 = 上游侧。首探针曾因 ssh 空闲超时残留孤儿进程持锁，被
  第二实例按设计 `PROBE_ALREADY_RUNNING`（exit 14）拒绝；清理孤儿后重跑，
  flock 契约行为符合预期。
- **受控 OAuth 回放**：`HTTP=200` / 1.9s / `status=completed` /
  `OUTPUT_MATCH=TRUE` / 无 Retry-After——经 VPS 本机 admission(8318)→CPA→
  上游全链，`gpt-6.1-sol` 单次零重试、1024 token 上限、health-probe 锁互斥。
- **自然流量（升级后）**：用户真实 Codex 流量即时恢复，`session-affinity:
  cache hit` 粘性路由正常（02:20 北京多条）；admission 三 lane
  `failure_streak=0`、无冷却、`pending=0`。
- **缓存命中率**（`cpa-health.py cache-canary`）：
  - DeepSeek lane：`hit_ratio=0.9579` 双样本一致，与 v8.0.15 基线**逐位
    一致**——升级零回归。
  - GLM lane（085ff35 参数化后首次正式采样）：样本 1 冷启动 0（首次写入
    属预期），样本 2 `cache_read_tokens=4096/4114` → `hit_ratio=0.9956`，
    GLM 隐式缓存在 v8.0.16 解析与命中正常。
  - 按缓存约束纪律（只做请求侧约束、不盲目开启 `support-prompt-cache-key`）
    无配置改动；两条可缓存 lane 均处或接近天花板。v8.0.16 新增的 claude
    explicit prompt cache 属本栈不存在的 lane，不启用。

## 回滚

必要时按 [cpa-manual-rollback.md](../runbooks/cpa-manual-rollback.md) 场景 A：

```bash
cp -a /opt/cliproxyapi/backups/20261006T180824.720290561Z-from-v8.0.15-canary-v8016/compose.yml /opt/cliproxyapi/compose.yml
docker compose -f /opt/cliproxyapi/compose.yml up -d --pull never
```

v8.0.15 镜像（`ebc2ffc1`）已确认本地保留。

## 证据层级

| 层级 | 结论 |
| --- | --- |
| `repo_verified` | 事务/fixture 脚本 md5 双端一致；本仓改动为脚本入库+证据文档 |
| `filesystem_projected` | doctor 9×投影 MATCH |
| `host_loaded` | 容器 `v8.0.16@71c86af1` restart=0、13 ID 满编、双 generation 门 HEALTH_OK |
| `controlled_live_replay` | OAuth lane 受控回放 PASS（200/1.9s/completed/预期输出，单次零重试） |
| `natural_live_accepted` | 升级后用户真实 Codex 流量即时恢复 + 粘性路由 cache hit；非 OAuth 5 路 200 |
