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

## 7. 当日续：登录闭环、受控首验与 opencode.ai 通道关闭（UTC+8 晚）

- 用户经管理 UI 保存插件配置（`login_region: intl`，config.yaml 被管理 API 重序列化为分组形态），
  并以 `GET /v8/management/oauth/auth-url?provider=workbuddy` 流程完成 OAuth 登录；
  凭据落盘 `workbuddy-intl-ca64800b-….json`（INTL realm）。
- **受控首验**：`/v1/models` 目录含 `deepseek-v4.1-flash`、无 opencode 残留；
  带 system 消息的 deepseek 单发 `200 / 31.2s / "我是 DeepSeek 最新版模型。"`（TTFT 窗口偏大，
  与 muse 同族"通道 TTFT 时间窗波动"先例一致，单样本勿过读）。
- **上游 11128 语义**：无 system 首消息的请求被上游按风控样式拒绝
  （`code 11128 "first message is not system prompt"`，30s stall 后 500），非账号问题；
  客户端必须保证对话以 system 开头（Claude Code 天然满足）。首败触发凭据冷却，
  紧跟的请求 503@0s 属冷却放大，重试须等 ≥60s。
- **INTL 目录实测 26 模型**（IDE+CLI 双身份并集）：deepseek-v4.1-flash + `-sg`、glm-5.2/5.3(-flash)、
  gpt-5.3-codex/5.4/5.5/5.6-luna/sol/terra、kimi-k2.6/k2.8-preview/k3、hy3/hy4-preview、o4-mini、
  gemini-3.5-flash、auto-chat/enhance-1.0/deep-model/fast-model 等；
  **`step-5-preview` 不在 API 目录**（用户在 workbuddy.ai 产品界面看到的名称，插件侧无此 id）。
- **opencode.ai 通道全关（用户指令，含 muse）**：`openai-compatibility` 组 `opencode-zen`
  条目置 `disabled: true`（schema 支持，日志实证 `disabled false -> true`）；
  `codex` 组 muse 条目（`muse-spark-1.3-contributor`，该组唯一条目）整体移除——
  **codex 组条目不支持 `disabled` 字段**（`unsupported group field disabled`，整次 reload 被拒），
  只能删除；`codex:` 置显式空组。热加载成功：`codex-api-key count: 1 -> 0`、
  OpenAI-compat 4 -> 3、客户端 8 -> 7，muse 通道自此失效（desktop muse 功能受影响，回滚可恢复）。
- 快照：`config.manual-snapshot-20261018T125749Z-pre-opencode-disable.yaml`（含误插行的中间态）、
  `config.manual-snapshot-20261018T130111Z-pre-muse-entry-removal.yaml`（矫正前）。
  排障教训：管理 UI 保存会重序列化 config（行号漂移、结构分组化），远端 config 编辑一律按内容匹配、
  改前快照、改后看 `config_reload.go` 日志确认 accepted/rejected。
- **权益钉定（用户纠偏后执行）**：`configs.workbuddy.models_intl: "deepseek-v4.1-flash"`
  （快照 `config.manual-snapshot-20261018T132243Z-pre-models-intl-pin.yaml`，热加载成功）。
  起因：插件动态发现广告上游全目录（26 幻影模型进 /v1/models），而账号权益仅 deepseek-v4.1-flash；
  钉定为替换式，/v1/models 34→13，workbuddy 只贡献 deepseek 一项。
  教训：`models_* 留空`仅适合权益=全目录的账号，权益受限账号必须钉定。
- **双账号调度加固（用户指令）**：`scheduler_mode: "credits"` + `stream_head_timeout: 60`
  （快照 `config.manual-snapshot-20261018T133614Z-pre-scheduler-credits.yaml`）。首次插入
  因 UI 已存 stream_head_timeout 撞重复键被 reload 拒绝（旧配置继续服务，无损），
  去重后 `config successfully reloaded`；/v1/models 复核 13 项、deepseek 在列。
  至此配置终态：intl + 权益钉定 + credits 调度 + 60s 头闸，其余默认。

