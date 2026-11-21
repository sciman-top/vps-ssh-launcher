# BWG CPA v8.0.80 升级、WorkBuddy CN 0.21.29 与深度风控/流式优化验收（2026-11-21）

**目标**：围绕 `bwg` 上多 OAuth 账号（OpenAI/Codex、Antigravity/Gemini、WorkBuddy CN、Claude 已退役车道）与多 API key 渠道的封号/限流/降智风控、吐字延迟与缓存命中率做全链路审查与落地升级，包括 `CLIProxyAPI` 升级至 `v8.0.80-gemini-raw-agy-effort`、`workbuddy-provider` CN 升级至 `0.21.29`、守卫策略与接入层修复、内核 TCP 调优、隔离网络命名空间模拟以及在线受控验收。`zz` 未触碰，`aliyun` 仅只读排障未写入；保留全部既有账号与渠道，未新增 OAuth 生成探针。

## 仓库提交与改动摘要

1. **`e4520af`** — `fix(cpa): stop TOS-pause 503 cascade, serialize OAuth refresh, tighten 503 capacity pin and Antigravity refresh age`
   - `scripts/remote/cpa_policy.py` 与 `scripts/remote/cpa-guardrail-apply.sh`：
     - 钉定 `oauth.auth-auto-refresh-workers: 1`（上游默认 16 并发 worker），串行化同机 OAuth 后台刷新，消除同 BWG 出口 IP 上多个 Antigravity 账号在同一秒并发打向 `oauth2.googleapis.com` 的锁步突发。
     - 新增 Antigravity 请求级止损规则（RSE）：`{status: 503, match: ["antigravity pool paused after TOS_VIOLATION"], action: "stop"}`，中和上游 `#7748` 在 `tos-pause-window` 触发后 503 级联冷却全部健康兄弟凭据的缺陷，并由 `cpa-admission` 承接 503 车道退避。
     - 显式投影 `requests.streaming.keepalive-seconds: 15` 与 `requests.streaming.send-keepalive-before-ft: true`，防止长思考首字前空闲断流/重试重放。
     - 收紧 `capacity_statuses` 契约校验为同时包含 `{429, 503}`。
   - `scripts/remote/cpa-guardrail-doctor.sh`：新增 `refresh_time_for_record()`，为 `provider == "antigravity"` 补齐顶层 Unix-ms `timestamp` 回退解析，修复全部 7 个 Antigravity 凭据此前报告 `refresh_age_hours=unknown` 的可观测盲区。
   - 同步更新 `tests/script_validation_support.py`、`tests/test_cpa_policy_runtime.py`、`tests/test_cpa_guardrails_script.py`、`tests/test_cpa_updater_apply.py`、`tests/test_cpa_health_doctor.py`。
2. **`e3a439c`** — `chore(cpa): pin bwg fork binary sha256 to v8.0.80-gemini-raw-agy-effort`
   - `scripts/remote/fork-binary.sha256` 更新为 `7727644c2cefd93b31e59ab4b66fc987fc6ed9cca6f6abe9a42577e49ac3cdcc`。
3. **`717c15f`** — `chore(cpa): pin workbuddy-provider CN version gate to 0.21.29`
   - `scripts/remote/cpa-guardrail-workbuddy-version-check.sh` 将 `EXPECTED_CN` 升级至 `0.21.29`（`EXPECTED_INTL=0.1.40` 维持关闭态 `closed_dark`），同步更新 `tests/test_cpa_guardrails_script.py`。
