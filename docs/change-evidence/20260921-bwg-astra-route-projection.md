# 2026-09-21 BWG 裸名路由与 Astra 投影验收

## Scope

- 仅处理 `bwg`；未连接或修改 `zz`，未推送 Git。
- 目标拓扑：`gpt-5.6-luna` → ChatGPT Plus OAuth；
  `gpt-5.6-sol` / `gpt-5.6-terra` / `gpt-6-astra` → `ai.input.im`；
  `glm-5.3-flash` → `open.bigmodel.cn/api/coding/paas/v4`；
  `deepseek-flash` → `api.deepseek.com`。
- 未修改 OAuth 凭据内容、账号数量、客户端密钥、出口、UA、TLS/header
  覆写、公网 Nginx 随机路径或 CPA loopback 数据面。

## Repository verification

- 实现提交：`a128b0b`（同步 BWG 裸名路由与 Astra 健康契约）。
- 同步更新 `cpa_bwg_guardrails.ps1`、`cpa-health.py`、`cpa_policy.py`、
  `test_scripts.py` 和当前 README 拓扑说明。
- Full gate：`131 passed, 1 skipped, 164 subtests`；Bandit、Ruff、Ruff
  format、Mypy 全部通过。
- 受影响 focused suite：`11 passed, 32 subtests`。

## Projection

- Backup-first apply 成功：`GUARDRAILS_APPLIED`，短暂重启窗口出现一次预期
  `curl: (56) Recv failure: Connection reset by peer`，随后 `READY_STATUS=200`。
- 最新远端备份：`/root/cpa-guardrails-backup-20260920T231835.897051257Z`。
- 投影后的 `cpa-health.py` SHA-256：
  `242eceef8a25d5ddad079f0f6471222c1b906beba9f6dd369f94c7e768b50b28`。
- 投影后的 `cpa_policy.py` SHA-256：
  `0961e1a7261e01bbcb22453551cc006cf1e9944664b64474dd0f18e009682a9f`。
- apply 后目录摘要为 6 个模型，且 `has_ai_input_im_bare_astra=True`。
  配置文件哈希与 apply 前相同，说明 Astra 声明此前已由并发修改先行落盘；
  本次 apply 将源代码、policy 和所有契约统一投影并重新验证。

## Host readback

- Fresh strict doctor：`DOCTOR_CONTRACT_OK`。
- 容器 `running`、restart=0；CPA `127.0.0.1:8317`；Nginx
  `0.0.0.0:8443`；IPv6 公网和 CPA listener 均 absent。
- 公网合同保持 `valid_path_unauth=401`、`bare_path=404`、`wrong_path=404`。
- `semantic-policy=OK`、`management-remote=LOOPBACK_KEYED`、
  `nginx-no-management-route=OK`、`oauth_monitor=OK`。
- `request-retry=0`、`max-retry-credentials=1`、`save-cooldown-status=false`、
  `session-affinity-subagents=false`。

## Controlled live evidence

- 第一次 `generation-all` 因已有 probe lock 返回 `PROBE_ALREADY_RUNNING`，未发出
  provider 请求；等待锁释放后只启动一次矩阵。
- 矩阵进程在 SSH 端 60 秒无输出而超时，未形成完整脚本 exit receipt，因此不把
  整体矩阵标为 `live_accepted`。
- 在该矩阵窗口的 CPA 日志中，`gpt-6-astra` 被明确选到
  `openai-compatibility:ai.input.im`，并返回 `200`，耗时 `5.816s`；同一窗口
  的 Sol、Terra、GLM、DeepSeek chat 路由也观察到 200。该证据证明 Astra
  的路由选择和一次真实响应，但不外推长期质量、账号免风控或整矩阵完成。
- 随后观察到另一个 Astra 请求以 499 结束；它来自同一数据面但无法仅凭脱敏日志
  证明属于本次矩阵，因此保留为客户端中断残余风险，不作为路由失败结论。

## Evidence boundary and rollback

- `repo_verified=yes`、`filesystem_projected=yes`、`host_loaded=yes`。
- `live_accepted`：Astra 单路由一次真实 `200` 证据；整套 generation-all
  仍为未闭合，不宣称完整矩阵验收或模型质量验收。
- VPS 回滚使用本次备份目录并重新执行 strict doctor；仓库回滚使用
  `git revert a128b0b` 后再投影。Git 回滚不能替代 VPS 备份恢复。
