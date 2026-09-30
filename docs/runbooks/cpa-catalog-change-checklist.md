# CPA 裸名/别名目录变更 Checklist

适用范围：bwg CPA 网关裸名/别名目录的增补、改名、退役
（真源 `scripts/remote/cpa_provider_routes.json`）。本清单把一次目录变更
收敛为照单执行，以本文件为起点，不再考古历史 diff。

## 分层事实：哪些手改、哪些派生

- **真源（手改）**：`scripts/remote/cpa_provider_routes.json`
  - `oauth_routes[].models`（OAuth lane）或 `providers[].models`（通道槽位）；
  - 排除清单：新 OAuth 名进 `codex_api_key_exclusions`（API-key lane 永不
    抢注），新 API-key 名进 `oauth_exclusions`（OAuth lane 永不抢注）；
    **退役名不删除**，保留在对应排除清单作墓碑（防复用/抢注，readiness
    fail-closed 拦截旧名重现）。
- **派生层（零改动）**：`cpa-health.py`（探针矩阵、expected_models、目录
  门）、`cpa_policy.py`（`EXPECTED_*` 常量与语义校验）、远端 config 的
  `oauth-excluded-models` 与 codex-api-key `excluded-models`（apply 时从
  manifest 派生）、`cpa-acceptance.py` 的生成契约断言。

## 必须同步的手改点

| 文件 | 位置 | 说明 |
| --- | --- | --- |
| `cpa_provider_routes.json` | 路由条目 + 排除清单 | 真源；退役留墓碑 |
| `scripts/remote/cpa-admission.json` | `lanes[].models` | 仅共享账号 lane 名单变化时 |
| `scripts/cpa_bwg_guardrails.ps1` | 两处 `expected` lanes 字典 + `CODEX_OAUTH_ROUTES_READY` 文案 | 独立预言机，**有意保留字面量**，勿改为派生 |
| `cpa_catalog_expectations.py` | `OAUTH_ROUTE_ALIASES` / `ADMISSION_LANE_MODELS` / `PROVIDER_MATRIX_TAIL` | 测试预言机唯一字面量 |
| `test_scripts.py` / `test_cpa_admission.py` | 仅复核故意变化型夹具（如 `matrix_targets` 里被刻意排除的别名） | 其余断言从常量模块派生 |
| `docs/runbooks/cpa-oauth-luna-slot.md`、`cpa-gateway.md` | 目录描述段落 | 记变更日期 |

## 执行顺序（铁律）

1. 按上表完成仓库改动。
2. full gates：`pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1`
   （目录/config 契约变更必走 full，不做 focused）。
3. **commit**：`-Apply` 要求干净树（脏树直接 REFUSE）。顺序是
   **commit → apply → doctor**；apply 后再补提交会让 projection-drift 门
   锚不上 HEAD 必 FAIL。
4. `-Apply`：确认 `GUARDRAILS_APPLIED`、`READY_STATUS=200`、备份目录行。
   apply 自带 CPA 容器与 cpa-admission 服务重启。
5. doctor 终验（默认严格模式）：`DOCTOR_CONTRACT_OK`、drift 全 MATCH、
   `MODEL_IDS_UNKNOWN=none`、`catalog_oauth_missing=none`。
6. 定向单发实测：远端 loopback `127.0.0.1:8318`（经 admission 全链），
   b64 投影探针脚本，`python3 -u` + `--command-timeout 300`；**一发不重试**
   （408/429/5xx 不 follow-up）；`finish=stop` 且回显模型名正确才算
   LIVE_ACCEPTED。
7. push（`main == origin`）；真实远端写入按需在 `docs/change-evidence/`
   留脱敏记录。

## 已知坑

- apply 前的 doctor 基线里 `admission-health=FAIL` /
  `client-model-catalog-contract=FAIL` 可能只是"工作区已改、远端未投影"的
  预期差异；上游目录先出现新名时 `MODEL_IDS_UNKNOWN` fail-closed 拦截属按
  设计。先对照最近收尾证据再判断，不要带病 -Apply。
- doctor 的 `catalog_oauth_missing` 按排序输出，不是 manifest 序。
- OAuth 别名在 health 目录门里天生 optional：上游撤名 = `not_listed`
  优雅降级不红；上游新增未声明名 = fail-closed。
- doctor 探针目录契约（`test_cpa_doctor_luna_state_*`）直接消费 guardrails
  内嵌 Python 块，改 `CODEX_OAUTH_ROUTES_READY` 附近逻辑后必须跑
  test_scripts.py 全量而非单测。
- `test_scripts.py` 中 policy 负例排除清单与夹具目录文本同形，编辑锚点须
  带三行上下文。
- 时序参考：2026-09-30 gpt-6.1-sol 增补从改动到 LIVE_ACCEPTED 约 1 小时，
  其中编辑约 15 分钟，其余是门禁与四轮远端验收往返。
