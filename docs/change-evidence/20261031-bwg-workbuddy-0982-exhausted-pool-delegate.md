# BWG WorkBuddy 0.9.82：全池冷却/积分耗尽改为交还宿主选择器（2026-10-31）

## 目标与根因

用户报告 ChatGPT desktop 经 CPA client key 调 DeepSeek 频繁报
`exceeded retry limit, last status: 429`、断流、`maintenance_in_progress`、
`502 Bad Gateway`。只读取证结论：

- 18:43:23/18:52:20/18:59:04（CST）CPA 与 admission 三次成对重启，各自造成
  断流、502、90 秒 `stale_write_ahead_cooldown` 429 与维护窗 503；
  `high demand` 文案在 52 天网关日志与容器日志中零命中，与本项目无关。
- WorkBuddy 三凭据处于上游 DeepSeek 模型级限流（code 6004）；
  19:35:17/19:35:19 两条请求 22ms/63ms 快失败
  `oauth_pool_unavailable: No eligible WorkBuddy account`（HTTP 500）。
- CPA 宿主把插件 SchedulerPick 的 `Reject` 经 `wrapRequestStopError`
  终止整个请求，混合 provider 回退（OpenCode）不生效；
  `max-retry-credentials=0` 只覆盖"候选仍在"的路径。

## 变更（仅插件，不改 admission/config/凭据）

- `scripts/patches/workbuddy-v0.8.89-exhausted-pool-delegate.patch`
  （sha256 `506485b6…`）：credits 模式下候选池仅因**可恢复冷却或积分耗尽**
  为空时返回 `Handled:false` 交还宿主选择器（OpenCode 可接手）；
  11128 channel reject、403+11140 隔离、积分护栏、disabled 凭据保持硬拒绝；
  新增拒绝/让渡原因计数日志。`VERSION`/`main.go` 版本号 0.9.81 → 0.9.82。
- 构建：`outputs/build-workbuddy-0982-20261031.ps1` 于既有 0.9.81 树
  `/root/cpa-workbuddy-0981-20261028` 复制为 0982 后应用；隔离容器
  `gofmt`(本仓文件) + `go vet` + `go test -count=1 ./...` 通过；
  `workbuddy.so` sha256 `81f0ccf3…`。
- 部署：`outputs/deploy-workbuddy-0982-20261031.ps1`（commit `818a18d`）。

## 部署事务（13:49:07–13:49:26 UTC）

两次 flock 排空尝试被持续流量饿死（admission 每请求持非阻塞共享锁），
13:31 并行事务记录了 `RESTARTED` 但容器 `StartedAt` 未变（假重启）并回滚。
改用 2026-10-25 先例：入口 vhost 临时 `return 503` → 在途排空（13 秒）→
原子替换 `.so` → `docker compose restart` → 以 `StartedAt` 变化验证真实重启
→ 注册 `version=0.9.82` → readiness `HEALTH_OK` → 无新 lane hold →
凭据字节不变 → 入口配置按 sha256 字节一致恢复。

- 备份：`/opt/cliproxyapi/backups/20261031T134907.528202434Z-plugin-0981-to-0982`
  （含 compose/config/nginx conf/原插件/auth sha256）。
- 维护窗副作用：13:49:21/13:49:23 两条入口 503（190 字节），入口随即恢复。

## 验证与边界

- 单测固化新契约：仅冷却池必须让渡（`handled=false`）、受限池保持硬拒绝、
  积分耗尽池让渡；全 disabled 保持拒绝。
- 换装后自然流量（13:49:20Z 起）：WorkBuddy 44 completed / 0 not_completed；
  容器注册行 `version=0.9.82` 1 条。未制造 6004 或 OAuth 失败流量，
  未清冷却、未换凭据/IP。
- 13:56 strict doctor `DOCTOR_CONTRACT_FAILED` 为并行会话已提交未部署的
  admission 改动（commit `145ba89`，`cpa-admission.py`+pin 与线上不一致）
  所致，与本插件换装无关；本事务验证以事务内
  registration/readiness/lane-hold/auth 四项为准。
- 边界：换装证明插件加载与路由语义，不证明上游 6004 频率下降或账号安全。

## 回滚

恢复备份目录中的 `workbuddy.so`（0.9.81@`36d8b720…`）并重启 CPA；
仓库侧 revert 补丁与脚本后重建。Git 回滚不替代远端恢复。
