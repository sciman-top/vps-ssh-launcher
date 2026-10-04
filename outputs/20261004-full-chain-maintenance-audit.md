# 个人 VPS 长期维护全链自动化 · 深度审查报告

- **审查对象**：`D:\CODE\vps-ssh-launcher`（本地工具 + bwg/zz 远端系统/软件/CPA/代理内核维护全链）
- **审查日期**：2026-10-04
- **审查方式**：全量只读代码走查（约 15.6k 行脚本 + 4.2k 行 Python + 13k 行测试 + 3.7k 行 runbook）、契约与实现逐条对齐、官方文档/社区实践比对、外置参考树（CLIProxyAPI v8.0.13）核对。**未连接真实主机、未做任何远端写入。**
- **结论一句话**：这是一个**成熟度显著高于同类个人项目的工程**——fail-closed 边界、投影闭环、脱敏、回滚与契约断言体系完备。本次审查初始发现 **1 个 P0（安全断言失效）**、**5 个 P1（可观测/事务边界/语义一致性）**、**17 个 P2（覆盖与卫生）**；随后已在工作树完成 P0、P1-3/4/5 及多项 P2 的最小修复并通过本地门禁，仍有无人值守告警、真实主机状态与部分覆盖/保留策略问题待后续决策。

### 当前执行状态（2026-10-04）

- **已修复并验证**：内核 Xray/sing-box 版本、SHA-256、服务和配置校验的 bash 短路语义；远端适配器 300 秒 idle / 900 秒 hard timeout 与阶段 marker；CPA updater 的 `75/76` 退码；APT 锁等待、`dpkg --configure -a` 自愈和 `autoremove --purge` 预演；Google/Gemini 路由逐文件后置校验；README/AGENTS/runbook 契约漂移；`scripts/` 远端 Python 的 compileall、ruff、bandit 覆盖。
- **当前证据**：`.venv\\Scripts\\python.exe -m pytest -q` → `412 passed, 1 skipped, 323 subtests passed`；`pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1` → build/test/bandit/ruff/format/mypy 全部通过；PowerShell AST 解析 → `PS_PARSE=PASS`；`git diff --check` 通过。
- **仍未验收**：未连接任何真实 VPS，未执行 `-RunIntegration`、`-Apply`、内核升级、系统升级、重启或服务重载，因此 `host_loaded`、`controlled_live_replay`、`natural_live_accepted` 均不能宣称。

---

## 0. 审查范围与方法

| 层 | 覆盖对象 | 方法 |
|---|---|---|
| 本地工具链 | `vps_ssh_launcher/cli.py`、`connect.ps1/.cmd`、`run.cmd`、`scripts/lib/project_environment.ps1`、`auto_install.py` | 逐函数走查 + 安全边界反证 |
| 维护控制平面 | `vps_ssh_launcher/maintenance/*`（adapters/automation/config/inventory/planner/state/receipt） | 三重开关与 fingerprint/回滚路径逐条验证 |
| 远端维护自动化 | `system_maintenance_cron.ps1`、`vasma_kernel_update_cron.ps1`、`v2ray_agent_*_cron.ps1`、`google_ipv4_routing.ps1`、`install_vps_maintenance_task.ps1` | 生成脚本的 bash 语义逐行核对（含 `set -e` 作用域） |
| CPA 护栏链 | `cpa_bwg_guardrails.ps1`（4160 行）、`cpa-auto-update.sh`、`cpa-admission.py/.json`、`cpa_provider_routes.json`、`cpa_policy.py`、doctor 断言 | 投影闭环 + 事务/回滚 + 契约点三处一致性 |
| 代理内核 | vasma xray/sing-box wrapper、`singbox-core-update-migration.md`、`kernel-core-manual-rollback.md` | 与 vasma 菜单耦合点、pin 语义、双 lane 互斥 |
| 门禁与测试 | `run_gates.ps1`、`pyproject.toml`、13 个 `test_*.py`、CI workflows | 覆盖面反推（哪些文件根本不在门禁内） |
| 外部对照 | Xray-core / CLIProxyAPI v8.0.13 源码、nginx / fail2ban / systemd / apt 官方语义、社区最佳实践 | 差异定位 |

**证据纪律**：本报告所有条目均**由本人直接读取源码/文件行号核实**，不采信未复核的转述。文末第 9 节专门列出"经复核后被判定为非缺陷"的疑点，避免后续误修。

---

## 1. 全链拓扑与真源清单

```
[本地 Windows]
  run.cmd → connect.cmd → connect.ps1 → ssh_tool.py → cli.py        # 连接/执行
  vps_maintenance.ps1 / install_vps_maintenance_task.ps1             # 每日 20:00 观察任务（S4U，隐藏）
        └─ vps-maint {inventory,plan,apply}  ← maintenance.toml      # 声明式控制平面（Xray/Docker 适配器）
  cpa_bwg_guardrails.ps1  (默认只读 doctor；-Apply/-RotatePath/-Quarantine/-Restore/-Deactivate)  # bwg 专用
  cpa_failure_triage.py / cpa_admission_risk_audit.py / cockpit_provider_health.py / cpa_error_dump_forensics.py  # 只读归因

[bwg VPS]
  /etc/cron.d/vps-launcher-monthly-maintenance   → monthly-maintenance.sh   (每月 1 日, apt)
  /etc/cron.d/vps-launcher-kernel-update         → auto_update_{xray,singbox}.sh (周更, 经 vasma 菜单)
  /etc/cron.d/vps-launcher-v2ray-agent-update    → vps-launcher-v2ray-agent-update.sh (周五 14:40)
  /etc/cron.d/vps-launcher-v2ray-agent-renewtls  → vps-launcher-v2ray-agent-renewtls.sh
  cliproxyapi-update.timer                       → cpa-auto-update.sh      (每日, 72h 成熟期)
  systemd cpa-admission.service (127.0.0.1:8318) → CPA 容器 (127.0.0.1:8317) ← nginx 8443 + 16-hex 路径
  全部写事务共用 /run/vps-ssh-launcher-maintenance.lock (flock -n)
```

**真源（single source of truth）**
- 路由/排除/别名：`scripts/remote/cpa_provider_routes.json`
- admission 契约：`scripts/remote/cpa-admission.json` ↔ 硬编码常量 `cpa-admission.py:62/67/68`
- 远端维护产物：由各 `*_cron.ps1` 内联 heredoc 生成（**无仓内副本**）
- 门禁：`scripts/run_gates.ps1` + `pyproject.toml`

---

## 2. 总体评价

