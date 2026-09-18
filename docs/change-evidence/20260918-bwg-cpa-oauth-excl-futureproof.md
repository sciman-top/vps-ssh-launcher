# BWG CPA 审查收口：OAuth 排除清单通配预加固 + 登出应急事务重写

- Scope: 远端 `config.yaml` 一次受保护写入（`oauth-excluded-models`）；仓库侧
  guardrails 事务重写与 README 注记。授权：用户 2026-09-18"按你的推荐/建议
  连续执行修复和优化，直至完成所有任务"（对应同日只读审查报告的
  P3-a/P3-b/P3-c/P2/P3-e 处置）。

## 变更

1. **oauth-excluded-models 预加固（P3-a，远端写入）**：在既有 9 项之上追加
   `codex-*`、`gpt-5.7*`、`gpt-6*` 通配（共 12 项），使上游新增 codex 内部
   模型与下一族 GPT 在 OAuth 侧 fail-closed，而不是以裸名泄漏进目录后由健康门
   每日 exit 20 暴露。清单只影响 OAuth/file 类凭据（v7.2.158 源码
   `service_models.go:530-541`），openai-compat 四条路由不受影响。
2. **`-DeactivateOAuthLuna` 事务重写（P3-b，仓库侧）**：原事务断言 r1 拓扑
   （ai.input.im 的 codex-api-key 前缀+裸名两条目）并编辑 config 把裸 luna
   改挂 r1；r1 已于 9/18 删除，事务重触发只会 REFUSE（fail-safe 但按钮失效）。
   重写为现拓扑：不编辑 config（裸 luna 唯一来源就是 OAuth 文件，删文件即
   离场；恢复 = runbook 设备流重登，受新通配清单约束仍只露 luna）；新增活跃
   OAuth 文件预检（≥1，含不可读拒绝）；目录复验断言反转为
   luna 缺席 + 其余 4 条裸路由存活；末尾标记
   `BARE_LUNA_ROUTE=oauth_removed`、`HEALTH_NOTE=generation_defers_exit10_until_reenroll`。
   保留测试钉定的约束（显式开关后、不归档 auth 目录、payload 过 bash -n）。
3. **README 注记（P2/P3-c）**：relay-8003 上游为明文 `http://`（CPA 无上游
   TLS 配置面），sol/terra 定位非敏感备用通道；客户端对策（非流式、超时
   ≥120s、退避 ≥30–60s）；排除清单通配加固说明。
4. **403 分类（P3-e，只读）**：24h 内 18 个 upstream-403 全部来自单一外部 IP
   （阿里云段）、`auth_status=200`（有效密钥）、成对出现、约 15–18 分钟节律
   （05:57–06:45Z 窗口）——自有二级客户端打到某个上游拒绝的路由，非攻击、
   非网关故障；保留错误文件已被 10 文件上限轮转，端点不可进一步定位。
   401 全部为 loopback doctor 自探。无配置动作。

## 执行与验证（远端）

- 首批（14:44Z 附近）**ABORT 并干净回滚**：校验脚本把未加引号的
  `$ALLOWED` 展开为 5 个位置参数、python 仅取第一个词作允许集，96s 收敛断言
  必然失败触发设计内回滚（还原 config + 重启 + models 200 确认）。回滚后
  sha 复核等于变更前 `5e348b5b…`，证明无部分写入。
- 次批（14:47:51Z）成功：`OEM_UPDATED total=12`；config sha
  `5e348b5b… → afa172b2…`；备份 `/root/cpa-oauth-excl-futureproof-20260918T144751Z`
  （700，含变更前 config）；容器重启后裸目录严格等于 5 集合
  {glm-5.3-flash, gpt-5.6-luna, gpt-5.6-sol, gpt-5.6-terra, deepseek-flash}；
  单次 luna 生成 `GENERATION result=HEALTH_OK`（OAuth 链路端到端）。
- 终验 doctor：`DOCTOR_CONTRACT_OK`（exit 0），auth-permissions=OK、
  gateway-transport=OK、luna_state=available、镜像 digest 不变
  （v7.3.7@sha256:4ce7b1b5…）、RestartCount=0。
- 仓库门禁：`git diff --check` 干净；`pytest test_scripts.py` 35 passed /
  96 subtests。

## 回滚

- 远端：`cp /root/cpa-oauth-excl-futureproof-20260918T144751Z/config.yaml
  /opt/cliproxyapi/config.yaml && docker restart cli-proxy-api`（恢复 9 项
  清单；健康门与目录契约均与清单值无关，回滚无目录风险）。
- 仓库：revert 本切片提交；`-DeactivateOAuthLuna` 事务无远端驻留物。

## 边界

- 排除清单是"消极过滤"：若上游上线清单外的新命名族，健康门 5 集合契约会以
  exit 20 暴露（只暂缓更新，不回滚、不影响在途路由）；处置 = 加一条通配 +
  重启。上游 #5943（per-account excluded_models 启动竞态修复）未发版，
  列为重启窗口观察项（本次重启实测无泄漏窗）。
- relay 明文 HTTP 属外部约束，本地无修复面；已按"非敏感备用通道"定位记录，
  是否要站主提供 https 或弃用该通道由用户决定。
- 遗留 user-side：二级客户端（阿里云 IP）403 路由的自查；provider 侧 OAuth
  会话吊销仍未做。
