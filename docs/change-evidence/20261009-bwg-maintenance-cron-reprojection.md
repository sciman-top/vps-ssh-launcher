# 2026-10-09 BWG 维护 cron 载荷重投影收敛证据

## 目标与范围

- 目标：41ceb97 拉取审查后，确认并收敛 `021179b`（trap 中断语义 130/143）
  相对 bwg 部署态的投影漂移；顺带以只读探针完成双机一致性验收。
- 范围：维护 cron 域四个 wrapper（renewtls / script_update / vasma kernel /
  monthly maintenance）。CPA 域零触碰（无 CPA/Nginx/admission 写入、无容器
  重启）；`zz` 不在范围（其维护 wrapper 按项目设计本就未投影）。

## 只读探针基线（写前）

- `cpa_bwg_guardrails.ps1` 严格 doctor：`DOCTOR_CONTRACT_OK`，CPA 域远端
  == 41ceb97 HEAD（20261008 晚间 -Apply 收敛后无新漂移）。
- `bwg_full_maintenance.ps1 -RunIntegration`（Observe）：`FULL_CHAIN_OK`，
  资源全部处于 manual/deferred 设计路径，xray noop/present。
- 四个 wrapper 只读探针收集 -Apply 钉定值（fresh read）：install.sh
  `fca0ad30…`（v3.5.25）、xray 二进制 v26.3.27 `8255dd93…`（与部署 wrapper
  内嵌钉定一致）、vasma 路径与 vasma 钉定一致；xray active，443/8443 在听。
- 写前安全检查：`/run/vps-ssh-launcher-maintenance.lock` 空闲，无活跃交互
  会话，最近维护备份 2026-10-07T14:54Z（上一投影代），无并行会话冲突。

## 漂移判定（哈希证据）

- 真实漂移仅一处：`/usr/local/sbin/vps-launcher-v2ray-agent-update.sh`
  部署 sha `666d1352…`（== ad82497 旧代）≠ HEAD `scripts/remote/
  v2ray-agent-script-update.sh` `00ee9535…`。
- `021179b` 对 renewtls / vasma / monthly 三者 ps1 的改动经定位均在一次性
  投影驱动器（`rollback_apply` / `rollback_on_exit` / trap 行），持久部署
  载荷未变，故预期幂等；renewtls 已由幂等 apply 实证（sha 投影前后不变）。

## 变更事务（逐项串行，各带备份）

| 项 | 命令 | 结果 | 备份目录 |
|---|---|---|---|
| renewtls | `-Profile bwg -Apply` | `RENEWTLS_LOCKED_PROJECTED`+`RUNTIME_VERIFY_OK`，sha `8dd72b07…` 幂等不变 | `v2ray-agent-renewtls-deploy.Wx0ZUU` |
| script_update | `-Profile bwg -Apply -InstallSha256 fca0ad30…` | `UPDATER_PROJECTED`+`RUNTIME_VERIFY_OK`，**真实收敛** `666d1352…`→`00ee9535…`，部署态 trap 复验为 `on_error 130/143` 新语义 | `v2ray-agent-script-update-deploy.EW9l6o` |
| vasma kernel | `-Profile bwg -Kernel xray -Version v26.3.27 -InstalledSha256 8255dd93… -VasmaSha256 fca0ad30… -Apply` | 幂等：部署 `auto_update_xray.sh` sha `f1c51d58…` 不变，钉定值在位 | `v2ray-agent-maint.RkayCN`、`OgRQDR` |
| monthly | `-Profile bwg -Apply` | 部署 `monthly-maintenance.sh` 由 HEAD 驱动器重写（`2f34bab5…`），cron 文件 `f59b13c1…` 幂等不变，legacy crontab 0 条 | `v2ray-agent-maint.VQ8mrp` |

全部 apply exit 0；PRUNE 按 keep_8 策略各移除 1 个最旧备份；无 REFUSE /
FAILED / 回滚触发。

## 写后复验

- `systemctl is-active`：xray=active、nginx=active；CPA 容器 Up 11h
  （docker compose 管理，无 systemd 同名单元），loopback 网关 401（未带
  凭据的预期应答）；443/8443 在听。
- 四个被触碰远端文件 `bash -n` 全过（`ALL_SYNTAX_OK`）。
- 复跑 Observe：`FULL_CHAIN_OK mode=Observe profile=bwg`，网关访问日志零
  5xx（401/404 均为本机探针 loopback 应答）。
- 严格 doctor 未复跑：CPA 域零触碰，写前 doctor 绿按 E4 复用。

## 回滚

各事务备份即回滚源：`/var/backups/v2ray-agent-*-deploy.*`、
`v2ray-agent-maint.*`（含变更前 wrapper/cron 副本，root 700）；wrapper 内
`rollback_apply`/`restore_apply_state` 可按目录恢复。Git 回滚不适用远端。