**做得好的（同类项目罕见）**
1. **fail-closed 贯穿**：`-Apply` 前置 `Assert-ProjectionSourcesUnchanged`（工作区必须干净）、doctor 比对 `HEAD` blob 而非工作区、`write_base64_file` 写后校验哈希、任一步失败即 `restore_all` 并报 `ROLLBACK_VERIFIED/FAILED`。
2. **多源互锁**：guardrails `-Apply` 与 updater/系统维护/内核 wrapper 共用同一 `flock` 文件；`-QuarantineOAuthLuna` 期间 `-Apply` 主动 `REFUSE`，防止例行投影静默撤销风控决定。
3. **契约点显式化**：`limit_conn cpa_cc 20` 等参数在 doctor 断言、`-Apply` required 清单、`ensure_nginx_directive` 锚点三处同步，并有 `test_scripts.py` 断言与 runbook。
4. **归因纪律成熟**：四层 429 形态对照表、`cpa_failure_triage.py` 按"等待预算指纹"分层、`cpa_error_dump_forensics.py` 只读响应段且退出码即判据。
5. **安全卫生**：`config.yaml`/`auth` 属主独占断言、error dump 24h 老化 + `chmod 600`、fail2ban 回环豁免为不变量、凭据销毁与流量隔离分离。

**系统性短板（本次审查的主要价值）**
- **A. 安全断言与实现语义脱节**（P0）：`set -e` 在 bash 条件上下文失效，导致两个内核 wrapper 的版本/哈希校验成为死代码。
- **B. 全链"静默"**（P1）：所有远端定时任务失败只落远端日志，无 `OnFailure`/心跳/通知；本地 doctor 也不汇总远端 wrapper 的上次结果。无人值守链路的失败可见性依赖"人恰好去看"。
- **C. 已采集的事实未被使用**（P1）：`reboot_required` 已被 inventory 采集，却既不进 plan 也不进 doctor 告警——补丁"装而未生效"这一最常见运维陷阱无判据。
- **D. 静态分析覆盖与运行时代码错位**（P2）：门禁的 ruff/mypy/bandit **不覆盖** `scripts/remote/*.py`——而那正是 admission 闸门、策略校验、健康探针的实现（约 3.3k 行）。

---

## 3. 发现汇总表

| 编号 | 级别 | 位置 | 标题 | 状态 |
|---|---|---|---|---|
| P0-1 | **P0** | `scripts/vasma_kernel_update_cron.ps1:377-382, 756-760` | 内核升级的版本/SHA-256 断言因 bash `set -e` 条件上下文失效而成为死代码 | 已复核（源码+语义） |
| P1-1 | P1 | 全部 `/etc/cron.d/*` + `docs` | 无人值守维护链无主动告警/心跳，失败静默 | 已复核 |
| P1-2 | P1 | `inventory.py:98-102` / `system_maintenance_cron.ps1:377-380` | `reboot_required` 已采集但从不告警/动作 → 内核补丁长期不生效 | 已复核 |
| P1-3 | P1 | `maintenance.example.toml` + `maintenance_cli.py:470-472` | 控制平面默认 `command_timeout=30`（hard 60s）对 Xray/Docker 下载+重启事务偏紧，静默期可触发空闲超时 | 已复核 |
| P1-4 | P1 | `scripts/remote/cpa-auto-update.sh:9, 252-255` | 锁忙退码用 `1` 而非约定的 `75`；卫生步骤 `exit 1` 与真实更新失败同形 | 已复核 |
| P1-5 | P1 | `scripts/system_maintenance_cron.ps1:311` | `autoremove --purge` 无预演、无删除清单记录 | 已复核 |
| P2-1 | P2 | `run_gates.ps1:208-211` | bandit 仅扫 3 个 source target；`scripts/remote/*.py` 与 4 个 `scripts/*.py` 完全不在静态分析内 | 已复核 |
| P2-2 | P2 | `pyproject.toml` testpaths ↔ `run_gates.ps1:147-159` | 测试清单双源，易漂移 | 已复核 |
| P2-3 | P2 | `AGENTS.md` C 节 ↔ `run_gates.ps1:193-213` | 声明的 fixed order 含 `invariant`，但默认 Full 不含该阶段 | 已复核 |
| P2-4 | P2 | `pyproject.toml [tool.ruff.lint]` | lint 仅 `E4,E7,E9,F` | 已复核 |
| P2-5 | P2 | `v2ray_agent_script_update_cron.ps1:158-160` | 只读分支在缺 `install.sh` 或服务不 active 时非零退出 | 已复核 |
| P2-6 | P2 | `google_ipv4_routing.ps1:176` | 后置校验 `grep -Eq … f1 f2` 任一文件命中即通过 | 已复核 |
| P2-7 | P2 | guardrails/vasma/adapters 备份目录 | `/root/cpa-guardrails-backup-*`、`/var/backups/v2ray-agent-*`、`vps-ssh-launcher-*` 无保留策略/轮转 | 已复核 |
| P2-8 | P2 | `system_maintenance_cron.ps1:295-336` | 无 `dpkg --configure -a` 自愈；apt 无锁等待/超时 | 已复核 |
| P2-9 | P2 | `cpa-gateway.conf` | nginx 网关配置无仓内真源，主机重建不可从仓库还原 | 已复核 |
| P2-10 | P2 | `cpa-admission.py:395-400` | 单测手搓 config 可省略 `early_probe_interval_seconds`（仅测试面，非生产路径） | 已复核 |
| P2-11 | P2 | `README.md:321-324` | admission lane 成员与 `cpa-admission.json` **漂移**（README 漏 `gpt-6.1-sol`、多 `deepseek-v4-pro`） | 已复核 |
| P2-12 | P2 | `AGENTS.md:17`、`test_scripts.py:2061` | 写入面清单只列 `-Apply`/`-RotatePath`，遗漏 `-Quarantine/-Restore/-DeactivateOAuthLuna`（三者确实写远端） | 已复核 |
| P2-13 | P2 | `test_scripts.py` | 519/776（≈67%）断言是 `assertIn/assertNotIn` 静态文本匹配，**可被等价改写绕过** | 已复核 |
| P2-14 | P2 | `test_scripts.py:2038-2046` | PS7 保护只覆盖 12 个 `.ps1` 中的 7 个（其余 5 个实际都有 `#requires`，故为覆盖缺口而非现存缺陷） | 已复核 |
| P2-15 | P2 | `pyproject.toml [tool.pytest.ini_options]` | 未启用 `--strict-markers` / `filterwarnings = error`；`skipTest` 依赖 bash/pwsh 存在（本机 `bash -l` 约 3s） | 已复核 |
| P2-16 | P2 | `test_temp_cleanup_guard.py:86` | `assertTrue(removed or recorded)` 近似恒真 | 已复核 |
| P2-17 | P2 | `docs/runbooks/cpa-gateway.md:41` | 终判 marker 写作 `OBSERVE_OK/OBSERVE_FAILED`，实际输出为 `DOCTOR_CONTRACT_OBSERVE_OK/FAILED` | 已复核 |

