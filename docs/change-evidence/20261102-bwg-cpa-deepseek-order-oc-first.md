# BWG DeepSeek 服务顺序 oc-first（2026-11-02 22:58 北京）

**性质**: 真实远端写入（config.yaml 定点改 + route manifest 投影 + CPA 重启）· 脱敏留档
**用户授权**: "在bwg VPS CPA上，DeepSeek车道顺序修改为 oc→INTL→CN"
**关联**: 20261102-bwg-wb-realm-order-intl-first-restore.md（wb 层内 INTL→CN 与 priority-reclaim 补丁）

## 1. 语义与机制

用户指令：DeepSeek 顺序 = **oc → workbuddy INTL → workbuddy CN**。宿主
priority 分桶取最高非空层且高值=高层（参考源码锚
`sdk/cliproxy/auth/selector.go` `availableAuthsFromPriorityBuckets`/
`conductor_selection.go`），故 oc slot2 `priority: -1 → 1` 即升顶层；
wb 层内 INTL→CN（tier2 前缀）与 priority-reclaim 语义不变。oc 全冷却时
wb 层接管；这是 provider 层顺序，与 wb 层内顺序正交。

## 2. 远端事务（/run/vps-ssh-launcher-maintenance.lock 内）

- 备份: `/root/cpa-deepseek-order-oc-first-backup-20261102T145826Z/`
  （config.yaml + cpa_provider_routes.json 副本 + pre-sha256.txt，0700；
  pre sha config `3a3011ce…` / manifest `c5b8f68a…`）
- config.yaml: 全文件唯一 `priority:` 行（165）定点 `-1 → 1`（sed 锚定
  缩进整行，改后 grep 复核）
- manifest: 仓内 `scripts/remote/cpa_provider_routes.json`（slot2
  priority=1）base64 投影，JSON 校验后同 fs 原子替换（0644 root:root）
- **热加载实测未生效**：改后 30s 无 reload 日志、探针仍由 wb 承接 →
  锁内 `docker restart cli-proxy-api`（重启前 12s 窗 1 条请求）后生效。
  运行学锚：**宿主 priority 变更不要依赖 config 热加载，按需重启**。

## 3. 验收证据

- **oc 承接**：探针 200，响应 usage 带 `opencode.billing_source: go`
  计费指纹（admission 的 `lane=workbuddy-oauth` 是模型→车道分类，不代表
  实际上游——车道指纹锚 usage.opencode vs usage.credit）。首探针 16
  completion token 全部为 reasoning（finish=length，content 空）属探针
  形状非错误。
- **wb 层静默**：重启（14:59:55Z）后 wb auth 零写入、零 conductor 失败
  （oc 健康时整层不接流量）。
- **doctor**: 路由段 `deepseek-manifest-opencode-priority=1`、
  `deepseek-live-opencode-priority=1`、`workbuddy-realm-order=INTL_THEN_CN`、
  `deepseek-provider-order=OPENCODE_THEN_WORKBUDDY`、
  `admission-integrity=OK`；提交前终判 `DOCTOR_CONTRACT_FAILED` 仅因
  projection-drift 锚 HEAD blob（工作树未提交的预期态），提交 99313d8 后
  复跑 `DOCTOR_CONTRACT_OK` 零 FAIL/DRIFT。

## 4. 语义影响（runbook 已同步）

- CN 积分消耗在 oc 健康期**停止**（wb 整层静默）；wb 只在 oc 冷却/失败时
  承接（INTL→CN）。oc 侧计费为免费池（zen go allowance）。
- 记忆中"oc priority -1 单层调度设计勿改"旧口径作废（2026-11-02 用户指令
  推翻）；上游 #71 的 priority-reclaim 提案不受影响（wb 层内语义）。
- 重启副作用：内存冷却/会话亲和清空一次（与既往重启同，瞬态）。

## 5. 回滚

维护锁内将备份 config.yaml/manifest 复制回（核对 pre-sha256），doctor 断言
revert 至 99313d8~1（priority=-1 / WORKBUDDY_THEN_OPENCODE），重启 CPA。
Git 回滚不能替代远端恢复。
