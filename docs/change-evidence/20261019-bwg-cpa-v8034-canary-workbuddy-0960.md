# BWG CPA v8.0.34 canary + workbuddy 0.9.60 换装台账（2026-10-19）

**性质**: 真实远端写入（CPA 镜像 canary 事务 ×2 + 手动回滚 ×1 + 插件原子换装 ×1）· 脱敏留档
**授权**: 用户当次指令"自主在 bwg VPS 上直接更新/升级 CPA 及其插件等，连续执行修复和优化"（2026-10-19）
**关联**: 20261019-workbuddy-intl-system-first-patch.md（intl 补丁与 DNS 钉定）、20261019-bwg-workbuddy-account-failover.md（现役 0.9.58p 构建与 failover 补丁）、runbook `cpa-workbuddy-plugin.md`

## 1. CPA v8.0.32→v8.0.33→v8.0.34 canary

- 事务脚本：`outputs/deploy-cpa-v8034-canary-20261019.sh`（复刻 v8.0.33 模板；digest 双锁定、
  admission 空闲断言、pre-generation 门、备份回滚、exit 0/1/10/20/75 契约）。远端副本
  `/opt/cliproxyapi/canary-v8034.sh`（sha256 `1b24bb17…`，与本地逐字节一致）。
- 12:40:56Z 首次执行 exit 0：v8.0.33@`561fe0fb` → v8.0.34@`439eb95f`
  （tag commit `e7d4c73`，created 2026-10-19T11:12Z，amd64），restarts=0。
  备份 `/opt/cliproxyapi/backups/20261019T124104…-from-v8.0.33-canary-v8.0.34`。
- 12:52:26Z 手动回滚到 v8.0.33（判别实验，见 §2）；12:54:06Z 同脚本再次执行 exit 0
  恢复 v8.0.34（镜像已本地，秒级）。两次备份目录均留档。
- v8.0.34 相关面：pluginhost 调度器快照+执行器注册期宿主锁释放、OpenAI translator
  逐流 pin payload/tool maps、Codex 主动刷新提前量 96h、antigravity credits 负 hint
  恢复、Claude web search 结果缓存、claude api-key cloak 处理。
- 验证（全部通过）：
  - cache-canary 双轮四样本：首样本 0（GLM 上游 10h TTL 过期，文档化良性），随后
    连续三样本 `hit_ratio=0.9956`——与历史基线逐位一致，缓存无损。
  - generation-all 4/5 通道 200/stop（gpt-6-astra 14.9s、glm-5.3-flash 6.0s、
    gpt-5.6-terra 2.7s、glm-5.3 3.7s，吐字正常）；`gpt-6.1-sol-input` 404
    `optional_route_unavailable` 为 ai.input.im 通道侧已知偶发（先例 ~8h 自愈），非回归。
  - OAuth lane 单发 ×2：claude-haiku-5-5 200/1.5s/stop、gemini-3.8-flash-high
    200/3.9s/stop。chatgpt-oauth 按纪律不另加探针（每日 timer 冒烟覆盖）。
  - 容器日志 v8.0.34 版本横幅 + 10 分钟零 error/panic；v8.0.34 启动期对 0faaff85
    的"过期模型注册丢弃"warn 为新 pluginhost 语义的预期行为。

## 2. workbuddy 11128 波：归因定案（非升级回归，上游侧开放事件）

时间线（UTC，journal + docker logs；旧容器日志随重建丢失，签名比对补证）：

- 00:03:56 / 00:26:38Z：deepseek-v4.1-flash 两次 200（intl 补丁在役，含 user-first 形状）。
- 12:29:45Z：外部客户端（request hash `f7df955f`，今日新形状）500
  （500/capacity=false/retry_after=absent/route=responses）——**升级前 12 分钟**；
  12:30:33Z 池冷却 503。与后述 11128×2 派发失败签名逐字段同型。
- 12:41Z v8.0.34 上线。12:43:29Z admission half-open 派发同一 hash 请求：
  ca64800b 与 0faaff85 先后 11128 `request illegal`（444/666ms），全池熔断。
- 判别实验（每步一发，共 3 发，随后按 runbook 停止派发）：
  - v8.0.34 已知好形状 user-first 单发 → 11128（480ms）；
  - 回滚 v8.0.33 同形状 → 11128（463ms）；
  - 插件换装 0.9.60 后同形状 → 11128（474ms，快转移 209/219ms/账号，failover 熔断正常）。
