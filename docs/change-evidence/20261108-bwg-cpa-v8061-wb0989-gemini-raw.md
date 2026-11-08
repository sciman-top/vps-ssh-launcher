# 2026-11-08 BWG CPA：v8.0.59 → v8.0.61 + WorkBuddy 0.9.89 + fork 二进制重建

## 结论

- 现役：CPA 镜像 `v8.0.61@sha256:fc714b43…`，运行时二进制
  `v8.0.61, Commit 681ce41b+gemini-raw`（SHA `31f2f9d9…`），WorkBuddy 插件
  `0.9.89`（`cpa-multi-plugins v0.8.97`，`.so` SHA `f2c678ff…`）。
- 三件工件在同一次 drain 事务、同一次重启内一起换装：镜像 tag、bind-mount 的
  fork 二进制、插件 `.so`。`config.yaml`、全部凭据字节未变，无新增 lane hold。
- 用户本次明确要求“直接更新到最新版”，故按 [cpa-gateway.md](../runbooks/cpa-gateway.md)
  “更新器策略”的既有例外（用户当次授权的人工联合 canary）升级，**不绕过**任何
  drain / 备份 / 回滚契约，也不改 updater 的 72h soak 规则本身。

## 为什么这不是普通的镜像换 tag

现役“v8.0.59”其实是 **本地 fork 二进制**：`5d495e1` =
官方 v8.0.59 + Gemini 未签名 thought 的原始 reasoning 补丁
（[gemini-thought-streaming 台账](20261108-bwg-cpa-gemini-thought-streaming.md)）。
compose 用 `./CLIProxyAPI-patched:/CLIProxyAPI/CLIProxyAPI:ro` 覆盖镜像内二进制，
**只换镜像 tag 不会改变运行代码** —— 旧事务模板正是在这里失配。

上游 v8.0.59→v8.0.61 的 `internal/translator/gemini/openai/responses/` **零改动**
（两版本该文件 blob 相同 `22c32f67`），因此 fork 补丁在 v8.0.61 上仍然必要且可
逐字复用。`git merge-tree fork/gemini-raw v8.0.61` 无冲突。

## 上游增量（24 commits，v8.0.59..v8.0.61）

- 连接/流：`ce38c75c` message_stop 后 drain 并复用上游连接、`91e0dee8` 图片流
  SSE keep-alive 不再插进事件中间、`69637a66` 纯空白 reasoning 不开 thinking block。
- 降智/质量映射：`e6931d51` Claude `output_config.effort` 正确映射。
- OpenAI/Codex（封号/限流相关）：整族 SIWC 修复——`5f38d557` SIWC 恢复期仍保留
  原生 Codex 可用、`8c6d8b18` 维护唤醒期间推迟续期、`3891ebaa` 复用已签发
  SIWC 客户端、`80ee739f` 保留 SIWC 轮换并遵守续期窗口、`09b3a579` 强化
  deferral 与 `expires_in` 解析、`baec297f` 分区失败与唤醒取消并存；
  `f4d3f061`/`05e668cf` 未答工具调用/孤儿 tool output 的 transcript 处理。
- 配置面：`40c16182` 新增 `routing.session-affinity-rebind-on: never`（**可选**，
  本栈维持 `quota`）；`f69db99d` 插件商店 release 缓存刷新（opt-in，未启用）；
  `00748974` 只给 `UsageDetail` 加可选字段（ABI 兼容，插件 0.9.88/0.9.89 均未使用）。
- 无 cache_control / prompt-cache 改动：升级不改变命中率。

## 插件 0.9.89（issue #80，本仓 11-07 审计提出）

HTTP 500 + 业务码 `11134`（"the model provider is temporarily unavailable … switch
to another model"）此前无人记账：不进 6004 registry、不是额度错误、也不匹配 #35 冷却
表，于是 priority reclaim 每次都再选中同一对死组合。0.9.89 把它按**模型级**故障记入
#35 表（保守 60 s unknown 窗口，不是 300 s 限频档），并在调度、catalog、executor
快失败与面板统一以 `model_provider_fault` 呈现。**不改**账号选择、会话粘性、
per-credential 并发或 `max-retry-credentials`。

