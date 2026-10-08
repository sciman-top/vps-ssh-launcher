# 2026-10-08 BWG CPA v8.0.20 canary 升级证据

## 目标与范围

- 目标：将 `bwg` 的 CPA 从 v8.0.16 升级到 v8.0.20（v8.0.17…v8.0.20 四个
  release 均发布于 2026-10-07，距升级不足 72h 成熟期；用户当次明确指令
  直接升级，与 2026-10-07 v8.0.16 先例同型）。
- 范围：**仅 `compose.yml` 镜像声明**。`config.yaml`、OAuth 凭据、Nginx、
  admission（当日已先行完成浮点修正部署，见
  [20261008-bwg-admission-floatfix-deploy.md](20261008-bwg-admission-floatfix-deploy.md)）、
  路由清单零改动。不触碰 `zz`，不轮换 key。

## 上游变更评估（v8.0.16...v8.0.20）

- 风控相关（本栈 OAuth lane 直接受益）：v8.0.17 `fix(cliproxy)` 刷新期间
  不再选中已被拒绝的凭据并保留配额冷却、凭据版本/注册 epoch 透传、
  in-flight token 刷新不再并发覆盖凭据——三者均为防封号/防限流正向修复。
- 请求正确性：v8.0.17 gemini/claude 翻译与系统提示词位置修复；v8.0.20
  translator 不再丢弃附件与空轮次（桌面 Codex responses wire 直接受益）、
  openai/interactions 转换器显式返回错误；v8.0.18 SSE 兼容的紧凑 JSON
  错误体。
- 不触达项：v8.0.18/20 的 xAI Grok TTS 与 Grok CLI 动态版本（本栈无 xai
  lane）；v8.0.19 服务端 GitHub token 配置（未使用）。
- 无 breaking change、无 config schema 变更、无限流/OAuth 刷新语义负面
  变更。

## 事务前置

- digest 双信源交叉验证：本机（走 Xray 代理）与 VPS（直连）对
  `registry-1.docker.io` 查询 `v8.0.20` manifest 均返回
  `sha256:bdd21270b6e2833bff4ee2b0dd51648a4cbc448569afd6bf028be5f3950dd5fe`
  （header 与 body 哈希一致，OCI index）。
- fixture 验收（`outputs/fixture-v8020.ps1`）：acceptance 脚本分块上传
  sha256 双端校验、py_compile 通过；v8.0.20 实体二进制（sha256 前缀
  `9da559ec6795ff06`，digest-pinned `docker create` + `docker cp` 抠出）
  在 `unshare --mount --net --fork` 私有 namespace 内运行 repo 源
  `cpa-acceptance.py` + `cpa-update-acceptance.py`：目录契约（12 ID 在册、
  墓碑 fail-closed）→ overload（`200 overload=true upstream_calls=1`）→
  cooldown（`503 upstream_calls=0`）→ 62s recovered（`200 completed=true
  same_process=true`）→ `actual_health HEALTH_OK`；updater 四场景
  `start_fail` exit=1+还原、`model_exposure` exit=1+回滚、`transient`
  exit=10+UNVERIFIED、`success` exit=0。`ACCEPTANCE_RESULT=PASS`、
  `CLEANUP_OK` 无进程泄漏。镜像在 fixture 前预拉取，docker pull 时再次
  校验 digest 一致。

## 升级事务

`outputs/cpa-v8020-canary.sh`（远端 `/root/cpa-v8020-canary.sh`，sha256
`ccf890f5d036a24be5aea2ecb7cd358ebad90a2be4b027990aaef919da1e4999` 双端一
致，`bash -n` 通过），保留 `cpa-auto-update.sh` 全部本地安全栅栏：flock
共享维护锁 → compose 现值精确钉校验（`v8.0.16@71c86af1…`）→ error-dump
收权 → 前置 generation 门 → 备份根健康 → 备份 compose → digest-pinned
pull（fixture 已预拉取）→ 原子单行替换 →
`docker compose config --quiet && up -d --pull never` → 后置 generation
门 → 任一失败自动回滚。不 prune。

- 结果：`03:25:53Z CANARY_START` → `03:25:58Z OK: updated v8.0.16 ->
  v8.0.20`，5 秒完成，前后 generation 门均 `HEALTH_OK`，回滚未触发。
