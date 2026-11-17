# 2026-11-17 BWG CPA：WorkBuddy INTL 车道关闭暗置 + hy4flash-free 退役删除

## 触发与决策（用户指令，同日第二批）

- **`hy4flash-free`（x0.00 免费档）退役删除**：真源声明全撤（manifest pin、oauth.settings、admission
  lane models、`zero_cost_models`、context overrides slug）+ 部署侧 `oauth-excluded-models` 墓碑 +
  doctor `client-model-catalog` 门 fail-closed 拦截复现。
- **`deepseek-v4.1-flash-sg` / `deepseek-v4.1-flash-intl` 保留但关闭暗置**：manifest pin/桥/admission
  lane/状态文件/预言机**全部原样保留**（重启用零重建），关闭机制 =
  `plugins.configs.workbuddy-ai-provider.enabled: false`（插件卸载）+ intl settings 定义摘除（core
  不再认识两名 → 本地 400 拒识）。
- 判否路径（重要教训）：**"经 oauth-excluded-models 关闭"不成立**——排除表是 pin 的补投影，
  `-Apply` 会自动摘除 pin 内来源的精确排除（"stale retirement" 清理）；首轮手插 sg 排除被当批
  -Apply 冲洗（n=23 无 sg，sg/intl 复活并漏上游）。结构自洽的关闭态只有插件禁用。

## 仓库落点（full gates `-RunParallel` 三轮全绿，均 1722 passed）

- `b6bd168`：退役面（manifest pin 回 [sg]、admission.json models [intl,sg] + zero_cost 摘除、
  doctor/apply 期望表、expectations、8 个测试文件、README+runbook、context overrides 摘 slug）。
- `df7d8f2`：doctor `workbuddy-intl-plugin` 门翻转（钉 `disabled` + `closed_dark_20261117`；
  enabled while closed = drift）+ 测试夹具。
- `f01c989`：`workbuddy-version` 门翻转——closed-dark 健康态 = **仅 CN 注册**；INTL 注册行 =
  drift（`UNEXPECTED_REGISTRATION`），缺席 = `intl:disabled_closed_dark_20261117`。
- `0581eeb`：intl settings 通道整键移除（policy 要求通道非空，空列表也拒；`EXPECTED_OAUTH_SETTINGS`
  运行时从 manifest 派生，键移除即自动放行）——验收探针显示保留定义会让 core 仍认识两名并尝试路由。
- 验收探针/事务：`outputs/deploy-wb-intl-close-retire-20261117.ps1`（-Apply 冲洗教训的事务）、
  `deploy-wb-intl-disable-20261117.ps1`（插件禁用）、`deploy-wb-intl-settings-drop-20261117.ps1`
  （settings 摘除）、`verify-wb-intl-close-acceptance-20261117.ps1`。
- `cpa-admission.py` 全程一字未动（pin 保持 `fcf95564` 代）。

## 远端事务与验证

1. `deploy-wb-intl-close-retire-20261117.ps1`：排除表 +2（sg、hy4flash-free）+ settings hy4flash-free
   条目摘除 + admission.json/routes 同换重启 → `WB_CLOSE_20261117_CONTRACT_OK`。**随后被 -Apply 的
   派生冲洗（见判否路径）**——排除表中 hy4flash-free（已不在 pin）留存为墓碑 ✓，sg 被摘 ✗。
2. `deploy-wb-intl-disable-20261117.ps1`：`enabled: true→false` 同 inode 手术（diff-scope 断言仅
   plugins 键）+ 容器重启 + **插件不注册验证**（INTL 注册行=0、CN=1）+ 目录面即时核验（13 模型、
   三名缺席）→ `DEPLOY_OK`。
3. `deploy-wb-intl-settings-drop-20261117.ps1`：`oauth.settings.workbuddy-ai-provider: []` 手术（锚定
   `oauth:` 的 `settings:` 父块——该通道键在 plugins/excluded-models/model-alias 下同名并存）+
   routes 由后续 -Apply 投影 → `SETTINGS_DROP_OK` + `DEPLOY_OK`。
4. `cpa_bwg_guardrails.ps1 -Apply` ×2：17 项 `PROJECTION_HASH_VERIFIED`、`READY_STATUS=200`、
   `GUARDRAILS_APPLIED`（policy semantic 门对新 manifest 放行）。
5. 严格 doctor：**`DOCTOR_CONTRACT_OK`**、`workbuddy-version=cn:0.21.27 intl:disabled_closed_dark_20261117`、
   `workbuddy-intl-plugin=disabled keepalive=off note=closed_dark_20261117`、
   `workbuddy-alias-bridge=OK intl-only`、`MODEL_IDS_UNKNOWN=none`、wb 两 lane OPEN（INTL lane 为
   休眠态：声明在、无流量）。
6. 单发验收（一发不重试）：
   - `hy4flash-free` → **400 `model_not_found`**（本地拒识）；
   - `deepseek-v4.1-flash-intl` / `-sg` → core 本地 400（**journal 权威**：`upstream_header_ms=2`、
     `usage_*=na`、`sse_bytes=0`——2ms 本地拒，零上游触达、零凭据信号；200 信封是 core 统一错误
     包装形态非透传）；
   - `/v1/models`：13 模型，三名缺席，CN 裸名 + oc 在册；
   - CN 车道自然流量不受影响（CN 插件注册行在、lane OPEN）。

**ACCEPTANCE_RESULT=PASS**

## 手筋（后续必读）

1. **排除表 = pin 补投影**：pin 内来源的精确排除会被 -Apply 自动摘除（"声明保留但排除关闭"在该
   管线不成立）；关闭暗置的唯一结构自洽机制 = 插件禁用 + settings 定义摘除。
2. **同名通道键四处并存**：`workbuddy-ai-provider:` 在 plugins.configs / oauth.excluded-models /
   oauth.settings / oauth.model-alias 下都有——config.yaml 行级手术必须先锚定顶层父块
   （plugins:/oauth:）再搜子键，全文件首匹配必错位（本轮第 1/2 次 REFUSE 根因）。
3. **config.yaml 混合行尾**：plugins 块是旧写入方的 CRLF 区（oauth 块是 11-17 新 LF 区）——行级
   等值比较必须 `rstrip("\r\n")`，写回用子串替换保留原行尾。
4. **routes 文件不要手换**：`-Apply` 逐字节投影它且漂移门锚 HEAD blob；手换会被 `REFUSE
   routes_baseline_drift` 拒（-Apply 投影产物 SHA ≠ 仓内 LF 归一化 SHA）。
5. policy `oauth.settings` 通道**必须非空**——摘定义要整键移除（运行时派生的 EXPECTED 自动跟随）。

## 重启用手筋（未来裁决后）

`plugins.configs.workbuddy-ai-provider.enabled: true` + config reload（或走一次 -Apply），sg/intl
两名即回归（pin/桥/lane/状态文件/预言机全部原样在位）；如需恢复免费档须重建 hy4flash-free 声明
（已退役，doctor 目录门 fail-closed 拦截，按退役铁律须用户主动指令）。
