# 2026-10-07 BWG 维护 wrapper 投影批次收敛证据

## 目标与范围

- 目标：收敛 2026-10-07 全链审查遗留的 Medium「五 wrapper heartbeat
  盲区 / 投影滞后」——现役远端 wrapper 与仓库 HEAD 渲染对齐，并实证
  status 写入链路终态。用户当轮显式授权远端写入。
- 范围：**仅 `bwg`** 的四个 cron wrapper（monthly / renewtls /
  script-update / kernel-xray）。CPA 投影、google_ipv4、`zz` 零改动；
  内核 pin 原样传递（v26.3.27，10/7 定案零漂移，不上 prerelease）。
- google_ipv4 经查证其 `-Apply` 只重跑已部署的受 pin 委托脚本、不重投影
  脚本本体，且探针全绿（marker/config-ok/双栈出口正常），无收敛价值，
  本批次跳过。

## 前置基线（只读）

- CPA 默认严格 doctor：`DOCTOR_CONTRACT_OK`，`==projection-drift==`
  9×MATCH（10/5 的 CPA 投影收敛保持，无需 CPA `-Apply`）。
- 状态目录已活跃：五个 status 文件（cpa-update / kernel-xray /
  monthly / renewtls / v2ray-agent-update）全部 `result=success` 且带
  `finished_at`——10/7 早间审查记录的「现役旧投影不写 status」已被
  10/5-10/6 晚的并行 apply 批次实际收敛，renewtls 当日 01:30 定时运行
  成功亦证明 `exec` 吞 EXIT trap 的问题在主机已修复。
- 主机 pin 三组自洽：xray `v26.3.27`/`8255dd93…`（二进制实测一致）、
  vasma `v3.5.25`/`fca0ad30…`、script-update `SOURCE_REF=5c5e2b7`。

## 变更明细（逐个 `-Apply`，各自带时间戳备份）

| wrapper | 备份目录 | 结果 |
| --- | --- | --- |
| renewtls | `…renewtls-deploy.R7kVmy` | `RENEWTLS_LOCKED_PROJECTED`；新 sha `8dd72b07…` 与部署前逐字节一致（零漂移，等价确认） |
| monthly | apply 内建备份 | 部署后 sha `2f34bab5…` 与部署前逐字节一致（零漂移）；cron.d/`logrotate-config-ok` 复验通过 |
| script-update | `…script-update-deploy.3gESUB` | `UPDATER_PROJECTED`；`UPDATER_SHA256=666d1352…` 与部署前逐字节一致（零漂移） |
| kernel-xray | `…v2ray-agent-maint.Q6Mgez` | **唯一真漂移项，已收敛**：10/5 部署版 → 10/7 14:54 新投影（sha `f1c51d58…`），pin 原样投影（`TARGET_VERSION=v26.3.27`、`EXPECTED_SHA256=8255dd93…`、`EXPECTED_VASMA_SHA256=fca0ad30…`），`syntax-ok`，PRUNE 清理 1 个过期备份 |

结论：四个 wrapper 中三个为字节级等价确认（说明 10/5-10/6 的部署已含
全部行为修复，`36150f3` 封装重构未改渲染内容）；kernel-xray 携带
10/5 深审后的渲染差异完成真实收敛。

## 后置复验（批次后即时）

- CPA doctor：`DOCTOR_CONTRACT_OK`，`==projection-drift==` 9×MATCH，
  `oauth_days_left=7`——批次未扰动 CPA 面。
- 服务：xray / nginx / cron 全部 `active`，xray 配置 `run -test` 通过。
- 状态目录五个 status 文件齐全；cron.d 四条目（月度 `0 14 1 * *`、
  renewtls `30 1 * * *`、script-update `40 14 * * 5`、kernel
  `20 14 * * 5`）与仓库默认渲染一致。

## 边界与回滚

- 本批次不升级任何内核/脚本版本（pin 未动），不触碰 CPA 容器、Nginx
  与 OAuth 凭据，不涉及 `zz`。
- 回滚入口：各 apply 备份目录（见上表）+ 重新运行对应 wrapper
  `-Apply` 投影回旧 pin 即可；kernel wrapper 旧版备份在
  `/var/backups/v2ray-agent-maint.Q6Mgez`。
- `zz` 侧遗留（kernel-sing-box wrapper / script-update / google_ipv4
  探针）按逐台纪律，待用户确认 bwg 联网正常后另行处理。
