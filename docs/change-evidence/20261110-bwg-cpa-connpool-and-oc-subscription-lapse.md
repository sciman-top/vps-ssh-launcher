# 2026-11-10 bwg CPA：API key 车道 connection-pool 启用 + oc Go 订阅失效定案

**性质**：真实远端写入（config.yaml 事务）+ 上游侧计费事件定案 · 脱敏留档
**用户授权**："如有必要，就修复/优化本项目代码" 及追问 "不用修复和优化？"

## 1. connection-pool 启用（优化事务）

### 1.1 依据

- 生产 fork `cli-proxy-api-fork:v8.0.61-gemini-raw` == 上游 `v8.0.61` tag == main HEAD
  `681ce41b`，包含 `34a01b76`/`bf834311`（per-api-key transport pool，`connection-pool`
  块为 opt-in 开关）。
- 上游提交自述：历史行为为 **one-transport-per-request**（API key 车道每次请求全新拨号）。
- 实测握手（bwg → 上游，3 样本）：opencode.ai tcp≈3ms / tls≈35-37ms；
  input.codes tcp≈3-11ms / tls≈41-64ms。即每次请求约 40-65ms 可复用收益
  （oc 车道 header p50 2.3s 的 ~1.7%，低于单请求感知阈值；收益主要为连接 churn
  消除与协议常态面）。

### 1.2 改动

`/opt/cliproxyapi/config.yaml` 的 `api-keys.openai-compatibility` 四个组
（`ai.input.im` / `opencode` / `http-bridge-8003` / `zhipu-plan`）各插入
`connection-pool: {}`（组级 opt-in，默认 idle-conn-timeout 300s /
max-idle-conns-per-host 2）。纯文本行插入，无注释/格式重排。

- config SHA：`269579af7b65…` → `be822b0b5eb8…`
- 语义校验：pyyaml 前后解析（剔除 connection-pool 键后）全等，插入计数断言 =4。
- OAuth 车道不适用池化（上游设计）；antigravity 自有 `connection-pool` 开关未启用
  （TTFB 6.8s 为 thinking 结构性，握手占比 ~0.6%，按 R5 不做）。

### 1.3 事务与回执

工具：`outputs/_connpool_txn_20261110.py`（base64 投影远端执行，
备份 → 行插入 → YAML 语义断言 → 重启 → readiness 门禁 → 失败自动回滚）。

```text
EDIT_OK groups=['ai.input.im', 'opencode', 'http-bridge-8003', 'zhipu-plan']
SHA pre=269579af7b65 post=be822b0b5eb8
BACKUP_OK /opt/cliproxyapi/backups/20261109T163157-connpool-20261110
READINESS_OK HEALTH_OK
RESTART_OK
TXN_OK
```

附带清理：三个陈旧投影暂存目录
`/opt/cliproxyapi/.projection.{iarpzb,6EwAs4,L9qAOb}`（各 3 文件/236K，2026-11-08
07:02-07:06 deploy-admission-backlog 事务残留）已删除。

### 1.4 验收

- strict doctor：`DOCTOR_CONTRACT_OK`（updater/health/policy/admission/compose/
  nginx-syntax 全 OK；fail2ban 行为探针在役）。
- 容器重启后存活即证 fork v8.0.61 正确解析新 knob（strict YAML 下未知字段会拒启）。
- 非相关车道自然流量：WB CN 3×200；admission journal 自重启 0 错误行。
- doctor 附带观测：6008×3（WB INTL credits，既有已知额度状态）；
  gemini flash-high→flash-n 替换分布于 2 个凭据（池级行为，非单账号降智）。

## 2. oc 车道 Go 订阅失效（上游侧事件，与本事务无关）

### 2.1 时间线

- `00:25:32`（+8）oc 车道最后一笔成功自然流量；
- `00:31:57` 本事务重启容器；
- `00:32:41` 重启后首笔 oc 请求（cpa-health generation 探针）→ 上游
  `403 permission_error: "An active OpenCode Go subscription is required to use
  Go models."`；随后全链单发同因 503 `auth_unavailable`。

### 2.2 定案实验（洗清事务嫌疑）

**绕过 CPA 直连** `https://opencode.ai/zen/go/v1/chat/completions`，同一把
key、同 session 头 → 完全相同的 403 JSON。计费/订阅判定为 key 账号级，
与传输层（连接池/TLS 指纹）无关；纯时间巧合。

