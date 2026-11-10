# Launcher 维护状态与诊断审查（2026-11-10）

**性质**：代码/诊断脚本审查与本地修复 · 未执行真实 SSH、远端写入、部署或
凭据/路由变更
**用户授权**："全面深度审查"；"授权按你的推荐/建议连续自动修复和优化，直至完成所有任务。"
**范围**：`vps-maint` 的状态持久化与无人值守尝试记账、本地锁恢复、日常 BWG
观察链、Cockpit/CPA 只读诊断的边界，以及相关当前文档和 CI 触发面。

## 1. 已修复的独立失败模式

| 优先级 | 问题 | 修复与验证边界 |
|---|---|---|
| P1 | 无人值守适配器返回 busy 后，调用方清理路径可能把已持久化的 `applied` 动作再次保存，导致后续执行被当成未决而永久拒绝 | 清理时从数据库读取并恢复该精确动作；尝试计数退款和计划状态更新在同一 SQLite 事务完成。回归测试覆盖模拟本地中断后的持久恢复 |
| P2 | stale lock 恢复先判死进程、再无条件删除，owner 被 successor 替换时可能删掉新一代锁 | owner 增加随机 fencing token；恢复将 owner 私有重命名并复核 token，不清除未见过的 successor。旧版无 token 的死 owner 转人工审查 |
| P2 | plan 输出发布已存在 inventory 时仍可能先写入状态库，失败后留下成为 newest 的计划 | 两个输出目标在存储前统一检查；发布失败仅在计划仍为 pristine 时撤回。不能补偿已取得执行证据的计划 |
| P2 | BWG 日常验收未传客户端实际使用的公网名与 REALITY 端口 | `vps_maintenance.ps1` 对 bwg 明确传入 `-PublicName fq.sciman.top -RealityPublicPort 443`；测试同时保留 drift/acceptance 的 fail-closed 路径 |
| P2 | non-OAuth replay 声称只打印脱敏前缀，实际输出原始 `BODY_HEAD` | 删除正文输出，只保留 HTTP 状态、正文长度、完成标记和固定形状；配置 token 读取不再保留整份配置文本 |
| P3 | request-log audit 在数据库缺失/不可读时退出 0；自然流量审计的本地 SSH 进程缺少可靠墙钟上限；不可读 sidecar manifest 及仅绑定其他端口的 manifest 都可能被静默跳过 | 分别改为非零诊断退出、915 秒外层墙钟上限、显式 `sidecar-routability-incomplete` finding；新增捕获输出的分块传输契约测试 |

## 2. 文档与门禁一致性

- README 中主机密钥说明与实际默认值统一：CLI 与 `connect.ps1` 默认严格校验，
  TOFU 必须显式 opt in。
- 不再在 README 固化硬化车道数量；当前入口和 lane 语义以 proxy runbook 为真源。
- 将"所有输出零泄露"收窄为实际可保障的已知敏感形态冲刷边界，并明确自由文本
  仍需操作者控制。
- 代理 runbook 记录日常 BWG 公网 REALITY 检查，以及六个基础节点追加备用 SNI
  节点后形成七个节点。
- Documentation workflow 的 push/PR 触发包含 `scripts/check_docs.py` 与
  `tests/test_ci_meta.py`，避免修改检查器本身时不运行其契约测试。

## 3. 证据与未覆盖边界

仓库验证以最终 full gate、`git diff --check` 与 `scripts/check_docs.py` 的当次
receipt 为准。受控测试只证明本地状态转换、PowerShell 参数接线与诊断输出契约；
没有证明真实 VPS 可达、远端锁行为、公网/运营商网络质量、上下游账号安全或自然
客户端验收，也不表示两机已应用本次代码。

## 4. 回滚

本地代码与文档按本提交撤销即可。本次没有远端备份或反向脚本需要运行；不得把 Git
回滚误报为远端恢复。
