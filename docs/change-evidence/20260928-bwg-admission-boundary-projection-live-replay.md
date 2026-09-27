# BWG admission 边界修复投影与受控实战验收（2026-09-28）

## 范围与授权

- 代码基线：`4e9ca73d8ae046137e91730cfe64a1b2ceca783b`（修复 VPS 维护与 admission 边界）。
- 目标：仅处理 `bwg` 的 admission/维护边界修复；`zz` 未访问、未读取、未修改。
- 用户明确授权 BWG-only 投影和受控 live replay，并允许本次 OAuth/Usage 消耗；本次未轮换凭据、未轮换公网随机路径、未消费原始 Usage queue。
- SSH 使用严格 host-key checking；没有执行主机重启、apt、内核升级或其它系统维护写入。
- 证据不包含密码、私钥、token、订阅地址、随机公网路径、公共 IP、请求体或响应正文。

## 投影前基线

- fresh strict doctor 返回非零的唯一原因是：`projection-drift-cpa-admission.py=FAIL`。
- 其它受管文件、Nginx/CPA/Admission 服务、端口、策略、模型目录和权限检查均通过。
- `request-retry=0`、OAuth quarantine=`none`、冷却状态为空；未消费 Usage queue。

## Backup-first 投影

- 执行入口：`scripts/cpa_bwg_guardrails.ps1 -Profile bwg -Apply`。
- 结果：`GUARDRAILS_APPLIED`，所有受管文件均输出 `PROJECTION_HASH_VERIFIED`。
- 本次远端回滚备份：`/root/cpa-guardrails-backup-20260927T225634.945397433Z`。
- admission 重载期间出现一次预期的短暂连接重置和 8318 不可达，随后 `HEALTH_OK`、`READY_STATUS=200`；事务未回滚。
- apply 后 `cpa-admission.py` 的远端 hash 与本地 HEAD blob 一致。

## Host loaded 复验

- fresh strict doctor 返回 `DOCTOR_CONTRACT_OK`。
- `drift=cpa-admission.py MATCH`，其余受管文件继续 `MATCH`。
- `admission-service=enabled-active`、`admission-health=OK`、8318 仅监听 loopback、8443 公网随机路径契约保持。
- `request-retry=0`、`save-cooldown-status=false`、模型目录无未知 ID、Nginx syntax 和 CPA/Admission syntax 全部通过。

## 受控 live replay

回放从 BWG 本机经公网 8443 TLS 随机路径进入 Nginx，再经过 admission 8318 到 CPA；每个请求只发送一次，不自动重试。

### 三并发完整流

- 发送 3 个同一 OAuth lane 的 `gpt-6-luna` streaming chat 请求，模拟 2–3 个并发 Desktop 会话的共享 lane 压力。
- 3/3 返回 HTTP `200`，SSE 均包含数据帧、结束标志和 finish 信息；无 `Retry-After`、无流内 error。
- 响应大小均为 776 bytes；单次 TTFB/总耗时分别为 `3045/3229 ms`、`1870/2026 ms`、`788/968 ms`。
- 观测到 `max_inflight=1`、`max_pending=2`，符合当前 OAuth 单飞与 bounded queue 契约。
- 回放结束：`inflight=0`、`pending=0`、`failure_streak=0`、`cooldown_remaining=0`。

### 客户端 RST 断连

- 另发 1 个较长 streaming 请求，确认 admission 已持有 OAuth lane 后发送 RST-style client close。
- 返回头阶段为 HTTP `200`，确认 `seen_inflight=true`。
- 断连后 `2880 ms` 内释放 `inflight/pending`；最终 `retired_readers=0`，累计退休 reader 计数为 `1`，说明断连收尾可观测且没有 resident reader 泄漏。
- 断连后 `failure_streak=0`、`cooldown_remaining=0`，未因客户端断连错误打开上游容量冷却。

## 分层判定

| 层级 | 结果 | 依据 |
|---|---|---|
| `repo_verified` | PASS | 本地 commit；Full gate `226 passed, 1 skipped, 236 subtests passed`；静态检查、测试和 `git diff --check` 通过 |
| `filesystem_projected` | PASS | backup-first apply、备份目录、受管文件 hash 验证和事务结果 |
| `host_loaded` | PASS | post-apply fresh strict doctor=`DOCTOR_CONTRACT_OK`，projection drift 全部 MATCH |
| `controlled_live_replay` | PASS | 3 并发完整 SSE + 1 次客户端 RST 断连，lane 状态和释放时间均符合契约 |
| `natural_live_accepted` | NOT CLAIMED | 受控模拟/实战探针不等同于用户真实 Desktop 自然会话；需要用户在正常业务窗口单独观察 |

## 回滚与残余边界

- 远端回滚只使用本次事务备份目录，恢复后重新执行同一 strict doctor；Git 回滚不能替代远端恢复。
- 本次没有消费原始 Usage queue；OAuth provider generation 已按授权发生 4 个有界请求，未自动重试。
- provider 长期配额、账号风控、自然 Desktop 稳定性和长期模型质量不由本次受控 replay 证明。
- Git 提交尚未推送到 `origin/main`；这不影响本次 BWG 主机已加载的本地 HEAD 投影，但远端仓库同步仍是独立收口事项。
