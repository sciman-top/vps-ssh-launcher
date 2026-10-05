# 2026-10-05 BWG CPA v8.0.15 canary 升级证据

## 目标与范围

- 目标：将 `bwg` 的 CPA 从 v8.0.13 升级到 v8.0.15（v8.0.15 发布于
  2026-10-04 22:31 UTC，未满 updater 的 72h 双源成熟期；用户当次明确指令
  直接升级）。v8.0.14 与 v8.0.15 一并收编。
- 范围：**仅 `compose.yml` 的镜像声明**。`config.yaml`、OAuth 凭据、Nginx、
  admission、路由清单零改动。不触碰 `zz`，不轮换 key，不新增上游重试。
- 附带切片：升级前把当日已部署但未提交的 admission `RecoverAfterReset`
  修复提交入库（d74ddcd），并执行 guardrails `-Apply` 收敛
  `cpa-health.py`/`auto-update.sh` 投影滞后（doctor 9×MATCH）。

## 上游变更评估（v8.0.13...v8.0.15）

- v8.0.15（2 commit）：`fix(openai)` 截断流缺 `finish_reason` 时显式报错；
  `fix(antigravity)` 终端 chunk 后客户端断连按成功处理。
- v8.0.14（对本栈相关项）：`fix(codex)` OpenAI 响应翻译保留 URL citations；
  `feat(auth)` 凭据变更互斥锁 + 持久化增量合并（单 Plus 账号风控正向加固）；
  `feat(registry)` 自定义模型目录源与动态重载（opt-in，未启用）；
  `feat(payload)` protobuf/multipart finalizer。antigravity/claude lane
  本栈不存在，不触达。
- 无破坏性变更、无新增必配项、无 config 语义变化；release notes 无缓存/
  路由/限流配置项变动。

## Fixture 模拟验收（生产零接触）

v8.0.15 实体二进制（sha256 `ef0b03f439ba103f…`，`--version` 自报
`v8.0.15, Commit a4acc9f`）从新镜像 `docker cp` 抠出，在
`unshare --mount --net --fork` 私有 mount+net namespace 内、
`/opt/cliproxyapi` 被 fixture 目录遮蔽的环境运行 repo 源
`cpa-acceptance.py`（md5 `88781636…`）+ `cpa-update-acceptance.py`
（md5 `4377ab1e…`）+ doctor 已验 MATCH 的 `cpa-health.py`/`auto-update.sh`：

- 目录契约（含墓碑 fail-closed）→ overload（`status=200 overload=true
  upstream_calls=1`）→ cooldown（`503 upstream_calls=0`）→ 62s recovered
  （`200 completed=true same_process=true`）→ actual_health `exit=0
  HEALTH_OK`。
- updater 四场景：`start_fail` exit=1+还原+`ROLLBACK restored=`；
  `model_exposure` exit=1+还原+回滚；`transient` exit=10+`UNVERIFIED:`
  未重试；`success` exit=0。`ACCEPTANCE_RESULT=PASS`。
- 转录 `/tmp/cpa-v8015-fixture.log`（远端，不入库）。
- **fixture 目录新依赖**：10/5 深审后 `cpa-health.py` 在 import 期加载同目录
  `cpa_provider_routes.json`（缺失则 `_ROUTE_MANIFEST_ERROR` 使全部 check
  返回 20）；首轮 fixture 因此在目录契约断言失败，补入该文件（bytes 与
  远端投影一致，sha256 `de21f0ec…`）后通过。后续 fixture 组装须包含它。

## 升级事务

脚本 `outputs/cpa-v8015-canary.sh`（远端 `/root/cpa-v8015-canary.sh`，
`bash -n` 通过，md5 `34d4f40508bafc1224ddcde01a126712`），保留
`cpa-auto-update.sh` 全部本地安全栅栏：flock（共享维护锁）→ compose 现值
精确钉校验（`v8.0.13@sha256:6ce96259…`）→ error-dump 收权 → 前置
generation 门 → 备份根健康（≥2GiB、700）→ 备份 compose
（`backups/20261005T135149.721097925Z-from-v8.0.13-canary-v8015/`）→
digest-pinned pull（`v8.0.15@sha256:ebc2ffc1…`）→ 原子单行替换 →
`docker compose config --quiet && up -d --pull never` → 后置 generation 门
→ 任一失败自动回滚。不做 prune：v8.0.13 镜像保留为本地回滚集。

