# 2026-11-23/24 bwg：CPA fork 换代 v8.1.1-sigiso → v8.1.2 五叠层（上游收编波同步）

**性质**：真实远端写入 + release 级证据留档（redaction-first；账号一律 sha8 代号）。
**授权**：用户 2026-11-23 深夜裁决"现在立即执行"（fork v8.1.2 换代时机问答）。

## 背景与裁决

上游当日发布 **v8.1.2（tag `85225381`，2026-11-23T14:49Z，51 commits）**，集中收编
本仓 watch 台账：#7960（signatureCache 按凭据域 + unscoped fail-closed）、#7961
（transport 按 auth 身份 + refresh grant digest 分池）、#7924（TOS 终态在
skip-cooldown 分支保持）、#7957（503 capacity Retry-After 遵从）、#7952（fork 失败
保父 affinity）、#7934（compaction 摘要校验）+ thinking-level 原生路由
（`2f86992c`，与 agy-effort 叠层同域）+ 大批 Claude/Gemini 翻译质量修复。冲突矩阵
与叠层处置表见 runbook
[cpa-observed-baselines-and-upstream-log.md](../runbooks/cpa-observed-baselines-and-upstream-log.md)
五节（b406b2b）。

**关键上游语义**：replay KV **有意不按凭据分域**（`antigravity_reasoning_replay_cache.go:564`
注释明言 session key 是连续性边界、独立于凭据保 failover；#7846 的 auth 前缀方案
仍是 open PR）→ crossauth/sig-iso 叠层无上游等价物，**保留并 rebase**。

## 合并方法（可复现配方）

外置参考仓 fetch 至 v8.1.2 refs 后，在 `HG03-work/cpa-fork-v812-20261123/merge`
做 **git 三方合并**：分支 `fork-v811-plus-overlays` = v8.1.1 tag + 五叠层 tarball
整文件提取（gemini-raw v8.0.79 版 / agy-effort、guard、crossauth v8.1.1 版 /
sigiso 取自 bwg `/root/cpa-v8111-build/sigiso.tar.gz` sha8 `1cf9e533`），merge
v8.1.2。四文件冲突全解：execute/tokens=上游 `selection.ID` 改名 + 保留
`antigravityIsolateRequestSignaturesDoc` 挂钩；stream.go/apply.go 为 **CRLF 假冲突**
（规范化后 fork 增量仅 14/67 行），解法=取 v8.1.2 版 + 重放真实增量
（stream：daily-cap 块移至 TOS 检查后/`prepareAntigravityRequest` 前、isolate 挂钩、
SSE 环节 record 挂钩；apply：envelope 级提取钩 + 四函数）。另补 v8.1.2 删除的
`time`/`fmt` 导入（daily-cap 块依赖）。

**超代判定（agy-effort）**：回归测试
`TestApplyThinkingUsesAntigravityEnvelopeLevel`/`TestExtractReasoningEffortConfigPrefix`
在 v8.1.2 基座上 **PASS** —— 上游 thinking-level 原生路由与叠层的 shared-pass
envelope 提取互补（上游在模型选择层读意图、叠层在共享 thinking 通道防 xhigh→high
钳制），**保留**。crossauth（replay auth 前缀）与 sig-iso（请求侧剥离）按上述
语义保留；guard/daily-cap 挂钩重打（execute 自动落位正确）；gemini-raw 整组
重生成。v8.1.2 版五叠层 tarball 落 `outputs/cpa-*-v8.1.2.tar.gz`（本地产物，
同历代惯例不入库；sha8：gemini `5a22829b`/agy `ebdfb85c`/guard `aeb483e6`/
crossauth `a0996393`/sigiso `952c786c`）。

## 构建路径偏离（护栏触发的合规替代）

bwg 主机 `MemAvailable=1377→1430 MB`（drop_caches 后），低于构建护栏下限
（1500MB floor，`scripts/cpa_fork_rebuild.ps1` ca198c2 内存护栏）→ 远端构建
REFUSE。按护栏自身提示走 **本地构建**：WSL Ubuntu-24.04 + golang 1.26.10
（aliyun 镜像，go.mod 要求 1.26.10 精确匹配）+ CGO_ENABLED=1 原生构建。
`go vet` 全过；race 测试 `executor 2.293s ok / thinking 1.115s / helps 1.313s`。
产物 binary
`197894122e72960b367e37a8c6a1f770d20892c371057f6add5bb16a6f47f95a`
（Commit 串 `85225381+gemini-raw+agy-effort+daily-cap+crossauth-replay+sig-iso`，
BuildDate 2026-11-23T15:46:17Z）。

**glibc 门**（WSL Ubuntu 24.04 glibc 2.39 链接 vs 运行基底 bookworm 2.36）：
构建脚本内建硬门——`ldd` 符号扫描无 `GLIBC_2.37+` 引用 + bookworm 基底容器内
`-h` 执行 rc=0，双过才允许建镜像（`BOOKWORM_SMOKE_OK`）。SFTP 分块流式上传
（paramiko，凭据不落输出）+ 远端 sha256 双验。

