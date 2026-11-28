# BWG admission 部署：zcode-start-plan 挂起持久锁 + provider risk holds 加固（2026-11-28）

**性质**：真实远端写入 + release 级证据留档（redaction-first）。时间均为 UTC。
**授权链**：用户指令（2026-11-28，CPA v8.1.8 换代任务）+ 用户追问"需要投影，模拟/受控实战验收？"（2026-11-28，明确要求补齐投影与模拟/受控实战）。
**代码来源**：并行会话提交 `3a17341`（"fix(cpa): harden provider risk holds and zcode suspension handling"，10 文件 +371/−43，CI 绿）——本台账只覆盖其**部署与验收**；代码评审属原会话。

## 终态

| 项 | 前 | 后 |
|---|---|---|
| admission 完整性 pin | `2bc34f73`（11-27 12:02Z 上线） | **`08633273`**（DEPLOY_OK 2026-11-28T01:1xZ） |
| `/usr/local/libexec/cpa_admission_bootstrap.py` | `24254069` | **`177c59f8`**（新增 `--zcode-start-plan-state-file` 旗标） |
| request guard | SOURCE_SHA256=2bc34f73 | 重渲染为 08633273 |
| zcode 风险状态文件 | 不存在 | `/var/lib/cpa-admission/zcode-start-plan-risk.json`（nobody:nogroup 600，`{"version":1,"not_before":0,"hold_reason":null}`，事务内初始化） |
| workbuddy 版本期望 | 0.21.32 | **0.21.33**（`cpa-guardrail-workbuddy-version-check.sh` + 测试夹具随本切片提交） |
| 其余 17 项投影 | — | `GUARDRAILS_APPLIED` 17×`PROJECTION_HASH_VERIFIED`、`HEALTH_OK`、`READY_STATUS=200` |

备份：`/opt/cliproxyapi/backups/20261128T010804.603467591Z-admission-zcode-suspension-20261128`
（含 admission/config/guard/bootstrap/pin 与 zcode 状态缺省标记；首次尝试
`20261128T010231…` 同名备份为回滚演练产物，已验证恢复）。

## 部署事务要点（`outputs/cpa-v818-build/deploy-admission-zcode-suspension-20261128.ps1`）

- 模板：`deploy-admission-codex-refusal-retry-after-20261127.ps1`。差异：bootstrap
  同事务交换；config/routes 已在主机为新值（1be9cf4 已投影），仅作基线断言。
- **主机级模拟**（dry-run 与 apply 各跑一遍，staged 模块以 nobody 编译+加载）：
  `STAGED_RULE_OK zcode_durable_hold empty_405_neutral array_unwrap wb_regression`
  ——3012 结构化体→account_restricted、空 405 不判罚、单元素 JSON 数组错误信封
  解包（#8409）、wb 6008 裸形状回归。
- **首尝试回滚**：新 admission 在 `LaneState` 初始化即崩溃（`_load_risk_state`
  对缺失的 zcode 状态文件 fail-closed）→ `REFUSE readiness_timeout` → 回滚已验
  证。修正：事务内按 apply 脚本同形自初始化状态文件（含缺省标记回滚）后重跑成功。
  这是"reviewed deploy 先于 -Apply"顺序下的鸡生蛋缺口，下次同类代际需在事务内
  预置新状态文件。
- **线上字节语义重放**：`LIVE_SEMANTIC_OK pin_source_ok`（对
  `/opt/cliproxyapi/cpa-admission.py` 现场字节重放同断言集）。

## 受控实战验收（经 8318 admission，单发不重试）

五发探针全部 HTTP 200：`gemini-4-hydrogen-xhigh`（agy，PONG）、
`deepseek-v4.1-flash`（wb+插件 0.21.33，PONG）、`glm-5.4`（zhipu，GLM54_OK）、
`gpt-6.1-sol`（chatgpt，SOL_OK）、`glm-5.3-flash`（zhipu）。四车道+新
zcode-start-plan 车道全 `LANE_IDLE`、`hold=None cooldown=False`、零风控信号；
zcode 状态文件保持干净占位（无假锁）。

## strict doctor（终验）

`DOCTOR_CONTRACT_OK`：`runtime_binary_hash=MATCH`（v8.1.8 fork binary
`17dc3d9c`）、`admission-integrity=OK`、`admission-health=OK`、
`workbuddy-version=cn:0.21.33 intl:uninstalled`、`zcode-sidecar-service=enabled-active`、
`credential_ban_signals_7d=0`。

## 运输层异常记录（本地，未入仓代码）

本日 `Invoke-LauncherRemoteCommand -StreamStdin`（单连接 gzip stdin 流）对
v8.1.8 时代的 apply/admission 大载荷（≈660KB gz）在 +2s 处被远端 EOF/路径重置
打断（verbose：`[chan 0] EOF received` + `EOF in transport thread`；服务端
sshd 只见连接建立无错误；bash -x 零输出=脚本未执行）。同尺寸合成载荷、doctor
载荷、分块路径、SFTP 均正常。本次以 SFTP+脱离会话与分块路径
（`-LauncherEntryPointOverride` 显式默认路径使 StreamStdin 不生效）绕开，未改
仓库代码。复测条件：下次换代若单流仍复现，先走分块。

## 回滚

`/opt/cliproxyapi/backups/20261128T010804.603467591Z-admission-zcode-suspension-20261128`
内五文件回拷 + `systemctl restart cpa-admission` + integrity check（事务自带
rollback 逻辑已实战验证过一轮）；zcode 状态文件按备份缺省标记删除；仓库侧
`git revert 3a17341` 后重跑 guardrails `-Apply`。
