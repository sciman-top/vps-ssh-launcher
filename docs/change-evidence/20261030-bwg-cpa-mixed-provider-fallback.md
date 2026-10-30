# BWG CPA 混合 DeepSeek 回退修复与受控验收（2026-10-30）

## 目标与根因

本次目标是修复裸名 `deepseek-v4.1-flash` 在 WorkBuddy 全池失败后不能继续
尝试独立 OpenCode provider 的问题，同时保留现有 OAuth、API key、WorkBuddy
插件和路由。

根因不是 `credits` 未执行账号级 failover，而是 CPA 宿主的全局
`routing.retry.max-retry-credentials=1` 截断了**同一请求**的混合 provider 遍历。
第一个 WorkBuddy 凭据收到 6004 后，当前执行轮就结束；稍后日志出现的其他
WorkBuddy 账号来自独立请求再次被 `credits` 调度，并不是前一个请求已经完整遍历
三账号后才进入 OpenCode。

只读参考源码 checkout 为
`D:\CODE\external\vps-ssh-launcher-references\repos\CLIProxyAPI`，revision
`90c447d3a8727d115aa7552ec93ce023736ed492`。该版本的
`internal/config/config.go` 将 `0` 定义为不设单轮凭据上限；
`sdk/cliproxy/auth/conductor_execution.go` 的 `executeMixedOnce` 只在上限大于
零时提前停止，随后通过 `pickNextMixed` 按 `tried` 集继续选择 provider。因而
`max-retry-credentials=0` 配合 `request-retry=0` 的准确语义是：耗尽当前轮全部
合格凭据后继续混合 provider，且不再开启第二轮。

三个 WorkBuddy 凭据在短时间内得到相同模型的 6004、且 reset 时间相同，最符合
同一 DeepSeek 模型/上游 realm 的共享限制窗口。现有证据不能证明限制键一定是
出口 IP，也不能证明只按单账号 token 计量；因此没有轮换 IP、UA、设备指纹或账号，
也没有清除冷却或制造 OAuth 失败流量。

## 变更

- 源码提交：`cd688aa`（`fix(cpa): restore mixed deepseek provider fallback`）。
- `routing.retry.max-retry-credentials`：`1` → `0`。
- `routing.retry.request-retry`：保持 `0`。
- WorkBuddy：`scheduler_mode=credits`、`account_max_concurrent=1`、
  `identity_switch_on_429=false`、会话亲和保持不变。
- 未删除、轮换、刷新或替换任何 OAuth/API key；未修改 WorkBuddy plugin 二进制、
  Nginx、ZZ 或其他 VPS。
- 同步修正 `cpa-gateway.md` 风险登记表中残留的 `全局值=1` 旧文案；历史台账中的
  旧值保持原样，作为历史记录。

## 生产投影

按备份优先路径执行：

```text
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -Apply
```

回执：`exit=0`、`GUARDRAILS_APPLIED`、`READY_STATUS=200`。

本次远端配置备份：
`/root/cpa-guardrails-backup-20261030T135004.871512167Z`。
回滚只恢复该目录中本次事务涉及的 `config.yaml`，并按
`docs/runbooks/cpa-manual-rollback.md` 复验；不要用 Git 回滚代替远端恢复，
不要覆盖 auth JSON。

## 投影后 fresh strict doctor

时间：`2026-10-30T13:56:12Z`。

关键读回：

```text
container=running restart=0
request-retry=0
max-retry-credentials=0
workbuddy-scheduler_mode=credits
workbuddy-account_max_concurrent=1
workbuddy-identity_switch_on_429=false
workbuddy-config=OK
cooldown_state=none
MODEL_IDS_UNKNOWN=none
MODEL_CONTEXT_DRIFT=none
projection-drift=全部 MATCH
DOCTOR_CONTRACT_OK
```

这确认了 `repo_verified -> filesystem_projected -> host_loaded` 三层；它不等于
真实 OAuth 生成请求的 `live_accepted`。

## 自然流量观察

生产日志中观察到一条脱敏请求（CPA request id `86490698`）：

```text
13:52:39Z WorkBuddy eb624ce3 ... code 6004 ... reset 22:42:48+08:00
13:52:39Z WorkBuddy intl-0faaff85 ... code 6004
13:52:40Z /v1/chat/completions -> 200，总时长 1.858s
```

这证明 `max=0` 后当前请求没有在首个 6004 处立即返回，最终请求恢复为 HTTP 200。
CPA 的成功日志不记录成功 provider 名称；第三个 WorkBuddy 凭据的异步
`CREATE/WRITE` 事件发生在该 200 之后。因此这条自然流量只能把 OpenCode 命中判为
强推断，不能当作 provider-specific 硬证；它也不能证明三账号健康或证明限流键是
IP。窗口内没有观察到 OpenCode 失败行。

## 模拟/受控验收边界

仓内 `scripts/remote/cpa-acceptance.py` 已将合成 fixture 的
`max-retry-credentials` 同步为 `0`，并保持 `request-retry=0`。该 fixture 使用
私有 mount/network namespace、loopback fake upstream 和合成 key，不读取生产
OAuth；它能证明 CPA 混合执行与冷却协议，不能加载真实 WorkBuddy plugin，也不冒充
WorkBuddy OAuth live 验收。仓内受影响测试和完整门禁已通过：

```text
818 passed, 1 skipped, 403 subtests passed
Bandit: passed
Ruff: passed
ruff format: passed
mypy: passed
```

唯一跳过项是显式 opt-in 的真实 SSH integration。后续若要取得 provider-specific
硬证，只能在同样隔离的 synthetic WorkBuddy-like provider + fake OpenCode upstream
中记录每次 provider 的选择；不得为了验收强制真实账号失败或重复触发 OAuth。

## 结论

本次修复已完成投影并加载。`6004` 共享窗口仍由上游决定，`credits` 和 CPA failover
是可用性降级，不是账号保护或配额隔离。继续观察应采用自然、低频、单会话流量；不得
通过换号、换 IP、清冷却、并发压测或客户端重试来验证限流边界。
