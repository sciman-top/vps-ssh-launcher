# BWG CPA v8.0.37 canary + workbuddy 0.9.62 换装台账（2026-10-21）

**性质**: 真实远端写入（CPA 镜像 canary 事务 ×1 + 插件原子换装 ×1）· 脱敏留档
**授权**: 用户当次指令"自主在 bwg VPS 上直接更新/升级 CPA 及其插件等，注意防范多个 OAuth 账号的封号/限流/降智风险，吐字缓慢，优化缓存命中率……连续执行修复和优化"（2026-10-21，与 10-19/10-20 同指令口径）
**关联**: 20261020-bwg-cpa-v8035-canary.md（v8.0.35 基线）、20261021-bwg-workbuddy-14003-wrapper-fix.md（admission 分类器在役态）、20261019-bwg-cpa-v8034-canary-workbuddy-0960.md（插件构建程序模板）

## 1. 前态与升级面取证

- 生产 CPA v8.0.35@`114ea95c` + workbuddy 插件 0.9.60-local@`3bca8a39`；升级前 strict
  doctor `DOCTOR_CONTRACT_OK`（13:0x，三 lane OPEN、claude 墓碑 inert、substitution
  7d=1 单已知对、TLS 81 天）；generation-all 4/5 通道 200/stop（sol-input 404 为
  ai.input.im 通道侧已知遗留，先例自愈）；cache-canary 首样本 0（GLM 10h TTL 过期，
  文档化良性）+ 第二样本 `hit_ratio=0.9956` 与历史基线逐位一致。
- v8.0.36（10-20）+ v8.0.37（10-21 官方 release notes + compare）风险面评估：无 OAuth
  凭据路径改动。升级相关面：TTFT 流式测量（#6794，内部指标）；antigravity Hub 请求
  对齐 + 版本化模型缓存失效（#6798，antigravity lane 翻译面，需探针）；executor 下游
  User-Agent 透传**仅 xAI 与 openai-compat 执行器**（5026f36 逐点核对；codex/
  antigravity 执行器未动，workbuddy 插件自管 backendHeaders 不受影响；zhipu lane 走
  openai-compat → 客户端真实 UA 透传，GLM 官方支持第三方工具，指纹面无恶化）；Claude
  cache_control 空块跳过（claude lane 退役 → inert）；SIWC 绑定/状态端点（未使用）；
  watcher auth 文件错误保凭据加固；v8.0.37 三个 per-stream 缓存性能 PR
  （#6828/#6829/#6831，降低每流 CPU 开销）。源码核对口径：外置仓对象库上限 v8.0.31，
  按纪律未 fetch，以官方 release notes/compare 为替代证据；生产版本以本台账与容器
  横幅为准。
- 插件上游 `mmqz/cpa-multi-plugins` 当日 v0.8.73→v0.8.76 四连发：#43（Global 会话头）
  合并（06cf92b）+ e324b43（0.9.61）扩为 **realm 无条件**填充 + 8024bd5（DeepSeek
  Harness intro 重写修 CN 11-128 + **11128 改 statusError{400} request-scoped**，
  终结"裸 11128 致 v8 宿主轮遍冷却全部账号"面）+ 07e9eff（0.9.62 bump+测试）。本仓
  10-19 的 conversation-headers 补丁符合其"上游合并且含 Intl 即退役"条件（本仓
  issue #44 / PR #43 评论证据链被上游采纳）。

## 2. CPA canary 事务（v8.0.35 → v8.0.37）

- 脚本 `outputs/deploy-cpa-v8037-canary-20261021.sh`（v8035 模板复刻，digest 双锁
  `114ea95c…`→`6e36e6ae…`（Docker Hub tag API 取法经 v8.0.35 在役 pin 复现验证），
  空闲断言、pre-generation 门、备份回滚、exit 0/1/10/20/75 契约不变）。
