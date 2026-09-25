# CPA 手动回滚与降级（bwg）

自动回滚只覆盖 updater 自己修改的 Compose 镜像声明和 guardrails `-Apply`
涉及的投影文件。本页处理自动机制之外的人工回滚：升级已被 updater 判定
成功但事后验收失败、config.yaml 被误改、镜像需要降级。

## 原则

- updater 策略是"不降级"：自动路径永远不会往回走，降级只能人工执行。
- 一切人工回滚先取锁：与 updater/内核/月度维护共用
  `/run/vps-ssh-launcher-maintenance.lock`（`flock -n`，拿不到锁就改时间窗，
  不排队）。
- 回滚到旧镜像 = 恢复旧 compose（digest 钉死）+ `--pull never`，绝不 `latest`。
- 逐台纪律不变：单机执行、第二条 SSH 复验、确认联网正常。

## 场景 A：升级后验收失败，手动退回旧版本

updater 的备份 compose 保存在 `/opt/cliproxyapi/backups/<ts>-from-<cur>/`，
且只在验证成功后才清理（保留 8 份）；旧镜像受 prune 双 ID 保护不被删除。

1. 找最近的成功更新备份：

   ```bash
   ls -dt /opt/cliproxyapi/backups/*-from-* | head -3
   ```

2. 记录当前状态（回滚证据）：

   ```bash
   docker inspect cli-proxy-api --format '{{.Config.Image}} {{.Image}}'
   sha256sum /opt/cliproxyapi/compose.yml
   ```

3. 恢复备份 compose 并启动（镜像身份由 compose 内 digest 引用 +
   `--pull never` 双重钉死）：

   ```bash
   cp -a /opt/cliproxyapi/backups/<dir>/compose.yml /opt/cliproxyapi/compose.yml
   docker compose -f /opt/cliproxyapi/compose.yml up -d --pull never
   ```

4. 复验：`python3 /opt/cliproxyapi/cpa-health.py readiness` →
   strict doctor（本地跑 guardrails 默认模式）全绿 → 一次 `gpt-6-luna`/
   `glm-5.3-flash` 单发生成确认。
5. 回滚后镜像与旧 compose 一致即完成；不要顺手 prune，等下一轮 updater。

## 场景 B：config.yaml 损坏 / provider 配置错误

guardrails `-Apply` 每次在 `/root/cpa-guardrails-backup-<UTC.nano>/`（权限
700）留 `config.yaml` 变更前副本：

1. `ls -dt /root/cpa-guardrails-backup-* | head -3` 选变更前时点。
2. 恢复副本（保持权限）：

   ```bash
   cp -a /root/cpa-guardrails-backup-<dir>/config.yaml /opt/cliproxyapi/config.yaml
   chmod 600 /opt/cliproxyapi/config.yaml
   docker restart cli-proxy-api
   ```

3. config 级 `oauth-excluded-models` 等不走热载，重启是唯一保证。
4. 复验同场景 A 第 4 步。

## 场景 C：投影文件漂移（doctor `==projection-drift==` FAIL）

doctor 锚定 HEAD blob：本地未提交就 `-Apply` 会让六个 drift 门全 FAIL。处置
不是手改远端文件，而是对齐仓库：

- 远端是真源（比如带外改过）→ 把差异带回仓库、提交后再 `-Apply` 重投影。
- 仓库是真源 → 直接重跑 `-Apply`（写后即验 `PROJECTION_HASH_VERIFIED`）。

## 禁止

- 不把本页步骤做成定时任务或无人值守流程；降级永远人工个案。
- 不用 `docker compose pull` + `latest` "降级"——那是升级到不确定版本。
- 不删除或覆盖 `/opt/cliproxyapi/backups/`、`/root/cpa-guardrails-backup-*`；
  清理只由 updater 的有界逻辑执行。
- 不在回滚窗口触碰 auth JSON（镜像回滚与凭据无关；凭据问题走
  [OAuth 失效恢复](cpa-oauth-failure-recovery.md)）。
