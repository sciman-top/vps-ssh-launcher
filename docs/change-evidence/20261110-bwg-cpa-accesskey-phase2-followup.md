# bwg CPA 访问键 phase 2 跟进回执 — 2026-11-10

> 本回执只记录本次会话的脱敏事实。访问键值、远端主机地址、密码、OAuth
> token、随机能力路径和完整请求头均不写入仓库。

## 目的与范围

- 目标机：`bwg`；只处理 CPA 网关访问键和本地活动客户端配置。
- `http-bridge-8003` 按用户要求保留，未删除、改名或退役；它仍是明文 HTTP
  的已接受传输风险，后续审计继续单独标示。
- OAuth 账号、上游 API key、出口 IP、UA、模型并发、重试次数和冷却状态均未
  轮换、清除或放宽。

## 远端 phase 2

1. stage 1 先把 `access.api-keys` 从单键扩成双键，并保留配置备份：
   `/opt/cliproxyapi/backups/20261109T174229-accesskey-add-stage1/config.yaml`。
2. phase 2 备份双键配置后收敛为单个新键：
   `/opt/cliproxyapi/backups/20261109T175347-accesskey-remove-phase2-20261110/config.yaml`。
3. `cpa_policy.py` 返回 `POLICY_OK`；容器重启后 readiness 返回 `HEALTH_OK`。
4. 本机 8317 探针结果：`ACTIVE_KEY_COUNT=1`、`ACTIVE_KEY_PROBE=200`、
   `OLD_KEY_PROBE=401`；`/healthz` 返回 `200`。
5. 远端 staging keyfile 已删除。回滚入口是 phase 2 备份恢复后重新执行
   policy/readiness 校验；不以 Git 回滚替代远端恢复。

## 本地活动配置卫生

- stage 1 传播后复扫发现
  `C:\Users\sciman\.cockpit_tools\codex_model_providers.json` 仍有 1 处旧键
  （运行中客户端回写造成的竞态）。已先写入同目录
  `codex_model_providers.json.bak-keyrot-repair-20261110-030847`，再替换并
  解析 JSON。
- 复扫结果：`ACTIVE_FILES_PRESENT=5`、`ACTIVE_KEY_LITERAL_COUNT=8`、
  `STALE_OLD_KEY_HITS=0`。没有重启 Codex、Cockpit 或 Claude 进程；内存中的
  旧键由网关 `401` 失效语义自然收敛。

## 风控与性能判定

- 不把历史 `gpt-6-luna` 容量失败解释成全局封号或“降智”；现有 admission
  模型级 cooldown、`request-retry=0`、`max-retry-credentials=3` 和 lane
  并发上限继续生效。
- 最近一小时 telemetry 的 cache 命中率与 first-text 延迟仍以模型/上游为主：
  WorkBuddy `deepseek-v4.1-flash` 约 `99.94%` / `3.6s` p50，ChatGPT
  `gpt-6-luna` 约 `97.06%` / `11.3s` p50，`gpt-6.1-sol` 约 `95.01%` /
  `3.8s` p50。没有证据支持通过提高并发、清冷却、压缩 prompt 或替换模型
  来“优化”它们。
- 两次 cache canary（chat、responses）第二次命中率分别约 `0.9956`、
  `0.9959`，均以 `HEALTH_OK` 收口；缓存不是当前瓶颈。
- phase 2 重启后的 fresh `/healthz` 快照为 `status=ok`，四条 lane 均为
  `inflight=0/pending=0/cooldown=none`；当前内存窗口中
  `gpt-6-luna` 为 `cache_hit_ratio=0.9535`、first-text p50 `13591ms`
  （45 samples），`gpt-6.1-sol` 为 `0.9567`、`4856ms`（27 samples），
  WorkBuddy `deepseek-v4.1-flash` 暂无样本。重启会清空滚动内存窗口，因此
  空样本不能解释为降智或封号。
- 随后的只读 strict doctor 返回 `DOCTOR_CONTRACT_OK`；WorkBuddy 版本为
  `0.9.89`，三条 7d `6008` 信号分别落在三个 INTL credential 上。doctor
  同时报告 `invalid_grant=0`、`TOS_VIOLATION=0`、`PERMISSION_DENIED=0`、
  `11140=0`、`insufficient_quota=0`，所以当前证据是额度/积分耗尽信号，
  不是 OAuth 封号信号。Antigravity 保留两份活动凭据，刷新失败为 0；这与
  用户要求保留多账号一致，继续观察而不删除或合并。
- Gemini 的 295 次 `gemini-3.8-flash-n` 回显均对应请求的
  `gemini-3.8-flash-high`，distinct pair=1、unknown pair=0，doctor 将其
  标为 `KNOWN_NAME_VARIANT`；这是上游名称回显差异，不足以证明降智。
- strict doctor 的 24h latency aggregate 显示 admission 等待 p50/p90 为
  `0/2ms`，upstream header p50/p90 为 `2176/4207ms`；可测的 149 个
  first-text 样本 p50/p90 为 `8268/44143ms`，其中真正有文本的 135 个样本
  为 `8489/32132ms`。这把“吐字慢”的主要归因指向上游首包/思考和客户端
  断开，而不是本地队列；first-text 可测占比仅 4%，工具调用、reasoning-only
  和空文本回合没有被伪造为文本延迟。
- fail2ban `cpa-gateway` fresh status 为 `Currently banned: 0`、
  `Total banned: 1`；这是网关边界的历史封禁计数，不是上游 OAuth 账号封禁，
  且 loopback 已在 jail 中豁免。

## 证据层级与边界

- `repo_verified`：本文件、现有 admission/policy/telemetry 代码和门禁脚本。
- `host_loaded`：本回执中的远端 policy、readiness、key probe、healthz 与备份
  路径均为当次 SSH fresh read。
- `live_accepted`：本次只做网关鉴权与健康探针；未把它外推为所有 OAuth 账号
  的长期自然流量验收。账号级封禁、上游配额和 provider 风控仍需持续观察。
