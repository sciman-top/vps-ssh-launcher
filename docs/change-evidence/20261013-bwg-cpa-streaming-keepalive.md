# 2026-10-13 BWG CPA streaming keepalive 启用证据

## 决策与范围

在 v8.0.27 canary 升级闭环（见同日升级证据）之后，启用上游 `519d590` 引入的流式
SSE keep-alive，作为"吐字缓慢"面的网关侧缓解：

- `requests.streaming.keepalive-seconds: 15`
- `requests.streaming.send-keepalive-before-ft: true`

语义（v8.0.27 源码只读核实，`sdk/api/handlers/handlers.go`）：心跳为纯下游
`: keep-alive\n\n` SSE 注释与 WebSocket Ping，**不产生任何上游流量**；15s 间隔让
慢 TTFB 推理回合与流中静默间隙对空闲读超时短于 Nginx 300s lane 的客户端保持存活，
把"静默挂死→客户端掐断→重试加压"变成"温等"，减少浪费的上游调用，利好风控姿态。
默认值（0/false）保持关闭语义；9/28 关闭 `stream-bootstrap-buffering` 的教训
（9.4-10.3s 死气后一次性 burst）与本机制正交：keepalive 不缓冲、不改变 chunk 时序，
只在静默期插入注释帧。

`nonstream-keepalive-interval` 维持 0（非流式 JSON 填白行对严格客户端风险更高，
且本栈健康探针与多数消费方为非流式，无此需求）。

## 远端 config 事务

`outputs/keepalive-edit.sh` + 收据 `outputs/keepalive-config-edit-20261013.log`：

- 前置备份 `/opt/cliproxyapi/backups/20261012T231927.751439210Z-config-streaming-keepalive`
  （700 目录、600 文件）。
- 文本级锚点插入（`^requests:$` 全文唯一，`n != 1` 即 REFUSE），插入前后双
  `yaml.safe_load` 深度相等校验（除新增 streaming 块外整文档逐值一致），原子
  `os.replace` 并保留原文件权限；`streaming` 已存在时 REFUSE。
- `CONFIG_KEEPALIVE_EDITED` 后 `docker compose restart cli-proxy-api`；
  容器 23:19:28Z 起 running，镜像仍为 v8.0.27@b7cf456b。

## 策略钉死与三向持久

- `scripts/remote/cpa_policy.py` `EXPECTED_CONFIG_PATHS` 新增两键（缺键/漂移均
  fail-closed）；`tests/script_validation_support.py` 与 `tests/test_cpa_policy_runtime.py`
  的合成合法 config 同步 streaming 块。
- focused pytest（policy runtime / guardrails script / updater apply）49 passed 后
  全量门禁 `558 passed, 1 skipped, 269 subtests`（`outputs/gates-20261013-v8027.txt`，
  首跑 lint:format 拒绝两处新行格式，format 后全绿）。
- 提交 `ca3f684` 后 `cpa_bwg_guardrails.ps1 -Apply` 投影（clean-tree 门禁按设计
  先拒绝过一次未提交源，留痕后按序执行）；`GUARDRAILS_APPLIED`，apply 流程将容器
  于 23:29:13Z 重启。
- post-doctor `outputs/doctor-20261013-post-keepalive.txt`：`DOCTOR_CONTRACT_OK`、
  `semantic-policy=OK`（新键对活配置生效）、`drift=cpa_policy.py MATCH`（投影逐字
  一致）、`POLICY_OK`、冷却 none、`MODEL_IDS_UNKNOWN=none`。由此升级/`-Apply`/
  管理API 编辑三向均被 policy fail-closed 兜底。

## 升级后探针

- cache-canary（`outputs/post-v8027-cache-probe.txt`）：两样本均 `4096/4114`、
  命中率 `0.9956`，与基线逐位一致；`support-prompt-cache-key` 与 session-affinity
  零改动。
- quality 矩阵（受控实战，`outputs/post-keepalive-generation-all.txt`）：
  `generation-all`（`CPA_HEALTH_NO_OAUTH=1`，预算 7 请求，失败继续）在 keepalive
  配置下 `gpt-6-astra` 3.2s、`glm-5.3-flash` 1.3s、`deepseek-v4.1-flash` 0.7s、
  `gpt-5.6-terra` 2.6s、`glm-5.3` 1.3s、`muse-spark-1.3-contributor` 1.3s 全部
  HTTP 200 `finish=stop`（6/6 条可达通道）；`gpt-6.1-sol-input` 400（356ms）为
  已知渠道侧定价丢失（见下）。首轮 `quality-canary`（`outputs/post-keepalive-probes.txt`）
  在 sol 处快速中止（exit 20），故改用 continue-on-failure 模式补齐矩阵。
