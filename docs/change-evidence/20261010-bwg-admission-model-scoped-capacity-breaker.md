# BWG CPA admission 容量熔断拆分出模型作用域

日期：2026-10-10（Asia/Shanghai）。只处理 `bwg`，未连接或修改 `zz`；桌面 UI
验收仍需用户确认。

## 结论

`chatgpt-oauth` lane 的容量熔断原先只有一个作用域：任何被识别的容量信号都冷却
整条 lane。上游对**单个模型**的答复（`200` +
`Selected model is at capacity`）因此会连带拒绝同一账号下健康的兄弟模型 ——
2026-10-10 受控实测中，`gpt-6-luna` 的容量答复之后紧接着的 `gpt-6.1-sol`
单发请求被本机拒成 `429`（`admission_reason=cooldown`、`retry_after=471`），
在桌面侧呈现为 `exceeded retry limit, last status: 429`。

现在 `capacity_scope()` 把容量答复分两档：**点名模型的答复只停那个模型**
（独立退避阶梯、独立探针节奏、尊重上游 `Retry-After`、按代际校验探针成功），
而凭证级信号（`server_is_overloaded`、rate/usage limit 文本、无标记的
`429`/`503`）与解析失败的答复仍按整条 lane 冷却。两档作用域同时被
`cpa_policy.py`、`cpa_admission_risk_audit.py` 与 strict doctor 冻结为
fail-closed 契约。

## 证据链

### 1. 受控 A/B（同一 netns 顺序跑两代，同一假上游）

`outputs/accept-modelscope-20261010.{ps1,sh}` 在**一个一次性 `unshare -n`
网络命名空间**内顺序运行两代进程，两代都用生产绑定契约
（listen `127.0.0.1:8318`、upstream `127.0.0.1:8317`），因此不需要改写
config（`load_config` 硬钉这两个端口），也不触碰 live 服务、live CPA 与
live journal。假上游只对 `gpt-6-luna` 回容量口径，其它模型固定
`200 {"output_text":"ok"}`，按 `upstream.mode` 开关给两种形态：
`marker`（无 `Retry-After`）与 `marker_retry`（带 `Retry-After: 600`，
即 2026-10-10 生产事故的形态）。

| 上游形态 | 代际 | CASE1 luna | CASE2 luna | CASE3 luna | CASE4 sol |
| --- | --- | --- | --- | --- | --- |
| marker | NEW | 200 | 200 | `429 model_cooldown`（`retry-after: 60`） | **200** |
| marker | OLD | 200 | 200 | 200（排队 10.0s 后作 `early_probe`） | 200（同样排队 10.0s） |
| marker_retry | NEW | 200 | `429 model_cooldown`（`retry-after: 600`） | `429 model_cooldown`（600） | **200** |
| marker_retry | OLD | 200 | `429 cooldown`（600） | `429 cooldown`（600） | **`429 cooldown`（600）** |

- 判定量是第 4 行的 **CASE4 sol**：同一上游信号下 OLD 拒绝健康兄弟模型、
  NEW 正常服务，与事故形态逐字对应。日志行
  `lane_reject ... model=gpt-6.1-sol reason=cooldown retry_after=600`（OLD）
  对 `upstream_result ... model=gpt-6.1-sol status=200 capacity=false`（NEW）。
- `/healthz` 快照：NEW 的 `marker` 场景
  `cooldown_scope=model model_cooldowns={'gpt-6-luna': 60}`、
  `cooldown_active=False`；`marker_retry` 场景为 `{'gpt-6-luna': 600}`；
  旧世代没有这两个字段（`None`），只报 lane 级
  `cooldown_active=True cooldown_remaining=600`。
- NEW 的 11s 后探针（CASE5）：`lane_probe ... reason=model_half_open scope=model`
  被放行到上游，上游再次回容量标记后窗口按阶梯续期为 120s，说明模型级探针
  确实闭环而不需要重启。
- 断言：`ASSERT_FAILURES=0`、`AB_RESULT=PASS`；收尾检查
  `stray_proc=none`、`TEMP_DIR_REMOVED`、生产 `/healthz` 仍 `status=ok`。
  完整收据：`outputs/accept-modelscope-20261010.txt`。

### 2. 远端部署事务（受控写入）

`-Apply` 在本机不可用：`%APPDATA%\vps-ssh-launcher\providers.env` 不存在
（`outputs/apply-20261010-modelscope.txt`），而 `cpa-guardrail-apply.sh`
刻意拒绝用它轮换 admission 世代。沿用 2026-10-08 的既有先例，用
`outputs/deploy-admission-20261010.ps1 -Profile bwg` 在
`flock -n /run/vps-ssh-launcher-maintenance.lock` 内单事务写入：

1. 只读预检：旧 sha admission `3eec9fcf…`、config `24f19d55…`、
   policy `a5d01064…`、pin `3eec9fcf…`；`SVC=active`、`STATUS=ok`；
