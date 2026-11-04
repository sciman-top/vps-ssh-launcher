# BWG DeepSeek 服务顺序 wb-first 恢复 INTL→CN→oc（2026-11-04 08:19 北京）

**性质**: 真实远端写入（config.yaml 定点改 + route manifest 投影 + CPA 重启）· 脱敏留档
**用户授权**: "在bwg VPS CPA上，DeepSeek车道顺序修改为 INTL→CN→oc"
**关联**: 20261102-bwg-cpa-deepseek-order-oc-first.md（本次为其逆操作）；20261101-bwg-deepseek-lane-priority-reserve.md（同形首选态）

## 1. 语义与机制

用户指令：DeepSeek 顺序恢复 = **workbuddy INTL → workbuddy CN → opencode**。
宿主 priority 分桶取最高非空层（sdk/cliproxy/auth/selector.go），oc slot2
`priority: 1 → -1` 即回到兜底层；wb 凭据缺省 priority=0 回顶层。wb 层内
INTL→CN（tier2 前缀 + priority-reclaim）与既有会话亲和语义不变。oc 仅在
wb 整层冷却/失败时承接（免费池 zen go allowance）。

## 2. 远端事务（/run/vps-ssh-launcher-maintenance.lock 内）

- 备份: `/root/cpa-deepseek-order-wb-first-backup-20261104T001939/`
  （config.yaml + cpa_provider_routes.json + pre/post-sha256.txt，0700/0600；
  pre config `3a3011ce…` 同 oc-first 期、manifest `2fbe1dee…`）
- config.yaml: 全文件唯一 `priority:` 行（175）定点 `1 → -1`（sed 锚定缩进
  整行，staged 校验后同 fs 原子替换）
- manifest: 仓内 `scripts/remote/cpa_provider_routes.json`（slot2
  priority=-1，sha `15307348…`）base64 投影，sha256 + JSON 校验后同 fs
  原子替换（0644 root:root）
- **第一次执行 REFUSE（如实记录）**：事务脚本 heredoc 段漏写 `| base64 -d`
  管道，.new 落盘为 base64 文本（sha `bd02e83e…` ≠ 期望），sha256 守卫
  fail-closed 拒收、远端零污染；修管后幂等重跑成功。CRLF 假设经核被否
  （暂存 blob 与工作树文件 sha 相同）。
- 流量窗核查 15s 零 `POST /v1` → `docker restart cli-proxy-api`，
  容器 Up 复验正常。重启副作用：内存冷却/会话亲和清空一次（瞬态）。

## 3. 验收证据

- **wb 承接**：直连 8317 探针 200，TTFB 1.34s，finish=stop，响应 usage 仅
  `credit` 键（车道指纹锚 usage.credit vs usage.opencode）= WB_LANE。
- **零错误**：重启后 6m 窗 `[error ]` 计数 0；codex/zhipu/terra 各车道
  session-affinity 正常重建。
- **live 态**：config 175 行 `priority: -1`；远端 manifest sha 与仓内
  提交 blob 对齐；doctor `deepseek-route-order` 段期望值已随本次提交翻转
  （`deepseek-manifest/live-opencode-priority=-1`、
  `deepseek-provider-order=WORKBUDDY_THEN_OPENCODE`、
  `workbuddy-realm-order=INTL_THEN_CN` 不变），提交后 strict doctor 复跑
  为提交收口门禁。

## 4. 语义影响（runbook 已同步）

- wb-first 恢复 11-01 首选态：INTL 每日额度先耗（tier2 设计），CN 积分
  其后承接，oc 免费池仅整层冷却时兜底。CN 积分消耗在 oc 健康期**恢复**
（与 oc-first 期相反）。
- 既有 oc 亲和会话按设计继续 replay，随会话自然 churn 迁回 wb；新会话/
  无会话请求立即走新裁决。
- priority 变更热加载不生效（运行学锚，与 11-02 一致），按需重启。

## 5. 回滚

维护锁内将备份 config.yaml/manifest 复制回（核对 pre-sha256），git revert
本次提交（doctor 期望值随 revert 回 OPENCODE_THEN_WORKBUDDY / priority=1），
重启 CPA。Git 回滚不能替代远端恢复。

## 6. 并行会话边界

执行同窗工作树存在另一会话在途遥测改动（first_text_ms 三件套，含
cpa-guardrail-doctor.sh）；本次提交经选择性暂存（git apply --cached）仅
纳入 deepseek 顺序相关 hunks，在途字节未纳入、未投影。
