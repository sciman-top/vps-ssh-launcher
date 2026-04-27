# CLAUDE.md — vps-ssh-launcher（Claude 项目级）
**项目**: vps-ssh-launcher
**承接来源**: `GlobalUser/CLAUDE.md v9.44`
**适用范围**: 项目级（仓库根）
**最后更新**: 2026-04-27

## 1. 阅读指引
- 本文件只写本仓事实、门禁命令、证据位置和回滚入口，不重写全局 `R/E` 语义。
- 固定结构：`1 / A / B / C / D`。
- 裁决链：`运行事实/代码 > 项目级文件 > 全局文件 > 临时上下文`。
- 自包含约束：执行规则以本文件正文为准，不依赖外部子文档或治理脚本作为前置条件。
- 渐进披露边界：根文件保留本仓边界、门禁、阻断、证据和回滚；长主机排障、真实 SSH runbook 和历史证据放入 `docs/` 或 `.claude/rules/`。

## A. 项目基线
### A.1 事实边界
- 本仓是 Python SSH/VPS 启动辅助工具，核心入口为 `run.cmd`、`connect.cmd`、`connect.ps1`、`ssh_tool.py`、`auto_install.py`。
- `target.json` 是本机敏感配置，默认位于用户配置目录；仓库只保留 `target.example.json`。
- 明文密码、私钥路径和 root 直登是受支持的本机使用模式，但不得提交真实凭据或把凭据写入日志/证据。
- 真实 SSH 集成测试默认跳过；只有显式 `VPS_SSH_LAUNCHER_RUN_INTEGRATION=1` 或 `scripts/run_gates.ps1 -RunIntegration` 才能运行。

### A.2 执行锚点
- 每次改动先声明：当前落点 -> 目标归宿 -> 验证方式。
- 默认中文沟通、中文解释、中文汇报；代码标识符、命令、日志、报错、SSH/Windows 字段保留英文原文。
- 全局规则给风险、语言、N/A 和门禁语义；本文件给 vps-ssh-launcher 的入口边界、凭据安全、真实命令、证据与回滚入口。
- 项目规则只保留本仓不可由代码/CI自动推断且会改变执行、风险或验收的事实；长流程下沉到子文档或工具专属规则。
- 小步闭环，优先根因修复；止血补丁必须标明回收时点。

### A.3 N/A 分类与字段
- `platform_na`：平台能力缺失、命令不存在、Windows 进程环境异常或真实 SSH 环境不可用。
- `gate_na`：门禁步骤客观不可执行（含 dev 依赖缺失、集成凭据缺失、纯文档/注释/排版改动）。
- 两类 N/A 均必须记录：`reason`、`alternative_verification`、`evidence_link`、`expires_at`。
- N/A 不得改变门禁顺序：`build -> test -> contract/invariant -> hotspot`。

### A.4 触发式澄清协议
- 默认执行：`direct_fix`（先修复、后验证）。
- 触发条件：同一 `issue_id` 连续失败达到阈值（默认 `2`），或凭据/真实主机风险与期望持续冲突。
- 一次最多 3 个澄清问题；确认后恢复 `direct_fix` 并清零失败计数。
- 留痕字段：`issue_id`、`attempt_count`、`clarification_mode`、`clarification_questions`、`clarification_answers`。

## B. Claude 平台差异
- 用户规则：`~/.claude/CLAUDE.md`；项目规则：仓库根 `CLAUDE.md` 或 `.claude/CLAUDE.md`。
- 个人项目偏好用 gitignored `CLAUDE.local.md` 或 `@~/.claude/...` import；多 worktree 共享偏好时优先 import；路径级差异用 `.claude/rules/`，不要假定 `CLAUDE.override.md` 存在。
- 临时排障规则必须记录清理点，结论后删除或恢复并复测。
- 诊断优先执行 `claude --version`、`claude --help`；交互场景可用 `/memory` 查加载链，非交互不可用时按 `platform_na` 记录。
- auto memory / local memory 只作辅助上下文；与代码、项目规则或证据冲突时以仓库事实为准。
- Claude 权限/安全或重复验证要求应固化到 `.claude/settings*.json` permissions、hooks、CI 或本仓门禁；不要只依赖自然语言规则。
- 需要禁止读取敏感文件、限制工具或固定沙箱时，优先用 `.claude/settings*.json` 的 `permissions.deny` / `sandbox`；不要把硬安全边界只写成提醒。
- 替代命令仅用于补证据，不得改变本仓门禁顺序与阻断语义。