---

## 4. P0 详述

### P0-1 内核升级的版本/SHA-256 断言是死代码（bash `set -e` 条件上下文语义）

**当前状态：已修复。** `scripts/vasma_kernel_update_cron.ps1` 已改为每一步显式 `|| return 1`，并新增 bash 行为级回归测试；本地全量测试和统一门禁通过。

**位置**
- `scripts/vasma_kernel_update_cron.ps1:377-382`（生成的 xray wrapper）
  ```bash
  verify_target_xray() {
    [ "$(current_xray_version)" = "$TARGET_VERSION" ]
    [ "$(sha256sum "$XRAY_BINARY" | awk '{print $1}')" = "$EXPECTED_SHA256" ]
    [ -x "$XRAY_BINARY" ]
    verify_current_xray
  }
  ```
- 同文件 `:756-760`（sing-box 同构）；调用点 `:437`、`:818`：`if ! verify_target_xray; then … restore_xray … exit 11; fi`

**现象（机制）**
bash 手册明确规定：**当一个函数在 `-e` 被忽略的上下文中执行时，函数体内所有命令都不受 `-e` 影响**。`if ! func` 正是这种上下文。因此 `verify_target_xray` 的返回值**只取最后一条命令的状态**：

- `verify_target_xray` → 末条为 `verify_current_xray`
- `verify_current_xray`（`:372-375`）→ 末条为 `xray run -test -confdir …`；其首行 `service_is_active xray` 的失败同样被吞掉

结论：`[ "$(current_xray_version)" = "$TARGET_VERSION" ]` 与 `[ "$(sha256sum …)" = "$EXPECTED_SHA256" ]` 的失败**不会**导致 `exit 11`，也**不会**触发 `restore_xray`。sing-box 侧同理，末条退化为 `assert_google_ipv4_route`。

**影响**
- `README.md`（"### vasma 内核周更"）与 `docs/runbooks/kernel-core-manual-rollback.md` 承诺的 *"升级后复验版本、哈希、配置和服务，失败时恢复二进制并报告 `ROLLBACK_VERIFIED`；缺少 pin、latest 漂移或校验失败都会 fail closed"* 中的**版本与哈希两部分未被执行**。
- 实际仍然生效的只有：`latest_version == TARGET_VERSION` 前置门（`:427/:807`）、`xray run -test` 配置校验、二进制原子替换、`trap recover_on_error ERR`。
- 真实风险场景：vasma 装出**非 pin 版本**或**损坏/被替换的二进制**（只要 `xray run -test` 能过），wrapper 会走到 `:442 UPDATE_STARTED=0` 并记 `"========== vasma Xray-core update done =========="`，**静默接受**。

**佐证（同仓正确模式）**
`vps_ssh_launcher/maintenance/adapters.py:102-154` 的 Xray 适配器把同类校验写成**顶层命令 + `set -Eeuo pipefail`**（errexit 生效）：
```bash
"$binary" version | awk '/^Xray / {print $2; exit}' | grep -Fx "$version" >/dev/null
"$binary" run -test -confdir "$confdir"
systemctl restart xray
systemctl is-active --quiet xray
```
即：**同一仓库里已经存在正确的实现，内核 wrapper 是唯一的例外。**

**建议修复**（显式短路，不依赖 errexit）
```bash
verify_current_xray() {
  service_is_active xray || return 1
  "$XRAY_BINARY" run -test -confdir "$XRAY_CONFDIR" >> "$LOG" 2>&1 || return 1
}
verify_target_xray() {
  [ "$(current_xray_version)" = "$TARGET_VERSION" ] || return 1
  [ "$(sha256sum "$XRAY_BINARY" | awk '{print $1}')" = "$EXPECTED_SHA256" ] || return 1
  [ -x "$XRAY_BINARY" ] || return 1
  verify_current_xray || return 1
}
```
`verify_target_singbox` / `verify_current_singbox` 同法。**并补一条回归测试**：在 `test_scripts.py` 中对生成出的 wrapper 做 `bash -c` 级语义断言（构造"版本不符但配置合法"的 stub，断言函数返回非零）——否则同类回归会再次逃过字符串断言。

---

## 5. P1 详述

### P1-1 无人值守维护链无主动告警/心跳（静默失败）

**位置**：`/etc/cron.d/vps-launcher-*` 全部 wrapper；`cliproxyapi-update.timer`；`README.md` 明示"失败通过本地日志、receipt 和非零 Task Scheduler 结果暴露"。

**现象**：四类远端定时任务（月度 apt、内核周更、v2ray-agent 脚本更新、RenewTLS、CPA updater）**全部静默**：
- 失败只写远端日志：`/var/log/monthly-maintenance.log`、`/etc/v2ray-agent/crontab_*_update.log`、`/opt/cliproxyapi/auto-update.log`
- 无 `systemd` 的 `OnFailure=` 单元、无 heartbeat、无外部通知（仓库内 grep 不到任何 alert/webhook/notify/MTA 配置）
- 本地 doctor（`cpa_bwg_guardrails.ps1`）**只**汇总 CPA 侧（`==timer==`/`==timer-result==`/`auto-update.log` tail），不汇总月度维护、内核周更、脚本更新三类 wrapper 的结果

**影响**：一次失败可能数月不被发现。最典型的是**内核 pin 落后**：`latest != TARGET_VERSION` 时 wrapper 记 `UNVERIFIED` 并 `exit 10`（安全但静默），pin 就永久停在旧版本而无人知晓。

**建议**（按成本排序）
1. 最低成本：给 cron.d 增加 `MAILTO=<addr>`（需本机 MTA）或改用 systemd timer + `OnFailure=vps-launcher-alert@.service`。
2. 与现有体系一致：把"三类 wrapper 最近一次结果 + 日志尾部"纳入 `cpa_bwg_guardrails.ps1` doctor（新增 `==kernel-timer==`/`==monthly-maintenance==` 段），让既有的"本地 doctor 就是巡检入口"继续成立。
3. 状态化：让 wrapper 写一个 `/run` 或 `/var/lib` 下的 `last_result` 状态文件（含 UTC 时间戳与结论码），doctor 对"超过 N 天未成功"报 FAIL。

