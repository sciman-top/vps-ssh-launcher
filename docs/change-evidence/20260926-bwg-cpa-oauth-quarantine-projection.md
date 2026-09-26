# BWG CPA OAuth lane 可逆隔离：投影与受控实战验收（2026-09-26）

## Scope

- Target: BWG only。ZZ 未访问、未改动。
- 请求变更：把 CPA 封号/限流/降智审查的 P1-A（OAuth lane 可逆隔离）与 P2-A
  （探针默认 opt-out）落地，投影到远端，并做一次可逆的单机受控验收。
- 本仓提交：`12358df`（本地 `cpa_bwg_guardrails.ps1` + 远端 `cpa-health.py` /
  `cpa_policy.py` + `test_scripts.py` + 五份 runbook/README + `pyproject.toml`）。
- 变更后追加提交：`restore_all` 零改动路径不再重启容器（见 S6）。
- 未新增任何凭据、随机路径、订阅地址或完整命令回显。

## 投影对象

远端 `cpa-health.py` 与 `cpa_policy.py` 内容变更，需 `-Apply` 投影；其余四个受管
文件（`auto-update.sh`、`cpa_provider_routes.json`、fail2ban filter/jail）未变更。
doctor 的 `==oauth-quarantine==` 段与 apply 的隔离拒绝门内嵌在本地 guardrails
脚本里，跑 doctor 即生效，无需投影。

## Timeline

- **S0 提交。** `12358df`。核对工作区 = HEAD：`git diff HEAD -- scripts/remote/`
  为空，且 HEAD blob SHA-256 与 doctor 期望值逐字节一致
  （`cpa-health.py` `9eb9bfe2…`、`cpa_policy.py` `5d9ee4ce…`）。投影顺序不可颠倒
  （doctor 的 `==projection-drift==` 比对 HEAD blob，`-Apply` 投影工作区字节）。
- **S1 只读 strict doctor（投影前）。** `DOCTOR_CONTRACT_FAILED`（exit 1）。
  `==projection-drift==` 只有两项 MISMATCH（`cpa-health.py`、
  `cpa_policy.py`），其余四项 MATCH——正是"待投影"信号。其余契约项全 OK：
  `gateway-throttle-status=429`、`gateway-per-ip-rate-limit=OK`、
  `config-permissions=owner-only`、`MODEL_IDS_UNKNOWN=none`、
  `cooldown_state=none`、`luna_state=available`、`oauth_monitor=OK`。
  新 `==oauth-quarantine==` 段读数为 `oauth_quarantine=none`。
- **S2 `-Apply` 投影。** 六个文件全部 `PROJECTION_HASH_VERIFIED`；备份
  `BACKUP_DIR=/root/cpa-guardrails-backup-20260926T020705Z`；`READY_STATUS=200`；
  `GUARDRAILS_APPLIED`（exit 0）。投影后 `config.yaml` SHA-256 未变
  （`8dc5c078…`）。重载 nginx 时出现一次瞬时 `curl (56) Recv failure`，随后
  `HEALTH_OK`。
- **S3 doctor 复验。** `DOCTOR_CONTRACT_OK`（exit 0）；`projection-drift` 六项
  全 MATCH；容器 `v7.3.17@sha256:a1dffb9c…` `restart=0`。
- **S4 拒绝路径验收（零改动）。** 在无标记状态执行 `-RestoreOAuthLuna`：
  远端输出 `REFUSE no OAuth quarantine marker to restore` +
  `ROLLBACK_SKIPPED no_mutation`，exit 1。随后 doctor 的 `==container==`
  显示 `started=2026-09-26T02:07:06.943229289Z`、`restart=0`，与执行前完全
  一致——**证明拒绝路径没有造成容器重启**（该行为由 S6 的修复引入，S4 即其
  实测证据）。
