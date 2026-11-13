# 阿里云与 HOME 的 frp 中继

本页记录隔离的 frps/frpc 部署与其验证边界。它只承载一条受控 TCP 映射，不能将 ECS 变成任意目标或跨境流量中转。用途、单位接入授权和阿里云条款仍是独立门槛。

## 拓扑与权属

```text
受控客户端 → ECS:public-port → frps(TLS/token, loopback proxy bind)
          → HOME frpc → HOME loopback landing service
```

- 阿里云 5200 控制面使用 `/opt/frp-aliyun/` 与 `frps-aliyun.service`。原 `/opt/frp/`、`frps.service`、27000/52443 映射保持独立，不能将其当作新部署或随意改写。
- HOME 二进制与私有运行配置在 `%USERPROFILE%\.frp\`；控制面任务为 `vps-ssh-launcher-frpc`。token 使用 `DPAPI CurrentUser`，服务端证书 pin 到本地文件。
- 中继映射只使用本机 loopback 监听服务。ECS 的 `proxyBindAddr` 为 `127.0.0.1`，`allowPorts` 精确限定映射端口。
- 家庭电脑睡眠、注销或离线会切断映射。定时任务登录自启不承诺 24 小时可用性。

## 首次或修复部署

先只读检查私有 `%APPDATA%\vps-ssh-launcher\target.json` 中 `aliyun` profile 的主机、端口、用户、密码认证字段；仓库不保存这些值。执行会进行文件和只读远端预检：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\frp_relay.ps1 -Setup -Apply
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\frp_relay.ps1 -Status
```

预检核对官方 frp release 的 SHA-256、Windows 可执行文件、已 pin 的 SSH 身份、远端 root/架构与当前监听。未获得额外授权时，不应继续替换现有服务、安全组或其他代理配置。

远端安装前须保留当前 unit、配置、二进制及证书/密钥的安全副本；部署后先验证 `frps verify`、服务 active、期望监听，再启动 HOME 控制面。失败时恢复原 unit/config 并 `systemctl restart` 回滚。对于已经建好的独立 5200 部署，恢复副本是 `/opt/frp-aliyun/frps.toml.pre-bind-5200`。

## 分层验收

| 层 | 最低证据 |
| --- | --- |
| `filesystem_projected` | 官方归档和已安装 frpc 的 SHA-256 一致；token 文件不以明文进入工作区 |
| `host_loaded` | frps unit active、指定端口监听；frpc 任务 active 且仅一个受管实例 |
| 公网控制面 | 不只是 TCP 连通，而是对 frps TLS ServerHello 的成功读取 |
| frp 登录/映射 | frpc 日志出现 `login to server success` 与 `start proxy success`；ECS 只新增受控映射监听 |
| `live_accepted` | 使用独立临时 REALITY 配置，只替换中继地址/端口；真实 HTTP 响应成功并确认出口属于预期下游 |

任一层失败都应标为 `BLOCKED` 或 `NOT_RUN`，不得用主机监听、TCP 握手、配置校验或旧收据代替 `live_accepted`。云端安全组控制台展示与公网可达性不一致时，以新鲜的真实探针为准，并检查规则是否真正绑定实例或是否存在额外网络层。

## 安全组与可回读性

控制面端口只允许固定的受控源 `/32`。规则的新增和修改都要在控制台或已认证的 API 读取确认；未认证 SDK 调用不能作为读取成功的证据。禁止将现有所有规则整体覆写为最小集合，避免误删 SSH 或其他服务入口。

## 停止与回滚

停止 HOME 映射不会删除 ECS 服务：

```powershell
Stop-ScheduledTask -TaskName vps-ssh-launcher-frpc
```

不把删除 27000/52443 旧服务当作新部署验收的一步；它们的影响面须单独评估、备份与切换。若需要停止新服务，只操作 `frps-aliyun.service`，并保留其恢复文件；防火墙/安全组按本次精确新增规则回退。不要把 Git 回退当作远端恢复。
