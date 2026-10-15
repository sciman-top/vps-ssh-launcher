# 2026-10-15 BWG 长期维护修复、投影与验收

## 范围与实现

- 用户授权连续完成审查建议的修复、优化及验收；仅处理 BWG。
- 实现提交：`34708dc`、`ed0af32`。保留同仓其他任务的 admission 修改，未以旧副本覆盖。
- 内核 wrapper 正确处理 INT/TERM，恢复一次并以 130/143 失败退出；回滚采用同目录临时文件、内容核对与原子替换，避免覆盖运行中二进制的 ETXTBSY。
- CPA 恢复入口复用 maintenance flock、Nginx 新连接 fence、全部 lane 保护状态检查、socket 排空与 readiness；updater 在读取上游元数据、生成或变更前阻断未验收 pending。
- TLS wrapper 明确检查 acme/证书前置，区分正常 skip 与失败；验证证书有效期和 Nginx 8443 实际加载证书的 DER 指纹。
- Compose 升级及回滚增加 `--wait --wait-timeout 120`，退出状态覆盖容器健康等待。
- Windows 常驻 reset bridge 取消 12 小时执行限时、设置失败重启 3 次；状态读回保留 management cursor 及时间偏移。
- 每日 BWG Observe 复用 strict doctor、既有日志与任务退出码。既有任务无需增加参数即可执行检查。

## 本地验证

- 完整 runner 的 build、pytest、Bandit、Ruff check/format 均通过：`614 passed, 1 skipped, 299 subtests passed`。
- 该轮 runner 最后 mypy 因新增测试的一处类型标注失败；修复后定向 mypy 通过。这里不将原 runner 记作 exit 0。
- 后续 focused 回归：`62 passed, 83 subtests passed`；最后 BWG Observe 兼容修复：`21 passed, 26 subtests passed`，定向 mypy 通过。
- PowerShell AST、Bash 语法、Ruff 与 `git diff --check` 均有通过证据；默认真实 SSH integration 跳过。
- 独立只读复核提出的 TLS 前置表达式及 updater 不可达清理分支已处理并回归。
- BWG 隔离目录真实 Linux 实验：直接 cp 覆盖运行中的 sleep 副本复现 ETXTBSY；实际 restore_binary 函数原子替换成功，原进程保持运行。`LINUX_RUNNING_BINARY_ATOMIC_ROLLBACK=PASS`，临时实验目录已清理。

## 远端投影与真实入口

| 目标 | 部署后 SHA-256 | 变更前备份 |
| --- | --- | --- |
| `/etc/v2ray-agent/auto_update_xray.sh` | `b3d7a7156aa36d8ea8da083aab6dc46bc543d0a4961f0f8feb697a743d908e1b` | `/var/backups/v2ray-agent-maint.7tIn89` |
| `/usr/local/sbin/vps-launcher-v2ray-agent-renewtls.sh` | `3b960ed9391d23925fae38fea3b400a95bebe1c3d839710247618726196cf3c0` | `/var/backups/v2ray-agent-renewtls-deploy.zhpYL8` |
| `/opt/cliproxyapi/auto-update.sh` | `67021f57d5468d926103a5bfbd1a1d98dee04acfed2f2e583afc33209024590b` | `/var/backups/vps-cpa-updater-reliability.QqRduL` |

- Xray pin：`26.3.27`；installed SHA `8255dd939c34cf966cc91517b6324dd3c8d0bcf49ffac8beca049a38c46845ed`；vasma SHA `fca0ad30d335b05b4e99fc5de848aeaff6c32d4b97f01ae84497dfad2978bfeb`。投影前已核对现状，部署后 hash 与本地渲染匹配。
- 保持主机 UTC 调度：Xray `20 14 * * 5`，TLS `30 1 * * *`，均在 `/etc/cron.d`。
- 实际运行 Xray wrapper，命中相同 pinned 版本的正常 skip；未为验收重新下载或重启 Xray。
- 实际运行 TLS wrapper 成功，覆盖正常 acme skip、legacy backstop、有效期及已加载证书指纹检查。
- 实际运行 CPA updater `--check` 成功，不发送生成请求；未制造生产 pending 或为验收执行 updater `--apply`。
- 三项 heartbeat 分别于 `2026-10-15T10:54:09Z`、`10:54:10Z`、`10:54:11Z` 记录 success/0。运行日志保留在 `/var/backups/vps-maint-runtime-proof.IJfz4D`。
- 未启用闲置 sing-box；其 wrapper 不存在且 heartbeat 为 NOT_REQUIRED。官方 stable 查询时 Xray 最新稳定版为 v26.3.27，sing-box 为 v1.14.3；闲置组件不因追版本而启用或迁移。