### P1-2 `reboot_required` 已采集但从不使用

**当前状态：部分修复。** 远端月度维护和 CPA doctor 已输出 `REBOOT_REQUIRED`/`reboot_required` 及年龄告警；它仍未进入 `maintenance` planner 的阻断/延期动作，因此真实主机仍需人工决定重启窗口。

**位置**：`vps_ssh_launcher/maintenance/inventory.py:98-102`（采集 `reboot_required=present|absent`）；`planner.py` 全篇无引用；`system_maintenance_cron.ps1:377-380` 仅 `log "WARN: reboot required; NOT rebooting automatically"`。

**现象**：事实被采集进 inventory 并参与 fingerprint，但 plan 里没有任何 action/warning 消费它；本地 doctor 也不读它。

**影响**：`apt-get upgrade` 后若需重启（内核 / libc / systemd），补丁**装而未生效**，且无任何判据提示。这是长期无人值守 VPS 最典型的"看着在维护、实际没打上"的陷阱。

**建议**：在 `planner.py` 增加一个**只读告警型 action**（`status="deferred"` 或新增 `warning` 类），或在 doctor 增加 `reboot-required` 断言；进一步可记录首次出现时间，超过阈值（如 30 天）报 FAIL。

### P1-3 控制平面默认超时对远端适配器偏紧，且事务静默期无输出

**当前状态：已修复。** 适配器执行已使用独立的 300 秒 idle / 900 秒 hard 下限，并在下载、安装/拉取、校验阶段输出 marker。

**位置**：`maintenance.example.toml` `[settings] command_timeout = 30`；`maintenance_cli.py:467-472`：
```python
command_timeout=policy.command_timeout,             # 30 → idle timeout
command_hard_timeout=policy.command_timeout * 2,    # 60 → 绝对上限
```

**现象**：`build_xray_upgrade_command` 的下载段为
```bash
curl --fail --silent --show-error --location … "$archive_url" -o "$tmp_dir/xray.zip"
```
`--silent` 期间**零 stdout**。Paramiko 的 idle 计时按"无输出"推进 ⇒ 下载耗时 >30s 即本地掐断；hard 上限 60s 同样可能早于"下载 + unzip + restart + 复验"完成。

**影响**：本地掐断后远端脚本可能继续（`install_vps_maintenance_task.ps1:68-70` 的注释已承认"a mid-transaction hard kill orphans the remote adapter work"），本地记 `unverified`，形成"远端已改、本地不知"的漂移；且下一次 apply 会因 `applied`/`unverified` 状态要求人工复核。

**建议**：把适配器命令的超时与全局 `command_timeout` 解耦（adapter 层显式 ≥300s），或在远端脚本下载前后打印 marker（保持 `--silent` 但加 `echo`）以续期 idle 计时；示例 TOML 的默认值应给出与真实事务时长匹配的量级说明。

### P1-4 CPA updater 的退码语义与其余 wrapper 不一致

**当前状态：已修复。** 锁忙使用 `75`，卫生拒绝使用 `76`，成功更新后的卫生失败保持非致命并记录告警。

**位置**：`scripts/remote/cpa-auto-update.sh:9`
```bash
flock -n 9 || { echo 'UPDATE_ALREADY_RUNNING'; exit 1; }
```
对比其余 wrapper（`vasma_kernel_update_cron.ps1:315`、`system_maintenance_cron.ps1:227`、`google_ipv4_routing.ps1:126`）统一使用 `exit 75`（`EX_TEMPFAIL`）。

另 `:252-255`：`secure_error_dumps` 失败 → `log 'DEFER: …'` + `exit 1`。

**影响**：`systemctl show cliproxyapi-update.service -p Result/ExecMainStatus` 无法区分「锁忙跳过」「卫生步骤拒绝」「真实更新失败」——而 doctor 的 `==timer-result==` 段正是读这两个字段。运维判读会被误导。

**建议**：锁忙统一 `exit 75`；卫生拒绝用独立码（如 `exit 76`）；doctor 相应按码分档展示。

### P1-5 `autoremove --purge` 无预演、无删除清单

**当前状态：已修复。** 月度脚本现在先执行并记录 `apt-get -s autoremove --purge`，再进入实际 purge。

**位置**：`scripts/system_maintenance_cron.ps1:300-312`
```bash
step "apt-get update" apt-get update
# upgrade 前有 apt-get -s upgrade --with-new-pkgs 预演（:302）
step "apt-get upgrade --with-new-pkgs" apt-get upgrade --with-new-pkgs "${APT_OPTS[@]}"
step "apt-get autoremove --purge" apt-get autoremove --purge "${APT_OPTS[@]}"
```

**现象**：升级前有 `-s` 模拟并记录 `apt-simulation-before-upgrade`；但 `autoremove --purge` 既无 `-s` 预演，也不记录将被删除的包清单。

**影响**：无人值守下 `--purge` 会连同配置文件一起删除；若某个"孤儿"包实际被业务依赖（如手工安装的驱动/库），事后无清单可回溯。

**建议**：升级前追加 `apt-get -s autoremove --purge` 并把 `Remv`/`Purg` 行写日志；对关键包 `apt-mark hold`；或把 `--purge` 降级为 `autoremove`（保留配置），由人工决定 purge。

---

## 6. P2 详述（覆盖、卫生与一致性）

下表记录初始审查发现。当前工作树已收口 P2-1（扩大 `scripts/` compileall/ruff/bandit 覆盖）、P2-3、P2-5、P2-6、P2-8、P2-11、P2-12、P2-17；P2-7 目前增加了 doctor 计数但尚无轮转，其他条目仍按表中建议保留。

