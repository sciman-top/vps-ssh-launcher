# 2026-09-13 撤除过度保守的 CPA 总并发限制

用户认可撤除全入口并发 3 的建议并授权实施。仅 BWG，本次精确删除
`limit_conn_zone $server_name zone=cpa_total:1m;` 和 `limit_conn cpa_total 3;`。
保留每 IP 并发 6、10r/s、burst 20、auth_request、fail2ban、随机路径、凭据和冷却。

依据：[Nginx 官方 limit_conn 文档](https://nginx.org/en/docs/http/ngx_http_limit_conn_module.html)
说明多个限制叠加，server_name key 限制整个 server；HTTP/2 和 HTTP/3 并发请求
分别计数。因此全局 3 会让独立上游相互占用，不是单 OAuth 官方配额。

备份 `/root/cpa-remove-global-limit-20260913/nginx.conf`；修改前断言恰有两条目标，
原子写入，nginx -t 成功后 reload。无需 CPA 重启。

公网 TLS、无代理受控验证（仅保留不完整请求体，不提交生成到上游）：
- 保持 3 个请求，第 4 个 models 请求 HTTP 200，14 个模型。
- 同一 IP 保持 6 个请求，第 7 个 models 请求 HTTP 429。
- 释放后 models HTTP 200。

doctor 改为校验已有每 IP 并发 6，不再要求过时的全局并发 3。
回滚只恢复本次 Nginx 备份，nginx -t 后 reload；该回滚会恢复较严格上限，
仅在此变更造成独立问题时采用。历史 evidence 保留，但 README 明确当前状态。
没有新的全局阈值、账号管理系统、永久监控或上游压力测试。
