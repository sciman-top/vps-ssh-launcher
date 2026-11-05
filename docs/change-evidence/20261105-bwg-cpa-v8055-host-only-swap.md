# BWG CPA v8.0.55 host-only 换芯与 flash-sg 车道收口（2026-11-05）

## 目标与边界

用户要求把 BWG 的 CPA 升到 [v8.0.55](https://github.com/router-for-me/CLIProxyAPI/releases/tag/v8.0.55)
及插件最新版，并防范 OpenAI/GPT、Antigravity/Gemini、WorkBuddy CN/INTL、
Claude/Anthropic 的封号、限流、降智与吐字/缓存风险。本次只变更 BWG：

- 没有新增、删除、轮换、隔离或导出任何 OAuth/API-key 凭据；没有发送 OAuth
  生成探针；验收使用隔离 fixture、readiness、合成上游与升级后自然流量。
- 本换芯事务不改插件：换芯时为 cpa-multi-plugins `v0.8.93`（WorkBuddy
  `0.9.85`）的本地 intl-shared 构建（SHA `264722fa…`），全程字节不变。
  同日晚些时候已由独立任务换装到官方 `v0.8.94`（WorkBuddy `0.9.86`，SHA
  `04ed2703…`）；该插件事务与本节 v8.0.55 的宿主换芯分开审计。
- Claude 维持惰性退役墓碑；v8.0.55 的 Claude cloaked date pin 持久化不参与流量。

## 版本与源码审查（v8.0.54..v8.0.55，3 commits）

- 新增 `internal/kvstore`（bbolt 本地 KV，默认 `<auth-dir>/kv.db`，本栈无消费方，
  惰性基础设施；升级后实测生成 32768 字节 `kv.db`，启动日志零报错）。
- `openai_compat_executor` 支持 Codex remote compaction v2（仅 openai-compatible
  路由；本栈 chatgpt 走原生 codex executor）。
- Antigravity compaction summary 载荷额外过滤 `additional_tools` 项（仅影响
  compaction 触发时的 summary 载荷，常规请求不变）。
- 无登录、scope、凭据迁移或 translator 耦合变更，因此不重授权、不做桌面形状冒烟。

## T1 前置修复：flash-sg 第二桶投影收口

接手时发现前一任务把 `models_intl` 扩为
`deepseek-v4.1-flash,deepseek-v4.1-flash-sg`（config SHA `0e943ac6…`）但未投影
`cpa_provider_routes.json`/`cpa-admission.json`，`cpa-health.py readiness` 处于
`LOCAL_CONTRACT_FAILED`（rc 20）；且 admission 的 `model_lanes` 无 flash-sg，
该模型请求会绕过单飞道与 6004 共享窗（drift 读数中可见
`passthrough/deepseek-v4.1-flash-sg/HTTP200/completed:1` 直通实证）。

- `scripts/cpa_bwg_guardrails.ps1 -Profile bwg -Apply`：13 项投影 MATCH，
  apply 内建 `systemctl restart cpa-admission` 加载新 lane 模型表。
- 投影后：readiness `HEALTH_OK`（rc 0）、healthz lanes
  `workbuddy-oauth models=['deepseek-v4.1-flash','deepseek-v4.1-flash-sg']`、
  strict doctor `DOCTOR_CONTRACT_OK`。
- apply 的 yaml 重排使 config SHA 变为
  `2b6bc82c7e0e4cc19eacdab02969213ea9127c32b2ce8d3fe1c409b8d1ab435c`
  （`SEMANTIC_EQUAL: True` 对比备份，workbuddy 块与 api-keys 全不变）。

## T2 隔离加载与受控验收

- 镜像 `eceasy/cli-proxy-api:v8.0.55@sha256:afe1135e8d8752867b73d717c1ca8ed27c44bfe8c6e1a2bebf228dba98889eac`
  （commit `e6e5708`，BuiltAt 2026-11-04T18:04:09Z）；staging binary SHA
  `0b67578ef1941298ce58ddfce3cab8051a3bedc7292e6f1894c1c6c34b99d88d`，`ldd` 只依赖 libc。
- `outputs/cpa-v8055-stage-isolated-load-20261105.ps1`：无凭据、无默认网络
  mount/network namespace 中，v8.0.55 宿主加载**当时在役的 intl-shared 补丁插件**
  （SHA `264722fa…`）：`ISOLATED_PLUGIN_LOAD=PASS`，插件注册 0.9.85，
  `scheduler_priority_reclaim=true`、`account_max_concurrent=1`，零 panic/fatal。
- `outputs/cpa-v8055-acceptance-20261105.ps1`：合成上游验收（含修复后的
  image-readback 回滚 stub）八场景全过：

```text
overload: HTTP200 capacity marker, upstream_calls=1
cooldown: HTTP503, upstream_calls=0
recovered: HTTP200 completed, upstream_calls=1, same_process=true
actual_health: HEALTH_OK
start_fail: exit1, old_compose_restored=true, ROLLBACK_VERIFIED
model_exposure: exit1, old_compose_restored=true, ROLLBACK_VERIFIED
transient: exit10, image retained, local readiness true, UNVERIFIED
success: exit0, local readiness true
ACCEPTANCE_RESULT=PASS
FIXTURE_CLEANED=YES
```

## T2 生产事务（host-only swap）

`outputs/cpa-v8055-swap-transaction-20261105.sh`（经
`run-cpa-v8055-swap-20261105.ps1` 投递）在共享维护锁 + admission requests.lock
排他 drain 内执行。前两次尝试 fail-closed 退出（CONFIG_BASELINE_MISMATCH →
更新为 T1 后的 2b6bc82c；PRE_HOLD_PRESENT unverified_request → 早高峰瞬态，
重试至第 4 次捕获三车道同时干净窗口），事务内部零回滚触发：

- 备份 `/opt/cliproxyapi/backups/20261105T001851.135716779Z-from-v8.0.54-to-v8.0.55`
  （compose/config/plugin + 凭据哈希）。
- 仅原子替换 compose 镜像行，单次 `docker compose up -d --pull never`。

```text
DIGEST_READBACK=OK
CLIProxyAPI Version: v8.0.55, Commit: e6e5708
PLUGIN_REGISTERED version=0.9.85
START_LOG_CLEAN（panic|failed to load 零命中）
PLUGIN_AND_CONFIG_UNCHANGED（插件 SHA 264722fa、config SHA 2b6bc82c 前后一致）
READINESS_OK（HEALTH_OK）
AUTH_BYTES_UNCHANGED（auth/*.json 逐文件哈希不变；kv.db 非 json 不入该面）
KVSTORE_DB_PRESENT size_bytes=32768
LANE_HOLD_DELTA=none
restarts=0
SWAP_OK v8.0.54->v8.0.55
```

## 升级后验收

- strict doctor：`DOCTOR_CONTRACT_OK`；三 OAuth 车道 `OPEN hold_reason=none`，
  容器 `v8.0.55@sha256:afe1135e… restart=0`，13 项投影 MATCH。
- `proxy_hardening_drift.ps1 -Profile bwg`：`HARDENING_DRIFT=host-security`——
  唯一项为内核 CVE-2026-98432（running `6.8.0-147` < fixed `6.8.0-149`），
  `mptcp_runtime=disabled`、`mptcp_persisted=yes`、缓解在位。该漂移是宿主层
  既有状态，与本次升级无关。同日授权修复窗复核：`apt-get update` 后
  `linux-image-generic` 候选仍为 `6.8.0-147.147`，版本化
  `linux-image-6.8.0-149-generic` 不在任何启用口袋；Launchpad 交叉核对确认
  `6.8.0-149.149` 仅存在于 noble-proposed（2026-10-26 发布）。生产口袋纪律
  不启用 proposed、不手工 dpkg 预发布内核，故该修复等 updates 口袋发布后随
  月度维护安装、下次受控重启窗生效；当前 MPTCP 缓解在位，drift 维持
  informational。xray/google-ipv4/firewall-baseline 等其余车道全部 ok。
- 换芯后 ~15 分钟自然流量（`cpa_natural_traffic_audit.py --ssh-profile bwg
  --hours 0.25`，零注入探针）：
  - `workbuddy-oauth/deepseek-v4.1-flash` 43/43 completed，cache_hit_ratio
    0.94，queue p50=1ms，slow_5s 全归因 upstream（11），无 capacity 断连。
  - `workbuddy-oauth/deepseek-v4.1-flash-sg` 首笔自然请求 200 completed
    （first_text 1084ms），已按单飞道配对（admission_paired=1）——T1 直通洞关闭后
    flash-sg 端到端在 v8.0.55 上实测可用。
  - `chatgpt-oauth/gpt-6-luna` 26/26 completed，cache_hit_ratio 0.99，
    first_text p50=10.6s（上游 TTFB 主导）；`gpt-5.6-luna` 2/2。
  - 吐字水位：wb sse_bytes_per_s p50≈25KB/s、output_tokens_per_s p50≈123-223/s，
    与 8.0.54 基线同量级；慢窗归因 upstream，queue/decode≈0。
  - 换芯后容器日志 157 行：0 panic、0 error；47 条 warn 全部为设计内 wb 6004
    共享窗/模型级限流 fast-fail 与 #70 credits snapshot 未武装提示（既有行为）。

## 回滚

同维护锁与 drain 内，从上述备份恢复 compose 镜像行并
`docker compose up -d --pull never`，readiness 与 strict doctor 复验回到
v8.0.54@sha256:66e51ccb…；插件与 config 未动无需恢复。Git 回滚不能替代远端
恢复；OAuth token 运行期自然刷新，不回放凭据快照。T1 的回滚入口为
cpa-guardrails 备份目录（含 admission stop/start 流程）。

## 证据边界

已达 `repo_verified`、`filesystem_projected`、`host_loaded`、
`controlled_simulation`（ACCEPTANCE_RESULT=PASS）与换芯后短窗自然
`live_observed`。不声称长期封号风险为零、模型未降智、provider 额度充足或
flash-sg 长期调度质量已建立；后续每日 updater 在插件启用态仍按
PLUGIN_CANARY_REQUIRED 语义 defer 宿主升级（exit 77）。
