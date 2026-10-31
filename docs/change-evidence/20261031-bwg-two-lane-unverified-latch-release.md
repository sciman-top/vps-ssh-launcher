# 2026-10-31 BWG workbuddy+chatgpt 双车道 `unverified_request` latch 释放

## 授权与边界

- 用户出示 ChatGPT desktop（Codex）403 截图（`Shared upstream lane workbuddy-oauth
  requires operator review`）+ 本会话 AskUserQuestion 明确授权"执行释放（推荐）"。
- 仅触碰 `/var/lib/cpa-admission/chatgpt-risk.json` 与
  `/var/lib/cpa-admission/workbuddy-risk.json` 的 `hold_reason` 字段与 admission
  一次受控重启；不改凭据、不改 config.yaml/compose/插件、不动 `not_before`；
  claude 退役墓碑（`authentication_required`，2026-10-19 起）按既有边界保持不动。

## 信号与根因（先只读探针，再 journal）

- 客户端面：`deepseek-v4.1-flash` 与 gpt 系模型全部 403
  `operator_review_required`（`retry_after=300`），客户端重连 5/5 全部被打回。
- journal 时间线（UTC 10-31）：02:28:13 `Stopped` / 02:28:15 `Started
  cpa-admission`（受控 stop/start；docker 容器同窗口重启，ExitCode=0、
  RestartCount=0、非 OOM，非崩溃）；02:28:21 起连续 `lane_reject
  reason=operator_review_required`（workbuddy `deepseek-v4.1-flash`、chatgpt
  `gpt-6.1-sol`），即用户症状起点。
- 状态读数：`chatgpt-risk.json`（mtime 02:27）/ `workbuddy-risk.json`（mtime
  02:28）均为 `{"version":1,"not_before":0,"hold_reason":"unverified_request"}`；
  healthz 四车道 `inflight=0`、`pending=0`。
- **判定**：02:28 重启事务（时间线与当日 gpt-reserve 投影落地吻合——healthz
  chatgpt 车道模型已含 `gpt-reserve`，旁证）跨越在途请求，write-ahead 标记按
  fail-closed 设计在启动加载时 latch 成 manual hold。同款固有竞态第三次发生
  （2026-10-20 chatgpt、2026-10-21 chatgpt 两案见既有台账）；非上游风控信号，
  唯一无失败样本。重启不解锁在本次再次实证：02:28 的重启本身未清锁，盘上
  标记随启动重新 latch。

## 处置（cpa-gateway.md「OAuth 持久化风险状态的人工恢复」全流程）

1. 备份：`/root/chatgpt-risk-backup-20261031T024544Z.json`、
   `/root/workbuddy-risk-backup-20261031T024544Z.json`（owner nobody:nogroup、
   0600、sha256 与原文件逐字节一致）。
2. 空闲确认：healthz 四车道 `inflight=0`、`pending` 全 0。
3. `systemctl stop cpa-admission` → python 断言 `version=1`、`not_before=0`、
   `hold_reason=unverified_request` 后置 `hold_reason=null`（任何断言失败自动
   从备份还原两文件）→ 保 owner/mode → start。
4. 落盘复核：两文件均为
   `{"version": 1, "not_before": 0, "hold_reason": null}`，owner=nobody、
   group=nogroup、mode=0600 保持。

## 验收

- healthz：四车道 `hold_reason` 全 null；zhipu 车道 `inflight=1` 为自然流量
  恢复佐证。
- 8318 回环各单发一次（有界，非探针风暴）：`deepseek-v4.1-flash` →
  **HTTP 200 / 2.94s / finish=stop**；`gpt-6.1-sol` → **HTTP 200 / 2.73s /
  finish=stop**（key 远端解析未回显、无凭据入日志）。
- 自然流量随后由用户客户端回归，无后续合成请求。

## 边界与回滚

- 单发成功证明 admission 层与上游数据面恢复；不证明供应商账号长期状态。
- latch 机制本身是 fail-closed 正确设计：重启事务无法根除"空闲检查后、重启前
  溜入在途流"的固有竞态（10-21 案已裁定维持现状不为改 admission），本次不加
  代码改动。缓解抓手仍是部署事务的 lane 空闲前置检查 + 事后 doctor
  `==lane-hold==`。
- 回滚（仅在需要重新置锁时）：恢复备份文件（保 owner/mode）+ 重启 admission。
