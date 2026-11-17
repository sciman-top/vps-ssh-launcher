# bwg CPA 验收回执：A1–A5 + B6 演练（2026-11-17）

**性质**：只读验收 + 一次受控的换芯演练。**窗口**：2026-11-17 04:02–04:22 (+08)。

**授权**：用户 2026-11-17 04:01 (+08) "授权按你的推荐/建议连续自动修复和优化，直至完成所有任务"，
覆盖上一轮列出的 A1–A5 与 B6。**B7（真实生成探测，会消耗账号额度）未做**，等单独授权。

**背景**：2026-11-16 的四项 admission 修复与 v8.0.75 换芯只有单测与"部署后只读复验"，
没有任何请求真正走过新分支；派生镜像的验收用例（`cpa-acceptance.py`）与换芯事务的成功路径
也只跑过一次。本回执补齐这三层。

---

## A1 四项 admission 分支（用**已部署的文件**驱动）

工具：`scripts/remote/cpa-acceptance-branches.py`。它把 **`/opt/cliproxyapi/cpa-admission.py`**
作为 shadow 实例启动（独立 state 文件、独立 netns），用桩上游驱动真实请求，所以判定由
线上同一份代码产生，而不是由副本产生。

| 分支 | 判据 | 结果 |
|---|---|---|
| `pooled_models` 收窄 | 6003 → `cooldown_scope=model`、`model_cooldowns[deepseek-v4.1-flash]=300`、无 hold | **PASS** |
| `STANDING_RESTRICTION_*` | `"account has been disabled"` → `hold_reason=account_restricted` | **PASS** |
| `GEMINI_CAPACITY_MARKERS` | 非 429、无速率措辞的 `RESOURCE_EXHAUSTED` → 车道冷却 60 s | **PASS** |
| 未带 `@type` 的 `retryDelay` | `{"details":[{"retryDelay":"20s"}]}` → `server_retry_after_remaining=60` | **PASS** |

```
BRANCHES_CHECKED=4 FAILED=0
BRANCH_ACCEPTANCE=PASS
```

**代价**：6003 这一类"通用路径已成立"的容量信号现在只停模型；6004 的每（凭据 × 模型）
park 仍由插件持有。此前只有单测，现在有了行为证据。

## A2 派生镜像验收（`cpa-acceptance.py`）

从**运行中的容器**取出二进制（sha `2bdc6cbd…6e73`）放进一次性目录，在独立 netns +
mount namespace 里把该目录挂到 `/opt/cliproxyapi`，跑项目自带验收脚本：

```
{"stage": "overload", "status": 200, "overload": true, "upstream_calls": 1}
{"stage": "cooldown", "status": 503, "upstream_calls": 0}
{"stage": "waiting", "seconds": 62}
{"stage": "recovered", "status": 200, "completed": true, "upstream_calls": 1, "same_process": true}
{"stage": "actual_health", "exit": 0, "result": "HEALTH_OK"}
ACCEPTANCE_RESULT=PASS
```

**两处必须如实记录的偏差**：

1. 脚本里两处 `runpy.run_path(.../'cpa-update-acceptance.py')` 会驱动 **updater**。本机 compose
   指向派生 fork 镜像，updater 按设计拒绝（`FORK_IMAGE_REBUILD_REQUIRED`）。
   **这一处已修复（见 §A6）**：不再靠临时副本手改，脚本自己给出显式判据。
2. `FIXTURE_ONLY` 标记必须落在脚本**硬编码**的 `/opt/cliproxyapi`；生产目录不能写，故用
   `unshare -m` + `mount --bind` 在命名空间内覆盖。宿主机 `/opt/cliproxyapi` 未被写入。


## A6 updater 用例的可验证化（本轮修复）

上一版`cpa-acceptance.py`把 updater 演练写成两处内联 `runpy.run_path`，于是在"scompose 指向派生
镜像"的主机上**没有任何合法方式**说明"这一步跑不了"——操作者只能去改一份临时副本，而这正是
回执里不该出现的未声明偏差。现已改为一等公民：

