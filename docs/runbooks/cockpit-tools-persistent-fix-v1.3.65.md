# Cockpit Tools v1.3.65 Direct API 持久修复

**版本边界**：本补丁只适用于 `1.3.65`，不能投影到 `1.3.66` 或其他版本。
`cockpit_sidecar_guardrails.ps1 -Mode Project` 会先检查已安装主程序版本，
不一致或无法读取版本时在任何文件写入前拒绝。fq 公网 direct 模式不经过这份
sidecar；应先检查账号接入方式与模型目录，不为 direct 链路恢复 `10909`。

本说明针对官方 `v1.3.65`(tag commit `0b6514b40880efdfd7752ebd5113c6811bafe721`)。
官方应用更新(2026-10-02 01:13 本地)替换了带本地修复的 sidecar 二进制,且官方
生成器在每次应用启动、每次 sidecar 拉起前都会重写活动 config/manifest,把
`request-retry=1`、`stream-bootstrap-buffering=true`(OAuth sidecar)与
`maxAccountConcurrency=0`(provider gateway sidecar)写回,完整回退 v1.3.64 时代的
本地闸门与退避修复。本补丁在 sidecar 二进制层重建修复并以入口钳制兜底生成器缺陷。

v1.3.63/v1.3.64 patch/runbook 与 v1.3.63 时代的 SSE flush 家族资产已于 2026-10-07
退役至 git 历史(其修复已并入官方 v1.3.64+ 与本补丁);当前活动修复路径以本文为准。

## 当前状态(2026-10-10 复核):契约保留,但补丁不在生效状态

- 本机 Cockpit 已是 **1.3.66**,`scripts/cockpit_sidecar_policy.json` 仍钉
  `1.3.65`,因此 `-Mode Project` 在任何文件写入前抛
  `Installed Cockpit version mismatch; no files changed`。`Audit`/`Verify`
  只读不受影响,并如实并排报告 `policyVersion=1.3.65` /
  `installedVersion=1.3.66` 与 `installed.sha256=6E7CA54E…`(≠ policy
  `72860FD9…`)。**该 policy 对 1.3.66 是自拒的,留着不会写入任何文件。**
- 安装目录 exe 是 2026-10-08 官方更新替换后的构建(SHA `6E7CA54E…`,
  44,452,352 字节),**不含本补丁的任何函数**:同一二进制里有 460 个 `main.*`
  符号(含上游既有的 `main.bindProviderGatewayAccount`、
  `main.providerGatewayBackoffState`、`main.waitForAccountConcurrencyChange`),
  但 `clampMaxAccountConcurrency`、`capAccountConcurrencyWaitMs`、
  `admitDirectProviderAccount`、`recordDirectProviderBackoff`、
  `localDefaultMaxAccountConcurrency` 全部缺失。⇒ 官方更新把带修复的二进制
  换掉了,`request-retry=1` 与 `stream-bootstrap-buffering=true` 重新成为纯
  生成器写回值,没有入口钳制覆盖。
- 影响面当前有限但非零:`codex_local_access.json` 现在 `enabled=false`
  (本机 Codex API 服务关闭),桌面 provider 列表指向远端
  `https://fq.sciman.top/fc3003d5715fbdf6/v1`(443 车道,见
  `cpa-gateway.md`),所以桌面链路不经过本 sidecar。一旦重新启用本机本地接入
  (10909/14185/42405),慢吐字与重试放大就跟着生成值回来。
- **处置结论:不删除**这份 policy/guardrail/runbook/patch。`Audit`/`Verify` 是
  本机 sidecar 契约(45 s 封顶、`RequestRetry=0`、bootstrap buffering off、
  并发零值桥接 3)的唯一可执行记录,且
  `tests/test_cpa_policy_runtime.py`、`tests/test_maintenance_cron_family.py`
  与 `README.md` 引用该入口。要恢复“生效”,须按本文件流程对 1.3.66 重新打补丁、
  重建 sidecar 并更新 policy pin;Go 工具链本机可用
  (`C:\Program Files\Go\bin\go.exe`),但 1.3.65 源码树
  (`%TEMP%\cockpit-tools-v1.3.65-build-20261002`)已清理,需重新取上游源码。
- 可回收的是 2026-09-30 的三个
  `cockpit-cliproxy.exe.before-sse-flush-*.bak`(合计约 132 MB):没有脚本按路径
  引用它们,也不再是任何当前对照的基线。若日后仍要做 r 世代 A/B,保留最新一个
  (`…-161739.bak`)即可满足 `cockpit_gate_wait_cap_check.py --control`。

## 相对官方 v1.3.65 的修改(8 个文件)