- **S5 隔离 / 恢复往返验收。**
  - `-QuarantineOAuthLuna`：`ACTIVE_OAUTH_FILES=1` →
    `QUARANTINE_APPLIED aliases=gpt-5.6-luna,gpt-6-luna` →
    `OAUTH_CREDENTIAL_RETAINED=yes`、`QUOTA_STATE_RESET=no`、
    `READY_STATUS=200`，exit 0。
  - 隔离态 doctor：`oauth_quarantine=active aliases=gpt-5.6-luna,gpt-6-luna`，
    `oauth_quarantine_since=2026-09-26T02:10:53Z`，
    `oauth_quarantine_note=credential_retained; background_refresh_continues;
    not_a_reset_mechanism`；`MODEL_IDS` 中两个 Luna 别名已消失；
    `catalog_gpt6_luna=absent`；`DOCTOR_CONTRACT_OK`（exit 0）——隔离被识别为
    已声明的契约状态，**没有误报 OAuth 故障**。
  - 流量面验收（同一 SSH 会话内）：非 OAuth 路由 `glm-5.3-flash` 生成返回
    `HEALTH_OK`；对 `gpt-6-luna` 的 `chat/completions` 返回
    `HTTP 400` + `error.code=model_not_found`（`invalid_request_error`），
    即被 CPA 本地拒绝、未触达 provider。两条判据同时成立。
  - `-RestoreOAuthLuna`：`QUARANTINE_RELEASED aliases=gpt-5.6-luna,gpt-6-luna`、
    `RESTORED_OAUTH_ALIASES=pending_catalog`、`READY_STATUS=200`，exit 0。
    `pending_catalog` 是预期读数：重启后 CPA 尚未把 OAuth 路由重新登记进
    `/v1/models`，脚本据此显式标注而不是失败。
  - 恢复后 doctor：`oauth_quarantine=none`；`MODEL_IDS` 中两个 Luna 别名
    回归；`catalog_gpt6_luna=present`、`luna_state=available`；
    `cooldown_state=none`；`oauth_monitor=OK`；drift 六项全 MATCH；
    `DOCTOR_CONTRACT_OK`（exit 0）。
- **S6 追加修复。** 验收中发现 `restore_all` 无条件 `docker restart`，使"拒绝
  执行"这种零改动路径也付出一次全量容器重启，并把拒绝信号淹没成看似已验证的
  回滚。修复为：回滚前用 `cmp -s` 比对 `config.yaml` 与备份，字节一致即
  `ROLLBACK_SKIPPED no_mutation` 并跳过重启与就绪探测；仅在确有改动时输出
  `ROLLBACK_VERIFIED`。S4 即为该修复的实测证据。

## 可逆性证据

`config.yaml` SHA-256 往返：

| 阶段 | SHA-256 前缀 |
|---|---|
| 投影前 / `-Apply` 后 | `8dc5c0781eafbbbd` |
| 隔离中 | `78e0fb148ef98e7c` |
| 恢复后 | `8dc5c0781eafbbbd` |

`-Apply` 未改变 `config.yaml`；隔离只改 `oauth-excluded-models.codex`，恢复后
字节回到隔离前状态。

## Verification boundary

- 远端 `cpa-health.py` / `cpa_policy.py` 与 HEAD blob SHA 相同，部署字节即受测
  字节。
- doctor 对部署版 `cpa_policy.py` + 线上 `config.yaml` 跑出 `POLICY_OK` 与
  `semantic-policy=OK`，隔离态与恢复态各一次。
- 全程未读取、未复制、未删除、未回放任何 OAuth 凭据 JSON；未调用
  `reset-quota`；未清理冷却状态。凭据文件数在三个阶段均为 1
  （`ACTIVE_OAUTH_FILES=1`、`oauth_codex_files=1`）。
- 隔离窗口内只发出 1 次真实 provider generation（`glm-5.3-flash`，用于证明非
  OAuth lane 未受影响）。未对 OAuth lane 发出任何生成请求。
- 未评估 provider 侧会话状态；`OAUTH_CREDENTIAL_RETAINED=yes` 只证明 VPS 本地
  保留可刷新物料。

## Hygiene

- 三个 `BACKUP_DIR`（`/root/cpa-guardrails-backup-20260926T020705Z`、
  `/root/cpa-oauth-quarantine-backup-20260926T021053Z`、
  `/root/cpa-oauth-quarantine-backup-20260926T021240Z`）保留，未删除。