### 2.3 影响与恢复

- oc 是 24h 最大车道（2056 完成零失败，last 00:25）；失效后 `-oc` 别名消费方
  将得到 403/503。CPA 已将该凭据判 unavailable（in-memory）。
- 恢复路径：用户续订 OpenCode Go 后 `docker restart cli-proxy-api`（清除
  in-memory 凭据禁用态）；或用户裁决把 `-oc` 消费方改指 WB CN
  `deepseek-v4.1-flash`（异提供方，需用户确认）。
- 24h 零失败并非"永不失败"：pick 连续性掩盖订阅状态，本例为教训——订阅类
  额度状态没有被动探针。

### 2.4 回滚

- config：`cp -a /opt/cliproxyapi/backups/20261109T163157-connpool-20261110/config.yaml
  /opt/cliproxyapi/config.yaml && docker restart cli-proxy-api`；
  或删除四组 `connection-pool: {}` 行后重启。Git 回滚不能替代远端恢复。

## 3. 边界

- 本次不改变 request-retry / max-retry-credentials / 冷却 / 会话亲和语义；
  未增删凭据、模型或路由；`cpa_provider_routes.json` 只含 slot/模型拓扑，
  无传输 knob，无 drift。
- ~~连接池行为由上游代码+配置解析保证，未做逐请求复用断言~~ → 已由 §7 三臂
  受控演练补齐（2026-11-10 用户质询后）。
- 订阅恢复、消费方改道为用户业务决定，agent 不代办。

## 7. 受控实战验收补强（2026-11-10 用户质询后补齐）

### 7.1 config 文本级 diff 回执

`diff 备份 vs live` = **恰好 4 行新增 `    connection-pool: {}`**，零其他改动
（与事务断言一致）。

### 7.2 三臂 A/B/C 演练（同一生产镜像，隔离实例）

实例构造：`docker run --network host`，server.host 钉 `127.0.0.1`、端口 18317，
OAuth 目录挂载为空目录（零 OAuth 凭据、零上游刷新），`plugins.enabled: false`，
usage-statistics 关闭；假上游=本机 `127.0.0.1:19999` HTTP/1.1 keep-alive
计数服务器，逐请求记录 peer 连接；凭据状态互不复用生产。逐臂后容器删除。

| 臂 | config | 请求结果 | 上游侧观测 |
|---|---|---|---|
| A | `connection-pool: {}`（同生产） | 200×3 | **1 连接 / 3 请求**（默认参数复用） |
| B | 四组 pool 行移除（对照） | 200×3 | **1 连接 / 3 请求**（默认 transport 本就复用） |
| C | `idle-conn-timeout: "1s"`，请求间空闲 2.5s | 200×3 | **2 连接 / 3 请求**（第三请求空闲超时重拨） |

C 臂证明 **knob 被解析并真实作用于传输层**（配置 → resolver → transport 行为）。
演练后生产容器零影响（Up 连续、`healthz ok`），实例与 `/tmp` 演练目录全部拆除。

### 7.3 结论修正（诚实口径）

- §1.1 的"每次请求都在付 TCP+TLS 握手"**被 B 对照臂推翻**：one-transport-
  per-request 是上游 proxy-aware 客户端的历史行为；生产无 `proxy-url`，直连
  路径经 Go 默认共享 transport **本来就在复用连接**。本次改动的**延迟收益
  ≈ 0**（该收益此前已被默认行为兑现），40-65ms 实测值是"若无复用"的假设量。
- 改动的实际价值：per-key 连接隔离（两 key 永不共享连接）、idle socket
  上界收紧（每 transport 2 而非克隆默认 100）、显式 idle-conn-timeout 控制，
  以及**未来任何渠道配置 `proxy-url` 时该 knob 立即成为 load-bearing**
  （彼时历史行为=每请求重拨）。保留理由 = 零成本 + 上述性质 + C 臂证明的
  真实生效，而非延迟。

## 8. 附带报备

- 2026-11-10 会话中一次未脱敏 sed 将 `access.api-keys[0]` 值与 management
  bcrypt 哈希带入本地会话记录（未入仓未推送）。按仓库先例（refresh_token /
  LE 私钥同类）记为择机轮换项，建议与 oc 订阅恢复的重启窗口合并执行双阶段
  access key 轮换。