统一补丁:`outputs/cockpit-tools-v1.3.65-persistent-fix.patch`(862 行,含新文件,
`git add -N` 后导出);r2 快照保留为 `outputs/cockpit-tools-v1.3.65-persistent-fix-r2.patch`
(789 行)。源码树:`%TEMP%\cockpit-tools-v1.3.65-build-20261002`。

1. **Direct/fixed-provider 账号并发闸门**(自 v1.3.64 移植,官方仍缺失):
   `provider_gateway.go` 把绑定账户传入闸门,入场前 `admitDirectProviderAccount`,
   响应后 `recordDirectProviderBackoff` 缓存 429/503 的有效 Retry-After;
   `manifest_policy.go` tracker 增加 `providerBackoffs` 与同锁退避检查;
   `provider_gateway_concurrency.go` 为闸门与退避实现。
2. **入口钳制 `stream-bootstrap-buffering=false`**(自 v1.3.64 移植):
   `main.go` 加载 config 后强制关闭,防生成器写回 true 造成慢吐字回归。
3. **入口钳制 `request-retry=0`**(v1.3.65 新增):官方生成器每次启动写回 1,
   sidecar 入口强制归零,维持本机不放大重试契约。
4. **入口钳制 `maxAccountConcurrency<=0 -> 3`**(v1.3.65 新增,
   `clampMaxAccountConcurrency`):官方生成器**从不把账号并发族复制进 provider
   gateway collection** —— `build_provider_gateway_collection_for_profile`
   (`src-tauri/src/modules/codex_local_access_provider_gateway.rs:1837`) 从
   `new_empty_local_access_collection()` 起步,随后
   `apply_provider_gateway_template_settings`(:1566)只复制 18 个无关字段,
   `max_account_concurrency` 与 `account_concurrency_wait_ms` **都不在其中**,
   因此该 sidecar 永远只看到结构体默认值(并发 0、等待 120000 ms),用户设置
   一概无效。manifest 被写 0 会静默禁用 Direct 闸门,故零值桥接为本地契约值 3
   (上游 [PR #2677](https://github.com/jlcodes99/cockpit-tools/pull/2677)
   未合并,2026-10-02 复核仍 Open;仓库为 jlcodes99/cockpit-tools,勿与
   router-for-me/CLIProxyAPI 的同号 PR 混淆;改动真源后需同步
   `localDefaultMaxAccountConcurrency` 常量并重建)。
5. **入口封顶 `accountConcurrencyWaitMs`**(r3 新增,`capAccountConcurrencyWaitMs`):
   同一根因的另一半 —— provider gateway sidecar 恒收到生成器默认的 120000 ms
   等待预算,导致闸门饱和时客户端被挂满两分钟才收到 429。入口把预算封顶为
   `localMaxAccountConcurrencyWaitMs`(45000 ms),更小的配置值原样保留;该常量对
   provider gateway 就是有效值,对 API 服务 sidecar(能读到真 collection)只是上界。

Rust 生成器侧的两行字段复制修复(PR #2677 内容)未在本版重建主程序;sidecar
入口钳制已覆盖其危害,主程序保持官方原版。

## 本地闸门等待时长(两侧各自的生效路径)

`accountConcurrencyWaitMs` 与 `maxAccountConcurrency` 都是
`%USERPROFILE%\.antigravity_cockpit\codex_local_access.json`(Codex API 服务的
collection,UI 路径「Codex API 服务 → 调度选项」)里的用户设置。**两个 sidecar 的
生效路径不同**,这是本节的要点:

| sidecar | collection 来源 | 用户设置是否生效 |
|---|---|---|
| API 服务(`codex_local_access_sidecar`) | 直接读真 collection | 生效 |
| provider gateway(`codex_provider_gateway_sidecars/<hash>`) | `new_empty_local_access_collection()` + 18 字段模板复制 | **不生效**(见上节第 4/5 条) |

因此 2026-10-02 做了两件事:

1. 把真 collection 的 `accountConcurrencyWaitMs` 由 `120000` 收紧为 `45000`
   (备份 `codex_local_access.json.bak-waitcap-20261002`) —— 覆盖 API 服务 sidecar,
   同时让 UI 显示与实际一致;
2. 在 r3 二进制入口加 `capAccountConcurrencyWaitMs` 封顶 45000 ms —— 覆盖
   provider gateway sidecar(它读不到 collection)。

`maxAccountConcurrency` 保持 3 未动:UI 值对该 sidecar 无效,改它必须重建二进制,
而当前值已与 UI 一致。

依据:本机 `%USERPROFILE%\.antigravity_cockpit\codex_local_access_logs.sqlite` 的
`request_logs` 表中 `error_category='quota_or_rate_limit'` 且 `latency_ms>=115000`
的行,即"等满整个闸门预算后仍被拒"的事件数:09-27 4 次、09-28 6 次、10-01 9 次、
10-02 4 次;10-02 的 4 次 latency 恰为 120005/120020/120049/120006 ms,且同一时刻
Nginx 访问日志 `429=0`(请求从未离开本机),证明该 429 由本机闸门产生。等满预算才
失败说明该请求本来就需要更久,缩短预算不会让任何"本可成功"的请求失败,只消除白等。

复验口径(重载后):`request_logs` 中 `latency_ms` 落在 45000 附近的
`quota_or_rate_limit` 行取代 120000 附近的那些,`latency_ms>=115000` 应归零。

生效时机:下一次 sidecar 启动(Cockpit 重启或 Codex 切号)。**不要**为此
`taskkill` Cockpit;磁盘值与运行值在重载前不一致属预期。
**2026-10-02 22:30 已由用户重启 Cockpit 完成重载**:两个 sidecar 均加载 r3,
受控对照验收通过(见下节)。

## 构建与验证(2026-10-02)

| 项目 | r2(2026-10-02 上午) | r3(2026-10-02 晚) |
|---|---|---|
| SHA-256 | `C03966F524D8CF4491B30C3CDBBB2124B05F38F3A482B9691BFCDCAE363E5D95` | `72860FD906BD992674830EE18D522F969B6E4EE8EBC3EC55A2D253423385B36E` |
| 大小 | 44,315,136 字节 | 44,315,136 字节 |
| go test . -count=1 | PASS(3.657s,3 个 clamp 单测) | PASS(31.022s,含 4 个 cap 单测) |
| go vet ./... | 通过 | 通过 |
| gofmt(行归一化后) | 通过 | 通过(补丁涉及的 8 个文件全部 CLEAN) |
| 受控回放 | 10909 `glm-5.3`(zhipu lane,零 OAuth 配额):200/completed/1.6s | 10909 `glm-5.3`:200/completed/10.57s |

工具链:go1.26.3/windows/amd64,CGO_ENABLED=0,`-trimpath -ldflags '-s -w'`。

r1(SHA `4041...8860`,无 maxConc 钳制)已被 r2 取代;r2 与 r3 的差异仅
`capAccountConcurrencyWaitMs` 常量、函数、调用点与 4 个单测。

### r3 等待封顶的受控对照验收(2026-10-02 22:35,`ACCEPTANCE_PASS`)

方法：使用当前维护入口
`scripts/cockpit_gate_wait_cap_check.py`（历史 A/B 快照
`outputs/gate-wait-cap-acceptance-20261002.py` 仅作为当日证据保留）：起一个**本地桩上游**
(接受连接但永不回包),把线上 provider-gateway 的 config/manifest 复制到 scratch 目录
并把 `port` 改成 19109、删掉 `proxy-url`、抬高 stream 超时;两次运行使用**完全相同**的
config 与 manifest(manifest 里仍是 120000),**唯一变量是二进制**;先用 3 条并发请求占满
`maxAccountConcurrency=3`,再发第 4 条测其 429 延迟。零上游配额消耗。

| 二进制 | 第 4 条请求 | 错误体 |
|---|---|---|
| r3(封顶 45000 ms) | **429 @ 45.003 s** | `account_concurrency_exceeded` …「等待 **45.0** 秒后仍没有可用槽位」 |
| r2(无封顶) | **429 @ 120.002 s** | 同 code …「等待 **120.0** 秒后仍没有可用槽位」 |

⇒ 封顶**确实覆盖了 manifest 里陈旧的 120000**,并且只缩小等待、不改变拒绝语义。

补充事实:22:30 那次 Cockpit 重启**只重新生成了 API 服务 sidecar 的 manifest**
(已变成 45000),provider gateway 的 manifest **仍是 09:38 的 120000/0 未被重写**
——这正是必须有二进制兜底的原因,也说明不能靠"重启后看文件值"来验收。

**为什么文件不会被重写(机制)**:该 manifest 由
`codex_local_access_sidecar_config.rs` 的 `prepare_sidecar_launch_config_in_dir_sync`
生成,落盘用的是 `write_secret_string_atomic_if_changed` —— **内容不变就跳过写入**。
由于两个并发字段压根没被复制,生成出的内容与上一次逐字节相同,于是写入被跳过、
mtime 停在 09:38。⇒ **"文件陈旧"本身就是这个 bug 的自证症状**,不是独立问题。

### 运行加载与重启后现场(2026-10-02 22:30 重启)

```
app 重启 22:30:21 → 两个 sidecar 22:30:21/22 启动
  PID=35944  --config ...\codex_local_access_sidecar\config.json
  PID=12564  --config ...\codex_provider_gateway_sidecars\36218dcc…\config.json
  两者 ExecutablePath 均为安装目录 exe，SHA = 72860FD9…(r3)
API 服务 manifest @22:30 : accountConcurrencyWaitMs=45000, maxAccountConcurrency=3
provider gateway manifest: 仍为 09:38 的 120000 / 0（生成器未重写）
```

重启后本机 `request_logs`:**非 200 请求 = 0**;成功请求 n=10,p50 27.3s / max 159.4s
(慢仍在上游,与闸门无关)。当日 429 的 latency 桶仍只有 `lt5s:10` 与 `ge115s:4`,
后者全部是重启前 10:51/11:02 的历史事件。

## 安装备份(安装目录 `C:\Users\sciman\AppData\Local\Cockpit Tools\`)

- `cockpit-cliproxy.exe.official-v135-20261002.bak`:官方 1.3.65 原版
  (SHA `076D5082B2875607E2AE7890381436C1FA04BC946A92CCCC36CAB22187BEA35B`),
  回滚目标。
- `cockpit-cliproxy.exe.v135-gate-r1-20261002.bak`:r1 中间版,审计用。
- `cockpit-cliproxy.exe.v135-gate-r2-20261002.bak`:r2,由 r3 部署时 rename 产生,
  审计用。

安装分为磁盘投影与运行加载。先记录当前 sidecar PID、启动时间、映像路径与
SHA,再 rename 旧 exe 为备份、copy 版本匹配的新 exe。运行中的进程仍使用旧
映像;此时只能报告 `filesystem_projected`,运行加载验收仍待完成。

需要加载新产物时,在用户已授权的维护窗口通过 Cockpit 的正式启停入口完成重载,
随后核对双 sidecar 的映像 SHA、启动时间、监听端口及受控响应行为。日常诊断与
验收不得执行 `taskkill /F /T` 或停止现有 Cockpit。重载后活动 config/manifest
文件值会被生成器再次写坏,属预期;运行态由入口钳制兜底,验收以映像与行为为准,
单独的磁盘 SHA、端口存活或 `/v1/models` 200 均不能证明补丁已加载并生效。

## Cockpit 应用升级后复查清单

每次官方版本更新(主程序版本号变化)后执行:

1. `Get-Item ...cockpit-tools.exe | % VersionInfo.ProductVersion` 确认新版本号;
2. sidecar exe SHA 是否仍等于本补丁产物;被官方更新替换则按本文重建
   (上游新 tag → `git apply` 旧 patch 或手工移植 → 测试 → 构建 → 安装);
3. 顺序单发一次非 OAuth 模型请求(如 `glm-5.3`)验证 200/completed;
4. 检查新版本是否包含上游 PR #2677 的并发字段传递修复,并实测正值配置能够
   保存、生成和加载,四路请求仍按账号上限排队;通过后才移除对应的
   `clampMaxAccountConcurrency` 与 `capAccountConcurrencyWaitMs`(两者同一根因:
   生成器不复制账号并发族)。该 PR 不修复重试或流式缓冲,`request-retry`
   与 buffering 钳制必须分别核对官方实现并通过对应行为验收后才可移除;
5. OAuth lane 行为验收(单发 luna)遵守配额窗口纪律,周限额触顶期间勿探。

日常重启(app 或 sidecar)不需要重建或重装;入口钳制保证运行态不受生成器
重写影响。文件值 drift(request-retry=1 / buffering=true / maxConc=0 /
waitMs=120000)不构成告警,除非 sidecar 版本回退。

## 回滚

rename 当前 exe → 还原 `cockpit-cliproxy.exe.official-v135-20261002.bak` 为
`cockpit-cliproxy.exe`,先完成磁盘回滚。运行中的旧映像继续服务;只有在用户已
授权的维护窗口通过正式入口重载并核对进程映像后,运行回滚才完成。加载官方
备份后恢复其原有行为:无 Direct 闸门、无 Retry-After 等待、request-retry=1
放大与 buffering=true 慢吐字回归,按 v1.3.64 文档口径处理。Git 不参与本回滚
(产物不入库)。

只回滚 r3 的等待封顶(保留 r2 的其余修复)时,把
`cockpit-cliproxy.exe.v135-gate-r2-20261002.bak` 还原为 `cockpit-cliproxy.exe`
即可;若同时要恢复 120000 ms 的等待预算,还需把
`codex_local_access.json.bak-waitcap-20261002` 还原为 `codex_local_access.json`。
