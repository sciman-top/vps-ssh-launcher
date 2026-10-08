# 2026-10-08 BWG admission retry-after 浮点修正窄事务部署

## 范围与依据

- 用户授权在 bwg 自主升级 CPA 并连续执行修复优化；本切片为其中先行修复。
- 代码提交：`10b190e`（`_ceil_remaining_seconds` 吸收 ulp 级浮点误差、
  `reset_probe_throttled` 分支补 `max(1, ...)` 下限、`snapshot()` 锁内取
  单调时钟；回归测试 monkeypatch 固定单调时钟 6419.828s）。
- 本机（office 工作站）无 `%APPDATA%\vps-ssh-launcher\providers.env`，
  `cpa_bwg_guardrails -Apply` 不可用，故沿用 2026-10-07 验证过的窄事务：
  仅替换 `/opt/cliproxyapi/cpa-admission.py` 并重启 `cpa-admission.service`，
  不触碰 CPA 镜像、config.yaml、Nginx、fail2ban 与通道限额。
- 驱动脚本：`outputs/deploy-admission-20261008.ps1`（共享维护锁、双端
  sha256 三重校验、py_compile 前置、三 lane inflight/pending=0 与
  retired_readers=0 空闲门、备份目录含 rollback.sh、失败自动恢复）。

## 本地验证

- `pytest tests/test_cpa_admission.py`：56 passed。
- compileall / ruff check / bandit / `git diff --check`：clean。
- 期望 SHA-256（git blob LF 口径，与 doctor `Get-HeadBlobSha256` 一致）：
  旧 `c64114425612a992b38d6d1acff20fda1a7048091d0deb3ac6cef566b7ad3693`，
  新 `e7a5e108916e8fd4923a2423207187a80dc0bd25fb72436fc2c95a5d7e3e1ae3`。

## 过程记录（含两次中断，均由事务门安全拦截）

1. 首次事务执行在空闲门被拒（chatgpt-oauth inflight=1 真实流量），按设计
   等待重试；探针确认 Python 3.12.3、`math.ulp` 可用、旧文件哈希匹配。
2. 等待窗口期间（03:06:51Z–03:07:31Z），另一台被授权维护机用同一驱动
   脚本与同一提交内容（备份目录
   `/root/cpa-admission-floatfix-backup-20261008T030651Z`，哈希同为
   `e7a5e108…`）完成了一次等价部署并重启服务。两端无写冲突：内容一致、
   共享锁互斥成立。
3. 复核该备份时误执行了其 `rollback.sh`（该脚本不识别额外参数），远端被
   恢复为旧版 `c6411442…` 并重启——属操作失误，未造成数据损坏；顺带实证
   了备份回滚路径可用。随即用同一驱动重新部署：
   `DEPLOY_OK sha=e7a5e108… pid=502555
   backup=/root/cpa-admission-floatfix-backup-20261008T031727Z`。
4. 两个孤儿暂存目录（`/root/.cpa-adm-stage-20261008T030006Z`、
   `...T030234Z`）已清理。

## 验收

- 远端 `/opt/cliproxyapi/cpa-admission.py` sha256 =
  `e7a5e108916e8fd4923a2423207187a80dc0bd25fb72436fc2c95a5d7e3e1ae3`，与
  HEAD blob 一致；严格 doctor 9×投影 MATCH、`admission=OK`、
  `DOCTOR_CONTRACT_OK`。
- 重启后 admission 正常承载真实流量（journal upstream_result 200 序列），
  三 lane 无冷却、无容量事件。
- 本事务只证明 repo_verified / filesystem_projected / host_loaded 与回滚
  路径；未做付费模型受控回放（CPA 升级切片的 quality/cache canary 覆盖
  全链，见同日升级证据）。

## 回滚

```bash
bash /root/cpa-admission-floatfix-backup-20261008T031727Z/rollback.sh
```

回滚后远端为 `c6411442…`（885fdc2 版本），需同步将本仓源码回退到该版本，
否则 doctor 投影比对会报 drift。
