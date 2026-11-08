# fork 派生镜像的 provenance、复现与活体验收（2026-11-09 补强）

**性质**：真实远端写入（派生镜像重建 + 单次 drain 换装 + 台账投影）· 脱敏留档
**用户授权**："授权按你的推荐/建议连续自动修复和优化，直至完成所有任务。"
**前序**：[20261109-bwg-cpa-fork-image-convergence.md](20261109-bwg-cpa-fork-image-convergence.md)

## 0. 本补强解决什么

前一份台账把派生镜像写成了既成事实，但留下三个真实缺口，本轮回执逐条闭合：

| 缺口 | 前态 | 本轮回执 |
|---|---|---|
| 构建不可复现 | "换机需按本表重放构建"，而构建从未入库 | `outputs/build-fork-image-20261109.ps1`，参数化 + 全断言 |
| 镜像无 provenance | `docker image inspect` 的 Labels 为 `null` | 四个 OCI 标签：base.name/base.digest/upstream.tag/fork-binary.sha256 |
| 宿主无台账副本 | `ledger=none`，直接读主机无法判断该跑哪个二进制 | `outputs/project-fork-ledger-20261109.ps1` 投影 + 与运行态比对 |

## 1. 构建脚本（`outputs/build-fork-image-20261109.ps1`）

契约：基线按 **index digest** 钉定并断言拉取结果；fork 二进制作为**独立工件**输入
（基线镜像里是官方 `f822140a…`，不是 fork `31f2f9d9…`——这是首版脚本的错误假设，
已被 dry-run 当场证伪并修正）；构建后从产物镜像里**取出**二进制再算哈希，并回读
标签；`-Apply` 只写本地 tag，不重启任何容器。

```
BASE_DIGEST_OK sha256:fc714b43…
FORK_BINARY_SHA_OK 31f2f9d9…
CONTEXT_READY image=cli-proxy-api-fork:v8.0.61-gemini-raw
BUILT_BINARY_SHA_OK 31f2f9d9…
LABELS_OK base=sha256:fc714b43… fork=31f2f9d9…
BUILD_OK image=cli-proxy-api-fork:v8.0.61-gemini-raw image_id=sha256:786669dd7438d600e808f70e93ab930228f5b0e7c37987278a40201d9456c466
```

## 2. 换装（`outputs/switch-fork-image-relabel-20261109.ps1`）

tag 被重指后，容器仍引用旧镜像 id，单靠 restart 只会继续跑旧层。事务：维护锁 →
容器/标签 id 双校验 → lane-hold 预检 → `requests.lock` 排他 drain → 备份 →
`up -d --force-recreate` → 镜像 id / 启动日志 / 二进制 / 标签 / 就绪 / 凭据 / hold 七项复验，
失败回滚（恢复 tag + recreate）。

```
PRE_STATE_OK current=8c6ab375… tag=786669dd…
DRAIN_OK → IMAGE_ID_READBACK=OK 786669dd…
CLIProxyAPI Version: v8.0.61, Commit: 681ce41b+gemini-raw
START_LOG_CLEAN version=v8.0.61 plugin=0.9.89
BINARY_SHA_OK 31f2f9d9…
LABELS_OK base=sha256:fc714b43… fork=31f2f9d9…
READINESS_OK → AUTH_BYTES_UNCHANGED → LANE_HOLD_DELTA=none → restarts=0
RELABEL_OK
```

### 2.1 本次最该记住的教训：又一次单点采样

第一次换装**实际成功**，却报了 `PLUGIN_NOT_REGISTERED` 并触发回滚——脚本在容器启动
**1 秒后**只采样了一次启动日志，而插件在 2 秒后才注册。这与 2026-11-08 的
readiness 单点采样是**同一类缺陷第二次复发**。已改为 10×2 s 有界轮询，并把原因
写进脚本注释；重跑一次走完新的轮询路径：`RELABEL_OK`，exit 0。

回滚侧也暴露一处：`docker image tag <image-id>` 在旧镜像已被清理后报
`No such image`，即"回滚到被 GC 的镜像"不可行。本次旧镜像仍在，故未造成损失；
文档已记明：**换装前必须确认旧镜像 id 仍在本机**。

