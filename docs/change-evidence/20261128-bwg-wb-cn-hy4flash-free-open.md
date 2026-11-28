# 2026-11-28 BWG CPA：WorkBuddy CN 车道开放 hy4flash-free（裸 pin + 零成本声明）

## 触发与决策（用户指令）

- 用户指令「CPA 中开放 workbuddy cn 的 Hy4 flash 模型路由/映射」：`hy4flash-free`
  （x0.00 促销免费档，上游试用窗 2026-11-16..11-30，显示名 "Hy4 flash"）在
  **CN** 车道以裸 upstream id 重新开放。2026-11-17 该名曾随 INTL 侧退役删除
  （见 [20261117-bwg-wb-intl-close-dark.md](20261117-bwg-wb-intl-close-dark.md)）；
  本次只动 CN，INTL 关闭暗置态不动（`workbuddy-ai-provider` 插件保持卸载）。
- 无别名桥：没有其他 lane 认领该名，客户端名 = 裸 upstream id（与 CN 裸名先例一致）。
- 上游可用性预证实：开放前 CN 插件的 active ping 已持续以 `hy4flash-free` 打
  两台 CN 凭据且 200（生产日志），dynamic discovery 17 模型。

## 仓库改动（commit `23412d0`，Full gates `-RunParallel` 1915 passed / 30 skipped / 903 subtests）

- `cpa_provider_routes.json`：CN `plugin_routes` 第四 pin `hy4flash-free`；
  `oauth.settings.workbuddy-provider` 追加 `{name, max-context-length: 1000000}`
  （沿用 11-17 INTL 声明值）；`workbuddy_developer_role_override.models` 追加；
  `plugin_exclusions.workbuddy-provider` 摘除 `hy4flash-free`（apply 会
  REFUSE `plugin exclusion blocks a declared plugin model`，与 984a935 同款）。
- `cpa-admission.json`：`workbuddy-oauth` lane models 同步（catalog_check 派生）
  + `zero_cost_models: ["hy4flash-free"]`——credits 型 hold/冷却不再 gate 免费档，
  账号型信号仍全 lane 生效（admission.py 通用逻辑，代码零改动）。
- `cockpit_local_provider_policy.json`：`hy4flash-free` 退出 retired_models
  （catalog_check 第 6 门要求 retired∩live=∅）；`hy4free` 保持退役。
- 派生面：catalog_check `--sync` 更新 admission models、context overrides
  （1,000,000 窗 / compact 320000）、apply/doctor 内嵌 lane 表、expectations。
- 测试：realm-pins、admission-credits（zero_cost 断言翻转）、credits-e2e 注记、
  cockpit-prune 夹具、catalog-check 夹具（slug 改 `hy4free` 维持 not-live 路径）。
- 文档：README lane 表、cpa-gateway.md（AUP 行/退役史/6008 段）、
  cpa-workbuddy-plugin.md（收窄目录/退役史/6008 行）。

## 远端事务顺序（commit 先于投影；`-Apply` 拒脏树）

1. `cpa_bwg_guardrails.ps1 -Apply`（`-LauncherEntryPointOverride` 走分块路径，
   规避 v8.1.8 时代 StreamStdin 大载荷坑）：`GUARDRAILS_APPLIED`、
   `READY_STATUS=200`、备份 `/root/cpa-guardrails-backup-20261128T141856.571947848Z`。
   注意：本批同投了前序会话遗留的未推送提交 `c1b2ee0`（chatgpt-oauth foreign
   headers 防护，admission 代 `b92fd094`）——该代已在上个会话完成 reviewed
   deploy（live pin == HEAD pin 预检实证），本次 `-Apply` 对其字节恒等重投影，
   无代际轮换。
2. config.yaml 手术（`outputs/deploy-wb-cn-hy4flash-open-20261128.ps1`，
   DryRun 预演后 apply）：仅摘 `oauth.excluded-models.workbuddy-provider` 中
   `    - hy4flash-free` 一行（plugin 投影只追加不冲洗，陈旧项须手术摘除），
   同 inode 写入 + post-parse 断言（排除表 == manifest 13 项 / settings 含
   hy4flash-free / alias 桥不动 / diff_keys==["oauth"]）+ 核心容器重启。
   `DEPLOY_OK`，备份 `20261128T142710.047784626Z-wb-cn-hy4flash-open-20261128`。
3. 严格 doctor：`DOCTOR_CONTRACT_OK`、drift 14 文件全 MATCH、
   `MODEL_IDS_UNKNOWN=none`、`catalog_oauth_missing=none`、
   `workbuddy-excluded-workbuddy-provider=PRESENT n=13`、
   wb lane OPEN（hold=none）、INTL 保持 `disabled_closed_dark_20261117`、
   ban 信号 7d=0。收据 outputs/gates/doctor-wb-cn-hy4flash-20261128.log（本地）。

## 事务 refusal 与修正（三轮，全部 fail-closed 无状态损伤）

1. 首轮 apply：预检 `active_requests_not_drained`（CN lane 在途流量）→
   零改动退出。修正：预检加 120s 有界排空等待。
2. 二轮：手术+重启成功，契约探针 30s 窗内 `/v1/models` 全败 → 按设计
   `ROLLBACK_VERIFIED`（回到排除态）。根因：只等了 admission healthz（它不随
   容器重启），核心启动竞速。修正：契约重试 180s。
3. 三轮：契约仍 503 → 根因是**事务独占持有 admission 流量锁期间经 8318 代理
   探目录被自己的租约门挡住**（11-17 模板契约块只做模块断言不发代理请求）。
   修正：锁内直探核心 8317 契约（`CORE_CONTRACT_OK served_count=12`），
   释放流量锁后再补 8318 旁证（`PROXY_SPOT_OK`）。

## 单发验收（一发不重试）

- `hy4flash-free` → HTTP 200、`finish=stop`、上游回显 **`hy4-flash-free`**
  （已文档化映射 `hy4-flash-free <== hy4flash-free`，cpa-model-account-multi-channel.md）、
  content 2 字符 + reasoning 40、prompt 23 / completion 24 tokens →
  **LIVE_ACCEPTED**。
- credits 归因（受控窗：快照→单发→快照，2s 间隔）：9 账号 total_used/
  total_remain **零变动** → **ZERO_COST_CONFIRMED**。插件管理端点两次静默
  快照（3s）字节恒等，排除载荷易变字段假阳性；另观测到的一次 -11 差分落在
  无请求窗口，归因 CN lane 并发 deepseek 自然流量，与本模型无关。
- `/v1/models` 目录面 12 模型，hy4flash-free 在册；lane 计数器排空正常。

## 观察项（不阻塞）

- **2026-11-30 促销窗到期**：上游若重新计价，`-free` 名将失真——按退役铁律
  由用户裁决，不自动墓碑。
- CN 插件 active ping 本就以 `hy4flash-free` 为 ping 模型（每账号约 5 分钟），
  开放不新增探针面；cpa-health 对共享订阅 lane 不做矩阵探针，无新增常驻请求。

## 回滚入口

- config.yaml：备份 `20261128T142710.047784626Z-wb-cn-hy4flash-open-20261128`
  （恢复排除行 + 重启核心即回到关闭态）。
- 声明面：git revert `23412d0` 后再走一次 `-Apply`（排除表墓碑由
  plugin_exclusions 追加逻辑自动回填远端）。
