# bwg CN/INTL admission lane 拆分：投影与受控验收（2026-11-17）

**性质**：真实远端写入（reviewed deploy 事务 + 两次 `-Apply`，其一失败回滚）+ 受控单发验收。
**窗口**：2026-11-17 07:30–08:10 (+08)。
**授权**：用户 2026-11-17 "CN/INTL admission 拆分已完成一部分工作，你接手并投影生效"。
被投影的契约来自提交 `12c487f`（其提交信息明确 host projection 与 controlled live acceptance 待办）。

## 1. 投影前基线（只读 doctor）

8 个投影目标 MISMATCH（want=12c487f HEAD blob，got=12c487f^ 部署代，本地推导与 `got=` 全部对上）：
`cpa-admission.py`、`cpa-admission.json`、`cpa_provider_routes.json`、`cpa_request_guard.py`、
`cpa_admission_bootstrap.py`、`auto-update.sh`、`cpa_policy.py`、pin 文件；其余 9 目标 MATCH。
三个下游 FAIL 均为未投影的表现：`admission-integrity`（pin 旧）、`admission-health`（在役 4 lane）、
`workbuddy-intl-risk-state-permissions`（状态文件未建）。

## 2. 第一次 `-Apply`：fail-closed 拒绝（零变更）

`REFUSE admission_pin_transition_pending`——admission 代码换代被 pin 过渡守卫拦截
（bulky apply 不允许新旧 launcher/admission 混代）。按 92f0f57 先例走 reviewed deploy 事务。

## 3. 六件同换事务（`outputs/deploy-admission-cpintl-split-20261117.ps1`）

六件必须是同一原子单元，否则新 admission 拒绝启动：

1. `/opt/cliproxyapi/cpa-admission.py`（pin `16ace2c9…`）
2. `/opt/cliproxyapi/cpa-admission.json`（5 lane；`workbuddy-intl-oauth` 桶 =
   `deepseek-v4.1-flash-intl`/`-sg`/`hy4free`，CN 桶只剩裸名）
3. `/opt/cliproxyapi/cpa_provider_routes.json`（INTL plugin route + `oauth_model_aliases`
   fork 条目；`load_config` 启动时对 manifest 交叉校验，旧 manifest 必失配）
4. `/usr/local/libexec/cpa_request_guard.py`（内嵌新代 sha）
5. `/usr/local/libexec/cpa_admission_bootstrap.py`（新增 `--workbuddy-intl-state-file` 传参）
6. `/etc/vps-ssh-launcher/cpa-admission.sha256`（pin 同事务轮转 `6abf9d7f…` → `16ace2c9…`）

外加预建 `/var/lib/cpa-admission/workbuddy-intl-risk.json`（nobody:nogroup 0600，目录 0700，
payload `{"version":1,"not_before":0,"hold_reason":null}`）。流程：staged 校验 + 基线 sha 钉死 →
maintenance 锁 → 请求锁排他 drain（四 lane idle 断言）→ 备份 → 换件 → integrity check → 重启 →
五 lane readiness + 部署代契约证明（model_lanes 映射、zero_cost、inflight=1、6004 封顶 300）。
**结果**：`DEPLOY_OK`，备份 `/opt/cliproxyapi/backups/20261116T234255.333335427Z-admission-cpintl-split-20261117`。

## 4. 第二次 `-Apply`：catalog 电池失败并回滚（根因修复）

- 失败：全部文件投影成功后，`docker restart` 后的 `cpa-health.py readiness` 电池 12×5s
  全部 `LOCAL_CONTRACT_FAILED`（exit 20），`restore_all` 回滚，`ROLLBACK_FAILED` 一次
  （restore_all 尾部的监听/路由断言与重启同窗执行；未逐项定位，恢复结果另行全量只读复核证明）。
- 根因：readiness 的 fail-closed 目录门（`ids - allowed` 即拒）从 manifest `plugin_routes`
  原始名取允许集，**不读 `oauth_model_aliases`**——fork alias `deepseek-v4.1-flash-intl`
  一旦被 CPA 挂上目录即为"未知 ID"。`12c487f` 把 alias 折进了 apply/doctor 的契约面，漏了
  `cpa-health.py` 这一面。
- 回滚落点复核（只读）：六件事务文件保持新代（`$BK` 即事务后状态）、config.yaml 恢复无 alias、
  auto-update.sh/cpa_policy.py 回到旧 sha、CPA/admission 健康。主机自洽，无半投影态。
- 修复：`7adf0f7`——`_PLUGIN_MODEL_NAMES` 折入 manifest `oauth_model_aliases` 的 alias
  （归 optional：目录缺失仍健康），回归测试从真 manifest 派生期望并断言行为分支；
  Full 门禁并行 suite 1729 passed。

## 5. 第三次 `-Apply`：成功

`GUARDRAILS_APPLIED`，目录 16 模型含 deepseek 三入口；随后 strict doctor：
17/17 `MATCH`、`admission-integrity=OK`、`admission-health=OK`、双 workbuddy 状态文件
permissions OK、`lane-hold-workbuddy-intl-oauth=OPEN`、`DOCTOR_CONTRACT_OK`（exit 0）。

## 6. 受控单发验收（经 admission 8318 回环，key 不回显，错误体只取 type/code）

| 入口 | lane_admit 归因 | HTTP | 响应要点 |
|---|---|---|---|
| `deepseek-v4.1-flash` | `workbuddy-oauth` | 200 | 上游回显同名，finish=stop，内容恰为 "OK" |
| `deepseek-v4.1-flash-intl` | `workbuddy-intl-oauth` | 200 | **上游回显 `deepseek-v4.1-flash`**（fork→上游裸名实证） |
| `deepseek-v4.1-flash-sg` | `workbuddy-intl-oauth` | 429 | admission 已按正确 lane 准入；上游 `rate_limit_error/rate_limit_exceeded` |
| `hy4free` | `workbuddy-intl-oauth` | 200 | 上游回显 `hy4`；finish=length（16 token 探针预算内无文本，路由已证） |

其余验收面：`/v1/models` 读回含四入口与 `deepseek-v4.1-flash-oc` 单宿别名；healthz 五 lane
全 idle；`workbuddy-risk.json` 与 `workbuddy-intl-risk.json` 均 nobody:nogroup 0600；
config.yaml 持久化 `name: deepseek-v4.1-flash, alias: deepseek-v4.1-flash-intl, fork: true`。

**sg 429 判读**：按 runbook §4，429 属上游信号——单发不重试、不触发有界回退、不碰 cooldown。
lane 拆分（本次变更的全部范围）已由 lane_admit 证明；sg 模型桶的上游可用性是 provider 侧
瞬态（历史上 11134/429 均有先例），留给常态观测，不作本变更缺陷。

## 7. 回滚入口

- 主机：事务备份目录（§3）+ `restore_all` 语义（六件 + config.yaml + 服务重启）；
  bulk apply 备份窗保持 keep_8。
- 仓库：revert `7adf0f7`（health 折入）与本记录；admission 契约回退需再走一次同构事务
  （pin 换回旧代），不得用普通 `-Apply` 回滚 admission 换代。

## 8. 残留与移交

- 无 -Apply 欠账：全部 17 投影目标 MATCH。
- sg 模型桶上游 429：provider 侧，常态观测。
- `deepseek-v4.1-flash-intl` 的长期 QoS、账号资格、provider 限流隔离均不在本次证明范围
  （runbook §5 明示单发边界）。
