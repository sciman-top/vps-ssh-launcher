# 2026-10-18 BWG CPA v8.0.32 canary 升级证据

用户当次授权直接升级 bwg VPS 的 CLIProxyAPI 至官方 v8.0.32，并完成风控、流式节奏和缓存命中检查。官方 release 发布于 2026-10-16T18:57:27Z；Docker Hub digest 为 sha256:68f6e2d691f7b71aca21cfe87c77b7076cbea26c8ef490b15619b1623a7c0699。因仍在 updater 的 72 小时双源成熟窗口内，按指定版本采用手动 canary。

上游变更涉及 Claude 工具参数规范化、credential 切换时 session date pin、thinking block 绑定和 Devin 配额 cooldown。保留现有 request-retry=0、max-retry-credentials=1、session affinity、15 秒流式 keepalive；不提高并发、不清冷却、不额外触发 OAuth 探针。升级前 admission 24h 有 ChatGPT OAuth 排队拒绝及一次 gpt-6-luna capacity 429，升级不被当作配额修复。

## 事务与回滚

- 脚本：outputs/deploy-cpa-v8032-canary-20261018.sh；LF SHA-256 38e0205115b6cc9fbe5721986c66313319e956d53f2719c2946a2829bbb56d9a。远端上传 sha256sum -c 与 bash -n 通过。
- 预检 admission healthy、所有 lane inflight=0,pending=0，旧 pin 精确匹配，CPA_HEALTH_NO_OAUTH=1 generation 为 HEALTH_OK，旧镜像可回滚。
- 备份 /opt/cliproxyapi/backups/20261017T175001.240847164Z-from-v8.0.31-canary-v8.0.32/compose.yml；仅原子替换 Compose image 行。结果 OK: updated v8.0.31 -> v8.0.32，退出 0，未回滚。

## 验证

- 容器 CLIProxyAPI Version: v8.0.32, Commit: 911f49c，目标 digest running、restart=0；generation/readiness 均 HEALTH_OK。
- 严格 doctor DOCTOR_CONTRACT_OK，CPA loopback、唯一公网 Nginx/random path、admission integrity、投影哈希、策略、权限、Nginx syntax 均通过；配置哈希未变。
- 非 OAuth glm-5.3-flash cache canary：样本一 0/4114，样本二 4096/4114 = 0.9956，与基线一致。
- 非 OAuth deepseek-v4.1-flash stream：HTTP 200，TTFT 2.024s，45 data frames，最大帧间隔 0.057s，[DONE] 存在，无 >5s 或 >15s 间隔。
- quality-canary 启用 CPA_HEALTH_NO_OAUTH=1，gpt-6-astra 首探针 120.034s 网络不可用后 exit 10 fail-fast，未重试或绕路；近 10 分钟日志无 timeout、credential refresh failure、substitution 或 5xx markers。该通道故障及上游容量风险仍未被升级消除。

## 分层结论

| 层级 | 结论 |
| --- | --- |
| repo_verified | release、digest、事务脚本哈希与语法核对通过。 |
| filesystem_projected | 仅 Compose image pin 改变，远端配置 hash 未变。 |
| host_loaded | v8.0.32 digest 运行，restart=0，generation/readiness/doctor 通过。 |
| controlled_live_replay | cache 与 stream 非 OAuth 采样通过；quality 矩阵因首模型网络不可用 fail-fast 未完成。 |
| natural_live_accepted | 未宣称；未制造 OAuth/桌面自然会话，长期额度、封号风险和上游稳定性仍需自然流量观察。 |

回滚入口：恢复上述备份 Compose，以旧 digest 执行 docker compose up -d --pull never，再跑严格 doctor/readiness。本次未回滚。
