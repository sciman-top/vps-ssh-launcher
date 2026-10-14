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
  `antigravity-sciman.phys@gmail.com.json` 0600 落盘，主容器热加载，已备份至
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

- **上游目录漂移**：登录时 antigravity 上游供 8 模型，数小时后仅余
  `gemini-3.8-flash-high`（OAuth 别名天生 optional，doctor 按设计不红）。
  且出现 `served "gemini-3.8-flash-n" for requested "gemini-3.8-flash-high"`
  的上游改名/替换信号——`==model-substitution==` 门禁将开始计数，属"降智"
  疑虑的第一优先归因入口，观察勿干预。
- `gemini-3-flash` 当前 400（`unknown provider`，上游目录暂撤）属 optional
  优雅降级；manifest 继续声明意图，上游恢复即回归。
- doctor `==oauth-monitor==` 的 days_left 仍只认 type=codex（Google refresh
  token 长效故低风险），invalid_grant 由全容器日志扫描兜底——已核实缺口，
  保持观察项不扩门禁。

## 回滚

- 通道退役：删除 auth JSON + 静默重启（无新开关，R5）。
- 代际回滚：`/root/cpa-admission-antigravity-backup-20261014T150802Z/rollback.sh`
  （校验哈希、恢复五文件+config.yaml+pin、重启、healthz）。Git 回滚不能代替
  远端恢复。
