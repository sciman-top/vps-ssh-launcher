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

## 3.5 是否需要向上游提 issue/PR（2026-11-08 判定：**不需要**）

本轮所有可执行修复都落在**本仓自己的代码**里，逐项归属如下。不要重复评估：

| 发现 | 归属 | 判定依据 |
|---|---|---|
| accept 队列默认 5 | **本仓** `scripts/remote/cpa-admission.py` | admission 是本仓的 Python 前置代理，上游 `internal/api/server.go` 用 `net.Listen` + Go 默认，与此无关 |
| glibc arena 碎片 | **本仓** 运行配置 | 上游是 Go 二进制，堆由 Go runtime 管理；本仓 admission 才是 glibc malloc 进程 |
| policy 探针依赖 ruff | **本仓** doctor 工具 | 上游无 cpa_policy |
| pytest 退出挂起 | **本仓** `tests/` | 与上游无关 |
| `/v1/files` 404 | **无主** | CPA 本就未实现该端点；请求来自宿主 `172.19.0.1` 的 8–15s 探活，属客户端行为 |
| v8.0.60 未升级 | **上游已修，等 soak** | 不需要 issue；soak 到期 `2026-11-10T20:13:52Z` |
| 6008 错误信封 | 上游 **#7313 已提** | 见 `20261107-cpa-6008-error-envelope-issue-draft.md`，勿重复开案 |

- 上游 Go 侧既有内存类 issue（#2215 / #3048 / #3947）**均已 closed**，且都是
  Go 进程层面，与本轮 Python admission 不是同一问题，引用它们会误导。
- 已有上游 PR 惯例与在途项：`mmqz/cpa-multi-plugins#50`（workbuddy 截断流误报）、
  `#71`（priority reclaim），本任务未触及插件，不动。
- 结论：**本轮不提 issue、不提 PR**。若将来 admission 被上游收编为官方组件，再把
  accept 队列与 arena 两条作为可复现缺陷上报。

## 4. 已发现但**未完成**的问题（按优先级）

### P0. admission accept 队列过浅（**已修复并部署**，commit `1076574`）

- 现象：`ss -ltn` 显示 `LISTEN 0 5`。受控并发验收（走 415 拒绝路径，**不触达任何上游**）
  实测：并发 1 → 447 req/s；并发 8 → **57 req/s，p99 1060 ms**；并发 32 →
  29 req/s，且出现一次连接重置。而 admission 自身 CPU 为 **0%**。
- 根因：`ThreadingHTTPServer` 的 `request_queue_size` 默认 **5**，突发在
  创建 Handler 线程之前就被内核队列串行化了。
- 修复：`Server.request_queue_size = 128`。修复后同口径复测：
  并发 8 → **550 req/s，p99 22 ms**；16 → 731 req/s，p99 28 ms；
  32 → 575 req/s，p99 46 ms，无重置。RSS 全程平稳 64.6 MB。
- 边界：backlog 只吸收"进入 lane 之前"的突发；lane 的 `max_inflight` /
  `max_pending` 与 120s 队列预算仍决定是否准入，**不会让账号多承担上游请求**。
- 部署回执：pin `5763ad94 → f48f9d1c`，`DEPLOY_OK`，备份
  `…/20261108T084617…-admission-backlog-20261108`，`LISTEN 0 128` 已确认。
- 回滚：`cp /opt/cliproxyapi/backups/20261108T084617.889146712Z-admission-backlog-20261108/cpa-admission.py`
  与同目录 `cpa-admission.sha256` 回原位 → `systemctl restart cpa-admission`。

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

### P3. 上游 v8.0.60 未升级（**时机未到，不是遗漏**）

- `v8.0.59` 现役；`v8.0.60` 含与本栈症状直接相关的修复：
  `27762819`（保持会话中 system item 原位以保住 prompt cache 前缀）、
  `374056f2`（deferred tool 经 ToolSearch 加载时保留缓存前缀）、
  `ce38c75c`（`message_stop` 后归还上游连接）、`5f38d557`（SIWC 恢复期保持原生 Codex 可用）。
