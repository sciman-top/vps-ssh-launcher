# 深审：冗余 / 过度设计 / 门禁过重（2026-11-14）

只读审查 + 第一批已落地删减。基线来自实测命令，不是估读。

## 0. 基线（实测）

| 面 | 规模 | 备注 |
|---|---|---|
| `vps_ssh_launcher/` | 32 文件 / 6.5k 行 | 领域核心 |
| `scripts/` | 112 文件 / 54.7k 行（`scripts/remote/` 19.9k） | 含按字节投影的远端运行时 |
| `tests/` | 93 文件 / 46.5k 行 / 1,690 用例 | 单次全量 280–294s |
| `docs/` | 159 文件 / 16.7k 行 | runbooks 29 篇 7.2k；change-evidence 129 份收据 10.5k |
| 门禁 | `scripts/run_gates.ps1` fixed order | contract:catalog → build → test → invariant → hotspot → lint/type |

## 1. 结论：真正的冗余在哪

冗余的主体**不是**整段复制，而是四类结构性重复：

1. **同一契约在"注册表测试 + 每个 lane 自己的测试"两处各写一遍**（`assert_apply_is_opt_in` 11 份、`ROLLBACK_FAILED` 7 文件）。
2. **领域模块 + 大杂烩 `test_maintenance.py` 两处各测一遍**（lock/policy/automation）。
3. **把源码文本字面量当契约**（`assertIn` 全仓 2,392 处；`test_cpa_guardrails_script.py` 单个测试 818 行 / 171 个 `assertIn`）。
4. **门禁里写着不生效的开关**（`MYPY_PLATFORM` 是 mypy 不读的变量；`-FullScope Format` 无人调用）。

## 2. 已落地（本批，全部有测试或引用面证据）

| # | 改动 | 依据 | 收益 |
|---|---|---|---|
| 1 | 删 `scripts/lib/project_environment.ps1` 的 `New-LauncherTemplateConfig` | 全仓唯一命中是定义行自身；模板真源是 `vps_ssh_launcher/config.py:initialise_user_config` + `target.example.json` | 15 行死代码 |
| 2 | 删 `run_gates.ps1` 的 `MYPY_PLATFORM` 写入/保存/恢复 | mypy 只认 `--platform`；venv 内 grep 0 命中 | 8 行 + 一个误导性开关 |
| 3 | 删 `run_gates.ps1` 的 `-FullScope Format` 分支 | CI 只用 Static/Runtime，README 只文档化两者，无测试断言 Format | 5 行死分支 |
| 4 | 删 `state.py` 的 `save_plan(allow_invalid_root_update=...)` 旁路 | 唯一调用者是测试；该旁路无条件覆盖 `plan_json`，正是 `record_plan_receipt` 要防的回退 | 22 行 + 关闭一条测试专用后门 |
| 5 | `save_plan_if_absent` 返回 `(plan, newly_stored)`，删 `_PLAN_EXISTENCE_PROBE=1000` 历史扫描 | 原来靠 `list_plans(limit=1000)` 反推"本次是否新建"，同一事务里已知 | 12 行 + 去掉一次 O(n) 扫描 |
| 6 | 删 `automation.py` 的 `AUTO_PROFILE = AUTO_PROFILE` 自赋值 | 无意义自引用 | 2 行 |
| 7 | 删 `tests/test_maintenance.py` 中与 `test_maintenance_lock.py` 完全重复的 6 个锁用例 | 后者 16 例逐一覆盖同分支，且更新 | ~140 行 |
| 8 | 删 `test_config.py` 的 `Socks5ResolutionTests` | `test_socks5_proxy.py` 的 `ResolveSocks5ProxyTests` 覆盖同函数同分支且更全 | ~78 行 |
| 9 | 删 4 个低价值元测试（testpaths 自证、docs.yml 字面、conftest 字符串自指、adapters 文本版层次） | 分别由 pytest 自身、CI 失败可见性、行为测试覆盖 | ~75 行 |
| 10 | 修 `test_connect_entrypoints` 模板断言指向真源 | 原断言钉在被删的 PowerShell 死代码上 | 修正 1 个脆弱断言 |
| 11 | 修 README.md / README.en.md 被标题打断的表格行 | 表格尾两行落在 `### 工作树行尾` 之后，渲染为裸文本 | 两处结构缺陷 |
| 12 | 修 `docs/change-evidence/README.md` 的过期存量快照 | 实测 129 份 / 引用 43 / 未引用 86（页面写 127/45/82）；并注明快照会漂移 | 消除必然漂移 |

## 3. 审查已确认但**未**动（结论与理由）

