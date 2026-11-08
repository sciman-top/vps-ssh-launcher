# 交接书：bwg CPA 风控/性能审查（2026-11-08）

> 交接对象：接手的 AI 或工程师。
> 本文件只写**当前真实状态、已验证结论、已知错误、未完成项与边界**。
> 不要相信任何未在本文件标注证据层级（`repo_verified` / `host_loaded` /
> `live_accepted`）的结论，包括本文自己的推断。

## 0. 先读这三条，它们最容易出错

1. **admission 不 import `cpa_policy`。** 2026-11-08 的
   [台账](change-evidence/20261108-bwg-cpa-cache-ttft-telemetry-and-memory.md)
   曾把 admission RSS "724 → 384 MiB" 记为 policy 按需加载的收益。**这是错误归因**：
   `grep -c cpa_policy cpa-admission.py` = 0。真实原因只是部署事务重启了进程，
   清掉了 RSS 累积；重启后 45 分钟它已回到 462 MiB 并仍在爬。policy 的内存改造
   只对**执行 policy 的那个短命进程**有效，不改善常驻的 admission。
2. **`max-retry-credentials` 现值是 3，不是 1。** 文档里 `1` 的表述是历史；权威值
   以 `scripts/remote/cpa_policy.py` 的 `EXPECTED_CONFIG_PATHS` 与远端
   `/opt/cliproxyapi/config.yaml` 第 40 行为准。
3. **WorkBuddy 有 14 份凭据（7 CN + 7 INTL），不是 3 份。** `workbuddy-tier2-*`
   是 CN（`domain=www.codebuddy.cn`），`workbuddy-intl-*` 是 INTL。

## 1. 环境与访问

- 仓库：`D:\CODE\vps-ssh-launcher`，baseline `main` / upstream `origin/main`。
- 目标机：bwg（`144.34.229.116:29712`），另有一台 `zz`；**本任务只动 bwg**。
- 连接真源：`%APPDATA%\vps-ssh-launcher\target.json`（含明文密码，**不得外泄、
  不得写入证据/提交**）。
- 直连命令（本机已验证）：
  `./.venv/Scripts/python.exe ssh_tool.py --profile bwg run --command '<cmd>' --command-timeout 120`
- 真实 SSH 集成测试默认跳过；需显式 `$env:VPS_SSH_LAUNCHER_RUN_INTEGRATION="1"`。
- **不要**用 `run.cmd` 传带管道的命令（引号会被破坏）；**不要**在远端用
  `journalctl --since 6h`（该机 journalctl 不认这个单位，要用 `"6 hours ago"`）。

## 2. 当前真实状态（2026-11-08 15:30 +08:00）

### 远端

| 项 | 值 | 层级 |
|---|---|---|
| CPA 容器 | `v8.0.59`，pin digest `40dda818…`，`restarts=0` | `host_loaded` |
| WorkBuddy 插件 | `0.9.88`（官方件，已注册） | `host_loaded` |
| admission | 运行中，uptime 约 45 min，RSS 462 MiB / HWM 497 MiB | `host_loaded` |
| admission 完整性 pin | `dd025ad90c8432159cc4dcf1849b3ddba600f23e874fa6e4c6021a7ac34451a0` | `host_loaded` |
| strict doctor | `DOCTOR_CONTRACT_OK`，14 项投影全 MATCH | `host_loaded` |
| lane-hold | chatgpt / workbuddy / antigravity `OPEN`；claude `HELD`（tombstone） | `host_loaded` |

### 仓库

- 已推送到 `origin/main`：`aaa9f6c`（遥测+探针）、`5565ed2`（探针纯标准库+夹具）、
  `346a5a2`（台账）。
- **工作树当前有别的会话未提交的改动**：`docs/runbooks/cpa-failure-triage.md`、
  `scripts/cpa_natural_traffic_audit.py`。**不要替我提交或回滚它们**，先问用户。
