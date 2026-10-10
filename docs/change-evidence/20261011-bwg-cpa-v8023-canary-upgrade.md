# 2026-10-11 BWG CPA v8.0.23 canary 升级证据

## 目标与范围

- 目标：`bwg` 的 CPA 从 v8.0.21 升级到 v8.0.23（v8.0.23 发布于
  2026-10-09 14:32Z，距升级约 28h，不足 72h 成熟期；用户当次明确指令
  直接升级，与 2026-10-09 v8.0.21、2026-10-08 v8.0.20 先例同型）。
- 范围：**仅 `compose.yml` 镜像声明**。`config.yaml`、OAuth 凭据、Nginx、
  admission（5273f59e 代）、路由清单零改动。不触碰 `zz`，不轮换 key。

## 上游变更评估（v8.0.21...v8.0.23）

- v8.0.22（6 commits）：responses websocket `response.interrupt`、
  antigravity token 计数、claude 限额解析/compaction、pluginhost detach——
  本栈无 antigravity/claude 凭据，websocket 车道未启用
  （Cockpit `supportsWebsockets=false`），不触达。
- v8.0.23（10 commits，重点两项）：
  - `#6488` fix(openai-compat)：streaming TTFT 改为在有效 token 帧上测量——
    仅影响 GLM/DeepSeek 车道的 TTFT 统计口径（对"吐字缓慢"归因是测量
    正向），不改流式行为。
  - `#6490` fix(auth)：home dispatch 跳过本地状态过滤（home owns
    credential availability）——对本栈单凭证 lane 实际影响极小；本栈
    admission 层位于 CPA 之前，车道级熔断不受 CPA 内部状态语义影响。
    已在升级后自然流量窗复核（零异常）。
  - 其余：codex 图片请求模型解析、claude 流式 delta/effort 指令、
    tool call ID 归一、xai websocket 复位——不触达。
- 无 breaking change、无 config schema 变更、无限流/OAuth 刷新语义变更。

## 事务前置

- digest 双信源交叉验证：本机（经代理）与 VPS（直连）查
  `registry-1.docker.io` 的 `v8.0.23` manifest 均返回
  `sha256:fc250aafe345d178fe7fb1b2966efb2ca204e69ce0cf73eed661209f33e24b70`
  （OCI index）。
- fixture 验收（`outputs/fixture-v8020.ps1 -ImageRef <digest-pinned> -Tag
  v8023`，收据 `outputs/fixture-v8023.txt`）：repo acceptance 脚本分块上传
  sha 双端校验 + `py_compile` 通过；v8.0.23 实体二进制（digest-pinned
  `docker create`+`docker cp` 抠出）在 `unshare --mount --net --fork`
  私有 namespace 内运行：目录契约（bare 12 ID 在册）→ sol 场景
  `200 stop` → `actual_health HEALTH_OK`；updater 四场景 `start_fail`
  exit=1+还原、`model_exposure` exit=1+还原、`transient` exit=10+
  UNVERIFIED 落日志、`success` exit=0。`ACCEPTANCE_RESULT=PASS`、
  `ACCEPTANCE_EXIT=0`、`CLEANUP_OK`。
  - 泄漏排查：fixture 收据中 `pgrep` 命中 `609087 ./CLIProxyAPI`，经
    `/proc` 核验 `cwd=/CLIProxyAPI`、启动时间 13:34:15Z 与容器一致，且
    `docker inspect cli-proxy-api State.Pid=609087`——为**生产容器主进程**
    的相对 argv[0]，非 fixture 泄漏；进程绝对路径签名（
    `/opt/cliproxyapi/CLIProxyAPI`）零命中。

## 升级事务

`outputs/cpa-v8023-canary.sh`（本地 LF sha256
`35e502f85cbd555a194c155ba78a921fc4cc40e6a94739948c8b7c5efb3dd8e2`，
`bash -n` 通过），经 `outputs/deploy-cpa-v8023-canary.ps1` 以
`-Profile bwg` 注入 `VPS_SSH_LAUNCHER_PROFILE=bwg` 执行。保留
`cpa-auto-update.sh` 全部本地安全栅栏：flock 共享维护锁 → compose 现值
精确钉校验（`v8.0.21@831f2fd4…`）→ error-dump 收权 → 前置 generation 门 →
磁盘水位 → 备份 compose → digest-pinned pull（fixture 已预拉取）→ 原子
单行替换 → `docker compose config --quiet && up -d --pull never` → 后置
generation 门 → 任一失败自动回滚（INT/TERM 显式 130/143）。不 prune。

- 一次通过：18:25:22Z `CANARY_START` → 18:25:34Z
  `OK: updated v8.0.21 -> v8.0.23`（约 12 秒），前后 generation 门均
  `HEALTH_OK`，回滚未触发。
