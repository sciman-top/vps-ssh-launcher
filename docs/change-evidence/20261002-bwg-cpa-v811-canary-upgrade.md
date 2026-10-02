# 2026-10-02 BWG CPA v8.0.11 手动 canary 升级证据

## 目标与范围

- 目标：将 `bwg` 的 CPA 从 v8.0.10 升级到 v8.0.11（用户当次授权；release
  发布于 2026-10-02 14:48 UTC、Docker tag 14:50 UTC，未满 updater 的 72h
  成熟期，走手动 canary；与 v8.0.10 升级同一日、同一套流程）。
- 范围：仅 `compose.yml` 的镜像声明；`config.yaml`、OAuth 凭据、Nginx、
  admission、路由清单零改动。不触碰 `zz`，不轮换 key，不新增上游重试。

## 上游变更评估（v8.0.10...v8.0.11，共 7 个 commit）

- `5ec3144` `fix(gemini)`：responses 翻译处理拆分 usage metadata 与 max
  tokens。本仓无 Gemini lane，语义上不触达。
- `30aa1f1` `test(executor)`：transport 测试的连接复用边界放宽，仅测试。
- `8fbf152` `fix(executor)`：service tier 存在时保留 responses 顶层 token
  usage——OAuth `/v1/responses` 计量面正向修复，升级后真实流量复核正常。
- `d7c3c0a` `feat(antigravity)`：上游连接复用 debug 日志（PR #6285）。
  未使用该 provider。
- `52d5507` `feat(config)`：新增 shared upstream provider settings 配置。
- `3be5fa4` `fix(config)`：支持历史 v8 配置别名（读取路径兼容）。
- `e2bff01`：merge #6285。
- 无破坏性变更；release 范围内无 OAuth/chatgpt 后端行为改动。

**风险决策**：不写入任何新配置段——远端 `config.yaml` 预检无 shared
upstream 设置且升级前后逐字节未动（本次升级不修改 config），`52d5507`
新特性保持 opt-in 不开启；`3be5fa4` 属读取兼容修复，无需配置动作。

## Fixture 模拟验收：抓到一次目录契约漂移（非二进制问题），吸收后 PASS

首轮 fixture（v8.0.11 实体二进制 sha256 前缀 `a92ad7b1d0397e33`，digest
预核拉取，mount+net namespace 生产零接触）在语义三场景
（overload/cooldown/62s recovered）**全过后**，`cpa-health.py generation`
实体验收返回 exit 10——v8.0.10 同场景 4 小时前是 HEALTH_OK。

诊断链（一次性诊断探针，生产零接触）：

1. 失败确定性复现：pre-state 重试 16 连败，目录 bare_diag 全程完整、
   sol 直探全程 200。
2. 用 CPA 同款请求形态直打：4 组 chat/completions
   （glm-5.3-flash ×2 max_tokens、glm-5.3、deepseek-flash）全部 200
   且响应体逐字段正确；再用 cpa-health 自身的 `_loopback_request` 机制
   打 catalog（10 ID）与 generation body（完整合法 JSON、无 error 字段）
   均成功——**二进制请求路径无任何异常**。
3. 给 cpa-health 副本打 exit-10 分支标记后复跑：
   `MARK: catalog_incomplete required_minus_ids=['gpt-5.6-terra']
   n_ids=10 n_allowed=13 n_required=9`——exit 10 来自目录完整性门
   （required 集缺 terra），不是生成请求。
4. 根因：并行会话今日目录变更 `99cf814`（槽位 3 http-bridge-8003 增补
   `gpt-5.6-terra` 裸名路由）已落清单+投影+生产（config 与
   cpa_provider_routes.json 均为 16:02:5xZ 同刻写入、容器同刻重启），
   但 `cpa-acceptance.py` 的合成 fixture config 未同步 terra——
   required 含 terra 而 fixture 目录无 terra，门如实失败。

**处置（验证吸收）**：`scripts/remote/cpa-acceptance.py` 的
http-bridge-8003 fixture 条目按 99cf814 的声明镜像补入
`{"name": "gpt-5.6-terra", "alias": "gpt-5.6-terra"}`（唯二改动之一，
随本证据文档同 commit 入库）。修正后 fixture 全量重跑：
目录契约（含墓碑 fail-closed）→ overload → cooldown → 62s recovered →
generation 实体 HEALTH_OK，updater 四场景（start_fail 回滚 /
model_exposure 回滚 / transient UNVERIFIED / success 0）全符合，
`ACCEPTANCE_RESULT=PASS`、`ACCEPTANCE_EXIT=0`、`CLEANUP_OK`；
bare_diag 11 ID 含 terra。转录 `outputs/cpa-v811-fixture-transcript.txt`
（不入库），诊断探针 `outputs/cpa-v811-probe2*.sh`、
`outputs/cpa-health-debug.py` 留 outputs 备查。

## 前置波折：canary 前置门正确 DEFER 一次（同款已知模式）

