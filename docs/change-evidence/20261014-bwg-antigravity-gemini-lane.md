# BWG antigravity（Google AI Pro / Gemini）通道接入

## 触发与决策

- 用户 2026-10-14 决策：Google AI Pro 付费账号经 CPA antigravity OAuth lane 接入网关，
  单人手动低频、两台桌面分时段调用、单凭据，风险接受（同 2026-09-26 GLM 模式）。
  守 2026-09-20 既有边界：不做规避工程，UA/目录用上游 antigravity 默认。

## 登录（远端只读+受控临时容器）

- 生产容器 v8.0.28；`-antigravity-login` 无 device flow，loopback 回调 51121 且绑定
  全接口——登录用临时容器（同镜像同挂载、`-p 127.0.0.1:51121:51121` 仅发布宿主
  loopback、config 只读挂载），用户浏览器授权后以 paste-back 回调 URL 经宿主
  curl 注入，token 交换自 bwg 美国出口完成。凭据
  `[redacted account identifier]` 0600 落盘，主容器热加载，已备份至
  `/root/auth-backups/`。地区封锁实测为 IP 定位型：bwg 出口（US）回环探针 200；
  用户本机直连报 `UNSUPPORTED_LOCATION` 属本机路径问题，与网关无关。

## 目录与 lane 契约（commit 869eae8）

- manifest 新增 `antigravity-oauth` 路由（gemini-3-flash / 3.1-flash-image /
  3.1-flash-lite / 3.5-flash-lite / 3.8-flash-high / claude-opus-4-6-thinking /
  claude-sonnet-4-6 / gpt-oss-120b-medium），8 名进 `codex_api_key_exclusions`；
  按共享订阅 OAuth 不变量挂 `antigravity-oauth` admission lane
  （inflight=2/pending=4/120s/冷却阶梯 60-900s）。
- Luna 事务语义收窄到 `chatgpt-plus-oauth` 路由：quarantine/deactivate 模板、
  doctor cooldown-state、policy `EXPECTED_OAUTH_ROUTE_ALIASES` 四处；
  antigravity 通道不受 Luna 开关影响。
- full gates PASS（572 passed / 1 skipped；bandit/ruff/mypy 净）。

## 同日收窄（commit a4a5074，用户决定）

- 用户登录后同日要求"Antigravity OAuth 只开放 gemini-3.8-flash"。antigravity
  目录无裸名 `gemini-3.8-flash`（收窄前回环实测 8 模型在列，3.8 档仅
  `gemini-3.8-flash-high`），按该名落地：manifest 路由缩为单模型，新增
  `antigravity_oauth_exclusions`（其余 7 名），`-Apply` 派生写 config
  `oauth.excluded-models.antigravity` 做 provider 级排除（目录即消、请求
  本地拒绝），7 名在 `codex_api_key_exclusions` 留墓碑；admission lane
  模型名单与 apply/doctor 预言机同步；doctor 新 `==antigravity-lane==`
  门禁核对排除表漂移。full gates PASS（572 passed / 1 skipped）。

## 受控部署（outputs/deploy-admission-antigravity-20261014.ps1）

- 常规 `-Apply` 按 `admission_integrity_pin_mismatch` fail-closed 拒绝 pin 轮换
  （设计内）；走受审查事务：维护锁下原子轮换 cpa-admission.py + integrity pin +
  cpa-admission.json + cpa_policy.py + cpa_provider_routes.json 五文件，config.yaml
  同事务补 8 个 `api-keys.codex[*].excluded-models` 派生名；integrity check、
  semantic policy、三 lane healthz 全过。
  `DEPLOY_OK` 备份 `/root/cpa-admission-antigravity-backup-20261014T150802Z/`。
- 跟进 `-Apply` 投影新门禁脚本：`GUARDRAILS_APPLIED`，ready 检查含
  `has_antigravity_gemini_3_8_flash_high=True`。

## 验收

- 严格 doctor：`DOCTOR_CONTRACT_OK`、`MODEL_IDS_UNKNOWN=none`（登录前缺口闭合）、
  `admission-health=OK`（三 lane）、`admission-integrity=OK`、drift MATCH、
  `tls-cert=OK`。
- 定向单发（8318 经 admission 全链，一发不重试）：
  `LIVE_ACCEPT_CHECK model=gemini-3.8-flash-high served=gemini-3.8-flash-n
  finish=stop content='OK'` → LIVE_ACCEPTED。

## 观察项（不阻断）

- **目录 8→1 归因更正（a4a5074 补记）**：网关目录从 8 模型缩到
  `gemini-3.8-flash-high` 是上节 config 排除所致（收窄前 23:0x 回环实测
  上游仍供 8 模型），不是上游目录漂移。上游当前是否仍供其余 7 名未探测
  （管理 API secret-key 为 bcrypt 哈希不可回读；亦无需绕过自身排除验证）。
  上游真实改名信号只有 `-n` 回显 warn：
  `served "gemini-3.8-flash-n" for requested "gemini-3.8-flash-high"`——
  `==model-substitution==` 门禁将开始计数，属"降智"疑虑的第一优先归因
  入口，观察勿干预。
- `gemini-3-flash` 等 7 名自 a4a5074 起不在任何 lane 声明，请求被本地拒绝
  （如 400 unknown provider）属排除契约按设计生效，非上游故障；未来要
  恢复任一名时，从 `antigravity_oauth_exclusions` 与
  `codex_api_key_exclusions` 墓碑移除并走目录变更 checklist。
- doctor `==oauth-monitor==` 的 days_left 仍只认 type=codex（Google refresh
  token 长效故低风险），invalid_grant 由全容器日志扫描兜底——已核实缺口，
  保持观察项不扩门禁。

## 回滚

- 通道退役：删除 auth JSON + 静默重启（无新开关，R5）。
- 代际回滚：`/root/cpa-admission-antigravity-backup-20261014T150802Z/rollback.sh`
  （校验哈希、恢复五文件+config.yaml+pin、重启、healthz）。Git 回滚不能代替
  远端恢复。

## 第二窗口复核与证据合并（同日深夜）

- 双窗口协作：a4a5074 由第二窗口（ZCode 会话）首发门禁凭据（572 passed
  全绿）并 push；869eae8 同窗口复核后 push；-Apply/受控事务由第一窗口
  自行完成，双方零碰撞。期间清掉一个挂死的孤儿 run_gates 进程
  （pytest 26min 近零 CPU）。
- 8318 定向单发实际双发互证：第一窗口 `content='OK'` + 第二窗口
  `200 / 2.59s / finish=stop / content='PONG' / 78 tokens`；结果一致。
- 凭据双备份冗余无害：`/root/auth-backups/`（事务）+
  `/root/antigravity-*.json.backup-20261014`（第二窗口，sha256 与源一致，600）。
- 纪律锚点：本 lane 不挂定时生成探针（doctor 目录读数除外），质量基线=
  本日单发样本+后续自然流量；codex `oauth_days_left=8`（~10/22 刷新点，
  timer 自愈勿干预）。
- 本节由两份并行台账（34865a4 `20261014-bwg-cpa-antigravity-oauth-lane.md`
  与本文件）合并而来，重复文件已删除，本文件为唯一台账。