- 备份：`backups/20261010T182527.587260513Z-from-v8.0.21-canary-v8023/`。
- 回滚集：v8.0.21 镜像（`831f2fd4`）保留在本地镜像列表，未 prune。

## 验证与风控复核

- **post doctor**：容器 `v8.0.23@fc250aaf` `restart=0`、
  `MODEL_IDS_UNKNOWN=none`、投影全 `MATCH`、`admission-health=OK`、
  `oauth_monitor=OK`、`cooldown_state=none`、`catalog_oauth_missing=none`、
  `luna_state=available`、`gateway-443-lane=OK`、`DOCTOR_CONTRACT_OK`
  （`outputs/doctor-20261011-post-v8023.txt`）。
- **quality-canary**（OAuth lane 默认豁免，10 路计划）：5 路 200/stop——
  `gpt-6-astra` 200/2.9s、`deepseek-v4.1-flash` 200/1.8s、
  `gpt-6.1-sol-input` 200/2.0s、`glm-5.3-flash` 200/1.8s、
  `deepseek-flash` 200/1.1s；`gpt-6-astra-ciii` 502 = slot2 CIII 上游
  慢性故障（v8.0.15/16/20/21 期同型，非本次回归），按契约触发粘性
  再隐藏（保护层按设计工作），后续路由按契约 exit 10 暂缓、零额外请求。
- **受控 OAuth 回放**（真实桌面入口 `fq.sciman.top:8443` 随机路径单发，
  `outputs/probe-live-fq-sol-20261011.txt` 后段）：`gpt-6.1-sol` stream，
  `HTTP=200`、无 `X-CPA-Admission-Reason`、无 `Retry-After`、无容量标记，
  `completed_ms=2303.9`（header 1.48s/首字 2.18s），usage 307 in/5 out，
  journal 对账 `waited_ms=0`。
- **缓存命中率**（`cpa-health.py cache-canary` 双样本×2 lane）：
  - DeepSeek lane：`hit_ratio=0.9579` 双样本一致（3712/3875），与
    v8.0.15/16/20/21 基线逐位一致。
  - GLM lane：样本 1 冷启动 0（首次写入属预期），样本 2
    `cache_read_tokens=4096/4114` → `hit_ratio=0.9956`，与 v8.0.20/21
    基线一致。零配置改动；两条可缓存 lane 均处天花板，v8.0.23 无缓存
    相关 lane 语义。
- **吐字/TTFT**：质量 canary 全部非 OAuth 路由 latency ≤2.9s、OAuth
  回放首字 2.18s/completed 2.30s，处于既有健康区间；"吐字缓慢"既有定案
  （上游生成主导、本地占比 3-4%）不变。#6488 使 GLM/DS 车道 TTFT 统计
  口径更准，属后续诊断面改善，不构成行为变更。
- **自然流量窗（18:25:29Z→18:52Z，约 27 分钟，夜间低谷）**：零 5xx、
  零 429、零 limit REJECTED、admission 零拒绝零容量事件；容器日志零
  panic/fatal；session-affinity LCP 缓存重启后 miss+重建属预期。
- **探针预算总账**（真实 provider generation，升级后）：11 发
  （6 quality-canary + 4 cache-canary 双样本×2 lane + 1 OAuth 回放），
  其中上游侧失败 1 发（astra-ciii 慢性 502）；OAuth lane 恰好 1 发。
  fixture 全程合成上游，零真实 provider 流量。

## 回滚

必要时按 [cpa-manual-rollback.md](../runbooks/cpa-manual-rollback.md) 场景 A：

```bash
cp -a /opt/cliproxyapi/backups/20261010T182527.587260513Z-from-v8.0.21-canary-v8023/compose.yml /opt/cliproxyapi/compose.yml
docker compose -f /opt/cliproxyapi/compose.yml up -d --pull never
```

v8.0.21 镜像（`831f2fd4`）已确认本地保留。

## 证据层级

| 层级 | 结论 |
| --- | --- |
| `repo_verified` | canary 脚本 sha256 本地核对；digest 双信源一致；本仓改动为脚本入库+证据文档 |
| `filesystem_projected` | doctor 投影全 MATCH（admission 5273f59e 代不变） |
| `host_loaded` | 容器 `v8.0.23@fc250aaf` restart=0、双 generation 门 HEALTH_OK、`DOCTOR_CONTRACT_OK` |
| `controlled_live_replay` | OAuth 车道真实入口受控回放 PASS（200/2.30s/completed/无 Retry-After，单发）；quality-canary 5 路 200（唯一 502 为既有上游慢性故障）；cache-canary 双 lane 基线逐位一致 |
| `natural_live_accepted` | 升级窗（27 分钟）零 5xx/429/limit 拒绝、admission 零拒绝、容器零 panic；窗口为夜间低谷，样本薄，持续观察以用户日常使用为准 |
