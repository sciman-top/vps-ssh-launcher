# BWG WorkBuddy 插件优先级回收补丁部署（2026-11-02 13:38 北京）

**性质**: 真实远端写入（生产插件 .so 换装 + CPA 容器重启）· 脱敏留档
**用户授权**: "授权按你的推荐/建议连续自动修复和优化，直至完成所有任务"
**关联**: 20261102-bwg-wb-realm-order-intl-first-restore.md（顺序翻转）、20261101-bwg-wb-realm-order-intl-first.md §4（粘性棘轮的发现与诊断）

## 1. 问题与设计

INTL-first 顺序落地产后，当日诊断证实粘性棘轮缺口：wb 插件 `pickActiveAuth`
（0.9.82 与 0.9.84 语义一致）粘住当前账户直到其 exhausted/disabled——**6004
模型级冷却过期不回收**。全池级联节流（11-02 11:22/11:55 两轮，44 秒内 7 INTL
全中）把服务压到 CN tier2 后，INTL 恢复也不会自动回切，直到 CN 积分耗尽。
诊断期间用 bounce（锁内改名即走即回）清座两次验证了杠杆，但那只是瞬态手段。

修复设计（最小改动）：`pickActiveAuth` 每次选户取**排序最前的 live 候选**；
全 exhausted/disabled 的兜底分支（保留 current，否则取首）不变。语义变化：
面板钉选（使用中）让位于任何更早的 live 账户（优先级>钉选），仍控制
all-exhausted 兜底与面板显示。上游无现成旋钮（0.9.84 active_auth.go 无
reclaim 逻辑、无 config 键），故本地补丁。

## 2. 构建与保真验证

- 构建树 `/root/cpa-workbuddy-0984-20261102-build`（VERSION 0.9.84，含 10-19
  intl system-first 守卫补丁 payload.go:698/886）。
- 树内产物 `workbuddy.so`（12175696B）与生产部署件 **SHA256 相等**
  （`bbf57c0a…`）= 构建链保真、树携带生产全部补丁。
- 补丁：`active_auth.go` 单点替换（删除粘性短路，保留 next 选择与兜底），
  pristine 由逆替换重建，unified diff 存仓
  `scripts/patches/workbuddy-v0.9.84-priority-reclaim.patch`。
- 构建：`golang:1.26-bookworm` 容器 `CGO_ENABLED=1 go build -buildmode=c-shared
  -ldflags "-X main.version=0.9.84"`；产物 12175960B，gofmt 仅报树内固有
  chat_error.go（非本补丁文件）。
- 同源重建不可字节复现（Go buildid，预期）——以源码 diff 审查 + 注册验证 +
  行为验收替代 sha 证明。

## 3. 部署事务（/run/vps-ssh-launcher-maintenance.lock 内）

- 备份: `/root/cpa-plugin-priority-reclaim-backup-20261102T053814Z/`
  （workbuddy.so.pre-patch = bbf57c0a…，pre-sha256.txt，0700）
- 原子替换: 同 fs `mv workbuddy.so.new → workbuddy.so`（0755 root:root，
  新 sha `2665af74…`；不 cp 覆盖运行中映射的共享对象）
- `docker restart cli-proxy-api`（重启窗约数秒，重启前 12s 窗 2 条请求）
- 注册实证: `plugin unloaded … + plugin registered plugin_id=workbuddy
  version=0.9.84`（13:38:16）

## 4. 验收证据

- **服务健康**: 单发探针 pong（8318 回环）；全量 strict doctor
  `DOCTOR_CONTRACT_OK`（`workbuddy-realm-order=INTL_THEN_CN`、
  `admission-integrity=OK`）。
- **重启副作用即收益**: 容器重启清空插件内存冷却态后，首探针即由排序最前的
  `intl-0faaff85` 服务（mtime 05:39:34 = 探针时刻）——证明"上游 6004 已过期、
  仅插件内存冷却残留"的场景现在自然回归 INTL-first。
- **合成回收验收（rename 杠杆三段）**——本补丁核心断言，旧代码第三步必然失败:
  1. P1（pong）: `intl-0faaff85`（排序最前）服务，mtime 05:40:40；
  2. P2（0faaff85 改名移走，pong）: 次选链服务（1610d8fd→b915f393→ca64800b
     多写入系 account_max_concurrent=1 并发溢出，符合语义），mtime 05:41:19-33；
  3. P3（0faaff85 改名回归，pong）: **05:41:53 立即回收到 intl-0faaff85**——
     旧粘性语义会停留在次选账户。
- 全程零失败、watcher 对 4 次 rename 逐条增量处理。

## 5. 行为变化与运维语义（runbook 同步）

- 顺序语义现在是**每请求优先级真语义**：文件名序=服务序，冷却/恢复自动跟随；
  之前的 bounce 清座杠杆对"冷却棘轮"不再需要（保留用于其他诊断场景）。
- 面板钉选降级为"显示+all-exhausted 兜底"，不再能压过更高优先级的 live 账户。
- 账户切换频率上限=冷却窗口边界（与级联期间固有切换相同），稳态（首个账户
  健康）零切换。
- 已知残留: 上游 6004 级联期间服务仍会落到 CN tier2（设计内溢出）；补丁保证
  恢复即回切。

## 6. 回滚

维护锁内 `mv` 备份件回 `/opt/cliproxyapi/plugins/workbuddy.so`（核对
bbf57c0a…），`docker restart cli-proxy-api`，并 revert 本提交移除仓内补丁
文件。Git 回滚不能替代远端恢复。

## 7. 上游化跟踪（同日用户授权）

提案已提交上游：https://github.com/mmqz/cpa-multi-plugins/issues/71
（sticky 棘轮最小复现 + opt-in priority 模式提案；正文脱敏，无 IP/账户/
主机信息；PR 待维护者认可方向后再提交，届时按其首选形态重构为 opt-in
旋钮并退役本地补丁——patch 头部退役条款已关联该 issue）。
