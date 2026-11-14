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
