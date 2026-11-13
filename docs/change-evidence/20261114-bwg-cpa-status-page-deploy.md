# BWG CPA 状态页上线（on-host daemon + nginx 壳）

日期：2026-11-14 · 执行：agent（用户确认方案后按写集执行）· 主机：bwg

## 变更摘要

CPA 状态页从"本地 CLI 经 SSH 采集"升级为 **VPS 常驻服务 + 浏览器直开**：
`https://fq.sciman.top:28317/cpa-status/`（继承 CPAMC 壳的 TLS + basic auth + 限流）。

## 写入集（全部经 `scripts/cpa_status_deploy.ps1`）

| 项 | 路径 | 说明 |
|---|---|---|
| 源码 | `/opt/cpa-status/cpa_status.py` | 与仓内 `scripts/cpa_status.py` 字节一致，sha256 `6e6d609797bd…a86c7`（audit MATCH）；覆盖前自动备份 `.bak.<ts>` |
| 服务 | `/etc/systemd/system/cpa-status.service` | root + NoNewPrivileges/ProtectSystem=full/ProtectHome=read-only/PrivateTmp；`--remote-serve --port 8319`（进程内采集，无 SSH） |
| nginx | `/etc/nginx/conf.d/cpamc-management.conf` | 插入 `location /cpa-status/`（auth_basic=cpamc.htpasswd + cpamc_rl 限流 + proxy 127.0.0.1:8319）；插入前备份，`nginx -t` 门禁通过才 reload |

## 验证（当次实测）

- 服务：`systemctl is-active` = active；`curl 127.0.0.1:8319/json` 返回完整文档（11 模型/17 渠道）
- nginx：错误凭据访问 `/cpa-status/` → **401**（basic auth 门禁生效）；loopback 直探 8319 → **200**
- audit receipt：`sync=MATCH service=active nginx=NGINX_PRESENT`

## 安全边界

- 页面监听**仅 127.0.0.1**（`serve()` 代码级守卫拒非回环绑定）；公网唯一入口在 nginx basic auth 之后
- 管理密钥仍只在 `/root/.cpa-management-key`（0600），daemon 在主机上读取，不传输、不打印
- 文档与 CLI/本地页同一套脱敏（邮箱掩码、无任何密钥回显）

## 回滚

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/cpa_status_deploy.ps1 -Rollback
```

（停用并删除服务与 /opt/cpa-status、还原 nginx 备份片段并 reload。）

## 关联

- 仓内提交：--remote-serve 模式 + 部署脚本（本次提交）；状态页本体 f852072/42e56b9
- 设计与数据面语义见 `cpa-status-view-tool` 记忆与 `scripts/cpa_status.py` 模块头
