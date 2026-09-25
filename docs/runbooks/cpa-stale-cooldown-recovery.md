# CPA 陈旧冷却恢复（bwg）

本 runbook 处理 bwg 上 CPA 凭据已过冷却窗口、配额应已恢复，但模型仍从
`/v1/models` 目录缺席的情况；不处理真实配额未恢复、凭据失效、网络故障或
本地契约失败（`LOCAL_CONTRACT_FAILED`）。健康状态分为四类：
`HEALTH_OK`、`UPSTREAM_UNAVAILABLE`、`RELAY_DEGRADED`（历史标签，当前指
ai.input.im）、
`LOCAL_CONTRACT_FAILED`。

## 背景机制

- 上游存在冷却状态陈旧问题：[#5639](https://github.com/router-for-me/CLIProxyAPI/issues/5639)
  （codex `usage_limit_reached` 后模型冷却不复评）与
  [#5770](https://github.com/router-for-me/CLIProxyAPI/issues/5770)
  （配额恢复后冷却滞留约 18 天）。
- 上游源码核对（v7.2.158 参考 checkout）：非配额瞬态冷却写入注册表投影时
  不带过期时间戳，重投影只由下一次请求结果、token 刷新或 auth/config 重载
  触发；截至 v7.3.4 未修复（v7.3.x 仅新增"传输层失败不冷却凭据"，减少假
  冷却来源，不改变滞留本身）。
- 2026-09-16 起本部署 `save-cooldown-status: false`：冷却为纯内存态，容器
  重启即清除，`auth/*.cds` 不再写入（doctor `cooldown_state` 恒为 `none` 属
  预期）；上游 issue 中"重启即恢复"的说法在当前状态下直接成立。
- 历史状态（`save-cooldown-status: true`，2026-09-08～09-16）下冷却持久化为
  `auth/*.cds`、随容器重启保留；下方 `.cds` 处置流程仅适用于该状态或残留文件。
- 只有一个 OAuth 凭据时，凭据级冷却等于该凭据全部在册模型一起变暗。
  启动后约一个 `transient-error-cooldown-seconds` 窗口内的目录断言必须
  容忍合法瞬态冷却，超过一个窗口仍缺席才按本页处理。

## 识别

- 优先看 `scripts/cpa_bwg_guardrails.ps1 -Profile bwg` 的
  `==cooldown-state==`：只有 `luna_state=stale_cooldown_suspected`（冷却已过期，
  Luna 仍从目录缺席）才进入本页。`active_cooldown` 是正常退避，必须等待，不能
  清除；`unavailable_unclassified` 先按上游或本地目录故障处理。
- 2026-09-16 起持久化关闭，`.cds` 数据源消失，`stale_cooldown_suspected` 不再
  出现；滞留的实用判据改为：一次 `docker restart cli-proxy-api`（清空内存冷却）
  并等满一个瞬态冷却窗口后 Luna 仍从目录缺席——此时已非冷却滞留，转上游或
  本地目录故障排查。
- `doctor` 的 `==timer-result==` 段或 `/opt/cliproxyapi/auto-update.log` 出现
  `UNVERIFIED: upstream unavailable`（更新器 exit 10），且持续超过一个
  上游配额窗口（通常一周）。
- bare 目录较基线塌缩或缺失在册模型。目录基线不再用固定数量口径：唯一事实源是
  `scripts/remote/cpa_provider_routes.json` 的声明（`providers` 全部 alias 构成存活
  集合，`optional_models` 可缺席，`oauth_routes` 别名由 OAuth lane 提供）；运行时
  读数以 strict doctor `==client-model-catalog==` 的 `MODEL_IDS=` 为准，未知 ID 一律
  异常。本文旧版“五项/三项 bare 基线”口径已作废，不得作为判据；已退役 ID
  （`r1/*`、已退役的 `glm-5.3-flashx`）重新出现即为异常。
- `readiness` 仍 `HEALTH_OK` 而 `generation` 返回 `UPSTREAM_UNAVAILABLE`：
  本地契约未坏，属上游侧缺席。

## 最小诊断

```bash
./.venv/Scripts/python.exe ssh_tool.py --profile bwg run --command \
  'ls -la /opt/cliproxyapi/auth/*.cds 2>/dev/null; python3 /opt/cliproxyapi/cpa-health.py readiness; python3 /opt/cliproxyapi/cpa-health.py generation'
```

当前默认无 `.cds`（`ls` 为空即符合预期）；若存在（历史/回退状态），其 mtime 应
能与配额事件时间对应，只处理与故障通道对应的文件，不确定时先记录文件名与
mtime 再继续。health 只输出四类状态字符串，不回显响应正文。

## 恢复（逐条执行，人工个案）

1. 备份目标 `.cds`：
   `mkdir -m 700 -p /root/cpa-cds-backup-<UTC> && cp -a /opt/cliproxyapi/auth/<file>.cds /root/cpa-cds-backup-<UTC>/`
2. 当前默认（持久化关闭）：只允许一次 `docker restart cli-proxy-api` 清除全部内存冷却；
   随后等待完整瞬态冷却窗口再做一次 generation 复验。不得在 `408/429/503` 后循环重启或
   自动重复 generation 请求。
   仅当 `save-cooldown-status: true`（历史/回退状态）才需要先停容器再删文件
   （运行中删除可能被内存态回写）：
   `docker stop cli-proxy-api && rm /opt/cliproxyapi/auth/<file>.cds && docker start cli-proxy-api`
3. 复验：公网目录恢复到与 `cpa_provider_routes.json` allowed 清单一致的基线
   （用 doctor `MODEL_IDS=` 比对，不得引用本文旧版的固定数量口径），
   `cpa-health.py generation` 返回
   `HEALTH_OK`、容器 `running` 且 restart 计数未增长、strict doctor 通过。

## 回滚与边界

- 把备份的 `.cds` 拷回 `auth/` 并重启容器即完全回滚；本流程不触碰
  auth JSON、config、Nginx 与更新器。
- 恢复期间通道中断，安排在无使用依赖的时段；不自动化、不批量执行。
- 若 `generation` 返回 `LOCAL_CONTRACT_FAILED`（20），先运行
  `scripts/cpa_bwg_guardrails.ps1 -Profile bwg` 定位本地契约，再回本页。