- **2026-11-08T08:47Z 实测**：docker hub 已有 `v8.0.60`（amd64 digest
  `8472aae0…`，pushed 2026-11-07T20:15Z），但 `cpa-auto-update.sh` 的 `mature()`
  要求 `published_at` 满 72h。`v8.0.60` 发布于 **2026-11-07T20:13:52Z**，
  soak 到期 **2026-11-10T20:13:52Z**，当时剩余 **59.4h**。
- 结论：updater 判 "no newer mature release" 是**正确**的，不是故障。
  **不要手工 `docker compose up` 绕过 soak**。
- 到期后：等 timer（每日 04:22Z）自动换芯；或按 runbook 手动触发并复核。
  换芯后必须复核 `cpa_policy` 的 `EXPECTED_CONFIG_PATHS` 与 v8.0.60 配置键是否仍匹配
  （若有键被重命名，strict doctor 会以 `POLICY_FAILED` 报出）。

### P4. 两个已定性、**无需动作**的观测（我此前的描述有误，已更正）

- **`gpt-6.1-sol` 的"16 token 小调用"不是额度浪费**。
  2026-11-08 复查 24h：187 次调用中 81 次 `usage_out<=32`，但**平均输入仅 53 token**，
  合计只消耗 4293 输入 token。这是轻量探活/标题类请求，几乎不占账号预算。
  **更正**：先前"77 次只产出 16 token 却消费 10 万级输入"的说法**错误**——
  那两个数字来自不同的请求集合（`usage_in` 聚合 vs 单次 `usage_out`），不可相除。
- **`/v1/files` 的 404 不是异常**。
  24h 内 1453 次（CPA 侧计数），全部来自 **`172.19.0.1`**（宿主在 docker 网桥上的地址，
  绕过 nginx，所以 nginx 日志里查不到），间隔 8–15s，稳定探活节奏。
  CPA 未实现该端点，返回 404 属预期；它不触达任何上游账号、不消耗额度。
  若要消掉这条 warn 噪声，应关掉该探活客户端**或**在 CPA 侧加一个 404 静默规则，
  两者都不是风控问题。

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

## 8. 接手复审补充（2026-11-08 第二会话）：封号潮外部情报 × 上游配置面普查

> 触发：用户报告社区再度出现 OpenAI/GPT、Antigravity/Gemini、WorkBuddy(cn/intl)
> 封号潮，要求对 bwg CPA 做防封/限流/降智全面复审。本节只记录**增量**事实与判定，
> 不推翻上文。源码依据：CLIProxyAPI 参考仓 main@91e0dee8（v8.0.60+，已按授权更新）
> 与 cpa-multi-plugins@v0.8.96。

### 8.1 外部情报（全部为转述，非官方公告；不得外推为本栈已中招）