| 编号 | 位置 | 问题 | 建议 |
|---|---|---|---|
| P2-1 | `run_gates.ps1:208`（bandit 用 `$sourceTargets`）、`:147-169`（`$testFiles`/`$supportFiles`） | **静态分析盲区**：`scripts/remote/*.py`（`cpa-admission.py` 1351 行、`cpa_policy.py` 999 行、`cpa-health.py` 926 行——正是安全闸门实现）与 `scripts/{cockpit_gate_wait_cap_check,cockpit_non_oauth_replay,cockpit_request_log_audit,cpa_stream_acceptance}.py` 均不在 ruff/mypy/bandit 范围；bandit 只扫 3 个 target。其中 `cpa_stream_acceptance.py` **被 `test_scripts.py:4094` import 却未登记进 `$supportFiles`** ⇒ 连 compileall/lint 都没有 | 把 `scripts/` 全量（或至少 `scripts/remote/`）加入 `$pythonTargets` 与 bandit 目标。行为测试虽已覆盖（`test_scripts.py` 以子进程驱动），但缺少 lint/类型/安全扫描。另：`cpa_recovery_workflow.ps1`（README 声明的"统一执行入口"，含 `-ApplyRemote` 远端写）**没有任何行为测试**，仅被 PS 语法解析覆盖 |
| P2-2 | `pyproject.toml [tool.pytest.ini_options] testpaths` ↔ `run_gates.ps1:147-159` | 测试清单**双源**（当前一致，但需人工同步） | 由 `run_gates.ps1` 从 pyproject 解析，或反之；至少加一条一致性断言测试 |
| P2-3 | `AGENTS.md` C 节"fixed order：build → test → invariant → hotspot" ↔ `run_gates.ps1:193-213` | 默认 Full 只跑 build/test/hotspot(bandit)/lint/format/type，**无 invariant 阶段**（`pip check`/`pip-audit` 仅在 `-RunDependencyAudit` 时插入） | 文档改为"invariant 为可选（`-RunDependencyAudit`）"，或把轻量 invariant 纳入默认 |
| P2-4 | `pyproject.toml [tool.ruff.lint] select = ["E4","E7","E9","F"]` | 未启用 `E501`（行宽）、`S`（bandit 规则集）、`B`（bugbear）、`UP` 等 | 分阶段引入；先 `E501`+`B`，再 `S`（与 bandit 互补） |
| P2-5 | `v2ray_agent_script_update_cron.ps1:92, 158-160` | `set -Eeuo pipefail` 下，只读分支的 `sha256sum /etc/v2ray-agent/install.sh`（文件缺失）与 `verify_runtime`（服务不 active）会让**只读探测**非零退出，与"只读=信息"语义不符 | 只读分支显式 `\|\| true` 或降级为 `echo <fact>=missing`，把判定留给调用方 |
| P2-6 | `google_ipv4_routing.ps1:176` | 后置校验 `grep -Eq 'gemini\|google_ipv4_out\|ForceIPv4\|…' 09_routing.json 98_google_ipv4_outbound.json` —— `grep` 对多文件是"任一命中即成功"，弱于同脚本 check 分支（`:84-90`）的**逐文件**断言 | 改为逐文件断言（与 check 分支一致），否则"只写进一个文件"也会判 PASS |
| P2-7 | `cpa_bwg_guardrails.ps1:1797/2161/2621`、`vasma_kernel_update_cron.ps1:432/812/873`、`adapters.py:118/181` | 备份目录**无保留策略**：`/root/cpa-guardrails-backup-*`、`/root/cpa-oauth-quarantine-backup-*`、`/root/cpa-guardrails-path-backup-*`、`/var/backups/v2ray-agent-*`、`/var/backups/vps-ssh-launcher-*` 只增不减（对比 `cpa-auto-update.sh:135 RETENTION_KEEP_BACKUPS=8` 与 `prune_backups`，其余路径没有等价清理；doctor 也只统计 `$DIR/backups`） | 为每类备份加 `keep N` 轮转；doctor 增加 `/root` 与 `/var/backups` 的计数/占用断言 |
| P2-8 | `system_maintenance_cron.ps1:285-336` | 有 `dpkg --audit` + `apt-get check` 前后校验，但**无 `dpkg --configure -a` 自愈**；apt 调用未加 `-o DPkg::Lock::Timeout` | 中断后续跑场景（apt 被 kill）会卡在 broken 状态；建议 `dpkg --configure -a` 兜底 + 锁等待超时 |
| P2-9 | `cpa-gateway.conf` | nginx 网关配置**无仓内真源**：仓库只有 anchor 断言与就地迁移（`ensure_nginx_directive`），完整配置只存在于主机 | 把 `cpa-gateway.conf` 纳入仓库并作为投影源（与 `cpa_provider_routes.json` 同级），否则主机重建/迁移不可复现 |
| P2-10 | `cpa-admission.py:395-400` | `config.get("early_probe_interval_seconds", …)` 允许省略该键（为单测手搓 config 服务）；生产路径 `load_config` 必设，故非生产缺陷，但削弱了"JSON 不等常量即 raise"的强约束叙事 | 保持现状可接受；若要更严，可在 `load_config` 之外增加"测试构造也必须完整"的断言 |
| P2-11 | `README.md:321-324` ↔ `scripts/remote/cpa-admission.json:14, 51` | **契约漂移（建议立即修，成本近零）**：README 写 `chatgpt-oauth`（`gpt-6-luna`/`gpt-5.6-luna`）——实际 lane 有 **3** 个成员（含 `gpt-6.1-sol`）；写 `deepseek-official`（`deepseek-flash`/`deepseek-v4-pro`）——实际 lane 只有 `deepseek-flash`。README 是操作者心智模型的入口，漂移会误判"哪些名字受共享账号闸门保护" | 把 README 的 lane 成员改为从清单派生的措辞（如"见 `cpa-admission.json` 的 `lanes[].models`"），避免人工同步 |
| P2-12 | `AGENTS.md:17`、`test_scripts.py:2061` | **写入面清单不完整**：两处均称"只有 `-Apply` 或显式 `-RotatePath` 才写入远端 CPA/Nginx"，但 `-QuarantineOAuthLuna`/`-RestoreOAuthLuna`/`-DeactivateOAuthLuna` **同样执行远端写事务**（`cpa_bwg_guardrails.ps1:1934, 2154, 2602` 附近）。测试也因此存在同一盲区 | 补全写入面枚举；测试改为断言"所有写分支都在 switch 白名单内"而非硬编码两个 switch |
| P2-13 | `test_scripts.py`（519/776 ≈ 67% 为 `assertIn`/`assertNotIn`） | **静态文本断言可被等价改写绕过**：如 `assertNotIn("ssh -L", text)` 用 `ssh  -L`（双空格）即可绕过。项目本身已在 P0-1 上暴露了这一类风险（断言文本在、语义失效） | 对安全不变量优先用**行为级**断言（生成产物 → `bash -n`/`bash -c` 执行 → 断言退出码），静态断言只作补充 |
| P2-14 | `test_scripts.py:2038-2046` | PS7 保护只覆盖 12 个 `.ps1` 中的 7 个；缺 `bwg_full_maintenance.ps1`、`cockpit_sidecar_guardrails.ps1`、`cpa_recovery_workflow.ps1`、`v2ray_agent_renewtls_cron.ps1`、`v2ray_agent_script_update_cron.ps1`。**经核实这 5 个当前都有 `#requires -Version 7`** ⇒ 属覆盖缺口，非现存缺陷 | 把清单改为"glob 全部 `scripts/*.ps1`"，消除人工维护 |
| P2-15 | `pyproject.toml [tool.pytest.ini_options]` | 未启用 `--strict-markers` 与 `filterwarnings = error`；另有约 14 处 `skipTest` 依赖 bash/pwsh 是否存在（本机 `bash -l` 启动约 3s）⇒ 本地 Full 门禁可能**静默缩小**而不易察觉 | 启用 `--strict-markers`；对 skip 计数设上限断言或输出汇总 |
| P2-16 | `test_temp_cleanup_guard.py:86` | `assertTrue(removed or recorded)` 近似恒真（两个分支任一成立即过） | 拆成两个明确断言 |
| P2-17 | `docs/runbooks/cpa-gateway.md:41` | 终判 marker 写作 `OBSERVE_OK / OBSERVE_FAILED`，实际输出为 `DOCTOR_CONTRACT_OBSERVE_OK / DOCTOR_CONTRACT_OBSERVE_FAILED`（`cpa_bwg_guardrails.ps1:1767-1773`）。子串匹配仍可命中，属**低危命名漂移** | 统一为实际 marker |

