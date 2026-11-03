# BWG CPA v8.0.52 → v8.0.53 联合 canary（2026-11-03）

## 目标与授权

用户按 round-15 全链审查推荐授权连续执行：人工联合 host/plugin canary
v8.0.52→v8.0.53。本窗无配对 re-auth——round-15 增量审查证实
v8.0.52..v8.0.53 无登录/scope 耦合变更（对比 v8.0.52 窗的 aicode 配对）。

## 版本审查（v8.0.52..v8.0.53，16 commits）

- v8.0.53 = `01b24f1`（2026-11-03T12:27Z 发布）；镜像 digest
  `sha256:19eae277664c18d980701cf1a283b11440f82efbac17e910a9120fdd5ac0e792`
  （镜像构建 12:28:41Z）；镜像抽取二进制 sha256 `5fc980e1…`，`ldd` 仅 libc。
- upgrade-relevant 四项：`e5827065` disabled 凭据 + 被拒 refresh token 的
  auto-refresh 空转短路（上游 #7183；生产 claude 隔离态不加载凭据故非在产
  事故，属解封路径保险）；`d3899771` 定向 OAuth re-login 全 provider 生效
  （就地覆写 + 身份校验兜底）；`01b24f1` codex max_output_tokens 跨翻译器
  保留（#7230，desktop 客户端自带上限自此真实生效）；`24b04548` antigravity
  SAFETY/PROHIBITED_CONTENT finish → Claude refusal（#7201，拒绝可观测）。
- watch 项：`0130d6f3` claude Fable-only 429 model-scoped（隔离中不触发）、
  `6d4102f3` Claude Code native-entrypoints、`a1eeb60e` 非 codex 目录路由
  关闭 image_detail_original。
- 零变更面：缓存语义、admission/inflight/冷却阶梯、连接池——chatgpt.com
  分支仍无连接复用（round-10 结构性 TTFB watch 继续挂账）；payload override
  与 developer-role 保留逻辑原样在位，本地 zhipu developer→system override
  （f2cad18）在 v8.0.53 行为不变，无双重改写。

## 隔离加载证

`ISOLATED_PLUGIN_LOAD=PASS`：从 staged 镜像 `docker cp` 抽取二进制（与生产
运行字节同源，sha256 `5fc980e1…`），fixture 树无凭据，`unshare --mount
--net` 隔离；banner `v8.0.53, Commit: 01b24f1`；workbuddy `0.9.84` 注册 +
`account_max_concurrent=1 applied`；零 panic/fatal。

## 备份

`/opt/cliproxyapi/backups/20261103T151007.118655503Z-from-v8.0.52-to-v8.0.53-plugin-0.9.84-unchanged/`：
compose.yml（与 live 逐字节一致）、config.yaml、workbuddy.so（sha
`2665af74…` 未变）、`credentials.sha256`（14 凭据）。

## 生产事务（2026-11-03T15:10:51Z 起，~7 秒）

- 首次尝试按设计 fail-closed 拒绝：`chatgpt-risk.json=unverified_request`
  write-ahead 瞬态（恰有 codex 请求在途），零改动、回滚未触发；20s 退避重试
  落入间隙。
- 断言链全过：compose pin `v8.0.52@sha256:5d5bab31…`、备份 compose 与 live
  逐字节一致、插件 sha、回滚镜像在位、磁盘 13.4GiB、三 lane hold=None、
  traffic_guard healthz 校验；维护锁 + `requests.lock` 排他 drain。
- 原子换 compose 镜像行（替换计数=1）→ `docker compose up -d --pull never`
  重建。
- 验证全绿：`DIGEST_READBACK=OK`、宿主 banner `v8.0.53, Commit: 01b24f1`、
  插件注册 `0.9.84` + `account_max_concurrent=1` + `daily_credits_cap=300`
  applied、`HEALTH_GENERATION rc=0`、`AUTH_BYTES_UNCHANGED`、
  `LANE_HOLD_DELTA=none`、`restarts=0`。失败路径自动回滚 trap 全程武装
  （备份 compose 回放 + up + readiness），未触发。

## 升级后复验

- 8318 单发 `gemini-3.8-flash-high`（chat 形状）SSE = 200 / 3.12s / 3 events
  / `PONG` / completed。
- 8318 单发 `glm-5.3-flash`（responses wire + developer item 桌面形状）=
  200 / 3.09s / `PONG` / completed——developer→system payload override 链
  在 v8.0.53 端到端成立，窗口内零 `1214`/`角色信息`。
- grant 重基线 watch（round-15 增量：`AuthGrantFingerprint` api_key/
  session_token fallback）：窗口零 duplicate/conflict 告警。
- strict doctor：`DOCTOR_CONTRACT_OK`（policy=OK 含 zhipu override 规则、
  三 OAuth lane OPEN、claude 墓碑 inert、workbuddy 0.9.84 全 pin、compose
  与 nginx 语法 OK、TLS 68 天）；换 pin 窗 `lane_reject=0`。
- 事务/加载证/post-verify 脚本随台账入库 `outputs/cpa-v8053-*.sh`（R8）。

## 回滚入口

- 镜像：备份目录 compose.yml（v8.0.52 pin `5d5bab31`）回放 +
  `docker compose up -d --pull never`（v8.0.52 镜像本地在位）。
- 凭据：备份目录 `credentials.sha256` 比对；插件 workbuddy.so 副本在备份。

## 运行态变化与边界

- 每日 updater 明日 04:10Z 起回到 current=v8.0.53「无候选」路径。
- watch：①`max_output_tokens` 自此真实生效，desktop 客户端长输出可能出现
  新截断点；②:28317 面板的定向 OAuth re-login 语义变为"就地覆写目标凭据"
  （身份校验兜底在），巡检/自动化若带 `name`/`auth_index` 参数需复核；
  ③claude lane 维持隔离（11-12 AUP 政策不变）；④chatgpt.com 无连接池
  （结构性 TTFB）与 #6280 sensitive-words（上游仍 open，本地
  `['gpt','openai']` 钉定仍为唯一防线）维持既有裁定。
- 两台 desktop 消费面无感知（仅网关侧变更）；zz 不涉 CPA，逐台边界不触发。
