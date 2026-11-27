# BWG ZCode 起步套餐车道：额度耗尽识别与边车自动领取关闭（2026-11-27）

用户指令：起步套餐车道保留，可修复与优化；授权按建议连续执行。

## 背景

第二轮全面深审（`d1e1636`）只读发现两处，未执行：

1. `zcode-proxy` 边车配置没有 `claim` 段，按 v4.9.1 默认值 `claim.auto: true` 运行，
   每 5 分钟用 OAuth JWT 轮询 `zcode.z.ai` 活动页。本部署用的是 BigModel 起步套餐，
   该轮询没有收益。
2. 该车道此前从未做过线上验收，48h 内唯一一发是 502，原因未定位。

## 取证

边车源码按 tag `v4.9.1` 只读取得（`src/config/loader.ts` `resolveClaimConfig`、
`src/claim/runtime.ts`、`src/proxy/body-transformer.ts`、`src/proxy/system-prompt.ts`、
`src/proxy/handler.ts`）。

- `claim.enabled` 与 `claim.auto` 的默认值均为 `true`；轮询请求携带存储的 OAuth JWT 与
  设备标识，命中活动会自动领取并解验证码。
- 边车在**出站时**才前置官方三块系统提示（`applyStartPlanOpenAISystem`），块 2 是
  字节精确的官方稳定文本。admission 在边车之前改写的是客户端自带的 system 文本，
  不触及官方块。上一轮记录的"方向性冲突"据此撤回。
- 边车对**非 2xx** 的上游错误有 `upstreamErrorPassthrough`，但对 HTTP 200 包业务错误
  没有处理，统一落到 `translatedBatchResponse` 的 `502 translation_failed`，把上游 JSON
  引在自己的 message 里。

## 受控实战

经 admission（8318）→ CPA（8317）→ 边车 → 上游，单发、不重试。探针
`Reply with the single word PONG.`，`max_output_tokens=64`。

| 时间（UTC） | 路径 | 结果 |
| --- | --- | --- |
| 13:48:35 | 经 admission，流式 | 200 + `response.failed`，0.59s，客户端只见 `Upstream status=502` |
| 13:51:51 | 同上，边车临时 `serve debug` | 同上；debug 行显示上游 `← 200 OK`（`application/json`） |
| 13:54:34 | 直连边车 18082，非流式 | 502 `translation_failed`，message 引出上游原文 |

上游原文：

```text
{"code":1005,"msg":"exceed quota limit","logid":"…"}
```

**结论**：502 = 起步套餐额度已用完。不是风控（那会是 405/3012），不是边车故障，也不是
免费窗口的问题。48h 前那一发 502 同形，与本次改动无关。

取证途中的一次误判（如实记录）：手工跑 `zcode-proxy auth status` 时只带了 `HOME`、没带
`ZCODE_PROXY_CREDENTIAL_SECRET`，报 "Ignoring corrupted or stale credentials / Not logged
in"，一度被当成重启弄坏了凭据。带上完整环境后复测 `Logged in: bigmodel`；凭据文件与
种子文件的 mtime 均为 2026-11-21，未被触碰。另：一次 `serve --help` 在 SSH 会话的 cwd
（`/root`）生成了名为 `--help` 的模板文件（仅占位密钥），已删除。

## 仓库改动

- `scripts/remote/cpa-admission.json`：`zcode-start-plan` 车道 `capacity_markers` 追加
  `"exceed quota limit"`。`capacity_scope` 对错误文本做子串匹配且不看状态码，所以边车的
  502 与流式路径上的同一响应体都命中。`cpa-admission.py` 字节未变，**admission pin 不轮换**。
- `tests/test_cpa_admission.py`：`test_start_plan_quota_refusal_is_a_lane_capacity_signal`
  固化边车 502 形状、429 同文本、非额度的翻译失败不冷却、兄弟 GLM 车道词表不变。负向
  对照：去掉词表项后该测试失败。
