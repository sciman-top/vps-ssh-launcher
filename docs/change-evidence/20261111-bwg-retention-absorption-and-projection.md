# bwg 保留契约吸收、传输稳定化与投影收口（2026-11-11）

**触发**：用户授权按清理评审建议连续执行；并行会话留下 41 文件未提交工作轮（门禁红、
停在稳定化中途），按"并行会话转验证吸收"先例吸收完成并投影。

## 1. 吸收与稳定化（8964c33，后被并行会话吸收推送）

- 吸收面：压力分级保留（free<8GiB 手动家族收紧 keep-4）、周龄+校验和保护的
  构建源码归档回收、build cache 时龄修剪、精确身份孤儿容器清理、safe_files
  私有原子写原语、admission 车道状态身份钉定（dev/ino）与恢复相位加固。
- 稳定化（8 红→绿）：admission 恢复锁——恢复写以 `state_write_failed` 兜底
  保住运营锁、首失败快照移到置位前（原守卫是死代码，`_pre_failure_hold`
  从未记录）；分块传输 receiver（project_environment + cpa_bwg_guardrails
  两份）修正双引号笔误并把循环改为流末语句（bash 顺序消费 stdin，旧帧序
  把后置语句当数据吞掉=静默空转）；mock 升级为 create/chunk/commit/framed
  四形态；integrity pin 重钉。

## 2. 传输 Hybrid（75b7563 + 60a59ea）

单次调用分帧把整个 transfer 塞进一个 launcher argv——真机 Win32
CreateProcess 32K 上限直接拒启（"文件名或扩展名太长"，190K doctor 载荷从未
到达 ssh_tool；bash 桩不经过 argv 故四轮本地验收全部无法暴露）。修正为
预算分派：外层 base64 ≤24K 走单发帧；超限（及 -ViaTempFile）回退逐块独立
调用投递 mode-600 远端临时文件 + 小型 commit receiver，commit 先验字节数
再解码（初版计数比较因 `\"` 转义笔误恒错、且外层无 set -e 形同虚设，首轮
真机事务的 `integer expression expected` 警告暴露后修正为失败即 exit 21）。

## 3. admission reviewed deploy（000bc95 事务 + 000bc95 后的契约修正）

- `-Apply` 按设计拒绝 admission 变更（pin mismatch）→ 克隆
  deploy-admission-edge-denial-20261109 事务为
  `outputs/deploy-admission-stabilization-20261111.ps1`：drain + 基线三查
  （live/pin/config 哈希）+ staged sha 校验 + nobody 编译预检 + 备份
  （unit/脚本/pin/lane-state）+ EXIT 回滚 trap + 仅重启 admission +
  就绪等待 + 双恢复契约活体断言。
- **首轮事务触发回滚且 ROLLBACK_VERIFIED**（回滚机器实战自证）；根因=契约
  脚本用了原始 json lane dict（缺 load_config 的顶层合并键）。修正为
  `module.load_config` + Path 后二轮 `DEPLOY_OK pin=c410187f…`，
  RECOVERY_CONTRACT_OK 真机通过（上游 hold 保真恢复 + 无前置 hold 时
  运营锁保留）。
- 备份：`20261111T101726.989724456Z-admission-stabilization-20261111`（首轮
  回滚备份 `…065420…` 亦留存）。

## 4. 投影竞态与 readiness 修复（c3ffa0d + 8ea900e 前后）

- 第一次 -Apply：15 文件全部投影+哈希验证后，健康电池单发 curl 撞容器/
  admission 同窗重启（connection reset）→ 按契约全量回滚（回滚把投影还原、
  新文件删除；admission 因事务先行换新而保持）。doctor 复核确认回滚状态
  一致、无部分写入。
- 修正：apply.sh 健康步加 12×5s 有界重试（持续失败仍回滚）。
- 第二次 -Apply 仍 7 连败——**非竞态**：新 fail-closed catalog 检查拒绝
  `deepseek-v4.1-flash-oc`（今日用户决策退役的 OpenCode leg，并行会话
  2366cd0/20540ac 已裁 manifest+retired_hosts）与 `gemini-3.8-flash-tiered`
  （antigravity 在役分层别名，manifest 不管理）。本地差集闭合后
  （6b6cb0c：tiered-only allowance，oc 保持 fail-closed 直至 slot 退役落
  catalog），第三次 -Apply `GUARDRAILS_APPLIED`，opencode slot 随
  retired_hosts 剥离，catalog 复净（oc 消失），15 文件哈希验证。
- 超窗观察：工作树 18:49 存在并行会话 stash-pop 冲突残留（test_cpa_updater
  _apply.py，"Stashed changes" 为吸收前旧注释），按 upstream 侧解决，stash
  条目保留未动。

## 5. 实弹验收

- updater `--check`：current=v8.0.61 target=v8.0.61，SOAK_PENDING
  newest=v8.0.67（69.7h）正确延期；BACKUP_HEALTH ok（62 目录/1.04GiB）。
- updater `--apply` no-update 路径 rc=0：孤儿容器 removed=0、backups keep-8
  removed=0、家族链活体（workbuddy 过期 1 目录实删、admission 22 目录被
  168h 地板正确压住）、源码/工具链归档快照双建、build cache 空闲跳过。
  `IMAGE_RETENTION_DEFERRED reason=backup_inventory_failed` 按设计延期至
  04:07 timer 复判（非阻断）。
- 事务后服务态：容器 Up、healthz 200、四 lane 在册、live admission=pin=
  c410187f、catalog oc 消失。
- doctor：投影漂移族全 MATCH；剩余 FAIL 两条均为运行态——
  `catalog_oauth_missing=gpt-6.1-terra`（OAuth 凭据冷却窗口模型暂离
  /models，早前探针曾在线，自愈回归）与 wb 车道 authentication_required
  hold（10:10:30Z 池子真空期经 oc 403 确定性复锁，早于本轮全部部署操作；
  oc 退役后该复锁路径已消失，释放事务与验收属 wb 车道运行线）。

## 6. 边界

- 未触碰：QQ tar、wb-canonical-key、QQ-bot 备份、wb 车道释放事务（并行
  会话已按其时间窗执行并收口，见 20261111-bwg-workbuddy-hold-release.md）。
- daemon.json builder GC 预置（VALIDATE_OK）保持休眠，随下次 daemon 启动
  生效。
- 并行会话活跃协作：其推送吸收了本会话全部前置提交；本会话 amend 2661cf4
→6b6cb0c 与其推送竞态造成的分叉按合并解决（见提交历史）。
