# 2026-11-02 双机 kernel wrapper 菜单门投影（受控实战验收）

## 变更内容

第十二轮审查（见 commit `ab6bdbb`）为 `vasma_kernel_update_cron.ps1` 新增
`MENU_UPDATE_ALLOWED` 门：zz xray 车道（旁路加装核心，vasma 不感知）硬编码 `0`，
wrapper 在菜单驱动在即（upstream latest == pin 且运行二进制不一致）时 exit 10
UNVERIFIED 并保留已验证安装；bwg 保持 `1` 行为不变。本批次将新模板受控投影到
双机，使该守卫在生产生效。

- 执行命令形态：`vasma_kernel_update_cron.ps1 -Profile {zz,bwg} -Kernel xray
  -Apply`，三 pin（Version / InstalledSha256 / VasmaSha256）全部来自当次只读探针
  fresh read，未引用历史值。
- 逐台节奏：zz 全项复验通过 → 用户确认 zz 联网正常 → bwg 同套收敛（AGENTS 逐台边界）。

## 验收判据（双机全 PASS）

| 判据 | zz | bwg |
|---|---|---|
| dry-run 预检 exit 0 | ✓ | ✓ |
| `-Apply` exit 0 | ✓ | ✓ |
| 远端 wrapper 含 `MENU_UPDATE_ALLOWED` | `"0"` | `"1"` |
| 三 pin 投影后保持 fresh（v26.10.31 / 二进制 sha / vasma sha=v3.5.33 pin） | ✓ | ✓ |
| wrapper sha256 == 本地 HEAD 渲染（占位符替换后逐字节） | `87cfb3ce…` | `794bb30e…` |
| 备份落盘（回滚入口） | `v2ray-agent-maint.caSYL1` | `v2ray-agent-maint.KhyHaN` |
| 干跑 wrapper：pin-hit 短路 `skip reinstall` + `Configuration OK` + exit 0 | ✓ | ✓ |
| 服务 active + 端口在听（zz: 443/21470/10094 TCP + 443 UDP；bwg: 443/21470/10094/8443 TCP + 443/8444 UDP） | ✓ | ✓ |
| cron 双行分列（xray 周五 14:20 / sing-box 14:30 UTC） | ✓ | ✓ |
| `proxy_hardening_drift.ps1` | `NONE` | `NONE` |

## 意味着什么

- zz 上 vasma 菜单管道自此不可能被 wrapper 驱动（`16→1→1` 按键在守卫处截停）；
  zz xray 版本变更唯一路径 = 受控手动换芯（备份→停→换二进制→sha 验→启）+ 匹配
  pin 重投影（pin 命中走短路，不触碰守卫）。
- bwg 周更行为零变化（守卫在 `=1` 时直通，行为探针 `test_menu_guard_blocks_manually_managed_core_before_vasma`
  双分支钉住）。

## 回滚

- 远端：恢复投影前备份（`/var/backups/v2ray-agent-maint.{caSYL1,KhyHaN}` 内
  xray-wrapper/cron-file 原样拷贝）或用投影前旧模板重放 `-Apply`；服务未受影响，
  回滚只涉文件层。
- 仓库：commit `ab6bdbb` revert + 重投影。

## 残留与挂账

- `-RotatePath`（bwg capability path 轮换，中和 git 历史泄漏）已推荐、待用户排期，
  不与本批次混批。
- 第十二轮其余 P3/Low 挂账见记忆 `full-chain-review-20261102-round12`。
- 脱敏说明：本文件不含 IP、凭据、订阅地址；sha256 为公开二进制/脚本指纹与 pin 值。