- `docs/runbooks/cpa-ban-throttle-incident-response.md`：撤回"方向性冲突"，记录 502 真因、
  排查手筋与 `auth status` 的环境要求。
- `docs/runbooks/cpa-observed-baselines-and-upstream-log.md`：七续。

## 远端写入一：边车 `claim.auto: false`

单元 `zcode-start-plan.service` 不受本仓投影管理，按"预演 → 备份 → 保 inode 写 → 重启 →
运行态验证"执行。预检为本地 YAML 解析（不触网；`zcode-proxy claim list` 本身就会把 JWT
发往活动页）。

- 变更前：`config.yaml` 600/root/271 字节，无 `claim` 段。
- 写入：同 inode，末尾追加注释与 `claim:\n  auto: false`；其余键经解析往返比对相等。
- 备份：`/root/zcode-start-plan-config.before-claim-off-<ts>.yaml`（0600，sha8 `f326ec45`）。
- 验证：重启后 active；旧进程关停日志为
  `shutdown: cleared captcha pool + highspeed timers`（此前是 `cleared auto-claim + captcha
  pool + highspeed timers`，`auto-claim` 已消失）；重启后 `[claim]` 行 0；strict doctor
  `zcode-sidecar-service=enabled-active`、`listener=OK`、`public-socket=ABSENT`。
- 回滚：`cp <备份> /opt/zcode-start-plan/config.yaml && systemctl restart zcode-start-plan.service`。

取证期间临时把单元改为 `serve debug`（先备份单元文件），发一发探针后已还原为 `serve`
并确认 `debug: ON` 行为 0。

## 远端写入二：admission 词表投影

`pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -Apply`，结果见下文
「投影结果」。

## 投影结果

`-Apply` 于 2026-11-27 14:06:55Z：`GUARDRAILS_APPLIED`，17 项 `PROJECTION_HASH_VERIFIED`
（含 `/opt/cliproxyapi/cpa-admission.json`），`HEALTH_OK`、`READY_STATUS=200`，备份
`/root/cpa-guardrails-backup-20261127T140655.904827405Z`。投影前主机无在途事务：最后一个
admission 备份目录是 12:02Z，五条车道 inflight/pending 均为 0。随后 strict doctor
`DOCTOR_CONTRACT_OK`，17 项 MATCH，`admission-integrity=OK`，`credential_ban_signals_7d=0`，
`zcode-sidecar-service=enabled-active`。

## 受控实战（修复后）

14:08Z，经 admission 单发、不重试，两发：

| 发 | admission journal | 边车请求表 | 含义 |
| --- | --- | --- | --- |
| 1 | `status=502 capacity=true retry_after=absent protocol_result=capacity`（6.9s） | 第 1 行 502 | 同一个上游拒绝，修复前是 `capacity=false / upstream_error` |
| 2 | `status=503 capacity=true retry_after=present protocol_result=capacity`（12ms） | **无新行** | CPA 核心已对该凭据冷却，admission 把它如实标为容量；零上游接触 |

说明：admission 自身的熔断阈值是连续 2 次失败，所以探针 1 之后 `cooldown_active=False`、
`failure_streak=1`；挡住探针 2 的是 CPA 核心对 502 的凭据冷却。两级保护现在都把这次
拒绝当作容量处理。额度何时重置没有官方数字，重置前该车道仍会返回配额拒绝。

`live_accepted`：额度耗尽这一状态下的分类与零重试均已实测；额度恢复后的成功路径要等
额度重置后的自然流量，本次未验证。

## 边界

- 未改冷却阶梯、并发与 RPM 上限、清洗规则、边车二进制与版本。
- 3012（套餐被暂停 / 临时风控）落持久锁**未做**：需把该车道纳入 `DURABLE_OAUTH_LANES`，
  牵涉 strict backoff、风险状态文件、doctor 的 lane-hold 门禁与 policy 校验。
- 起步套餐额度何时重置没有官方数字；重置前该车道必然返回配额拒绝。
- 未触碰 `zz`。
