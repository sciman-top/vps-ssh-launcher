# 2026-10-21 BWG chatgpt-oauth lane `unverified_request` hold 释放（部署副作用收口）

## 授权与边界

- 用户出示 Codex 客户端 403 截图（`requires operator review`）+ 本会话常驻指令
  "按推荐连续修复直至完成" + "现有 OAuth 账号与 API key 渠道全保留"。
- 仅触碰 `/var/lib/cpa-admission/chatgpt-risk.json` 的 `hold_reason` 字段与
  admission 服务一次受控重启；不改凭据、不改 config.yaml/compose/插件、不动
  `not_before`、不触碰 claude 退役态（`claude-risk.json` 保持
  `authentication_required` 历史值）。

## 信号与根因（先 doctor，再 journal）

- 客户端面：Codex IDE 经公网路径请求 `gpt-6.1-sol` 得 403
  `Shared upstream lane chatgpt-oauth requires operator review`。
- doctor（2026-10-20 16:1x UTC）：`oauth_monitor=OK`（codex days_left=8、
  27.7h 前刷新成功、7 天零刷新失败）——凭据本体健康；24h
  `admission_lane_reject_reasons` 含 `operator_review_required:28`
  （chatgpt-oauth 23）；错误转储 `codex/gpt-6.1-sol` 2 份 `auth_unavailable`。
- journal 时间线（UTC 10-20）：15:26:51 `gpt-6.1-sol` 200 + 流内容量标记
  （`capacity=true successful=false`）→ 15:28:43 与 15:31:27 admission 两次
  重启（同日 14003-ladder 部署事务，见 20261020 台账；首次 -Apply 曾因 chatgpt
  在途 fail-closed 拒绝）→ 15:31:27.737 起连续 `lane_reject
  reason=operator_review_required retry_after=300`。
- 状态读数：`chatgpt-risk.json = {"version":1,"not_before":0,
  "hold_reason":"unverified_request"}`。
- **判定**：部署重启时有在途请求结果未确认，被 write-ahead/流式锁存机制按设计
  落 `unverified_request`（插件 runbook §7：复核 admission 停启与请求时间线后
  按人工恢复流程处理）。非上游风控信号；唯一失败样本属已知的上游容量窗透传族。

## 处置（cpa-gateway.md「OAuth 持久化风险状态的人工恢复」全流程）

1. 备份：`/root/chatgpt-risk-backup-20261020T162907Z.json`
   （owner nobody:nogroup、0600、原字节保留）。
2. 空闲确认：healthz 四 lane `inflight:0`、pending 全 0。
3. `systemctl stop cpa-admission` → `hold_reason` 改 `null`
   （`not_before=0` 本就无窗口，未缩短任何等待）→ 保 owner/mode → start。
4. healthz 复验：`chatgpt-oauth hold_reason=null`、`cooldown_active=false`、
   四 lane OPEN、`state_persisted=true`。

## 验收

- 8318 回环单发一次（有界，非探针风暴）：`gpt-6.1-sol` →
  **HTTP 200 / 2.75s / 正文 "ok"**（key 远端解析未回显、无凭据入日志）。
- 自然流量随后由用户客户端回归，无后续合成请求。

## 边界与回滚

- 单发成功证明 admission 层与上游数据面恢复；不证明供应商账号长期状态，不消除
  第三方 OAuth 条款风险。
- `unverified_request` 锁存本身是 fail-closed 正确设计：部署事务已做 lane 空闲
  检查，"检查后、重启前"溜入在途流属固有竞态窗口，维持现状不为本次改 admission。
- 回滚（仅在需要重新置锁时）：恢复备份文件（保 owner/mode）+ 重启 admission。
