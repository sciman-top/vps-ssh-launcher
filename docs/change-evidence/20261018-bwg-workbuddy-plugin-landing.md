# BWG CPA workbuddy 插件落装台账（2026-10-18）

**性质**: 真实远端写入（生产 CPA 容器新增动态插件）· 脱敏留档
**执行**: 单会话串行，只读探针先行，高风险步骤断点验证

## 1. 背景与决策

- 目标：在 bwg CPA（v8.0.32）以动态插件方式接入腾讯 WorkBuddy/CodeBuddy（国际版优先）的 DeepSeek 等模型。
- CPA 原生不支持（上游 issue #2506 not planned）；选定第三方插件 `mmqz/cpa-multi-plugins`（同类中最活跃：CI 发版、CN/Intl/Global 三 realm 合并、协议层多源标注、MIT）。对比与风险论证见当日调研记录（lovingfish 已停更、Sliverkiss 原库已删、linux.do 有封号案例）。
- 风险面：逆向协议 ToS 风险挂账接受（antigravity lane 同款用户决策模式）；插件为全信任原生代码进生产容器，以固定 tag + 双哈希核验 + 最小落装面（单 .so）约束。

## 2. 变更前基线（只读证据）

- 容器 `cli-proxy-api` = `eceasy/cli-proxy-api:v8.0.32@sha256:68f6e2d6…`（compose digest 锁定），x86_64，GLIBC 2.36。
- 插件产物 `.so` 的 GLIBC 符号上限 2.34 < 2.36；导出符号 `cliproxy_plugin_init` 与宿主 `loader_unix.go` 匹配；宿主 `pluginabi.ABIVersion=1`。
- compose 已预置 `./plugins:/CLIProxyAPI/plugins` 挂载；config.yaml 无 plugins 段。
- guardrails strict doctor：`DOCTOR_CONTRACT_OK`（含已知观察项：model_substitution WARN、chatgpt-oauth 拒绝计数）。

## 3. 变更序列（UTC+8 当日）

1. 产物核验：GitHub API asset digest ↔ 本地 sha256 ↔ 远端重下载 sha256 三方一致
   （zip `b54325f7…`、workbuddy.so `5872d3da…`）；release `v0.8.68`（2026-10-12）。
2. 远端落位：`/opt/cliproxyapi/plugins/workbuddy.so`（8,708,288 B，0755）；config 备份
   `config.manual-snapshot-20261018T120312Z-pre-workbuddy-plugin.yaml`（既有命名惯例）。
3. config.yaml 幂等插入（python3 脚本，插于最后顶层键块之前，改动仅 2 行）：
   `plugins: {enabled: true}`。
4. 受控 `docker restart` → 容器 10s 内恢复，零错误日志。
5. **首验加载失败**（无 pluginhost 日志、/proc/1/maps 计数 0）→ 对锚 v8.0.32 源码定位：
   无 `configs.<id>` 条目时 `defaultRuntimeItemConfig` 默认 `enabled: false`，静默跳过。
6. 补 `configs.workbuddy.enabled: true` → **config 热加载生效**（file watcher，未再重启）：
   `pluginhost: plugin loaded plugin_id=workbuddy path=plugins/workbuddy.so`、
   `plugin registered plugin_id=workbuddy plugin_name=workbuddy version=0.9.58`；
   maps 计数 5。现有 lane 流量（codex 会话重绑定后连续 200）未受影响。

## 4. 变更后验证与并行会话漂移归属

- 复跑 strict doctor：`DOCTOR_CONTRACT_FAILED`，失败项仅
  `cpa-admission.py` / `cpa-admission.sha256` 投影漂移 + `admission-integrity=FAIL`。
- 归因（哈希级证据）：漂移 `want=76eeb1c6…` = 本仓 HEAD（`3b3a85a`）的
  `scripts/remote/cpa-admission.py` 内容 sha256；`got=e2735c1d…` = 变更前提交
  （49bc8d3）同文件内容 sha256 = 远端现役文件。即**并行会话于当日 20:09:39（+08）
  提交并推送 admission 修复后尚未 `-Apply` 投影**，属该变更的待收敛状态，
  与本次插件落装无关（本次仅动 config plugins 块与 plugins/ 目录，均不在投影清单）。
- 处置：不代做 admission `-Apply`（归属并行会话的在途变更，避免碰撞）；插件侧无需投影。

## 5. 当前状态与剩余步骤

- 插件已加载、已注册，等待用户执行 OAuth 登录（Intl/CN）或面板凭据导入（Global）。
- 登录后首验顺序：中性小探针 → 真实形态探针（含上游敏感词），通过后才接真实客户端。
- 回滚入口：runbook `docs/runbooks/cpa-workbuddy-plugin.md` 第 5 节（删 .so + 恢复快照 + 热加载/重启）。

## 6. 证据锚点

- 产物哈希、config 快照文件名、加载日志行、maps 计数见上文；临时产物在会话 tmp 目录，不入仓。
- 上游锚点：CLIProxyAPI v8.0.32 tarball（GitHub 官方 tag）`internal/pluginhost/`、`cmd/server/main.go:685`。
