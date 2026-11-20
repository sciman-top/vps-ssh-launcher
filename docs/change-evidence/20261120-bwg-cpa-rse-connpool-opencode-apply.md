# BWG CPA 多 OAuth 风控、连接池隔离与 OpenCode CF1010 加固验收（2026-11-20）

**目标**：面向 `bwg` VPS 上运行 `cli-proxy-api-fork:v8.0.79-gemini-raw` 的多 OAuth 账号（Codex/ChatGPT、Antigravity/Gemini、WorkBuddy CN/INTL）与多 API key 渠道，结合上游 `CLIProxyAPI` 源码审查与隔离模拟、受控实战验收，补齐请求级 429/403 止损、Antigravity 每凭据隔离连接池与 OpenCode Cloudflare 1010 防护，并完成远端投影与全量验证。全程不触碰 `zz`，保留全部既有 OAuth 账号与 API key 渠道，WorkBuddy RPM 维持 `18`。

## 改动范围

1. **前置并存路由（提交 `24ac9b0`）**：
   - 在 `opencode` 槽位开放 `glm-5.3-oc` 与 `glm-5.3-flash-oc`。
2. **OAuth 请求级 429 止损与 Antigravity 每凭据连接池隔离（提交 `d33029f`）**：
   - 在 `scripts/remote/cpa_policy.py` 与 `scripts/remote/cpa-guardrail-apply.sh` 中钉定 `oauth.request-scoped-errors`：
     - `antigravity`：`status: 429`，正则 `(?s)"status"\s*:\s*"RESOURCE_EXHAUSTED"\s*\}\s*\}\s*$`，`action: stop`（拦截缺少 `details` 块的裸 WAF/风控 429，防止换号扇出并避免误冷却首号；真配额 429 仍正常换号）。
     - `codex`：`status: 429`，正则 `^\s*\{\s*"detail"\s*:\s*"Rate limit exceeded"\s*\}\s*$`，`action: stop`（拦截裸 WAF `detail` 429，防止误冷却）。
   - 钉定 `oauth.providers.antigravity.connection-pool`：`enabled: true`、`idle-conn-timeout: "30s"`、`max-idle-conns-per-host: 2`（源码确认按 `auth.ID` 分桶隔离 HTTP/TLS 连接池，兼顾同凭据复用提速与跨凭据零 TCP/TLS 串号）。
   - 同步更新 `docs/runbooks/cpa-model-account-multi-channel.md` 及对应单元测试。
3. **受控实战发现的 OpenCode Cloudflare 1010 根因修复（提交 `04fd115`）**：
   - 首轮受控单发发现：用裸 `Python-urllib` UA 请求 `opencode` 槽位时触发上游 Cloudflare `403 error code: 1010`，且现场 `opencode.request-scoped-errors` 为 `null`（2026-11-14 槽位重建后仅靠手工遗留的规则已静默丢失），导致单次 1010 将单凭据 `opencode` 槽位整槽打入 503 `auth_unavailable` 冷却。
   - 在 `scripts/remote/cpa_provider_routes.json` slot 2 真源中同时钉定：
     - `headers.user-agent: "cpa-bwg-gateway/1.0"`（从源头阻止裸脚本 UA 透传触发 Cloudflare 1010）；
     - `request-scoped-errors: [{"status": 403, "match": ["1010"], "action": "stop"}]`（纵深兜底防误冷却）。
   - 在 `scripts/remote/cpa-guardrail-apply.sh` 与 `scripts/remote/cpa_policy.py` 中接入该字段投影、严格子集 header 升级保护与规则校验，补齐单测并更新 `docs/runbooks/cpa-gateway.md`。

## 分层验证与证据

- **`repo_verified`**（提交 `d33029f`、`04fd115`）：
  - `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1 -RunParallel` 全部通过（`GATES_EXIT=0`）：`CATALOG_CHECK=PASS`、`bandit`、`ruff check`、`ruff format --check`（177 files）、`mypy`（164 source files）、并行 `pytest` 1794 tests PASS（4 workers, 64.1s，tree hash `68f03f73d0156112968492985d5461cf1454a13ea55aaf0af276f00266dd2635`）。
  - `git diff --check` 退出 0，`scripts/check_docs.py` 返回 `DOCS_CHECK=PASS (42 documents)`。
