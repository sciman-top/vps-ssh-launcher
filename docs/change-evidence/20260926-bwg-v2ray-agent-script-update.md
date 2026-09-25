# BWG v2ray-agent 管理脚本自动更新链投影与受控验收（2026-09-26）

## 目标与事故边界

本次针对过去“自动更新 mack-a/v2ray-agent 后代理服务全崩”的风险，建立独立的
管理脚本更新链。当前 BWG fresh read 已确认 Xray、Nginx、fail2ban active，Xray
配置测试通过，安装器本体无需重装；本次没有执行 `auto_install.py`、`install.sh`、
vasma 菜单或代理重启。

- 范围：只处理 `bwg`，未连接或修改 `zz`。
- 当前官方 raw `install.sh`：401478 bytes，v3.5.25，SHA-256
  `fca0ad30d335b05b4e99fc5de848aeaff6c32d4b97f01ae84497dfad2978bfeb`。
- BWG `/etc/v2ray-agent/install.sh` 和 `/usr/bin/vasma` 与该官方内容完全一致。
- Xray 仍为 26.3.27；sing-box 当前 inactive，本次没有把 sing-box 混入恢复链。

## 仓库实现

提交 `cb1f4ec`（`加入 v2ray-agent 管理脚本安全周更链`）新增：

- `scripts/remote/v2ray-agent-script-update.sh`：固定官方 HTTPS raw 地址；候选做
  大小、`bash -n`、版本标记和菜单锚点校验；使用共享 flock；只原子替换
  `/etc/v2ray-agent/install.sh`；失败恢复旧脚本；不调用 vasma、不执行菜单、不重启服务。
- `scripts/v2ray_agent_script_update_cron.ps1`：严格 host-key、fresh 安装器 hash
  前置、备份优先投影和 `/etc/cron.d` 安装；远端投影失败恢复 updater/cron。
- `docs/runbooks/v2ray-agent-script-update.md`：投影、受控验收、回滚和供应链边界。
- README 与静态测试同步长期维护链入口。

## BWG 投影

先执行只读投影器，确认远端没有同名 updater/cron，安装器 hash 和服务状态正常；随后
以 fresh hash 执行：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\v2ray_agent_script_update_cron.ps1 `
  -Profile bwg -InstallSha256 fca0ad30d335b05b4e99fc5de848aeaff6c32d4b97f01ae84497dfad2978bfeb -Apply
```

Apply 成功：

- 备份：`/var/backups/v2ray-agent-script-update-deploy.6C93Yp`。
- updater：`/usr/local/sbin/vps-launcher-v2ray-agent-update.sh`，权限 755，投影
  SHA-256 `979bd113…96484a689`，`bash -n` 通过。
- cron：`/etc/cron.d/vps-launcher-v2ray-agent-update`，每周五 14:40 UTC，明确调用
  updater `--apply`。
- 输出 `RUNTIME_VERIFY_OK`、`UPDATER_PROJECTED`；安装器 hash 未漂移。
- Xray、Nginx、fail2ban active，Xray 配置测试 OK；投影没有重启这些服务。

## 模拟与受控实战

- **只读候选检查**：官方候选与现有安装器均为 v3.5.25、同一 hash；日志输出
  `CHECK_NO_CHANGE`、`SERVICES_OK`。
- **Apply 无变化回放**：日志输出 `NO_CHANGE`、`SERVICES_OK`，没有替换安装器，
  没有服务重启。
- **锁忙负路径**：持有共享维护锁时，updater 返回
  `CONTROLLED_LOCK_BUSY_EXIT=75`，日志为 `DEFERRED_BUSY`。
- **异常候选 fail-closed 模拟**：临时 mock `curl` 返回 17-byte 伪候选；updater
  返回 1，日志为 `REFUSE candidate_size=17`，安装器 hash 保持不变，临时 mock 已
  删除。
- **长期链回读**：原 Xray wrapper/cron 仍在位；CPA fresh strict doctor 仍为
  `DOCTOR_CONTRACT_OK`；fresh inventory reachable，主机 fingerprint 未漂移。

## 分层验收

| 层级 | 结果 | 依据 |
|---|---|---|
| `repo_verified` | PASS | full gate：187 passed、1 skipped、213 subtests；Bandit、Ruff、mypy、`git diff --check` 通过 |
| `filesystem_projected` | PASS | updater 和 cron 备份、原子投影、权限、hash/readback 全部通过 |
| `host_loaded` | PASS | updater syntax OK、cron 在位、安装器 hash/版本保持、Xray/Nginx/fail2ban active、CPA doctor OK |
| `controlled_live_replay` | PASS | `--check`、无变化 `--apply`、锁忙 75、异常候选闭锁、最终全链 readback |
| `natural_live_accepted` | NOT CLAIMED | 仍需正常业务窗口观察真实用户流量；本次不发送 provider generation，不把脚本 hash 或服务 active 外推为账号/模型长期质量 |

## 长期维护归宿与限制

- 该 updater 已并入个人 VPS 长期维护链，与 Xray、CPA、月度维护共用锁和分层验收。
- 它更新的是管理脚本，不是代理恢复器；代理服务已经损坏时必须走单独的人工备份、
  `auto_install.py --execute` 或 vasma 恢复方案，不能由本周更链自动重装。
- `master` 是可变分支；HTTPS、大小、语法和锚点校验不是完整源码审查或签名验证。
  上游结构变化会闭锁，等待人工审查后再调整仓库规则。
- OAuth renewal 当前仍为观察窗口；CPA 的 502/503/429 仍按上游/客户端观测分类，
  不作为封号、限流或降智结论。
