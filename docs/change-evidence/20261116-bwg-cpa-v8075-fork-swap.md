# bwg CPA v8.0.61 → v8.0.75（fork 重建 + 换芯）回执（2026-11-16）

**性质**：受控远端变更。**执行窗口**：2026-11-16 17:16–17:38 UTC（2026-11-17 01:16–01:38 +08）。

**授权**：用户 2026-11-17 01:07 (+08) "授权按你的推荐/建议连续自动修复和优化，直至完成所有任务"，
延续当次"全面深度审查…防范封号、限流、降智风险，优化吐字、缓存"的请求。

**层级**：本页所有运行时事实为 `host_loaded`，源码/构建事实为 `repo_verified`。
远端效果（真实请求）见 §6。

---

## 1. 为什么不是普通换芯

现役镜像 `cli-proxy-api-fork:v8.0.61-gemini-raw` 与官方镜像**唯一差异**是入口二进制：
它带本地 Gemini raw-reasoning 翻译补丁（`outputs/cpa-gemini-raw.patch`）。updater 自身
对此有门禁（`FORK_IMAGE_REBUILD_REQUIRED`，exit 77），**只能**走"重建二进制 → 重建镜像 →
换 compose 行"的事务。因此本轮没有、也不能用 `cpa-auto-update.sh`。

## 2. 上游依据（`repo_verified`，本地外置仓 main@c19787b2 = v8.0.75）

`v8.0.61..v8.0.75` 共 125 提交，与本任务直接相关的：

| 提交 | 内容 | 关联风险 |
|---|---|---|
| `2c5b01f4` | Claude 默认传输关闭空闲连接复用，回滚 post-`message_stop` body drain | **吐字**：下一条请求不再挂在已完成但未释放的流后面 |
| `5a351d95` + `e96d4c48` | `TOS_VIOLATION` 直接把该**凭据**停用（OAuth 刷新保留）；池级保护性暂停默认关闭 | **封号**：处置落到凭据层，不整池停摆 |
| `ed1c23f9` | Gemini/Vertex 尊重 429 的 `Retry-After` 与 `RetryInfo` | **限流**：Antigravity 车道不再比上游 reset 早恢复 |
| `7e82d0d1` / `cf07c78a` / `f2efe2c4` | Gemini 零输出 502 / 异常终止不冷却凭据、200 承诺前失败 | **限流**：减少假失败造成的账号冷却 |
| `4da9f685` | 终端 429 携带池的真实恢复时间 | 客户端退避对齐 |
| `69ed202a` | 冷却截止后模型列表自动恢复 | 可用性 |
| `14e57717` | 插件执行器可上报**凭据级** 429 | WorkBuddy 类通道归属正确 |
| `098af7d5` | 跨等价模型拼写共享冷却 | 别名不再互相踩 |
| `84534584` | 文档：`X-Session-ID` 与全文压缩下的会话亲和 | **缓存**：客户端侧解法依据 |

## 3. 二进制重建

新增 `scripts/cpa_fork_rebuild.ps1`（参数化，取代 2026-11-08 的一次性脚本）。执行前的全部
证明：

| 项 | 值 |
|---|---|
| tag → commit | `v8.0.75` = `c19787b25262b5e6e46a9e8da62f4333e7e478bc`（GitHub ref API 核对） |
| 源码 tarball sha256 | `9d7518a0…b92c`（archive of that commit；模块行断言为 `CLIProxyAPI/v8`） |
| 原始 translator sha256 | `6f8e55d1…fdc2` |
| 移植方式 | **3-way apply**（patch 写于 v8.0.61，对 v8.0.75 已不逐字可应用；文件从 2517 行长到 2735 行） |
| 移植产物 | `outputs/cpa-gemini-raw-v8.0.75.tar.gz`，sha256 `9d929922…b838`，仅含 translator 包的源码+测试 |
| 移植后 translator sha256 | `b903efca…d809`（测试文件 `37308f61…e297`） |
| 门禁 | `go vet` + `go test ./internal/translator/gemini/openai/responses/` **通过**（在隔离 golang 容器内） |
| 产物 sha256 | `2bdc6cbd3f21d7951ab779b38b10a932293679ec1cde5ba3809e0ea5fd876e73`（77,947,712 B） |
| 版本自证 | `CLIProxyAPI Version: v8.0.75, Commit: c19787b2…+gemini-raw` |

**踩坑记录（已修进脚本）**：把源码放在 `/root` 下 bind-mount 给容器编译时，完整编译报
`open internal/...: no such file or directory`——同一条命令、同一目录分别运行都成功，唯独
连续两步（vet/test 后紧接着 build）会失败；根因未定（该机 overlayfs），处置是把构建目录移到
`/var/tmp/cpa-fork-build-<tag>` 并在成功后删除。**不要**再把它移回 `/root`。

## 4. 派生镜像

