# 2026-11-14 bwg CPA OpenCode slot 2 单宿别名重新收录

## 背景与授权

- 用户 2026-11-13 深夜续订 OpenCode Go 订阅，指令：重新启用 opencode，
  恢复 `deepseek-v4.1-flash-oc`，且 **opencode 上只开放
  `deepseek-v4.1-flash-oc`**。
- 前置事实：2026-11-11 `2366cd0` + `20540ac` 已整槽退役（订阅失效、用户
  当时决定不续订）。`providers.env` 的 `BASE_URL_2`/`API_KEY_2` 映射两次
  退役期间保留未动，续订沿用原 key（无凭据轮换）。
- 续订生效实证：本地直探上游 `GET /zen/go/v1/models`（携带原 key）返回
  200，30 个模型含 `deepseek-v4.1-flash`。

## 变更（commit dbf93ea，8 文件）

- routes manifest：slot 2 以仅 `{name: deepseek-v4.1-flash, alias:
  deepseek-v4.1-flash-oc}` 单宿条目回归——无裸名双宿、无 priority 排序、
  `x-opencode-session` 钉头保留；`opencode.ai` 摘出 `retired_hosts`
  （该条目在位时 apply 会把 live provider 剪掉，摘除后 slot 才能投影）；
  `oauth_exclusions` 复录别名墓碑防 OAuth lane 抢注。
- doctor `deepseek-route-order`：manifest/live 双向断言恰 1 条 opencode
  路由且模型集=仅别名（键名改 `deepseek-*-opencode-models`，期望值
  `alias-only`），终行 `WB_BARE_ONLY_OC_ALIAS`。
- acceptance fixture（本就别名单宿形状，逐字回植）、fixture echo 表、
  `PROVIDER_MATRIX_TAIL` 探针位、guardrails 契约测试、`cpa-gateway.md`/
  `cpa-workbuddy-plugin.md` 同步记录收窄口径。
- 同事务把已提交的 `d2f2eb7`（chatgpt `gpt-6.1-sol`/`gpt-6.1-terra`
  死名退役）一并投影上线。

## 门禁与验收

- full gates（`scripts/run_gates.ps1`）：pytest 1343 passed / 25 skipped
  （701 subtests）、bandit、ruff check + format、mypy 全绿，exit 0。
- `-Apply`：`GUARDRAILS_APPLIED`、`READY_STATUS=200`、
  `BACKUP_DIR=/root/cpa-guardrails-backup-20261113T162305.089872801Z`、
  全部投影 `PROJECTION_HASH_VERIFIED`；catalog `models=11`（原 10 +
  oc 别名）。
- doctor 终验（strict，`PWSH_EXIT=0`）：`DOCTOR_CONTRACT_OK`；
  `deepseek-manifest/live-opencode-models=alias-only`；
  `deepseek-provider-order=WB_BARE_ONLY_OC_ALIAS`；`MODEL_IDS` 含
  `deepseek-v4.1-flash-oc`；`MODEL_IDS_UNKNOWN=none`；
  `MODEL_CONTEXT_DRIFT=none`；drift 全 MATCH；`heartbeat=cpa-update
  result=deferred exit_code=77` 按 `19a329d` 新契约不再 fail。
- 定向单发（8318 全链，一发不重试）：
  `LIVE_ACCEPTED http=200 finish=stop echo_model=deepseek-v4.1-flash
  ms=899 text='OK'`（回显上游名=relay 语义正确）。

## 事故与处置（同窗）

- apply 后 cpa-admission 进入 crash loop：16:24:18Z 起每次启动
  `FileNotFoundError: /var/lib/cpa-admission/chatgpt-risk.json` →
  `startup_failed` → systemd auto-restart 循环，8318 不可用约 2–3 分钟
  （16:24:18Z–16:27Z；CPA 容器 8317 未受影响）。
- 根因：`chatgpt-risk.json` 在 16:23:49–16:24:18 之间被带外静默删除。
  journal 无删除痕迹；HEAD 全链路（apply.sh/doctor.sh/admission.py/
  launch.sh/bootstrap）均无状态文件删除路径；apply 备份仅含投影文件不含
  risk 状态。该窗口内本会话与另一并行会话同时在主机上活动（apply/SSH 短
  会话交织，来源无法唯一归因）；16:24:18 的第二次 admission restart 亦
  不来自本会话的 apply（脚本仅 line 2409 一次 restart，与 16:23:11 对账）。
- 复盘锚点：admission 启动对四个 `<lane>-risk.json` 是严格预存在契约
  （startup 读取无 `FileNotFoundError` 容忍），任一缺失即 crash loop =
  公网网关全不可用。修复 = 重建中性状态文件
  （`{"hold_reason": null, "not_before": 0, "version": 1}`，属主
  nobody:nogroup、0600）+ restart，随即 `active` + healthz 200。
- 副作用：chatgpt lane 的 ~18645s 持久冷却随状态重建清零（`not_before=0`）。
  该冷却本就由已退役死名合成、到期无效，清零无害且让 `gpt-6-luna` 提前
  恢复可服务。
- 挂账：与并行会话核对 16:23:49–16:24:18 的带外动作与 16:24:18 restart
  来源。后续如需"清 lane 持久状态"，必须保持文件存在（写中性内容而非
  删除）；若要容忍缺失，属 `cpa-admission.py` 改动 = reviewed deploy 面，
  单独评审。

## 边界

- wb credits 仍死（6008），裸名 `deepseek-v4.1-flash` 维持不可用（属
  workbuddy 车道，不在本次范围）。
- 裸名 / `muse-spark-1.3-contributor` / `claude-haiku-5-5` 不随槽回归
  （用户指令）。
