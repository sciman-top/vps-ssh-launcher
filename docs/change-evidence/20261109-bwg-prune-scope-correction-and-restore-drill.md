# 更正与补充：prune 保护缺口的真实范围 + 恢复演练（2026-11-09）

**性质**：只读验证 + 台账更正
**触发**：用户第二次追问"是否全部完成"。这次专门去验证我上一轮**只声明未证明**的两件事，
结果之一是**我上一轮的表述有误，本文件更正**。

## 1. 必须更正的错误表述

上一轮我写过：

> 旧逻辑只保护 1 个镜像，新逻辑保护 54 个。

这个对比是**误导性的**，虽然两个数字本身都来自真实测量：

- `protected_old=1` 是脚本口径错误——它只统计"无 `image-id` 记录、需回退解析
  compose 引用"的备份，而 84 个备份里 81 个已有记录，所以分母被剔空；
- `protected_new=54` 是"所有记录到的镜像 id 去重"，与前者不是同一口径相减。

**真实范围**（重新测量）：

```
backup_compose_refs pinned_by_digest=81 unpinned=3
  unpinned x2   cli-proxy-api-fork:v8.0.61-gemini-raw
  unpinned x1   eceasy/cli-proxy-api:v7.2.154
cpa_images_on_disk=55  old_logic_would_delete=1
```

结论修正为：**81/84 的备份引用自带 digest，可变 tag 从未影响它们**；真正暴露在
"tag 漂移"风险下的是 **3 个无 digest 的引用**，而 fork 镜像恰好是其中之一
（2 个 `fork-image-relabel` 备份）。所以缺陷是真的，但影响面比我上一轮暗示的
**窄得多**，且 3 个都已被 recorder 覆盖。

写错的原因：我把"两种口径的两个数"并排放进一句话当因果论证，没有先对齐分母。
这与本仓 2026-11-08 那次"cache 分母"错误是同一类——**数字对，比较错**。

## 2. 已证明的两件事

### 2.1 prune 保护的正确性

```
recorded_total=77 still_present=77 gone=0
```

77 个记录了镜像 id 的备份，其镜像**全部仍在磁盘上**——说明记录与保护是自洽的，
没有"记录了但已被删"的悬空引用。（另有 7 个标记 `image_gone` 的旧集不计入。）

### 2.2 回滚集真的能恢复（此前只声明"complete"，未实际拉起）

对一个 `bind_mount` 型"完整"回滚集做**隔离恢复演练**（无凭据、无默认网络
namespace，不触碰生产）：

```
set=20261108T161841.256612745Z-converge-v8.0.61-wb0989-gemini-raw
set_binary=330b7a55…（v8.0.59 + Gemini 补丁）
RESTORE_PROCESS_ALIVE=yes
CLIProxyAPI Version: v8.0.59, Commit: 5d495e1, BuiltAt: 2026-11-08T04:40:04Z
plugin registered plugin_id=workbuddy … version=0.9.89
RESTORE_FAULT=no
```

即：从该备份取出的二进制**确实能启动、能加载当前插件、无 panic**。这补上了
"complete 只表示文件齐全"与"complete 表示能跑"之间的缺口。

注意它打印的是 `v8.0.59` —— 这正是**回滚集应有的行为**：该备份捕获的是换装前
的运行时，回滚就该回到 v8.0.59。若它打印 v8.0.61，反而说明备份被污染。

## 3. 投影状态（本次复查，全部 MATCH）

```
drift cpa-auto-update.sh MATCH
drift cpa-health.py    MATCH
drift cpa_policy.py    MATCH
drift cpa-admission.py MATCH
```

新 updater（含 `record_image_ids`）已在宿主生效：`grep -c image_id_file` = 6，
真机探针 `IMAGE_ID_RECORDING_OK`。

## 4. 结论：现在缺的到底是什么

| 项 | 状态 |
|---|---|
| 投影（4 个受控文件） | MATCH，无漂移 |
| updater 只读候选 | `current=v8.0.61 target=v8.0.61` |
| 运行态 | `786669dd…`，二进制 = 台账 `31f2f9d9…` |
| 8443 车道四车道 | 200 / terminal / PONG |
| 443 车道四车道 | 200 / terminal / PONG |
| canary 拒绝语义 | fork→77 / bind-mount→77 / 纯官方→0 |
| 回滚集可恢复性 | complete=77 / incomplete=0 / image_gone=7（已标记） |
| **回滚集实拉演练** | **本轮补做，PASS** |
| 私有 registry | **不做**，理由与替代方案见 [registry 决策台账](20261109-bwg-registry-decision-and-rollback-identity.md) |

**未做的只剩一项且需你决定**：是否接受"不做 registry"这个判断。若你要求必须做，
请给 registry 主机与 TLS 证书，我按台账 §5 的最小形态执行，并同步改
`firewall_baseline.ps1` 的 allowlist（属受控面变更，需单独评审）。

## 5. 边界

- 本轮零生产写入：只读查询 + 隔离 namespace 内的恢复演练。
- 恢复演练的 fixture 目录已随 trap 清理。
- 更正段落不删除原错误表述，保留可追溯性。
