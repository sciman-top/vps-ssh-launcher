# 2026-11-12 bwg CPA 退休路由、更新器与风险审计收尾

## 范围

- 仅处理 bwg CPA 与本地审计器；不修改或重启 zz。
- 保留既有 OAuth 账号、API key、并发/重试、冷却与 hold；未轮换凭据或清除状态。
- slot 3 明文 HTTP 仍是用户已接受的传输风险。

## 代码与本地验证

- `4fefcd1` 把 `gpt-6.1-sol-input` 从 slot 1 活跃宣告面移除，同时保留为
  `retired_models` 与两个排除集中的墓碑，防止意外复活；`gpt-6-astra` 等已有路由不变。
- `e857757` 覆盖既有分块 SSH 传输的字节重建与 Win32 大小边界，没有改变凭据或远端策略。
- `56b672c` 在缺失来源归档年龄记录时保持保守重建窗口，并修正探测锁路径文档。
- `4f507e1` 不让已退休名字的历史失败再被算成活跃路由告警；每条仍在宣告路由上的
  警告及其 accepted-risk 边界继续保留。
- 最终全量门禁：`1285 passed, 21 skipped, 627 subtests`；此后的审计器切片
  完整 pytest 为 `1286 passed, 21 skipped, 627 subtests`，bandit、ruff、format、
  mypy（108 个源文件）均通过。跳过项有平台原因；连接真实 SSH 的专项测试默认
  opt-in，实际 BWG 操作由下述 doctor 和请求回执验证。

## BWG 投影与回执

- 推送后由 `scripts/cpa_bwg_guardrails.ps1 -Profile bwg -Apply` 写入 BWG。
- 备份：`/root/cpa-guardrails-backup-20261111T160154.196113842Z`。
- Apply 输出 `GUARDRAILS_APPLIED`；16 项写入后有 `PROJECTION_HASH_VERIFIED`，
  后续 doctor 的受管投影全部 `MATCH`。
- 远端更新器 LF SHA-256：
  `86c09677cf9830d0a5aef543157a5bc673d49cab82ce52cd5ecb2a221d4ae830`。
- admission SHA/pin 均为
  `c410187f7975131630bcdb0c7e2bc4f045df1fb5fc9165a59b1bd1867fda6ea5`；
  health runtime 已提交并部署。容器为 `v8.0.61-gemini-raw`、running、
  restart=0；8317/8318/8443 监听正常。
- 最终只读 doctor：`DOCTOR_CONTRACT_OK`；`/healthz` 为 ok，WorkBuddy 无 hold、
  无冷却，单账号 inflight 上限保持 `1`。更新器 `--check` 为 exit 0，
  当前与目标均为 v8.0.61，未触发升级。
- Apply 期间曾短暂连接不到 8318；服务恢复后，首次只读检查把 `/v1/models`
  发到 admission 端口得到预期 401，改走带 key 的 CPA loopback 后得到 200，
  不属于部署失败。

## 受控与自然流量边界

- 单请求、零重试的 WorkBuddy Responses 冒烟：HTTP 200、
  `response.completed`、`pong`、无容量标记。另一个较长 SSE 为 HTTP 200、
  448 events、`response.completed`、2443 字符。
- 经 BWG 的公共随机路径完成单次 OpenAI chat-completions 流：HTTP 200、
  `[DONE]`、`PONG`。这是受控 replay，不是新号/新客户端验收。
- Apply 后 `cpa_gateway.access.log` 中 POST `200/200` 为 508 条；其中
  WorkBuddy admission journal 的自然完成回合为 375 条，未出现容量标记。
  日志中另有 1 次 `503/503` 和 1 次启动后早期 `500/auth_status=502`；
  未重发或掩盖失败，故持续自然验收保持 `PARTIAL`。
- 本机 Cockpit 日志最新行已陈旧，且审计基线为 `fail=0 warn=5`：
  `lane-cooldown-server-cap`（有意遵循上游退避）、`plaintext-http-provider`
  （用户已接受）、`posture-data-stale`，以及 `gpt-5.6-luna` 与
  `gpt-6-luna` 两条本机旧数据路由警告。BWG 当前健康须以 doctor 与 journal 判读。
- 用户此前遇到的 WorkBuddy `cooling down or busy` 提示是共享单在途容量窗口，
  不是本次投影故障。之后的无状态冒烟成功并未证明每个真实客户端都恢复。

## 风险与回滚

- 退休路由不得因清理墓碑、槽位清单回退或文档样板而被重新宣告；
  `retired_models`、`oauth_exclusions`、`codex_api_key_exclusions` 保持一致。
- 投影回滚使用 `4fefcd1` 之前的已审查提交重跑 `-Apply`，并利用上述备份目录；
  回滚 Git 不能替代远端恢复。没有配置凭据、账号、冷却或 hold 状态变更。
- 下一步只需从用户真实客户端再走一个非探测自然请求，绑定请求结果与 BWG
  journal；若仍报 capacity，应记录为上游等待窗口，不调高 retry 或并发。
