# 2026-10-18 BWG CPA 风控评审收口（substitution 遥测口径 + 退役同步投影）

## 授权与范围

- 用户指令："按你的推荐/建议自动执行修复和优化，直至完成所有任务"，对应
  2026-10-18 多 lane 风控评审（会话内只读审查）所列 AI 推荐项与在途收口。
- 本次事务三部分：
  1. 吸收并行会话已推送的仓侧修复（`c894a02` opencode.ai 退役的策略同步、
     `a1949ee` 目录契约 plugin_routes 并集、`4fb16f0` readiness 插件模型
     allowed-optional），执行其留档的"-Apply 待维护窗口"收口；
  2. doctor `==model-substitution==` 按对拆分与 v8.0.3x 每请求 warn 口径修正
     （`9b22c77`，本会话实现）；
  3. triage 工具与事故 runbook 信号词汇增补（同 `9b22c77`）。
- BWG only；未访问 ZZ，未改 OAuth 凭据、capability path、Nginx/Fail2ban、
  config.yaml 与 admission 冷却状态。

## 上游核实结论（只读，GitHub API，2026-10-18）

- **PR #6649（antigravity 身份句触发 gemini-3-flash 429 的剥离修复）未合并**
  （state=closed、merged_at=null、base=dev）：该修复不在 v8.0.32/33 任何
  release。本部署 antigravity lane 单模型钉定 `gemini-3.8-flash-high` 且
  `gemini-3-flash` 在排除墓碑，实际暴露≈无；若未来放开 gemini-3-flash 需先
  复核该 PR 状态。
- v8.0.33（2026-10-18 发布）内容盘点完成：#6691 OAuth 凭据排除优先级修复、
  插件模型排除保留、Claude 短 Retry-After 每分钟冷却收窄、antigravity schema
  清洗线性化（OOM 修复）、SSE 订阅遥测过滤、陈旧快照防护、流截断检测——为
  updater 72h soak（~2026-10-21 到期）的评估输入。
- 另核 #6742（v8.0.31 Codex OAuth 凭据 ~7 天周期性失效）持续 watch；额度新政
  2026-10-29（OpenAI）与 Anthropic 新版 Usage Policy/Supported Regions 生效日
  2026-11-12 为后续复核触发点。

## 部署经过与保护

1. 仓侧改动 `9b22c77`（doctor/triage/runbook/测试）经 full gates 全绿
   （658 passed + 1 skipped + 317 subtests；compileall/bandit/ruff check/
   format/mypy 全过）后推送（`4fb16f0..9b22c77`），push 后 post-push
   fetch/status 确认 main==origin。
2. `-Apply` 投影：备份
   `/root/cpa-guardrails-backup-20261018T160257.518685023Z`；
   `GUARDRAILS_APPLIED`、`READY_STATUS=200`。远端读回哈希：
   cpa_policy `a5cd4a08…`、cpa_provider_routes `a9bf3cf7…`（均=HEAD blob）、
   cpa-health `dd799226…`（4fb16f0 新版）、admission 家族保持 `76eeb1c6…`
   未动。事务内 CPA 容器重建一次（started `2026-10-18T16:02:59Z`，v8.0.32
   同 digest），apply 内置 post-verify `HEALTH_OK` / READY 200 通过。
3. strict doctor 终验（apply 后立即）：exit 0、`DOCTOR_CONTRACT_OK`、
   `semantic-policy=OK`（三条 POLICY_FAILED 全部消除：stabilize 断言迁
   v8 规范位 `upstream.claude.header-defaults`、opencode 退役期望同步）、
   投影 12×MATCH 零 MISMATCH、`admission-integrity=OK`、
   `chatgpt-oauth`/`antigravity-oauth`/`claude-oauth` 三 lane OPEN 无 hold、
   `claude_lane=OK`（3 认领 + 16 墓碑）、`antigravity_lane=OK`（1 认领）、
   `oauth_quarantine=none`、目录 13 名契约一致（plugin_routes 并集生效，
   MODEL_IDS_UNKNOWN=none）。
4. 新 substitution 段输出：`model_substitution_warnings_7d=0`、
   `model_substitution_distinct_pairs=0`、`model_substitution=OK`、新
   coverage 文案生效。计数较当日早间 261 归零的原因是容器重建使
   `docker logs` 从本次启动起算（runbook 文档化的既有边界，非信号变化；
   261 已归因为单对 antigravity 命名观察）。doctor 侧 awk 管线以当日真实
   日志形样本预验（2 对拆分/distinct=2/auth_index 不外泄）。

## 回滚

- 仓侧：revert `9b22c77`（单提交，5 文件）。
- 远端：从 `/root/cpa-guardrails-backup-20261018T160257.518685023Z` 恢复
  policy/manifest/health 旧版并以旧哈希重投影；admission 家族未变更无需
  恢复。Git 回滚不代替远端恢复。

## 验证层级与边界

- 已证明 `repo_verified -> filesystem_projected -> host_loaded`（doctor 契约
  与远端哈希读回）。未发送任何生成探针，不新增 controlled/natural live
  验收结论；doctor OPEN 不证明上游接受账号。
- 遥测输出不含凭据标识、auth_index、日志原文或完整 capability path。
- 外部社区情报（错误码 14003/6002 分层、safety-buffering 头、turn-state
  路由语义）为 2026-10-18 检索快照，来源与日期见当次评审记录；判据入
  runbook 后仍以现场 doctor/dump 取证为准。
