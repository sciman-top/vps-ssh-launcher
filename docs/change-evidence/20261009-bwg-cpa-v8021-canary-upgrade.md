# 2026-10-09 BWG CPA v8.0.21 canary 升级证据

## 目标与范围

- 目标：将 `bwg` 的 CPA 从 v8.0.20 升级到 v8.0.21（v8.0.21 发布于
  2026-10-08 13:26Z，距升级不足 72h 成熟期；用户当次明确指令直接升级，
  与 2026-10-07 v8.0.16、2026-10-08 v8.0.20 先例同型）。
- 范围：**仅 `compose.yml` 镜像声明**；前置独立收敛了一次既存投影漂移
  （`cpa_policy.py`，见下）。`config.yaml`、OAuth 凭据、Nginx、admission、
  路由清单零改动。不触碰 `zz`，不轮换 key。

## 上游变更评估（v8.0.20...v8.0.21，5 commits）

- `7c2133e` fix(cliproxy)：批量模型注册期间并发 auth 更新调和（#6453）——
  批量注册任务不再用陈旧凭据快照覆盖新状态，完成后以更新世代重注册。
  对本栈单 OAuth 账号是防凭据状态回退的正向修复。
- `249e9ef` fix(meta)：responses 请求保留外来 reasoning 密文（#6450）——
  不再剥离未知格式的 reasoning encrypted 内容，桌面 responses wire 经
  非 OpenAI 后端的推理上下文保真，属请求正确性正向修复。
- `0a23627` fix(config)：v8 API 序列化前归一 collection 风格并去除
  mapping key 引号——仅输出侧重写形态、值不动，不改既有 `config.yaml`
  读取语义；本栈不经管理 API 回写配置。
- `2d5351b` feat(vertex)：原生 Gemini interactions API——需要 `interactions`
  凭据选项，本栈无 Vertex 凭据不触达；post doctor `MODEL_IDS_UNKNOWN=none`
  证实目录无意外新增。
- `54946fa` docs：移除 README_CN 完整 QQ 群号。
- 无 breaking change、无 config schema 变更、无限流/OAuth 刷新语义变更。

## 事务前置

- digest 双信源交叉验证：本机（走 Xray 代理）与 VPS（直连）对
  `registry-1.docker.io` 查询 `v8.0.21` manifest 均返回
  `sha256:831f2fd42d5dbbe708ee88b661867ca173d7b57daad955330d2bdd98f5b7535b`
  （OCI index，header 与 pull Digest 一致）。
- fixture 验收（复用已入库参数化 `outputs/fixture-v8020.ps1 -Tag v8021`）：
  repo 源 acceptance 脚本分块上传 sha256 双端校验、py_compile 通过；
  v8.0.21 实体二进制（sha256 前缀 `2fe7df9737a863dd`，digest-pinned
  `docker create` + `docker cp` 抠出）在 `unshare --mount --net --fork`
  私有 namespace 内运行：目录契约（12 ID 在册、墓碑 fail-closed）→
  overload（`200 overload=true upstream_calls=1`）→ cooldown（`503
  upstream_calls=0`）→ 62s recovered（`200 completed=true
  same_process=true`）→ `actual_health HEALTH_OK`；updater 四场景
  `start_fail` exit=1+还原、`model_exposure` exit=1+回滚、`transient`
  exit=10+UNVERIFIED、`success` exit=0。`ACCEPTANCE_RESULT=PASS`、
  `CLEANUP_OK`；镜像在 fixture 前预拉取并再次校验 digest。生产容器进程
  （PID 505281，`/CLIProxyAPI/CLIProxyAPI`）经 `/proc` exe/cwd/netns 核实
  为容器本体，非 fixture 泄漏。

## 投影收敛（升级前置，独立事务）

- 升级前例行严格 doctor **红**：`drift=cpa_policy.py MISMATCH`——54d5e06
  （死常量清理）在 10/8 投影之后改动了 HEAD blob，远端为旧版。这正是
  2026-10-07 审查"待 -Apply 批次收敛"类遗留的现存一项（本轮实际漂移仅此
  一文件）。