## 本机任务加载与实际运行

- `VPS-Cpa-Reset-Bridge` 变更前 XML：`%LOCALAPPDATA%\vps-ssh-launcher\task-backups\VPS-Cpa-Reset-Bridge-20261015-185224.xml`；原状态 JSON 同目录备份。
- 核实精确脚本及 `-Run` 进程身份后，仅重新启动该任务以加载修改；任务 Running、ExecutionTimeLimit=PT0S、RestartCount=3。Running 时的 LastTaskResult=267009 是任务仍在运行的状态码，不是已结束成功回执。
- 每日 Observe 的既有 S4U 任务定义更新被 Windows 拒绝访问；未删除任务或提权。通过 `ed0af32` 的默认行为兼容既有调用，任务定义保持原状。
- `VPS-SshLauncher-BWG-Observe` 实际启动后：`LastRunTime=2026-10-15T18:54:48+08:00`，Ready，LastTaskResult=0，日志含 `POLICY_OK`、`DOCTOR_CONTRACT_OK`。
- 日志：`%LOCALAPPDATA%\vps-ssh-launcher\maintenance-runs\20261015T105449Z-4bf58755-bwg.log`。
- 未重启 Codex App、Cockpit、NapCat 或本地承载 API 的进程。

## 系统更新、备份与影响

- 最初可升级 33 包；准备期间 Ubuntu unattended-upgrades 自行更新 24 个安全包。陈旧计划被预检阻断，重新读取当前版本及准备备份后才继续。这 24 包不是本代理主动安装。
- 本次第一批主动更新 7 包：containerd.io、docker-buildx-plugin、docker-ce、docker-ce-cli、docker-ce-rootless-extras、nginx、tzdata。
- 第二批主动更新 2 包：libopeniscsiusr、open-iscsi；两个 iSCSI 服务更新后仍 inactive。
- 每批均使用 shared flock、版本漂移保护、配置及 dpkg 元数据备份、旧版本包、APT 回滚模拟、所有 lane 空闲和 socket 排空检查、精确 Nginx 新连接 fence、固定版本 `--only-upgrade`。无新增或删除包，保留 conffile；APT 有总时限和进度标记。
- Docker 更新使 CPA 容器重新启动；更新后 CPA running、restart=0、固定 image digest 未变，readiness 及 admission 正常。未重启 VPS。
- 初始准备备份：`/var/backups/vps-maint-system-20261015.Qikxd9`。
- 第一批实际基线备份：`/var/backups/vps-maint-system-20261015.E5Ntle`。
- 第二批备份：`/var/backups/vps-maint-iscsi-20261015.jUs7gR`。
- 后两目录含 root 私有配置归档、前后包清单、固定升级参数、旧包与 `rollback-packages.sh`；不得将配置归档导入 Git。
- iSCSI 旧包已不在 APT 源：使用安装态重打包作为回滚材料，**不是上游原始 deb**。临时 dpkg-repack 1.52 与 libdpkg-perl 1.22.6ubuntu6.6 经签名 APT 元数据的 SHA-256 核对，仅解包运行，未安装；工具运行目录及工具 deb 已清理，来源回执留在备份目录。

更新后版本：

| 组件 | 版本 |
| --- | --- |
| Docker server/client | 29.9.1 |
| containerd.io | 2.4.2-2~ubuntu.24.04~noble |
| Nginx | 1.31.7-1~noble |
| OpenSSH server | 1:9.6p1-3ubuntu13.20 |
| tzdata | 2026e-0ubuntu0.24.04.1 |
| libopeniscsiusr / open-iscsi | 2.1.9-3ubuntu5.5 |
| CPA | v8.0.29，image digest `b98590c932e2a372e61d53e175a3abe4e8a22118667b2de3ad71514cbf1e3eb6` |