- 结果：`13:51:47Z CANARY_START` → `13:51:53Z OK: updated v8.0.13 ->
  v8.0.15`，6 秒完成，前置/后置 generation 均 HEALTH_OK，回滚未触发。
- 日志：`/opt/cliproxyapi/v8015-canary.log`。

## 验证与风控复核

- **post doctor**：`DOCTOR_CONTRACT_OK`、`v8.0.15@ebc2ffc1 restart=0`、
  **13 ID 全在册**（`MODEL_IDS_UNKNOWN=none`）、9×投影 MATCH、
  `gateway-transport=OK`、`gateway-per-ip-concurrency=20`、
  `gateway-per-ip-rate-limit=OK`、`gateway-throttle-status=429`（配置回显）、
  **`oauth_monitor=OK`**（刷新窗口已过、无告警）、`cooldown_state=none`、
  `luna_state=available`、`timer_last_trigger_age_hours=9`。
- **updater 自洽**（实跑 `--check`）：`CANDIDATE current=v8.0.15
  target=v8.0.15 soak=72h`，`BACKUP_HEALTH status=ok backups=24`。
- **quality-canary**（版本变动的既定触发项；OAuth lane 默认豁免，零 OAuth
  消费）：10 条非 OAuth 路由 200×5——`gpt-6-astra` 10.1s、
  `deepseek-v4.1-flash` 2.0s、`gpt-6.1-sol-input` 17.1s、
  `glm-5.3-flash` 1.7s、`deepseek-flash` 0.7s；唯一 502 =
  `gpt-6-astra-ciii`（slot2 CIII 上游慢性故障，既有现象，非本次回归）。
  slot1 `ai.input.im` 三模型全部恢复 200。
- **OAuth lane 实证（自然流量，零额外消费）**：升级完成后首个请求即
  `200 | 3m35s | POST /v1/responses`（21:51:53 起 3.5 分钟真实长流式生成
  完整走完），且 `session-affinity: cache hit`（粘性路由在新版本正常）；
  升级窗口非 200 仅 quality-canary 自身的 ciii 502 一条。
- **admission**：`cpa-admission.service` active（升级不触碰，独立 systemd
  内存态），healthz `status=ok`、`failure_streak=0`、`cooldown_active=false`、
  `pending=0`、`retired_readers=0`。
- **缓存命中率基线**（`cpa-health.py cache-canary`，DeepSeek lane 双同前缀
  样本）：`input_tokens=3875 cache_read_tokens=3712 cache_miss_tokens=163
  hit_ratio=0.9579`，两样本一致，`HEALTH_OK`。按缓存约束纪律
  （只做请求侧约束、不盲目开启 `support-prompt-cache-key`），无配置改动；
  该基线仅代表 DeepSeek lane 受控样本，不外推其他 lane。

## 观察项（非本次引入）

- 容器在本次升级前当日有两次外部来源 restart（21:05 北京 docker restart，
  RestartCount=0、非 OOM、容器 ID 保留；13:39 一次来自本切片 `-Apply` 的
  config 重载重启），均为运行簿内受认可动作，日志前后皆正常流量。
- 槽位2 `codex.ciii.club` 上游仍处慢性 502 窗口（与 CPA 版本无关）。
- 并行会话正在同一恢复链路上增补 `-CooldownResetConfirmed` 信号区分
  （quota_reset vs cooldown_reset），本切片未触碰其文件。

## 回滚

必要时按 [cpa-manual-rollback.md](../runbooks/cpa-manual-rollback.md) 场景 A：

```bash
cp -a /opt/cliproxyapi/backups/20261005T135149.721097925Z-from-v8.0.13-canary-v8015/compose.yml /opt/cliproxyapi/compose.yml
docker compose -f /opt/cliproxyapi/compose.yml up -d --pull never
```

v8.0.13 镜像（`6ce96259`）已确认本地保留。

## 证据层级

| 层级 | 结论 |
| --- | --- |
| `repo_verified` | 事务脚本/fixture 脚本 md5 双端一致；本仓改动为证据文档 |
| `filesystem_projected` | doctor 9×投影 MATCH（含当日新收敛的 cpa-health.py） |
| `host_loaded` | 容器 `v8.0.15@ebc2ffc1` restart=0、13 ID 满编、双 generation 门 HEALTH_OK |
| `natural_live_accepted` | OAuth lane 3m35s 真实流式生成 200 + 粘性路由 cache hit；非 OAuth 5 路 200 |
