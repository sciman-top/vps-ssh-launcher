# 2026-10-17 bwg CPA claude-oauth lane 落地（Claude Pro 订阅 OAuth）

## 决策链（同日三次翻转，均用户驱动）

87fb574 开（登录前风控预埋）→ 1e56b44 关（收紧，未及远端部署）→ 用户订阅 Pro 后
重开（5bff9dd）→ 本台账：lane 落地完成。用户两次收敛认领范围，终态=只认领
`claude-opus-5-5` / `claude-sonnet-5-5` / `claude-haiku-5-5` 三名。

## 登录（第三次尝试成功）

- 前两次：`account_on_hold`（浏览器登错 2024 受限老账号，已排除）；授权页
  "Claude Max or Pro is required"（免费号墙，用户随后订阅 Pro）。
- 成功次：临时容器（v8.0.31 digest db1812ea 锁定、config/plugins 只读挂载、
  auth 读写、`-p 127.0.0.1:54545:54545`）+ `--entrypoint /bin/sh` 覆盖（生产
  entrypoint 为 sh -c 形式，直接传参会被吞）。paste-back 回调注入完成令牌交换，
  凭据 0600 落 `/opt/cliproxyapi/auth/`，`/root/auth-backups/` 备份，主容器
  `auth file changed (CREATE)` 热加载（2→3 auth entries）。
- fail-close 实战判据 PASS：凭据在役 + strict doctor `DOCTOR_CONTRACT_OK` +
  MODEL_IDS 零新增 claude 名。
- 真实目录经凭据 OAuth 令牌在 VPS 本地向 `api.anthropic.com/v1/models` 读取
  （令牌不出 VPS）：14 名。

## lane 落地变更（提交 01202cf..）

- manifest：`oauth_routes` 增 `claude-oauth`（三名）；`claude_oauth_exclusions`
  16 名墓碑（账号 14 目录名中未认领的 11 个 + models.json 静态清单的 5 个
  legacy dated 名，后者经冷启动目录实测暴露后补全）；`claude_api_key_routes`
  清空——zen claude API-key 路由（槽位 2 / opencode.ai）随 OAuth haiku 供给
  退役，其 payload 钉死段三条规则与 cpa-health 行为回归网（空路由自禁用）
  同批退出。
- cpa-admission.py/json：第四 lane `claude-oauth`（共享订阅不变量
  inflight=2/pending=4/120s/60→900s 阶梯），integrity pin 轮换。
- cpa-guardrail-apply.sh：claude 排除改为清单派生；admission 契约表补第四 lane。
- cpa-guardrail-doctor.sh：admission-health lane 表补第四 lane。
- cpa_policy.py：`EXPECTED_CONFIG_PATHS` 排除表钉 16 名；审查路由表补
  `claude-oauth`；`EXPECTED_ADMISSION_MAX_INFLIGHT` 补 claude-oauth=2。
- 远端 config.yaml 同事务文本手术：payload 三条 claude 规则摘除、
  `api-keys.claude` 组摘除、排除表 16 墓碑、codex 组排除追加三名
  （bare-route overlap）。CPA 热加载实证（`oauth-excluded-models[claude]:
  updated` 系列 reload 日志）。

## 部署过程（受审查事务，5 次尝试）

采用 outputs/deploy-admission-claude-20261017.ps1 + complete 变体（分离执行
绕传输 60s idle 超时）。过程中修复两个事务自身缺陷并留档：YAML 列表项
`- ` 前缀未剥导致组定位失败；分离守护继承 flock fd 长占维护锁（`9>&-` 修复）。
中途半换装态（文件=新/pin+config=旧）由收尾事务就地完成。**教训：config.yaml
编辑的 errexit 失败路径须显式 restore；编辑匹配器须对 YAML 两种行形态做演练
（本次本地 dry-run 演练抓到 muse 组截断）**。

## 验收

- strict doctor：`DOCTOR_CONTRACT_OK`；`MODEL_IDS` 恰 14 名（11 非 claude +
  三认领），`MODEL_IDS_UNKNOWN=none`；`semantic-policy=OK`；
  `admission-health=OK`（四 lane）；drift 全 MATCH。
- 回环 8318 定向单发一发：`claude-haiku-5-5` `stop=end_turn` content='OK'
  0.85s → **LIVE_ACCEPTED**（同时验证 zen→OAuth 的 haiku 供给切换）。
- 目录：`/v1/models` claude 名恰三名；admission healthz 四 lane 全 healthy。

## 边界与遗留

- `oauth.excluded-models` 对 antigravity provider 生效（目录 1 名实证）、对
  claude provider 在 v8.0.31 运行态经热加载+冷启动双重实证生效（16 墓碑后
  目录恰三名）——先前"不生效"的观察为多轮半换装态的中间读数，已在冷启动后
  消除。
- admission `days_left` 监控仍只认 codex；claude 凭据靠 invalid_grant 全容器
  扫描 + error dumps 兜底（既有已知盲区）。
- 上游 #6692 收编后，zen claude 路由的 payload 规则已随路由退役，无遗留。
- 本机 Claude Code 经网关消费面（ANTHROPIC_BASE_URL + 网关 access key）归
  claude-antiban 仓落地；Claude Desktop 维持官方 lane。
- 权限模式：Auto mode 在网关路由未验收，需权限提示的工作用 Manual（`default`）。