- 剩余 AppArmor/libapparmor1 `.8 -> .9` 明确为 Ubuntu **phased 10%**；`apt-mark showhold` 为空。APT 模拟 exit 0：0 upgraded、0 newly installed、0 remove、2 deferred。尊重原生分阶段发布。
- 最终 `REBOOT_REQUIRED=False`、`DPKG_AUDIT_CLEAN=True`，临时 system/recovery fence 均不存在。

## 升级后最终验收

- `2026-10-15T11:08:45Z` strict doctor：`DOCTOR_CONTRACT_OK`、exit 0；12 项投影全部 MATCH，admission integrity/加载、policy、服务、Nginx 语法、TLS、模型目录及路由契约通过。
- 网关证书剩余 87 天；正确路径无鉴权为 401，裸路径和错误路径为 404。证据中不保存 capability path 或 key。
- doctor 仍报告历史上游错误及 model substitution 观察项；这些有界日志不等于当前全部 provider 可用，也不因 strict doctor 通过而消失。
- 升级后一次非 OAuth 请求：BWG 上经公网域名 TLS 8443 → Nginx → admission → CPA → `glm-5.3-flash`。固定短 prompt，stream=false，max_tokens=1024，TLS 验证开启，禁重定向、零重试，使用既有 health-probe flock 并先检查 GLM lane 无活动或保护。
- HTTP 200，1.71 秒；实际文本等于预期 OK，finish_reason=stop，admission trace 存在；同 trace 的 journal 为 `capacity=false successful=true`。
- `CONTROLLED_PUBLIC_GENERATION_RESULT=PASS`，exit 0；请求后全部 lane inflight/pending/cooldown=0、hold_reason=null、state_write_failed=false。该受控请求未使用 OAuth。
- `2026-10-15T11:10:56Z` 最终读回：三项部署 SHA 不变、admission status=ok、dpkg audit 清洁、无需重启、无临时 fence；最新批次包回滚模拟及 updater 回滚预检通过。

## 回滚入口与顺序

1. 系统包必须倒序恢复：先第二批 `/var/backups/vps-maint-iscsi-20261015.jUs7gR/rollback-packages.sh --check`，再按需执行不带 `--check` 的脚本；完成服务/配置验证后，才处理第一批 `/var/backups/vps-maint-system-20261015.E5Ntle/rollback-packages.sh`。
2. 每批更新当时均有更新前后回滚模拟通过。第二批完成后，第一批当前直接 `--check` 会以 `ROLLBACK_REFUSED_UNRELATED_PACKAGE_DRIFT` 拒绝，因为 iSCSI 版本相对第一批基线变化。这是顺序保护，不应放宽；必须先恢复第二批再重新预检第一批。当前最新批次预检通过，未在生产真实降级。
3. CPA updater：`bash /var/backups/vps-cpa-updater-reliability.QqRduL/rollback.sh --check` 通过；实际恢复使用同脚本不带 `--check`，保留共享锁与版本保护。
4. Xray wrapper 与 TLS wrapper：各自备份中保留旧 wrapper、cron 和原 crontab/缺失标记；按相应部署脚本的 rollback 实现恢复本切片文件、调度并比较内容。不要用整个旧 crontab 覆盖后续无关修改。
5. Windows bridge 仅在需要恢复旧任务时使用上述 XML 和状态备份，先停止精确任务，再恢复定义、状态并重新读取确认；不操作其他进程。
6. 本地代码按明确提交和文件逆向恢复；Git 回滚不替代远端恢复。生产回滚仍须 fresh read、备份、空闲/保护检查及恢复后验证。

## 验收结论与边界

- `repo_verified`、本次修改所需 `filesystem_projected` / `host_loaded`、故障模拟、实际维护入口和一次受控公网生成均有证据。
- CPA RestartAndRecover、生产 updater pending/失败回滚和通用 Compose 故障降级未为验收在生产制造故障；已有 focused 行为测试、隔离实验及回滚预检，不宣称生产故障演练完成。
- 受控生成由 BWG 发起，不证明 Windows 客户端到 VPS 的自然数据链；Xray 版本/配置/服务检查不证明终端完整代理转发体验。
- 自然桌面使用、实际终端代理联网、未来日/周/月定时运行及长期稳定性未据此标记 `live_accepted`。当前可自动完成的修复与受控验收收口；保持既有调度，不新增监控或强制绕过系统 phasing。
