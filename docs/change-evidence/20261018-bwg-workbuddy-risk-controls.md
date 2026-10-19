# BWG WorkBuddy 风控恢复记录（2026-10-18）

**性质**: 生产配置最小恢复与热加载回读。记录先前误禁用及纠正；不含凭据、请求体、访问令牌或管理口令。

## 1. 事件与依据

- 用户要求控制封号、限流与输出质量风险，并授权按建议自动修复；用户并未要求关闭 WorkBuddy 账号或通道。
- 执行时错误地把风险控制做成插件实例禁用。用户指出应保持账号和渠道可用后，立即恢复。此前禁用是操作失误，不是推荐的常规风控措施。
- WorkBuddy 官方 [Acceptable Use Policy](https://www.workbuddy.ai/document/acceptable-use-policy) 与[个人服务协议](https://www.workbuddy.ai/document/term)限制未经授权的自动化访问、逆向访问、限制规避和未获许可的互操作；官方[错误码说明](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Error-Code)将 `6003/6004` 列为频率限制信号，没有公布本插件可用的安全阈值。
- 社区 [issue #2152](https://github.com/yetone/magpie/issues/2152) 是单用户报告，不能推出封禁因果或安全请求节奏。没有可用的阶跃星辰独立 API，WorkBuddy Intl OAuth 目录的历史观测也没有 `step-5-preview`。

## 2. 最小恢复事务

1. 管理页回读确认插件此前未注册、有效插件 0/0；认证页显示 WorkBuddy 凭据 2 份且均启用。只读取凭据文件数量，不读取内容。
2. BWG 只读状态：CPA 容器 running，重启数 0；配置权限 `0600`、SHA-256 `a8e731b68fd43641b0e1e98178ed302261eb85ad5942d0623e849e80d5dfcf1c`。配置中 WorkBuddy 为 `enabled=false`；四项自动任务键缺省，插件默认会开启相关后台任务。
3. 先 dry-run 语义预演，确认配置差异仅限 `plugins.configs.workbuddy.enabled` 和四项自动任务键。备份为 `/opt/cliproxyapi/config.manual-snapshot-20261018T145110Z-pre-workbuddy-reenable.yaml`，权限 `0600`，SHA-256 `a8e731b68fd43641b0e1e98178ed302261eb85ad5942d0623e849e80d5dfcf1c`。
4. 保持插件实例开启，并将 `checkin_auto=false`、`tasks_auto=false`、`lifecycle_auto=false`、`token_keepalive=false`。保留 `scheduler_mode=credits` 与其他 provider 配置。
5. 文件写入触发 config reload，但管理页仍显示 0/0；因此没有把磁盘状态误报为运行态注册。随后使用管理页的 WorkBuddy 实例开关启用。
6. 最终配置 SHA-256 `a2c7eb2e03872add97f70798aba748f57af688b6b9f18cfc57d598772a5e3`、权限 `0600`；配置回读为 `enabled=true`、四项自动任务均 `false`。管理页显示“生效中 / 已注册”、有效插件 1/1；插件版本 `0.9.58`。
7. 日志记录 config reload 成功（2026-10-18 22:47:38 UTC+8），及 `plugin loaded` / `plugin registered`（22:52:24 UTC+8）。CPA 持续 running，重启数 0。

## 3. 风控边界与验收

- 用户发起的模型调用入口保持注册；本次没有发起模型生成请求、OAuth refresh、账号/IP/设备切换、冷却清理或容器重启。
- 最新 BWG 配置只读回读：`routing.retry.request-retry=0`、全局 `max-retry-credentials=1`；没有改全局凭据回退策略，以免影响同容器其他 provider。调用端 retries 由本机 Codex 检查确认未报告 `desktop-gateway-retry-amplification`；该检查退出码为 1，是因为 `public_gateway` 上 DeepSeek / Muse 两个已选模型不可路由，与插件管理页的 WorkBuddy 注册状态属于不同证据。
- 未查询 WorkBuddy `/v1/models` 或发起模型生成请求；因此 Codex 公网 profile 到 BWG WorkBuddy 的端到端路由、上游账号状态与 live acceptance 均未验收。
- WorkBuddy 凭据仍为 2 份；未删除或读取凭据内容。四项自动任务的配置值已读回为 false，但没有账号请求来证明插件运行时行为。
- 固定插件版本的 shutdown 回调为 no-op，之前逻辑卸载不能证明旧 scheduler goroutine 已退出；CPA 未重启以保护同容器其他 provider。goroutine 数量和恢复后账号状态未验证。
- `repo_verified`：本地 runbook、落装台账与本记录反映当前恢复状态，待 diff 检查。
- `filesystem_projected`：远端配置值、权限、哈希和恢复前备份已读回。
- `host_loaded`：CPA 管理页 1/1 与 `pluginhost` 注册日志确认插件已注册；容器未重启。
- `live_accepted`：未做模型请求；不据此宣称上游账号状态或自然请求成功。
- 完整配置快照记录的是恢复前旧状态，含 WorkBuddy 禁用且自动任务键缺省。直接覆盖会再次关闭渠道；只能在确需恢复配置损坏时审查并做语义级合并。
- 后续风控保持渠道注册，以调用端 retries=0、按 `Retry-After` / reset 停止重试、遇 `403/11140` 转官方支持、关闭插件后台自动动作来控制活动。不得切账号/IP/设备、清冷却或批量探测。


