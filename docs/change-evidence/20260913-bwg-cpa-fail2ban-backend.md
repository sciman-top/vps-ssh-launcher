# 2026-09-13 BWG CPA 防护收口

- 用户授权继续执行上一轮修复与优化；仅 BWG。未变更 key、随机路径、模型或 CPA 容器。
- Fresh read：cpa-gateway active，但 `get ... logpath` 返回 No file is currently monitored，
  journalmatch 也为空。`defaults-debian.conf` 设置 backend=systemd，而该 jail 只配置
  Nginx logpath，导致不读取鉴权失败。此前正则匹配测试不足以证明运行计数生效。
- Nginx cpa_safe 格式无时间戳，无法可靠按 findtime 判断日志事件发生时间。

## 变更与验证

- 备份 `/root/cpa-fail2ban-backend-20260913/` 中 nginx.conf / jail.conf 分别对应
  `/etc/nginx/conf.d/cpa-gateway.conf` 与 `/etc/fail2ban/jail.d/cpa-gateway.conf`。
- 仅在安全日志末尾添加 `time=[$time_local]`，现有前缀与 failregex 保持兼容；
  不引入请求路径、Authorization 或上游 URL。
- cpa-gateway 显式 backend=polling，logpath 加 tail，避免启用时回放旧无时间戳失败日志。
- nginx -t / fail2ban-client -t 成功，reload Nginx，reload --restart cpa-gateway 成功；
  其他 jail 不重启。非阻断 allowipv6 默认值警告与此次文件后端修复无关。
- 本机直连公网、TLS 校验、无代理：单次错误 key 请求 HTTP 401；随后运行 jail
  Total failed=1 / Currently failed=1，banned=0；File list 指向实际 Nginx 日志。
  未进行阈值封禁实验，以免阻断正常客户端。
- strict doctor 通过，新增 safe-log-timestamp=OK 与 fail2ban-file-monitor=OK。
  CPA 仍 v7.2.156 / restart count 0；timer enabled/active；路由 401/404/404 正确。
- 本仓 guardrails 的日志生成兼容迁移旧格式，doctor 对上述缺口返回失败；
  auth 文件输出改为权限统计，不输出可能带邮箱的文件名。
- `.gitattributes` 为 shell 脚本固定 LF，避免 Windows checkout 转成 CRLF 后无法直接执行。
- 备份约 5.3 MB，维护备份约 2.7 MB，根卷剩余约 33 GB（使用率 13%）；本次无删除依据。

## 回滚与未决项

仅恢复备份 nginx.conf / jail.conf 到各原路径，验证 nginx -t 与 fail2ban-client -t，
然后 reload Nginx 和 reload --restart cpa-gateway。回滚会恢复原有防护缺口，
只有本次变更引发独立故障时才执行。

key 历史暴露撤销尚未执行：服务器无法发现所有消费旧 key 的外部设备配置。
需要用户提供应同步的客户端清单/配置位置，或明确接受旧 key 撤销引发的短时中断。
保留双 key 不构成撤销历史风险，因此未虚增新 key。下一次自然 timer tick 与长期
账号安全/质量/缓存率也不能以短时探针代替；未改变本次授权边界或创建永久监控系统。
