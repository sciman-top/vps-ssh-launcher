<!-- 2026-11-07 草稿（本仓元信息，GitHub 渲染不可见）
目标仓: github.com/mmqz/cpa-multi-plugins (workbuddy 插件)
证据源: bwg 生产容器日志 2026-11-07（宿主 CLIProxyAPI v8.0.58 + workbuddy 0.9.87 官方构建
       = v0.8.94 + #76 修复；config: scheduler_mode=credits, scheduler_priority_reclaim=true,
       request-retry=0, max-retry-credentials=1）
源码锚: floating main（cooldown.go / policy.go / scheduler.go / chat_error.go）
脱敏: 凭据以截断 UID 指称，无 token/密钥；requestId 为上游不透明 UUID
复核: 2026-11-07 晚在生产升至 v8.0.59 + workbuddy 0.9.88（v0.8.96）后重核——
      v0.8.95..v0.8.96 只并入 #77 观察面（冷却清单/清除/面板），
      recordUpstreamFailure 五条路由与 isSoftRateLimit 零改动，本 issue 结论在
      0.9.88 上原样成立
已提交: 2026-11-07 用户授权代理提交 →
      https://github.com/mmqz/cpa-multi-plugins/issues/80
      （正文=本稿剥离本元信息注释+文末追加 0.9.88 复核行）
-->

# Model-level provider faults (HTTP 500, code 11134 family) never enter any cooldown table — priority reclaim re-serves the same dead pair

## Impact

`scheduler_mode: credits` + `scheduler_priority_reclaim: true` serve the first live candidate in host file order on every pick. An upstream **model-level fault** — HTTP 500 with `{"code":11134,"msg":"the model provider is temporarily unavailable, please retry later or switch to another model","extError":{"code":"internal_error"|"service_unavailable"}}` — is recorded by **nothing**:

- not the 6004 registry (`model_ratelimit.go` owns 429+6004 only),
- not the account-level path (it is not a hard credit error),
- not the #35 cooldown table: `recordUpstreamFailure` routes 429/soft-rate-limit wording to 300 s, daily-quota wording to UTC+8 midnight, empty streams / in-stream frames to 60 s — a plain HTTP 500 fault matches none of them (`isSoftRateLimit` only accepts status 429 or the wording "rate limit"/"too many requests"/"throttl").

So the failed `(credential, model)` pair stays "live" and priority reclaim re-selects it on every subsequent request. With `request-retry: 0` / `max-retry-credentials: 1` the client just sees repeated failures, healthy later candidates never rotate in, and a realm-wide provider wobble pins the whole pool head on one credential until the upstream heals or the credential drains. The upstream's own message advises "switch to another model" — the same model-scoped semantics that gave 6004 its dedicated registry (issues #35/#76).

## Production observation (2026-11-07, times UTC)

| time | model | credential | failure |
| --- | --- | --- | --- |
| 15:06:55 | deepseek-v4.1-flash-sg | …workbuddy-intl-1610d8fd… | upstream 500 code 11134 (internal_error), 47.0 s |
| 15:21:41 | deepseek-v4.1-flash | …workbuddy-intl-ca64800b… | upstream 500 code 11134 (internal_error), 31.7 s |
| 15:26:59 | deepseek-v4.1-flash | …workbuddy-intl-ca64800b… | upstream 500 code 11134 (service_unavailable), 21.2 s |

The second and third rows are the same `(credential, model)` pair re-selected **5m18s apart** while the fault persisted — a 60 s unknown-window entry would have rotated the third request to the next candidate. The 15:13:50 flash 6004 on another credential shows the 6004 registry working as intended in the same window (the pair was skipped at pick).

## Source path (floating main)

- `cooldown.go` — `recordUpstreamFailure`: five routes (6004→early-return to its own registry; hard credit→early-return account-level; `status==429 || isSoftRateLimit`→300 s; daily-quota wording→midnight; empty stream / in-stream frame→60 s). An HTTP 500 fault body falls through all of them.
- `policy.go` — `isSoftRateLimit`: `status == 429` or body contains `rate limit` / `too many requests` / `throttl`. `"temporarily unavailable, please retry later"` matches none.
- `scheduler.go` — candidate filter skips `modelIsCooling` / `modelRateLimitActive` / `accountRestrictionCooling` / `guardSkipCredential`; nothing covers 11134.
- `chat_error.go` — 11102 ("model [x] service info not found") already gets a dedicated translation; 11134 is the same model-level fault family and currently has no owner at all.

## Suggested direction

Classify the model-level fault family (11134 "the model provider is temporarily unavailable"; consider the 11102 adjacency) into the #35 table with the conservative `modelCooldownUnknown` 60 s window, so the pick rotates and self-heals by probe. Deliberately **not** the 300 s rate-limit tier: a fault is not a throttle, no `Retry-After`/reset is declared. If an envelope variant ever carries a declared reset instant, prefer parsing it over the fixed window.