- **`scripts/patches/` 7.1k 行**：15/16 补丁被自己的 README 标为历史或已退役，唯一在用的也已上游收编。删除需单独授权（批量删除类变更），本批只记录。
- **`scripts/remote/` 19.9k 行**：逐字节投影 + SHA256 钉住，删改会改远端完整性契约；需要单独一轮，且必须同时改投影清单与 pin。
- **`docs/change-evidence` 86 份未引用收据**：政策页已写明"本月收据一律不删"；跨月未引用为 0。是否迁到 `archive/` 是政策决定，不是清理动作。
- **`test_cpa_guardrails_script.py` 的 818 行字面量测试**：收益最大（~450 行），但 doctor 输出标签可能被外部消费者依赖，需要逐条确认后才可拆。
- **`test_cpa_admission.py` 的 37 个 `BaseHTTPRequestHandler` 子类**：可收敛为工厂（~300 行），但改动面大，需独立批次。
- **`run_gates.ps1` 的 `test:filesystem-baseline` 腿**：与主 test 腿跑同一组测试，仅翻转环境变量；可用纯函数单测替代，但要先证明 conftest 分支仍被覆盖。

## 4. 明确判定为"不过度设计"（不要动）

`scripts/lib/proxy_tls_contract.ps1`（5 个消费者）、`proxy_firewall_contract.ps1`（2 个消费者）、`cpa_health_runtime.py`（被远端哈希钉住）、`launcher_transport.py` + `audit_cli.py`（真实复用）、三个 CI workflow（职责正交）、`invariant`（pip check + dependency_audit）审计的是解释器实际安装集合，不是 `requirements.txt` 的重复。

## 5. 验证

- `pytest` 全量、`run_gates.ps1`（Auto→Full）、`check_docs.py`、`ruff check/format`、`mypy`。
- 回滚：本批只改工作树，`git checkout -- <path>` 即可；无远端写入。

## 6. 未覆盖

`scripts/remote/` 逐函数审查、`cockpit_provider_health.py` / `cpa_status.py` / `cpa_natural_traffic_audit.py` 完整性、mutation 检验（未评估"测试是否真能失败"）。
## 7. 2026-11-15 追加批次（第二轮深审 → 修复）

第二轮只读深审（读真实代码 + 隔离复现）发现 4 个"门禁看不到"的真实缺陷，已全部修复并各自补回归测试：

| # | 缺陷 | 依据（复现） | 修复 |
|---|---|---|---|
| 1 | `vps_ssh_launcher/maintenance/apply.py` 的中断收据指针被前向转移守卫静默丢弃，磁盘留下没人引用的收据 | 真实 `save_plan` 路径复现：修复前 AFTER 仍为 `applied` 且 `receipt_path` 为空 | 改走 `record_plan_receipt`（只挂指针），新增 `test_interrupted_apply_links_its_receipt_from_an_applied_plan`（撤掉修复必红） |
| 2 | `scripts/remote/cpa-guardrail-apply.sh` 只 trap EXIT/INT/TERM：SIGHUP 时 bash 仍执行 EXIT trap 但 `$?==0`，恢复被跳过却报成功 | bash 探针实测 `exit_trap_rc=0` + `outer_rc=129`，`RESTORE_RAN` 未打印 | 补 `trap 'exit 129' HUP`；回滚判据改为事务阶段 `MUTATED`；新增真实发 HUP 的行为测试 |
| 3 | `tests/script_validation_support.py` 的 `LANE_SCRIPTS` 手写 14 条，而 `scripts/` 有 16 个投影 `-Apply` lane，`cpa_workbuddy_*` 完全在写安全契约之外 | 目录扫描实测 16 vs 14 | 注册表改为目录派生 + 带理由的显式豁免表，并断言"派生集合必须等于树" |
| 4 | `scripts/check_docs.py` 不覆盖 `docs/audits/`；`.github/workflows/docs.yml` 仍写已迁出的 `HG03_Final_v4/**` | 实测 `DOCS` 40 篇不含 audits；该目录已不在仓内 | 文档扫描改为 `docs/**`（排除 `change-evidence`）；CI 触发面同步，新增两条回归 |

同批修掉的小缺陷（各有回归测试）：

- `scripts/reality_alt_sni_lane.ps1` 用私有 `/run/vps-ssh-launcher-alt-sni.lock`，而它同样重写 xray/nginx 并重启 xray → 改用共享维护锁；`test_lock_registry.py` 收缩为"单一锁"。
- `scripts/zz_xhttp_nginx_front.ps1` 的 dry-run 分支写 `write_status success`，会覆盖上一次真实 apply 的结果 → 只读分支不再写状态。
- `scripts/merged_subscription_lane.ps1` 的 `merged-sub-token.txt` 与 URL 文件用裸 `Set-Content`（继承父目录 ACL）→ 用 `icacls /inheritance:r` 收紧并复核。
- `scripts/nginx_camouflage_site.ps1` 只 `nginx -t` 不 reload，http 级 404 drop-in 首次 apply 不生效而漂移门仍绿 → 补 reload（可覆盖，harness 用 stub）。
- `scripts/remote/frp_bwg_relay.py` 的 `config-contract` 恒定打印 PASS → 真正读取并校验，失败非零退出。

