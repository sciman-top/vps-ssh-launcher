# BWG WorkBuddy 11128 冷却与账号池故障切换

- 当前授权：保留全部账号和 API key 渠道；用户明确要求自动发现失败、冷却失败账号并切换可用账号，新增账号不得受固定 2 个的上限限制。
- 事故：ChatGPT Desktop high-demand 提示，对应 0faaff85 的 WorkBuddy deepseek-v4.1-flash 调用于北京时间 07:22:35、07:30:57 返回 11128。该业务码不证明账号封禁，也不能证明与账号状态无关。
- 根因：11128 未记入插件冷却表；selected 模式阻止自动切换；max-retry-credentials=1 只允许一个凭据尝试。
- 修复：固定 v0.8.68 源码、既有 INTL/selected 补丁之上追加 account-failover 补丁；11128 原始及流错误路径对失败凭据写入 5 分钟本地熔断，其他凭据不受影响；全池冷却、禁用、耗尽时拒绝；冷却自然到期。
- 运行配置：scheduler_mode=credits；max-retry-credentials=0；request-retry=0。CPA v8.0.33 conductor_execution.go 以 tried 集合排除已尝试凭据；0 取消固定凭据计数上限，候选池耗尽或成功即停止。新增账号无需修改计数。流输出后不承诺透明重放。
- 5 分钟为本地抑制连续失败的策略，不是官方 reset；插件冷却表为进程内状态，配置重载保留，进程重启丢失；本轮未改成持久化。
- 不触碰明确人工风险 hold、不轮换或删除凭据、不增加并发、不改模型或公网入口。
- 构建：隔离 Go 1.26 CGO、1 CPU/768 MiB、无生产凭据挂载。第三账号接手、账号间隔离、流错误识别、自然到期、全池失败及原调度/stream-head 定向测试通过，exit 0。
- 构建树：/root/cpa-workbuddy-failover-build-v3-20261019/src；补丁来源及 MIT 沿用既有 selected-account 补丁。
- 新插件 SHA256：1238117f394af6dc556be182c79a2536ce268b373c25d89d8deb43d2d4dc2d7e。
- 新 policy LF SHA256：327ee289869ee42877588d0283324be10f2a2934e18d82224dfc95b77c655db8。
- 部署：先预演、备份、维护锁、空闲检查、实际容器依赖检查；原子替换 so，保留 config inode，重启 CPA 加载插件后恢复 admission。CPA 镜像仍 v8.0.33，compose 与全部凭据字节一致。
- 备份/回滚：/root/cpa-workbuddy-failover-backup-20261019T000304Z/rollback.py；--dry-run 已通过。实际回滚需空闲窗口，会恢复旧 so/config/policy 并重启 CPA；不会删除凭据或清除风险状态。
- 初次 doctor 报 policy 字节差异：先规范 Windows CRLF 为 LF，随后提交新策略使 HEAD 哈希与实际投影对齐。最终严格 doctor 为 DOCTOR_CONTRACT_OK / exit 0，policy MATCH，四条 OAuth lane 无人工 hold。
- 本地完整测试初次 670 passed、1 failed、1 skipped、317 subtests；失败是旧投影仅接受 max-retry-credentials=1。修正投影兼容及写入值后，相关 policy/updater 39 passed、37 subtests，guardrails/doctor 48 passed、77 subtests；其余未受影响结果复用。Bandit、ruff、format、mypy 通过。
- 真实有界验收：通过 admission 向 deepseek-v4.1-flash 发出一次 system-first 流请求，调用端零重试；HTTP 200、收到正文、完成标记、无错误帧。该请求没有自然触发失败，生产失败→另账号成功仍待自然事件；不能用这次成功证明永久无风控。
- 补充实际 CPA 二进制隔离验收：从生产容器只复制 v8.0.33 二进制，在独立 network/mount namespace 运行，只有虚拟 API keys 和本地模拟上游，无生产配置、凭据或外网。同步/流式均按 fixture-1→fixture-2→fixture-3 各尝试一次，前二失败、第三返回完整 200；后续请求仅调用 fixture-3，证明 host 冷却生效。全失败场景尝试三账号后返回 500 并停止；POOL_ACCEPTANCE_RESULT=PASS / exit 0。这补齐 host 池遍历及停止证明，与插件 11128 定向测试共同覆盖两层；未把模拟 API-key 账号称为真实 WorkBuddy OAuth 切换。
- 外置参考仓只读；另一个任务的 runbook/landing/risk-controls 与 outputs/apply-failure-cooldown-20261019.py 未吸收。