- 全量门禁：`pytest` 1157 passed / 18 skipped；bandit / ruff / ruff-format / mypy 干净。

## 3. 本轮已验证的结论（可直接复用）

### 3.1 缓存命中率不是瓶颈

24h 按模型聚合 `usage_cached / (usage_cached + usage_in)`：

| 模型 | 请求 | 命中率 |
|---|---|---|
| `deepseek-v4.1-flash`（WorkBuddy） | 215 | 97.6% |
| `deepseek-v4.1-flash-sg` | 424 | 99.1% |
| `gemini-3.8-flash-high` | 724 | 49.3% |
| `gpt-6-luna` | 176 | 45.1% |
| `gpt-6.1-sol` | 130 | 48.3% |
| `gpt-6.1-terra` | 324 | 49.0% |
| `glm-5.3-flash` | 448 | 49.4% |

线上现有 `/healthz` 的 `telemetry.models.<alias>` 可直接读，不必再 grep journal。
剩余杠杆在**客户端上下文压缩**（一次 compact 让整段前缀重算），不在网关。

### 3.2 吐字慢不是网关造成的

`waited_ms`（admission 排队）p50 0–2 ms / p90 2–18 ms；`upstream_header_ms`
p50 2.6 s / p90 9.1 s；`first_text_ms` p50 6.5 s / p90 20.2 s。
详见 `docs/runbooks/cpa-context-speed-budget.md`。

### 3.3 三条封号线：本窗口内无账号级信号

