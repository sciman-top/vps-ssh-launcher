# 2026-10-14 bwg CPA Antigravity OAuth lane 接入（全链闭环）

## 依据

- 用户 2026-10-14 定案：Google AI Pro 付费账号、单人手动低频、两机分时、
  单凭据、风险接受；同日追加决定：暴露面收窄为仅 `gemini-3.8-flash-high`
  （antigravity 目录无裸名 `gemini-3.8-flash`，3.8 flash 档仅此一名）。
- README 容灾政策（4e672e2）：Google 2026-06-18 公告后个人 AI Pro 不再走
  Gemini CLI OAuth，antigravity lane 是该订阅在 CPA 的唯一受支持形态。

## 变更（869eae8 + a4a5074，main==origin）

- `antigravity-oauth` 路由 + admission lane（inflight=2/pending=4/120s/
  冷却阶梯 60-900s）；`cpa_policy.py` 把"共享订阅 OAuth 必须挂已审核 lane"
  升为 fail-closed 契约（无闸共享凭据 = 封号形态）。
- 其余 7 个目录模型经 config `oauth.excluded-models.antigravity`
  provider 级排除（-Apply 从 manifest `antigravity_oauth_exclusions` 派生），
  `codex_api_key_exclusions` 留墓碑防 API-key lane 抢注；doctor 新
  `==antigravity-lane==` 门禁 + `MODEL_IDS_UNKNOWN` fail-closed。
- `cpa-admission.py` 审核白名单 +1 行，integrity pin 受控轮换
  （fbaeb631… → efda0da9…）。
- Luna 事务（quarantine/deactivate/doctor/policy）语义收窄钉在
  chatgpt-plus-oauth 路由：antigravity 通道不受 Luna 开关影响，其幸存不算
  契约违约。

## 验收

- 本地：full gates PASS（572 passed/1 skipped，bandit/ruff/format/mypy 净；
  两轮：869eae8 修复后由本会话复核、a4a5074 由本会话首发）。
- 凭据：`-antigravity-login` 临时容器（--rm、`-p 127.0.0.1:51121`、umask 077）
  loopback OAuth 完成，auth 目录仅新增 `antigravity-*.json`（600），容器自毁
  无残留；备份 `/root/antigravity-*.json.backup-20261014`（sha256 与源一致，600，
  不入仓）。
- 投影：-Apply 后 doctor `==projection-drift==` 11 项全 MATCH（含新 pin），
  `==antigravity-lane== OK`（allowed=gemini-3.8-flash-high，7 排除生效），
  `MODEL_IDS` 含 gemini-3.8-flash-high 且 `MODEL_IDS_UNKNOWN=none`，
  `DOCTOR_CONTRACT_OK`。
- 单发验收（loopback 8318，api-key 服务端取、不回显）：
  `chat/completions gemini-3.8-flash-high → 200 / 2.59s / finish=stop /
  content='PONG' / 78 tokens`。上游回显 model 字段为
  `gemini-3.8-flash-n`（上游自称名，路由按目录 id，无契约影响）。
- 封锁分类判定：bwg 出口（US）下通道 LIVE → 本机 IDE 直连 403
  `UNSUPPORTED_LOCATION` 为纯 IP 定位型，账号无国别限制。

## 纪律与看护

- 探针低频：本 lane 不挂定时生成探针（doctor 目录读数除外），验收单发
  共 1 发；质量基线以该样本 + 后续自然流量为锚。
- 退场：删 auth 文件 + 静默重启即可；触发器=连续 invalid_grant 或配额
  长期耗尽干扰主通道。oauth-monitor 的 invalid_grant 全容器扫描覆盖本
  凭据；days_left 只认 codex 为已知缺口（Google refresh token 长效，观察项）。
- 观察项（既有，非本次引入）：codex `oauth_days_left=8`（~10/22 刷新点，
  timer 自愈勿干预）。

## 回滚

- 仓库：revert 869eae8+a4a5074 后重投影。
- 远端：auth 文件删除即断凭据（备份在 /root）；admission/config 变更随
  -Apply 反向重投影恢复；pin 轮换随 git 回滚同步还原。