```python
def run_update_rehearsal(root):
    if os.environ.get("CPA_ACCEPTANCE_DEFER_UPDATE_REHEARSAL") != "1":
        runpy.run_path(str(root / "cpa-update-acceptance.py"))["run_cases"]()
        return
    ...   # 读 compose 的 image 作为证据
    if not image:
        _refuse("update_rehearsal_deferred_without_compose_evidence")
    if image.startswith("eceasy/cli-proxy-api"):
        _refuse("update_rehearsal_deferred_on_official_image")
    print("UPDATE_REHEARSAL=DEFERRED_BY_DESIGN reason=fork_image_%s" % image)
```

三条纪律：**默认仍然真跑**；豁免必须显式设变量；豁免前必须读到 compose 证据，且**官方镜像
不许豁免**（那正是该演练存在的场景）。

**线上实测**（fixture compose 取自生产镜像行）：

```
FIXTURE_COMPOSE_IMAGE=cli-proxy-api-fork:v8.0.75-gemini-raw
ACCEPTANCE_RESULT=PASS
UPDATE_REHEARSAL=DEFERRED_BY_DESIGN reason=fork_image_cli-proxy-api-fork:v8.0.75-gemini-raw
```

单测（`tests/test_remote_acceptance_gate.py`，共 4 条）钉住三种情形：不设变量→真跑；无 compose→
拒绝；官方镜像→拒绝；派生镜像→给出带镜像名的 DEFERRED 判据。
## A3 admission 契约与姿态审计（24h）

`scripts/cpa_admission_risk_audit.py --hours 24` → `-- fail=0 warn=5 --`，判决
"PASS with warnings - warnings are accepted risk"。

| 级别 | 发现 | 判读 |
|---|---|---|
| WARN | `advertised-failing-route-stale-data`（`gpt-6-luna` 41/41、`gpt-5.6-luna` 10/16） | 日志停更 914 h，只说明停更前在失败，不是活故障 |
| WARN | `lane-cooldown-server-cap` | 既有接受项：尊重上游 `Retry-After` 优先于可用性 |
| WARN | `plaintext-http-provider`（`http-bridge-8003`） | 既有接受项，已登记 |
| WARN | `posture-data-stale` | 客户端日志停更，live 分类不可证 |
| INFO | `alias-multi-slot-pooled`、`attribution-boundary`、`late-stream-failures=0` | 预期 |

**契约半边 0 fail**：lane/路由/冷却/排除集全部自洽。

## A4 fork 补丁端到端（本轮关键发现）

在隔离 netns 里跑部署二进制 + 桩 Gemini 上游，走原生 **Gemini** 路由、以 Responses 流向下游：

```
FORK raw_reasoning_events PASS raw_deltas=4 raw_done=2 summary_deltas=4
FORK_PATCH_ACCEPTANCE=PASS
```

**两次失败的路由诊断（有价值的更正）**：

1. `codex-api-key` 路由（`/v1/responses`）**不会**把 Gemini 的 `thought` 部件翻译成
   `reasoning_text`：正文原样透传。用 codex 路由"验证 fork"会得到恒假阴性。
2. `claude-api-key` 路由（`/v1/messages?beta=true`）同样不走 Gemini translator。
3. 只有 `gemini-api-key` 路由（→ `…/models/<m>:streamGenerateContent`）走
   `ConvertGeminiResponseToOpenAIResponses`——**fork 补丁只对这条路径生效**。
   生产上它对应 **Antigravity 车道的 Gemini 模型**；ChatGPT/Claude 车道不经此翻译层。

## A5 本地客户端（发现**既有**漂移，未擅自改）

| 项 | 结果 |
|---|---|
| `cockpit_sidecar_guardrails.ps1 -Mode Audit` | `status=ok`，180 min 窗口 0 事件 |
| `-Mode Verify` | **FAIL**：`INSTALLED_HASH/INSTALLED_VERSION/RUNNING_OFFICIAL_IMAGE/GATE_FIELDS` 全 False |
| 原因 | 已装 Cockpit **1.4.0**（sha `2AA9FCC5…`），策略钉的是 **1.3.67**（sha `7977EAF9…`）；且**没有 sidecar 进程在跑** |
| `cockpit_provider_health.py` | 7 个 provider、2 条 `gateway-not-required`、1 条 `provider-plaintext-http` 提示 |

