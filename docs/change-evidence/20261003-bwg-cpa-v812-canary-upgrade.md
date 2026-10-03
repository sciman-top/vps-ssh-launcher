# 2026-10-03 BWG CPA v8.0.12 手动 canary 升级证据（含槽位1 sol-input optional 化）

## 目标与范围

- 目标：将 `bwg` 的 CPA 从 v8.0.11 升级到 v8.0.12（release 2026-10-02
  20:21 UTC，未满 72h 成熟期，走手动 canary）。
- 伴随切片：升级前置门被 ai.input.im 第三次慢性 502 窗口卡住约 3.5h 后，
  按用户指令将 `gpt-6.1-sol-input` 转为 optional（目录变更，走清单全流程，
  commit `d983c37`），随后 canary 一次通过。
- 范围：canary 仅 `compose.yml` 镜像声明；optional 化仅 routes manifest 的
  slot-1 `optional_models`。`config.yaml`、OAuth 凭据、Nginx、admission、
  排除清单零改动。不触碰 `zz`，不轮换 key。

## 上游变更评估（v8.0.11...v8.0.12，共 7 个 commit）

- `8348923` `feat(pluginhost)`：host routing reset cooldown 回调。
- `d306f2c` `fix(codex)`：**模板原生 freeform apply_patch 默认保留**——
  无 `client.codex` 段（本仓状态）时，原生声明 freeform apply_patch 的
  模板模型（如 gpt-6.1-sol）此前被清除 `apply_patch_tool_type`，现在按
  上游模板保留通告。属保真修复（客户端本就自带该 tool call），非新能力
  注入；`enable-apply-patch` 配置语义不变（false=仅原生模板模型通告）。
  本仓维持不写 `client:` 段。
- `6d57ac9`+`e6f6f26` `fix(claude)`：cloaked reminder 会话日期钉住
  （防 prompt cache 失效）+ `pause_turn` stop reason 翻译。无 Claude lane，
  不触达。
- `c163bae` `fix(auth)`：**保存终态 unauthorized 并阻止不安全刷新重试**
  ——对单 Plus OAuth 账号的风控面是正向加固（死凭据不再反复刷新撞上游）。
- `0fb50a1` `feat(management)`：v8 management API 向 api-keys 配置注入
  auth index。远端管理已源码级禁用（allow-remote=false），无影响面。
- `2044a01` `fix(codex)`：responses 请求 include 保留 web search sources。
- 无破坏性变更、无新增必配项。

## Fixture 模拟验收（生产零接触）

v8.0.12 实体二进制（sha256 前缀 `cae12c665a8b45e9`，digest 预核拉取）在
mount+net namespace 内：目录契约（含墓碑 fail-closed）→ overload →
cooldown → 62s recovered → generation 实体 HEALTH_OK，updater 四场景
（start_fail 回滚 / model_exposure 回滚 / transient UNVERIFIED /
success 0）全符合，`ACCEPTANCE_RESULT=PASS`、`CLEANUP_OK`。转录
`outputs/cpa-v812-fixture-transcript.txt`（不入库）。
harness 即 v8.0.11 轮补齐 terra 后的版本，零派生直接复用。

## 前置阻塞与目录切片：槽位1 sol-input 转 optional（d983c37）

- 06:03 UTC 预检发现目录缺 `gpt-6.1-sol-input`（required）：ai.input.im
  上游对 `gpt-6.1-sol` 生成请求持续 502 `"Upstream access forbidden"`
  （/models 仍列名=粘性模型态视图摘除）。与前两次不同，本次窗口慢性
  （06:03Z 起持续 3.5h+，历史两次为 44min/13min），restart 不恢复
  （上游仍在失败，重注册即再摘除）。
- 处置演进：先按既有 playbook 等待窗口（远端循环每 3-5 分钟单发
  8-token 探测，恢复即 restart+canary，40+24 次尝试全 502）；用户明确
  指令"上游本身不可用，不应继续等待"后，转目录原生解法——按清单将
  sol-input 转 optional。