- 12:59:11Z `ADMISSION_IDLE_OK` + 非 OAuth generation `HEALTH_OK` → 12:59:22Z
  `RECREATED` → 12:59:35Z **exit 0**：v8.0.35 → v8.0.37（tag commit `ce07d18`，
  BuiltAt 2026-10-21T07:55:18Z），restarts=0。备份
  `/opt/cliproxyapi/backups/20261021T125914.251074365Z-from-v8.0.35-canary-v8.0.37`。
- 容器横幅 `v8.0.37, Commit: ce07d18`；workbuddy 0.9.60 插件在新宿主正常注册；
  启动后 error/panic 扫描零新增（仅 sol-input 404 与一发 gpt-6.1-sol 502
  `server_is_overloaded`——chatgpt 容量窗家族，10-20 已文档化，lane 无 hold/冷却残留）。

## 3. CPA 验收

- generation-all（升级前后同口径）：4/5 通道 200/stop；升级前 3.2/2.0/3.5/2.1s、
  升级后 3.9/3.1/4.4/2.7s（astra/glm-flash/terra/glm-5.3），同带波动；sol-input 404
  两轮均在（通道侧已知，非回归）。
- cache-canary：升级后双样本 `hit_ratio=0.9956`（4114/4096），与基线逐位一致，
  缓存无损。
- OAuth lane 回环单发：gemini-3.8-flash-high **200 / 2.16s / stop**（v8.0.36
  antigravity Hub 对齐实战验证；上游回显 `gemini-3.8-flash-n` 为已知观察级命名差异，
  按既定口径继续观测勿改名消警）；复验第二发 200 / 4.5s / stop。chatgpt-oauth 按
  纪律不加探针（每日 timer 冒烟覆盖；容量窗家族见 §2）。
- 吐字/流式：GLM 流式探针 TTFB **0.96s** / total 1.70s / 49 个 SSE 事件——v8.0.37
  下客户端侧首字延迟健康，"吐字缓慢"未复现；v8.0.36 的 TTFT 测量为内部指标（info
  日志无 ttft 行），客户端 TTFB 即本次观测口径；v8.0.37 per-stream 缓存 PR 降低宿主
  CPU 逐流开销为结构性优化，无需配置改动。
- User-Agent 透传影响评估：见 §1；zhipu lane 透传后如需钉定可用 `header:User-Agent`
  配置覆盖（当前无需，观察项）。

## 4. workbuddy 插件 0.9.60-local → 0.9.62-local

- 源：上游 `v0.8.76` tarball（SHA-256
  `45e177377c3dc76020b244b8e97f59efc18368c99d71f57fbe6d8400ceef1409`，VERSION=0.9.62）。
- 补丁矩阵：selected-account、account-failover 两补丁 `git apply --check --recount`
  对 v0.8.76 **干净应用**（远端 sha 与本地逐字节一致 `dd8bbddb…`/`c322d2c2…`）；
  conversation-headers 补丁退役（上游收编，见 §1）。
- 构建：隔离 `golang:1.26-bookworm`（--cpus=1 --memory=768m，无生产挂载，--rm）；
  `go test -count=1` 全套 `ok … 8.484s`（含我方两补丁测试 + 上游 0.9.62 新测试）后
  `CGO_ENABLED=1 go build -buildmode=c-shared`。产物 sha256
  `94a1c82550fc5ef2e3ee6098847f7efdb1e9292b93152906f0bc40c716194929`（12,017,376 B，
  换装后远端读回一致）。构建树留档 `/root/cpa-workbuddy-upgrade-20261021/`。
- 换装事务：`outputs/install-workbuddy-0962-20261021.sh`（0960 模板复刻：维护锁、
  空闲断言、容器内 ldd 依赖检查、备份、同 FS rename 原子替换、restart、注册断言
  version=0.9.62 不满足自动回滚）。13:15:47Z `ADMISSION_IDLE_OK` + `LDD_OK` →
  13:15:52Z **exit 0**：注册 `version=0.9.62`，readiness `HEALTH_OK`。备份
  `/opt/cliproxyapi/backups/20261021T131547.737582035Z-plugin-0960-to-0962`（旧
  0.9.60 `3bca8a39` 可回滚）。CPA v8.0.37 镜像 pin 与全部凭据字节未动。
