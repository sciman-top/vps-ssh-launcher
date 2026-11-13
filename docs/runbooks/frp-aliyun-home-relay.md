# 阿里云 frp 中继（BWG 常驻终态）

本页记录已实战验收的 H-frp 中继。终态为 `OFFICE → ECS:52443 → BWG frpc → BWG 本机 REALITY:443 → 境外`，BWG 主动外连，因此不依赖 HOME 电脑在线。它只承载一条受控 TCP 映射，不能将 ECS 变成任意目标或通用跨境中继；用途、单位接入授权与阿里云条款仍是独立门槛。

旧版 HOME 落地与 5200 控制面已退役。HOME 不要运行重复映射；ECS 的 `/opt/frp-aliyun`、`frps-aliyun.service` 与 5200 也不属于终态。保留其恢复材料仅用于历史回退，禁止重新启动而未重新评估安全组和端口占用。

## 拓扑与权属

```text
受控客户端 → ECS:52443 → frps(TLS/token)
          ← BWG frpc（主动连接 ECS:27000）→ BWG:127.0.0.1:443
```

- ECS 终态使用 `/opt/frp/frps`、`/opt/frp/frps.toml` 与 `frps.service`：控制端口 27000，映射端口 52443，TLS 强制与 token 认证，允许端口严格限定为 52443。
- BWG 使用 `/opt/frp-bwg/frpc`、`/opt/frp-bwg/frpc.toml` 与 `frpc-relay.service`：`User=nobody`、配置文件仅 `root:nogroup` 读写、保护系统目录，`Restart=on-failure`。
- frpc 映射只能是 `127.0.0.1:443 → ECS:52443`，不做通配转发。ECS 安全组对 27000 只放行 BWG 来源；52443 只放行实际受控客户端来源，7000/5200 死规则已删除。
- TCP 模式下数据实际经过 ECS；ECS 3Mbps 是共同带宽上限。它不是零中间节点方案，长期无 ECS 依赖的形态仍是独立宽带与 WireGuard。

## 首次或修复部署

先只读检查私有 `%APPDATA%\vps-ssh-launcher\target.json`；仓库不保存凭据。终止当前 HOME frpc 任务前确认没有其他必需映射，随后执行：

```powershell
Stop-ScheduledTask -TaskName vps-ssh-launcher-frpc
Unregister-ScheduledTask -TaskName vps-ssh-launcher-frpc
.\\.venv\\Scripts\\python.exe -m pytest tests/test_frp_bwg_relay.py -q
```

终态主机只读核验使用受控私有 profile 与已登记的 SSH 身份，不启动额外的 frpc。验证 BWG 与 ECS 的 service 状态、版本、监听和重启策略；不得以配置语法成功代替真实客户端验收。

远端安装前须保留 unit、配置与二进制副本；部署后先 `verify`、检查服务 active 与端口，再从受控客户端做真实 REALITY 访问。失败时恢复原配置/unit 并重启对应服务；不要删除 ECS 中用于恢复的备份。

## 分层验收

| 层 | 最低证据 |
| --- | --- |
| `filesystem_projected` | 已安装 frpc/frps 为 v0.72.0；BWG 配置文件权限限为受管身份；token 不以明文进入工作区 |
| `host_loaded` | ECS `frps.service` active 且 27000/52443 均在监听；BWG `frpc-relay.service` active、enabled，且存在到 ECS:27000 的控制连接 |
| frp 登录/映射 | ECS/BWG 日志出现登录与 `relay-52443` 的 `start proxy success`；BWG 只映射自己的 `127.0.0.1:443` |
| `live_accepted` | v2rayN **复制** `bwg-reality-vision`，只把地址改为 ECS 公网地址、端口改为 52443，保留 REALITY 参数不变；实际打开境外网页成功 |

任一层失败都应标为 `BLOCKED` 或 `NOT_RUN`，不得用主机监听、TCP 握手、配置校验或旧收据代替 `live_accepted`。云端安全组控制台展示与公网可达性不一致时，以新鲜的真实探针为准，并检查规则是否真正绑定实例或是否存在额外网络层。

## 安全组与可回读性

27000 仅允许 BWG 当前出口 `/32`；52443 仅允许 Office 当前出口 `/32`。规则的新增和修改都要在控制台或已认证 API 读取确认；未认证 SDK 调用不能作为读取成功的证据。禁止整体覆写安全组，避免误删 SSH 或其他服务入口。

## 停止与回滚

如临时停止中继，只停 BWG 侧主动连接的客户端，保留 ECS 服务：

```powershell
.\.venv\Scripts\python.exe ssh_tool.py --profile bwg run --command "sudo systemctl stop frpc-relay.service"
```

恢复后应复验 BWG、ECS 服务和真实客户端访问；不要用单独的 Git 回退代替远端恢复。客户端建议保留独立别名与原 `bwg-reality-vision`，以便回切本地核实入口。