**判定**：与本次 CPA 换芯无关的**本地客户端版本漂移**。按"不擅自放宽门禁"的纪律，本轮
**没有**改 `cockpit_sidecar_policy.json` 的 pin——重新钉一个未经验证的版本等于把验收门禁改松。

## B6 换芯事务演练（v8.0.75 → v8.0.62 canary → v8.0.75）

| 步骤 | 结果 |
|---|---|
| 造 canary 二进制（v8.0.62 + 同一补丁，3 路合并） | `22850f4b…980e`，`go vet` + 测试通过 |
| 造 canary 镜像（base `sha256:c3384886…e79c`） | `cli-proxy-api-fork:v8.0.62-gemini-raw` = `sha256:c22f2643…1df9` |
| 切到 canary | `CANARY_OK`，容器内版本自证 `v8.0.62, Commit 4b3f2c7e…+gemini-raw`，`HEALTH_OK`，四车道无 hold |
| **收敛契约负向验证** | 用生产脚本对着 canary 状态跑 → `COMPOSE_UNEXPECTED ref=…v8.0.62-gemini-raw`，**拒绝执行且零变更** |
| 切回生产 | `RESTORE_OK`，`RUNTIME_BINARY`、`AUTH_BYTES_UNCHANGED`、`LANE_HOLD_DELTA=none`、`START_LOG_CLEAN` |
| 演练后 doctor | `DOCTOR_CONTRACT_OK`、`runtime_binary_hash=MATCH`、`admission-integrity=OK` |

**代价**：两次容器重建，每次约 2–3 s 真实断流（在 drain 排他窗口内）。

## C. 本轮更正（相对 11-16 回执）

| 出处 | 旧表述 | 本轮证据 |
|---|---|---|
| 11-16 §3 | fork 补丁收益写在"Gemini 翻译"下未限定路径 | 补丁**只**作用于 `gemini-api-key` 路由；codex/claude 兼容路由不经该函数 |
| 11-16 §7 | admission 四项"已投影，DOCTOR_CONTRACT_OK" | 现补上**行为证据**：四个分支各有真实请求命中 |

## D. 仍未验证 / 未做

| 项 | 原因 | 建议 |
|---|---|---|
| updater **演练本身**（`cpa-update-acceptance.py` 的 8 个场景） | 派生 fork 镜像按设计无法运行它；**判据已可验证化**（§A6），但场景内容仍未在本机跑过 | 需一个"官方镜像时代"的演练窗口 |
| ~~B7 真实生成探测~~ | ~~消耗账号额度与画像~~ | **已补做（同日 B7 追记，见下）** |
| 本地 Cockpit 1.4.0 重钉 | 未经验证的版本不进 pin | 用户确认升级方案后再改策略 |
| ~~CN/INTL 拆 lane~~ | ~~契约变更~~ | **用户已裁决，同日落地（部署回执见 cpintl-split 事务 + TOS 启用回执）** |

## F. B7 追记（2026-11-17 同日补做，用户授权）

零重试单发 ×2（8318 回环、max_tokens=64、响应体不打印）：

| 探针 | 结果 |
|---|---|
| `deepseek-v4.1-flash-intl` | http 200，1.75s，served_model=`deepseek-v4.1-flash`（fork 别名正确解析），finish=stop，in=9/out=1；admission 归因 `workbuddy-intl-oauth` |
| `deepseek-v4.1-flash` | http 200，1.72s，served_model=`deepseek-v4.1-flash`，finish=stop；归因 `workbuddy-oauth` |

零替换、零冷却、零凭据级信号；codex（luna 24h n=1386/completed 1369）与
antigravity（sonnet/hydrogen 各 1 发完成）车道已有当日自然流量活体证据，未额外
消耗。至此本回执 A1–A5 + B6 + B7 全部闭环。

## E. 回滚 / 清理

- canary 镜像与二进制保留为回滚材料，不占生产路径。
- 演练产生两份备份：`…-canary-v8.0.62`、`…-restore-v8.0.75`，由 doctor 保留窗口管理。
- 生产当前已是 v8.0.75，无需动作。