- 结论：11128 波与 CPA 版本、插件版本无关；上游（codebuddy.ai INTL）在 00:26–12:29Z
  之间新增了请求特征层风控。佐证：报错为 generic "request illegal"，不含 system-first
  违规特有的 "first message is not system prompt" 文案；上游 0.9.59（即本仓 PR #42
  收编，payload.go:483/671 与本地 intl 补丁语义一致）由贡献者以**全新凭据**验证
  user-first 11128→200，而本地两个存量凭据跨三版工件同败。
- 候选根因（未定案，不做规避工程）：出口 IP/EdgeOne 钉定节点级指纹升级；存量 token 的
  header-lineage 失配（0.9.58 起 `login_platform` 只种子新登录，旧 auth 文件
  "keep whatever they carry"，IDE-minted token 配 CLI headers 属已知 #26/#38 类）。
- 处置：按 runbook §4 停止探针确认、不刷 OAuth、不清冷却、不换 IP/设备；本地 5 分钟
  熔断自然循环，恢复依赖上游回退或**用户驱动的 INTL 重登**（凭据操作，不在代理授权内）。
- 看护点：经 doctor 日志扫描与自然流量观察恢复信号；DNS 钉定
  `www.codebuddy.ai:43.159.106.56` 仍在位，连接类故障先核 DNS/TLS 再定向更新。

## 3. workbuddy 插件 0.9.58p → 0.9.60-local 换装

- 源：上游 `v0.8.72` tarball（sha256 `810c5457684899ae51e4f513a0f30c16a5afbb501fbf6a5044b01347e35de9ed`，
  workbuddy VERSION=`0.9.60`；0.9.59→0.9.60 仅计费测试封闭性修复）。
- 补丁矩阵（`scripts/patches/`，均不改补丁文件本身）：
  - `intl-system-first`：**退役**——上游 PR #42 收编（payload.go 483/671 两处
    `isGlobalDomain||isIntlDomain` 与本地补丁逐点等价，符合其"上游发布等价处理即退役"条件）。
  - `selected-account`：对 v0.8.72 `git apply --check --recount` 干净通过，照常重放。
  - `account-failover`：须在 selected 之后应用（头注顺序约束；裸树 check 失败仅为缺垫，
    垫后干净）。11128→5 分钟凭据级熔断+池内转移行为随换装保留。
- 构建：隔离 `golang:1.26-bookworm`（--cpus=1 --memory=768m，无生产挂载，--rm 自动清理）；
  `go test -count=1` 全套 `ok … 8.564s`（含新 failover 测试与上游 INTL 测试）后
  `CGO_ENABLED=1 go build -buildmode=c-shared`。产物 sha256
  `98dac3101779de85646df58041b044076c41b8e1e3a0fcf572c7ec6405eba7e8`（12,012,776 B，
  与在役文件逐字节一致）。构建树留档 `/root/cpa-workbuddy-upgrade-20261019/`（源+补丁+产物）。
- 换装事务：`outputs/install-workbuddy-0960-20261019.sh`（远端
  `/opt/cliproxyapi/install-workbuddy-0960.sh`，sha `dae22ad1…` 与本地一致）。维护锁、
  空闲断言、**容器内 ldd 真实依赖检查（LDD_OK）**、备份、同 FS rename 原子替换（不 cp
  覆盖运行中映射）、`docker compose restart`、注册断言 version=0.9.60（不满足自动回滚）。
  13:06:17Z exit 0：`plugin registered plugin_id=workbuddy version=0.9.60`。
  备份 `/opt/cliproxyapi/backups/20261019T130612…-plugin-0958p-to-0960p`
  （旧 0.9.58p sha `1238117f…`）。CPA 镜像 pin 与全部凭据字节未动。
- 回滚入口：备份目录内 `workbuddy.so` + 事务脚本 rollback 路径同型（rename+restart）。

## 4. 门禁与终态

- 升级前 strict doctor（12:3x）：`DOCTOR_CONTRACT_OK`，无 P1/P2（substitution 7d=1 观察
  级、workbuddy 503 容量 ×1、TLS 83 天）。
- 终验 strict doctor（13:1x，全部写入之后）：`DOCTOR_CONTRACT_OK`；model-substitution
  7d=0；五 lane 全部 inflight=0/pending=0，仅 workbuddy failure_streak=1（预期，熔断窗）。
- 生产终态：CPA `v8.0.34@sha256:439eb95f…` + workbuddy 插件 `0.9.60-local@98dac310…`；
  compose `extra_hosts` DNS 钉定与 `./plugins` 挂载保留；updater timer 72h 成熟门不受
  影响（v8.0.34 已在役，收敛为 no-op）。

## 5. 遗留与移交

