# 2026-09-15 BWG CPA 裸名目录网关拆分（luna=OAuth，sol/terra/astra=中转）

用户澄清网关语义后给出最终规格：第3网关 = r1 中转通道，只禁用 luna；
第4网关 = OAuth 通道，只开放 luna；客户端 key 的裸名目录里 luna 对应
第4网关、astra/sol/terra 对应第3网关。本变更取代同日上午的
`20260915-bwg-cpa-luna-disable.md`（全局禁用 luna）。

## 实现

- `config.yaml`：原 `prefix: r1` 活动条目加 `excluded-models: ["gpt-5.6-luna"]`
  （第3网关只禁 luna）；追加**无前缀** ai.input.im 条目（复用同一 key），
  声明 `models: [gpt-5.6-sol, gpt-5.6-terra, gpt-6-astra]`。无前缀条目注册
  裸名，`force-model-prefix: true` 下裸名只路由到无前缀凭据，串池防线不变。
- OAuth 凭据 `excluded_models` 回滚为原始 8 通配（第4网关=仅 luna）。
- 契约同步：`cpa-health.py` 裸目录集合 = {glm, luna, sol, terra, astra}、
  生成冒烟 = luna（OAuth 通道存活而中转站可能宕）；fixture `cpa-acceptance.py`
  镜像（codex-api-key 声明 4 模型 + glm）；guardrails 摘要打印
  `has_oauth_luna/has_relay_bare_sol/has_relay_bare_terra`；runbook 基线；
  `test_scripts.py`（33 passed / 68 subtests）。投影 sha `d64b2dc7…` 与本地一致。

## 结果

- 裸目录恰为预期 5 模型：`glm-5.3-flash`、`gpt-5.6-luna`、`gpt-5.6-sol`、
  `gpt-5.6-terra`、`gpt-6-astra`——声明式注册未泄漏中转站其它模型
  （无 5.5/spark/image）。总目录 16 = 5 裸名 + 11 个 `r1/*` 前缀
  （`r1/gpt-5.6-luna` 已消失）。
- readiness / generation 均 `HEALTH_OK`（luna 冒烟走 OAuth，约 2s 级）。
- 裸名 `gpt-5.6-sol` 探针不再瞬间 400 未找到，而是挂起等待上游至命令级超时
  ——路由成立；r1 站点自 2026-09-09 起故障（当日 408），sol/terra/astra 需
  站点恢复才真正出字，期间这些条目会表现为超时/5xx。
- strict doctor `DOCTOR_CONTRACT_OK`；`force-model-prefix: true`、公网 8443
  随机路径与认证契约全部保持。

## 追加（同日晚）：gpt-5.5 裸名别名到 GLM

用户要求裸名 `gpt-5.5` 也路由到 GLM 计划。zhipu-plan 条目 `models` 追加
`{name: glm-5.3-flash, alias: gpt-5.5}`（别名机制：客户端裸名 → 上游实际
模型 `glm-5.3-flash`）。备份 `/root/cpa-glm-alias-backup-20260915T133000Z/`。

- 裸目录基线更新为 6 模型（上表 + `gpt-5.5`）；`cpa-health.py` allowed 集
  与 fixture 镜像、guardrails 摘要（新增 `has_glm_alias_55`）、runbook 基线、
  单测同步（33 passed），投影 sha `06e7302f…` 与本地一致。
- 端到端验证：`gpt-5.5` 生成 HTTP 200，`responded_model=glm-5.3-flash`
  （未开 force-mapping，响应回显真实模型名），`finish=stop`，exact OK；
  readiness / generation `HEALTH_OK`；strict doctor `DOCTOR_CONTRACT_OK`。
- 已知特性：GLM 为推理模型，`max_tokens` 过小会被推理消耗（给 512+ 冗余）；
  桌面 "5.5" 条目现在走 GLM 计划配额，不再依赖中转站。

## 边界与回滚

- 备份 `/root/cpa-gateway-split-backup-20260915T131601Z/`（config.yaml、
  OAuth 凭据、前一版 cpa-health.py）；回滚 = 拷回并 `docker restart
  cli-proxy-api`。
- sol/terra/astra 因中转站暂态冷却从目录缺席时，`cpa-health.py` generation
  走 exit 10（更新器 defer，不回滚），目录恢复后自愈；这是既有设计。
- 如需 5.5 / 5.3-codex-spark 也裸名开放，向无前缀条目的 `models` 追加一行
  声明即可（当前按用户规格只声明 astra/sol/terra）。
- 完整 fixture 受控验收仍未重跑（机制与模型名无关），下次二进制/更新验收
  按更新后脚本重建。
