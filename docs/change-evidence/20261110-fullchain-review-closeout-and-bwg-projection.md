# 2026-11-10 全面审查修复批收口 + bwg guardrails 重投影

## 变更依据

8 切片并行全面审查（HEAD=8baf742 起点）发现 1×P1（recovery fence 无 HUP 陷阱）、9×P2、14×P3；
修复批 **8a6f036**（37 文件，+549/-90）已推，Full gates 绿（build/test/bandit/ruff/format/mypy，
pytest 1228 passed）。仓库侧证据见该提交信息（ACCEPTANCE_RESULT=PASS）。

## 真实远端写入（bwg，单机）

- `scripts/cpa_bwg_guardrails.ps1 -Profile bwg -Apply`：重投影清单 convergence。
  触发原因：8a6f036 修改了投影源 `scripts/remote/cpa-auto-update.sh`（prune_manual_backup_families
  补 BACKUP_ROOT 守卫），doctor ==projection-drift== 首验
  `drift=auto-update.sh MISMATCH want=4a34b463… got=db87078d…`（预期陈旧，非缺陷）。
  Apply 结果：`GUARDRAILS_APPLIED`，EXIT=0，备份剪枝 `PRUNE removed=1 policy=keep_8`。
  其余 13 项投影源字节相同幂等（cpa-admission.py 等均未触碰内容）。
- 无服务重启（admission 字节未变）；无其他主机写入。

## 受控实战验收（全部只读探针，判据行）

- doctor 复验（bwg）：`DOCTOR_CONTRACT_OK`，==projection-drift== 13/13 `MATCH`
  （含本次收敛的 auto-update.sh）；新 doctor 代码实弹全流程跑通
  （==container== image 末段截断渲染正常、fail2ban 探针临时文件归位 DIAGNOSTIC_DIR）。
- vasma kernel wrapper read（bwg，xray/sing-box 双车道）：EXIT=0/0，
  pin 命中 skip reinstall，伪装面 `decoy-applied`、obfs lane `listening=yes port=8444`。
- v2ray renewtls read（bwg）：EXIT=0，`RUNTIME_VERIFY_OK`
  （非致命 verify_runtime 改动在位，本轮投影新鲜未触发 fallback）。
- 伪装车道漂移门：`HARDENING_DRIFT=NONE` 双机（bwg `proxy_hardening_drift.ps1` EXIT=0；
  zz `run_proxy_drift_task.ps1` EXIT=0）。
- maintenance inventory（bwg，`--run-integration` 只读）：EXIT=0，`reachable=True`，
  37 facts（含 xray/sing-box config sha256 键），原子 0600 输出写实弹（Windows 按设计跳过 chmod）。
- zz subscription read（zz）：EXIT=0，`zz_subscription=applied`（match→noop 路径；
  drift 分支的 read-only write_status 移除逻辑随 payload 就位，下次 drift 实际触发时生效）。

## 惰性部署面（无需本轮写入）

车道 payload（hysteria2/cdn/stale_firewall/camouflage/merged_sub/zz_sub 的 bash 载荷）与
cron 族 payload 属流式或下次 -Apply 生成时生效：远端当前跑旧 payload 为既有生产行为，无害；
pytest 沙箱已对新 payload 渲染执行（1228 绿）。下次对应车道 -Apply / cron 重建自然收敛。

## 回滚入口

- bwg auto-update.sh：`/var/backups` keep_8 内 apply 前备份（本次 `PRUNE removed=1` 后仍保留
  本轮备份）；或 `git show 8a6f036~1:scripts/remote/cpa-auto-update.sh` 重投影。
- 仓库侧：`git revert 8a6f036`（本地切片整体回退，远端按上述备份恢复）。

## 挂账（勿当新发现）

- `cpa-admission.py` 每请求 7×json.loads 优化：字节级受管投影，须 reviewed deploy+pin 同步，单独切片。
- doctor ban-signals `auth_file=` 哈希化：契约钉死归因面，设计裁决。
- 待验证 3 条与设计面裁决项见提交 8a6f036 报告/记忆 fullchain-review-20261110-round。
