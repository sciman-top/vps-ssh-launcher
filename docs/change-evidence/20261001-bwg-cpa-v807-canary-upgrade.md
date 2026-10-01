# bwg CPA v8.0.7 手动 canary 升级收据（2026-10-01）

## 授权与背景

- 用户当次明确指令：自主升级 CPA 至 v8.0.7 并防范封号/限流/降智风险，按建议连续执行修复与优化（豁免 soak；先例 10/1 v8.0.5、9/25 v7.3.17）。
- 升级前生产：`eceasy/cli-proxy-api:v8.0.5@sha256:a3ffe52b…`（10/1 01:54Z 前 8h 由 canary 部署，restarts=0）。
- updater `--check` 证实 v8.0.7 未满 72h 成熟度（`CANDIDATE current=v8.0.5 target=v8.0.5`，timer 仅空转），故走同款手动 canary，逐条镜像 `cpa-auto-update.sh` apply 语义。

## 变更面审查（风控视角）

v8.0.5→v8.0.7（upstream 5 commits，9/30 发布）：

- **封号指纹面：零改动**——无 UA/请求头/cloaking/刷新节奏/限流变更，无配置格式变更。
- `82f8e92` uTLS client 支持协商 ALPN：旧代码无视协商强制 HTTP/2；新代码按协商结果选 h2 或 http/1.1（同一条 uTLS 连接），**Chrome uTLS 指纹本身不变**。ChatGPT 后端正常协商 h2，本部署走原路径不变；仅新增对不开 h2 的中间盒的兼容，中性偏正。
- `a8ffd5a`+`67cb32b` codex tool 参数整数归一化按目标 executor 判定（后者细化前者）；`9e71c20` 上游流在首个 payload 前断连转 502（error 观测增益，喂给冷却分类更准）；`97f244b` 目标 executor 传入兼容转换与 token 计数（用量记账更准）。三者均为 codex 客户端兼容修复，对本网关 desktop/bot 消费正向。

## 执行（canary 脚本 sha256=a428bca3…，outputs/cpa-v807-canary.sh）

时序（UTC）：预检（config sha `52c295f2` 不变、auth 2 项、磁盘 32G free、备份 16 个健康）→ 严格 doctor `DOCTOR_CONTRACT_OK`（9×MATCH、oauth_monitor=OK、cooldown_state=none、24h 零 5xx 零限流信号）→ pre readiness+generation 双 HEALTH_OK → 备份 `backups/20261001T015403.267274389Z-canary-from-v8.0.5`（700）→ pull `v8.0.7@sha256:35779b5d…`（Docker Hub tag digest）→ compose image 行单点替换（REFUSE≠1 保护）→ `docker compose config --quiet && up -d --pull never` → post generation HEALTH_OK → **CANARY_OK @ 01:54:12Z**。

回滚路径：恢复备份 compose + `up -d --pull never`（v8.0.5 镜像本地保留未 prune）；脚本对 exit 1 自动回滚、对上游侧 503 类（generation exit 10 + readiness OK）按 updater 语义保新。

## 验收

- 容器：`v8.0.7@sha256:35779b5d…`（Commit 97f244b，BuiltAt 9/30T19:15Z）started 01:54:10Z，restarts=0；启动日志 error/fatal/panic 计数=0；config sha 不变；auth 2 项未动（无重登录）。
- 公网双 lane 单发（各消费 1 turn）：`gpt-6-luna`（OAuth lane）200，first_event 904ms，`response.completed` 契约成立，total 2425ms；`glm-5.3-flash`（非 OAuth lane）200，64 events 流动，total 1624ms（completed=false 为 openai-compat 桥接终止事件形状差异，非故障；canary generation 门对该目标 HEALTH_OK 三重佐证）。
- 严格 doctor（升级后）：**DOCTOR_CONTRACT_OK + POLICY_OK**——投影 9×MATCH、容器 restart=0、`oauth_monitor=OK`（days_left=4、7d 零刷新失败，lead24h 自动刷新点 ~10/2）、目录=精确 10 裸名（含 optional gpt-image-2.5）、`cooldown_state=none`、safe-limit/per-ip 限流 OK。
- fixture（v8.0.7 二进制 sha `d546fedb…` 从新镜像提取，netns 沙箱零生产接触，七件套全部 ==HEAD）：**ACCEPTANCE_RESULT=PASS**——过载三阶段+62s 同进程恢复、目录契约（9 裸名+墓碑 fail-closed+`sol_diag=200 gpt-6.1-sol stop`）、updater 四场景（start_fail/model_exposure→exit1+回滚、transient→exit10 保新、success→exit0）；清理后零残留。

## 优化建议处置（allow-remote 加固：以源码证据关闭，不应用）

v8.0.4 轮次遗留的 `remote-management.allow-remote: true -> false` 候选，本轮以参考源码核实判定语义后**决定不应用**：`handler.go` 的 `localClient` 是对 clientIP 的字符串精确匹配（`127.0.0.1`/`::1`），`!localClient && !allowRemote` 即拒绝（参考 checkout handler.go:274,338）。本部署为 docker bridge + `127.0.0.1:8317:8317` 端口映射，宿主侧管理调用的容器侧源地址是网桥网关 IP 而非 `127.0.0.1`——改 false 会切断宿主侧全部管理面调用。"纯纵深防御"的原评估未考虑该 NAT 细节；现状态（loopback-only 绑定 + 无 nginx 管理路由 + admission/fail2ban）已是本拓扑的正确形态，doctor 的 `LOOPBACK_KEYED` 接受态正为此设。

## 观察项

- 今晚 22:05+08 巡检、明日 04:00 UTC timer（将报 current=v8.0.7）。
- OAuth 自动刷新点 ~10/2（days_left=4，doctor lead24h 自动监护；失败才需 device-login）。
- 本轮消耗：luna/glm 各 2 发（pre/post 健康门 + 公网探针各 1）+ fixture 全程零真实凭据零生产消耗。
- 教训（通道类）：fixture 首场景有 62s 静默等待，超过 ssh 工具 60s idle 窗口——长跑脚本必须先 setsid 落盘再轮询；且远端 pkill/pgrep 模式会自匹配调用 shell 自身 cmdline，需用不重叠的模式或事后核对状态。