## 镜像与 swap 事务

- 基底 `eceasy/cli-proxy-api:v8.1.2@sha256:37733f2a…`（RepoDigest 实拉钉住），
  镜像 `cli-proxy-api-fork:v8.1.2-gemini-raw-daily-cap-authiso-sigiso`
  id `sha256:ceb72412…`，binary 回读 sha + base/fork/tag/build.site 四 label 全验
  （`outputs/build-fork-image-v812-20261124.ps1`，含 glibc 门）。
- swap（`outputs/cpa-v812-sigiso-swap-transaction-20261124.ps1`，DryRun+Apply 一次过）：
  drain 锁 + 备份
  `/opt/cliproxyapi/backups/20261123T160725.574237789Z-swap-v8111-sigiso-to-v812-sigiso/`
  → `IMAGE_ID_READBACK_OK` → `START_LOG_CLEAN version=v8.1.2
  plugin=workbuddy-provider:0.21.30 intl=closed_dark` → `RUNTIME_BINARY_OK
  19789412` → `READINESS_OK` → `AUTH_TOKEN_REFRESHED_SAFE
  disabled_set_unchanged=true` → `LANE_STATE_DELTA=none` → `SWAP_OK`（16:07:30Z）。

## 验收

- **8318 铁律（过 admission）**：`gemini-3.8-flash-high` 200/3.57s/PONG
  （served `gemini-3.8-flash-n` 已知复核对）；`gemini-4-neon-xhigh`
  200/2.23s/PONG（served `gemini-4-neon`）。探后 lane 归位。
- 账本：`scripts/remote/fork-binary.sha256` → `19789412…`（本提交）；doctor
  runtime-binary 比较源为 git HEAD blob，本提交落 HEAD 前 FAIL=预期姿势。

## 回滚

镜像级：compose image 改回 `v8.1.1-gemini-raw-daily-cap-authiso-sigiso`
（id `sha256:2fe58d9f…`，binary `8b593f38`）+ force-recreate（备份目录含原
compose/config/插件/凭据清单）。代码级：重放本配方于 v8.1.1 基座（HG03-work
merge 仓 `fork-v811-plus-overlays` 分支即原态）。

## 后续观察

- #7946 内存尖峰指纹（并发 320K 流 + 容器无解释重启）仍适用于 v8.1.2。
- "agy 重登后 restart 容器"纪律在 #7961 已正式修复后**可退役**（transport 按
  grant digest 分池），下次重登自然验证。
- #7964（Claude→Gemini 丢 stop_sequences）/#7963（tini 僵尸）为 v8.1.2 后新
  watch（见 runbook 五节）。
- 上游 v8.1.2 发布仅 ~8h 即上产（用户裁决即时执行）；若上游出 v8.1.3 回归修复，
  按常规 soak 纪律评估。

## 回滚（同夜 16:19:59Z，SWAP_OK 后 12.5 分钟）

上游 issue **#7966**（15:43Z 开）指认 v8.1.2 tag 自带两个行为回归，在我们
构建树上实锤复现（`go test -run TestIssue6825` / interrupted-message 配对测试
均 FAIL）：

- `4df055de`：**并行工具调用流索引重复**（`[0 1 2 0 1 2]`，参数被拆裂到重复
  索引上）——OpenAI 格式客户端的并行 tool call 形状直击桌面代理日常；
- `ae88b00c`：中断消息与真实输出配对错乱（空 text part 入 contents）。

两提交均在 v8.1.1..v8.1.2 区间 → v8.1.1-sigiso 代干净。**决策（比例原则）**：
v8.1.2 增量为非急性加固（sig-iso/crossauth/daily-cap/repeat-reject 四层在
v8.1.1 代已全在），回归是功能性破坏 → 立即回滚。
`outputs/cpa-v812-rollback-transaction-20261124.ps1`（DryRun+Apply 一次过，
备份 `20261123T161955Z-swap-v812-to-v8111-rollback`），v8.1.2 暴露窗口
16:07:30→16:19:59Z（12.5 分钟，期间 1h 审计零异常、无并行工具调用受害
流量实证）。回滚后 8318 探针 200 PONG；账本
`scripts/remote/fork-binary.sha256` 回写 `8b593f38`。

**教训（固化为换代纪律）**：①fork 构建的测试腿必须跑**全量**
`./internal/...`（本代 -run 白名单漏掉 TestIssue6825 族，~3 分钟成本换掉
一次 12.5 分钟暴露）；②发布 <24h 的 tag 即使"收编 watch 清单"也要先跑
全量测试再 swap（8h 新鲜度风险本次实弹兑现）；③上游 main 破损要按
tag 归属判定（"fail on main"≠tag 干净，本案 tag 即含回归）。

**重上路径**：上游修复落地（v8.1.3 或 #7966 修复 commit）后，重放本台账
配方（merge 树在 HG03-work，基座换新 tag，全量测试门），40 分钟内完成。
"重登后 restart"纪律在重回 v8.1.2 系前**恢复执行**。
