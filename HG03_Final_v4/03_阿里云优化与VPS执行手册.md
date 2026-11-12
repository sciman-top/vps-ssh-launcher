# 阿里云专项优化、搬瓦工维护与工程门禁

## 1. 使用现有仓库执行，不建第二个控制面

先查 AGENTS、实际脚本和运行手册；确认 profile 对应真实主机（bwg、zz 是 README 名称，阿里不能猜）。运行只读命令时用现有 launcher 单 profile，复杂命令使用 -CommandFile；RunAll 仅原白名单。以下是 README 已记录的入口示例，执行前核对当前版本和本机已有私有配置。launcher 的命令串可含管道，但 `run.cmd` 经 PowerShell 转发时可能损坏引号；只读脚本优先用独立 `-CommandFile`，通过 Git Bash 的 Python 入口运行 `ssh_tool.py`，或直接受控执行已核验的脚本，示例中的反引号拼接不跨 PowerShell：

```text
.\run.cmd -Profile <verified-profile> -CommandFile <local-audit-file>
./.venv/Scripts/python.exe ssh_tool.py --profile <verified-profile> run --command '<read-only-command>'
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg
```

尖括号不是字面值；最后一条须先确认当前真实脚本默认 Doctor/只读。不要输出真实 target.json 或脚本回显中的秘密。只读审计仍可能消耗 CPU/IO，限定范围、低优先级、错峰。

## 2. ECS A0 容量审计

- 规格：云控制台实际 vCPU、内存、磁盘类型/容量、出入带宽、续费价；OS `nproc`, `free -h`, `df -hT`, `df -ih`, `uptime`, `vmstat 1 10`, `swapon --show`, `/proc/pressure/{cpu,memory,io}`。记录 MemAvailable、si/so、steal、iowait，不把缓存全算成不可用内存。
- Docker：`docker ps` 结构化字段、`docker stats --no-stream`、`docker system df`；按实际容器 `docker inspect --format` 取 State.OOMKilled、RestartCount、Memory、NanoCpus、LogConfig.Type。不要完整 inspect（可能含环境密钥）。分辨镜像共享层、可写层、卷、build cache，不能简单叠加共享层。
- 磁盘：用 `du -xhd1` 逐层查实际数据根、DockerRootDir、日志、备份；大目录用低优先级且限定时间，避免影响生产。对每项记录路径别名、大小、所有者、最后使用、依赖、能否重建、保留期、清理风险。只共享脱敏摘要。
- OOM/容器退出：按可见内核日志计数，注明权限、保留覆盖期和采样时间；无日志不等于无 OOM。自然渲染/备份窗口采样，不触发生产压力。读 `systemctl list-timers --all`、现有 QQ 清理/备份脚本，检查已有维护锁和调度。
- 优先复用 `D:\CODE\qq-astrbot-stack\scripts\sample-vps-baseline.py` 的隔离、低优先级 60 秒双采样与 cgroup 增量；它只读且必须通过现有 `_remote_ops` 严格主机 pin。外部脚本不能先被当作本仓信任代码直接借用，需单独审查调用与环境。不要将一次 `docker stats` 或 `docker system df` 读数外推为峰值、无 OOM 或回收安全。
- 网络：用云监控确认 3Mbps 出网及业务峰值、流量计费；不要跑无批准大流量测速。3Mbps 理论 375kB/s，实际有协议开销，不能因为卸载 QQ 而提高带宽。
- 参考 QQ README FAQ：NapCat 日常 150–250MB（启动峰值曾 768MB）、AstrBot+渲染 350–500MB、各限约 1GB，整体日常约 1GB；是项目声明，不是当前实测。常态 MemAvailable 400–600MB、系统盘保留约 8GB、无持续换页/OOM 为规划参考，不是硬 SLA。真实峰值决定能否共存。

## 3. ECS A1 独立优化切片

| 切片 | 执行条件与操作 | 复验/回滚 |
|---|---|---|
| 数据分类 | A 关键数据；B 可再生缓存；C 已确认冗余；D 未知禁止删 | 列对象及依赖，不能把 reclaimable 当安全可删 |
| 离机备份 | 备份配置、持久卷、QQ登录状态、数据库、补答队列、必要镜像版本/摘要；应用一致性，SQLite 用支持的备份方法或批准停写窗口 | 加密到批准位置、校验 hash、隔离恢复测试，不在快满盘上再堆巨包；不公开凭据 |
| 清理 | 审批具体对象；先转移过期备份、处理明确冗余缓存/镜像；不用 blanket prune | 逐项前后 df/du，保留当前与回滚镜像/卷；不可逆对象须单独审批 |
| 日志/备份策略 | 查现有每周清理、journal、Docker 日志驱动和任务；按实际需求轮转、错峰、保留 | 日志驱动变更可能需要容器重建，单独批准；保留排障证据 |
| 内存/CPU | 按实际压力降低渲染并发、错峰备份；核实容器限额、资源竞争；Swap 依据磁盘类型/压力评估 | 避免盲降限额导致容器 OOM；swap 不替代 RAM，检查 si/so 与响应 |
| QQ 停用 | 用户单独批准；确认服务、watchdog、重启策略及停止范围；先停不删 | 测量释放 CPU/RAM/带宽；恢复原启动顺序和 watchdog，QQ 可能需重新验证登录 |
| QQ 迁移/卸载 | 离机恢复已证实，用户明确批准，先停用观察后再卸载 | 保留数据与备份，数据销毁另审批；验证剩余服务与磁盘、依赖 |
| 清理授权 | 每个候选必须记录精确路径/镜像/卷/日志对象、身份、大小、生成者、保留期、依赖、离机恢复位置与恢复校验。QQ `/opt/qq-codex-bot/data`、NapCat 登录态、持久配置、待处理任务与 `data/plugins` 默认不可删。现有 QQ 日常清理脚本的白名单与保留期需新鲜只读核验 | Docker `RECLAIMABLE` 仅估算；恢复演练失败或对象不明即停止，不把删除磁盘文件当作释放 RAM |