- 首次启动（19:05:44 UTC）前置 generation 门 exit 10 DEFER，镜像未动。
- 归因：与 v8.0.10 升级时同款 ai.input.im 抖动——18:54-18:57 UTC 上游
  对 `gpt-6.1-sol` 生成请求返回 502 `"Upstream access forbidden"`
  （容器日志同期有真实客户端 ~1/min 重试在撞它），CPA 将
  `gpt-6.1-sol-input` 从目录视图摘除；required 集含该非 optional 别名，
  门在目录完整性检查即返回（无生成流量）。
- 直探上游 `/v1/models`：13 模型、`gpt-6.1-sol` 仍在=视图滞后；
  `docker restart` 重注册后目录 13 ID 全在册，19:07:25Z 重跑 canary。

## 升级事务

事务脚本与 v8.0.10 同构（复刻 auto-update.sh 语义 + compose 现值精确钉
校验 `v8.0.10@0b007a6a`，钉不匹配即 REFUSE）：flock 互斥 → error-dumps
权限收紧 → 前置 generation 门（glm-5.3-flash，GLM lane，零 OAuth 配额）
→ 备份健康（700 / ≥2 GiB）→ 备份 compose.yml → digest-pinned pull
（`v8.0.11@sha256:1d7f8c15...`，Docker Hub tag 元数据预核）→ 原子替换
（计数 ≠1 即 REFUSE）→ `compose config --quiet && up -d --pull never`
→ 升级后 generation 验收 → 失败自动回滚。

- 执行：base64 落盘 `/root/cpa-canary-v811.sh`（bash -n 通过），
  setsid 后台执行；完成后临时脚本与输出已删，
  `/opt/cliproxyapi/manual-canary-v811.log` 留审计。
- 结果：`19:07:25Z CANARY_START` → `19:07:39Z OK: updated v8.0.10 ->
  v8.0.11 digest=sha256:1d7f8c154a9804ba33c5332bf76cdb3a05791d6fd275ccad8f2a63859ab25df9
  backup=/opt/cliproxyapi/backups/20261002T190735.904246049Z-from-v8.0.10`；
  前置与升级后 generation 均 HEALTH_OK，14 秒完成，回滚路径未触发。
- 清理：不 prune；v8.0.10 镜像保留为本地回滚集。

## 验证与风控复核

- strict doctor：升级前基线与升级后复核均 `DOCTOR_CONTRACT_OK`。
- `==container==`：`running restart=0`，
  `image=eceasy/cli-proxy-api:v8.0.11@sha256:1d7f8c15...`。
- `==client-model-catalog==`：13 个登记 ID 全部在册
  （含槽位 3 `gpt-5.6-terra` 与槽位 1 `gpt-6.1-sol-input`），
  `MODEL_IDS_UNKNOWN=none`。
- `==oauth-monitor==`：`oauth_monitor=OK`、`oauth_days_left=3`；
  `==cooldown-state==`：`cooldown_next_retry_after=none`、
  `catalog_oauth_missing=none`、`luna_state=available`——本次重启后
  OAuth 三别名全部在册（下午塌缩的 `gpt-5.6-luna`/`gpt-6.1-sol` 已随
  重注册回归，ai.input.im 抖动窗口的 sol-input 亦回归）。
- `==projection-drift==` 9×MATCH。
- updater 自洽（实跑 `--check`，只读）：`CANDIDATE current=v8.0.11
  target=v8.0.11 soak=72h`——次日 timer 仅 readiness，无动作无回滚。
- 升级窗口流量：8 分钟窗口零 5xx/429；`8fbf152`（responses usage 保留）
  与 `cf3102c` 翻译面由后续真实桌面流量持续观察，无需专项探针。

## 边界与后续

- 本次升级不构成 provider 侧风控/配额变化的证明；账号级判据仍以
  403/quota 响应与 OAuth 刷新失败为准。
- `52d5507` shared upstream provider settings 与 v8.0.9 的
  `client.codex` 段同样保持不写入；如需启用按目录变更清单单独评审。
- ai.input.im 的 502 permission 抖动当日已两次摘除 `gpt-6.1-sol-input`
  目录视图（13:30 与 18:54 UTC），每次都以 restart 重注册恢复；该上游
  的稳定性 watch item 持续，若频发可评估把 sol-input 调整为 optional
  （属目录变更，须走清单流程，不在本切片）。
- 既有待办不变：`gpt-5.6-luna` 生成级 LIVE_ACCEPTED 待 ≈10/4 傍晚 Plus
  周限额重置后单发补验（本轮 doctor 读数 `luna_state=available`）。
- 并行会话协同注记：本切片吸收了其目录切片（99cf814）遗漏的 fixture
  harness 同步（cpa-acceptance.py terra 行）；工作区另有其未提交改动
  （pyproject.toml、run_gates.ps1、conftest.py），本切片未触碰。
