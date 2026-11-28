# 2026-11-28 BWG CPA：WorkBuddy CN hy4 flash 促销档改挂付费档（hy4flash-free → hy4flash）

## 触发与决策（用户指令，同日第二批）

- 用户指令：11-30 促销窗到期、且近两日不打算使用——「能否一步到位，直接修改为付费的
  Hy4 flash」。改挂映射经本地 CN IDE 产品缓存实锤：付费档 = 上游 id **`hy4flash`**
  （name 同为 "Hy4 flash"，**x0.03** credits，maxInputTokens 960000 /
  contextWindow 300k 默认 300k-600k-960k 档 / maxAllowedSize 1000000，与免费档同参）；
  非 `hy4std`（现名 "Hy4"，x0.29）亦非判否过的 `hy4-preview*` 付费桶。
- 语义变化：CN lane 的 hy4 flash 档从零成本促销 pin 换成计费 pin；
  `zero_cost_models` 整键摘除——credits 型 hold/冷却对全 lane 恢复生效
  （6008 仍按可恢复窗冷却、不落持久 hold）。`hy4flash-free` 回归排除表墓碑
  （12-01 促销结束后若上游该名失真，无残留面）。

## 仓库改动（commit `6d2e07c`，Full gates `-RunParallel` 1915 passed / 30 skipped / 903 subtests）

晨间开名事务（[20261128-bwg-wb-cn-hy4flash-free-open.md](20261128-bwg-wb-cn-hy4flash-free-open.md)）
的镜像翻转，六面同步：

- manifest：CN `plugin_routes` 第 4 pin `hy4flash-free`→`hy4flash`；
  `oauth.settings` 条目同名替换（max-context-length 保持 1000000 家族钉值）；
  developer-role override 同名替换；`plugin_exclusions` 摘 `hy4flash`（pin 化）
  + 回填 `hy4flash-free`（墓碑）。
- `cpa-admission.json`：lane models 同名替换（catalog_check 派生）；
  `zero_cost_models` 整键移除。
- `cockpit_local_provider_policy.json`：`hy4flash-free` 回归 retired_models
  （本地 cockpit 恢复剪除）。
- 派生面：catalog_check `--sync`（admission models、context overrides slug
  同参替换、apply/doctor lane 表、expectations）。
- 测试翻转两处漏网后全绿：catalog-check lane 断言、realm-pins 的
  `json.dumps(plugin_routes)` 叙事误中（route note 文本含 `hy4flash-free` 字样，
  收窄为只查模型列表与墓碑表）。
- 文档：README lane 表、cpa-gateway.md 三处、cpa-workbuddy-plugin.md 三处。

## 远端事务（零回滚，一次通过）

1. `cpa_bwg_guardrails.ps1 -Apply`：`GUARDRAILS_APPLIED`（exit 0）。**追加逻辑自动
   回填 `hy4flash-free` 墓碑**到远端排除表（14 项过渡态）——免费名退役无需手术。
2. `outputs/deploy-wb-cn-hy4flash-paid-swap-20261128.ps1`（DryRun PASS → apply，
   内置晨间三坑修复：120s 排空等待 / 锁内直探核心 8317 契约 180s 重试 /
   释放流量锁后 8318 旁证）：仅摘远端陈旧 `    - hy4flash` 行 + 核心重启。
   `DEPLOY_OK`，备份 `20261128T150008.939055201Z-wb-cn-hy4flash-paid-swap-20261128`。
3. 严格 doctor（HEAD `6d2e07c`）：`DOCTOR_CONTRACT_OK`、drift 17 项全 MATCH、
   `MODEL_IDS_UNKNOWN=none`、`catalog_oauth_missing=none`、排除表 `n=13`、
   wb lane OPEN、INTL 保持关闭暗置。

## 单发验收（一发不重试）

- `hy4flash`（付费）→ HTTP 200、`finish=stop`、回显 **`hy4-flash`**（付费 id
  连字符形，非 free 形）、content 2 字符 + reasoning 637、prompt 23 /
  completion 134 tokens → **LIVE_ACCEPTED**。
- credits 受控窗（快照→单发→快照，2s）：**account[3] total_used 1407→1409、
  total_remain 2093→2091** → **BILLED_CONFIRMED**——计费门控恢复实证
  （对照晨间免费档 9 账号零变动）。
- 核心目录契约 12 模型：含 `hy4flash`、无 `hy4flash-free`。

## 换挂后 ping 形态（观察项，非缺陷）

插件 active ping 换挂后池内多数账号走 `auto`（上游默认路由）全部成功；两台账号
保留 `hy4flash-free` 作为插件内部 ping 默认——插件 ping 直连上游、不经客户端目录
收窄，促销期内上游仍可达，属插件内部状态非配置泄漏（客户端面契约已实证无 free 名）。
促销结束后若该 ping 名上游失真，预期插件自行回退默认路由，届时按日志观察。

## 回滚入口

- config.yaml：备份 `20261128T150008.939055201Z-wb-cn-hy4flash-paid-swap-20261128`。
- 声明面：git revert `6d2e07c` → `-Apply`（免费 pin 回归需再做一次晨间式手术摘
  `hy4flash-free` 墓碑行，事务模板 `deploy-wb-cn-hy4flash-open-20261128.ps1`）。