### 7.1 顺带发现的测试基础设施缺陷（影响面更大）

Git Bash 对 `bash -c` 的脚本在约 8192 字节处**静默截断且仍返回 0**。`nginx_camouflage_site.ps1` 的 19 KiB payload 因此在行为测试里只跑了前 8 KiB——测试一直是绿的，但尾部（含本轮新增的 reload）从未被覆盖。已在 `ScriptValidationMixin._spill_long_script` 统一处理：超过 8000 字节的 `-c` 脚本落到临时文件再执行，所有 lane harness 自动受益。

### 7.2 验证

`pwsh -NoProfile -File scripts/run_gates.ps1 -Profile Full`：`1677 passed, 31 skipped`，catalog / ruff / format / mypy / bandit 全绿。
## 8. 2026-11-15 第三批：投影与受控实战验收

### 8.1 修复（代码侧）

`scripts/remote/frp_bwg_relay.py` 的两个非 contract 模式此前**完全不可用**：它们导入 `scripts._remote_ops`，而该模块在本仓从未存在（`git log --all` 无任何记录），所以 `bwg` 与 `aliyun` 都在打开 socket 前就以 ImportError 退出。现在改为复用启动器已有的配置解析、私有 DACL 校验、profile 校验与严格 host-key 检查。

临时文件保持文档承诺的形状：一个指向真实 target.json 的指针，凭据文件本身不离开私有位置。

### 8.2 真实联调发现的第二个缺陷

主机侧 `ALIYUN_CONTROL_ESTABLISHED` **永远报 0**，而中继显然是通的。原实现只看行尾字段，但服务端该位置是客户端临时端口，监听端口在本地列。这与本模块存在的意义（失败看起来像 0）是同一类缺陷，只是低了一层。现在按形状匹配地址字段（两端任一命中），既修正两端计数，也仍能忽略 Recv-Q/Send-Q 列。

### 8.3 投影（真实远端写入）

`-Apply` 首次被正确拒绝：`REFUSE admission_pin_transition_pending`。这是门禁按设计工作，不是缺陷——主机 admission 停在 `d2256369`，仓库 HEAD 要求 `104c0ef5`，中间跨了 2 个代际（`33fc8a6` 的 O_NOFOLLOW 加固与 `cc6f80e` 的 ELOOP 归类）。

按设计路径处理，未绕过门禁：

| 步骤 | 证据 |
|---|---|
| dry-run 复核事务 | `DRY_RUN_OK baseline=d2256369 candidate=104c0ef5 idle=true no_swap=true` |
| 代际轮换（排空 → 备份 → 只重启 admission → 回读） | `DEPLOY_OK pin=104c0ef5`，备份 `20261115T013036...-admission-delatch-20261114` |
| 重新投影 | `GUARDRAILS_APPLIED`，`EXIT=0` |
| doctor 复查 | `admission-integrity=OK`、`DOCTOR_CONTRACT_OK`、`EXIT=0` |

注：`outputs/deploy-admission-delatch-20261114.ps1` 的 `-ExpectedLivePin` 默认值 `de1a548d` 是更早的代际，对当前主机必然 `REFUSE live_guard_generation_drift`。已用显式参数传入真实基线，未修改该历史事务脚本。

### 8.4 验收结果（只读或受控写入，未触碰 zz）

```text
BWG_FRPC_RELAY_ACTIVE=active      BWG_FRPS_CONTROL_ESTABLISHED=1
BWG_LOCAL_REALITY_LISTENER=1      BWG_FRPC_VERSION=0.72.0
ALIYUN_FRPS_ACTIVE=active         ALIYUN_EXPECTED_LISTENERS=2
ALIYUN_CONTROL_ESTABLISHED=1      ALIYUN_FRPS_VERSION=0.72.0
valid_path_unauth=401  bare_path=404  wrong_path=404
HARDENING_DRIFT=NONE
admission-integrity=OK  DOCTOR_CONTRACT_OK
```

### 8.5 未覆盖

- **live_accepted**：需要真实联网客户端（复制节点、改地址端口、打开境外网页）。这是操作者判定，不由代理代签。
- **zz**：本轮未做任何远端写入，也未投影。
- **admission 代际升级本身的功能验收**：只证明部署成功与既有契约（healthz 200、integrity OK、public route 401/404/404），未对 de-latch 行为做流量级验证。
