# 2026-10-08 BWG admission thinking suffix 修复与部署验收

## 依据与范围

- 用户授权持续审查并修复 BWG CPA admission；源码提交 `962ad1f`。
- CPA v8.0.20 `internal/thinking/suffix.go` 的 `ParseSuffix` 接受末尾括号形式。旧 admission 对完整模型名 exact match，导致合法的 `gpt-6.1-sol(low)` 等请求绕过显式 lane。
- 修复仅在 lane 判定时解析基础模型；原始请求字节及 reasoning 参数不改写。
- 远端仅替换 `/opt/cliproxyapi/cpa-admission.py` 并重启 admission；CPA 容器、Nginx、凭据、路由配置与限额保持原值，ZZ 无写入。
- 驱动 `outputs/deploy-admission-20261008.ps1` 使用共享锁、空闲与冷却前置检查、哈希校验、备份及失败恢复。回滚脚本现在拒绝未知参数，`--dry-run` 仅校验备份。

## 本地验证

- 新增 15 项后缀回归；本地 HTTP fixture 证明原请求原样转发，冷却中的后缀请求返回 429 且不调用上游。
- 当前代码完整门禁：`515 passed, 1 skipped, 214 subtests passed`；Bandit、Ruff check/format、mypy（54 files）通过。跳过项为默认 opt-in 的真实 SSH pytest；本轮另已真实运行 BWG/ZZ wrapper check，exit 0。
- PowerShell 部署脚本 parser 无错误，`git diff --check` 通过。

## 真实部署与后验收

- UTC `2026-10-08T03:52:12Z` 完成事务；`DEPLOY_OK`、`STAGE_CLEANED`、`DRIVER_RESULT=PASS`、runner exit 0。
- 旧 SHA256：`e7a5e108916e8fd4923a2423207187a80dc0bd25fb72436fc2c95a5d7e3e1ae3`。
- 新 SHA256：`3eec9fcf0966759bd76ed2e48e066320bed0695701477fce49fc91cfd8eb09a4`，后验收再次直接读取远端文件匹配；admission PID `509543`。
- 严格 doctor UTC `03:56:04Z` exit 0：九项 projection MATCH、admission health/syntax OK、`DOCTOR_CONTRACT_OK`；CPA 容器仍为 v8.0.20，restart=0。
- 单次公网 HTTPS Responses 流式请求 `gpt-6.1-sol(low)`，UTC `03:55:41Z`，零重试：HTTP 200、completed、PASS；首正文 3.718s、总 6.031s、output 163、reasoning 0、正文首尾区间约 84.11 visible tokens/s。
- request ID `edc437b848cc49d6adc0dea342aca623` 与远端 journal 关联：`lane=chatgpt-oauth model=gpt-6.1-sol status=200 capacity=false waited_ms=0`。这直接证明部署后的合法 suffix 已进入 admission，而非 passthrough。
- 未在真实账号上制造 cooldown；冷却阻断由本地 HTTP fixture 验证。

## 回滚

- 备份目录：`/root/cpa-admission-floatfix-backup-20261008T035212Z`（沿用 driver 名称，内容对应本次 suffix 事务）。
- 已执行只读预演：`bash /root/cpa-admission-floatfix-backup-20261008T035212Z/rollback.sh --dry-run`，exit 0、`ROLLBACK_DRY_RUN_OK`。
- 实际恢复使用同一命令去掉 `--dry-run`；恢复旧 SHA 后需同步仓库版本，否则 strict doctor 将按设计报告 drift。验收未执行实际回滚。

## 证明边界

- 本切片已证明 repo_verified、filesystem_projected、host_loaded 与 suffix 公网 live_accepted。
- 受控短输出/low 推理样本不证明长上下文、max 推理、所有模型或长期 quota 健康；不能保证永久无 capacity/429、无封号或无模型质量变化。
- 最新 doctor 的 24h admission 聚合：capacity events=0，rejects=2（queue_timeout=1、downstream_gone=1）；因此仍存在真实排队超时边界。
- `auto` 动态解析及未知模型仍不属于本次显式 lane 覆盖修复，不能把配置 coverage 通过解释为所有动态路由都已受控。