- 清单全流程（铁律）：manifest slot-1 `optional_models` 增补
  `gpt-6.1-sol-input`（模型仍声明，上游恢复后重启重注册即回归服务；
  仅健康门不再把上游可用性当本地契约，与 OAuth 别名既有 optional 语义
  一致）→ 同步 `test_scripts.py` 字面量 → `cpa-gateway.md` 变更记录。
  预言机三字面量与 guardrails lanes 字典经核对不受影响（声明列表/lane
  成员均未变）；doctor 的 catalog_summary 为纯观测无断言。
- **full gates**：`run_gates.ps1` 全绿（282 passed + 1 skipped +
  305 subtests；首轮 lint:format 抓到我与并行会话遗留的两处格式问题，
  ruff format 修正后通过）。
- commit `d983c37` → `-Apply`（`GUARDRAILS_APPLIED`、`READY_STATUS=200`、
  备份 `/root/cpa-guardrails-backup-20261003T091020.679405568Z`）→
  doctor 全绿（9×MATCH、`DOCTOR_CONTRACT_OK`）。

## 升级事务

与 v8.0.11 同构（复刻 auto-update.sh 语义 + compose 现值精确钉校验
`v8.0.11@1d7f8c15`）：flock 互斥 → error-dumps 收权 → 前置 generation
门（glm-5.3-flash，GLM lane）→ 备份健康 → 备份 compose → digest-pinned
pull（`v8.0.12@sha256:f2f1ee7a...`）→ 原子替换 → up -d → 升级后
generation 验收 → 失败自动回滚。

- 结果：`09:11:12Z CANARY_START` → `09:11:17Z OK: updated v8.0.11 ->
  v8.0.12 digest=sha256:f2f1ee7a3cd18f49b8e4ba13611b86b9f9069122eff9fb182912996964aa945d
  backup=/opt/cliproxyapi/backups/20261003T091114.167338393Z-from-v8.0.11`；
  前置与升级后 generation 均 HEALTH_OK，5 秒完成，回滚未触发。
  optional 化后目录门不再被上游窗口卡死。
- 清理：不 prune；v8.0.11 镜像保留为本地回滚集；远端临时脚本与窗口
  循环已清理，`manual-canary-v812.log` 留审计。

## 验证与风控复核

- post doctor：`DOCTOR_CONTRACT_OK`、v8.0.12@f2f1ee7a restart=0、
  **13 ID 全在册（`gpt-6.1-sol-input` 已随 09:10 apply 重启回归目录）**、
  `MODEL_IDS_UNKNOWN=none`、9×投影 MATCH、`luna_state=available`、
  cooldown none。
- `oauth_monitor=WARN_RENEWAL_WINDOW`（days_left=3 进入 ≤72h WARN 带）：
  预期节奏，lead24h 自动刷新窗口 ~10/4 开启（刷新链路 0 失败），非失败。
- updater 自洽（实跑 `--check`）：`CANDIDATE current=v8.0.12
  target=v8.0.12 soak=72h`。
- 实战流量：升级后真实 `/v1/responses`（17.8s 流式）与
  `/v1/chat/completions` 200，升级窗口零 5xx/429——`d306f2c`（apply_patch
  保留）与 `2044a01`（web search sources）在 OAuth lane 的首个真实样本
  正常。

## 边界与后续

- `gpt-6.1-sol-input` optional 语义：上游恢复时它正常出现在目录并服务；
  上游再入 502 窗口时目录摘除不再卡健康门。若上游侧长期不恢复，退役
  与否按目录清单另行评审。
- `oauth_monitor` WARN 窗口：~10/4 lead24h 自动刷新；10/4 傍晚既有待办
  （5.6-luna 生成级 LIVE_ACCEPTED）不变。
- 并行会话协同：其 10/3 晨间提交（342067c 门禁挂起修复等）与本切片
  无冲突；本切片 commit 前的工作区为其提交后的干净态。
