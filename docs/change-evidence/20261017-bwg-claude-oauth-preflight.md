# 2026-10-17 bwg CPA Claude OAuth 登录前风控预埋（fail-close）

## 触发与决策

- 2026-10-16 用户决定：在 bwg CPA 接入 Claude 订阅 OAuth lane，本机
  Claude Code 经网关消费（推翻 cpa-claude-account-risk.md 决策 1 的"不承载"），
  登录时机另定。
- 2026-10-17 用户指令：暂缓登录，先落登录前防封风控。本窗口执行：
  VPS 只读取证 + 两项 config 预埋（真实远端写入）+ runbook 固化。
  同窗口曾按用户消息发起登录并被叫停：临时容器 `cpa-claude-login` 已
  `docker rm -f` 移除，auth 目录维持 2 凭据，**零凭据写入**。
  授权 URL 曾进会话文本（未兑换即废弃，无 token 发行）；后续按 runbook
  纪律最小化展示。

## 变更内容（config.yaml，热加载，无容器重启）

1. `oauth.excluded-models.claude: [claude-*]` —— claude OAuth 目录 fail-close：
   登录后目录即消、请求本地拒绝，lane 落地前零暴露。API-key 路由豁免
   （v8.0.31 `service_models.go oauthExcludedModels` 对 `authKind=apikey`
   返回 nil），在役 zen `claude-haiku-5-5` 不受影响。
2. 顶层 `claude-header-defaults.stabilize-device-profile: true` —— 真实 Claude
   Code 客户端软件画像按凭据持久化（KV 7 天 TTL），防画像漂移；
   不设 UA/OS/timezone 覆盖（不伪装纪律）。

两键均在 v8.0.31 上游源码核实存在（`internal/config/config_types.go`
`stabilize-device-profile`、`config.go OAuthExcludedModels`）；插入点按既有
`codex:`/`antigravity:` 键风格文本手术，保持注释与顺序；写入前 yaml 结构校验
（无顶层重复键）。

## 证据

- 备份：`/opt/cliproxyapi/config.yaml.bak-20261016-claude-preflight`（sha256
  前 16 位 `81482320b5cffd89`；新文件 `f47bb4a8b3d6d5bb`）。
- 热加载日志：`oauth-excluded-models[claude]: added (1 entries)`、
  `config successfully reloaded, triggering client reload`、
  `8 clients (2 auth entries + … + 1 Claude API keys + 1 Codex keys + …)`，
  payload 钉死段 2+3 条规则无损。
- strict doctor：`DOCTOR_CONTRACT_OK`，drift 12 项全 MATCH，`MODEL_IDS` 12 模型
  不变（`claude-haiku-5-5` 仍在役 = apikey 豁免实证），`semantic-policy=OK`、
  `admission-health=OK`、`oauth_monitor=OK`（codex days_left=6）、antigravity
  lane 与排除表无扰。
- 严格脱敏：全程未打印密钥、凭据文件名邮箱、授权 URL 之外的敏感值。

## 回滚

宿主恢复备份后 watcher 自动热加载：
`cp config.yaml.bak-20261016-claude-preflight config.yaml`；doctor 复验
`MODEL_IDS` 与 drift。

## 边界

- 本预埋同时兼容新旧两版决策：排除表在"不承载"语义下是阻断加固，
  在"接入"语义下是 fail-close 前置；`stabilize-device-profile` 对现有
  lane 零行为变化。
- runbook 见 [docs/runbooks/cpa-claude-oauth-attach.md](../runbooks/cpa-claude-oauth-attach.md)；
  决策修订注记见 cpa-claude-account-risk.md。

## 同日后续：两次登录尝试与决策二次翻转

- 登录尝试二（01:1x）：回调 `account_on_hold`——**浏览器登录态为登错的 2024
  受限老账号**，与本仓/网关无关；现场清理，零写入。
- 登录尝试三：换目标账号后授权页健康，但报 **"Claude Max or Pro is required
  to connect to Claude Code"**——免费账号被官方拒绝，订阅为硬前提。
- 01:28 并行会话 1e56b44 将决策翻转为关闭（runbook 转关闭守卫、policy 钉
  stabilize=false），其投影未及部署（drift FAIL 在途）。
- 用户同日订阅 Pro 后仲裁重开：本仓将 policy/apply/夹具的 stabilize 预期翻回
  `true`（真实 Claude Code 画像按凭据持久透传的源码语义，v8.0.31
  `claude_executor_request.go` stabilize 分支），恢复接入版 runbook 并补三次
  实操拒答形态；1e56b44 的 Antigravity 收紧与目录墓碑独立成立、保留不动。
  远端 config 从未翻转（stabilize 仍为 true、claude-* 排除在位），重开投影
  即可对齐。