2. 暂存新世代并 `py_compile`；
3. 备份 `/root/cpa-admission-modelscope-backup-20261010T041140Z/`
   （`cpa-admission.py.old`、`cpa-admission.json.old`、`cpa_policy.py.old`、
   `cpa-admission.sha256.old`、可执行 `rollback.sh`、`backup.sha256`），
   `ROLLBACK_DRY_RUN_OK`；
4. 安装后逐文件 chown/chmod 并复核 sha，同一事务轮换
   `cpa-admission-integrity-pin.txt`；
5. `ADMISSION_INTEGRITY_OK`、`SEMANTIC_POLICY=OK`，restart 后断言
   `GENERATION_FIELDS=ok`。

收据：`outputs/deploy-modelscope-20261010.log`（`DRIVER_RESULT=PASS`
`pid=585417`）。

新世代：admission `183fdd1f68d7a2b8de4fc5dd478ea81c7965dcb49d807e995e54c6bb682fd2c6`、
config `942f6f2aef4f5f9223f337db25d2416d6c4fed0d7ef912ca6c4dcc9a472de5d0`、
policy `f7906ac89d5200246aa8a3ad80b1a9664b53da50cbd53317da84e2230e46ff7b`、
pin 同 admission。

### 3. 线上只读复验（部署前后同一 strict doctor）

- 部署前：`DOCTOR_CONTRACT_FAILED`，`admission-health=FAIL`，
  `cpa-admission.py` / `cpa-admission.json` / `cpa_policy.py` /
  `cpa-admission.sha256` 四项 `MISMATCH`
  （`outputs/doctor-20261010-modelscope-pre.txt`）。
- 部署后：`DOCTOR_CONTRACT_OK`，`admission-health=OK`、
  `semantic-policy=OK`、`drift=` 全 `MATCH`
  （`outputs/doctor-20261010-modelscope-after.txt`）。
- 真实桌面入口的一次受控探针
  （`fq.sciman.top:443` → xray → nginx → admission → CPA，`gpt-6.1-sol`，
  `stream=true`）：HTTP `200`、无 `X-CPA-Admission-Reason`、无
  `Retry-After`、无容量标记；`header_ms=1391`、`first_text_ms=11891`、
  `completed_ms=17047`、`text_delta_count=159`、`text_gap_max_ms=359`、
  `visible_output_tokens=163`、`failures=[]`
  （`outputs/probe-live-fq-sol-20261010.txt`）。
- 收尾后的 fresh strict doctor 仍是 `DOCTOR_CONTRACT_OK`（exit 0），PID
  `585417`、`PIN_MATCH=yes`（`outputs/doctor-20261010-modelscope-final.txt`）。
- 部署后的 24h admission journal 计数（`outputs/probe-admission-journal-20261010.txt`）：
  `MODEL_COOLDOWN_REJECTS=0`、`MODEL_PROBES=0`、`LANE_COOLDOWN_REJECTS=5`、
  `UPSTREAM_RESULTS=156`、`CAPACITY_TRUE=40`。即**新分支尚未被真实流量触发**
  （`model_cooldown`/`scope=model` 这两个串在旧世代不可能出现，因此 0 表示没有
  发生过，而不是没统计到）；它的行为证据只来自第 1 节的受控 A/B。

### 4. 契约冻结（仓库）

- `scripts/remote/cpa-admission.py`：新增 `capacity_scope()`；`Lease` 增加
  `model` / `model_probe` / `model_generation`；`LaneState` 增加模型级窗口、
  阶梯、探针节奏、上游 `not_before` 与代际戳；`acquire(alive, model=...)`
  在 lane 冷却判定之后、并发预算判定之前插入模型级冷却分支（放行
  `model_half_open` 探针，其余 `reason=model_cooldown`）；
  `release(..., capacity_scope=)` 按作用域归属，并让被模型档答复的 lane 探针
  一并解锁 lane（否则一个被停模型会永久挡住兄弟模型）；`snapshot()` 增加
  `cooldown_scope`（`lane`/`model`/`none`）与 `model_cooldowns`。
- `scripts/remote/cpa-admission.json`：`chatgpt-oauth` 声明
  `model_capacity_markers`，另两条 lane 显式为空表。
- `scripts/remote/cpa_policy.py`：`EXPECTED_MODEL_CAPACITY_MARKERS` +
  子集不变量 + 未评审 lane 必须为空（fail-closed）。
- `scripts/cpa_admission_risk_audit.py`：`REQUIRED_MODEL_CAPACITY_MARKERS`
  与新发现码 `lane-model-capacity-marker-scope` /
  `lane-model-capacity-scope-missing`。