## C. 项目差异（领域与技术）
### C.1 模块职责
- `connect.ps1` / `connect.cmd` / `run.cmd`：用户启动入口、Python 解析和依赖安装。
- `ssh_tool.py`：配置解析、SSH 连接、并行执行和退出码映射。
- `auto_install.py`：依赖安装辅助。
- `scripts/run_gates.ps1`：统一本地门禁入口，含 build/test/contract/invariant/hotspot/lint/typecheck。
- `test_*.py`：单元、wrapper 和默认跳过的真实 SSH 集成测试。
- `docs/security-waivers.md`：Bandit 等安全豁免和复审依据。

### C.2 门禁命令与顺序（硬门禁）
- build：`python -m compileall -q ssh_tool.py auto_install.py test_ssh_tool.py test_auto_install.py test_scripts.py test_integration_real_ssh.py`
- test：`python -m pytest -q`
- contract/invariant：`python -m unittest -q`
- hotspot/full：`pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1`
- fixed order：`build -> test -> contract/invariant -> hotspot`
- `scripts/run_gates.ps1` 默认不跑真实 SSH；需要真实联通回归时显式传入 `-RunIntegration` 和 integration 参数。

### C.3 失败分流与阻断
- build 失败：阻断，先修语法、导入和 wrapper 路径。
- test 失败：阻断，先修回归失败再继续。
- contract/invariant 失败：高风险阻断，禁止发布或修改连接行为。
- hotspot/full 失败：阻断；如因缺少隔离 Python/dev 依赖失败，先按 README 建 `.venv`，无法执行时按 `gate_na` 落证。
- 真实 SSH 失败必须先区分：仓库逻辑、凭据/配置、远端主机状态、宿主 Windows 网络/进程环境。
- 代理内核更新、`vasma` 菜单驱动、`xray`/`sing-box` stop-start、月度维护实跑等会影响联网能力的高风险行为，必须逐台执行；完成一台后先复验服务、配置和端口，并暂停等待用户确认正常后，才能执行第二台。禁止并行触发两台 VPS 的代理内核更新或重启类动作。

### C.4 证据与回滚
- 证据目录：`docs/change-evidence/`（不存在则创建）。
- 最低字段：规则 ID、风险等级、执行命令、关键输出、是否触发真实 SSH、回滚动作。
- 回滚优先使用 git 历史；涉及真实 SSH 或主机连接时必须记录跳过/执行条件、目标 profile 和恢复入口，不记录真实密码/私钥。

### C.5 CI 与本地入口
- 用户入口以 `run.cmd` / `connect.cmd` 为准。
- 本地完整门禁以 `scripts/run_gates.ps1` 为准；`pip check` / `pip-audit` 需要隔离 `.venv` 或显式 `VPS_SSH_LAUNCHER_PYTHON`。
- Windows 环境异常先检查 `ComSpec`、`SystemRoot`、`WINDIR`、`APPDATA`、`LOCALAPPDATA`、`PROGRAMDATA`，不要先把 `WinError 10106` 归因到仓库逻辑。
- 手动触发 `/etc/v2ray-agent/auto_update_xray.sh` 或 `/etc/v2ray-agent/auto_update_singbox.sh` 时，远端命令必须只执行 wrapper 本身；后续 `xray` / `sing-box` 配置检查必须另开第二条 SSH 命令，避免被 `vasma` 内部 `pgrep -f` 误匹配为残留进程。

### C.6 Git 提交与推送边界
- `整理提交全部` 的“全部”仅指：`本次任务相关 + 应被版本管理 + 通过 .gitignore 的文件`。
- 默认不纳入：`target.json`、真实密码/私钥、临时日志、`.venv/`、缓存和运行态探针。
- `push` 仅推送既有 commit 历史；文件筛选必须在 `git add/commit` 前完成。

## D. 维护校验清单
- 仅落地本仓事实，不复述全局规则正文。
- 与全局职责互补，不重叠、不缺失。
- 协同链完整：`规则 -> 落点 -> 命令 -> 证据 -> 回滚`。
- `Global Rule -> Repo Action`：
  - `R6`: 本仓门禁命令是硬门禁；quick/fast 只能作为已声明的日常反馈切片，交付前仍按 full gate 或固定顺序收口。
  - `R8`: 证据与回滚字段是最小留痕；缺字段必须按 N/A 口径说明。
  - `E4`: hotspot/full 复核 SSH 连接、凭据处理、Windows wrapper、真实 SSH integration 默认跳过边界。
  - `E5`: Python、Paramiko、系统 SSH、Bandit/pip-audit 等依赖变化必须记录供应链/工具基线；新增依赖前先说明必要性。
  - `E6`: `target.json` schema、profile 字段、凭据字段或退出码语义变化必须记录兼容性、迁移和回滚。
- 子文档只承载细节，不替代根文件中的硬门禁和项目事实。
- 三文件同构约束：`A/C/D` 必须语义一致，仅 `B` 允许平台差异。
