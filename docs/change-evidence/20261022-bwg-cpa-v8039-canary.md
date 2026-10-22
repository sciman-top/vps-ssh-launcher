# BWG CPA v8.0.39 canary upgrade (2026-10-22)

**Scope**: `bwg` only. `zz` was not connected or modified. No OAuth credential, IP, UA, concurrency, or retry-policy changes; no OAuth generation probes (`CPA_HEALTH_NO_OAUTH=1` smokes only).

## Release relevance

- Official release `v8.0.39` published 2026-10-22T13:18Z (below the updater's 72h dual-source maturity gate → manual same-day canary, user-authorized).
- chatgpt/codex lane: request-scoped 401 no longer rejects/refreshes tokens (fewer spurious refreshes; watch family #6742); `response.metadata` held during stream bootstrap buffering (`/v1/responses` stream fidelity); responses websocket turn reauthorization + denied status.
- Risk-positive: #6844 preserves runtime cooldowns across auth-file snapshot re-reads — a watcher event can no longer wipe a protective cooldown and burst the upstream.
- antigravity lane: Gemini schema cleanup skipped for the Responses tool reverse identity map (micro-perf).
- Inert here: claude cloak/context-window fixes (lane retired), vertex regional endpoints (unused), Claude web-search translator.
- Go 1.26.10 host-toolchain bump: the plugin boundary is `c-shared` (C ABI), so a host toolchain patch does not break `.so` loading; this joint canary additionally pins the reviewed plugin build and asserts its registration on the new host (the timer path refuses plugin hosts with exit 77, so a reviewed canary is the only sanctioned route).

## Transaction

- Script: `outputs/deploy-cpa-v8039-canary-20261022.sh` (+ `.ps1` runner through the `bwg` profile). First canary executed under the 2026-10-21 drain contract (32c1638): `verify_traffic_guard` + `requests.lock` exclusive `flock` drain placed before `MUTATED=1`; `PRE_HOLDS` baseline (empty) taken after the drain; post-restart hold delta found zero new holds.
- Joint plugin gate: preflight refuses unless `workbuddy.so` == `86441f5d…` (0.9.64-local-trunc, deployed 13:30Z same day); post-restart the script asserts `plugin registered plugin_id=workbuddy … version=0.9.64` in the new container's logs before any acceptance.
- Docker Hub amd64 manifest digest `sha256:12080917f358951ef8b6a6b0d275096123494c66d6a9be922189d0fe92c4ac7c`; rollback image `v8.0.38@sha256:113a7024…` verified local; backup `/opt/cliproxyapi/backups/20261022T135222.402396289Z-from-v8.0.38-canary-v8.0.39`.
- Timeline: preflight 13:52:20Z (`ADMISSION_IDLE_OK`, `PRE_GENERATION OK`) → recreated 13:52:28Z → `PLUGIN_REGISTERED` 13:52:32Z → post generation `HEALTH_OK` → `OK` 13:52:34Z; exit 0; `RestartCount=0`. Requests-lock drain window ≈ 6s.

## Post-write verification

- Container `v8.0.39@sha256:12080917…` running, `StartedAt=2026-10-22T13:52:28Z`; `workbuddy.so` unchanged `86441f5d…`; registration line present in logs.
- healthz: all four lanes `hold_reason=null`, no active cooldowns; workbuddy carrying real traffic (`inflight=1`) minutes after the swap.
- `cache-canary glm-5.3-flash`: sample 1 miss (fresh prefix, expected), sample 2 `hit_ratio=0.9956` (`cache_read_tokens=4096/4114`), `HEALTH_OK` — identical to the pre-upgrade baseline; no cache-policy change introduced.
- GLM stream probe (non-OAuth, via 8318 full client path): `status=200`, `ttfb_s=1.053` (baseline 0.991, same band), `total_s=1.860`, `events=47`, output exact (`cache stream probe ok`). 吐字健康；`/v1/responses` access-log 时长是整段生成耗时，不作为首字判据（runbook 口径）。
- Real traffic post-upgrade (13:52Z → ~14:15Z): `workbuddy-oauth` 13/13 `successful=true` (`deepseek-v4.1-flash`, 200)；zhipu 1/1（本会话探针）。无失败，无 `truncated_stream`。
- Strict doctor: `DOCTOR_CONTRACT_OK`; projection drift 12×MATCH; admission-integrity / lane-hold / oauth-monitor / antigravity-lane / claude-lane sections green. `lane-hold`: chatgpt/workbuddy/antigravity=`OPEN`，claude 墓碑惰性。`model_substitution_warnings_7d=0` 是容器重建即归零的已知重置行为，非行为变化。

## Risk review (OAuth lanes)

- **chatgpt/codex**: 24h `capacity_events=100`（`gpt-6.1-sol:70`）、`lane_rejects=26`（cooldown:17 / downstream_gone:8 / model_cooldown:1，全在本 lane）——上游容量窗透传，admission 阶梯自恢复，零 durable hold。v8.0.39 的请求级 401 不再触发 token 拒绝/刷新，减少刷新churn（风控正向）。未改并发/UA/凭据。
- **antigravity**: streak=0、`strict_backoff=true` 为既定配置；`sensitive-words` 钉定在 config.yaml（本次未触碰，doctor `cpa-policy` 绿）。substitution 命名差异对（`gemini-3.8-flash-n <== gemini-3.8-flash-high`）会随流量重新累积，属已知命名差异非真实降档（2026-10-22 晨判读），由 doctor 观测。
- **workbuddy intl**: 升级后 13/13 成功；流截断守卫（0.9.64-local）逐字节沿用；CN lane 仍未接入（需用户本人浏览器 OAuth，维持不变）。
- **claude**: 退役墓碑保留（`authentication_required`，惰性且承重——admission 加载无 missing-file 守卫，勿删）。

## Rollback

- `cp -a /opt/cliproxyapi/backups/20261022T135222.402396289Z-from-v8.0.38-canary-v8.0.39/compose.yml /opt/cliproxyapi/compose.yml && cd /opt/cliproxyapi && docker compose up -d --pull never`（v8.0.38 镜像 digest-pinned 保留本地）。回滚后复验插件注册 `0.9.64` 与 `readiness=HEALTH_OK`；Git 回滚不能替代远端恢复。

## Watch

- #6742（Codex OAuth ~7 天失效）——v8.0.39 的请求级 401 修复可能相关；后续 oauth-monitor 评审观察 `invalid_grant`/刷新失败是否减少。
- 上游 PR #6784（reasoning-only 流 EOF 缺 `response.completed`）仍未合并；插件侧截断守卫覆盖 workbuddy 三条路径，宿主侧修复合并后随下一 canary 评估。
