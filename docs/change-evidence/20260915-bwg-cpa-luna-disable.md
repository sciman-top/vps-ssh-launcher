# 2026-09-15 BWG CPA 裸目录禁用 luna 并开放其余 OAuth 模型

> 状态：已被同日晚间的网关拆分取代，见
> `20260915-bwg-cpa-gateway-model-split.md`（luna 恢复为 OAuth 通道唯一
> 开放模型，sol/terra/astra 改走无前缀中转条目）。本文保留作为过程记录。

用户报告客户端 key 只能调用 luna，并指令"第 3 网关禁用 luna，其它模型继续
开放"。第 3 网关即 cockpit 里的 fq.sciman.top CPA 公网网关。本次只处理 bwg，
不触碰 zz。过程中为清除一条已过期的滞留冷却记录，按 runbook 重启过一次
CPA 容器（秒级窗口）。

## 根因

- OAuth 凭据 JSON（`auth/codex-*.json`）的 `excluded_models` 字段（下划线，
  仓内旧文档误写为连字符）自 2026-09-08 起排除
  `codex-*`、`gpt-5.3-*`、`gpt-5.4*`、`gpt-5.5*`、`gpt-5.6-sol`、
  `gpt-5.6-terra`、`gpt-6-*`、`gpt-image-*`，仅放行 `gpt-5.6-luna`。
- ChatGPT desktop 的模型选择器是客户端内置列表（6 Astra/5.6 Sol/5.6
  Terra/5.6 Luna/5.5/5.2），不读取 `/v1/models`；其裸模型名只有
  `gpt-5.6-luna` 在网关裸目录在册，其余一律 400 `model_not_found`。
  `r1/*` 前缀模型与 `glm-5.3-flash` 从不在该选择器出现，属客户端限制。

## 变更

- `excluded_models` 翻转为 `["codex-*", "gpt-image-*", "gpt-5.6-luna"]`：
  内部 codex 变体与图片模型维持排除，luna 按指令禁用，其余聊天模型开放。
- 契约同步：`scripts/remote/cpa-health.py` 裸目录集合改为实际在册 6 模型、
  生成冒烟模型由 luna 改为 `gpt-5.6-sol`（terra 当时不稳定，sol 实测
  exact-OK）；fixture `cpa-acceptance.py` 目录镜像同步；guardrails apply 的
  目录摘要打印改为 `oauth_luna_absent/has_oauth_sol/has_oauth_terra`；
  `docs/runbooks/cpa-stale-cooldown-recovery.md` 基线更新；
  `test_scripts.py` 两处 health 用例同步（33 passed / 68 subtests）。

## 过程与结果

- 热加载一次轮询即生效（约 2 秒内），auth 翻转本身未重启容器。
- 翻转后裸目录为 6 模型；随后 codex 上游持续 `server_is_overloaded`
  （当日 docker 日志多次 502），terra 三次冒烟全失败并进入模型级暂态
  冷却，从 `/v1/models` 消失；`.cds` 记录 `next_retry_after` 过期后 terra
  仍不入册（符合上游 #5639/#5770 冷却滞留 bug 族的表现）。
- 按 `docs/runbooks/cpa-stale-cooldown-recovery.md` 执行：备份 `.cds` →
  停容器 → 删 `.cds` → 启容器；terra 立即重新入册。
- 最终裸目录：`glm-5.3-flash`、`gpt-5.3-codex-spark`、`gpt-5.5`、
  `gpt-5.6-sol`、`gpt-5.6-terra`、`gpt-6-astra`；`gpt-5.6-luna` 已消失；
  `r1/*` 12 个前缀模型不变。
- `gpt-5.6-sol` 冒烟 HTTP 200、`finish_reason=stop`、exact OK（共两次，
  含投影后 generation）；投影后 readiness 与 generation 均 `HEALTH_OK`。
- 收尾 strict doctor `DOCTOR_CONTRACT_OK`：容器 running、image digest 与
  pin 一致、timer success、公网路由契约全部保持。

## 边界与回滚

- 凭据变更前备份：`/root/cpa-auth-backup-20260915T115401Z/`（mode 700，
  位于 watched auth 目录之外）；回滚 = 拷回该 JSON 并等待热加载。
- 冷却记录备份：`/root/cpa-cds-backup-20260915T121509Z/`；回滚 = 拷回
  `auth/*.cds` 并重启容器。
- 健康检查对裸目录做严格集合比对：上游账号侧新增/更名模型、或任一模型
  处于暂态冷却缺席时，generation 报 exit 10（更新器 defer，不回滚）；
  冷却滞留不入册时按 runbook 个案处理，这是既有设计。
- fixture 受控验收（unshare 隔离 + 62 秒过载周期）本轮未重跑；其机制与
  模型名无关，下次二进制/更新验收时按更新后脚本重建 fixture。
- 客户端选择器中的 `5.2` 条目对应的模型不在 Plus 账号在册集合中，仍会
  400；luna 条目保留但调用按指令被拒。
- terra 在变更当晚会间仍间歇上游过载；模型已入册，上游恢复即可用。