---

## 7. 与官方文档 / 社区最佳实践的差距

| 主题 | 本项目做法 | 社区/官方更优做法 | 判定 |
|---|---|---|---|
| 定时调度 | `/etc/cron.d/vps-launcher-*`（root 用户位） | systemd timer：`OnFailure=`、`RandomizedDelaySec=`（错峰）、`Persistent=true`、`journalctl -u` 结构化日志、`ExecMainStatus` 可读 | **cron.d 是有理由的选择**（vasma `installCronTLS` 会 `sed '/v2ray-agent/d'` 重写 root crontab，2026-09-24 实际发生过）。但 cron.d 缺少失败钩子 ⇒ 与 P1-1 合流：**保留 cron.d，补 `MAILTO=` 或本地 doctor 汇总** |
| apt 无人值守 | 自研 `monthly-maintenance.sh`（update + upgrade --with-new-pkgs + autoremove --purge + autoclean + journal vacuum） | Debian 官方 `unattended-upgrades`（安全源自动、`needrestart` 集成、`/var/run/reboot-required` 语义） | 自研带来"可控窗口"的优势，但**放弃了官方 `needrestart`/自动安全更新**；至少应把 `needrestart -b` 读数纳入日志（当前无） |
| 内核/代理升级 | 经 vasma 菜单管道输入（`printf '16\n1\n1\ny\n' \| vasma`），强耦合于部署版脚本的提示位置 | Xray 官方 install-release 脚本 / 直接 GitHub release + SHA-256 校验 + systemd 单元 | 菜单耦合是"不接管上游"的合理取舍，但**管道输入脆弱**（vasma 改提示文案即失效）——当前已用锚点校验 fail-closed（`:341-360`），可接受；建议把"锚点缺失"也纳入告警（见 P1-1） |
| 反代入口限流 | nginx `limit_req`/`limit_conn` + 429 + `Retry-After` map | 官方一致（`limit_req_status`/`limit_conn_status` 用法正确） | ✅ 实现正确，无差距 |
| fail2ban | `backend=polling` + `logpath … tail` + 回环豁免 | 官方要求：`systemd` 后端读不到 nginx 文件日志，必须 polling/`tail` | ✅ 与官方一致 |
| 容器运维 | `docker compose` + digest pin + log rotation(`max-size:32m`) | 官方推荐 digest pin + log driver 限额 | ✅ 一致 |
| 可观测 | doctor 单点巡检 + 本地只读归因工具 | 结构化日志（journald）+ 指标/告警（Prometheus/健康探针心跳） | ⚠️ 缺口即 P1-1 |
| 静态分析 | ruff(E4/E7/E9/F) + mypy + bandit(3 target) | ruff 默认规则集 + `S`/`B`/`UP`；bandit 覆盖全部生产代码 | ⚠️ 缺口即 P2-1/P2-4 |
| CI | 双 OS 矩阵（win/3.11 + ubuntu/3.13），`actions/checkout@v7`、`setup-python@v7` | 版本在 2026 时间线有效（已核实 v7 存在），矩阵设计合理 | ✅ 无差距 |

---

## 8. 修复路线图

**进度说明**：第一批中的 P0-1、P1-3、P1-4、P1-5 以及 P2-11/P2-12/P2-17 已在当前工作树完成并通过门禁；P1-1、P1-2 和备份轮转仍是下一步重点。

**第一批（本周，P0 + 高收益 P1 + 零成本文档）**
1. **P0-1**：修 `verify_target_xray`/`verify_target_singbox`/`verify_current_*` 为显式短路；补 `bash` 语义级回归测试。**这是唯一必须立刻做的代码修复。**
2. **P1-1**：给四类 wrapper 增加结果状态文件 + doctor 汇总段（复用既有巡检入口，成本最低）。
3. **P1-2**：把 `reboot_required` 升级为 doctor 告警（数据已在 inventory 里）。
4. **P2-11 / P2-12 / P2-17（零成本）**：修正 README 的 admission lane 成员、AGENTS.md/测试的写入面清单、runbook 的 observe marker 命名。

**第二批（两周内）**
5. **P1-3**：适配器超时与全局解耦；远端脚本加进度 marker。
6. **P1-4**：统一退码（75=锁忙）。
7. **P1-5**：`autoremove` 加 `-s` 预演与清单记录。
8. **P2-9**：把 `cpa-gateway.conf` 纳入仓库作为投影源（灾难恢复价值高）。

**第三批（持续）**
9. **P2-1/P2-2/P2-3/P2-4/P2-14**：门禁覆盖与文档一致性收口（合并为一次"门禁加固"提交）。
10. **P2-13**：把安全不变量从静态文本断言迁移到行为级断言（与 P0-1 的回归测试同批）。
11. **P2-7**：备份轮转统一化。
12. 其余 P2 逐条评估。

---

## 9. 已复核并判定为「非缺陷」的疑点（**请勿按此修改**）

审查过程中出现过多条看似严重但经源码复核后被否定的结论，列出以免后续误修：

