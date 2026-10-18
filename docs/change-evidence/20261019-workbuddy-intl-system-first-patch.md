# WorkBuddy INTL system-first 本地补丁 + DNS 钉定台账（2026-10-19 00:1x）

**性质**: 真实远端写入（生产插件二进制换装为本地补丁构建 + compose DNS 钉定）· 脱敏留档
**关联**: 20261018-bwg-workbuddy-plugin-landing.md（落地与隔离史）、20261018-bwg-workbuddy-risk-quarantine.md（并行会话隔离）、risk-controls（并行会话在途）

## 1. 根因（两叠加故障，先后）

1. **DNS 间歇性污染**：`www.codebuddy.ai` 从 bwg 间歇解析 `0.0.0.1`（EdgeOne CNAME
   `eo.dnse2.com`；控制域名 api.github.com 无恙；23:1x 与复查时均正常）。命中窗口内
   请求 `dial tcp4 0.0.0.1:443: i/o timeout`，根本不到上游。
2. **插件 INTL realm 守卫缺口**：`ensureSystemMessageInPlace` / `ensureSystemMessage`
   的注入守卫为 `!isGlobalDomain(...) → skip`——只有 Global（workbuddy.ai）做
   system-first 自动注入；INTL（codebuddy.ai）上游实测同样强校验首条 system
   （11128 "first message is not system prompt"，426ms 快拒），user-first 客户端
   必失败。上游 main 分支同为旧守卫（未修），issue #40（同症状）开放中。

## 2. 修复

### 2.1 DNS 钉定（compose extra_hosts）

- `/opt/cliproxyapi/compose.yml` 服务段新增
  `extra_hosts: ["www.codebuddy.ai:43.159.106.56"]`（EdgeOne 真实 IP，
  `--resolve` 直连 302/0.2s 预验证可用）+ `docker compose up -d` 重建。
- 备份：`compose.manual-backup-20261018T155300Z-pre-dns-pin.yml`。
- 生效实证：请求 204ms 即达上游（原 34s DNS 停滞消失）；容器 /etc/hosts 含钉定行。
- 维护：EdgeOne 换 IP 时重解析更新钉定即可（可逆；症状不会劣于现状）。

### 2.2 插件本地补丁构建（INTL 纳入 system-first 注入）

- 源：v0.8.68 tag tarball（`6163f688bf902b7e366f4d4ca11ff674b1f042fd34d39e3289136fa87972e552`）。
- 补丁：`plugins/workbuddy/payload.go` 两处守卫
  `!isGlobalDomain(...)` → `!(isGlobalDomain(...) || isIntlDomain(...))`
  （`isIntlDomain` 为插件自带函数，billing.go）。CN 维持作者验证的豁免。
- 构建：bwg 上 `golang:1.26-bookworm` 容器 `CGO_ENABLED=1 go build -buildmode=c-shared`。
- 产物 sha256：`815a8f23b73ee037287ca7e3c290e9815bce9e8aec0d26358bae68cfa253ed23`
  （12,007,752 B），已装 `/opt/cliproxyapi/plugins/workbuddy.so`（0755）。
- 上游原版备份：`plugins/workbuddy.so.upstream-0.9.58.bak`
  （sha256 `5872d3da9d584cd25306ba3a329a8da1e898e333421b87271f84e33554a30edd`）。
- 注册实证：restart 后 `plugin registered ... version=0.9.58`。

## 3. 验证

- **user-first 单发**（修复前必失败形状，无 system 消息）：
  `200 / 1.3s / finish=stop / model=deepseek-v4.1-flash / 真实回复` —— 两种历史
  失败模式（DNS 滞留、system-first 11128）均已消除。
- doctor 严格：`DOCTOR_CONTRACT_OK`（含 compose 校验）。

## 4. 升级与回滚

- **升级**：上游发新版后，重放同一补丁再构建（补丁仅 2 处守卫，见 §2.2）；
  哈希比对后替换。上游若修复 INTL 守卫，直接用官方构建、丢弃本地补丁。
- **回滚**：`cp plugins/workbuddy.so.upstream-0.9.58.bak plugins/workbuddy.so`
  + 重启。回滚后 INTL user-first 客户端会重新出现 11128（已知限界）。

## 5. 风控注记

- 本补丁只把 Global 已有的注入行为扩展到 INTL（对齐上游自身意图，见
  payload.go 原注释 "Inserting a harmless system message unifies both paths"），
  不含任何指纹伪造/风控规避。
- 0faaff85 曾独立出现 `11128 "request illegal"`（22:53，DNS 修复前到达上游被拒），
  与 #31 的 WAF 开篇词拦截不同、与 system-first 亦不同；该号已按面板停用静置，
  in-service 账号为 ca64800b（21:5x 200 实证）。11128-request-illegal 若在
  ca64800b 复现，按并行 runbook §4 处置表执行，不做规避工程。
