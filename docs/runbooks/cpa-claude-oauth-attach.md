# Claude 订阅 OAuth lane 接入（bwg CPA）runbook

执行 2026-10-16/17 用户决定的 Claude 订阅 OAuth 接入（Pro/Max；**Free 账号无法
完成 Claude Code 授权，2026-10-17 实证**）。本文件曾于 2026-10-17 凌晨被
1e56b44 短暂改写为关闭守卫，同日用户订阅 Pro 后按用户决定重开本版。
合规边界与风险陈述见 [cpa-claude-account-risk.md](cpa-claude-account-risk.md)；
网关运维、异常分级、凭据生命周期见 cpa-gateway /
cpa-ban-throttle-incident-response / cpa-oauth-failure-recovery 各 runbook。

## 登录前基线（2026-10-17 已落地，远端 config.yaml 热加载生效）

- `oauth.excluded-models.claude: [claude-*]`：**fail-close**。OAuth 凭据落盘热加载后
  claude 目录即消、请求本地拒绝，lane 落地前零暴露。API-key 路由豁免有源码锚
  （v8.0.31 `sdk/cliproxy/service_models.go` `oauthExcludedModels` 对
  `authKind=apikey` 返回 nil），在役 zen `claude-haiku-5-5` 路由不受影响；
  通配符语义同 `matchWildcard`。
- `claude-header-defaults.stabilize-device-profile: true`：真实 Claude Code 客户端的
  软件画像按凭据持久化（KV 7 天 TTL），防重启/多客户端画像漂移。刻意不设
  UA/OS/timezone 覆盖，保持上游默认与随版本跟新（不伪装纪律）。
- 回滚基线：宿主 `/opt/cliproxyapi/config.yaml.bak-20261016-claude-preflight`。

## 登录（须用户在场；授权后 5 分钟回调窗）

1. 前置：strict doctor `DOCTOR_CONTRACT_OK`；平静时段；专用 Claude Chrome
   （claude-antiban `start-claude-chrome.cmd`，已登录目标账号、走钉定出口）就绪。
2. 临时登录容器（先 `docker inspect cli-proxy-api` 现核镜像 digest 与挂载，勿凭记忆）：
   注意生产 entrypoint 是 `sh -c` 形式，直接 `docker run <image> -claude-login`
   会被吞参，必须显式覆盖 entrypoint：
   ```bash
   docker run --rm -d --name cpa-claude-login \
     --entrypoint /bin/sh --workdir /CLIProxyAPI \
     -v /opt/cliproxyapi/config.yaml:/CLIProxyAPI/config.yaml:ro \
     -v /opt/cliproxyapi/plugins:/CLIProxyAPI/plugins:ro \
     -v /opt/cliproxyapi/auth:/root/.cli-proxy-api \
     -p 127.0.0.1:54545:54545 \
     <生产镜像@digest> \
     -c "umask 077 && exec ./CLIProxyAPI -claude-login"
   ```
3. `docker logs cpa-claude-login` 取 authorize URL（`claude.ai/oauth/authorize`，
   PKCE + 官方 ClientID）；用户在专用 Chrome 打开并授权。跳转
   `http://localhost:54545/callback?code=…&state=…` 报无法访问属预期
   （回调端口在 VPS），整页复制该 URL。URL 短时效、单次有效，未兑换即死；
   凭据/授权 URL/回调 URL 不入 Git 与 receipt，会话内展示限当场使用。
4. 5 分钟内宿主注入：`curl -s '<回调 URL>'`。令牌交换自 bwg 出口完成，
   与后续数据面同 IP（144.34.229.116，已实测 claude.ai 非硬封锁、三 OAuth
   端点可达）。
5. 验证：auth JSON 0600 落 `/opt/cliproxyapi/auth/`（文件名含账号邮箱，勿回显），
   备份 `/root/auth-backups/`；`docker logs cli-proxy-api` 见热加载
   （`2 auth entries` → `3`）；strict doctor `DOCTOR_CONTRACT_OK` 且 `MODEL_IDS`
   **无新增 claude 名**（fail-close 生效=目录零暴露）；异常转储无新增
   `error-v1-messages-*`。

### 登录环节实测拒答形态（2026-10-17 三次实操）

- 回调 URL 带 `error=access_denied&error_description=account_on_hold`（指向
  `claude.ai/restricted`）：**浏览器登录态是受限账号**（实测为登错的 2024 老
  账号）。处置=核对登录邮箱、换目标账号重来；不重试本次流程。
- 授权页报 **"Claude Max or Pro is required to connect to Claude Code"**：
  账号健康但无订阅，Claude Code OAuth 为订阅专属。唯一前提=先订阅
  Pro/Max（账号域动作），生效后重生成链接即可；无工程绕行。
- 流程判据：**回调 URL=以 `http://localhost:54545/callback` 开头且带 `code=`**；
  贴回 authorize 链接本身=流程未完成，重讲步骤勿注入。
- 事件窗口纪律不变：任何拒答形态都不重试连发、不换节点、不换号绕过。

### Pro 档用量纪律

Pro 的 5h/周限额窗比 Max 更紧：低频单会话优先，限额窗让 admission 冷却阶梯
排队；不挂定时生成探针。订阅后先纯官方使用一段时间再挂网关仍是推荐节奏
（timing 由用户决定）。