| 疑点 | 复核结论 | 依据 |
|---|---|---|
| "无人值守 `-AutoApply` 从未真正执行远端写入（只传 `-RunIntegration`）" | **不成立** | `install_vps_maintenance_task.ps1:51-53` 在 `-AutoApply` 时追加 ` -AutoApply`；`vps_maintenance.ps1:128-142` 据此传 `--yes --remote-write --run-integration --unattended` |
| "inventory 解析失败会静默降级为 unknown，plan 不 fail-closed" | **不成立** | `inventory.py:205-212` 非零返回即 `reachable=False`；`planner.py:29-37` 不可达即 `blocked`；`planner.py:81-90` xray 版本为 `unknown/absent/present` 时明确 `blocked` |
| "apply 不传 `--plan-id`，存在旧计划重放风险" | **不成立（非缺陷）** | `vps_maintenance.ps1` 在同一轮先 `plan` 再 `apply`（newest），且 `maintenance_cli.py:420-424` apply 前重新采集 inventory 并比对 fingerprint，漂移即拒绝 |
| "`verify_target_xray` 校验有效（只是写法啰嗦）" | **不成立，即 P0** | 见第 4 节 |
| "`google_ipv4_routing` 后置校验是弱 grep 导致整体失效" | **部分成立** | 复合条件 `if ! A \|\| ! B \|\| ! C` 本身**有效**（非 P0）；仅 P2-6 的多文件 grep 语义偏弱 |
| "`actions/checkout@v7` / `setup-python@v7` 不存在会导致 CI 全挂" | **不成立** | 外部核实：`actions/checkout` v7 在 2026 时间线已存在 |
| "`CLAUDE.md` / `GEMINI.md` 只有 1 行是占位符残留" | **不成立** | 内容为 `@AGENTS.md`，是这两个工具的标准 include 语法 |
| "nginx 限流参数与 README 不一致" | **不成立** | doctor 断言、`-Apply` required 清单、`ensure_nginx_directive` 三处均为 `limit_conn cpa_cc 20`，与 README 一致 |
| "`conftest.py` 全局吞掉临时目录清理异常 = 掩盖错误" | **不成立（有意为之）** | 这是对宿主 `CODEBUDDY_SAFE_DELETE_BULK_GUARD` 导致 pytest 永久挂起（实测 58 分钟）的**有界尽力而为**修法：线程 + 预算，超时则泄漏目录并 detach finalizer，且 session 结束打 warning 让环境故障可见 |
| "`skipTest` 依赖 bash/pwsh 存在 = 测试造假" | **不成立（部分为设计）** | 真实 SSH 集成测试 `skipUnless(VPS_SSH_LAUNCHER_RUN_INTEGRATION=1)` 是明确的 opt-in 设计；但其余基于工具可用性的 skip 会让本地 Full 门禁静默缩小，见 P2-15 |

---

## 10. 未覆盖 / 需人工决策项

1. **未做真实主机只读探测**：本报告全部结论来自仓库源码与文档；`reboot_required` 的实际状态、`/root` 备份目录的真实占用、四类 wrapper 的最近一次结果，均需在授权后以只读方式采集。
2. **`gpt-6.1-sol-input` 与 `gpt-5.6-terra` 的失败窗口**：用户已明确决定保留（`docs/runbooks/cpa-ban-throttle-incident-response.md`），本报告不重复建议。
3. **`cooldown_cap_seconds=900` vs 服务器退避上限 86400s**、**`probe_bytes=262144` 容量窗口盲区**：均为已记录的**接受项**，非缺陷。
4. **`natural_live_accepted` 未宣称**：长期稳定性仍需真实使用窗口观察。
5. **备份保留策略的取舍**：保留多久、是否需要异地，属运维决策，需用户定阈值。

---

## 附：本次审查使用的判据命令（只读，可直接复跑）

```bash
# 门禁（本地）
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1

# 归因与审计（本地，只读）
./.venv/Scripts/python.exe scripts/cpa_failure_triage.py --hours 24
./.venv/Scripts/python.exe scripts/cpa_admission_risk_audit.py --hours 24
./.venv/Scripts/python.exe scripts/cockpit_provider_health.py

# 远端 doctor（授权后，只读）
pwsh -NoProfile -File scripts/cpa_bwg_guardrails.ps1 -Profile bwg
```

---

## 11. 修复执行记录（2026-10-04 当日执行）

按第 8 节路线图连续执行。**未做任何远端写入、未 push**；唯一判据是 `scripts/run_gates.ps1`。

### 11.1 已修复