- 语义变化入册（runbook §1/§2/§4 已同步）：会话头 realm 无条件填充；11128 改
  request-scoped（上游插件层不再轮换/冷却账号）；我方 account-failover 补丁的
  11128→5 分钟凭据级熔断分支保留作本地保险丝（只短冷却、不轮换），两层兼容。

## 5. workbuddy 6004 窗口与 live 验收边界

- 13:12Z workbuddy 单发：**500 + 6004 wrapper**（上游模型级频率限制经 server_error
  信封透传）——10-21 深夜已定案的已知形态，admission 正确归 lane 阶梯自恢复
  （`hold=None`，非持锁），分类器行为在 v8.0.37 上实战成立；按纪律未重试。
- 13:17Z 半开窗口单发经 ssh_tool 60s idle 超时掐断：上游回包（6004）在客户端离开后
  由 admission 计失败（streak 5→6）并重挂 900s 顶档；13:20Z 单发被秒拒 429
  （1.8ms，未派发，`official_account_admission_circuit_open`）；healthz 复核
  `remaining=766` 确认顶档。此后按纪律停止探针。
- **结论**：上游 deepseek-v4.1-flash 6004 模型级窗当日未关（分钟级~小时级自恢复族），
  workbuddy 数据面 live-200 验收顺延至自然流量/阶梯自身半开探针；注册、依赖、路由、
  分类器行为均已实证。6004 窗本身与本次两笔变更无关（升级前即有同族 IDE 报错）。
- **台账定稿后两次受控补验（验收对账问询触发，均等窗口到期后单发）**：13:50:32Z
  （journal 锚定，upstream_result 落账，streak 6→7 重挂 900s 顶档）与 ~14:06Z（半开
  补发，回执 500/0.23s 同型 6004 wrapper）均复现同形态——上游模型级拥塞持续逾
  1h。两次补验进一步实证传输栈（v8.0.37→admission→0.9.62→上游）全链送达+分类
  正确；**此后永久停止人工探针**，live-200 由阶梯半开探针/自然流量自然闭合，无
  待办动作。

## 6. 终验与 doctor 单次红事件

- 换装后 strict doctor：13:33Z 一次 `DOCTOR_CONTRACT_FAILED`（输出未捕获，失败节
  未定位）；13:36Z 与 13:4xZ 两轮复跑均 **exit 0 + `DOCTOR_CONTRACT_OK`**，projection
  drift 13×MATCH、lane-hold OK（三 OPEN + 墓碑 inert）、substitution 7d=1、TLS 81 天、
  journal OK。单次红与 workbuddy 阶梯 early-probe 重挂同窗，假说=doctor 读
  `*-risk.json` 恰逢 admission 原子重写（"状态损坏"分支按设计 strict FAIL）；
  **未证实**，看护项：再现即抓全量输出定位。
- 生产终态：CPA `v8.0.37@sha256:6e36e6ae…` + workbuddy 插件
  `0.9.62-local@94a1c825…`；admission 组件（pin `19d64361`）未动。

## 7. 遗留与移交

1. 回滚入口：镜像=备份目录内 compose.yml + 本地 v8.0.35 镜像（`114ea95c` 在库），
   事务 rollback 路径同型；插件=备份目录内 0.9.60 `.so` + rename+restart 同型。
2. 看护点：workbuddy 6004 窗自愈与 live 单发补验（再现 14003→hold 即回归）；
   sol-input 404 自愈；gemini 命名差异继续观测；doctor 单次红假说（再现抓全量）；
   zhipu lane UA 透传后的上游画像观察（如需钉定走 `header:User-Agent`）。
3. updater timer 72h 成熟门在 v8.0.37 在役后收敛为 no-op。
4. `canary-v8037.log`/`canary-plugin-0962.log` 与四个 outputs 事务脚本留作事务记录
   （与 v8033/v8034/v8035 先例一致）。
