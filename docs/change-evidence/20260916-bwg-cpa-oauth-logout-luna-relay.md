# BWG CPA OAuth 登出（槽位保留）与裸名 luna 切换 r1

- Scope: 仅 `bwg`；未触碰 `zz`。授权：用户 2026-09-16 指令"暂时把 OAuth 真正/彻底
  退出 VPS 设备，但保留槽位；裸名 luna 路由到 r1/"。
- 槽位语义（按 `docs/runbooks/cpa-oauth-luna-slot.md`）：仅保留元数据（provider
  codex、裸名 luna、既定 OAuth 通道与设备登录流程）；**不保留任何 token 材料**
  ——事务刻意不备份 OAuth JSON，并删除 auth 目录、`/root` 与
  `/opt/cliproxyapi/backups` 下全部含 `access_token`/`refresh_token` 的 codex
  JSON（含历史备份副本）。provider 侧会话吊销不在本事务能力内（runbook 已注明，
  如需可经账号官方安全控制主动吊销设备/会话）。

## 实现与协作

- 并行会话提交 `e1828bb`（amend 自 3bbded9）：guardrails 新增
  `-DeactivateOAuthLuna` 事务（先停容器→r1 拓扑断言→裸名 luna 加入无前缀
  ai.input.im 条目→OAuth 删除→起容器→目录断言 bare+`r1/` 双 luna→三根
  零残留验证）、`cpa-health.py` 冒烟 luna→`gpt-5.6-sol`、fixture 上游回显
  请求模型、runbook 与测试同步。
- 本切片复核发现关键缺陷并修复：删除/验证扫描根原为 `/root` + `backups`，
  **漏掉活动 auth 目录**——活动 OAuth JSON 不会被删、CPA 重启照常加载、
  `OAUTH_REMOVAL_VERIFIED=yes` 假阳性（与 runbook 文字矛盾）。修复为三根
  （auth 目录 + `/root` + backups），先行执行即触发该缺口，故事务实际
  分两段完成（见下）。
- 文档同步：README 冒烟描述（代表性 OAuth 路由→代表性中转路由 sol）、
  `cpa-stale-cooldown-recovery.md` 基线（裸名 GPT 族全部来自中转条目；
  luna 站点封锁不属冷却）。
- 本切片另一增量（同日早先未提交、随本片收口）：fixture 验收上游回显请求
  模型（冒烟精确校验 responded model 的前提）。

## 执行时序（生产）

1. 并行会话先跑了带缺口的版本：config 已切换（bare luna→中转条目）、容器
   重启、新 health（sha `a3ebdf8c…`）已投影、`/root`+backups 历史副本已删；
   但活动 OAuth JSON 因缺口仍在 auth 目录并被加载。
2. 本切片执行修复版事务补完登出：
   `ROUTING_PREPARED bare_luna=r1` → `OAUTH_MATERIAL_REMOVED count=1`（活动
   auth JSON）→ `OAUTH_SLOT_RETAINED=metadata_only` → `BARE_LUNA_ROUTE=r1` →
   `OAUTH_REMOVAL_VERIFIED=yes`（三根零残留，此番为真验证）。

## 验证

- `auth/` 仅剩 `logs`，无任何凭据文件。
- readiness / generation 均 `HEALTH_OK`——sol 冒烟经中转真实出字（新冒烟
  首次生产验证）。
- 裸目录恰为基线 6 模型（luna 由中转条目注册），总数 17；`r1/gpt-5.6-luna`
  同在。
- 裸名 luna 单发实测 HTTP 502（站点/上游侧；当日早些为
  "Upstream access forbidden"，均为非本地问题）。sol/terra/astra/glm/5.5
  此前矩阵全部 200/stop。
- `NO_CDS_OK`（冷却持久化保持关闭）、`save-cooldown-status: false`、
  strict doctor `DOCTOR_CONTRACT_OK`、门禁 130 passed / 110 subtests 全绿。

## 回滚与边界

- OAuth：无文件级回滚（设计使然，token 材料已不存在于本机）；重新登入按
  runbook 走受支持设备登录流程生成新凭据，再以受控变更反转裸名 luna 映射。
- config 层回滚（如需撤下裸名 luna）：从无前缀条目 `models` 移除该行即可。
- 冒烟=sol 意味着中转站故障期间更新器按既有设计 exit 10 暂缓（不回滚）。
- generation-all 仍含 luna（注册路由的诚实上报；站点解封后即真实通过）。

## 受控验收（登出后追加，两层）

- 生产单发矩阵：`glm-5.3-flash`、`gpt-5.5`（responded=glm 别名精确）、
  `gpt-5.6-sol`、`gpt-5.6-terra`、`gpt-6-astra` 全部 200/stop；裸名
  `gpt-5.6-luna` 503 `internal_server_error`（站点侧当日第三种形态：
  403 forbidden → 502 → 503，均非本地）。luna 失败进内存冷却期间裸目录
  短暂 5 模型（预期自愈），稳态 6 模型/总数 17。全程 `NO_CDS_OK`。
- 生产冒烟两次 exit 10 后自愈 `HEALTH_OK`：sol 手动探针 4/4 HTTP 200 证实
  为中转瞬时 5xx 抖动（当日 502/503 量大的延续），defer 信号按设计工作，
  非契约失败。
- fixture 全场景（真实 v7.3.4 二进制 + 部署版 updater `563b0dcb…`/新 health
  `a3ebdf8c…`；验收脚本 `de8066d2…`/`1c35bde3…` b64+sha 断言）：
  `ACCEPTANCE_EXIT=0`——本次新增机制的闭环验证：sol 冒烟 × fixture 回显
  请求模型（旧 fixture 硬编码 luna 会在此精确校验下失败）。overload
  503/upstream=1、冷却窗零放大、62s 同进程恢复、真实 health exit 0、
  start_fail/model_exposure exit1+回滚、transient exit10 defer、success
  exit0。`NO_FIXTURE_LEFTOVERS`，临时目录已删，生产全程未受影响。
- 执行注记：astra 单发生成超过 SSH 通道 60s 空闲窗，改用后台 curl + 心跳
  输出后完成（与 fixture 的 setsid 脱离同属长任务操作口径）。