- `scripts/remote/cpa-guardrail-doctor.sh`：`admission-health` 要求
  `/healthz` 带 `cooldown_scope ∈ {lane,model,none}` 且 `model_cooldowns`
  为 dict，旧世代不再算通过；`cpa-admission-integrity-pin.txt` 同步轮换。

## 变更内容

- 仓库：`2c6b3f4`（13 文件，见上节）。
- 远端：`/opt/cliproxyapi/` 的 `cpa-admission.py`、`cpa-admission.json`、
  `cpa_policy.py`、`cpa-admission.sha256` 四文件按第 2 节事务替换。
- 收据与驱动：`outputs/accept-modelscope-20261010.sh`、
  `outputs/accept-modelscope-20261010.ps1`、
  `outputs/deploy-admission-20261010.ps1` 及本节引用的 `.txt/.log`。

## 验证

- 本地 full gate：`pwsh -NoProfile -ExecutionPolicy Bypass -File
  scripts/run_gates.ps1` → `548 passed, 1 skipped, 269 subtests`；bandit /
  ruff check / ruff format --check / mypy 全绿
  （`outputs/gate-20261010-modelscope.txt`）。
- 单元与端到端：`tests/test_cpa_admission.py` 覆盖模型级隔离、探针代际校验、
  无模型归属回退到 lane、lane 探针解锁，以及 fake upstream 的
  `429 model_cooldown` / 兄弟模型 `200` 端到端；
  `tests/test_cpa_admission_risk_audit.py`、
  `tests/test_cpa_guardrails_script.py` 冻结审计与医生契约。
- 受控 A/B 与线上只读复验见第 1、3 节。

## 证据边界

- 本次修复的是**本机 admission 的拒绝作用域**：它消除一个模型容量窗口对兄弟
  模型的连带 `429`。它不改变上游当前容量，也不保证 `gpt-6-luna` 本身可用 ——
  点名模型的请求仍按上游答复被拒并回到客户端。
- 结论里那次 `retry_after=471` 的实测是本次会话的一次性 live 探针，收据只在
  会话内；仓库内可复查的复现是第 1 节 A2 行（同一上游形态、同一判定量）。
- A/B 的上游是假上游，只复刻「单模型容量口径 + 长 `Retry-After`」这一形态；
  它不证明真实上游的容量分布、配额窗口或是否存在账号级风控。
- 桌面 UI 侧验收（Cockpit Direct OAuth / Direct API 的实际报错文本、TPS 观感）
  只能由用户确认；本文件不宣称 UI 已验证。
- 一次探针的 `200` 不是长期健康结论；`first_text_ms=11891` 属上游首字延迟，
  与本改动无关，也不作为 TPS 基线。
- `MODEL_COOLDOWN_REJECTS=0` 同时说明：桌面侧在部署后还没有产生过
  `gpt-6-luna` 容量事件，因此**真实流量尚未走到新分支**；症状是否消失仍需用户在
  桌面上复现同一用法后确认。

## 已知残余

- 443 车道的 `$remote_addr` 是 xray 新建连接的源地址（VPS 自身），per-IP 限流
  退化为全局桶（`10r/s`、并发 `20`）。恢复 per-IP 语义需要 `xver=1` PROXY
  protocol + nginx `proxy_protocol`，属 v2ray-agent/xray 配置域，本次未动。
- Cockpit provider `fq.sciman.top` 本地登记了 **两个**客户端 key，其中
  `cmk_1789525447013_1`（`sha12=0aa6a4a69091`，48 字符）在真实网关上返回
  `401`；CPA `config.yaml` 的 `api-keys` 只有一条，与可用 key
  `cmk_1790213261974_2`（`sha12=06e5d9a0e3f2`，39 字符）一致
  （`outputs/probe-client-keys-20261010.txt`，只打印 id 与截断 sha）。
  该条目不解释 `429`，但会给桌面侧带来 401 噪声；`codex_model_providers.json`
  由 Cockpit 管理，本仓不外编辑，建议在 Cockpit 界面里删除或重新绑定该条目。
- `gpt-6-luna` 别名在上游目录中的可见性属于已知长期项（doctor 报
  `catalog_oauth_missing`）；模型级窗口本身不改变它。
- 1.3.65 sidecar policy 补丁按用户指示**不移植**；`a5c3f02` / `6a4b39a`
  记录的状态（契约保留、补丁不生效、保留一个 `.bak` 作对照）维持不变。

## 回滚

- 远端：`sudo sh /root/cpa-admission-modelscope-backup-20261010T041140Z/rollback.sh`
  （按变更前备份恢复四个文件并回到旧 sha），随后
  `systemctl restart cpa-admission.service`。Git 回滚不能代替远端恢复。
- 仓库：还原 `2c6b3f4` 的 13 个文件；doctor 的 `admission-health` 世代断言
  与 `-Apply` 投影互不影响，回滚后 doctor 会重新要求 `cooldown_scope`
  字段，需与远端生成物同批次回滚。