- 46h/168h 容器日志中 `11134` 计数为 0：本次是**防御性收编**，不是故障修复。

## 事务与回执

1. 隔离加载（无凭据、无默认网络 ns）：`ISOLATED_V8061_WB0989_LOAD=PASS`，
   `v8.0.61` 打印版本、插件注册 `0.9.89`、两项护栏 applied、零 panic。
2. 生产 config 解析验证：把**真实** `config.yaml` 与 0.9.89 放进同一隔离 ns 启动，
   进程存活、版本与插件注册正确、无 unknown field / 解析错误。
3. fork 二进制重建（`outputs/build-cpa-v8061-gemini-raw-20261108.ps1`）：
   - 源码来自 `archive/681ce41b…tar.gz`；`go.mod`、translator 原文 SHA 逐个断言；
   - `git apply --check` + 应用后补丁文件 SHA 断言（`9d73d57f…`）；
   - `go vet` + `go test ./internal/translator/gemini/openai/responses/` 通过；
   - 构建 flags 与上游 Dockerfile 一致（`CGO_ENABLED=1 -trimpath -ldflags "-s -w
     -X main.Version=…"`），golang:1.26.10-bookworm（上游 go.mod 要求 1.26.10）；
   - 产物 `31f2f9d96ac8240356bb75984114d04d67de7d9f2c65a9aa061b207c724f0be5`。
4. 收敛事务（`outputs/run-cpa-v8061-converge-20261108.ps1`）：
   - `ENTRY_STATE compose=new plugin=new binary=old`（上一次失败留下的半迁移态，本次
     按收敛契约接管）；
   - 备份 `/opt/cliproxyapi/backups/20261108T161841.256612745Z-converge-v8.0.61-wb0989-gemini-raw`
     （compose/config/plugin/binary + 凭据哈希）；
   - `PRE_HOLDS_CLEAN` → `DRAIN_OK` → `DIGEST_READBACK=OK` →
     `COMPOSE_RECREATE_NOT_NEEDED explicit_restart=1` → `PLUGIN_REGISTERED version=0.9.89`
     → 版本行 `CLIProxyAPI Version: v8.0.61, Commit: 681ce41b+gemini-raw` →
     `RUNTIME_BINARY sha=31f2f9d9…`（宿主与容器一致）→ `READINESS_OK` →
     `AUTH_BYTES_UNCHANGED` → `LANE_HOLD_DELTA=none` → `restarts=0` → `SWAP_OK`。

## 本轮修掉的仓库缺陷（均有复现证据）

| # | 缺陷 | 证据 | 修复 |
|---|---|---|---|
| 1 | 旧模板只 `trap ... ERR`，脚本内 `exit 10` 不触发回滚 → 留下半迁移态（compose 已新、二进制仍旧） | 2026-11-08 三次尝试后的实际状态 | 新增 `trap restore_all EXIT` + `SUCCESS`/`ROLLED_BACK` 守卫 |
| 2 | 就绪断言只采样一次，容器重启 0.4 s 后即查询 → `ConnectionResetError`，健康换装被回滚 | 14:45:14、14:46:16 两次 `LOCAL_CONTRACT_ERROR class=ConnectionResetError` | `wait_readiness()` 120 s 有界轮询 |
| 3 | `set -o pipefail` 下 `docker logs` 接 `grep -m1` 因 SIGPIPE 返回 141，触发回滚 | 14:46 尝试 | 先捕获日志再 grep，并输出尾行诊断 |
| 4 | 只改 compose 镜像时 `docker compose up -d` 判定无变化，bind-mount 新文件不生效 | 15:02 `RESTART_NOT_APPLIED` | StartedAt 未变时显式 `docker compose restart`（仍在 drain 锁内） |
| 5 | 模板只认单一“旧/新”状态，崩溃后无法自我收敛 | 16:18 接管半迁移态 | `ENTRY_STATE` 三态校验 + `ALREADY_CONVERGED` 短路 |
| 6 | doctor 版本门禁钉 `0.9.88` 与现役不一致 → `DOCTOR_CONTRACT_FAILED` | 首次换装后 doctor | `scripts/remote/cpa-guardrail-workbuddy-version-check.sh` 改钉 `0.9.89`，测试同步 |