- **`filesystem_projected` + `host_loaded`**（`bwg`）：
  - 第一阶段 `-Apply`（提交 `d33029f`）：`BACKUP_DIR=/root/cpa-guardrails-backup-20261120T141605.484329425Z`、`GUARDRAILS_APPLIED`、`HEALTH_OK`、`READY_STATUS=200`、17 项 `PROJECTION_HASH_VERIFIED`、`models=18`、`config.yaml` SHA256 `482b15c721b7657a09ce9a2c1c9a880117b50116f0038ccbc6db504e23e3c506`。
  - 最终阶段 `-Apply`（提交 `04fd115`）：`BACKUP_DIR=/root/cpa-guardrails-backup-20261120T143755.279333546Z`、`GUARDRAILS_APPLIED`、`APPLY_EXIT=0`、`HEALTH_OK`、`READY_STATUS=200`、17 项 `PROJECTION_HASH_VERIFIED`、`models=18`。
  - 最终落盘 SHA256：
    - `/opt/cliproxyapi/config.yaml`: `d13173e9961d502dc539605557724fae628e04937fa559f46bb23c4f89c064db`
    - `/opt/cliproxyapi/cpa_policy.py`: `a9dc7fae44bfc2dec6797464c05f70daad0ec0b42b3461ec24e052a97c915a3d`
    - `/opt/cliproxyapi/cpa_provider_routes.json`: `7565c623c55ebe0b9df27aefbe80307271c1a00021060597fdeacb99e8320f05`
  - strict doctor（`pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/cpa_bwg_guardrails.ps1 -Profile bwg`）：`DOCTOR_CONTRACT_OK`、`DOCTOR_EXIT=0`、`POLICY_OK`、`semantic-policy=OK`、`admission-health=OK`、`admission-integrity=OK`、17 项 `projection-drift` 全部 `MATCH`、`credential_ban_signals_7d=0`、`cds_files=0`、`cooldown_state=none`、`nginx-syntax=OK`。
  - 生产配置与容器日志读回：`/opt/cliproxyapi/config.yaml` 中 `oauth.providers.antigravity.connection-pool`、`oauth.request-scoped-errors`、`opencode.headers`（`user-agent: cpa-bwg-gateway/1.0`, `x-opencode-session: cpa-bwg-gateway-01`）与 `opencode.request-scoped-errors`（`403`/`1010`/`stop`）全部一致；容器 `cli-proxy-api`（`cli-proxy-api-fork:v8.0.79-gemini-raw`）近 10 分钟 157 行启动及运行日志零配置/正则错误（`ERROR_OR_PARSE_COUNT=0`）。
- **隔离命名空间模拟验收（零生产凭据、`unshare --mount --net --fork`）**：
  - **Suite 2A（官方 `scripts/remote/cpa-acceptance.py`）**：使用在役二进制（SHA256 `f60ea67e04c3a6bd6785302926e2a2cc7022636c5b6ec0c0843b6c815617bc24`）执行 `overload -> cooldown -> 62s recovered -> actual_health HEALTH_OK`，`UPDATE_REHEARSAL=DEFERRED_BY_DESIGN`（fork 镜像），`ACCEPTANCE_RESULT=PASS`、`CLEANUP_OK`。
  - **Suite 2B（真实二进制 `oauth.request-scoped-errors` + `antigravity.connection-pool` 行为验证）**：双合成 Antigravity 凭据 + keep-alive mock 上游实测：
    - `SIM_CHECK1_POOL_REUSE_SAME_CRED=PASS`（同凭据复用到同一 TCP 源端口 `35860`）；
    - `SIM_CHECK2A_WAF_BARE_429`（裸 WAF 429 首号立即 `stop`，`upstream_calls=1`，零扇出至次号）；
    - `SIM_CHECK2B_AFTER_WAF_NO_COOLDOWN`（首号未误冷却，亲和保持返回 200）；
    - `SIM_CHECK3_REAL_QUOTA_FAILOVER`（带 `details` 真配额 429 正常换号至 `tok-ag-2` 并返回 200）；
    - `SIM_CHECK4_SIBLING_POOL_ISOLATION`（跨凭据连接严格分桶隔离，源端口 `35860` vs `35864`）；
    - `RSE_AND_POOL_SIM_ACCEPTANCE=PASS`、`CLEANUP_OK`。
- **`controlled_live`（仅非 OAuth 车道、经 `127.0.0.1:8318` 单发最小请求）**：
  - `zhipu-coding-plan/glm-5.3-flash`：`status=200 finish=length usage_in=17 usage_out=16 elapsed_ms=1341`，admission journal `status=200 capacity=false successful=true protocol_result=completed`。
  - `opencode/glm-5.3-oc`（**刻意使用裸 `Python-urllib/3.12` UA**）：`responded=glm-5.3 status=200 finish=length usage_in=17 usage_out=16 elapsed_ms=3718`，admission journal `status=200 capacity=false successful=true protocol_result=completed upstream_header_ms=3680`。
  - `opencode/glm-5.3-flash-oc`（**刻意使用裸 `Python-urllib/3.12` UA**）：`responded=glm-5.3-flash status=200 finish=stop usage_in=17 usage_out=3 elapsed_ms=841`，admission journal `status=200 capacity=false successful=true protocol_result=completed upstream_header_ms=838`。

## 回滚入口

- 远端配置与投影文件回滚：从 `/root/cpa-guardrails-backup-20261120T143755.279333546Z`（或第一阶段前基线 `/root/cpa-guardrails-backup-20261120T141605.484329425Z`）恢复受控文件，按 `docs/runbooks/cpa-gateway.md` 重启 `cli-proxy-api` / `cpa-admission` / `nginx` 后重跑 strict doctor；Git 回滚不能替代远端恢复。

## 边界说明

- 本次仅操作 `bwg`，未触碰 `zz`；全部既有 OAuth 账号与 API key 渠道均原样保留，WorkBuddy RPM 保持 `18`。
- `controlled_live` 仅覆盖非 OAuth 车道（`zhipu-coding-plan` 与 `opencode`），未对真实 OAuth 账号发起压测或生成探针；OAuth 账号在自然流量下的长期封控安全性、真实缓存命中率提升与首字延迟表现，仍需在后续自然流量和 doctor 观测窗口中持续跟进。
- WorkBuddy CN 插件升级（`0.21.27` → `0.21.29`）保持为独立维护窗口，未在本批次混入。
