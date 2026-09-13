# 2026-09-13 CPA 定时触发与故障注入验收

用户明确授权真实 timer 可回滚触发与隔离故障注入。只处理 BWG；生产没有故障注入。

## 真实 timer

- 原 timer：周一 UTC 04:00，随机延迟 30min，Persistent=true；原 service 未修改。
- 临时覆盖 `/run/systemd/system/cliproxyapi-update.timer.d/90-cpa-acceptance.conf`，
  清空原 OnCalendar 后设近未来时间，RandomizedDelaySec=0、AccuracySec=1s、Persistent=false。
- 11:18:34 UTC timer 自己触发原更新 service，11:18:37 UTC exit 0 / Result=success。
  现场 LastTriggerUSec=11:18:34，service start/exit 时间对应；没有手动 start service。
- finally 移除精确覆盖文件、daemon-reload、restart timer，原 unit SHA256 不变。
  恢复 next=2026-09-14 04:11:43 UTC；生产脚本与服务未因调度验收更改。
- Persistent 恢复后 LastTriggerUSec 再读取会显示旧持久时间戳，这不抹去现场触发证据；
  此次没有模拟机器停机补执行，也没有冒充原定周一自然触发。

## 隔离方法

- 从当前运行容器提取同一 CLIProxyAPI 二进制，不拉取新镜像，不复制 OAuth/API 凭据。
- `unshare --mount --net --fork`，只启用独立 loopback；临时目录绑定为命名空间内
  `/opt/cliproxyapi`。生产进程、挂载和网络不受影响；fixture 不能访问公网模型服务。
- 合成上游在 loopback 18318，真实 CPA 在隔离 loopback 8317，使用合成 key。
- 脚本 `scripts/remote/cpa-acceptance.py` / `cpa-update-acceptance.py` 要求 fixture marker
  和独立网络命名空间；不是默认运行入口，不得直接用于生产目录。

## 过载路径（通过）

- 配置与生产关键行为一致：request-retry=0、max-retry-credentials=1、60s transient
  cooldown、save-cooldown-status=true、stream-bootstrap-buffering=true。
- 合成上游 HTTP 200 SSE 先发 response.created，再发带 server_is_overloaded 的
  response.failed；真实 CPA 返回 503，上游实际访问次数=1。
- 上游切回正常后，冷却期立即请求仍 503，上游新增访问=0。
- 等待 62 秒后，不重启同一 CPA 进程，后续请求 200、response.completed，上游新增=1。
- 实际生产 cpa-health.py 对该真实 CPA 返回 HEALTH_OK / exit 0。
- 这证明当前 Codex executor 的过载/冷却/恢复机制；fixture 使用合成 API credential，
  不覆盖真实 OAuth token 刷新、真实上游容量恢复或账号政策。

## 更新脚本路径

使用原样生产 auto-update.sh 和真实 cpa-health.py；仅替代 release 元数据及 Docker CLI。
Docker 替身用真实 CPA 进程启停模拟镜像切换，因此不证明 Docker daemon 自身失败恢复。
替身与网络隔离保证不会拉取镜像、操作生产容器或访问 GitHub/上游。

- 新版本启动失败：exit 1，恢复旧 Compose，ROLLBACK 日志，真实旧 CPA readiness 成功。
- 新版本目录暴露额外模型：exit 1，恢复旧 Compose，旧 CPA readiness 成功。
- 持续上游 503：首次测试揭示模型冷却会从目录消失，旧 health 将其错判为本地失败，
  导致误回滚。已据此修正分类并添加回归检查：有效且无越权的目录为本地就绪证据，
  必需模型缺席是上游未就绪；无效目录/无法访问/额外模型仍是本地契约失败。

生产修复仅涉及 cpa-health.py，部署前单独备份原文件；原先 timer、CPA 配置、
凭据、限流和更新周期均保持。回滚只恢复 helper 备份，不需要重启 CPA。

## 修复后结果与清理

- 持续上游 503 重跑：exit 10，旧 Compose 未恢复，本地真实 CPA readiness 成功，
  无 ROLLBACK，存在 UNVERIFIED。保留版本但不谎报生成验收成功。
- 正常更新：exit 0，使用新 Compose，真实 CPA readiness 与生成检查成功。
- 本地回归覆盖目录冷却缺席、非法目录、越权模型、429/503 与一般契约错误。
  test_scripts.py 26 passed / 51 subtests；Ruff/format/Mypy/diff check 通过。
- 部署备份 `/root/cpa-health-acceptance-fix-20260913/cpa-health.py`；新 helper
  SHA256 `f0baa98dc80d8d9884f3afb19b49d070f34ca81de1718ae22094842cf588020a`，
  本地/远端一致；部署后实际生产 generation HEALTH_OK、strict doctor 通过。
- 生产仍 v7.2.157，running、restart=0；本轮未重启生产 CPA。
- 临时 fixture `/tmp/cpa-acceptance.Imr0bW` 已在核对绝对路径和 marker 后删除；
  没有残留 fixture CPA 进程。临时网络和挂载随进程退出销毁；测试数据只有合成内容，
  删除不影响生产数据，测试可由版本管理的验收脚本重新构造。
- timer 覆盖已移除，周一周期恢复；没有新增常驻监控或宿主配置。

## 可重复执行边界

Linux 主机上准备一个 mktemp 创建的空目录，仅放当前 CPA 二进制、两个验收脚本、
实际 updater/helper 和 FIXTURE_ONLY 标记；在私有 mount/net namespace 内 bind 为
`/opt/cliproxyapi` 后运行 cpa-acceptance.py。默认完整跑过载及四个更新场景；
CPA_ACCEPTANCE_UPDATE_ONLY=1 跳过已验证的 62 秒过载周期；
CPA_ACCEPTANCE_REMAINING=1 只选择持续上游故障与成功更新，用于本次因果修复复测。
环境选择仅属于隔离测试，不改变生产脚本。测试依赖本机 python3/PyYAML/ip/unshare，
不联网安装；执行前必须确认命名空间可用，结束后按准确临时路径清理。
