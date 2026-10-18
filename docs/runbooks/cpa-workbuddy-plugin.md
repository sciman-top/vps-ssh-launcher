# CPA WorkBuddy 插件 runbook（bwg）

**状态**: 插件已安装并加载（2026-10-18）；OAuth 登录/凭据导入待用户执行。
**适用主机**: bwg（CPA 容器 `cli-proxy-api`，官方镜像 `eceasy/cli-proxy-api:v8.0.32`，digest 锁定）。

## 1. 来源与固定版本（供应链锚点）

| 项 | 值 |
| --- | --- |
| 上游仓库 | `github.com/mmqz/cpa-multi-plugins`（MIT） |
| release tag | `v0.8.68`（2026-10-12 发布，CI 构建） |
| 插件版本 | workbuddy `0.9.58`（加载日志实证） |
| 产物 | `cpa-multi-plugins-linux-amd64.zip` |
| zip sha256 | `b54325f7c3426b0ca7083018e671af50f5e12b3311b42cefc2822cf573b910a5` |
| workbuddy.so sha256 | `5872d3da9d584cd25306ba3a329a8da1e898e333421b87271f84e33554a30edd` |
| 血统 | Sliverkiss/cpa-plugin（原初，已删库）→ mmqz 多源整理；协议层另参考 cockpit-tools / workbuddy2api-panel / OmniRoute 等 |

上游协议为逆向实现，模型 ID 由上游随时改名/退役（先例：hy3 族 2026-09-22 退役）；插件 v0.9.33 起已改为动态发现 + 配置钉定，勿在本地写死模型清单。

## 2. 已落装内容（2026-10-18）

1. `/opt/cliproxyapi/plugins/workbuddy.so`（0755，sha256 见上；宿主热加载，无需重启）。
2. `config.yaml` 新增块（插于顶层键之间，未动其它键）：
   ```yaml
   plugins:
     enabled: true
     configs:
       workbuddy:
         enabled: true
   ```
   注意：v8.0.32 对没有 `configs.<id>.enabled: true` 条目的 .so 会静默跳过（`defaultRuntimeItemConfig` 默认 false，无日志）——只写 `plugins.enabled` 不够。
3. 变更前备份：`/opt/cliproxyapi/config.manual-snapshot-20261018T120312Z-pre-workbuddy-plugin.yaml`。
4. compose 本就带 `./plugins:/CLIProxyAPI/plugins` 挂载，无需改 compose。

加载验证：`docker logs cli-proxy-api` 出现 `pluginhost: plugin loaded plugin_id=workbuddy` 与 `plugin registered ... version=0.9.58`；`grep -c workbuddy /proc/1/maps` ≥ 1。ABI 实证：v7.2.30 SDK 构建的插件可加载于 v8.0.32 宿主（`pluginabi.ABIVersion=1` 双侧一致）。

## 3. 用户登录/导入（待执行）

插件的 realm 语义（三选一，按凭据自动路由）：

| realm | 上游 | 接入方式 |
| --- | --- | --- |
| Global | `workbuddy.ai` | **面板凭据导入**（无直接 OAuth 登录口） |
| Intl | `codebuddy.ai` | 插件 OAuth 登录，`login_region: intl` |
| CN | `copilot.tencent.com` | 插件 OAuth 登录，默认 region |

入口：**登录动作在 CPA 上执行**——插件 AuthProvider 挂在宿主管理端点 `GET /v8/management/oauth/auth-url`（返回验证 URL）+ `GET /v8/management/oauth/status`（轮询；成功后宿主把凭据写入 auth 存储）。浏览器打开验证 URL 完成 Tencent 账号授权是唯一的用户侧动作。cockpit 的 CPA 插件页只是可选前端，调用的是同一批 CPA 管理端点，凭据不落在 cockpit；直接 curl 管理 API 等价。

**多账号**：一等支持。每账号一个独立凭据文件（`workbuddy-<uid>.json`，落在 CPA auth 目录）；插件路由 `GET .../accounts`（列表）、`POST .../select`（钉定活跃账号）、`POST .../refresh`、`GET .../credits`、`GET .../cooldowns`。路由模式 `scheduler_mode`：`off`（默认，交 CPA 内建跨凭据选择）/ `credits`（每次请求选剩余积分最高账号）。同 IP 多账号有关联性风控信号，建议账号数克制、保持人工节奏；`lifecycle_auto` 默认开会自动停用耗尽账号，客观上减少对耗尽账号的无效重击。

关键配置项（插件 `ConfigFields`，经管理 API 的 plugin config 修改）：

