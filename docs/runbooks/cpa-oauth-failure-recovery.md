# CPA OAuth 非自愿失效恢复（bwg）

本页处理 ChatGPT Plus OAuth 凭据的非自愿失效（刷新失败、到期未滚动、
`invalid_grant`）；主动登出与重新接入的常规流程见
[cpa-oauth-luna-slot.md](cpa-oauth-luna-slot.md)，疑似风控的分级处置见
[cpa-ban-throttle-incident-response.md](cpa-ban-throttle-incident-response.md)。

## 刷新节奏（判别基准）

CLIProxyAPI 在到期前 24 小时自动刷新 codex OAuth（按到期时间调度、最长 30 秒
唤醒校正、5 分钟失败退避）。doctor `==oauth-monitor==` 的阈值按小时对齐：

- 剩余 >72h：`OK`。
- 进入 72h：`WARN_RENEWAL_WINDOW`，非阻断，等自动刷新。
- 进入 24h 窗口且耗尽 2h 调度宽限仍未滚动（剩余 ≤22h）：`ACTION_REQUIRED`
  阻断——此刻才进入本页。
- `FAIL_EXPIRED` / `FAIL_EXPIRY_UNKNOWN` / `FAIL_REFRESH_SIGNAL`：直接进入本页。

## 判别流程

1. strict doctor 读 `==oauth-monitor==`：区分"刷新点未到（只需等）"与
   "宽限耗尽 / 刷新失败信号未消解"。
2. 确认失效是线程级还是凭据级：同凭证 `gpt-6-luna` 新会话发一次请求。新会话
   连续 200 → 旧线程问题，弃线程即可；新会话也失败 → 凭据级，继续。
3. 看容器日志确认信号类别：

   ```bash
   docker logs cli-proxy-api --since 48h | grep -iE 'invalid_grant|refresh_token_reused|credential refresh failed'
   ```

   `invalid_grant` / `refresh_token_reused` 是不可自愈信号（refresh token 已被
   上游吊销或复用检测拦截），只有 device-login 重登一条路。
4. `refresh_token_reused` 常见诱因：同一账号的凭据被第二个消费者（本地工具、
   另一台机器）并行刷新。恢复前先消除第二个消费者，否则重登后会立刻复发。

## 恢复（人工、单变量、备份优先）

1. 备份（只存本机，不入仓）：

   ```bash
   mkdir -m 700 -p /root/cpa-oauth-backup-$(date -u +%Y%m%dT%H%M%SZ)
   cp -a /opt/cliproxyapi/auth /root/cpa-oauth-backup-<UTC>/
   ```

2. 走受支持的交互式 device-login（在 SSH 会话内后台化，普通 nohup 的
   docker exec 会随 SSH 通道死亡）：

   ```bash
   setsid docker exec cli-proxy-api /CLIProxyAPI --codex-device-login \
     </dev/null >/tmp/cpa-device-login.log 2>&1 &
   ```

   按 log 里的 URL 完成浏览器授权；多设备/重复授权会增加风控暴露，一次
   会话只做一次。
3. 修权限：device-login 落盘的 auth JSON 是 0644，`chmod 600`，否则 strict
   doctor 的 `auth-permissions` 门直接 FAIL。
4. config 级 `oauth-excluded-models` 变更不走热载：涉及 config 时重启是唯一
   保证（`docker restart cli-proxy-api`）。
5. 复验（第二条 SSH）：strict doctor 全绿（oauth-monitor 回到 OK、目录 12 ID
   口径按 manifest 派生）、`gpt-6-luna` 新会话一次生成 200/stop、端口与
   fail2ban 状态不变。确认联网正常后结束。

## `-DeactivateOAuthLuna` 的定位

`pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg
-DeactivateOAuthLuna` 是唯一的凭据销毁入口：停止 CPA 后从 `/root`、备份目录
与活动 auth 目录删除全部 Codex OAuth JSON，不备份、不可逆，登出后 luna 双名
从目录消失。它只用于两件事：

1. 账号确认不可用后的凭据清除；
2. 更换 OAuth 凭据前的显式清场。

正常恢复永远优先 device-login；登出后目录契约由清单派生断言校验（出现
清单外 ID 即失败），见 README CPA 段。

## 禁止

- 不自动化本页任何步骤；不循环重试 device-login。
- 不在失效窗口做 OAuth lane 的批量生成验证。
- 不把 auth JSON、device-login 日志或 URL 带入 Git、receipt 或聊天回显。
