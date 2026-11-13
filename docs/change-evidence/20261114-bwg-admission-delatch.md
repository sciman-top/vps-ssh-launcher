# 2026-11-14 bwg admission 去误报化部署（6008 窗口化 + 认证锁实证化 + 包裹形状豁免）

## 授权与范围

- 用户指令："时常误报，而且冷却时间异常的很长，这些问题要深挖根因，彻底修复"。
- 调查后向用户呈报两档：原 B 档（fork 核心重建）经深挖确认**缺活样本验证**
  （触发器已摘除，合成路径的最后一环无法唯一定位），用户改选
  **A+B'（admission 侧同批）**；核心侧行为挂账上游 issue + 择窗手术。
- commit `3a2990b`（6 文件）：cpa-admission.py + integrity pin + 三测试文件 +
  cpa-failure-triage.md。

## 根因地图（代码级）

| 缺陷 | 锚点 | 后果 |
| --- | --- | --- |
| 6008 → 持久锁 `account_credits_exhausted` | cpa-admission.py 旧 1081-1084 | UTC+8 日额度烧尽被当账号死亡；11-07/11-08/11-11/11-13 四次误报同源 |
| 裸 401/403 → 持久锁 `authentication_required` | 旧 1063/1085 | 11-11 兜底腿 JSON 403 幻影整夜锁 |
| 裸引号豁免漏掉包裹形状 + strict 车道 RA 无上限镜像 | capacity_scope 1294-1300 / release strict 分支 | 核心对 400 model_not_found 合成 503+25902s RA，绕过豁免直接 park 车道 12h 级 |

## 部署（ reviewed deploy transaction）

- `-Apply` 被 `REFUSE admission_pin_transition_pending` **按设计拒绝**（pin
  轮换不许走 bulk re-projection）。
- 事务脚本 `outputs/deploy-admission-delatch-20261114.ps1`（模板
  `deploy-admission-credits-6008-20261107.ps1`）。
- **第一次尝试失败（如实记录）**：swap 后 readiness 超时，
  `cpa-admission-bootstrap startup_failed error=OSError detail=admission
  generation mismatch`——request-guard 模块内嵌 `SOURCE_SHA256` 与 admission
  代际配对，只换 admission+pin 不换 guard 必然断裂。事务自动
  `ROLLBACK_VERIFIED`（服务回到旧代，healthz 200）。
- 修复：guard 模板替换 `__SOURCE_SHA256__`=$newPin 后四件同换
  （admission+config+guard+pin，swap 前断言 live guard 仍配对旧代、staged
  guard 配对新代，rollback 含 guard 恢复）。二次执行
  `DEPLOY_OK pin=de1a548d…` + `DELATCH_20261114_CONTRACT_OK`（部署代内嵌
  Python 断言：6008 三形状→(None,None)、裸 permission_error→(None,None)、
  authentication_error→锁、suspended→account_restricted、直接/包裹
  model_not_found→capacity_scope None、zero_cost 配置在位、6004 上限 300）。
- 备份：`/opt/cliproxyapi/backups/20261113T182431.362848132Z-admission-delatch-20261114`
  （含 guard 回滚件）。首次失败事务备份
  `…181806.067434618Z-admission-delatch-20261114`（ROLLBACK_VERIFIED）。

## 验收

- DryRun：`DRY_RUN_OK baseline=29fa3b1c… candidate=de1a548d… idle=true`。
- healthz：四车道 `hold=None cooldown=False inflight=0`。
- 定向单发：wb `deepseek-v4.1-flash` 200/finish=stop/1.16s；
  chatgpt `gpt-6-luna` 200/finish=stop/1.96s（分类收紧不伤健康路径）。
- full gates：**1401 passed / 1 failed**——唯一失败
  `test_herestring_escaping…` 指向并行会话 untracked WIP 文件
  `scripts/cpa_status_deploy.ps1:69`（rglob 扫到 untracked），在本写集之外；
  admission/credits/credits_e2e/guardrails_script/health_doctor/policy_runtime
  套件全绿（365+71 passed），ruff check/format 与 mypy（gate 范围）绿。
- integrity pin 重生成（de1a548d…）随 commit 钉 HEAD。

## 回滚入口

- 事务备份目录反向恢复（含 guard）；仓库侧 revert `3a2990b`
  （cpa-admission.py 与 pin 必须同批回退）。

## 挂账

- 上游 issue：核心把 `model_not_found` 归类为 cooldown-worthy 且把
  usage-window reset 附着到永久性模型错误（#7313 先例）；补丁需活样本。
- `scripts/cpa_status_deploy.ps1` herestring 违规属并行会话 WIP，由其收口。
- 未来 admission 代际轮换必须走本事务族（四件同换），勿走 `-Apply`。

## 二次部署（同日全面复审增补，commit 961ec7a）

- 复审发现第一版豁免仍有未闭合形状：`protocol_errors` 的 prose 解包为
  workbuddy 专属且只认 int code，chatgpt 车道纯 prose 包裹体不产生任何
  error 条目，基于 `error_text` 的扫描会漏。`capacity_scope` 增加原始
  body 扫描，覆盖全部三种包裹形状（直接 JSON / JSON-envelope 包裹 /
  纯 prose）。
- 二次事务：baseline=`de1a548d` → candidate=`d2256369`，四件同换，
  `DEPLOY_OK` + `DELATCH_20261114_CONTRACT_OK`（含 prose 形状断言）。
- 验收：healthz 全车道 `hold=None`；wb 单发 200/finish=stop/1.44s；
  doctor `DOCTOR_CONTRACT_OK` exit 0（commit 后复跑；部署先于提交窗口内
  integrity/drift FAIL 属预期态，非缺陷）。
- 备份：`/opt/cliproxyapi/backups/20261113T184054.006115344Z-admission-delatch-20261114`。
