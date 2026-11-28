# Cockpit WaitCapSimulation holder 判定修复回执（2026-11-28）

**性质**：本机验收工具修复 + 受控模拟重跑，不触远端、不碰 OAuth/凭据。

## 背景

2026-11-17 A5 台账记录了已装 Cockpit sidecar 1.4.0（sha `2AA9FCC5…`）与
policy pin 1.3.67（sha `7977EAF9…`）的版本漂移；绕过 sha 用 live manifest 模型
`deepseek-v4-flash` 重跑 `cockpit_gate_wait_cap_check.py` 时，waiter 在 45.0s
正确收到 429（cap 生效），但 3 个 holder 在 200s 观察窗内无结果，旧判定按 FAIL
处理（结论：脚本对 1.4.0 行为的判定口径问题，不是闸门失效）。

## 根因

`fire()` 的 socket timeout 是 `WAIT_OBSERVE_LIMIT_S`（200s），而观察窗在 waiter
返回后即收口：1.4.x sidecar 对被闸门持有的上游连接不回写任何字节，holder 线程
保持阻塞恰恰是“槽位被占住”的表现。当 waiter 拿到 429 时，3 个槽位被占满是逻辑
必然——否则第 4 个请求会被转发到永不响应的 loopback stub，waiter 也不会有 429。
旧判定把这一正常占用签名误报为 `holders produced no result` FAIL。

## 修复（提交 `43e315f`）

`scripts/cockpit_gate_wait_cap_check.py`：holder 无结果且 waiter 已收 429 →
记 `holders_occupied=blocked_no_response`（诊断字段，不判失败）；仅当 holder
缺失**且** waiter 没有 429 时才报 error（那才是“占用不成立”的失败形态）。
诊断抓取（log tail）在 holder 缺失分支已先行修复（上轮入库）。

## 受控重跑（2026-11-28，本机 loopback stub + scratch sidecar）

```text
candidate sha256 = 2AA9FCC5…（live 1.4.0，经 --candidate 显式传入）
model = deepseek-v4-flash（live manifest 真实模型，不碰 OAuth）
holders = [None, None, None]  → holders_occupied=blocked_no_response
waiter  : status=429 elapsed=45.0s（cap 45s 生效）
ACCEPTANCE_PASS
```

验证：`pytest tests/test_cockpit_replay_and_wait_cap.py` 11 passed + 11 subtests；
ruff/format/mypy 绿。

## 边界

- 本回执只升级 WaitCapSimulation 的判定证据层级：本机模拟
  `controlled simulation = PASS`。它不改变 policy pin 1.3.67 → 1.4.0 的重钉
  决定（仍待用户确认升级方案）；`-Mode Verify` 对已装 1.4.0 的 sha FAIL 语义
  保持不变。
- 不对 OAuth lane 发任何探针；waiter 429 来自 loopback scratch sidecar 的
  闸门本体，不是上游账号限流。