| 编号 | 改动要点 | 证据 |
|---|---|---|
| **P0-1** | `vasma_kernel_update_cron.ps1` 的 `verify_current_xray` / `verify_target_xray` / `verify_current_singbox` / `verify_target_singbox` 全部改为显式短路（`\|\| return 1`） | 新增行为级回归 `test_vasma_target_verification_rejects_version_and_hash_drift`：**未修复**代码上 xray 3/5、sing-box 4/5 场景失败（全部误判为 ACCEPT）；修复后 10 个子场景全绿。测试用**真实函数体 + stub 依赖**驱动，静态文本断言抓不到这类失效 |
| P1-2 | 月度日志加 `REBOOT_REQUIRED=pending age_days=N` / `=none`；doctor 新增 `==host-hygiene==`（`reboot_required=…`、`reboot_required_advisory=STALE_REBOOT_PENDING`、`maintenance_backups root=… count=…`） | `test_system_maintenance_cron_safety_contracts`、`test_cpa_guardrails_freezes_public_data_plane_contract` |
| P1-3 | 适配器超时与交互 `command_timeout` 解耦（300s idle / 900s hard，policy 更大值仍胜出）；xray/docker 适配器加 `ADAPTER_STAGE=` 标记续期 idle 计时 | `test_maintenance.py` 36 passed |
| P1-4 | `cpa-auto-update.sh`：锁忙 `exit 75`、卫生拒绝 `exit 76`、头部写明退出码契约、卫生步骤改非致命（`\|\| log WARN`） | 该脚本既有测试已期望 76（并行会话已改测试），本次让**实现**与之对齐 |
| P1-5 | `autoremove --purge` 前新增 `-s` 预演，完整删除集（前 200 行）写入日志 | `test_system_maintenance_cron_safety_contracts` |
| P2-1 | `scripts/`（含远端运行时）纳入 compileall + ruff check；bandit 新增 `hotspot:bandit-scripts`（`-ll`，Medium+ 失败）。顺带修掉暴露出的 1×F401、2×mypy、5×Medium（4 处带理由的同行 `# nosec` + 1 处单行常量重构） | `run_gates.ps1` 目标集；`test_run_gates_covers_profiles_without_duplicate_tools` |
| P2-2 | 新增 `test_gate_test_files_match_pytest_testpaths`：`pyproject.testpaths` == `run_gates.$testFiles` == 仓库根 `test_*.py` 全集 | 同上 |
| P2-3 | `AGENTS.md` 的 fixed order 说明改为反映实现（invariant 仅 `-RunDependencyAudit` 时插入） | 文档 |
| P2-5 | 脚本更新只读分支：`install.sh` 读取加存在性判断、`verify_runtime` 失败降级为 `RUNTIME_VERIFY_NONFATAL_READ_ONLY` | `test_v2ray_agent_script_projection_is_pinned_and_backup_first` |
| P2-6 | `google_ipv4_routing.ps1` 后置校验改**逐文件**断言（`POST_APPLY_MARKER_MISSING file=…`） | `test_scripts.py` |
| P2-7 | 内核周更 wrapper 新增 `prune_core_backups`：成功后按 mtime 保留最新 8 份、永不删本次备份；`\|\| true` 保证 `pipefail` 下清理失败不把成功升级判为失败 | 新增 `test_vasma_core_backup_prune_keeps_newest_and_protects_current`（11 份 → 保留最新 8 + 受保护份 + 无关目录） |
| P2-8 | 月度维护新增 `dpkg --configure -a` 一次性自愈（仅仍 dirty 时记失败）；apt 加 `-o DPkg::Lock::Timeout=600` | `test_system_maintenance_cron_safety_contracts` |
| P2-11 | `README.md` admission lane 成员与 `cpa-admission.json` / `ADMISSION_LANE_MODELS` 对齐并注明真源 | 文档 |
| P2-12 | `AGENTS.md` 与 `test_scripts.py` 的"写入面"补齐为五个开关 | `test_cpa_guardrails_freezes_public_data_plane_contract` |
| P2-13（部分） | 新增回归测试均为行为级（真实函数体 + stub） | — |
| P2-14 | PS7 保护改为 glob 全部 `scripts/*.ps1`（7/12 → 12/12） | 同名测试 12 subtests |
| P2-15 | `pyproject.toml` 加 `addopts = ["--strict-markers", "-ra"]` | — |
| P2-16 | 新增 `test_cleanup_does_not_record_a_leak_when_the_delete_completes` 覆盖"完成即不记泄漏"分支 | 4 passed |
| P2-17 | runbook 的 observe 终判 marker 更正为 `DOCTOR_CONTRACT_OBSERVE_OK/FAILED` | 文档 |
| 附带 | 两处真实代码异味：`test_ssh_tool.py` 闭包捕获循环变量（B023）、`automation.py` 加 `raise … from None`（B904） | `ruff --select B023,B904` 通过 |
| 附带 | `conftest.py` 的临时目录清理预算改为可用 `VPS_SSH_LAUNCHER_TEMP_CLEANUP_BUDGET_SECONDS` 覆盖（默认 5.0 不变）。本机 safe-delete shim 让**每个** tempdir 站点都耗满预算，门禁测试步实测 79% 处卡住 ~10 分钟、faulthandler 反复 dump `_safe_rmdir` 栈；降到 0.5s 后同一轮门禁测试步 377s 完成 | `test_temp_cleanup_guard.py` 4 passed；`outputs/gate-20261004-audit-fixes-final.txt` |

### 11.1b 并行会话同期落地的部分（本 worktree 内，与上述改动共同构成最终态）

审查执行期间有**另一个会话在同一个 worktree 上实现同一份审查建议**，其改动已并入工作区并通过同一套门禁：

| 编号 | 对方落地的内容 | 说明 |
|---|---|---|
| P1-1（内核侧） | `vasma_kernel_update_cron.ps1` 只读探针新增 `==kernel-update-log==`，只回显 wrapper 自己的结构化标记（`UNVERIFIED` / `ROLLBACK_*` / `DEFERRED_BUSY`） | 与本次为月度/脚本更新/RenewTLS 加的三段同构 |
| **P1-2（planner 侧）** | `planner.py`：inventory 报 `reboot_required=present` 时，把 `upgrade/present/managed` 动作直接判 `blocked`（附人工重启指引），并新增测试 | 比本报告原建议（仅告警）更严；与本次的 doctor/日志可见性互补 |
| P2-4（部分） | `pyproject.toml` 的 ruff `select` 增加 `B` 规则集 | 本次刻意未做（见 11.2 第 2 条）；对方选择了启用 `B` 而保留 `I`/`UP`，与本次结论一致 |
| P1-4（测试侧） | `test_scripts.py` 把 cpa-auto-update 卫生门期望码从 `1` 改为 `76` | 本次的脚本修复正是与之对齐 |

⚠️ 对方在启用 `B` 后给 `scripts/cpa_stream_acceptance.py` 加了 `zip(self.text_times, self.text_times[1:], strict=True)`。**该写法必然抛 `ValueError`**（两侧长度按设计差 1），导致 `StreamAcceptanceTests` 10 个用例失败。已改为等价的 `zip(self.text_times[:-1], self.text_times[1:], strict=True)`（长度相等，`strict=True` 才成立），测试恢复 11 passed / 16 subtests。

### 11.2 有意不做（附理由，**不是遗漏**）

1. **P2-9（把 `cpa-gateway.conf` 纳入仓库）**：需要线上实际配置才能建立可信快照；本次**无主机访问**，凭猜测写入只会制造假漂移。留待授权只读采集。
2. **P2-4（ruff 扩到 I/B/UP）**：实测成本 56 处（45 可自动修）。其中 **UP017（`datetime.timezone.utc` → `datetime.UTC`）会破坏远端脚本** —— `scripts/remote/*.py` 由 VPS 的 `/usr/bin/python3` 执行，Debian 11 仍是 3.9/3.10，没有 `datetime.UTC`；`I`（导入排序）又会改动逐字节投影的 `scripts/remote/*.py` 制造投影漂移。故本次只取了其中有真实价值的两条（B023/B904）。**并行会话同期已启用 `B` 规则集**（`I`/`UP` 仍关闭，与本条结论一致）；本工作区最终态的 `ruff check`（含 `B`）已全绿。
3. **P2-10**：`early_probe_interval_seconds` 的默认值只服务单测手搓 config，生产路径 `load_config` 必设 ⇒ 保持现状。
4. **`scripts/remote/` 的 `ruff format` 与 mypy**：前者会为纯装饰性改动强制一次投影重应用，后者需给 38 个既有函数补注解。二者在 `run_gates.ps1` 内以注释写明原因。

### 11.3 仍未闭环

- **P1-1 的"主动告警"面**：本次把月度 / 内核 / 脚本更新 / RenewTLS 的**结构化结果提升到只读探针可见**（与 CPA 侧 doctor 已有的 `==timer-result==` 同级），但 `MAILTO` / `OnFailure` / 心跳仍未落地——那需要主机侧 MTA 或 systemd timer 改造，属运维决策。
- **P2-9 / P2-4 其余项**：见 11.2。

