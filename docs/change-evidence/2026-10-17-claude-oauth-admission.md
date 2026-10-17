# BWG Claude OAuth admission 风险控制部署

## 范围与结果

- 用户授权连续执行审查建议；仅 BWG，保留 Claude/Antigravity 开放状态、凭据、公网 Nginx 路径和 CPA 容器。
- 2026-10-17 11:07 UTC，在全部 lane 和 8317/8318 数据连接连续空闲后，仅替换 admission Python、JSON、systemd unit、完整性 pin，并重启 admission。
- 代码提交：`81f225d`、`c63a558`。脚本：`outputs/deploy-cpa-oauth-risk-20261017.ps1 -Execute`，使用共享严格 SSH 传输。
- 返回 `DEPLOY_RESULT=HOST_LOADED_NO_GENERATION`、`FENCE_REMOVED=true`；另行读取 iptables 得到 `FENCE_ABSENT`。
- 备份：`/root/cpa-admission-oauth-risk-backup-20261017T110706Z`，保留且未裁剪。

## 验证

- 最新受影响测试：165 passed，25 subtests passed；ruff check、受影响测试 format check、diff check、嵌入载荷及 pin 字节一致性通过。
- 前序完整门禁：640 passed、1 skipped、304 subtests passed；build、Bandit、ruff check 通过，修正格式后 format 63 files、mypy 63 files 通过。
- 部署事务本地模拟：成功路径与回滚路径通过；保留风险状态、清除围栏，无 Docker 操作。
- `scripts/cpa_bwg_guardrails.ps1 -Profile bwg` 返回 `DOCTOR_CONTRACT_OK`，所有投影项 MATCH，两个风险状态权限 OK。
- 部署事务读取两个 OAuth lane 的 `strict_backoff=true`、`state_persisted=true`、`state_write_failed=false`。
- 远端安装代码模拟：403、无期限429、100000秒429、529、SSE账号受限错误共5项 PASS；二次请求被阻断，状态重新加载后仍阻断；`real_provider_calls=0`。
- doctor 显示 CPA v8.0.31，容器启动于 04:06:03 UTC，早于本次 admission 部署，restart=0。额外容器查询为 Up 7 hours。首次辅助查询误用容器名，随后以实际 `cli-proxy-api` 清单及 doctor 核对。

## 回滚与边界

- 事务失败自动恢复四个文件并重新启动 admission；绝不恢复较旧风险状态。人工回滚同样应保留更新的 hold/not_before，恢复前备份、核对文件，并在空闲窗口操作。
- 未重启 CPA，未改变凭据或账号身份，未调用真实 Google/Anthropic 生成接口。
- 独立子代理复核因任务交接故障未取得；由主代理串行接管复核，补修非法有符号 Retry-After。不能声明独立复核成功。
- CPA 上游重试头丢失仍是依赖边界，本地未知期限停流仅降低重复调用风险。第三方 OAuth 使用条款风险、真实账号可用性、自然流量质量与 Auto 语义均未因此获得验收。
- doctor 的保留日志观察到7天内1次模型替换告警；属于质量观察信号，不作为本次已消除的风险。无真实质量基准调用。