1. ~~workbuddy 11128 上游波（开放）~~ **已由 §6 修复收口**（同日会话头对齐补丁）。
2. `gpt-6.1-sol-input` 404：ai.input.im 通道侧，先例自愈；下一次 doctor 复核即可。
3. EdgeOne 钉定 IP 失效时：备份后定向更新 `extra_hosts`，不关闭 TLS 校验。
4. 远端临时脚本 `/opt/cliproxyapi/canary-v8034.sh`、`install-workbuddy-0960.sh` 留作
   事务记录（与 v8033 先例一致）。

## 6. 11128 波修复收口（同日晚：会话头对齐，PR #43 + Intl 扩展）

用户追问"是否无法修复，可参考官方文档/社区/源码"后重启取证，全程零新增派发（除最终
一发验证）：

- **displayMsg 全文**（容器内 `/root/.cli-proxy-api/logs/error-v1-chat-completions-*`
  转储，容器重建即失、取证须赶在重建前）：
  `{"code":11128,"msg":"request illegal","displayMsg":{"en":"Request blocked.
  Please send it again","zh":"请求被拦截，请重新发送"}}` —— 11128 家族第四个子条件
  （≠system-first 文案、≠#31 WAF 签名句、≠Global 的 channel verification 文案）。
- **上游同日 PR #43**（ki11a-Conton，未合并无 CI）：Global 网关同日开始要求桌面客户端
  三个会话关联头 `X-Conversation-ID / -Request-ID / -Message-ID`，缺失即 11128
  "request illegal"；作者报告 A/B（同 token/body，加头 11128→200 SSE）。该单发对照
  不证明网关只检查存在/形状，也不证明跨轮连续性无要求。PR 只补 Global；本仓 INTL
  同日失败及补丁后单发成功支持将补丁扩展至 Intl，但没有完成隔离其他变量的 INTL A/B。
  插件 task_chat 的既有头用法属于源码旁证，不构成公开官方协议契约。
- **修复**：`scripts/patches/workbuddy-v0.8.72-conversation-headers.patch`
  （sha256 `d4a914c6…080`，138 行）——PR #43 的 main.go hunk 扩展到
  `isGlobalDomain || isIntlDomain`，blank-only 填充（caller 值优先、`growthClientToken()`
  每请求 UUID、CN 字节不变）+ 两个测试文件（PR 原测试 + INTL 扩展测试）。
  在 selected-account 之后的树上 `git apply --recount` 干净应用。
- **构建与换装**：全量 `go test` ok 8.488s（含新测试）→ CGO 产物
  sha256 `3bca8a39d0e88194342c078d3ad0066727671bae11943f39ca0ebd8500a72218`。
  首次换装被事务空闲断言 **fail-closed 拒绝**（并行会话 chatgpt-oauth inflight=2，
  属正确拒绝，获用户确认后由空闲等待器自动完成）；13:39:54Z exit 0，
  备份 `20261019T133954…`（前一版 98dac310 可回滚），注册 `version=0.9.60`。
- **验证**：user-first 最小单发 **200 / 1.4s / finish=stop**（该形状此前跨
  v8.0.33/v8.0.34 × 0.9.58p/0.9.60 全败）；终验 strict doctor `DOCTOR_CONTRACT_OK`。
- **遗留**：PR #43 尚未合并；上游合并且含 Intl 后本地补丁退役。向 PR #43 回贴 Intl
  实证属外部发布动作，待用户示意。
- **方法论沉淀**：11128 是多子条件家族，归因必须取 displayMsg 全文而非只看 code；
  错误转储随容器重建丢失，取证窗口在重建前。
- **审查边界**：现有测试证明 helper 填空、保留预填 Conversation ID 及 CN 不新增头；
  尚未覆盖真实 executor/task_chat 的 caller ID 传递、同对话多轮 ID 连续性、请求重放
  或上游缓存语义。保留当前生成策略，不能仅凭单发成功断言这些场景已验收。

### 后续只读复核（2026-10-19 13:51Z）

- 严格 doctor 退出码 0，`DOCTOR_CONTRACT_OK`；容器 running，启动于 13:39:55Z，
  restart=0，镜像仍为 v8.0.34 的 `439eb95f…` digest。
- 远端插件 SHA-256 读回与 §6 的 `3bca8a39…72218` 完全一致。
- 从 13:39:54Z 起的 admission journal 中，DeepSeek 共三条 `upstream_result`：
  13:40:34Z、13:44:36Z、13:47:41Z，均为 `lane=workbuddy-oauth`、
  `model=deepseek-v4.1-flash`、`status=200`、`capacity=false`、`successful=true`。
  本轮复核没有发起模型生成请求。上述摘要不能将某条记录与用户截图唯一关联，
  也不能证明回复内容、工具执行或跨轮关联正确。
- doctor 另有一条 Gemini 模型替代观察告警，与本次 DeepSeek 路由独立；未据此变更其他通道。
