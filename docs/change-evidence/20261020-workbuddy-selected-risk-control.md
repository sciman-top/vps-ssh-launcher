# WorkBuddy 固定选择与风险回退收紧

2026-10-20 21:28:51（北京时间），BWG CPA v8.0.34。

依据：生产插件已有 selected 模式，源码与生产构建产物 SHA-256 均为
`3bca8a39d0e88194342c078d3ad0066727671bae11943f39ca0ebd8500a72218`。
复用现有模式，避免未知 11128 后自动选择其他账号；不修改全局凭据池参数。

验证：在 golang:1.26-bookworm 容器中，源码只读挂载、无凭据、network none、
GOPROXY=off，运行 `go test -count=1 -run TestSelectedMode ./...`，退出 0。
缺失/禁用/额度耗尽选择拒绝回退，人工选择仍可用。第一次 tmpfs noexec 导致
测试程序 permission denied，改为隔离容器 `/tmp:exec` 后通过。

预演：YAML 语义差异仅 plugins.configs.workbuddy.scheduler_mode，credits→selected。
应用前健康快照所有 lane inflight/pending 为零，持有维护锁并校验原配置字节。
备份：`/opt/cliproxyapi/backups/20261020T132851Z-workbuddy-selected/config.yaml`，0600。
保留 bind-mount inode，写后 fsync 与字节读回；没有替换插件或重启服务。
CPA 21:28:51 日志确认 config successfully reloaded。

回滚：先确认当前配置仍为本事务版本，与备份进行语义比较；仅将 scheduler_mode
改回 credits，保留其他并发改动，原 inode 写回并验证配置重载。不得整份旧配置
覆盖后续变更或恢复旧 OAuth token。

边界：宿主重载日志不独立证明插件内模式已生效。未用真实账号诱发拒绝，
未发送生成探针；生产拒绝回退行为等待自然失败的脱敏时间线确认。selected
保留人工面板选择；不应人工换号接续配额/账号受限任务。没有账号级 token
预算阈值证据，不新增猜测预算。HTTP 精确例外保留；Gemini 上游 modelVersion
名称差异继续观测，不能单凭字符串判定降档或通过改名消除告警。
