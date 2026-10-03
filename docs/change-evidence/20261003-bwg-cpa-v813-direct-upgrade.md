# 2026-10-03 BWG CPA v8.0.13 升级证据（用户授权直接升级，绕过上游耦合前置门）

## 目标与范围

- 目标：将 `bwg` 的 CPA 从 v8.0.12 升级到 v8.0.13（release 2026-10-03
  09:02 UTC，未满 updater 的 72h 成熟期；用户明确指令"直接升级"）。
- 范围：**仅 `compose.yml` 的镜像声明**。`config.yaml`、OAuth 凭据、Nginx、
  admission、路由清单**零改动**。不触碰 `zz`，不轮换 key，不新增上游重试。
- 与既有手动 canary 的**唯一差异**：前置/后置的 generation 门被替换为
  readiness 门 + 健康非槽位1 lane 的 loopback 生成探针；原因见下节。

## 为何走"直接升级"而非标准 canary

标准 canary（`cpa-auto-update.sh` 语义）在**升级前**要求
`python3 cpa-health.py generation` 返回 0。本次该门返回
`UPSTREAM_UNAVAILABLE`，追因后确认**与待升级镜像无关**：

- 槽位1 `ai.input.im` 上游线上劣化（非短窗口，持续观测中）：
  `deepseek-v4.1-flash` 503、`gpt-6-astra` / `gpt-6.1-sol` /
  `gpt-5.6-terra` 均 502 `Upstream access forbidden`；槽位2
  `codex.ciii.club` 同样 502。
- 三条**健康** lane：槽位3 `35.213.82.91:8003` 200、槽位4 `open.bigmodel.cn`
  200、槽位5 `api.deepseek.com` 200。OAuth lane 个别 `server_is_overloaded`。
- 上游 `/models` 仍能列名、auth 通过 ⇒ **非凭据失效、非 CPA 故障**。

**本地目录门为何被卡死**（精确链路）：`cpa-health.py` 的
`_BASE_ALLOWED_MODELS` 以 `host != "ai.input.im"` 排除整个槽位1，其 channel
模型仅在 `channel_enabled` 时并入 `allowed`；`deepseek-v4.1-flash` **不在任何
optional 集合**，属 `required`。上游把该模型从 CPA `/models` 摘除后，
`required <= ids` 不成立 → `catalog_complete=False` → 非 readiness 模式返回
10。其余缺失模型（`gpt-6-astra`/`gpt-6.1-sol-input`/`gpt-6-luna`/
`gpt-image-2.5`）均在 optional 集合内，不影响门。

**用户决策**：先选择"仅等待上游恢复"（门控窗口）；上游在窗口观察期内未恢复，
随后明确指令"直接升级"⇒ 停止等待窗口，改用下述保留全部本地安全栅栏、
但以**本地就绪 + 健康 lane 探针**替代上游耦合生成门的直接升级事务。

## 上游变更评估（v8.0.12...v8.0.13，共 11 个 commit）

- `ed4d972` `fix(responses)`：补全 reasoning summary 流生命周期事件
  （`added` / `delta` 带 `item_id`+`summary_index` / `done` / part done，
  并合并为单一 completed summary block）。**直接命中本栈 `/v1/responses`
  桌面链路，是修复**。
- `ea8ffd5` `fix(devin)`：OpenAI Responses 格式提前流式输出内容、延迟
  thought step 终止并累积 signature 片段、关闭开放流步骤时 flush 延迟
  reasoning step stops。**同属 responses 流时序修复**。
- `d469266` `fix(auth)`：注册/更新期间先快照 scheduler auth 再解锁，修
  `MarkResult` 并发竞态 ⇒ 对单 Plus OAuth 账号的**正向加固**。
- `a3b7756` `fix(codex)`：execute 阶段先发布主模型 usage 再发布 image tool
  usage。计量顺序修正。
- `d7914af` `fix(codex)`：工具 schema 的 integer 字段归一化映射扩展
  （memories/history/notes/web/collaboration，支持嵌套路径与 union）。
- `2c1dcc7` `fix(claude)`：存在 web search 工具时在 codex request 的
  `include` 加入 `web_search_call.action.sources`。
- `e3abd9a`/`c575291`/`6b037f6` `fix(antigravity)`：Claude 4.6 → 5.5 模型
  替换、token limits 对齐、high model ID 更正。**本栈无 antigravity lane，
  不触达**。
- `5dbce4f` `perf(claude)`：诊断遍历前 ASCII 预过滤。**无 claude lane，
  不触达**（纯性能）。
- `42d484f` `fix(claude)`：408 → `timeout_error` 映射。不触达。
- **无破坏性变更、无新增必配项、无新默认行为影响本栈**。模型替换仅限
  antigravity 通道，本栈目录不含 Claude 4.6 ID。

