# 自动清理的覆盖面与缺口修复（2026-11-09）

**触发**：用户问"本项目中，vps/CPA 等应当具有自动清理功能？"
**结论**：应当有，且大部分层已有；**但 CPA 备份目录这一层此前不可靠，本轮修复。**

## 1. 清理能力普查（修复前的真实覆盖）

| 层 | 机制 | 触发时机 | 修复前状态 |
|---|---|---|---|
| 系统包/日志 | `system_maintenance_cron.ps1`：`apt autoremove --purge`、`autoclean`、`journalctl --vacuum-time=30d`、logrotate | 月度 cron | ✅ 可靠 |
| 容器 stdout | compose `json-file max-size=32m max-file=3`，doctor 断言 | 持续 | ✅ 可靠 |
| 错误转储 | `prune_error_dumps`（>24h） | 每日（早退之前） | ✅ 可靠 |
| CPA 备份目录 | `prune_backups` / `prune_images` | **仅在真有更新时** | ❌ 见 §2 |

## 2. 缺陷：清理被"无新版本"门禁挡住

`cpa-auto-update.sh` 在 `CUR == TARGET`（无成熟候选）时直接 `exit 0`，
而所有备份清理都在这行之后：

```
prune_error_dumps            ← 唯一在早退之前
if [[ "$CUR" == "$TARGET" ]]; then ... exit 0
prune_backups                ← 永远到不了
prune_manual_backup_families
prune_images
```

后果：清理只在"上游发版 **且** 已过 72h soak"的日子发生。而真正撑大备份目录的是
**手工事务**（`admission-*`、`plugin-*`、`converge-*`），与发版无关。实测：
117 个目录 / 约 800 MiB 从未被任何策略清理过。

这是设计意图（no-update 路径不消费上游配额）与清理职责的冲突：不消费是对的，
但**本地磁盘清理不该被上游门禁连坐**。

## 3. 修复

### 3.1 清理提到每日路径

`prune_backups` 与 `prune_manual_backup_families` 移到早退之前，与
`prune_error_dumps` 同级。安全性论证：二者**只删本地目录**——不调 provider、
不发 generation、不改 config、不重启，所以 2026-09-21 的"no-update 路径不消耗
订阅账号"规则仍然成立。

`prune_images` **刻意留在**成功路径之后：`docker rmi` 属于"已验证更新"的从属
动作，提前删镜像是另一类风险（在新一代被确认可用之前就删掉回滚目标）。

### 3.2 doctor 增加容量告警

`update_backups_advisory=OK|GROWING|LARGE`，阈值 dirs>150/200、KiB>1.5G/2G。
**是告警不是门禁**（不调 `mark_fail`）：迁移后保留大量备份是合法状态，但
"下次再靠人肉发现"不可接受。

当前实测：`update_backups=117 kib=1163108 advisory=OK`。

## 4. 验证

### 4.1 静态顺序断言（活体）

```
prune_backups                  before_no_update_exit=True
prune_manual_backup_families   before_no_update_exit=True
prune_images                   before_no_update_exit=False
```

### 4.2 行为演练：无更新时也清理

9 个 40 天前的 `*-admission-*` fixture + 一个 `*-from-v*`：

```
LOG PRUNE scope=backup_family pattern=*-admission-* kept=4 removed=5 min_age_h=168
fixture_before=9 fixture_after=4
DAILY_CLEANUP_RAN=yes
from_v_untouched=yes
```

### 4.3 `--check` 仍只读

`backups_before=117 backups_after=117`。契约未被破坏。

### 4.4 门禁

1192 passed / 18 skipped，bandit / ruff / format / mypy 全绿；
strict doctor `DOCTOR_CONTRACT_OK`。

## 5. 演练抓到的真实 bug（值得记）

首版 `RETENTION_BACKUP_PATTERNS` 写的是 `'admission-*'`，而真实目录名以 UTC
时间戳开头（`20261108T150211…-admission-…`），`find -name 'admission-*'`
**匹配不到任何目录**。fixture 演练立刻暴露（`before=9 after=9`），改为
`'*-admission-*'` 等锚定形态后通过。

若没有这个演练，代码会"看起来正确、实际不删任何东西"，与修复前的症状完全相同。

## 6. 仓库面

- `scripts/remote/cpa-auto-update.sh`：清理顺序 + `RETENTION_BACKUP_PATTERNS` 声明。
- `scripts/remote/cpa-guardrail-doctor.sh`：容量告警。
- `tests/test_cpa_cleanup_schedule.py`（新，5 项）：清理必须早于早退、
  `prune_images` 必须留在之后、三个 prune 各只调用一次、no-update 路径不发
  generation、doctor 告警不得 mark_fail。
- `tests/test_cpa_updater_apply.py`：`rm -rf` 契约从 1 处放宽到 2 处（各自钉定），
  `prune_images` 的 harness 切片改为按花括号配平，不再切到早退边界。

## 7. 边界

- 本轮未改变任何上游交互、账号姿态、并发或配置。
- `prune_images` 的时机与保护逻辑未变。
- 历史 7 个 `image_gone` 备份仍带 `UNRESTORABLE` 标记，不受本次清理影响。
