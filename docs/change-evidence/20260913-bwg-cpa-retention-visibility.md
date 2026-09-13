# 2026-09-13 BWG CPA 备份保留与更新失败可见性

用户授权按风控评估结论连续执行 P3 建议；仅 BWG，只涉及更新器与 doctor 两个既有面。

## 改动

- `cpa-auto-update.sh` 增加有界保留：仅在验证成功的更新路径之后执行
  `prune_backups`（保留最新 8 个更新备份目录）与 `prune_images`（保留运行中镜像
  加紧邻上一版本镜像）。UNVERIFIED（exit 10）与回滚路径绝不 prune；prune 失败只
  记 `PRUNE_FAILED` 日志并在下次更新重试，不会让已成功的更新转为失败。
- digest 固定拉取会使版本 tag 显示为 `<none>`，故回滚镜像按 ID 保护：ID 从本次
  更新备份内的 compose.yml 提取（与预更新 inspect 复用同一 `compose_image_ref`）；
  未打 tag 的本仓镜像按 ID 删除，`docker rmi` 只作用于
  `eceasy/cli-proxy-api:` 前缀的引用。
- doctor（`cpa_bwg_guardrails.ps1` 默认只读模式）新增两段纯信息输出：
  `==timer-result==`（更新 service 最近 Result/ExecMainStatus/ExitTimestamp 加
  auto-update.log 关键行尾部 6 条）与 `==inventory==`（根盘水位、更新备份数与
  大小、本仓镜像 tag 数）。设计上的 exit 10（上游暂不可用）不会使 doctor 失败，
  该段无任何 mark_fail。

## 依据与取舍

- 部署前探针：根盘 40G 用 13%（33G 可用），更新备份 3 份共 5.3MB，本仓镜像 3 个
  约 495MB（各 286MB）。无紧迫性，但每次真实更新净增一个镜像，增长无界；
  保留策略将其约束为常量水位。
- 主机无 mail/msmtp/sendmail 等任何通知传输，既有 cron 仅为 v2ray-agent 自维护。
  按"不新建治理面"约束，失败可见性落在既有 doctor 与 journal 上，不新建推送渠道；
  如未来需要主动推送，须另行授权。
- `/root` 下 18 个历史一次性维护备份共约 6MB，保留不动（会话级回滚锚点），
  不在本策略范围内。
- 保留常量：备份 8 份（约 2-3 天一个成熟版本的节奏约为一个月回滚深度）、镜像 2 个
  （当前 + 上一版；两步以上回滚需重新拉取远端 tag）。

## 部署与回滚

- 部署前备份 `/root/cpa-retention-deploy-20260913T140718Z/auto-update.sh`；
  回滚 = 恢复该文件（无需重启容器，更新器每次独立调用），Git revert 不影响远端。
- 部署为原子替换：临时文件 bash -n 通过、远端 sha256 与本地一致
  （7d77488a4b8971519bc8a9357d28e4a752d65604cbff7491b5b63f28039fdba2）后 mv。

## 验证

- 本地：focused suite 32 passed / 62 subtests，含新契约测试（唯一 `rm -rf` 且为
  `rm -rf --` 有界形式、唯一 `docker rmi`、prune 调用仅在成功日志之后、
  UNVERIFIED 段无 prune、回滚镜像按 ID 保护）；prune_backups 在临时目录的真实
  bash 执行：10 备份删最旧 2 个、保留 8 个、非目录文件不触碰；updater bash -n
  测试；Ruff、format、Mypy、git diff --check 通过。
- 远端：`auto-update.sh --check` 退出 0（backups=3、空闲空间远高于下限）；
  systemd 真实入口触发一次 `Result=success` / `ExecMainStatus=0`，日志
  `OK: no newer mature release; current=v7.2.157 health verified`（含一次真实
  Luna 生成健康检查）；对部署产物本体做函数提取的受控 prune 测试：
  kept=8 removed=2，与预期一致；strict doctor `DOCTOR_CONTRACT_OK`，
  新增 timer-result（success/0）与 inventory 段输出正常，timer 下次触发
  2026-09-14 04:05:53 UTC 未变。

## 边界

- 镜像 prune 的真实 `docker rmi`（含按 ID 删未打 tag 镜像）尚未在真实更新中执行；
  首个验证点为下一次真实更新（v7.2.158 预计 2026-09-14 16:05 UTC 满 72h，
  预计 09-15 04:05 UTC timer 采纳 157→158）。备份 prune 在真实 BACKUP_ROOT 上
  首次触发要等备份数超过 8。
- 探针时观察到一次 13:54:46Z 的 `--check` 运行（先于本次部署，只读路径、无状态
  变更、非本会话触发），未归因，仅记录。
- 全局并发 3 的观察项维持不变：如正常多设备使用受损再按实测调整；明确不做项
  （identity-confuse、预防性 per-credential proxy-url、自动轮换路径/key）维持原议。
