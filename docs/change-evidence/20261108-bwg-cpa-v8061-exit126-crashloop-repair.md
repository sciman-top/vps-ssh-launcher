# bwg CPA 网关 exit-126 crash loop 事故修复回执 — 2026-11-08/09

**性质**: 事故修复（真实远端写入：`chmod 700` + `docker restart`）· 脱敏留档
**触发**: 用户报告"使用 VPS 的 CPA client key 调用所有模型时都报错"（2026-11-08 深夜）
**前序**: v8.0.61 换芯事务链（`outputs/cpa-v8061-*`，当日另一会话）与 access key
双阶段轮换回执 [20261108-bwg-cpa-accesskey-rotation-stage1.md](20261108-bwg-cpa-accesskey-rotation-stage1.md)

## 1. 一句话结论

故障与 access key **无关**：网关容器 `cli-proxy-api` 因宿主 bind-mount 的入口二进制
丢失执行位（mode 600）进入 exit 126 crash loop（15:03:20Z 起，RestartCount=51+），
admission(8318) 对所有模型一律回 503 `upstream_unavailable`。修复 =
`chmod 700` + 重启，环回与公网全链单发均 200。

## 2. 时间线（UTC）

| 时刻 | 事件 |
|---|---|
| 04:48:13 | `CLIProxyAPI-patched` 构建落盘（mtime 之锚；事务脚本族 `umask 077`） |
| 14:47:16 | swap 事务重建容器（v8.0.59→v8.0.61 镜像 + wb 0.9.89 插件，sha `f2c678ff`）；bind-mount 生效后服务正常 |
| 15:02:11 / 15:03:01 | 两次 converge 事务尝试各留一份备份目录；live 文件 mtime/sha 证明**均在 mutation 前中止**（fork 二进制未上线） |
| 15:03:20 | 首次 `exec: ./CLIProxyAPI: Permission denied`；live 文件 **mtime 不变而 mode 变 600** —— 与"从 `chmod 600 "$BK"/*` 硬化的备份做保留 mtime 的还原（`cp -p`/`cp -a` 语义）"一致；精确调用方未留痕（runner 日志仅 transcript） |
| ~15:47 | 发现时容器 `Restarting (126)`，RestartCount=51，denied×53 |
| ~15:48 | `chmod 700 /opt/cliproxyapi/CLIProxyAPI-patched` + `docker restart` |
| 15:52 | 环回 models 200 + deepseek 单发 200；公网链（nginx 能力路径）models 200 + 单发 200；workbuddy 插件注册正常 |

## 3. 现场证据（脱敏）

- `docker inspect`：`RestartCount=51 ExitCode=126`，entrypoint `/bin/sh -c umask 077 && exec ./CLIProxyAPI`，挂载 `/opt/cliproxyapi/CLIProxyAPI-patched -> /CLIProxyAPI/CLIProxyAPI (ro)`。
- 宿主文件：`-rw-------`（600，root），mtime 04:48:13 未变；容器日志 `exec: ./CLIProxyAPI: Permission denied` ×53。
- crash loop 期间 `8318/healthz` 仍报 `status=ok`（upstream 字段为静态配置），**不能**作为 8317 存活证据。
- 误探说明：排查初期用错两处过期 keyfile（见 §5），产生少量 401/空 Bearer 探针；公网能力路径约 4 次，未触发封禁。

## 4. 缺陷与修复归宿

事务脚本族（swap + converge）三处共性缺陷，已在 `outputs/cpa-v8061-*-20261108.sh` 补丁：

1. **install 不钉执行位**：`install -m "$(stat -c %a "$PATCHED")"` 只传播旧模式；
   umask-077 构建产物或 600 备份还原会把 600 原样接力。→ 安装后固定
   `chmod 700 "$PATCHED.new"`（root-only 语义不变，仅补 owner 执行位）。
2. **重启前无 `[ -x ]` 预检**：→ `BINARY_NOT_EXECUTABLE` 快败，交给既有回滚。
3. **回滚路径自伤**：备份经 `chmod 600 "$BK"/*` 硬化，`restore_all` 用 `cp -a`
   原样搬回 = 回滚本身重新部署不可执行二进制（本事故的触发面）。
   → 回滚 mv 后补 `chmod 700`。
4. 观察项（未改）：`wait_readiness` 走 `cpa-health.py readiness` 直探 8317，
   正确；但 8318 healthz 在 8317 死亡时仍 ok，任何以 8318 为准的探活都会假绿。
   runner 输出仅 transcript、不落文件，事故溯源缺一手日志 —— 后续换芯建议补
   文件级日志（未实施，避免扩本次范围）。

## 5. 关联事实：探针 key 真源（防复发）

- 当前唯一 access key 真源 = `/opt/cliproxyapi/config.yaml` `access.api-keys[0]`
  （phase 2 已收敛为单键）；远端 `/root/.cpa-gw-key-20261108.txt` 与本地
  `%TEMP%` staging 件已按卫生流程删除。
- **`/root/cpa-gateway-newkey.txt`（mtime 2026-09-21）是过期遗留，不是现役 key**，
  勿再作为探针 keyfile；早期索引行中"8318 探针钥=gateway-newkey.txt"的口径已作废。
- 提取方式（不落 transcript）：远端 `python3 -c` 正则取 key 进 shell 变量，
  只输出状态码/长度，值不打印。

## 6. 回滚

- 本次修复的最小逆操作：`chmod 600 /opt/cliproxyapi/CLIProxyAPI-patched`（恢复
  事故态，无意义，仅备查）。
- 深度回滚（换回 v8.0.59）：按 swap/converge 事务备份目录与各自脚本 rollback 段
  执行；补丁后的脚本已保证回滚产物可执行。

## 7. 遗留

- **converge 未完成**：fork gemini-raw 二进制（sha `31f2f9d9`，755）staged 待装，
  生产现跑官方 v8.0.61 二进制（sha `330b7a55`）+ wb 0.9.89。事务两次尝试均中止于
  mutation 前（drain 锁竞争或契约拒绝），恢复执行须用补丁后脚本并由对应会话推进，
  非本次修复范围。
- v8.0.60 soak 到期 2026-11-10T20:13:52Z 的既有 automation（automation-9acf1459）
  预期仍按 phase-2 后单键口径验收。