## 3. 台账投影（`outputs/project-fork-ledger-20261109.ps1`）

`LEDGER_PROJECTED sha=31f2f9d9… live_matches=true`。这份 `/opt/cliproxyapi/fork-binary.sha256`
是**运维便利副本**，不是第二真源：doctor 仍从仓库 HEAD blob 注入期望值
（`__CPA_FORK_BINARY_SHA_VALUE__`），副本只回答"直接读主机时该跑哪个二进制"。

## 4. 受控实战验收

### 4.1 updater 只读候选（活体）

```
CANDIDATE current=v8.0.61 target=v8.0.61 soak=72h
BACKUP_HEALTH status=ok backups=184 free_kib=9445824
```

fork tag 解析正常，现役即最高成熟候选，无动作、不触上游。

### 4.2 canary 拒绝语义（活体提取 + 三种形状）

| 形态 | 结果 | 判定 |
|---|---|---|
| fork 派生镜像 | `rc=77 FORK_IMAGE_REBUILD_REQUIRED image=cli-proxy-api-fork:v8.0.61-gemini-raw` | 正确拒绝 |
| bind-mount 形态 | `rc=77 RUNTIME_BINARY_CANARY_REQUIRED bind_mount=/CLIProxyAPI/CLIProxyAPI` | 正确拒绝 |
| 纯官方 tag（无 fork、无挂载） | `rc=0` | 正确放行——拒绝是精确的，不是无差别拦截 |

### 4.3 公网全链四车道（nginx capability path → admission → CPA → 上游）

```
public_antigravity=200 t=2.10s terminal=1 text_len=4 pong=True
public_workbuddy  =200 t=1.29s terminal=1 text_len=4 pong=True
public_codex      =200 t=1.12s terminal=1 text_len=4 pong=True
public_zhipu      =200 t=1.40s terminal=1 text_len=4 pong=True
```

**这里也修了一个假阴性**：首版断言先对每个 delta 做 `strip()` 再拼接，而 SSE 会把
PONG 切成 `"P"` + `"ONG"`，于是健康车道被报成 `pong=False`。改为原样拼接、
末尾才 strip，并加 `terminal` 与 `text_len` 双判据。逐字段复核已证实两条"失败"
车道当时确实返回了 `P` + `ONG`。

### 4.4 镜像内容取证

```
NO_BINARY_BIND_MOUNT=OK
IMAGE_BINARY_SHA   = 31f2f9d9…（从产物镜像里取出）
RUNNING_BINARY_SHA = 31f2f9d9…
VERSION_PROBE      = CLIProxyAPI Version: v8.0.61, Commit: 681ce41b+gemini-raw
```

## 5. 仓库面

- 新增 `tests/test_cpa_updater_selection.py`：发行版选择契约**独立成文件**。
  原因：`tests/test_cpa_updater_apply.py` 被并行会话两次整体回退，脚本改动留下、
  配套测试被一起抹掉。选择逻辑是自包含契约，独立文件后不再被大模块的并发编辑波及。
  覆盖：`SOAK_PENDING` 只在无成熟候选时出现、只报最新浸泡版本、有成熟补丁候选时
  不出现、fork tag 解析出其上游版本（含 `@sha256` 形式）。

## 6. 回滚

```bash
BK=/opt/cliproxyapi/backups/<本次 TS>-fork-image-relabel
cp -a "$BK/compose.yml" /opt/cliproxyapi/compose.yml
cd /opt/cliproxyapi && docker compose up -d --force-recreate
```

- 旧镜像 `8c6ab375…` 在换装前仍在本机时可 `docker image tag 8c6ab375… <tag>` 回退；
  若已被清理，则须按 §1 重建。
- 目标态是派生镜像；回到 bind-mount 形态是合法终态，doctor 的
  `runtime_binary_source=bind_mount` 不会误判失败（只对 `unknown` 与哈希不符 FAIL）。

## 7. 边界

- 镜像仍只存在于 bwg 本地，未推 registry（用户门，未提供凭据）。
- 上游发布 v8.0.62+ 时 updater 具名拒绝，采纳 = 重跑 §1 + §2 事务。
- 账号侧姿态零改动：未新增/轮换账号、未清冷却、未提并发。