## lane 落地（登录后同窗口完成，解除 fail-close）

按 [cpa-catalog-change-checklist.md](cpa-catalog-change-checklist.md) 与
antigravity 2026-10-14 受审查事务模式：

1. manifest `oauth_routes` 新条目 `claude-oauth`（`shared_consumption=true`、
   `workload_class=subscription-oauth`、`admission_lane=claude-oauth`）；模型认领
   名单按用户 2026-10-17 决定收敛为三名：`claude-opus-5-5`、`claude-sonnet-5-5`、
   `claude-haiku-5-5`（其余 11 个目录名留 `claude_oauth_exclusions` 墓碑，apply
   派生写 provider 排除）。同名 haiku 自 OAuth lane 供给后，槽位 2 / opencode.ai
   的 zen claude API-key 路由同批退役：`claude_api_key_routes` 清空、远端
   `api-keys.claude` 组由 apply 摘除、其 payload 钉死段与 cpa-health 行为回归网
   （self-disabling）一并退役，远端 config 的三条 payload 规则同事务手工摘除。
2. `cpa-admission.json` 新 lane，共享订阅不变量：`inflight=2/pending=4/120s/
   冷却阶梯 60→900s`（同 chatgpt/antigravity）。
3. `cpa_policy.py`：`EXPECTED_OAUTH_ROUTE_ALIASES` 增 `claude-oauth`（现仅
   ChatGPT/Antigravity，policy 对未审查路由 fail-closed）；新增
   `claude_oauth_exclusions` 清单段——未认领名留墓碑并派生替换 config 的
   `claude-*` 通配（apply 生成排除、doctor 反向核对，同 antigravity 模式）。
4. doctor 按 `==antigravity-lane==` 模式补 `==claude-lane==` 门禁；受审查事务
   投影（维护锁 + 原子轮换 + integrity pin 同步）；台账落 change-evidence。
5. 验收：strict doctor 全绿（`MODEL_IDS_UNKNOWN=none`、新 lane admission
   OK）+ 8318 定向单发一发（LIVE_ACCEPTED 形态，不重试）。不做探针矩阵、
   不建真实账号定时探针。

## 已登录后的风险闸门

用户刚把 Claude Code/订阅 OAuth 登录到 CPA，或需要暂停该 lane 时，推荐先用
可逆 provider 隔离；不发送生成探针，不改 UA、设备画像、出口或客户端身份：

```powershell
# 停止所有 Claude subscription OAuth 模型路由，凭据保留
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -QuarantineClaudeOAuth
# 只有人工确认后才恢复路由
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -RestoreClaudeOAuth
```

隔离事务先备份并校验投影 marker-aware `cpa_policy.py`，再通过 CPA v8.0.31 的配置
热加载生效，不重启 API 服务。它要求恰好一个 Claude OAuth 凭据、策略基线通过、
准入 lane 连续 3 次 idle 且当前 `/v1/models`
含全部三个声明别名；然后保 inode 写入配置、原子写 marker，再轮询模型目录确认这三个别名已全部
消失。strict doctor 的预期状态为 `claude_lane=QUARANTINED` 与
`CLAUDE_OAUTH_CATALOG=blocked`。热加载或回读不符合预期时事务回滚；若运行态无法
回到原目录状态，它会报告 `ROLLBACK_FAILED`，此时停止写操作并安排维护窗口，不自行
重启 CPA。

隔离仅停止后续经 CPA 的模型调用；它不撤销官方 OAuth 授权/刷新会话，不证明账号
安全，也不保证免封、免限流或不降智。若 Anthropic 官方账号界面提供第三方授权撤销
入口，可在那里撤销；否则通过官方支持确认。不要把删除本地 token 文件或重启服务当作
账号解封方法。

## 本机消费面（claude-antiban 域，不在本仓落地）

- Claude Code（CLI/IDE）：`ANTHROPIC_BASE_URL` + `ANTHROPIC_API_KEY`（网关
  access key）经公网 8443 capability 路径——2026-10-16 已自然验收的接线；由
  claude-antiban `Set-ClaudeLane` 增设 gateway lane 承载。权限模式用 Manual
  （配置值 `default`）：Auto mode 分类器服务端复核在网关路由上未验收。
- Claude Desktop（聊天应用）：无受支持的自定义端点机制，不经网关，维持官方 lane。

## 运行纪律与急停

- 用量纪律：低频单会话；5h/周限额窗让 admission 冷却阶梯排队，不硬打；
  不挂定时生成探针（doctor 目录读数除外）。
- 监控：`oauth-monitor` days_left 只认 codex（已知盲区勿预建），claude 凭据靠
  invalid_grant 全容器日志扫描 + error dumps（401/403/CF challenge）+
  gateway-statuses 归因；账号侧信号由用户在官方界面人工复核。
- 急停：优先使用 `-QuarantineClaudeOAuth` 停止模型路由，保留 OAuth 文件与运行态；
  不手改 config、不清额度/冷却、不用生成请求验证急停。若决定彻底撤销第三方授权，
  到官方账号界面撤销；本地删除 OAuth 文件本身不等于服务方撤销授权。
- 事件窗口不换号、不清冷却、不轮换指纹续试（见
  cpa-ban-throttle-incident-response.md）。
