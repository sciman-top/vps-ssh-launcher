# BWG CPA 状态页的模型别名与凭据脱敏

2026-11-26 22:52（北京时间），使用 `scripts/cpa_status_deploy.ps1 -Profile bwg -Deploy` 部署状态页代码并重启 `cpa-status.service`。

- 来源校验通过。部署前 SHA-256 为 `5e3283e74d3a76bc09d1d2648bc46b83e0d6e8d79b12c27539f6e46776fc5a29`，部署后为 `0ee386c01c90bb19d7ecaa1105b20da6c74ddd75a90fd4ee84da493594247a39`。
- `STATUS_JSON_OK` 和 `STATUS_HTML_OK`。WorkBuddy `-wb` 归属 WorkBuddy CN，裸名归属 BigModel Coding Plan。凭据文件名中的邮箱通过短哈希脱敏。部署审计返回 `sync=MATCH`，状态服务运行中。
- 对比部署前后的 CPA 配置哈希、容器启动时间和凭据计数。活跃凭据为 Antigravity 2、Codex 1、WorkBuddy CN 8，另有 WorkBuddy INTL 停用凭据 7 个。BigModel `glm-5.3-flash` 非 OAuth 缓存探针两次均读到 4,096 / 4,114 个缓存 token，返回 `HEALTH_OK`。
- 回滚时从 `/opt/cpa-status/cpa_status.py.bak.20261126145218` 恢复 `cpa_status.py`，重启 `cpa-status.service` 后复核服务、JSON 和源文件哈希。
