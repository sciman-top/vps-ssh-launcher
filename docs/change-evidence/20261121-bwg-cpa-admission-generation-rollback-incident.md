# 2026-11-21 BWG CPA admission 代际失配崩溃循环事故与恢复记录

**性质**：真实远端写入事故 + 应急恢复记录（`docs/change-evidence/` 常驻口径）。与
[20261121-bwg-zcode-start-plan-integration.md](20261121-bwg-zcode-start-plan-integration.md) 互补：该文档记录成功投影与 start-plan 交付；本文记录其间两起 `-Apply` 失败/回滚引发的 admission 崩溃循环（约 8 分钟 + 约 7 分钟网关 admission 中断）与两次恢复动作。含一个仍待修复的流程缺陷（见 §5）。

## 1. 背景与并发快照

- 本日多代理并行：`01a1fa40`（深度硬化收口，接手方=本记录作者）、`14-31-13`（zcode-start-plan 集成）、全链审查会话（feb9dd6 校验器假阳性修复并补收口推送）。
- 三个投影事务竞争同一 BWG 与同一 worktree；flock 仅串行化远端事务本体，不防本地 payload 竞争与 HEAD 锚点漂移。clean-source 门禁两次正确拒绝了对方的投影源 WIP（门禁只检查投影源路径集合，untracked 探针产物不触发）。

## 2. `-Apply` 事务时间线（UTC）

| 时刻 | 事务 | 结果 |
| --- | --- | --- |
| 06:42:03 | 孤儿事务 #1（01a1fa40 的会话主体已死，子进程存活） | semantic-policy 过（`cpa-policy-test.log=POLICY_OK`）、daemon-reload 步未达（无日志），失败回滚；`docker restart` 06:43:18、admission 重启 06:43:19；`ROLLBACK_VERIFIED`（输出随死管道丢失） |
| 07:17:35 | 孤儿事务 #2（start-plan 线程） | 写入与校验推进到 timer 生效读回；`systemctl show "$UPDATER_TIMER_UNIT"` 用路径形式在 systemd 255 返回空串 → `ROLLBACK updater_timer_effective_readback delay=`（空）；回滚文件恢复完成但断言段报 `ROLLBACK_FAILED` |
| 08:05:20 | 事务 #3（start-plan 线程，含 26f37ac basename 回退修复） | `GUARDRAILS_APPLIED` 退出码 0（08:05:42），投影完整落地；备份 `/root/cpa-guardrails-backup-20261121T080520.729565978Z` |

**根因（26f37ac 已修）**：`systemctl show /etc/.../xxx.timer -p X --value`（路径形式）在 systemd 255 上对单元属性返回空（rc=0），名字形式返回 `5min`。本地测试 stub 对路径形式有应答，故单测全绿未拦截。判别手筋：同属性路径形式空、名字形式有值。

## 3. 崩溃循环 #1（07:18–07:26 UTC，约 8 分钟）

事务 #2 回滚恢复 bootstrap 为旧代，但该线程在事务前的手工预换把新 `cpa-admission.py`+pin"固化"进了备份 → 回滚后主机态 = 旧 bootstrap + 旧 cpa_admission_bootstrap 校验链 + 新 admission.py。

启动失败指纹（journal）：`cpa-admission-bootstrap startup_failed error=OSError detail=admission generation mismatch`，restart 计数至 208；8318 拒连，经 admission 的公网 lane 中断。

**机制**：bootstrap 校验 `sha256(cpa-admission.py) == cpa_request_guard.SOURCE_SHA256`；guard 内嵌的是上次投影时的 admission 哈希（067c6568），与新旋转的 53bb40c5 失配。

**恢复 A（07:26:13）**：从 `/root/cpa-guardrails-backup-20261121T064203` 恢复 `cpa-admission.py` + `/etc/vps-ssh-launcher/cpa-admission.sha256`（同代际旧值），`systemctl stop/start`；此后零重启，healthz=200。副作用：pin 回到旧代 → 后续 `-Apply` 被 `REFUSE admission_pin_transition_pending` 正确拦截（设计内）。

## 4. 崩溃循环 #2（07:49–07:56 UTC，约 7 分钟）

start-plan 线程用正式原子旋转把 `cpa-admission.py`+pin 转到 53bb40c5，但 **request guard 未随转**（旧 guard 内嵌 067c6568）→ 同款失配复发。

**恢复 B（07:55）**：部署 HEAD 投影字节的 `cpa_request_guard.py`（HEAD 模板 `__SOURCE_SHA256__` 替换为 53bb40c5，sha256 `502f40570bbaacc51ee246bdcb100376d0201f0a0f9f639f7a0648d39186686e`，与事务 #3 将写入的字节逐一致）→ admission active、healthz=200。此后 admission 三件套（admission.py+pin+guard）= 完整新代际且互洽，**后续事务失败回滚恢复的备份即一致状态，崩溃循环结构性根源消除**。

## 5. 流程缺陷与待修项（未在本日修复）

- **原子旋转 runbook 缺口**：`cpa-admission.py`+pin 的旋转必须与 request guard 重投影同批执行（guard 内嵌 admission 代际哈希）。本次两起事故同源。建议：把"旋转三件套"（admission.py、pin、guard 重投影）固化为单一脚本/事务，或在 `restore_all` 中检测 guard 与 admission 代际失配并强制对齐备份内的 guard。
- `restore_all` 的 `ROLLBACK_FAILED` 只报布尔，不报首个失败步骤；两起事故中排障均依赖备份目录内日志存在性推断。建议回滚失败时输出首个 `rollback_failed=1` 的步骤名。
- 本地测试 stub 的 systemctl 对路径形式有应答（单测未拦截真机 systemd 255 行为）；夹具宜用真机字节（同日 feb9dd6 的教训一致）。

## 6. 投影后验收（本记录作者的独立复核）

- strict doctor（只读）：`DOCTOR_CONTRACT_OK` 退出码 0；projection-drift 零 MISMATCH；`admission-integrity=OK`；`runtime_binary_hash=MATCH`；`semantic-policy=OK`。
- 非 OAuth 受控验收（zhipu/glm API-key lane，无 OAuth 生成探针、无轮换、无清冷却）：
  - cache-canary（`glm-5.3-flash`，同前缀两次）：`canary1_http=200` / `canary2_http=200`，`prompt_in=1127 cached=1088`（≈96.6% 命中，缓存通路在新投影下端到端工作）。
  - 经 `127.0.0.1:8318` 单发流式 SSE（`glm-5.3-flash`，`stream:true`）：`SSE_CONTROLLED_LIVE status=200 header_ms=35 total_ms=1167 events=23 has_done=True finish=length`。

## 7. Git

- 投影锚定 HEAD=26f37ac（6 深度硬化提交 b2419b5..348612c + 91983f3 + feb9dd6 + 26f37ac，均已推 origin/main）。
- 本记录为 docs-only 切片：`git diff --check` + `check_docs.py`（Focused 口径）。