4. **`75a29d0`** — `fix(cpa-admission): disable Nagle + coalesce SSE chunks, catch VALIDATION_REQUIRED and chatgpt prose errors`
   - `scripts/remote/cpa-admission.py`：
     - 启用 `Handler.disable_nagle_algorithm = True`，并在 `_write_chunk()` 中将分块头/体/尾合并为单次写入再 `flush()`，消除环回链路小块 SSE 的 Nagle/Delayed-ACK 卡顿并将单块系统调用从 3 次降至 1 次。
     - 将 `"VALIDATION_REQUIRED"` 加入 `STANDING_RESTRICTION_REASONS`，使 Antigravity 手机号/身份核验拦截直接锁存为更强的 `account_restricted` hold。
     - 扩展 `protocol_errors()` 解包 `chatgpt-oauth` 上 `upstream <status>: {json}` 包裹文本，并修正 `oauth_risk_signal()` 判定次序，仅在 `not errors` 时允许 `status == 403` 走 `edge_denial_body()` 豁免，避免上游 `account_deactivated` 等结构化 403 被误判为普通 WAF 页面。
   - `scripts/remote/cpa-admission-integrity-pin.txt` 轮换至 `067c65684aaf6bee5cc993faf64b3c0bc57ac3166afdd66c70d1e7b501a2c9bb`（渲染后 `cpa_request_guard.py` SHA256：`5647120b1eea852d6ce67a34d7c640a7331b9b079983d992fb71f73d0131a342`）。
   - `tests/test_cpa_admission.py` 新增 3 项单测覆盖 Nagle 禁用、`VALIDATION_REQUIRED` 锁存以及 `chatgpt-oauth` 文本包裹 429/403 风险识别。

## 远端升级与内核调优（仅 `bwg`）

- **Phase 1B（守卫策略投影）**：运行 `cpa_bwg_guardrails.ps1 -Profile bwg -Apply`，输出 `GUARDRAILS_APPLIED`、`READY_STATUS=200`、`HEALTH_OK`，17/17 项 `PROJECTION_HASH_VERIFIED`。投影后 `config.yaml` SHA256：`875af114cb431c38e8d9dc86dec3cdb53ec3049c8b822156ace74442a2ef070a`。
- **Phase 2（CPA `v8.0.80 + gemini-raw + agy-effort` 构建与 drain-locked 换芯）**：
  - 上游基线：`CLIProxyAPI` `v8.0.80`（commit `f5c5681a8361c9da53e93c60b6507816eee7468b`，带入 Antigravity OAuth 刷新 UA/传输对齐 `0861f9e1`、按凭据 `request.sessionId` `3a24d03c`、403 探测退避解耦 `f8e297aa`、刷新后 credits 预热死锁修复 `99a1c1ca`、`PrepareRequestAuth` 刷新锁串行化 `0a34244e` 与 RSE 止损尊重 `2f57a5a6`、credits 回退 stop-rules `a35e8be3`、Claude foreign advisor 剥离 `91a3f455`、插件 after-auth 终止修复 `5bfae629`）。
  - 叠加既有 `gemini-raw` 与 `agy-effort` 补丁，容器 `golang:1.26.10-bookworm` 内单测 `responses`（0.124s）、`thinking`（0.059s）、`helps`（8.332s）全通（`UNIT_TESTS_OK`）。
  - 产物：`/root/cpa-v8080-build/CLIProxyAPI-8.0.80-gemini-raw-agy-effort`（SHA256 `7727644c2cefd93b31e59ab4b66fc987fc6ed9cca6f6abe9a42577e49ac3cdcc`）；基础镜像 `eceasy/cli-proxy-api:v8.0.80@sha256:0695ae8721d623ed31179369fc7127d469f10a14a37a49c366e82a16383662f8`；派生镜像 `cli-proxy-api-fork:v8.0.80-gemini-raw`（Image ID `sha256:d7af79e6d57c00f541a4062e2e8d9ec26b89e24491fe208e795b62edad38a41e`；旧镜像 `v8.0.79-gemini-raw` `sha256:d8f6aad7ebea5abc80e8b4a82364141ecf9eba99f4a34838dd6f0e6af9e5cedb` 本地保留待回滚）。
  - 换芯回执：`DRAIN_OK`、`IMAGE_ID_READBACK_OK`、`START_LOG_CLEAN version=v8.0.80`、`RUNTIME_BINARY_OK`、`READINESS_OK`、`AUTH_BYTES_UNCHANGED`、`LANE_STATE_DELTA=none`、`SWAP_OK`。
