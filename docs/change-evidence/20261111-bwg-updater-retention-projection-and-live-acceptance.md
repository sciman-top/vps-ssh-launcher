# 2026-11-11 bwg CPA updater 保留重构投影与受控实弹验收

## 范围

- 将 `scripts/remote/cpa-auto-update.sh` 的保留重构链（`03471da`→`b9bf028`，含并行会话的 newest-8 镜像保护集、每日常规清理路径、prune_backups NUL 快照加固与本会话的实机模板修复）经 `scripts/cpa_bwg_guardrails.ps1 -Apply` 投影到 bwg `/opt/cliproxyapi/auto-update.sh`，写后即验 `DOCTOR_CONTRACT_OK`（14 项投影全 MATCH）。
- 受控实弹两轮：`bash /opt/cliproxyapi/auto-update.sh --apply`（前提 `--check` 确认 `target==current==v8.0.61`，v8.0.64 在 soak，不会误触发真升级）。
- 古董 tagged 镜像人工裁决（11-10 挂账收口）。

## 实弹发现与修复

- 第一轮 `--apply`：清理链按预期执行（orphan 0、backups kept=8、family min-age 守住、build_cache 因空闲跳过），但 `prune_images` 按设计 DEFER（`inventory_failed`）。
- 根因（bash 桩无法复现的实机 docker 语义，修复=commit `b9bf028`）：
  1. `docker image inspect` 无 `.Repository` 字段 → 改用 `{{json .RepoTags}}/{{json .RepoDigests}}` 亲缘校验；
  2. `.Created` 是 RFC3339 字符串、无 `.Unix` 方法 → GNU `date -u -d` 转 epoch；
  3. `RootFS.Layers` 摘要带 `sha256:` 前缀、原正则假设裸 hex → 正则修正；
  4. 内层诊断日志走 stdout 会污染命令替换的候选清单 → 改 stderr + LOG。
- 第二轮 `--apply`：全链成功。`PRUNE scope=images candidates=4 retained=0 removed=4 freed_bytes=1204417986`，镜像总数 17→13，磁盘 61%→59%。回收的 4 个均为 eceasy `<none>` digest 尾巴（无一属于 newest-8 保护面）。tagged 历史（v8.0.28–59）按候选筛选设计不在自动回收范围。

## 古董 tagged 人工裁决（2026-11-11 执行）

保护面实证先行：newest-8 备份中 2 个记录了 image-id——`fork-image-compose-20261109` 记录 `sha256:fc714b…`（=v8.0.61）、`from-v8.0.59-to-v8.0.61-wb0989` 记录 `sha256:40dda8…`（=v8.0.59，仍在保护窗口）。据此修正原推荐（v8.0.59 从删除名单撤下）。

- 删除（8）：`eceasy/cli-proxy-api` v8.0.28/31/34/35/53/55/58 + `golang:1.26-bookworm`（derivation RFC pin 的是 `golang:1.26.10-bookworm`，旧版未 pin）。全部可从上游 registry 重拉，可逆。
- 保留（5）：`cli-proxy-api-fork:v8.0.61-gemini-raw`（生产）、`eceasy:v8.0.59` + `v8.0.61`（newest-8 保护面记录 + fork 时代回退锚）、`golang:1.26.10-bookworm`（RFC pin 重建输入）、`bwg-local/cpa:v8.0.40-pr6784-sim-v2`（本地构建不可重拉、不在挂账清单，不碰）。
- `/root`：workbuddy/wbkimi/wbpin 小文件均为 KB 级历史事务备份，保留；RFC 明确"源码缓存目录"是重建事务合法输入，不清理。
- 结果：镜像 13→5，磁盘 59%→54%（20G used / 18G free）。容器 `Up 4 hours` 全程未受扰动。

## 验收判据（automation-9acf1459 期望逐项）

- `DOCTOR_CONTRACT_OK`；`runtime_binary_source=image`、`runtime_binary_hash=MATCH`（==runtime-binary== 段）。
- `docker ps`：`cli-proxy-api-fork:v8.0.61-gemini-raw Up 4 hours`（fork 终态形态）。
- `auto-update.sh --check`：`current=v8.0.61 target=v8.0.61`，`SOAK_PENDING newest=v8.0.64 remaining_hours≈65`（fork tag 解析正常、无成熟候选）。
- `access.api-keys count=1`（单键形态；仅打印计数）。
- `/healthz`：status=ok，telemetry 正常出数（models series 有样本）。
- 24h 日志：`invalid_grant=0`、`TOS_VIOLATION=0`、`panic=0`；`401/403=54` 全部为 nginx 容器网关地址对 `/v1/models` 的轮询 401（~27 分钟一次，远低于 fail2ban 阈值）——属 desktop/cockpit 旧凭据轮询，为「cockpit UI 更新键」既有挂账的延续信号，需用户在 UI 侧更新凭据，非远端可修。

## 回滚

- 投影回滚：`git checkout` 到投影前提交重跑 `-Apply`（guardrails 自带备份策略 keep_8）。
- 镜像回滚：被删 8 个均可 `docker pull` 上游对应 tag 重拉；运行容器与保留镜像全程未触碰。
- 无配置/凭据/备份目录变更。

## 后续

- 每日 timer 幂等重跑清理链（candidates 已清零 → removed=0）；下次月维（12-01）DISK_ROOT_AFTER 将以 ~54% 为基线。
- desktop/cockpit 凭据轮询 401：等用户在 cockpit UI 更新键后自然归零，不另立工单。
