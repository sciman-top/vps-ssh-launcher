# BWG CPA v8.0.55 → v8.0.58 宿主换芯（2026-11-07）

## 目标与边界

用户指令升级 CPA 至 [v8.0.58](https://github.com/router-for-me/CLIProxyAPI/releases/tag/v8.0.58)
及插件最新版，防范 OpenAI/Codex、Antigravity/Gemini、WorkBuddy CN/INTL、
Claude/Anthropic 的封号、限流、降智与吐字/缓存回归。本次只变更 bwg；
每日 updater 因插件宿主语义仍按 PLUGIN_CANARY_REQUIRED defer（exit 77），
人工联合 canary 是唯一 sanctioned 路径。没有新增、删除、轮换、隔离或导出
任何 OAuth/API-key 凭据；验收使用隔离 fixture、readiness、合成上游与升级后
自然流量。

## 版本审查（v8.0.55..v8.0.58，32 commits，109 files，+7327/−586）

- 无登录、scope、凭据迁移或 translator 破坏面；不重授权、不做桌面形状冒烟。
- `sdk/pluginabi/types.go` 仅加性 `ErrorCode()` 方法，无 ABI 版本 bump——
  在役插件兼容性仍以隔离加载实测为准（通过，见下）。
- conductor/auth 面：`isRequestScopedError` 将保留的 terminal response 归为
  request-scoped（除非显式 credential-scoped）——terminal 响应不再驱动凭据
  轮换，方向与本仓防封号姿态一致；count_tokens 501/不支持能力改为
  availability-neutral；错误 code 全链路穿透（`errorCodeFromError`）。
- usage manager 新增 `request_scoped` 失败记账码；perf 侧避免 200 OK body
  缓冲（内存/TTFB 正向）；Codex 侧 SIWC bypass 图片直连、websocket 终态 429
  透出、hosted image 拒绝；模型注册表新增 `gpt-6-chat-thinking`。
- 本栈无消费面的 Devin/`X-Tbh-Contributor-Mode`/gpt-image-2.6 增量不参与流量。

## 前窗：6008 事件闭环（前置条件）

换芯前 workbuddy 车道被 `retry_window_review_required` 误判持扣闸死
（6008 计费误分类），按
[6008 事件台账](20261107-bwg-workbuddy-6008-credits-hold.md)完成修复部署与
持扣释放、doctor 回绿后，swap 的 `PRE_HOLD_PRESENT` 门才可通过。本次事务
顺带把该预检与 doctor 的权威语义对齐：磁盘 `unverified_request` +
healthz live hold=none 视为 write-ahead 镜像放行（`PRE_WRITE_AHEAD_MIRROR`），
持续 chatgpt 流量下该镜像本会长期驻留；其余 reason 与任何 live hold 仍硬拒。

## digest 钉定勘误

v8.0.58 上游首次以 OCI 多架构 index 发布：tag 拉取记录的 RepoDigest 是
index digest `sha256:060f352248b413d2edac58d816aa5437972ce764a07250d2b9aef82db549cb6e`，
而非 manifest inspect 首个看到的 linux/amd64 叶子 `sha256:0573506b…`。
隔离加载脚本的 digest 断言按叶子钉定，fail-closed 拦下（`DIGEST_UNEXPECTED`），
改为随 v8.0.55 机制的 tag 拉取 digest 后通过。镜像在预拉阶段即落 bwg 本地。

## T1 隔离加载与合成验收

- 隔离加载（`outputs/cpa-v8058-stage-isolated-load-20261107.ps1`）：无凭据、
  无默认网络 namespace，v8.0.58（commit `e5e6ad1`，BuiltAt
  2026-11-06T13:16:54Z，binary sha `7ab39fbb…`，`ldd` 只依赖 libc）加载
  在役 canonical 0.9.87 `.so`（`7093a190…`）：
  `ISOLATED_PLUGIN_LOAD=PASS`，注册 `version=0.9.87`，护栏项 applied，
  零 panic/fatal——宿主 patch 线 ABI 兼容。
- 合成验收（`outputs/cpa-v8058-acceptance-20261107.ps1`）：八场景全过——
  start_fail/model_exposure 回滚验证、transient exit10 UNVERIFIED、success
  exit0、overload capacity marker、cooldown 零上游调用、recovered 同进程。
  首跑暴露验收 fixture 目录契约滞后：缺 2026-11-06 收编的
  `deepseek-v4.1-flash-oc`（必需面，API-key opencode 车道），已在
  `scripts/remote/cpa-acceptance.py` 的 opencode fixture 条目补声明后
  `ACCEPTANCE_RESULT=PASS`、`FIXTURE_CLEANED=YES`。

## T2 生产事务（host-only swap）

`outputs/cpa-v8058-swap-transaction-20261107.sh`（经
`run-cpa-v8058-swap-20261107.ps1` 投递，config 基线
`ab0e0a66…` 当次新鲜探针）。前两次尝试 fail-closed 退出
（`PRE_HOLD_PRESENT chatgpt-risk.json=unverified_request` ×2，write-ahead
镜像语义对齐前）；第 3 次 2026-11-06T18:39:08Z 捕获干净窗，事务内部零回滚：

```text
PRE_HOLDS_CLEAN / PRE_ASSERTS_OK
BACKUP dir=/opt/cliproxyapi/backups/20261106T183908.465687357Z-from-v8.0.55-to-v8.0.58
DRAIN_OK
DIGEST_READBACK=OK
RESTART_APPLIED started=2026-11-06T18:39:09.605724147Z
PLUGIN_REGISTERED version=0.9.87
CLIProxyAPI Version: v8.0.58, Commit: e5e6ad1
START_LOG_CLEAN（panic|failed to load 零命中）
PLUGIN_AND_CONFIG_UNCHANGED（插件 7093a190、config ab0e0a66 前后一致）
READINESS_OK（HEALTH_OK）
AUTH_BYTES_UNCHANGED
KVSTORE_DB_PRESENT size_bytes=4194304（惰性基础设施增长，无消费方）
LANE_HOLD_DELTA=none
restarts=0
SWAP_OK v8.0.55->v8.0.58
```

## 升级后验收

- strict doctor：`DOCTOR_CONTRACT_OK`；三 OAuth 车道 `OPEN hold_reason=none`、
  `workbuddy-version=0.9.87`、`MODEL_IDS_UNKNOWN=none`、13 项投影 MATCH。
- `proxy_hardening_drift.ps1 -Profile bwg`：`HARDENING_DRIFT=NONE`——
  既有 host-security 项（内核 CVE-2026-98432）已随月维口袋修复消失
  （`host_security=ok`），首次全绿。
- 受控单发：hy4free 流式 PONG 200、`TERMINAL=true`、
  `PRODUCTION_HY4FREE_ACCEPTANCE=PASS`；补齐换芯窗无自然流量的两条车道
  单发（`outputs/run-lane-singles-v8058-20261107.ps1`）：
  `antigravity-oauth/gemini-3.8-flash-high` 200/terminal/PONG（4.6s）、
  `zhipu-coding-plan/glm-5.3-flash`（更新器代表非 OAuth 路由）
  200/terminal/PONG（1.9s）——四条 OAuth 车道与非 OAuth 代表路由在
  v8.0.58 上全部有服务实证（`LANE_SINGLES_V8058_ACCEPTANCE=PASS`）。
- 换芯后 ~31 分钟自然流量（`cpa_natural_traffic_audit.py --hours 0.5`，
  零注入探针，208 records，0 warnings）：
  - `chatgpt-oauth/gpt-6-luna` 71/71 completed，cache_hit_ratio 0.987
    （0.99 基线带内），first_text p50=4.58s（上游 TTFB 主导），
    sse_bytes/s p50≈48.9KB/s，output_tokens/s p50≈139.8/s；
  - `workbuddy-oauth` 释放后凌晨自然流量稀薄：deepseek-v4.1-flash 1/1
    completed，hy4free 即受控 PONG；无 capacity 断连、无客户端断连；
  - `passthrough/deepseek-v4.1-flash-oc` 91/91 completed，slow_5s 6 例全部
    归因 upstream，queue/decode=0。
- 换芯后容器日志 121 行：0 panic、0 error；8 条 warn 全部为
  "model registration … superseded during computation"（v8.0.5x 新增的
  并发重算竞态守卫，丢弃陈旧发布保留最新 auth state；仅见于重启发现窗，
  稳态无复发，留观）。

## 回滚

同维护锁与 drain 内，从备份目录恢复 compose 镜像行（
`v8.0.55@sha256:afe1135e…` 本地镜像仍在）并
`docker compose up -d --pull never`，readiness 与 strict doctor 复验。
插件与 config 未动无需恢复；Git 回滚不能替代远端恢复；OAuth token 运行期
自然刷新，不回放凭据快照。

## 证据边界

已达 `repo_verified`、`filesystem_projected`、`host_loaded`、
`controlled_simulation`（ACCEPTANCE_RESULT=PASS）与换芯后短窗自然
`live_observed`。不声称长期封号风险为零、模型未降智、provider 额度充足或
流式质量长窗已建立；"superseded" warn 家族与 hy4free 的 6004 行为留观。
后续每日 updater 现役=最高成熟候选（v8.0.58 在役后回到无候选 exit 0 路径）。