## 验收

- strict doctor（换装后两次）：`DOCTOR_CONTRACT_OK`；`workbuddy-version=0.9.89`；
  14 项投影 MATCH；`policy-probe=OK`；`credential_ban_signals_7d=0`；
  `model_substitution=KNOWN_NAME_VARIANT`（仅已知 `gemini-3.8-flash-high →
  -n` 别名对，coverage 说明明确这**不是**降智证据）；TLS 余 63 天；
  `oauth_antigravity_monitor=WARN_MULTIPLE_ACTIVE_FILES` 为既有告警。
- 聚焦单发（`outputs/run-lane-singles-v8061-20261108.ps1`，每探针一次、不重试）：
  - `PROBE workbuddy-deepseek-v4.1-flash http=200 events=4 terminal=true text='PONG'`
    —— 插件数据面经新宿主仍然完整；
  - `PROBE gemini-3.8-flash-high http=200 completed=1 reasoning_text_delta=2
    reasoning_summary_delta=2` —— 重建后的 fork 二进制保留 raw reasoning 补丁。
    （首次探针用极短提示词时未产生推理，属预期空推理流，不是补丁丢失。）
- 遥测：`deepseek-v4.1-flash` 1h 窗口 `cache_hit_ratio=0.9985`；`/healthz`
  四条 lane 全 OPEN、无冷却；updater `--check` 报 `CANDIDATE current=v8.0.61
  target=v8.0.61`（现役即最高成熟候选，后续每日 timer 走 no-op）。
- 换装后 2h：admission 355 条 `upstream_result`，`lane_reject` 0；
  `waited_ms` p50=0 / p90=2 / max=1607；容器日志 0 条 401/4xx/5xx request
  line；admission RSS 53 MB（uptime 576 s）。workbuddy-oauth 90×200、
  antigravity-oauth 18×200、chatgpt-oauth 6×200。
- 已知非缺陷噪声：`workbuddy: 右键补给 black_cat 失败 … http 403: growth system
  is only available for personal users` 是 CN 账号的计划资格拒绝，与本次换装无关；
  `plugin registered/unloaded` 成对出现在 23:03、00:18 两次历史重启窗口，
  00:19 那次即本次换装。

## 回滚

- 同一维护锁 + drain 内：`cp -a <备份>/compose.yml <备份>/CLIProxyAPI-patched
  <备份>/workbuddy.so` 回原位 → `cd /opt/cliproxyapi && docker compose up -d --pull never`
  （compose 回退会触发 recreate）→ `python3 cpa-health.py readiness`。
- 备份：`/opt/cliproxyapi/backups/20261108T161841.256612745Z-converge-v8.0.61-wb0989-gemini-raw`。
- 二进制单独回滚可用旧件 `backups/20261108T150211…-converge-*/CLIProxyAPI-patched`
  （`330b7a55…`，fork v8.0.59）。
- Git 回滚不能替代远端恢复；远端回滚也不回退本次已入库的模板与门禁修正。

## 边界与遗留

- 消费级 OAuth 条款风险不变；本次未新增/轮换账号、未清冷却、未提并发。
- `session-affinity-rebind-on: never` 未启用：它把“瞬态 5xx 也不跨凭据”变成硬约束，
  单账号 lane 下会把可用性风险放大，须独立评审。
- updater 的 72h 成熟期规则未改：本次是用户当次授权的人工 canary。
- ufw/Docker 等防火墙面未变；`zz` 未触碰。
- 下一代换装须知：只要 fork 二进制的补丁仍未进入上游，**每次**换镜像都要同时重建
  二进制；若某次只想换镜像，必须先移除 compose 的 `CLIProxyAPI-patched` 挂载并在
  台账里写明补丁退役原因。
