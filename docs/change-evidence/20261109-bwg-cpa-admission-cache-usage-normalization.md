# 2026-11-09 BWG CPA：provider cache usage 归一化与受控验收

## 目标与边界

- 目标：修正 admission 对 Anthropic/Claude、Interactions 和部分
  WorkBuddy/plugin usage 形状的缓存分母与嵌套事件识别。
- 改动只影响 `/healthz` 的观测聚合，不改变请求字节、准入、模型路由、
  provider 重试、并发、冷却或凭据。
- 未读取、复制、轮换或重新登录任何 OAuth 凭据；未修改 IP、UA、设备画像。

## 仓库变更

- `scripts/remote/cpa-admission.py`
  - Anthropic/Claude 的 `input_tokens + cache_read + cache_creation` 归一化为
    总输入，cache write 进入分母但不伪计为命中。
  - 支持 `total_input_tokens`、`total_cached_tokens`、
    `total_output_tokens`、`cache_read_tokens`、`cache_write_tokens`。
  - 支持 `interaction.usage`、`metadata.total_usage` 和
    `metadata.usage` 事件形状。
- `scripts/remote/cpa-admission-integrity-pin.txt`
  - 新 pin：`476e1f24b2ea291c554542f1a4f60672aed5fa9cb39062f4882131ab094b85dc`
- 测试：
  - `tests/test_cpa_admission.py`
  - `tests/test_cpa_admission_telemetry.py`
- 提交：`5128bb9 fix(admission): normalize provider cache usage telemetry`

## 仓库验证

- admission/telemetry/credits/risk/natural-traffic 相关：
  `419 passed`。
- 全量 pytest：`1211 passed, 18 skipped, 595 subtests passed`。
- `ruff check`、本切片 `ruff format --check`、bandit：通过。
- full gate 最终 `lint:format` 失败只来自并行工作树中的三个订阅链路测试；
  本切片格式检查通过，未修改这些并行改动。

## 部署前预检

- live admission：
  `213d92c2ad729357ad7ca456f85b70b35390d21990097f9c88b3d58a316fe606`
- candidate admission：
  `476e1f24b2ea291c554542f1a4f60672aed5fa9cb39062f4882131ab094b85dc`
- admission config：
  `f3a45d75778d2b76ee9507a799bcd71f2e26d9680af16f869c1278ab7fb58716`
- integrity：`ADMISSION_INTEGRITY_OK`
- dry-run：`DRY_RUN_OK idle=true no_swap=true`
- 部署前 healthz：四条 lane `inflight=0`、`pending=0`，无冷却。

## 投影事务

- 入口：
  `outputs/deploy-admission-telemetry-20261108.ps1`
  `-ExpectedLivePin 213d92c2…`
- 事务执行维护锁、traffic guard 排空、备份、源码与 pin 原子替换、
  integrity、仅重启 `cpa-admission.service`，失败自动回滚。
- 备份：
  `/opt/cliproxyapi/backups/20261109T154141.115345904Z-admission-telemetry-20261108`
- 回执：

```text
REQUEST_GUARD_EXCLUSIVE_OK
ADMISSION_IDLE_OK
ADMISSION_INTEGRITY_OK
TELEMETRY_CONTRACT_OK series=0
ADMISSION_REGRESSION_GUARD_OK
DEPLOY_OK pin=476e1f24b2ea291c554542f1a4f60672aed5fa9cb39062f4882131ab094b85dc
```

## host_loaded 验收

- live SHA 与 `/etc/vps-ssh-launcher/cpa-admission.sha256` 内容均为
  `476e1f24b2ea291c554542f1a4f60672aed5fa9cb39062f4882131ab094b85dc`。
- live 文件：`root:root 0755 209387 bytes`；pin 文件：`root:root 0644 65 bytes`。
- integrity checker：`ADMISSION_INTEGRITY_OK`。
- service：`active/running`，`NRestarts=0`，`ExecMainStatus=0`。
- strict doctor：`DOCTOR_CONTRACT_OK`，13 项 projection drift 全部 `MATCH`。
- 在役 OAuth lane：
  - `chatgpt-oauth OPEN`
  - `antigravity-oauth OPEN`
  - `workbuddy-oauth OPEN`
  - `claude-oauth RETIRED no_oauth_route`

## 受控与自然流量验收

- 部署文件解析回放：OpenAI 总量型、Anthropic 分段型、Interactions
  嵌套型全部通过，输出 `USAGE_NORMALIZATION_LIVE_OK`。
- admission 回归护栏：编码请求在触达上游前返回 415
  `unsupported_content_encoding`，`ADMISSION_REGRESSION_GUARD_OK`。
- `cpa-health.py readiness`：`HEALTH_OK`。
- `cpa-health.py generation`：`HEALTH_OK`。
- `cpa-health.py cache-canary`：第二样本 `4096/4114`，命中率 `0.9956`。
- 单次零成本真实路径请求：
  - 请求模型 `hy4free`
  - `HTTP 200`
  - 回显 `hy4-free`
  - `usage_in=26`、`usage_cached=0`、`usage_out=8`
  - admission `/healthz` 随即出现 `hy4free` 的 1 个 usage 样本。
- 部署后自然流量观察窗至少 15 分钟（2026-11-09T15:41:41Z
  至约 15:56:41Z）：
  - passthrough 200 持续正常；
  - admission journal 中 `Traceback|CRITICAL|ERROR|lane_reject|
    request_guard_refused` 计数为 `0`；
  - CPA 容器近 15 分钟
    `TOS_VIOLATION|invalid_grant|11140|insufficient_quota|6008`
    计数为 `0`；
  - 四条 lane 最后快照均为 `inflight=0`、`pending=0`、`hold_reason=null`；
  - service 保持 `active/running`、`NRestarts=0`、`ExecMainStatus=0`；
  - integrity 仍为 `ADMISSION_INTEGRITY_OK`。

## 剩余风险与证据边界

- WorkBuddy 仍有 3 个 INTL 凭据各出现一次 `6008 Credits exhausted`；
  这是额度状态，不是封号证明。当前策略为保留凭据、零成本模型豁免，
  不使用清冷却、换号、换 IP 或重登续打。
- 本次不改变 `request-retry=0`、`max-retry-credentials=3`、
  `session-affinity-subagents=false` 或 WorkBuddy `reserve_credits`。
  这些参数的变更需要新的自然流量证据或业务连续性裁决。
- Claude OAuth lane 仍退役；本次 Anthropic 解析修复不会立即产生现网
  Claude 样本。
- 本记录证明本次投影、受控调用和自然流量的当前边界，不证明消费级 OAuth
  获得官方认可，也不提供免封、免限流或免降智保证。

## 回滚

- 在 admission 空闲并持有维护锁时，从上述备份恢复
  `cpa-admission.py`、`cpa-admission.sha256`，运行 integrity checker，
  重启 `cpa-admission.service`，再复验 healthz 和 strict doctor。
- Git 回滚使用 `5128bb9` 的 revert；Git 回滚不能替代远端备份恢复。
