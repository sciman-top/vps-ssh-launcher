# 2026-11-03 BWG admission 请求编码与原生流硬化

## 范围

- 唯一远端写入：bwg `cpa-admission` 专用部署事务。
- 未修改 `zz`、CPA 容器镜像、OAuth/API-key 凭据、并发、RPM、重试、冷却或
  模型路由。
- Claude OAuth lane 维持退役墓碑；本次不发送 OAuth generation。

## 变更

- `scripts/remote/cpa-admission.py` 在读取请求体前拒绝非 `identity`
  `Content-Encoding`，返回 `415 unsupported_content_encoding` 与
  `upstream_contacted=false`。CPA v8.0.51 会解码 `zstd` 请求体；此前 admission
  只检查原始字节，编码请求可能绕过 lane、用户图片与加密代理载荷保护。
- 原生 Gemini `:streamGenerateContent` 由路径识别为流式请求，并与
  Interactions `stream:true` 共用排队期早响应头与 SSE 心跳，避免长队列静默
  被客户端误判为网络失败或“吐字慢”。
- 新增排空式部署事务
  `outputs/deploy-admission-content-encoding-20261103.ps1`，同时轮换 admission
  源与完整性 pin。

## 验证

- `tests/test_cpa_admission.py`：`286 passed`。
- guardrail/policy/risk audit：`130 passed, 90 subtests passed`。
- 全量 pytest：`1062 passed, 18 skipped, 514 subtests passed`。
- Bandit、Ruff、Mypy 对 CPA 修改通过。全量 Gate 最终仅被两个并发代理硬化测试
  的既有格式问题阻断；本事务未修改这些文件。
- 部署 dry-run：
  `DRY_RUN_OK baseline=00490835362119d6ae5a63ef10ae8ec493e8f885173eb6b629d5c261dd4d0d74 candidate=df8d136fb9bcc873379d626db9b47338740c6e420c85b92c9443b9618ee4afb3 idle=true no_swap=true`。
- 第一次正式事务在 live 验收阶段错误地持有排它请求锁发送控制请求，因此收到
  503；事务自动执行 `ROLLBACK_VERIFIED`，恢复旧源与旧 pin。修正事务顺序后，
  正式部署再次通过 idle、完整性与控制请求验收。
- 最终结果：
  `ADMISSION_ENCODING_GUARD_OK`、`DEPLOY_OK`。
- 新源 SHA-256：
  `df8d136fb9bcc873379d626db9b47338740c6e420c85b92c9443b9618ee4afb3`。
- strict doctor：`DOCTOR_CONTRACT_OK`；所有投影文件哈希 `MATCH`；ChatGPT、
  Antigravity、WorkBuddy lane 为 `OPEN`，Claude 为惰性退役墓碑。

## 备份与回滚

- 失败事务备份：
  `/opt/cliproxyapi/backups/20261102T223457.713359380Z-admission-content-encoding-20261103`。
- 成功事务备份：
  `/opt/cliproxyapi/backups/20261102T223713.366789807Z-admission-content-encoding-20261103`。
- 回滚：从成功事务备份恢复 `cpa-admission.py` 与 `cpa-admission.sha256`，然后
  `systemctl restart cpa-admission`，执行 admission 完整性检查和 strict doctor。
  Git revert 不能代替远端文件恢复。

## 边界

- live 控制请求只验证编码拒绝，不接触 CPA/provider。
- 原生 Gemini 流队列行为由仓库端到端测试证明；没有为验收消费 Antigravity
  或 ChatGPT OAuth 生成额度。
- 长期 provider 配额、账号安全、模型质量、自然桌面回合和中国网络稳定性仍需
  自然流量观察，不由本次本地健康与控制验收外推。
