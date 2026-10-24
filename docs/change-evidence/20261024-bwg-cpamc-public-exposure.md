# BWG CPAMC 公网暴露（2026-10-24）

## 背景与诉求

CPAMC（CPA 自带管理面板，`CLI Proxy API Management Center`）原先仅经本机 SSH 隧道
访问：`ssh -L 18317:127.0.0.1:8317`（专用密钥 `~/.ssh/cpa_tunnel_ed25519`），
即 bwg 上 `127.0.0.1:8317`（cli-proxy-api 容器直连端口，绕过 admission）。
诉求：办公室电脑不经家庭内网/隧道也能访问面板、修改配置。

## 终态

- 办公室入口：`https://fq.sciman.top:28317/management.html`（根路径代理到同一面板）。
- 双层门禁：nginx basic auth（第一层）+ CPA 管理密钥（第二层，与隧道路径共用同一把）。
- 生成 API 不在白名单：公网 `/v1` 仍唯一走 `cpa-gateway.conf:8443`（nginx → admission
  8318 → CPA），本端点不可用于绕过 admission。
- 本机 `18317` 隧道保持原样，继续可用；两条路径访问的是同一 CPA 实例。

## 变更清单（全部为新增，未触碰既有生产配置）

| 落点 | 内容 |
|---|---|
| `/etc/nginx/conf.d/cpamc-management.conf` | 新增独立 server 块，`listen 28317 ssl`（v4+v6）+ `http2 on`，证书复用 `/etc/v2ray-agent/tls/fq.sciman.top.crt`（renewtls 周更维持） |
| `/etc/nginx/cpamc.htpasswd` | `root:nginx 640`，单用户 apr1 哈希 |
| `/root/backups/nginx-conf-d-before-cpamc-20261024-155331.tgz` | 变更前 `conf.d` 全量 + `nginx.conf` 备份 |

basic auth 明文密码不落仓、不进证据；存放于本机
`%APPDATA%\vps-ssh-launcher\cpamc-basic-auth.txt`（与 `target.json` 同级的本地私有配置）。

## 设计要点

- **白名单 = 面板真实请求面**（权威证据：CPA 容器近 7 日 docker log 逐路径统计）：
  `/management.html`、`/v0|/v8/management/*`、`/v0/resource/plugins/`（含
  `workbuddy/panel` 子路径）、精确根 `/`。与上游 `homeHeartbeatMiddleware`
  的管理面豁免清单一致。其余一律 `return 404`。
- **不转发 `X-Forwarded-For`/`X-Real-IP`**：CPA 侧客户端 IP 恒为 docker 网关
  172.19.0.1，与隧道路径完全同构；`allow-remote` 语义零变化。
- 管理路径族 `limit_req zone=cpamc_rl`（5r/s burst=20 nodelay）抑制爆破速率；
  `client_max_body_size 32m` 对齐 8443 块（config.yaml/凭据上传）。
- `proxy_buffering off` + 300s 读写超时，兼容面板长调用。
- access log 用 `cpa_safe` 格式（conf.d 字母序保证 log_format 先于本文件解析），
  落 `/var/log/nginx/cpamc_management.access.log`。
- 端口 28317 呼应 18317/8317，非扫描器常见端口；防御主体是双层凭据而非端口隐蔽。

## 验证证据（2026-10-24）

1. **公网状态码矩阵**（本机发起，走真实公网链路）：
   `noauth /management.html=401`、`noauth /=404`（无 pre-auth 泄漏；曾用 302 重定向，
   因 rewrite 阶段先于 access 阶段会无认证跳转，已改为默认拒绝）；
   正确 basic auth：`/management.html=200`、`/=200`、
   `/v0|/v8/management/config=401`（到达 CPA 管理鉴权层=第二层门禁生效）、
   `/v1/models=404`（生成 API 拒绝）、错误密码=401；
   `8443 / =404` 与变更前一致（生产网关零扰动）。
2. **浏览器引导验收**（IAB 实开页面）：SPA 完整渲染
   “CLI Proxy API Management Center” 登录页，自动识别当前地址
   `https://fq.sciman.top:28317`，等待管理密钥输入；面板为单文件 bundle，
   无白名单外静态资源。
3. **guardrails doctor**：`DOCTOR_CONTRACT_OK`，其中 `nginx-syntax=OK` 已覆盖新
   server 块；`==model-substitution==` WARN 为既有挂账观察项（单已知对），与本变更无关。
4. **排障记录**：首版全 500 根因 = htpasswd `root:root 600` 而 worker 以 `nginx`
   用户运行（error log `open() ... Permission denied`）→ `chown root:nginx` + `640`
   修复。教训：v2ray-agent 版 nginx 的 worker 用户是 `nginx`，不是 Debian 默认的
   `www-data`。

## 凭据轮换与回滚

- 轮换 basic auth：本机 `openssl passwd -apr1 '<新密码>'`，远端把
  `/etc/nginx/cpamc.htpasswd` 整行替换为 `sciman:<新哈希>` 后
  `nginx -t && systemctl reload nginx`。
- 管理密钥沿用原值；如需轮换，经面板 config 页或 config.yaml `remote-management`
  段操作（与本暴露无耦合）。
- 回滚：`rm /etc/nginx/conf.d/cpamc-management.conf /etc/nginx/cpamc.htpasswd`
  → `nginx -t && systemctl reload nginx`；或整体恢复上方备份 tar。

## 已知限制

- **OAuth 登录回调未暴露**：`/anthropic|codex|antigravity/callback` 等不在白名单。
  管理面板内发起的 provider OAuth 重登录（device/回调流）如依赖公网回调地址，
  需照旧在家庭隧道路径完成；面板的只读用量/配额/插件管理页不受影响。
- 办公室网络如对 28317 等非标端口出站限制，则无法直连（无替代通道设计；
  如出现再议）。