- **Phase 3（WorkBuddy CN 插件 `0.21.27 -> 0.21.29`）**：
  - 压缩包 SHA256 校验通过（`5ee2744364214c483955ad91cb74af8c9c1c9f08686e63dcd78ce302b7b2f476`），`workbuddy-provider.so` SHA256：`d6379dc46702f1ad7232ebca33f1ed50efc4aef35a5a5000a0d1140721c279d5`；临时目录 `/root/cpa-wb-02129-stage` 已清理；`workbuddy-ai-provider.so` 维持 `closed_dark`。
  - 换芯回执：`DRAIN_OK`、`START_LOG_CLEAN version=v8.0.80 plugin=workbuddy-provider:0.21.29 intl=closed_dark`、`READINESS_OK`、`MODELS_SURFACE_UNCHANGED`、`LANE_STATE_DELTA=none`、`WB_SWAP_OK`。
- **Phase 4（`cpa-admission` 原子轮换 + 内核 TCP 调优）**：
  - `cpa-admission` 首次尝试因 Python 3.12 后验加载缺少 `sys.modules[spec.name] = m` 触发自动回滚并实证 `ROLLBACK_VERIFIED`；修正检查逻辑后二次原子切换成功：`REQUEST_GUARD_EXCLUSIVE_OK`、`ADMISSION_IDLE_OK`、`ADMISSION_PIN_ROTATED old=fcf95564... new=067c65684aaf6bee5cc993faf64b3c0bc57ac3166afdd66c70d1e7b501a2c9bb`、`ADMISSION_INTEGRITY_OK`、`ADMISSION_CONTRACT_20261121_OK`、`DEPLOY_OK`。
  - 写入 `/etc/sysctl.d/99-cpa-tcp-tuning.conf` 设置 `net.ipv4.tcp_slow_start_after_idle = 0`（配合既有 `bbr` + `fq` 避免长思考空闲期拥塞窗口塌缩），回读确认 `0`。

## 分层验证证据

| 层级 | 执行内容 | 结论与关键回执 |
| --- | --- | --- |
| `repo_verified` | `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1 -RunParallel`（覆盖最终代码提交 `75a29d0`） | `CATALOG_CHECK=PASS`、`bandit` / `bandit-scripts`、`ruff check`、`ruff format --check`（177 files）、`mypy`（164 source files, 0 issues）、`pytest` `1768 passed, 30 skipped, 852 subtests passed in 72.18s` |
| `simulated`（隔离 `unshare --mount --net --fork`，零生产凭据、零默认路由） | 1) `cpa-acceptance-fork.py`<br>2) `cpa-acceptance.py`（`CPA_ACCEPTANCE_DEFER_UPDATE_REHEARSAL=1`）<br>3) 隔离插件 ABI + v8 配置加载（`0.21.27` 与 `0.21.29`）<br>4) 真二进制 RSE/连接池/流式模拟 | 1) `FORK raw_reasoning_events PASS raw_deltas=4 raw_done=2 summary_deltas=4` -> `FORK_PATCH_ACCEPTANCE=PASS`<br>2) overload 1 call -> cooldown 503 0 calls -> 62s 恢复 200 `same_process=true` -> `ACCEPTANCE_RESULT=PASS`<br>3) `ISOLATED_PLUGIN_LOAD_old_0.21.27=PASS`、`ISOLATED_PLUGIN_LOAD_new_0.21.29=PASS`，INTL 未注册<br>4) `SIM_A_KEEPALIVE_REUSE=PASS`、`SIM_B_WAF429_RSE_STOP_ZERO_FANOUT_ZERO_COOLDOWN=PASS`、`SIM_C_TOS503_RSE_STOP_NO_POOL_CASCADE=PASS`、`SIM_D_QUOTA429_FAILOVER_AND_PORT_ISOLATION=PASS`（跨凭据切换端口隔离 `[56782, 56786]`）、`SIM_E_KEEPALIVE_BEFORE_FT=PASS` -> `RSE_AND_STREAM_SIMULATION=PASS` |
| `filesystem_projected` + `host_loaded` | 最终 strict doctor（`scripts/cpa_bwg_guardrails.ps1 -Profile bwg`） | `DOCTOR_CONTRACT_OK`、`image=cli-proxy-api-fork:v8.0.80-gemini-raw`、`runtime_binary_hash=MATCH`、`admission-integrity=OK`、17/17 `projection-drift` `MATCH`、`semantic-policy=OK`、`workbuddy-version=cn:0.21.29 intl:disabled_closed_dark_20261117`、`workbuddy-config=OK`、全部 7 个 Antigravity 凭据数值化 `refresh_age_hours`（`0.0–0.5h`）、`oauth_antigravity_refresh_failures_7d=0`、`credential_ban_signals_7d=0`、`claude_lane=RETIRED no_oauth_route`、`nginx-syntax=OK` |
| `controlled_live`（仅非 OAuth 车道） | 1) `cache-canary`（`glm-5.3-flash`, `chat`）<br>2) 经 `cpa-admission:8318` 单发流式 SSE（`glm-5.3-flash`, `stream: true`） | 1) sample 1 `4114 in / 0 cached (0.0000)` -> sample 2 `4114 in / 4096 cached (0.9956 = 99.56%)`、`HEALTH_OK`<br>2) `SSE_CONTROLLED_LIVE status=200 header_ms=42 total_ms=1800 bytes=10676 has_done=True has_ok=True has_trace_id=True` |