- `cpa_bwg_guardrails.ps1 -Apply`：9 投影路径全 `PROJECTION_HASH_VERIFIED`
  （`cpa_policy.py` = HEAD 期望哈希 `a5d01064…`）、容器重启后
  `READY_STATUS=200`、备份 `cpa-guardrails-backup-20261008T163309Z`、
  `GUARDRAILS_APPLIED`；复跑 doctor `DOCTOR_CONTRACT_OK`。

## 升级事务

`outputs/cpa-v8021-canary.sh`（仓库 sha256
`b0994be20360d76c598ecd4cf4627c59f62ef52dfd19b74107645ae39aaeaee2` 双端一
致，`bash -n` 通过，远端 `/root/cpa-v8021-canary.sh`），保留
`cpa-auto-update.sh` 全部本地安全栅栏：flock 共享维护锁 → compose 现值
精确钉校验（`v8.0.20@bdd21270…`）→ error-dump 收权 → 前置 generation 门 →
备份根健康 → 备份 compose → digest-pinned pull（fixture 已预拉取）→ 原子
单行替换 → `docker compose config --quiet && up -d --pull never` → 后置
generation 门 → 任一失败自动回滚。不 prune。

- 第 1 次尝试 16:27:21Z：**前置 generation 门 exit 10 暂缓，零变更**——
  `glm-5.3-flash` 上游瞬断（目录 12 ID 满编排除目录缺名；容器日志零错误，
  read 侧无重试，符合"瞬态 4xx/5xx 不重试"契约）。
- 第 2 次尝试（间隔约 7 分钟）16:34:59Z → 16:35:05Z：`OK: updated
  v8.0.20 -> v8.0.21`，6 秒完成，前后 generation 门均 `HEALTH_OK`，
  回滚未触发。
- 备份：`backups/20261008T163500.656022949Z-from-v8.0.20-canary-v8021/`。
- 回滚集：19 个 cli-proxy-api 镜像 tag 全部保留（含 v8.0.20 `bdd21270`），
  未 prune。

## 验证与风控复核

- **post doctor（两次）**：容器 `v8.0.21@831f2fd4` `restart=0`、
  `MODEL_IDS_UNKNOWN=none`、7×投影 MATCH、`oauth_monitor=OK`、
  `cooldown_state=none`、`luna_state=available`、`DOCTOR_CONTRACT_OK`。
- **quality-canary**（OAuth lane 默认豁免，`PROBE_BUDGET
  oauth_lane=excluded:default_opt_out`）：5 路 200/stop——`gpt-6-astra`
  200/11.0s、`deepseek-v4.1-flash` 200/1.8s、`gpt-6.1-sol-input`
  200/3.4s、`glm-5.3-flash` 200/3.3s、`deepseek-flash` 200/0.7s；
  `gpt-6-astra-ciii` 502 = slot2 CIII 上游慢性故障（与 v8.0.15/16/20 期
  同型，非本次回归），该 502 随即触发粘性再隐藏（保护层按设计工作），
  后续 `generation-all` 目录门按契约暂缓（exit 10、零额外请求）。
- **sol-91 受控单发**：503/0.21s——上游 distributor 明确回答
  `model_not_found: No available channel for model gpt-6.1-sol under
  group default`（含对方 request id）；slot3 主机存活（直连 401 要求
  令牌、connect 0.11s）。该路由当日 03:4x 于 v8.0.20 下曾 200，上游渠道
  状态在其间自行变化，属上游侧涨落非升级回归；按契约不重复探针。
- **受控 OAuth 回放**（单发不重试，2026-10-08 16:39Z）：loopback 经
  admission(8318) → CPA v8.0.21 → 上游全链，`gpt-6.1-sol`、"Reply with
  exactly: OK"、1024 token 上限、api-keys 认证（服务端读取不回显）：
  `HTTP=200 / 3.44s / status=completed / OUTPUT='OK' / Retry-After=absent`。
- **自然流量（16:35Z→16:50Z 窗口）**：28 请求 = 22×200 + 扫描噪声
  （2×400、1×401、4×404），零 5xx、零 429、零 limit 拒绝；admission
  journal 自升级起 18 个 upstream_result、零拒绝；容器日志零
  panic/fatal。用户真实流量经 v8.0.21 全链正常。