| 项 | 值 |
|---|---|
| 基础镜像 | `eceasy/cli-proxy-api:v8.0.75@sha256:16bdaaf2…03c2`（index digest 钉定） |
| 产物 | `cli-proxy-api-fork:v8.0.75-gemini-raw` |
| image id | `sha256:0d9f911ea505ad345be000be135be2acd66b87f4a27589997c2afd29de61dd2c` |
| 构建产物二进制 sha | `2bdc6cbd…6e73`（与台账一致） |
| OCI 标签 | `base.digest`、`fork-binary.sha256`、`upstream.tag` 均已在构建后读回断言 |

## 5. 换芯事务（`outputs/cpa-v8075-swap-20261116.sh`）

执行链：锁 → 入口态判定（只接受 old）→ 目标镜像 id/标签三项断言 → 磁盘 ≥2 GiB →
`/healthz` 与持久锚预检 → 备份（compose + config + 凭据哈希）→ **drain 取 `requests.lock`
排他** → 原子改 compose 单行 → `compose up -d --pull never` → 运行时镜像与**容器内二进制
哈希**复核 → `cpa-health.py readiness`（180 s 有界等待）→ 凭据字节不变 → 车道锚无新增
hold → 启动日志无 `panic|fatal error|exec format error` → 放 drain。

失败路径：`EXIT` 与 `ERR` 同时 trap，回滚用备份 compose 重建并按同一有界等待复验。

输出摘录：

```
ENTRY_STATE compose=old cur=cli-proxy-api-fork:v8.0.61-gemini-raw
PRE_WRITE_AHEAD_MIRROR lane=chatgpt-oauth live=none
PRE_HOLDS_CLEAN / BACKUP dir=…/20261116T172137.333227955Z-swap-v8.0.75-gemini-raw
DRAIN_OK → RUNTIME_IMAGE_OK → RUNTIME_BINARY sha=2bdc6cbd… container
CONFIG_PINNED → READINESS_OK → AUTH_BYTES_UNCHANGED → LANE_HOLD_DELTA=none
START_LOG_CLEAN → restarts=0
SWAP_OK v8.0.61-gemini-raw->v8.0.75-gemini-raw image_id=sha256:0d9f911e…
```

## 6. 换芯后复验

| 项 | 结果 |
|---|---|
| 容器 | `cli-proxy-api-fork:v8.0.75-gemini-raw`，`Up`，`restart=0` |
| 容器内二进制 | `2bdc6cbd…6e73`（=台账 = `scripts/remote/fork-binary.sha256`） |
| strict doctor | `DOCTOR_CONTRACT_OK`（`runtime_binary_hash=MATCH`、`admission-integrity=OK`、17 项投影全 MATCH） |
| 凭据 | 22 份全部 `disabled=false`；4 份 Antigravity 令牌在换芯后继续刷新（到期 2026-11-17T02:1x +08） |
| 车道 | 四车道 `hold_reason=null`、`cooldown_active=false` |
| 真实请求 | `POST /v1/responses`、`POST /v1/messages` 均 200，admission 遥测开始累计样本 |
| 启动日志 | 0 次 `panic/fatal error` |

## 7. 同一窗口内的 admission 投影（P1）

`-Apply` 同时投影了 2026-11-16 复审的四项 admission 修复：

- `pooled_models` 收窄（部署后 `/opt/cliproxyapi/cpa-admission.json` 的 `workbuddy-oauth`
  带 `["deepseek-v4.1-flash"]`）；
- `STANDING_RESTRICTION_*` 统一、Antigravity 信号容错、`GEMINI_CAPACITY_MARKERS`；
- `requests.streaming.bootstrap-retries = 1`（部署后 `config.yaml:5` 已含该键）；
- `fork-binary.sha256` 台账随同一提交更新（`a84dbf7`）。
- 期间一次 `-Apply` 被 **`REFUSE cpa_busy`** 拒绝：一个此前中断的 SSH 会话仍持
  `/run/vps-ssh-launcher-maintenance.lock`。**处置**：确认该会话是本次重建的**孤儿**（父进程为
  `bash -c …base64 -d | bash` 的已弃 shell，容器 up 1 分钟无进展）后 `kill -TERM`，随后 apply 成功。
  该锁的语义（`flock -n` 拒绝而非排队）在此处起了正确作用。

## 8. 回滚

```bash
# 1) compose 回到旧派生镜像（镜像仍在本地，未被 prune）
sed -i 's|image: cli-proxy-api-fork:v8.0.75-gemini-raw|image: cli-proxy-api-fork:v8.0.61-gemini-raw|' /opt/cliproxyapi/compose.yml
cd /opt/cliproxyapi && docker compose up -d --pull never
# 2) 或直接还原整份备份
cp -a /opt/cliproxyapi/backups/20261116T172137.333227955Z-swap-v8.0.75-gemini-raw/compose.yml /opt/cliproxyapi/compose.yml
# 3) 台账与 doctor 必须同步回退：scripts/remote/fork-binary.sha256 改回 31f2f9d9…
```

回滚后 doctor 会以 `runtime_binary_hash=MISMATCH` 抓住"台账说新、实际跑旧"的不一致——这是
设计如此，不是故障。