## `aliyun` 只读排查记录（未做任何写入）

- 在排查 `bwg` 周期性后台调用来源时，对 `aliyun` 做只读核查，确认来源为 `qq-codex-bot-provider-watchdog.timer`（`OnCalendar=*:0/5` + `RandomizedDelaySec=90`）触发的 `/opt/qq-codex-bot/scripts/provider-watchdog.sh`。
- 关键现行配置：`provider_background_probes_enabled: true`、`heavy_grounding_runtime_http_model: gpt-6.1-sol`、`heavy_solution_runtime_http_model: gpt-6.1-sol`、`GATEWAY_BACKOFF_BASE_INTERVAL_SECONDS = 1800`、`DEFAULT_FULL_PROBE_INTERVAL_SECONDS = 3600`。
- 本次未对 `aliyun` 做任何修改；如需进一步降低空闲期后台合成探针频次，应在 `qq-astrbot-stack` 仓库单独评审与发布。

## 备份目录与回滚入口

Git 回滚不能替代远端恢复；以下备份均保留在 `bwg`：

1. **守卫投影回滚**：`/root/cpa-guardrails-backup-20261120T171200.779441976Z`
2. **CPA 镜像与 compose 回滚**：`/opt/cliproxyapi/backups/20261120T173221.404737132Z-swap-v8.0.79-to-v8.0.80-gemini-raw`（旧镜像 `cli-proxy-api-fork:v8.0.79-gemini-raw` 本地已保留）
3. **WorkBuddy CN 插件回滚**：`/opt/cliproxyapi/backups/20261120T173343.081184921Z-swap-wb-cn-0.21.27-to-0.21.29`
4. **`cpa-admission` 原子回滚**：`/opt/cliproxyapi/backups/20261120T174536.649566387Z-admission-nagle-risk-20261121`（此前首次演练自动回滚留痕于 `/opt/cliproxyapi/backups/20261120T174352.204947071Z-admission-nagle-risk-20261121`）
5. **内核 TCP 调优回滚**：删除 `/etc/sysctl.d/99-cpa-tcp-tuning.conf` 并执行 `sysctl -w net.ipv4.tcp_slow_start_after_idle=1`

## 未验证边界与后续建议