## 升级事务（直接模式）

脚本 `outputs/cpa-v813-direct.sh`（远端 `/root/cpa-v813-direct.sh`，
`bash -n` 通过，md5 `7945b34ad596c89d14ccfcd2010f652b`），保留
`cpa-auto-update.sh` 的全部本地安全栅栏：

flock（`/run/vps-ssh-launcher-maintenance.lock`）→ 前置 **readiness** 门
→ 备份根健康检查（700 权限）→ 备份 `compose.yml`
（`backups/20261003T143523.307871433Z-direct-from-v8.0.12/`）→
digest-pinned pull（`v8.0.13@sha256:6ce96259…`）→ 镜像存在校验 → 原子单行
替换 → `docker compose config --quiet && up -d --pull never`
→ 后置 readiness 门 → 健康 lane（`glm-5.3-flash`）loopback 生成探针
→ 任一失败自动回滚。

- 结果：`14:35:23Z DIRECT_START` → `14:35:41Z DIRECT_OK`，18 秒完成，
  回滚未触发。
- 日志：`/tmp/cpa-v813-direct.log`。
- 清理：不 prune；v8.0.12 镜像保留为本地回滚集；等待窗口脚本与
  canary 脚本按用户指令停用/留存。

### 等待窗口（先于直接升级执行，已被取代）

`/root/cpa-v813-window.sh`（门控窗口，24×300s，日志
`/opt/cliproxyapi/v813-window.log`）在 `attempt=1 PROBE_OK=0 PROBE_FAIL=3`
后由用户"直接升级"指令停止（`pkill -f cpa-v813-window`），未进入 canary。

## 验证与风控复核

- **post doctor**：`DOCTOR_CONTRACT_OK` 之外出现 `projection-drift
  cpa_provider_routes.json=MISMATCH`。
  - **该漂移非本次升级引入**：`stat` 显示远端
    `/opt/cliproxyapi/cpa_provider_routes.json` mtime = `09:10:21`
    （即 v8.0.12 `-Apply` 时），本次升级只写 `compose.yml`
    （mtime `14:35:30`）。
  - **真因 = 并行会话提交 `bbb6f10`「增补 BWG 槽位 2 CIII 路由」**
    （2026-10-03 22:35:16，恰在本升级窗口内）：槽位2 别名由
    `gpt-6-astra-cii` 改为 `gpt-6-astra-ciii` 并新增 `gpt-6.1-sol-ciii`。
    doctor 的 `want=54110cf3…` = 当前 repo HEAD blob = 工作区；
    `got=dd6617c4…` = 远端**尚未 -Apply** 的旧投影。
  - 即：**并行会话已提交但未 apply**，属其预期中间态；投影源工作区
    经核对干净，其可安全 `-Apply`。本切片不代其执行。
- 容器：`v8.0.13@6ce96259`、`restart=0`、`status=running`、
  `image-available=OK`。
- `MODEL_IDS` = 13 项（含 `deepseek-v4.1-flash`、`gpt-6.1-sol-input`
  在本容器重启后重新入册），`MODEL_IDS_UNKNOWN=none`。
- `cooldown_state=none`、`catalog_oauth_missing=none`、
  `luna_state=available`、`catalog_gpt6_luna=present`。
- `oauth_monitor=WARN_RENEWAL_WINDOW`：days_left 进入 ≤72h WARN 带，
  预期节奏（lead24h 自动刷新），非失败。
- updater 自洽（实跑 `--check`）：`CANDIDATE current=v8.0.13
  target=v8.0.13 soak=72h`，`BACKUP_HEALTH status=ok backups=23`。
- **真实流量（升级后 5 分钟，OAuth lane）**：7 连 `/v1/responses` 200
  （9.7s–30.1s） + `/v1/chat/completions` 200；**升级窗口零 5xx / 429**。
  这是 `ed4d972`（reasoning summary 流事件）与 `ea8ffd5`（提前流式 /
  延迟 thought stop）在本栈 OAuth 链路上的首个真实样本，均正常。

## 边界与后续

- **槽位1 `ai.input.im` 仍处于上游劣化**：本升证据不代表其恢复；其可用性
  与本地门解耦（本栈按用户当次指令以 readiness + 健康 lane 探针替代上游
  耦合门）。后端恢复由既有维护节奏与告警覆盖。
- **并行会话 `bbb6f10` 的 `-Apply` 待其自行执行**：投影源干净，可直接
  `-Apply`；本切片不触碰其文件。此后 doctor 的 projection-drift 会回到
  MATCH。
- 本升级属 release 首日采纳（未满 72h 成熟期），依据 = 用户当次明确授权
  与上游变更评估（纯 `fix`/`perf`、无破坏性变更）。
- `oauth_monitor` WARN 窗口：lead24h 自动刷新按既有节奏。
