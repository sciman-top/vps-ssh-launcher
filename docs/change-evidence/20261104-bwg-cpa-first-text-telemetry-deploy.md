# BWG CPA 首个可见文本遥测与受控部署（2026-11-04）

## 目标与边界

本次把 admission 的 `first_text_ms` 遥测投影到 BWG 生产，用于区分准入排队、
上游首字节和第一个非空可见文本的延迟。遥测只记录数值和分类，不记录响应正文；
不改变路由、凭据选择、并发、RPM、重试、cooldown、缓存键或成功判定。

用户要求保留现有 OAuth 账号和 API key 渠道。本次未删除、注销、轮换或隔离任何
凭据；Claude 继续保留惰性退役墓碑，slot 3 明文 HTTP API key 继续存在并维持告警。

## 仓库与外置版本

- CPA 镜像仍为 `v8.0.53`，运行 digest 与本次前一致；WorkBuddy 插件仍为
  `0.9.84`。
- 遥测实现提交：`ef7eae9`。
- 受审查 admission-only 部署事务：`6529332`，脚本为
  `outputs/deploy-admission-first-text-20261104.ps1`。
- 本地 `cpa-admission.py` 与 pin 的 LF-normalized SHA-256：
  `ba1fa5db12089c0d4f2d9af72a8673b5fcbbb1c1c474f501b1cf3d205e914f56`。

## 受审查远端事务

事务先取得 `/run/vps-ssh-launcher-maintenance.lock`，再对
`/run/cpa-admission/requests.lock` 取排他锁并复验四条 lane 的
`inflight=0/pending=0`。写入前备份 admission 源、完整性 pin，以及
`systemctl cat/show`、enabled/active 状态；随后以临时文件 + `mv` 原子替换源和
pin，仅重启 `cpa-admission`，不重启 CPA 容器。

`-DryRun` 结果：

```text
ADMISSION_IDLE_OK
ADMISSION_INTEGRITY_OK
DRY_RUN_OK baseline=df8d136fb9bcc873379d626db9b47338740c6e420c85b92c9443b9618ee4afb3 candidate=ba1fa5db12089c0d4f2d9af72a8673b5fcbbb1c1c474f501b1cf3d205e914f56 idle=true no_swap=true
```

正式事务结果：

```text
REQUEST_GUARD_EXCLUSIVE_OK
ADMISSION_IDLE_OK
ADMISSION_INTEGRITY_OK
ADMISSION_REGRESSION_GUARD_OK
DEPLOY_OK pin=ba1fa5db12089c0d4f2d9af72a8673b5fcbbb1c1c474f501b1cf3d205e914f56
```

远端回滚材料位于：
`/opt/cliproxyapi/backups/20261104T010438.678046090Z-admission-first-text-20261104`。
回滚时在同两把锁内恢复其中的 `cpa-admission.py` 与 `cpa-admission.sha256`，
重启 admission，重新运行完整性检查和 strict doctor；Git 回滚不能代替远端恢复。

## 配置投影与凭据保留

常规 `cpa_bwg_guardrails.ps1 -Apply` 在 admission 代际已对齐后完成：

```text
GUARDRAILS_APPLIED
READY_STATUS=200
```

随后 strict doctor 报告 `DOCTOR_CONTRACT_OK`，admission、配置、路由、Nginx、
fail2ban 和 systemd 投影均 `MATCH`，新 admission pin 为上述 `ba1fa5db…`。

API key 渠道用 Apply 前备份的 `config.yaml` 做字段级脱敏比对，结果为：

```text
API_KEY_CHANNEL_BYTES_UNCHANGED=YES count=5 fingerprint=21b9c5254be893a7
```

该 fingerprint 只用于本次比对，不是凭据值。

远端仍有 14 个 OAuth JSON 文件。与 2026-11-03 v8.0.53 备份保存的哈希清单相比，
6 个文件字节不同（2 个 Antigravity、4 个 WorkBuddy），其余 8 个逐字节一致。
备份仅保存哈希、没有旧文件副本，无法从中恢复字段级差异；本次 admission 和
guardrails 写集均不包含 `auth/`，因此没有回滚或覆盖这些 provider-managed
变化。当前 doctor 仍报告 Antigravity 两个活动文件的既有提醒，凭据全部保留。

## 运行态与受控验收

doctor 关键结果：

- `chatgpt-oauth`、`antigravity-oauth`、`workbuddy-oauth`：`OPEN hold_reason=none`。
- Claude：`RETIRED no_oauth_route`，墓碑惰性且不参与流量。
- `cooldown_state=none`；`insecure_http_providers=slot=3 ...` 仍为持续告警。
- `workbuddy-realm-order=INTL_THEN_CN`，`deepseek-provider-order=WORKBUDDY_THEN_OPENCODE`。
- `model_substitution_unknown_pairs=0`，现有唯一记录为已知名称变体。
- `first_text_ms n=10 p50=15293 p90=23536 max=23536`；观测样本仍小，
  不能外推长期账号质量或模型吞吐。

低成本受控单发（每项一发、无重试、服务端读取 key、无响应正文回显）：

```text
CONTROLLED_ACCEPT_GLM=PASS
CONTROLLED_ACCEPT_workbuddy-oauth=PASS HTTP=200 latency=1424ms events=10 completed=true
CONTROLLED_ACCEPT_antigravity-oauth=PASS HTTP=200 latency=3210ms events=9 completed=true
```

本次没有为验收主动消费 ChatGPT OAuth 配额。自然 24h journal 仍显示 ChatGPT
容量/上游 502/503 和少量客户端断流；10 次 lane reject 均为 ChatGPT model
cooldown 镜像。它们是 provider/自然流量信号，不构成本地账号封禁结论，因此没有
提高并发、增加重试、清 cooldown 或轮换账号。

## 证据分层与后续观察

- `repo_verified`：`ef7eae9` + `6529332`，本次完整门禁已通过（1094 passed，18 skipped，574 subtests）。
- `filesystem_projected`：本地 pin、脚本、回归测试和 change-evidence 已入库。
- `host_loaded`：远端 `DEPLOY_OK`、`GUARDRAILS_APPLIED`、`READY_STATUS=200`、
  strict doctor `DOCTOR_CONTRACT_OK`。
- `live_accepted`：上述三项受控单发通过；自然桌面完整回合、长期账号安全、
  provider 配额和中国网络稳定性仍需自然窗口观察，不能由本次短窗外推。
