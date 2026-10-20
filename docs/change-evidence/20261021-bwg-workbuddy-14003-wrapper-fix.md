# 2026-10-21 BWG workbuddy 14003 包装形状误锁修复与释放（dc5d049）

## 授权与边界

- 用户截图报告 `deepseek-v4.1-flash` 429（"exceeded retry limit"）随后 403
  operator review + 本会话常驻"连续自动修复"+ "现有 OAuth 账号与 API key 渠道
  全保留"。只动 admission 分类器与其 pin、一次受控重启、一次人工 hold 释放；
  不改凭据/config.yaml/compose/插件/公网入口。

## 信号与根因（先 doctor，再 journal + 转储）

- journal（UTC 10-20）：16:41:48–16:43:20 同一 `client_request_hash` 的
  **11 连发相同 payload** 全 200（客户端重试风暴；IDE 会话未加载 10-20 补齐的
  零重试磁盘配置）→ 16:43:24 `deepseek-v4.1-flash` 上游 429
  `retry_after=absent` → 16:44:17 起 lane_reject `operator_review_required`。
- 转储 `error-v1-responses-2026-10-21T004324`（+08）响应信封：CPA 包装形状
  `{"error":{"message":"upstream 429: {\"code\":14003,…模型繁忙…}","type":
  "rate_limit_error","code":"rate_limit_exceeded"}}`——**上游本体是 14003
  模型侧拥塞**（官方文案"请换模型或稍后重试"），wrapper 标签是 HTTP 形状标注。
- 缺陷：`oauth_risk_signal` workbuddy 分支按 `error.get("type") ==
  "rate_limit_error"` + 无 Retry-After（`known_retry=False`）置 durable hold
  （`cpa-admission.py` 原 ：455-462），**wrapper 标签击败了 43c7334 的 header
  层阶梯改动**；既有钉住测试喂的是原始信封形状（`{"code":14003}` 不进该分支）
  故测试绿而生产锁。16:43Z 这发是部署后首波 14003，立即暴露。
- 对照：chatgpt lane 同期 503 `server_is_overloaded` 走服务器退避，属已知容量
  窗透传族，与本缺陷无关。

## 修复（dc5d049）

- 新增 `quoted_upstream_code()`：从 wrapper `message` 提取内嵌上游 code（解析
  失败保持 fail-closed）；workbuddy 分支在 `type=rate_limit_error` 且内嵌
  code==14003 时不置 durable hold（HTTP 层 429 无 Retry-After 仍落 60s 阶梯
  首档自恢复）。裸 `rate_limit_error`（无内嵌 code）与内嵌 6003/6004 仍持锁；
  11140/认证路径不变；claude 分支不动（1dcbb68 定案）。
- 测试：生产包装形状（含 displayMsg 双语）→ `(None, None)` + StreamErrorProbe
  链路 `hold_reason=None`；裸形状/内嵌 6003/6004 仍 `retry_window_review_
  required`。183 passed；full gates 绿（721 passed + 345 subtests / bandit /
  ruff check / format（顶层范围）/ mypy）。
- pin 轮换：`b0e7ec31…` → `19d64361…`（仓内 pin 文件与远端
  `/etc/vps-ssh-launcher/cpa-admission.sha256` 一致）。

## 部署事务（reviewed deploy，outputs/deploy-admission-14003-wrapper-20261021.sh）

- staged 经分块 base64 上传 + `sha256`（==新 pin）+ `py_compile` 双验。
- 事务：maintenance lock → 基线 drift 校验（live==b0e7ec31）→ staged sha 校验
  → healthz 四 lane 空闲 → 备份
  `/opt/cliproxyapi/backups/20261020T170626.392370372Z-admission-14003-wrapper`
  （旧 py+pin，trap 回滚）→ 原子换装+pin 轮换 → integrity check
  `ADMISSION_INTEGRITY_OK` → 重启 → `HEALTH_OK lanes=4` → `OK deployed`。

## hold 释放（人工恢复流程）

- 备份 `/root/workbuddy-risk-backup-20261020T171026Z.json`（原值
  `hold_reason=retry_window_review_required, not_before=1792514664≈17:24:24Z`）。
- `hold_reason→null`；**偏离说明**：`not_before` 一并归零。契约"未到期不得缩短
  not_before"针对 provider 窗口；该值是 admission 对"无期限信号"的自设复核窗，
  而本信号已定案为模型侧拥塞（无 provider 窗口可尊重、面板额度 0 消耗），
  修复已部署使后续同型波只落阶梯，归零不引入上游施压面。

## 验收与运行态

- 8318 回环单发一次：`deepseek-v4.1-flash` → **HTTP 200 / 1.31s / 正文 "ok"**
  （key 远端解析未回显）。
- strict doctor：`DOCTOR_CONTRACT_OK`、`admission-integrity=OK`、13 项投影
  drift 全 MATCH（含 `cpa-admission.py`/`cpa-admission.sha256` 新 generation
  锚定 HEAD dc5d049）、chatgpt/antigravity/workbuddy 三 lane
  `OPEN hold_reason=none`。

## 边界与后续

- 单发成功证明 admission/上游数据面恢复，不证明供应商账号长期状态，不消除
  逆向 OAuth 插件的条款风险。
- `unverified_request`/`retry_window_review_required` 的锁存语义保持不变；
  本次只修正"wrapper 标签 ≠ 账号信号"这一分类输入。
- 用户侧遗留：出事 IDE 会话未加载零重试配置（"exceeded retry limit" 实证），
  建议重启会话使 `request_max_retries=0/stream_max_retries=0` 生效，避免重试
  风暴再次触发 14003 波（11 连发相同 payload 是本波直接前导）。
- 回滚：事务 trap 恢复备份 py+pin 并重启；hold 备份仅在需重新置锁时使用。
