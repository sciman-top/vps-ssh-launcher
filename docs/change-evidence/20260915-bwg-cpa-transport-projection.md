# 2026-09-15 BWG CPA 传输契约与投影通道修复

用户授权在保留公网 Nginx 8443、随机 capability path 和单一客户端 key 的前提下，
持续完成 BWG CPA 的风控修复；本次只处理 `bwg`，不连接或修改 `zz`。

## 改动

- doctor/Apply 固化 Nginx 传输契约：`client_max_body_size 32m`、
  `proxy_buffering off`、`proxy_read_timeout 300s`、`proxy_send_timeout 300s`。
  Apply 只验证这些既有配置，不改变公网路径、IP 限流值或客户端 key。
- doctor 的当前 access-log 汇总新增 `upstream_statuses`、
  `status_upstream` 和 `limit_markers`；保留 CPA error 文件增加 overload 标记数。
  两段都明确只用于定位，不外推为上游账号恢复或封禁结论。
- 修复 Windows 上大于 CreateProcess 命令行限制的 guardrail 投影：短脚本仍保持单次
  Base64 执行，超长脚本改为以 12,000 字符分片写入随机 `/tmp` 文件（模式 600），
  解码执行后删除；任何中断会在 `finally` 尝试删除临时文件。
- 测试改为识别 WSL Bash 的 `/mnt/<drive>/...` 路径，并经二进制 stdin 输送 harness，
  避免 Windows 在 `bash -c` 前抢先展开 Bash 变量。

## 部署与回滚

- 远端 `-Apply` 的备份目录：
  `/root/cpa-guardrails-backup-20260914T160026Z`。本次投影只更新已有的
  guardrail 管理文件；不轮换路径或 key，不修改 CPA 上游凭据。
- Apply 在短暂 CPA 重启窗口记录一次 `curl: (56) Recv failure: Connection reset by peer`；
  后续 readiness 为 HTTP 200，脚本完成并输出 `GUARDRAILS_APPLIED`。若需要回滚，
  恢复该目录中的文件并按 guardrail 原有回滚顺序重启 CPA、重载 Nginx/fail2ban。

## 验证

- 本地完整 gate：`115 passed, 1 skipped, 69 subtests passed`；Bandit、Ruff、
  format 和 Mypy 均通过。此前 WSL Bash 的 updater 语法检查与备份保留测试均通过。
- 远端 Apply：`READY_STATUS=200`，catalog 仍包含 OAuth Luna、r1 和 GLM，未暴露
  r2/DeepSeek；随机路径与客户端 key 未在输出中记录。
- Apply 后 fresh strict doctor：`DOCTOR_CONTRACT_OK`；`gateway-transport=OK`，
  8443 仅一个公网 listener、8317 仅 loopback、有效随机路径未授权为 401、裸路径和
  错误路径均为 404，Nginx/Compose/updater 语法均通过。
- 当前 24 小时 access log 中 502/503 与同值 `upstream_status` 对齐，
  `limit_markers` 仅有 `PASSED/PASSED` 或未进入限流区的位置，不能归因于入口限流。
  保留错误文件中有 overload 标记；这支持优先排查上游容量，不构成账号状态结论。

## 边界

- `zhipu-plan -> glm-5.3-flash` 仍处于配置中。其供应商计划是否允许经 CPA 转发是
  产品/合规决定，不由网关技术控制解决；本次未删除或改写该路由。
- 单一客户端 key 按用户决定保留。因此入口只能继续限制无效 key 的暴力尝试和来源 IP
  的突发，不能区分已取得该 key 的不同合法客户端；不把 Nginx 阈值描述为上游账号配额。
