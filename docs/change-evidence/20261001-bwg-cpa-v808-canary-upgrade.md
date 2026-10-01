# bwg CPA v8.0.8 手动 canary 升级收据（2026-10-01）

## 授权与背景

- 用户当次明确指令：继续直接升级 CPA 至 v8.0.8（同日第三跳：v8.0.5→v8.0.7→v8.0.8；前序收据 [20261001-bwg-cpa-v807-canary-upgrade.md](20261001-bwg-cpa-v807-canary-upgrade.md)）。
- 升级前生产：`eceasy/cli-proxy-api:v8.0.7@sha256:35779b5d…`（同日 01:54Z canary 部署，restarts=0）。
- updater `--check` 证实 v8.0.8 未满 72h 成熟度（`CANDIDATE current=v8.0.7 target=v8.0.7`），走同款手动 canary。执行时点 05:09Z，距今晚 22:00+08 月度维护首跑约 9h，距每日 04:00Z timer 无冲突（当日已空转）。

## 变更面审查（风控视角）

v8.0.7→v8.0.8（upstream 2 commits，发布 2026-10-01T03:33:40Z）：

- `b467a83` fix(xai): bump pinned grok client version to 1.0.44 for chat-proxy（PR #6252，merge `fd48ea6`）。
- **本网关零影响面**：改动仅触及 xAI/Grok chat-proxy 执行器的客户端版本 pin；本网关目录 10 裸名（luna/sol 系/astra/ciii/glm/deepseek/image）不含任何 xai 模型，OAuth/Codex/ChatGPT lane、请求头、UA cloak、刷新节奏、限流面全部零改动。风险等级：重建二进制本身即全部差异。

## 执行（canary 脚本 sha256=b0e1cfff…，outputs/cpa-v808-canary.sh）

时序（UTC）：预检（config sha `52c295f2` 不变、auth 2 项、v8.0.7 restarts=0、backups=17 健康、digest `sha256:7d7203c0…` 自 Docker Hub 解析）→ pre readiness+generation 双 HEALTH_OK → 备份 `backups/20261001T050915.810557581Z-canary-from-v8.0.7`（700）→ pull `v8.0.8@sha256:7d7203c0…` → compose image 行单点替换（REFUSE≠1 保护）→ `docker compose config --quiet && up -d --pull never` → post generation HEALTH_OK → **CANARY_OK @ 05:09:22Z**。

回滚路径：恢复备份 compose + `up -d --pull never`（v8.0.7 镜像本地保留未 prune）；脚本对 exit 1 自动回滚、上游侧 503 类按 updater 语义保新。

## 验收

- 容器：`v8.0.8@sha256:7d7203c0…`（Commit `fd48ea6`，BuiltAt 2026-10-01T03:34:04Z）started 05:09:21Z，restarts=0；启动日志 error/fatal/panic 计数=0；config sha 不变；auth 2 项未动。
- 公网双 lane 单发（各消费 1 turn）：`gpt-6-luna`（OAuth lane）200、`response.completed` 契约成立、first_event 1295ms、total 2275ms；`glm-5.3-flash`（非 OAuth lane）200、60 events、total 1116ms（completed=false 为桥接终止事件形状差异，同 v8.0.7 口径）。
- 严格 doctor（升级后）：**DOCTOR_CONTRACT_OK + POLICY_OK**——投影 9×MATCH、`oauth_monitor=OK`（days_left=4、7d 零刷新失败）、`MODEL_IDS_UNKNOWN=none`、目录精确 10 裸名、`cooldown_state=none`、24h 零 5xx 零 429（retry_after 全 absent）。
- fixture（v8.0.8 二进制 sha `05872ff4…` 从新镜像提取，netns 沙箱零生产接触，七件套全部 ==HEAD）：**ACCEPTANCE_RESULT=PASS**——过载三阶段+62s 同进程恢复、目录契约（9 裸名+墓碑 fail-closed+`sol_diag=200 gpt-6.1-sol stop`）、updater 四场景（start_fail/model_exposure→exit1+回滚、transient→exit10 保新、success→exit0）、清理零残留。

## 执行过程偏差记录（不影响结果）

- 首次 fixture runner 因 sed 模式遗漏（`cpa-acceptance-v807.py`→`v808` 未替换）在 mv 处退出，残留部分 fixture 目录已清理后以修正版重启；canary/验收主链无偏差。
- 本地 Xray 代理当日不可用（git push/WebFetch 走 127.0.0.1 代理被拒）：release 取证、digest 解析改由 bwg 侧 GitHub/Docker Hub API 完成，升级主链不受影响；仓库推送待代理恢复后重试。

## 观察项

- 观察面以机器调度为准（22:05 每日巡检不存在，bwg cron.d 仅 4 项）：每日 04:00 UTC update timer（将报 current=v8.0.8）、今晚 22:00+08 月度维护首跑（wrapper 内建 docker 快照复验/显式拉起，docker-ce 升级致 CPA 容器短暂重启属预期）、周五 22:20+08 内核周更。
- OAuth 自动刷新点 ~10/2（days_left=4，doctor lead24h 自动监护）。
- 本轮消耗：luna/glm 各 2 发（pre/post 健康门 + 公网探针各 1）；fixture 零真实凭据零生产消耗。