- OpenAI/GPT：168h 容器日志 `status=401|403` 计数 **0**；社区口径见
  [OpenAI 设备/卡上限](https://www.nediya.com/insights/2026-11-chatgpt-device-limit-shared-pool-gift-card.html)、
  [停用原因](https://qcode.cc/en/codex-account-ban-guide)。
- Antigravity：2 份凭据正常刷新，最近 13:31 有成功调用，7d `401/403` **0**；
  社区口径 [80aj 2026-11-06](https://www.80aj.com/2026/11/06/google-antigravity-gemini-ban/)。
- WorkBuddy：24h 1103×200 / 10×429 / 7×500；7d `lane_hold_latched` 仅 2 次且已释放，
  **无 401/403**；社区口径 [liurb 2026-10-20](https://blog.liurb.org/posts/2026/10/20/workbuddy-connect/)。
- 均为**转述**，非官方公告。不得外推为"本栈已中招"，也不得据此预防性换号/清冷却。

## 4. 已发现但**未完成**的问题（按优先级）

### P1. admission glibc arena 碎片（**已修复并部署**，commit `a22e9c9`）

- 修复：`scripts/remote/cpa-admission.service` 增加 `Environment=MALLOC_ARENA_MAX=2`。
- 部署回执（2026-11-08T08:12Z）：`STAGED_UNIT_OK` → `BACKUP=.../20261108T081209…-admission-arena-cap-20261108`
  → `DAEMON_RELOADED` → `DRAIN_ACQUIRED_IDLE` → `MALLOC_ARENA_MAX=2`（**在活进程环境里验证过**）
  → `ADMISSION_INTEGRITY_OK` → `DEPLOY_OK`。
- 实测效果：

  | 指标 | 改前 | 改后 |
  |---|---|---|
  | RSS | 682 MB（且仍爬升） | 重启后 30 MB；12 分钟后 83 MB |
  | HWM | 673 MB | 121 MB |
  | 64 MiB 对齐 arena 区域 | 14 | 1 |
  | `waited_ms` p90 | 5 ms | 4 ms（无劣化） |

- 回滚：`cp /opt/cliproxyapi/backups/20261108T081209.651380545Z-admission-arena-cap-20261108/cpa-admission.service
  /etc/systemd/system/` → `systemctl daemon-reload` → `systemctl restart cpa-admission`。
- 观察项（未闭合）：`MALLOC_ARENA_MAX=2` 的长期表现（是否仍在数天内爬回高位）
  与高并发时的 malloc 锁竞争。判读口径：`waited_ms` p90 若持续高于基线 5 ms，
  考虑回到默认或改 4。

### P2. 容器内存记账隔离（前提**已被推翻**，候选文件应删除）

- 已确证的事实：
  - `vmRSS` 从重启后的 384 MiB 爬到 536 MiB（约 55 min），`HWM == RSS`。
  - 进程内 **14 个匿名 `rw-p` 区域**，起始地址**精确按 64 MiB 对齐**
    （`75e894000000`、`75e898000000`… 步进 64 MiB），每个 19–54 MB，合计 462 MB。
    这正是 glibc 的 arena 堆（`HEAP_MAX_SIZE` = 64 MiB），**不是** Python pymalloc
    （按 1 MiB 步进）。
  - 数量 14 逼近 `nproc=2 → max_arenas=16` 的上限。
- **根因（决定性实验，2026-11-08）**：glibc **只在某个线程发现 arena 已被占用**时
  才新建 arena，且**永不销毁**。用 16 线程（`Barrier` 同时起步）持续做 8 MB 分配：

  | `MALLOC_ARENA_MAX` | arena 区域 | arena 常驻 | 进程 RSS |
  |---|---|---|---|
  | 默认（16） | **15** | 120.1 MB | 139.2 MB |
  | 2 | **1** | 8.0 MB | 27.1 MB |
  | 1 | **0** | 0 MB | 19.2 MB |

  三种配置 **HWM 都是 139 MB**：说明峰值由业务本身决定，而**常驻**由 arena 数决定。
  生产形态（14 个区域、RSS==HWM）与此完全一致。
- 修复：`Environment=MALLOC_ARENA_MAX=2`（unit 文件）。取 2 而非 1：2 核各一个
  arena，保留基本并行；1 会退化成全局单 arena。
- **代价（必须如实记录）**：同一压力测试下耗时 0.99s → 1.13s（2）→ 1.23s（1），
  即约 +14%/+24% 的 malloc 竞争。这是 16 线程同时猛打 malloc 的最坏合成场景；
  生产 admission 受 GIL 约束，实际影响需以部署后 `waited_ms` p90 对照为准。
- **此前两次证伪是自己实验设计不对，已作废**（保留记录以免重复踩）：
  1. ❌ 用「6 线程各做一次 32 MiB 读」复现不出 arena —— 分配次数太少、无竞争。
  2. ❌ 用「串行 160 线程各分配一次」也复现不出 —— 无并发即无竞争。
  - 教训：arena 只在**并发争抢**时产生，测 arena 必须用同步屏障起跑 + 持续分配。

### P2. 容器内存记账隔离（前提**已被推翻**）

- 原判断"admission 与容器共处 dockerd 的 cgroup，容器撞 512 MiB 会连带杀
  admission"是**错的**。实测：
  - admission 在 `/system.slice/cpa-admission.service`（`memory.max=max`）；
  - 容器在独立的 `docker-<id>.scope`（`memory.max=536870912`）；
  - `docker.service` 自身只占 71 MB；
  - 两个 cgroup 的 `memory.events` 都是 `oom_kill 0`。
- 结论：**不存在**跨 cgroup 的连带 OOM。`scripts/remote/cpa-oom-isolation.conf` 的前提不成立，
  已于 2026-11-08 **删除**（不部署、不留误导性候选）。

### P3. 上游 v8.0.60 未升级

- `v8.0.59` 现役；`v8.0.60` 含与本栈症状直接相关的修复：
  `27762819`（保持会话中 system item 原位以保住 prompt cache 前缀）、
  `374056f2`（deferred tool 经 ToolSearch 加载时保留缓存前缀）、
  `ce38c75c`（`message_stop` 后归还上游连接）、`5f38d557`（SIWC 恢复期保持原生 Codex 可用）。
- 升级必须走既有 soak（`auto-update.sh` 的 72h soak）与换芯事务，**不要手工
  `docker compose up`**。目前 updater 判定 "no newer mature release"。

### P4. 无关本任务但已登记

- `passthrough` 的 `/v1/files` 404 在 24h 内有 1376 次（占 http_error 的绝大部分）。
  可能是客户端行为，未定性。
- `gpt-6.1-sol` 24h 130 次调用中 **77 次只产出 16 token**，同时消费 10 万级输入；
  这是唯一 Plus 账号每分钟 token 预算的主要消耗面之一。处置属客户端行为，网关不应改写。

## 5. 必须遵守的操作纪律（踩过的坑）

1. **`-Apply` 在持续有流量时会拒绝**（`cpa_apply_requires_idle_lanes`，非阻塞锁，
   设计如此）。要投影又不能切在途流时，用"等 drain"事务范式：
   拿 `/run/cpa-admission/requests.lock` 的排他侧并等待，见
   `outputs/deploy-admission-telemetry-20261108.ps1`。
2. **admission 源码改动不能走 `-Apply`**：会被
   `admission_integrity_pin_mismatch` 拒绝。必须用上述事务同时轮换 source 与 pin。
3. **投影失败会自动回滚**，回滚会把文件还原成旧版——本轮曾因此让远端
   `cpa_policy.py` 变回没有 `check` 动词的旧版，导致下一轮探针"失败"。
   失败后**先 sha256 核验远端实际版本**，再决定下一步。
4. **doctor 的 `projection-drift` 比的是 HEAD blob**：源码改了没提交，
   `drift=... MISMATCH` 是预期的，提交后即 MATCH。不要为此改代码。
5. **远端没有 pip、没有 ruff**。任何依赖外部 linter 的检查都只能报"没跑"。
   `cpa_policy.py check` 已改为纯标准库 `ast`。
6. **本机 pytest 曾在打印 summary 后挂死**（已修：daemon 线程 + `block_on_close=False`）。
   若再次出现，先查是否有**别的会话遗留的僵尸 pytest**（
   `Get-CimInstance Win32_Process -Filter "Name='python.exe'"`），不要直接归因于代码。
7. **pprof 在容器网络命名空间**：`observability.pprof.addr=127.0.0.1:6060` 只在该
   netns 内可见，宿主要用 `nsenter -t <pid> -n curl ...`。诊断完**必须恢复原配置**
   并核对 `config.yaml` 的 sha256（基线前缀 `360657da02ec98e8`）。
8. 仓库有**并发会话**：提交前先 `git log --oneline -5` 看有没有别人刚提交，
   提交只 add 自己的文件，push 前 `git fetch` + 审查 `HEAD..origin/main`。

## 6. 建议接手顺序

1. 跑一次只读体检，确认现状与本文件一致：
   `pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg`
   （期望 `DOCTOR_CONTRACT_OK`；若 MISMATCH 先看是不是未提交的源码改动）。
2. 读 `/healthz` 的 `telemetry`，确认缓存/首字指标仍在出数。
3. 做 P1（arena 碎片 A/B），因为它是当前唯一"能实打实降 300+ MB 且风险可控"的项。
4. P2 要用户给窗口（重启 dockerd）；P3 走既 upgrade 事务。
5. 任何远端写入都留备份路径与回滚命令到 `docs/change-evidence/`。

## 7. 明确不要做的事

- 不要新增/轮换账号，不要清冷却，不要跨 realm 续打，不要改并发来"吸收"风控。
- 不要为提命中率改写 prompt 前缀（会让缓存命中归零，反而放大限流）。
- 不要把社区封号叙事当成本栈已中招的证据。
- 不要在 `claude` lane 上做任何预防性隔离；该开关权属用户。
- 不要动 `%APPDATA%` 下的配置与凭据，不要打印凭据内容。
