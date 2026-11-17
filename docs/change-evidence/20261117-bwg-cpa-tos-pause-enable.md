# bwg CPA TOS 池级保护停启用 + config 重绑定回执（2026-11-17）

**性质**：受控远端变更（config.yaml 单键 + 容器强制重建 + 热加载翻转验证）。
**授权**：用户 2026-11-17 "授权按你的推荐/建议连续自动修复和优化，直至完成所有任务"；
窗口值经选项裁决（先 2h → 短暂改判不启用 → 复议后终态启用 2h）。

## 1. 变更内容

| 项 | 值 |
|---|---|
| 键 | `oauth.providers.antigravity.tos-pause-window: "2h"`（新增） |
| 语义 | 首例 TOS_VIOLATION → 违规凭据自动停用 + Antigravity 全池暂停 2h；429 永不触发；到期自动恢复 |
| 备份 | `config.manual-snapshot-20261117T0000Z-pre-cpintl-alias.yaml`、`config.manual-snapshot-20261117T0100Z-pre-tos-pause.yaml` |

上游依据（`repo_verified`，本地外置仓 c19787b2=v8.0.75）：
`config_types.go` AntigravityConfig.TOSPauseWindow、
`antigravity_executor_credits.go`（markAntigravityTOSPause 仅 TOS 路径写入；
antigravityInTOSPause 纯时间判据 + kv.db TTL；enabled=false 短路即放行）、
`watcher/config_reload.go`（debounced 热加载）。

## 2. 过程中发现并修复的既有缺陷：单文件 bind-mount rename 陷阱

CN/INTL 别名条目（本日早前插入）与 TOS 键均经 host 侧 `os.replace` 写入，
而 compose 以**单文件 bind-mount** 挂载 `config.yaml -> /CLIProxyAPI/config.yaml`：
rename 后容器仍钉在旧 inode，**两次编辑对运行时全部不可见**（容器内
`grep -c tos-pause-window` = 0 实证）。`docker restart` 不重解析 bind-mount，
须重建：

```text
drain 锁（requests.lock 排他）→ docker compose up -d --pull never --force-recreate
→ HEALTHZ_OK attempt=1 → 容器内 count=1 → started=2026-11-17T00:37:36Z restarts=0
→ 启动日志 panic/fatal/exec-format=0
```

连带修复：`deepseek-v4.1-flash-intl` 别名自此才真正进入运行时目录
（运行时 `oauth-settings.workbuddy` 4 条目，序=alias/bare/sg/hy4free）。

## 3. 运行时验证（`host_loaded`）

| 项 | 结果 |
|---|---|
| `/v0/management/config` `antigravity.tos-pause-window` | `'2h'` |
| `oauth-settings.workbuddy` | 4 条目，FIRST_ALIAS=`deepseek-v4.1-flash-intl` |
| strict doctor | `DOCTOR_CONTRACT_OK`（0 MISMATCH / 0 FAIL） |
| admission | 5 车道 hold_reason=null 全程 |

## 4. 提前恢复手筋（端到端实测）

保 inode 翻转（`open(path,'r+')` 写回）触发热加载，零重启零断流：

```text
BEFORE='2h' → 翻"0" → RUNTIME='0' → 翻回"2h" → RUNTIME='2h'
docker logs: config file changed, reloading ×2
```

即：暂停期间确认波过去后，翻 `"0"` 即时恢复全池，处置完翻回 `"2h"`
重新武装；全程单文件单真源，无 inode 分叉。

## 5. 回滚

1. 快照还原：`cp -a config.manual-snapshot-20261117T0100Z-pre-tos-pause.yaml config.yaml`
   （保 inode 写入则热加载即回；若曾 rename 则再 `--force-recreate`）。
2. 或仅把键值改 `"0"`（禁用池级停，保留凭据级自动停用）。
3. 台账/doctor 无 pin 联动（config.yaml 不在投影 pin 集）；doctor 会以
   `semantic-policy` 读文件侧值，改键后 doctor 期望不变。