- **粘性路由**：重启后窗口 0 hit / 9 miss——session-affinity 绑定为容器
  内存态，16:35 重启清空后重建属预期，非回归。
- **缓存命中率**（`cpa-health.py cache-canary`）：
  - DeepSeek lane：`hit_ratio=0.9579` 双样本一致（3712/3875），与
    v8.0.15/v8.0.16/v8.0.20 基线逐位一致——升级零回归。
  - GLM lane：样本 1 冷启动 0（首次写入属预期），样本 2
    `cache_read_tokens=4096/4114` → `hit_ratio=0.9956`，与 v8.0.20 基线
    一致。按缓存约束纪律（只做请求侧约束、不盲目开启
    `support-prompt-cache-key`）无配置改动；两条可缓存 lane 均处天花板。
  - v8.0.21 未引入缓存相关 lane 语义；#6450 的 reasoning 密文保留对
    responses wire 的上游缓存键稳定性是边际正向。
- **探针预算总账**（真实 provider generation）：本轮合计 13 发（1 前置门
  瞬断 + 6 quality-canary + 1 sol-91 单发 + 1 OAuth 回放 + 4 cache-canary
  双样本×2 lane），其中上游侧失败 3 发（GLM 瞬断、astra-ciii 慢性、
  sol-91 渠道缺位）；OAuth lane 恰好 1 发。fixture 全程合成上游，零真实
  provider 流量。

## 回滚

必要时按 [cpa-manual-rollback.md](../runbooks/cpa-manual-rollback.md) 场景 A：

```bash
cp -a /opt/cliproxyapi/backups/20261008T163500.656022949Z-from-v8.0.20-canary-v8021/compose.yml /opt/cliproxyapi/compose.yml
docker compose -f /opt/cliproxyapi/compose.yml up -d --pull never
```

v8.0.20 镜像（`bdd21270`）已确认本地保留。

## 证据层级

| 层级 | 结论 |
| --- | --- |
| `repo_verified` | 事务/fixture 脚本 sha256 双端一致；digest 双信源一致；本仓改动为脚本入库+投影收敛+证据文档 |
| `filesystem_projected` | -Apply 后 7×投影 MATCH，doctor `DOCTOR_CONTRACT_OK` ×3 |
| `host_loaded` | 容器 `v8.0.21@831f2fd4` restart=0、双 generation 门 HEALTH_OK |
| `controlled_live_replay` | OAuth lane 受控回放 PASS（200/3.44s/completed/OUTPUT OK/无 Retry-After，单发）；quality-canary 5 路 200（唯一 502 为既有上游慢性故障）；sol-91 503 归因上游渠道缺位；cache-canary 双 lane 基线逐位一致 |
| `natural_live_accepted` | 升级窗口自然流量零 5xx/429/limit 拒绝、admission 零拒绝、容器零 panic；真实流量持续经 v8.0.21 全链 |

## 后续源码审查加固（2026-10-09）

- 历史事务完成后，本地复核发现该一次性 canary 的 `INT` / `TERM` 处理曾
  共用 `rollback` 默认退出码，信号到达成功命令后可能沿用 0；现分别显式
  传入 130 / 143，并保留 `ERR` 的原始失败码。
- 脚本现在要求 `VPS_SSH_LAUNCHER_PROFILE=bwg` 才继续；新增本地入口
  `outputs/deploy-cpa-v8021-canary.ps1` 固定使用 `-Profile bwg` 和共享严格
  SSH 主机校验传输，并注入该标记。此标记约束仓库支持的调用路径，本身不是
  远端独立身份证明。
- 这是仓库源码与本地调用入口的加固；本次没有重放 canary，也没有更新 VPS
  上的历史副本。上文脚本 SHA-256 仍指向 2026-10-08 实际执行的原始载荷。
- `repo_verified`：shell 信号与 profile 守卫回归用例通过；PowerShell 解析与
  Bash 语法检查通过。未据此声称远端当前副本已投影。