- 备份：`backups/20261008T032554.866988306Z-from-v8.0.16-canary-v8020/`。
- 回滚集：18 个 cli-proxy-api 镜像全部保留（含 `71c86af1` v8.0.16），未
  prune。

## 验证与风控复核

- **post doctor**（两次）：容器 `v8.0.20@bdd21270` `restart=0`、
  `MODEL_IDS_UNKNOWN=none`、9×投影 MATCH、`oauth_monitor=OK`、
  `cooldown_state=none`、`luna_state=available`、
  `gateway-throttle-status=429`（断言期望值）、`DOCTOR_CONTRACT_OK`。
- **quality-canary**（版本变动既定触发项；OAuth lane 默认豁免）：10 目标
  中 9 路 200/stop——`gpt-6-astra` 200/10.1s、`deepseek-v4.1-flash`
  200/3.1s、`gpt-6.1-sol-input` 200/11.8s、`glm-5.3-flash` 200/6.7s、
  `deepseek-flash` 200/1.1s 等；唯一 502 = `gpt-6-astra-ciii`（slot2 CIII
  上游慢性故障，与 v8.0.15/v8.0.16 期同型，非本次回归），exit 10 = 上游
  侧。
- **自然流量（升级后）**：03 时段 200 数升级后继续增长（57→68），零 5xx、
  零 429、`admission_capacity_events_24h=0`；容器日志升级窗口内 0
  error/panic；admission 自升级起零拒绝、零冷却。用户真实 Codex 流量经
  v8.0.20 全链正常。
- **受控 OAuth 回放**（补做，2026-10-08 03:42Z）：loopback 经
  admission(8318，新代码 e7a5e108)→CPA v8.0.20→上游全链，
  `gpt-6.1-sol`、"Reply with exactly: OK"、1024 token 上限、api-keys 认证
  （服务端读取不回显）：`HTTP=200 / 3.2s / status=completed /
  OUTPUT_MATCH=TRUE / Retry-After=absent`；journal 确认单次 http + 单次
  upstream_result、零重试零拒绝（request 计数 1:1）。
- **粘性路由复核**：升级后 60 分钟窗口内 OAuth lane 34 次
  `session-affinity: cache hit`（另有 deepseek/zhipu 各 1 次），sticky
  cache 机制在 v8.0.20 上正常；配对 10 次 LCP miss / 3 次 miss 属正常首
  次写路径。
- **缓存命中率**（`cpa-health.py cache-canary`）：
  - DeepSeek lane：`hit_ratio=0.9579` 双样本一致，与 v8.0.15/v8.0.16 基线
    逐位一致——升级零回归。
  - GLM lane：样本 1 冷启动 0（首次写入属预期），样本 2
    `cache_read_tokens=4096/4114` → `hit_ratio=0.9956`，GLM 隐式缓存在
    v8.0.20 解析与命中正常。
  - 按缓存约束纪律（只做请求侧约束、不盲目开启
    `support-prompt-cache-key`）无配置改动；两条可缓存 lane 均处或接近天
    花板。v8.0.17…v8.0.20 未引入新的缓存相关 lane 语义。

## 回滚

必要时按 [cpa-manual-rollback.md](../runbooks/cpa-manual-rollback.md) 场景 A：

```bash
cp -a /opt/cliproxyapi/backups/20261008T032554.866988306Z-from-v8.0.16-canary-v8020/compose.yml /opt/cliproxyapi/compose.yml
docker compose -f /opt/cliproxyapi/compose.yml up -d --pull never
```

v8.0.16 镜像（`71c86af1`）已确认本地保留。

## 证据层级

| 层级 | 结论 |
| --- | --- |
| `repo_verified` | 事务/fixture 脚本 sha256 双端一致；本仓改动为脚本入库+证据文档 |
| `filesystem_projected` | doctor 9×投影 MATCH |
| `host_loaded` | 容器 `v8.0.20@bdd21270` restart=0、双 generation 门 HEALTH_OK |
| `controlled_live_replay` | OAuth lane 受控回放 PASS（200/3.2s/completed/OUTPUT_MATCH/零重试/无 Retry-After，经新 admission 全链）；quality-canary 9 路 200（唯一 502 为既有上游慢性故障）；cache-canary 双 lane 基线 |
| `natural_live_accepted` | 升级后自然流量 200 持续、零 5xx/429/capacity 事件；34 次 session-affinity cache hit |