- 隔离标记 `/opt/cliproxyapi/oauth-quarantine.json` 已随恢复删除
  （`oauth_quarantine=none`）。
- 本轮无事故、无 rollback 触发。

## Residual watch

- 隔离窗口内观测到 `auth_unavailable_by_lane={"codex/gpt-6-luna": 5}`
  （保留样本，非全量计数）与单一客户端 1.0s 中位间隔的 503 簇；doctor 明确标注
  这类读数是 `incomplete_bounded_error_dumps`，不作为风控窗口结论。
- 已知限制（上游 `Retry-After` 不驱动冷却、无 lane 级聚合速率预算、未登记模型
  非请求前阻断、slot 3 明文 HTTP、本地 429 未带 `Retry-After`、双 key 过渡）
  仍开放，已在
  `docs/runbooks/cpa-ban-throttle-incident-response.md` 与
  `outputs/cpa-risk-review-2026-09-26.txt` 记录。

## S7 追加修复：OAuth 在册性口径（提交 `bf5e102`）

恢复后的收口 doctor 暴露了一个诊断自相矛盾：同一轮里
`==client-model-catalog==` 的 `MODEL_IDS` 含 `gpt-5.6-luna`，而
`==cooldown-state==` 报 `catalog_gpt6_luna=absent` 与
`luna_state=unavailable_unclassified`。

- **现场刻画**：连续 12 次（约 24s）采样 `/v1/models` 得到稳定结果——
  `gpt-5.6-luna` 在册、裸名 `gpt-6-luna` 缺席、`gpt-6-sol` 反而上架、总数 13、
  `cds_files=0`。约 10 分钟后再次采样，`gpt-6-luna` 自行回归，
  `luna_state=available`。期间 `config.yaml` SHA-256 始终为隔离前的
  `8dc5c078…`，`oauth-excluded-models.codex` 已还原——**属上游/账号侧目录波动，
  不是本地配置漂移，也不是隔离事务的残留**。
- **影响**：旧口径只看裸名，会在该窗口把仍在服务的 OAuth lane 报成故障
  （`unavailable_unclassified`），与同轮 `MODEL_IDS` 直接矛盾。
- **修复**：期望别名集合改由 `cpa_provider_routes.json` 的 `oauth_routes` 派生；
  新增 `catalog_oauth_aliases` / `catalog_oauth_missing`；状态细分为
  `available` / `available_partial` / `unavailable_unclassified` /
  `unknown_route_manifest` / `unknown_catalog_unreadable`。
  `catalog_gpt6_luna` 保留为单名兼容读数。
- **验证**：新增单测用本地 stub `/v1/models` 驱动 doctor 内嵌代码，覆盖三种在册
  组合；修复后 live doctor `DOCTOR_CONTRACT_OK`，`catalog_oauth_missing=none`、
  `luna_state=available`。该修复只在本地 guardrails 脚本内，无需重新投影。

## S8 追加变更：本地 429 的 Retry-After 契约（P2-D）

改动：`-Apply` 现在保证 nginx 公网入口具备

```nginx
map "$limit_req_status:$limit_conn_status" $cpa_throttle_retry_after {
    default "";
    "~REJECTED" 1;
}
...
add_header Retry-After $cpa_throttle_retry_after always;
```

- **作用域**：`add_header` 写在 **server 级**。这是安全的**前提**是文件内不存在
  其它 `add_header`（已核实：改动前部署文件零 `add_header`），因为 nginx 的
  `add_header` 不叠加继承，内层声明一个会顶掉继承的整组头。
- **不伪造**：map 只在 `$limit_req_status` 或 `$limit_conn_status` 为 `REJECTED`
  时非空；nginx 对空值 `add_header` 不发出该头。因此 `200`/`401`/`404` 与上游
  透传的 `429`/`5xx` 都不受影响。
- **投影**：`-Apply` 六个文件 `PROJECTION_HASH_VERIFIED`、`GUARDRAILS_APPLIED`
  （exit 0）；nginx 配置哈希 `88dba4ec…` → `74c02452…`；备份
  `/root/cpa-guardrails-backup-20260926T023421Z`。`nginx -t` 在 reload 前通过，
  失败即回滚。