- 本次受控在线验收仅覆盖非 OAuth 车道（`zhipu-coding-plan` / `glm-5.3-flash`），未向 OpenAI/Codex、Antigravity 或 WorkBuddy OAuth 车道发送额外生成探针；上述 OAuth 账号的自然流量缓存命中、思考吐字体感以及长期封号/风控安全性无法通过单次网关升级彻底消除或一次性证明，仍需在日常自然流量与 strict doctor 周期中持续观察。
- **用户侧操作建议**：
  - 同一会话内尽量避免中途切换模型，以免破坏前缀 KV 缓存命中。
  - **不把代理/IP 分流当作防封措施**。7 个 Antigravity 账号共享同一 BWG 出口是既有拓扑；
    按账号做代理出口隔离、换 IP、轮换设备伪装或预防性换号都属于规避风控，不能降低条款与
    身份归属风险，本仓不实施。若把账号迁回真实设备/合规网络，须先按平台条款、账号归属和
    迁移回滚单独评审，不能把代理池包装成保护。
  - 若需进一步压降空闲时段来自 `aliyun` 的后台合成请求，可在 `qq-astrbot-stack` 仓复核 `provider_background_probes_enabled` 与稳态/全扫探测间隔。

**2026-11-21 本地复审增量（此前远端已验收阶段的后续修复，尚未重新远端投影）**：CPA doctor、`-Apply` 预检及随机路径轮换的
TLS 回环探测已统一使用 CA 与 hostname 校验；临时校验材料位于 mode-700 私有目录，
不再接受 `curl -k`。路径轮换在原子替换配置后即记录 mutation，信号中断会恢复原配置并
复核旧路径。该批改动只证明本地契约与测试，未重新连接 VPS，也未声称已消除封号、
限流或降智风险。本地修复结论仍以最新 full gate 为准；真实远端投影、服务加载与
受控验收是独立层级。

本轮自然流量审计（24h、4620 条）显示长前缀缓存总体健康，主要首字等待来自模型
思考/上游首包，WorkBuddy 存在突发排队；这些是观测，不是账号“未封”或“未降智”
证明。本地 Antigravity 策略已停止改写自然 user 提示词，避免破坏代码、复合标识符
和 byte-stable prefix；System Instruction 的 `sensitive-words-replace` 仍待单独
复核。该策略尚未投影到 bwg，旧远端规则不因本地清单改动而自动消失。旧状态没有
验证替换是否每次成功命中、改写后是否仍能缓存，或是否带来长期质量收益，故本次
停用是前向修正而非宣称原有证据完全无效；清理残留规则仍须独立预演与远端回读。

后续只读复核又定位并修复了以下本地缺陷（仍未重新投影）：doctor/apply/rotate 将
`--cacert` TLS 参数以 NUL 分隔传给 Bash，command substitution 会丢弃 NUL，导致
回环严格探测拿到空参数并报告 `000`，现改为独立行的参数数组；防火墙 `-Apply`
遇到受管 unit 漂移时不再永久拒绝，而是在已有备份后原子替换并复验；updater 的
`INT`/`TERM` 在创建备份前延迟、在 Compose 改后走已验证回滚，并限制 Docker 启动
中断后的停止与旧镜像恢复时间，只接受 CPA 目录内的备份路径。更新器把 pull 600s、
Compose 180s、启停 45s、恢复 30s、Docker kill grace 15s、健康请求 120s 声明为
六条独立单腿的上界（合计 990s）；systemd drop-in 的 `TimeoutStartSec=40min` 是
整体 fail-stop，不是这些可选步骤相加后的实测时间。schedule checker 要求显式值
与 Effective 一致，任一冲突或不可读均拒绝；日常 04:00 更新之后仍有 04:10 系统
维护，不能把 40min 解释为固定的空闲窗口。防火墙 JSON 对受管表中缺 `expr`、空
表达式或非法表达式规则统一 fail-closed。`projected-sources` 会写入本次无凭据的
源快照及 `SHA256SUMS`，并绑定 `PROJECTION_COMMIT`；`auth/` 与 provider API key
不进入该目录。上述修复的本地门禁结果以本轮最终 Full 收口为准；不能据此宣称本轮
已经重新上机。

