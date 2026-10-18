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
- 维护：固定 IP 可能因 EdgeOne 调整而失效，不能保证长期可用。出现连接故障时先核对
  DNS 与 HTTPS/TLS 可达性，再备份并定向更新映射；不关闭 TLS 校验或自动换 IP。

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

- **升级**：仓库保存可重放补丁 `scripts/patches/workbuddy-v0.8.68-intl-system-first.patch`，
  来源为 `mmqz/cpa-multi-plugins` 的 `v0.8.68`，MIT 许可声明随补丁保留。补丁改变两处
  运行时守卫，同时修正上游仍断言 INTL 不注入的旧测试，覆盖两种域名、两条函数路径和幂等性。
  上游修复 INTL 守卫后，确认等价行为，再使用官方构建并退役本地补丁。
- **回滚**：先备份现役产物，在同目录准备备份产物副本，核对 SHA-256、权限及所有者后
  用同文件系统 rename 原子替换 `workbuddy.so`，再在获授权的维护窗口重建/重启 CPA。
  不直接 `cp` 覆盖运行中映射的共享对象。回滚后 INTL user-first 客户端可能重新出现 11128。

### 4.1 重放与最低验证

在启用 CGO 且有 C 编译器的 Go 1.26 Linux 环境、一次性源码副本中执行，不改外置参考仓。
Windows 默认 `CGO_ENABLED=0` 会排除含 `storedAuth` 的 `main.go`，从而编译失败；
这不是业务测试失败，不应改插件类型定义来绕过。先下载
`https://codeload.github.com/mmqz/cpa-multi-plugins/tar.gz/refs/tags/v0.8.68`，
核对归档 SHA-256 为 §2.2 的值并解压。以下命令在解压后的源码根执行，
`<launcher-root>` 替换为本仓绝对路径：

```text
git apply --check <launcher-root>/scripts/patches/workbuddy-v0.8.68-intl-system-first.patch
git apply <launcher-root>/scripts/patches/workbuddy-v0.8.68-intl-system-first.patch
cd plugins/workbuddy
go test -run 'TestEnsureSystemFirst|TestPrepareUpstreamBodyOpensWithSystemMidHistoryDeveloper' -count=1 -timeout 45s .
```

构建沿用 §2.2 的 Linux/CGO 环境。新产物须记录自己的 SHA-256；工具链、路径和构建环境
不同可能改变二进制，不能要求它与本次生产产物逐字节相同。先做离线测试与 ABI/依赖核查，
再备份、原子换装，并在获授权的维护窗口确认插件加载；不要自动发起模型探测。

### 4.2 自动升级边界

2026-10-19 复核 `scripts/remote/cpa-auto-update.sh`：只替换 compose 的 CPA 镜像声明，
保留 `extra_hosts` 与 `./plugins:/CLIProxyAPI/plugins` 挂载，不自动升级插件文件。远端
现役插件哈希与 §2.2 相同，原版备份存在，补丁生效后日志已有两条 DeepSeek HTTP 200。
这证明当前产物与现有升级脚本的保留行为，不证明未来 CPA 版本与该原生插件始终兼容；
升级后必须确认插件注册及模型路由，不能只凭通用 readiness 成功判定插件加载成功。

### 4.3 补丁维护验证（2026-10-19）

- 固定版本归档 SHA-256 与 §2.2 一致；补丁 `git apply --check`、应用、
  `git apply --reverse --check` 均通过。
- BWG 已有 `golang:1.26-bookworm` 镜像在独立临时源码目录执行上述 7 项离线测试，
  `OFFLINE_PLUGIN_TEST_EXIT=0`；覆盖 Global、INTL、CN、空输入、已有 system 及
  中途 developer 消息。测试容器限制 1 CPU / 768 MiB，无生产挂载、无模型请求，
  结束后移除测试容器和临时源码（`OFFLINE_PLUGIN_TEMP_CLEANED=true`）。
- 本仓 `pytest tests/test_cpa_updater_apply.py -q`：19 passed，28 subtests passed。
- 本次仅保存维护补丁与说明，未换装生产插件、改 DNS 或重启服务；Desktop 原会话的
  流式 Responses 自然验收仍与上述离线测试和已有 Chat HTTP 200 分开。

## 5. 风控注记

- 本补丁只把 Global 已有的注入行为扩展到 INTL（对齐上游自身意图，见
  payload.go 原注释 "Inserting a harmless system message unifies both paths"），
  不含任何指纹伪造/风控规避。
- 0faaff85 曾独立出现 `11128 "request illegal"`（22:53，DNS 修复前到达上游被拒），
  与 #31 的 WAF 开篇词拦截不同、与 system-first 亦不同；该号已按面板停用静置，
  in-service 账号为 ca64800b（21:5x 200 实证）。11128-request-illegal 若在
  ca64800b 复现，按并行 runbook §4 处置表执行，不做规避工程。
