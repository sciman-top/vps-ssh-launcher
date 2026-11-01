# BWG CPA DeepSeek 路由优先级窄投影（2026-11-01）

## 目标与范围

用户已授权按推荐方案连续修复。目标是让新增请求的 DeepSeek provider 软顺序保持为
WorkBuddy（CN 文件名序、再 INTL 文件名序）→ OpenCode；WorkBuddy 的
`credits` 粘性选择和已有 session affinity 仍然有效。CN/INTL 不是带 realm 参数的
硬选择器。

本次远端写集只有：

```text
/opt/cliproxyapi/cpa_provider_routes.json
```

未修改 `config.yaml`、`auth/`、OAuth/API key、WorkBuddy 并发、RPM、重试、冷却、
CPA 容器或 admission 服务；未发起生成请求。

## 依据与仓库变更

- 路由真源在 `scripts/remote/cpa_provider_routes.json` 的 OpenCode slot 2 固定
  `priority: -1`；WorkBuddy provider 保持默认 `priority=0`。
- `cpa-guardrail-apply.sh` 现在校验 provider priority 的范围并在声明存在时投影它。
- strict doctor 增加 DeepSeek 路由顺序检查，输出脱敏的 CN/INTL 计数与顺序。
- 上述仓库变更已在 commit `9270a19`（`fix(cpa): preserve deepseek workbuddy primary route`）。

## 远端事务

投影前 strict doctor 观察到 route manifest 仍是旧哈希，但 live OpenCode 已为
`priority=-1`；WorkBuddy 文件计数为 `cn:4,intl:7,other:0`，顺序为
`CN_THEN_INTL`。在共享 `/run/vps-ssh-launcher-maintenance.lock` 内完成以下事务：

- 旧文件 SHA-256：
  `7e665c4e0aad6384fbb18d3b0b807690a82cd22b59bc79a469e96c88be513e9b`
- 先备份到：
  `/root/cpa-provider-routes-backup-20261101T110906Z-f777a972e4cb.json`
- 新文件 SHA-256：
  `7343e2bec807654e4679696d0f8acc07a996771e6b50bb89fb46fb524d1712fa`
- 新文件权限为 `root:root 0644`；备份权限为 `root:root 0600`。
- 使用同目录临时文件校验 JSON 和 SHA-256 后原子替换；未重启 CPA。

## 验收证据

路由专项只读读回退出 0：

```text
route_manifest_sha256=7343e2bec807654e4679696d0f8acc07a996771e6b50bb89fb46fb524d1712fa
manifest_opencode_priority=[-1]
live_opencode_priority=[-1]
workbuddy_realm_counts=cn:4,intl:7,other:0
workbuddy_realm_order=CN_THEN_INTL
deepseek_provider_order=WORKBUDDY_THEN_OPENCODE
cpa_restart_count=0
ROUTE_ONLY_PROOF_EXIT=0
```

同次 strict doctor 的 DeepSeek 段已通过：

```text
deepseek-manifest-opencode-priority=-1
deepseek-live-opencode-priority=-1
workbuddy-realm-order=CN_THEN_INTL
deepseek-provider-order=WORKBUDDY_THEN_OPENCODE
drift=cpa_provider_routes.json MATCH
```

全量 strict doctor 仍报告两个与本次路由无关的既有投影漂移：当前 HEAD 的并发批次
`f2d7ba0` 已更新 `cpa-admission.py` 及其 integrity pin，但远端仍是上一代 admission
版本，因此输出 `admission-integrity=FAIL`、`projection-drift-cpa-admission.py=FAIL`
和 `projection-drift-cpa-admission.sha256=FAIL`。本次按并发写集保护规则没有投影或回滚
该批次；待其单独完成 admission 事务后，再恢复全量 `DOCTOR_CONTRACT_OK`。

## 回滚

如需回滚，仅在取得维护锁后把上述备份复制回
`/opt/cliproxyapi/cpa_provider_routes.json`，恢复 `root:root 0644`，再执行路由专项
读回和 strict doctor。Git 回滚不能替代远端恢复；不得恢复旧 OAuth/API key 文件，也不
应借此修改 WorkBuddy 并发或重试策略。
