# 2026-11-07 BWG CPA：WorkBuddy 6008 计费误分类事件与持扣释放

## 事件与根因

- 2026-11-06T16:57:16Z `lane_hold_latched lane=workbuddy-oauth
  reason=retry_window_review_required previous=none source=response_signal`。
- 上游信号是 WorkBuddy 业务码 **6008 "Credits exhausted"**（单一凭据的计费
  状态）。当时在役的 admission 生成（1703ba4f）存在两处缺陷：
  1. `protocol_errors` 只解包 11140/6003/6004，对裸 `{"code":6008,"msg":…}`
     与 CPA 的 `upstream 429: {json}` prose 包装均不可见；
  2. 事件落入通用 `retry_window_review_required` 兜底（宣称一个不存在的
     截止时间），并把**整条车道**连同零成本池化模型 `hy4free`
     （x0.00 credits）一起闸死——一个凭据的计费状态删掉了全部模型。
- 持扣为持久态，`not_before` 早已过期仍不自清：旧分类器下新请求再次 6008
  会原样再锁，自续死锁。

## 修复（commit 78758ea，pin 1703ba4f → 541e5b9e）

- `protocol_errors`：prose 包装中内嵌 provider code 上浮到 `message` 字段；
  裸 6008（`{code,msg}` 形）与 11140 族同样上浮。
- `oauth_risk_signal`：workbuddy 6008 → `account_credits_exhausted`
  （无重试截止），与 missing-deadline hold 分列上报。
- `LaneState`：零成本模型（`ZERO_COST_MODELS={hy4free}`）豁免该凭据级计费
  持扣；其余 hold 语义保持车道级不变。
- 测试：`tests/test_cpa_admission_credits.py` 12 例钉定分类与收窄范围。

## 生产事务

1. **修复部署**（2026-11-06T18:00–18:01Z，被中断的前一会话以其自建事务
   `outputs/deploy-admission-credits-6008-20261107.ps1` 执行，本会话收编该
   脚本并补 `-ForceRestart` 拒绝与 drain 契约测试）：
   - 维护锁 + `requests.lock` 排他 drain（带 `DRAIN_WAIT` 有界等待）、
     admission.py 与完整性 pin 原子同转、备份
     `20261106T180001.866927507Z-admission-credits-6008-20261107`。
   - 部署后代合约证明 `CREDITS_6008_CONTRACT_OK`（6008→billing 分类、
     14003 不误分类、hy4free 豁免、6004 cap=300 幸存）。
   - 回执 `DEPLOY_OK pin=541e5b9e…`。
2. **stale 持扣释放**（2026-11-06T18:24Z，
   `outputs/release-workbuddy-hold-20261107.ps1`，本会话新写）：
   - 精确 reason 断言（`retry_window_review_required`）→ 备份
     `/root/workbuddy-risk-backup-20261106T182427.875326485Z.json` →
     `requests.lock` 排他 + 四车道 idle → `hold_reason=null` 原子写 →
     仅重启 admission。
   - 按现行 cpa-gateway.md drain 契约执行（排他 flock + idle 断言 +
     lane-hold 差分），替代退役的入口 503 盾，避免全场 503 窗。
   - 回执 `RELEASE_OK lane=workbuddy-oauth cleared=retry_window_review_required`、
     `LANE_HOLD_DELTA=none`、`ADMISSION_INTEGRITY_OK`。

## 验收

- strict doctor：三 OAuth 车道 `OPEN hold_reason=none`、`lane-hold=OK`、
  `workbuddy-version=0.9.87`、`MODEL_IDS_UNKNOWN=none`、13 项投影 MATCH、
  `DOCTOR_CONTRACT_OK`。
- 受控单发：hy4free 流式 PONG `PRODUCTION_STATUS=200`、63 SSE events、
  `TERMINAL=true`、`PRODUCTION_HY4FREE_ACCEPTANCE=PASS`。
- 后续宿主换芯（同日 v8.0.58）在 WB 车道 OPEN 态下完成，见
  [同日宿主台账](20261107-bwg-cpa-v8058-host-swap.md)。

## 边界

- 不声称被清凭据的额度已恢复或 WorkBuddy 官方认可本接入方式；被清凭据
  按 `account_credits_exhausted` 语义待充值/耗尽面处置，其余模型由池内
  其余凭据继续服务。agent 未读取、复制或改动任何凭据文件。
