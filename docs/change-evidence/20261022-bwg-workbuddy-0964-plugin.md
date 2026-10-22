# 2026-10-22 BWG WorkBuddy 插件 0.9.64 换装

**范围**: 仅 `bwg`。未连接或改动 `zz`；未轮换任何 OAuth 凭据、未改 IP/UA/并发/重试策略；CPA 镜像保持 `v8.0.38` 不变；未发送任何 OAuth 生成探针。

## 换装前状态

- CPA 运行 `eceasy/cli-proxy-api:v8.0.38@sha256:113a70240fb48f5c6fd674c9de40b1af4e7ff629f7acc6a412c6014ab1922daf`，`StartedAt=2026-10-21T16:15:17Z`，`RestartCount=0`。
- 在役插件 `0.9.62-local`，`workbuddy.so` SHA-256 `94a1c82550fc5ef2e3ee6098847f7efdb1e9292b93152906f0bc40c716194929`。
- 官方最新 CPA release 仍为 `v8.0.38`，无镜像升级动作。
- 上游插件最新 release `v0.8.78`（2026-10-22 06:47:03Z），其 WorkBuddy `VERSION=0.9.64`。0.9.63 将 11128 触发短语清洗扩展到全部角色（user/assistant 历史、`tool_calls` arguments、reasoning mirrors）；0.9.64 仅修正注册元数据里的错误 GitHub 仓库地址。

## 隔离构建

- 构建脚本：`outputs/build-workbuddy-0964-20261022.sh` / `.ps1`（经 `bwg` profile 执行，`golang:1.26-bookworm`、`--cpus=1 --memory=768m`、无生产挂载）。
- 目录 `/root/cpa-workbuddy-upgrade-20261022`，源码树 `cpa-multi-plugins-0.8.78`。
- 源码 tarball SHA-256 `feb582d5ac7307b275f1162285774e531cf4b5bd9506e21d8eb212cc16fe0998`。
- 补丁：`scripts/patches/workbuddy-v0.8.68-selected-account.patch`、`scripts/patches/workbuddy-v0.8.68-account-failover.patch`，按序干净应用。
- `go test ./...`：`ok 8.548s`。
- 产物 `workbuddy.so` SHA-256 `8d531f30705988256f75cf86082e53acd0879ca8d453b862c718a469228d79e6`，12,018,712 字节。

## 换装事务

- 脚本：`outputs/install-workbuddy-0964-20261022.sh` / `.ps1`（经 `bwg` profile 执行）。
- 排空：以 admission 的 `/run/cpa-admission/requests.lock` 排它 `flock -w 60` 为准，而非 healthz 空闲快照；取得后再次校验 `traffic_guard` 指向同一锁。
- 换装前 lane hold 差分基线 `PRE_HOLDS` 为空（磁盘上的 `unverified_request` 是 `inflight>0` 的写前标记，运行中 healthz 判为 OPEN）。
- 容器依赖检查 `LDD_OK`；备份 `/opt/cliproxyapi/backups/20261022T123315.481884659Z-plugin-0962-to-0964/workbuddy.so`（旧 `94a1c825…`）。
- 原子 `install` + `mv` 替换，`docker compose restart cli-proxy-api`，注册断言 `version=0.9.64`，`health readiness` = `HEALTH_OK`。
- 换装后 `POST_HOLDS` 无新增 hold，事务返回 0。

## 换装后验证

- 插件文件 SHA-256 `8d531f30…`，12,018,712 字节，`root:root 755`。
- 容器 `StartedAt=2026-10-22T12:33:15Z`；日志 `plugin registered plugin_id=workbuddy plugin_name=workbuddy version=0.9.64`。
- `cpa-health.py readiness` = `HEALTH_OK`；strict doctor = `DOCTOR_CONTRACT_OK`。
- lane：`chatgpt-oauth=OPEN`、`workbuddy-oauth=OPEN`、`antigravity-oauth=OPEN`；`claude-oauth=HELD reason=authentication_required` 为未配置 lane 惰性墓碑，不参与门禁。
- 真实 INTL 流量恢复：换装后约 8 分钟内 56 次 `workbuddy-oauth/deepseek-v4.1-flash` 上游 `successful=true`，0 次失败；`/v1/responses`、`/v1/chat/completions` 均为 200。
- `cache-canary glm-5.3-flash`：样本 2 `hit_ratio=0.9956`（`cache_read_tokens=4096/4114`），`HEALTH_OK`。
- GLM 流式探针（非 OAuth lane）：`ttfb_s=0.991`、`total_s=1.277`、`events=17`，输出 `cache stream probe ok`。
- `/v1/responses` 200 时长分布（近 90 分钟，含换装前）：`n=518`，`p50=6.05s`、`p90=17.53s`、`p99=46.82s`、`max=55.89s`。这是整段生成耗时，非首字延迟；与共享上游容量窗一致，未据此调整并发或缓冲。
- 近 20 分钟 admission：145 次 `successful=true`、2 次 `successful=false`（均为上游容量类，无新增 durable hold）。

## 观测与风险边界

- **Antigravity 模型替换告警**：doctor 报 `model_substitution_warnings_7d=70`、`distinct_pairs=1`、`70 gemini-3.8-flash-n <== gemini-3.8-flash-high`。已核对：CPA 把客户端 `message.model` 写成上游 `modelVersion`（基础名），thinking level 经 `generationConfig.thinkingConfig.thinkingLevel` 单独传递；凭据 capability hints 含 `gemini-3.8-flash-high/low/medium/tiered`。计数随请求量增长，属命名差异，无真实降档证据；不改名消警、不改路由。
- **ChatGPT 容量事件**：24h admission `capacity_events=99`（`gpt-6.1-sol:69`、`gpt-6-luna:16`、`gpt-5.6-luna:9`、`deepseek-v4.1-flash:5`），`lane_rejects=29`（cooldown:20、downstream_gone:8、model_cooldown:1）。属上游容量窗，admission 阶梯冷却正常，无 durable hold；未为提高吞吐增加并发或轮换账号。
- **WorkBuddy CN 未接入**：远端仅 2 份 INTL 凭据，无 CN 凭据；`models_cn` 未钉定。CN 需用户本人完成浏览器 OAuth（腾讯实名体系），之后才能钉定 `models_cn` 并按独立受控变更验收。本次不代登录、不猜测目录。
- 未做：OAuth 生成探针、锁清理、凭据轮换、CN 登录、并发提升。

## 回滚

- 恢复备份：`cp -a /opt/cliproxyapi/backups/20261022T123315.481884659Z-plugin-0962-to-0964/workbuddy.so /opt/cliproxyapi/plugins/.workbuddy.so.rollback && chown --reference=/opt/cliproxyapi/plugins/workbuddy.so /opt/cliproxyapi/plugins/.workbuddy.so.rollback && mv -f /opt/cliproxyapi/plugins/.workbuddy.so.rollback /opt/cliproxyapi/plugins/workbuddy.so`，再 `cd /opt/cliproxyapi && docker compose restart cli-proxy-api`。
- 回滚后应复验注册版本回到 `0.9.62` 与 `health readiness`；Git 回滚不能替代远端恢复。