- Antigravity：2026-11-06/07 新一波 403 `TOS_VIOLATION`（随封下发 Error ID），
  多名首犯用户"全部第三方访问被吊销"；社区怀疑误判潮。申诉走邮件/表单，
  论坛贴 Error ID 不进复核队列（[forum 198644](https://discuss.ai.google.dev/t/needed-help-appeal-for-antigravity/198644)、
  [198629](https://discuss.ai.google.dev/t/appeal-antigravity-403-tos-violation-2026-11-06-wave-first-offence-all-third-party-access-revoked/198629/2)）。
- OpenAI：2026-11-07 又一波 Plus/Pro 停用，转述称无邮件直接删号；高频归因
  IP 信誉、支付渠道、多号关联、第三方工具（与 §3.3 的 nediya/qcode 口径一致）。
- WorkBuddy（CodeBuddy）：9–10 月起对反代打击，30 天不可解封、敏感词静默风控
  （[linux.do 2905723](https://linux.do/t/topic/2905723)、[3053639](https://linux.do/t/topic/3053639)、
  [3081912](https://linux.do/t/topic/3081912)）。官方客户端形态最安全；
  插件 fingerprint 按账号 salt 派生 deviceID（cpa-multi-plugins `fingerprint.go`），
  "整池同机签名"风险上游已规避。

### 8.2 本栈新鲜探针（2026-11-08 09:20–17:25 UTC，48h 容器日志 + 24h LCP）

- 上游账号级封号信号：`invalid_grant` / token expired / `ban|suspend|locked`
  计数 **0**，与 §3.3 一致。
- 48h 内 26×401 全部是 `172.19.0.1` 对 `/v1/models` 的无键/错键本地探活
  （不触上游）；429 仅 WorkBuddy intl `6008 Credits exhausted`——14:22Z 单请求
  （`5316b8cf`）按 `max-retry-credentials=3` 级联 3 份耗尽凭据后止，行为符合预期。
- LCP 会话亲和：24h hit 330 / miss+new-binding 24。
- doctor 复跑 `DOCTOR_CONTRACT_OK`；`model_substitution` 7d=483，仍为单一已知
  变体对 `gemini-3.8-flash-high <== gemini-3.8-flash-n`。

### 8.3 上游配置面普查（对照生产 config，逐键判定）

判定三档：**已用** / **保持默认（不动）** / **可选（用户门）**。未列出的键与本轮无关。

| 键 | 语义 | 判定 |
|---|---|---|
| `upstream.claude.header-defaults.*`（UA/版本/os/arch/timezone 等 8 键） | Claude 指纹族 | lane HELD 无凭据；复挂时再评 |
| `upstream.codex.header-defaults.user-agent` | Codex UA 覆盖 | 保持默认；无实证指纹缺陷前自定义独值反而突出 |
| `upstream.codex.signin-with-chatgpt-identifier` | SIWC 登录/刷新 agent hint，默认 `CLIProxyAPI` | 保持默认（全体用户共用默认值） |
| `routing.retry.max-retry-interval` | 重试退避上限 | `request-retry=0` 下无意义，不动 |
| `credentials.concurrency.*` / `in-flight` | 插件宿主每凭据并发台账 | 默认即可 |
| `requests.streaming.bootstrap-retries` | bootstrap 安全重试 | 保持 0（避免"同请求双发上游"嫌疑） |
| `oauth-request-scoped-errors` / per-credential `request-scoped-errors` | 自定义错误→请求级不冷却 | 已仅在 opencode zen（CF 1010）使用；6008 需要冷却，不加 |
| `quota-exceeded.antigravity-credits` | 积分耗尽处置 | `antigravity-credits=false`（免费配额模式）下不适用 |
| antigravity `sensitive-words` | 零宽混淆词表 | **上游无内置默认表**（消费点仅读配置，空=不混淆）；现 `[gpt,openai]` 为既定策略，维持 |
| antigravity UA | per-auth metadata `user_agent`，缺省内建 | 无 config 键可调，维持 |
| `server.response-compression` | 下行压缩 | 客户端经本机 admission，收益≈0，不加 |
| workbuddy `reserve_credits` | 每账号积分预留护栏（auth 级可覆盖） | 可选：能减少 6008 尾部打穿；改变花额度行为，**用户门** |
| workbuddy `login_platform` | 新登录客户端族 CLI/ide | 可选：下次新登录时定，现不动 |
| workbuddy `models_global` / `usage_report_*` | 全局模型表 / 用量上报 | 不需要 |

### 8.4 判定与遗留

- **本仓代码本轮零改动**：P0/P1 已由上午会话部署收口，本轮未发现新缺陷。
- v8.0.60（`27762819` 保 system item 原位护 prompt-cache 前缀、`374056f2` deferred
  tool 保缓存前缀、`ce38c75c` message_stop 后连接复用、`5f38d557` SIWC 恢复期不误
  冷却）soak 到期 2026-11-10T20:13:52Z，timer 2026-11-11T04:22Z 自动换芯；换芯后
  复核 `cpa_policy` 键表与 `/healthz` telemetry（P3 既定，勿绕过 soak）。
- 可选远端动作（均须用户点头，均不紧急）：
  1. `access.api-keys` 轮换（见 §8.5）；
  2. workbuddy `reserve_credits`。
- 降智判定：`gemini-3.8-flash-high→-n` 是 Google 侧响应模型行为，CPA 仅告警不改写，
  网关层无杠杆；继续用 doctor `model_substitution` 观测，勿为改名做别名改写。

### 8.5 凭据卫生事件（记录在案）

- 本会话做 config 脱敏投影时，`access.api-keys` 列表项的后续行未被掩码规则覆盖，
  曾短暂进入会话 transcript（仅本机；未写入任何文件/提交）。该键为网关访问键，
  轮换会同时影响全部本地客户端，低紧急度；是否轮换由用户决定，轮换须同步
  cockpit/客户端配置。教训：块级列表值需逐行掩码，不能只掩首行。