禁用未审查的 `docker system prune -a`、`docker volume prune`、`docker compose down -v`、递归删除、重装。QQ 项目原有业务代码仍由 qq-astrbot-stack 管理，基础资源运维由 vps-ssh-launcher 协调。清理不必须依赖网络路线；若没有获准新增业务，也可独立完成有效优化。对比优化前后资源（同类负载/采样窗口）、容器/QQ 功能、日志、带宽；若仍不足且有明确需求，先实际报价比较升配、迁移、另购，不自动采购。

## 4. BWG A0/A2 服务保护

依据 README 现有 CPA 三层：Nginx TLS 8443 + capability path → Admission 8318 → Core 8317，真实部署须核实。优先复用 CPA Doctor、cpa_failure_triage、cpa_natural_traffic_audit、cockpit_provider_health、相关 runbooks；只读检查四条 lane、现有 catalog、失败原因、维护任务和证书。原 Xray/sing-box、REALITY/Vision 及其他服务保留，不重装/升级或修改节点来迁就本任务。既有 `proxy_lane_acceptance` 若需远端访问由 HOME 负责，先核对只读模式与授权目标；不能把 README 上列出的版本/端口当真实生产。没有必要则 BWG 零写入。

新接入必须继续使用既有完整受保护 HTTPS URL（包括实际路径前缀）和原认证/限流/Admission。直接转发 8317、直达 8318、公开管理端口、剥除 capability path、关闭 TLS 校验都禁止。新客户端凭据可撤销，若需追加，由现有 CPA 管理机制评审且不覆盖已有 key；不改变四条 lane、OAuth 池、重试/熔断策略。不要为本任务执行 guardrails Apply/Quarantine/Deactivate/ControlledReplay、变更模型或内核，除非另立明确批准任务。

若获准固定应用入口必须使用 ECS Caddy：先查实际 Caddy 运行于宿主还是容器、现有 80/443、证书与域名手续、上游实际 DNS/TLS、路由与日志脱敏。公开 80/443 已有 ICP 边缘拦截记录；网络可达性、备案与用途授权是 C 的前置，不能通过改写 Caddy 或 Host 绕过。由 HOME 生成精确路径/方法 allowlist，固定 BWG 已有 HTTPS 上游、保留 Host/SNI 和真实 capability 路径；不提供任意目标或 CONNECT，不开启 CPA 管理路径。验证认证失败、允许路径、SSE 无缓冲中断、客户端取消、无自动重放计费 POST、原 QQ 站点正常。SSE 无需额外配置：官方文档明确 `reverse_proxy` 对 `Content-Type: text/event-stream` 的响应默认立即转发（`flush_interval` 对其忽略），不要为流式引入多余指令。使用实际部署对应的 `caddy validate` 和受控 reload，备份前后配置，失败恢复原配置并 reload。此处不提供含真实凭据的可复制 Caddyfile；路由前缀、实际容器网络和配置真源需在 A0 确认，批准后由 HOME 生成最终 diff。**用途未批准或入口边界未成立，不进入这一步。**

## 5. 工程变更事务

- HOME 读取当前 repo 约定，先文档与最小增量，必要时新增只读探针/通用维护资源与对应测试。`vps-maint` 只处理实际已登记的资源；pin/hash/fingerprint、单主机审批、锁、receipt、回滚。不得借升级 adapter 强行执行清理。
- 现有维护锁 `/run/vps-ssh-launcher-maintenance.lock` 与 QQ 定时任务协调，避免两个维护器并发。不要删除其他任务的锁或覆写 crontab。备份、版本、预检、逐步执行、后验、失败回滚、脱敏收据。每项生产写入单独审批，真实 SSH 集成按原 opt-in。
- `scripts/run_gates.ps1` 根据仓库实际参数：文档最低充分检查；代码 Focused 指定实际源码和对应测试；核心 Full 一次；真实 integration 单独批准。不得为过门禁降低安全或重复全套测试。local pass ≠ live accepted。只归档必要证据，不把私有生产日志、真实域名路径等提交公开仓库。
- ECS 配置清理、Caddy 路由与 QQ 恢复不属于现有 vps-maint 通用清理能力；不得伪造 adapter。只有现有资源真正支持的操作才走 Plan/Apply，其余每项单独审批、备份、验证与恢复。
