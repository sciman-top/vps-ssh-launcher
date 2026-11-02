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

**如果目标只是"让外部消费者停止触达 OAuth lane"**，不要用登出：用可逆的
`-QuarantineOAuthLuna` / `-RestoreOAuthLuna`（见
[cpa-gateway.md](cpa-gateway.md) 的"OAuth lane 可逆隔离"）。它保留凭据与后台
刷新、不重置配额/冷却，隔离期间 `-Apply` 会拒绝执行以防静默撤销该决定。

## Antigravity aicode scope 与下次 CPA 升级的耦合动作

上游 PR #6315（2026-11-02 进 dev，v8.0.51 不含）给 Antigravity OAuth 增加
`https://www.googleapis.com/auth/aicode` scope（`internal/auth/antigravity/constants.go`）。
该 scope 只在交互式登录（consent URL）时授予：refresh grant 不携带 scope
参数、Google 不会经 refresh 补授，存量凭据不会自动获得。当前 v8.0.51 在役
车道工作正常（上游 #5289 显示服务端强制目前只见于 Bidi/transcribe 类端点），
属"下次升级前平静处理"项，不是当前故障。

自动更新器会按 72h 成熟窗自动套用首个包含 #6315 的 release，因此该升级窗
必须配对执行（一次维护窗内完成，逐台边界不变）：

1. 升级同窗对两个 antigravity 凭据做受控 fresh re-login：照本页"恢复"节的
   setsid 后台模式，用 `--antigravity-login`（callback 固定 51121），一次
   会话只登一个账号。上游 #7204：targeted re-login 对 Antigravity 静默忽略
   target，要登成目标账号必须以该账号手工完成，勿依赖 target 指定。
2. 重登后验证 scope 已带上（token 只留在远端 shell 变量，不回显）：
   取 auth JSON 的 access_token 字段（路径以在役文件实际结构为准）后
   `curl -sG https://oauth2.googleapis.com/tokeninfo --data-urlencode
   "access_token=$T" | jq '{scope,email,exp}'`，期望 scope 列表含
   `.../auth/aicode`。
3. 复验：strict doctor 全绿（antigravity lane OK、refresh_failures_7d=0），
   外加一次 `gemini-3.8-flash-high` SSE 单发（照 live-probe 模式）。

同批 release 若含 pluginhost 模型流所有权修复（#7159 系）：容器整体重建
不受影响，但升级窗内勿经 management API 热重载 workbuddy.so（在途流会被
新语义中止；生产更新插件走容器重建，正常路径无感）。

## 禁止

- 不自动化本页任何步骤；不循环重试 device-login。
- 不在失效窗口做 OAuth lane 的批量生成验证。
- 不把 auth JSON、device-login 日志或 URL 带入 Git、receipt 或聊天回显。