- **sol-input 渠道侧 400，非本事务回归**：错误转储（error-v1-responses-
  20261013T072143、error-v1-chat-completions-20261013T073019，见
  `outputs/sol400-diag.txt`）显示 ai.input.im 返回"模型 gpt-6.1-sol 的价格尚未由
  管理员配置"（中转站侧定价配置丢失），且 07:21:43 一条自然 codex 客户端流量同样
  400，探针与真实流量同等受影响。升级闭环时（23:12Z）同探针 200@2.4s；
  `outputs/sol-heal-check-20261013.txt`（约 09:5x 北京单请求复查）仍复现同错。
  按"通道涨落勿当升级回归排查、不重复探针"纪律停止加压，恢复以自然流量/下次
  doctor 观察为准（与 10/12 forbidden 自愈同族）。
- 容器读回：v8.0.27@b7cf456b running、restarts=0、`repo_digests` 与目标一致。

## 模拟验收（keepalive 配置下 fixture 复跑）

`outputs/fixture-v8027-keepalive.txt`：以含 streaming keepalive 键的生产 config
副本 + v8.0.27 二进制（sha 前缀 `3536d7febce60425`）在 disposable namespace 复跑
全量验收——overload/cooldown/62s 恢复三态契约与 updater 四场景全部符合，
`ACCEPTANCE_RESULT=PASS`、`ACCEPTANCE_EXIT=0`、`CLEANUP_OK`（泄漏扫描零命中）。
（fixture 内 `sol_diag 200` 来自合成上游，与真实渠道无关。）验收套件覆盖的
updater 治理语义与 keepalive 配置共存无冲突。

## 流式行为观测

`outputs/keepalive-stream-observe.py`（key 不出远端）+ 收据
`outputs/keepalive-stream-observe-20261013.txt`：经 loopback 对 `deepseek-v4.1-flash`
发起单笔流式请求——HTTP 200、首帧 0.623s、93 帧全数据帧、`[DONE]` 在位、
`comments_before_first_data=0`。结论 `not_observed`：TTFB 0.623s 远低于 15s 阈值，
pre-FT keepalive 无触发窗口（该通道推理增量立即可流）。启用事实由 config+policy+
doctor 证据链承担；行为面待自然流量中出现 >15s 静默窗（慢 TTFB 或通道涨落）时兑现，
届时客户端将看到注释帧而非静默。

## 回滚入口

- 配置：恢复备份 `20261012T231927.751439210Z-config-streaming-keepalive` 后
  `docker compose restart`；或反向文本编辑删除两行。
- 策略：revert `ca3f684` 后重新 `-Apply`（否则 doctor 会对缺键报 semantic 违规，
  属预期的 fail-closed 行为）。
- 上游镜像回滚独立于本事务，见同日升级证据。

## 分层结论

| 层级 | 结论 |
| --- | --- |
| `repo_verified` | v8.0.27 源码只读核实 keepalive 语义与默认关闭；policy+测试钉死，全量门禁通过。 |
| `filesystem_projected` | config 文本事务带双解析校验；`-Apply` 后 `drift=cpa_policy.py MATCH`。 |
| `host_loaded` | 编辑后两度容器重启（23:19:28Z 手动、23:29:13Z apply 流程），running、generation 门通过；`semantic-policy=OK`。 |
| `controlled_live_replay` | cache-canary 0.9956×2 与基线逐位一致；keepalive 配置下 generation-all 6/6 条可达通道 200/stop（sol 400=渠道侧，升级前 200@2.4s 已验）；keepalive 配置下 fixture 全量验收 PASS；流式观测单笔 200/93 帧/`[DONE]`，keepalive 帧 not_observed（TTFB 0.623s < 15s）。 |
| `natural_live_accepted` | **未宣称**。sol-input 渠道侧 400（ai.input.im 定价配置丢失，自然流量同受影响，约 09:5x 北京复查仍未自愈）待渠道恢复；keepalive 行为面以自然流量与下次 doctor 观察。 |