- `login_platform`: `CLI`（默认）/ `ide`。**要 DeepSeek 必须保持 CLI 身份**：上游 `/v3/config` 目录按客户端身份分流，IDE 身份 10 模型无 deepseek 系列，CLI 身份 22 模型含 `deepseek-v4.1-flash` / `-sg`（2026-09-22 上游实测）。
- `models_global` / `models_intl` / `models_cn`: **留空**（推荐）——按凭据动态发现，5 分钟缓存；仅在需要钉定时填逗号分隔上游 ID。
- `checkin_auto` / `tasks_auto` / `lifecycle_auto` / `token_keepalive`: 默认全开（CN 账号每日 09:00/21:00 签到；22:00 token 保活防 Keycloak 离线会话过期）。Global/Intl 账号以插件实际路由为准。
- `scheduler_mode`: 默认 `off`；多账号再考虑 `credits`。

## 4. 风控纪律（防封号/防限流/防降智）

- **封号先例**：社区有 CPA 插件反代 WorkBuddy 后账号被封的报告（`403 request illegal`，连官方 APP 一并 403）。上游 ToS 禁止第三方访问，本插件属逆向协议——按 antigravity lane 先例「挂账接受风险」，是否启用由用户决策；勿做工程化规避。
- 单账号起步、低频人工节奏使用；不要把批量/自动化高并发流量路由到 workbuddy 模型。
- **内容审核**：上游有 prompt 黑名单（cat-and-mouse）。首次实测顺序：中性小探针 → 带 `gpt`/`openai` 等指纹词的真实形态探针；命中即拒答属上游审核，勿按故障排障。Claude Code 固定 system 模板命中风险由调用方自担。
- 上游 429/限流透传勿重试风暴；观测走插件配额路由与管理 API `plugins/:id/quota`。
- 模型目录动态发现，缺模型先查 realm 与登录身份（CLI vs IDE），再查上游改名/退役，勿当回归。

## 5. 实测运维事实（2026-10-18 首验）

- **上游 11128：对话首条消息必须是 system**（`"first message is not system prompt"`，按风控样式拒绝、30s stall 后 500）。Claude Code 等天然带 system 的客户端满足；user-first 请求会失败且触发凭据冷却（随后请求 503@0s，等 ≥60s 再试）。
- INTL realm 实测目录 26 模型（`deepseek-v4.1-flash`/`-sg`、glm-5.2/5.3、gpt-5.3-codex~5.6-terra、kimi-k2.6~k3、hy3/hy4、o4-mini、gemini-3.5-flash 等）；**`step-5-preview` 不在 API 目录**（desktop 产品界面显示 Step-5-Preview "Free now"，但上游只对 app 身份暴露；插件 v0.9.35 只探测 IDE+CLI 双身份，截至 v0.8.68 无 step* id、无相关 issue——勿在 `models_intl` 猜 id 钉定，等插件跟进后按 §7 升级）。
- **配置最优基线**：`models_intl: "deepseek-v4.1-flash"`（**已钉定**——账号实际权益只有这一个；插件的动态发现广告的是上游全目录而非账号权益，曾致 26 个幻影模型进目录；钉定后 /v1/models 34→13，workbuddy 只贡献 deepseek 一项）；除 `login_region: intl` 外其余保持未设置（=插件默认：checkin/tasks/lifecycle/token_keepalive 均 true）；`scheduler_mode: off`（多账号再改 credits）；`stream_head_timeout: 60`（可选加固——实测 TTFT ~30s、11128 类失败有 30s stall，>0 把首 token 前失败转成干净 HTTP 错，静默慢流超时后照常放行）；`usage_report_*` 留空（无 CPAMP）。UI 保存只写改动过的字段，未动的 toggle 保持"未设置"勿显式关闭。权益变化时改 `models_intl` 逗号串即可（替换式，非追加）。
- deepseek-v4.1-flash 单发 200 实证；TTFT 窗口 ~30s 量级（单样本，同 muse"通道 TTFT 波动"先例，勿按回归排障）。
- opencode.ai 通道已全关（`opencode-zen` disabled + codex 组 muse 条目移除；codex 组条目**不支持 `disabled` 字段**，只能删除）。muse 通道失效属预期。

## 6. 回滚

```bash
# 远端（bwg）
rm -f /opt/cliproxyapi/plugins/workbuddy.so
cp -p /opt/cliproxyapi/config.manual-snapshot-20261018T120312Z-pre-workbuddy-plugin.yaml /opt/cliproxyapi/config.yaml
# config 热加载生效；若需彻底立即生效： docker restart cli-proxy-api
# 可选：清理插件凭据文件 /opt/cliproxyapi/auth/workbuddy-*.json（保留则仅失去路由）
```

Git 回滚不能代替远端恢复；`.so` 不入仓。

## 7. 升级

跟踪 mmqz releases → 下载新 `cpa-multi-plugins-linux-amd64.zip` → 核对 GitHub API asset digest → 抽取 `workbuddy.so` 覆盖 `/opt/cliproxyapi/plugins/workbuddy.so`（config 无需改动）→ 观察热加载日志。禁止未核哈希直接覆盖。