- **doctor 复验**：`safe-throttle-retry-after=OK`、
  `throttle-retry-after-map-count=1`、`gateway-throttle-status=429`，
  `==public-route-contract==` 仍为 `valid_path_unauth=401` / `bare_path=404` /
  `wrong_path=404`，`DOCTOR_CONTRACT_OK`（exit 0），六项 drift 全 MATCH。
- **受控压测验收**（从 VPS 本机对公网入口突发 60 个**已认证** `/v1/models`
  请求，并发 24；用有效 key 是为了不产生 401 从而不触碰 fail2ban）：

  | 读数 | 值 |
  |---|---|
  | `BURST_CODES` | `{"200": 12, "429": 48}` |
  | `BURST_RETRY_AFTER` | `{"200": ["none"], "429": ["1"]}` |
  | `CONTROL_STATUS` / `CONTROL_RETRY_AFTER` | `200` / `none` |

  即：被本地限流器拒绝的响应**全部**带 `Retry-After: 1`，成功响应**一个都没有**，
  窗口排空后恢复正常。两类限流器（`limit_req` 与 `limit_conn`）都被触发。

## S9 追加验收：隔离期间 `-Apply` 的拒绝门（此前未验）

隔离事务引入的 `-Apply` 拒绝门（防止例行投影把 `oauth-excluded-models.codex`
改回原样、静默撤销风控决定）此前只在单测层面被覆盖，从未在真实环境触发过。
本轮补做，全程可逆。

- 基线（当前 HEAD）：六项 `projection-drift` 全 MATCH、`DOCTOR_CONTRACT_OK`；
  `config.yaml` `8dc5c078…`、nginx 配置 `74c02452…`、容器
  `started=2026-09-26T02:34:23Z`。
- **S9.1 隔离**：`QUARANTINE_APPLIED aliases=gpt-5.6-luna,gpt-6-luna`、
  `OAUTH_CREDENTIAL_RETAINED=yes`、`QUOTA_STATE_RESET=no`、`READY_STATUS=200`，
  exit 0；备份 `/root/cpa-oauth-quarantine-backup-20260926T025012Z`。
- **S9.2 隔离期间执行 `-Apply`**：远端输出
  `REFUSE OAuth lane quarantine is active; run -RestoreOAuthLuna before -Apply`，
  exit 1。该门位于 apply 脚本中**备份目录创建之前**、`restore_all()` 定义之前，
  因此拒绝路径不进入任何事务框架。
- **S9.3 零变更四重证明**：

  | 证据 | 读数 | 结论 |
  |---|---|---|
  | `config.yaml` | `78e0fb14…`（= 隔离态哈希） | 未被重算回原样 |
  | nginx 配置 | `74c02452…`（= 基线） | 未被改写 |
  | `/root/cpa-guardrails-backup-*` | 最新仍是 `…T023421Z`（02:34 那次） | 未创建备份 → 在 `mkdir` 前退出 |
  | 容器 | `StartedAt=02:50:13Z`、`RestartCount=0` | 唯一重启来自隔离，被拒的 apply 未重启 |

- **S9.4 恢复**：`QUARANTINE_RELEASED`、`RESTORED_OAUTH_ALIASES=pending_catalog`、
  `READY_STATUS=200`，exit 0。
- **S9.5 收口 doctor**：`oauth_quarantine=none`、`luna_state=available`、
  `catalog_oauth_missing=none`、`cooldown_state=none`、`oauth_monitor=OK`、
  `safe-throttle-retry-after=OK`、`fail2ban-ban-scope=loopback_exempt`；
  `config.yaml` 回到 `8dc5c078…`（与隔离前逐字节一致），nginx 配置仍
  `74c02452…`；六项 drift 全 MATCH；`DOCTOR_CONTRACT_OK`（exit 0）。

本轮仅触发 2 次容器重启（隔离 + 恢复），未产生 OAuth lane 生成请求。