本地策略已将 `session-affinity-subagents` 从 `false` 改为 `true`：设计目标是在多子任务
共享父会话前缀时继续落到同一凭据，减少同前缀跨账号分叉与缓存局部性丢失。该字段
只对已识别 parent 的子任务生效，不能据此声称并发突发自动被限流。它尚未投影或做
OAuth/子任务自然流量 A/B；生产仍是旧值。局部修复还前移了
`-Apply` 的 EXIT/HUP 处理与私有预检清理，并修正 CDN 订阅路径、主机和地址比较：
`/a` 正常时 `/b`、`/left/right` 或错误发布主机必须判漂移，避免已发布的 dead secret
或错误 WS 发布被误报为健康。两者均为本地 `repo_verified`，不等于远端已加载。

本轮另将 `max-retry-credentials` 目标从 `3` 降为 `1`，即一次请求只使用一份
OAuth 凭据，`request-retry` 继续保持 `0`；以后是否重试由调用方的下一次明确请求
或选定的 API-key route 承担。代价是单个账号的瞬时 429 会更直接地显现。这个取舍
减少同一失败提示词与并发突发扩散到多个消费级账号，但没有证明能“防封”；它既
不是对平台条款的授权，也不能消除限流或模型质量风险。

**最终本地收口（仍未重新投影）**：以当前工作树运行 `scripts/run_gates.ps1 -RunParallel`，
结果为 `1813 passed, 30 skipped, 884 subtests passed in 107.30s`，`ruff check`、`ruff format
--check`（178 files）、`mypy`（165 source files）、`bandit` 与 `CATALOG_CHECK=PASS` 均通过；
`scripts/check_docs.py` 为 `DOCS_CHECK=PASS (42 documents)`。Windows 上新加的 updater
中断恢复行为夹具在 native WSL 中通过，验证停止启动、还原备份 compose 并从原镜像重新
启动后返回 `31`；原失败来自 Git Bash 不能复现 Linux `/proc` 与进程组边界，已移除仅供
夹具使用的生产分支。这些结果只证明本地代码与门禁，不证明 bwg 已加载。

同日最终只读 strict doctor（`scripts/cpa_bwg_guardrails.ps1 -Profile bwg`）结果为
`DOCTOR_CONTRACT_FAILED`：运行镜像为 `v8.0.80-gemini-raw`，投影/运行时二进制约束仍为
17/17 `MATCH`，但生产仍显示旧 `session-affinity-subagents=false`、updater 超时仍为
`20min`、TLS 探测仍报 `000`。这些是本地契约与部署差异信号，不是账号封禁证据。doctor
读数为 ChatGPT OAuth `1`、Antigravity `7`（刷新失败 `0`）、WorkBuddy CN `9`、INTL `7`
（`closed_dark`）、Claude `RETIRED`；当前账号/凭据数以此现场只读结果为准。

近 7 天出现 `106` 次 `model_substitution`，均归属同一脱敏凭据哈希；这是质量复核信号，
不能单独证明上游降智或账号受限。24h 自然流量中 WorkBuddy CN 的
`waited_ms` p50/p90 约 `3.6s/8.3s`，上游响应头约 `2.7s/4.5s`，首文约
`2.4s/4.0s`；ChatGPT `gpt-6.2-sol` 长前缀缓存命中约 `92%`，首文 p50/p90 约
`8.29s/26.36s`；`gpt-6-luna` 缓存约 `76%`，首文约 `3.68s/52.62s`；
Antigravity `neon-xhigh` 缓存约 `92%`、首文约 `8.96s/24.18s`，
`hydrogen-xhigh` 缓存约 `65%`、首文约 `6.86s/14.89s`；Zhipu `glm-5.3-flash`
缓存约 `92%`、首文约 `7.25s/26.61s`。因此缓存总体健康，主要待优化项是
WorkBuddy 突发排队与消费级账号的瞬时并发，而不是继续改 prompt 追缓存。
