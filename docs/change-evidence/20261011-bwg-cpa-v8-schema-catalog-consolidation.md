# 2026-10-11 bwg CPA v8 schema 迁移与目录收敛（0890bf5 / 6d88f5d→2faf626 amend / b413e32）

## 背景与事故定性

用户当天 12:11–12:16（北京）经 CPA v8 管理 API（源 172.19.0.1=宿主本机，持明文
mgmt key）实时完成大部分目录变更。CPA v8 写路径把 `/opt/cliproxyapi/config.yaml`
迁移为 v8 布局（`config-version: 8`，mtime 2026-10-11T04:16:11Z）。仓库工具链
（policy/doctor/health key 提取、updater readiness）只认 legacy schema，当天出现：

- doctor 四处 FAIL：semantic-policy（全字段 None）/ client-model-catalog（key 提取
  失败 401）/ management-remote / heartbeat-cpa-update；
- 每日 updater（04:16:59Z）readiness DEFER exit 1；
- `-Apply` 在首个 legacy 键校验处 fail-closed REFUSE（无损，回滚会重启容器；
  v8 文件是 CPA 自写，重启加载安全）。

容器全程靠内存配置继续服务，数据面无中断。用户决策走**方案 b：全家族迁 v8 原生
schema**（含刷新参考源码授权；`_shared` 外置根 ACL 只读，改用上游 v8.0.23 tag
tarball 只读消费，`.git` 与 references.manifest.json 均 DENY 写，消费决定记录于本文件）。

## schema 锚点（v8.0.23 源码 internal/config/config_v8.go buildV8Paths）

host/port→server.*；remote-management→management；api-keys→access.api-keys；
request-retry→routing.retry.request-retry；disable-cooling/save-cooldown-status/
transient-error-cooldown-seconds→routing.cooldown.*；force-model-prefix→
routing.force-model-prefix；oauth-excluded-models→oauth.excluded-models；
codex.*→upstream.codex.*；quota-exceeded.antigravity-credits→
oauth.providers.antigravity.antigravity-credits；logs/usage→observability.*；
顶层 api-keys 变为渠道族映射（api-keys.openai-compatibility 组条目
{base-url, keys:[{api-key}], models, headers...}，运行时经 flattenV8 展开回
api-key-entries）；routing 与 quota-exceeded（减 antigravity-credits）顶层共享。

## 变更（三个提交，均 full gates 550 passed，已推 origin/main）

1. **0890bf5 目录收敛（真源）**：slot2=opencode.ai/zen/go/v1（deepseek-v4.1-flash、
   muse-spark-1.3-contributor 裸名）；slot1 收敛 astra+sol-input；slot3 只留
   gpt-5.6-terra；slot5(api.deepseek.com) 退役→admission.json 删 deepseek-official
   lane（policy/apply/doctor 预言机同步）；retired_hosts+=codex.ciii.club、
   api.deepseek.com（旧别名留墓碑）；health 基线元组与缓存金丝雀默认改
   glm-5.3-flash；cpa-admission.py 零字节改动（integrity pin 5273f59e 不轮换）。
2. **6d88f5d v8 原生工具链**（amend 含 596 行 KeyError 修复）：apply/policy/doctor/
   health/quarantine/deactivate/acceptance 全部按上表路径改造；KEY 提取
   access.api-keys；allowed blocks 守卫收敛 {routing, upstream, observability,
   oauth, api-keys}。教训：apply 内嵌 python 此前只有文本断言测试，
   `CONFIG_POLICY_READY` 打印行的 legacy 键直取漏网——首次 -Apply
   KeyError rollback（config.yaml 恢复 sha 一致，ROLLBACK_FAILED 为旧 health
   验证假阴性）；后续补全事务级 fixture 执行测试为待办。
3. **b413e32 slot2 会话头受控豁免**：实测 zen go 端点生成必须带
   `x-opencode-session`（缺则 400 MissingSessionID）。manifest slot2 钉
   `{x-opencode-session: cpa-bwg-gateway-01}`，apply/policy 对称强制
   （缺失/不符/未声明槽位均拒），是 2026-09-18 传输键禁令的唯一受控豁免。

## 投影与验收（guardrails -Apply × 3：1 失败回滚 + 2 成功）

- 最终成功 apply：备份 `/root/cpa-guardrails-backup-20261011T061230.837523493Z`，
  `GUARDRAILS_APPLIED`、`READY_STATUS=200`、models=10 与目标清单逐项一致、
  全部退役别名 False、投影 12×MATCH、`DOCTOR_CONTRACT_OK`（exit=0）、
  `MODEL_IDS_UNKNOWN=none`、`catalog_oauth_missing=none`、admission-integrity
  全 MATCH。
- updater 手动触发一次：`Result=success`、`HEALTH_OK`、
  `OK: no newer mature release; current=v8.0.23 readiness verified`——心跳转绿。
- 定向单发实测（127.0.0.1:8318 全链，一发不重试）：
  - `deepseek-v4.1-flash`：**LIVE_ACCEPTED**（200 / finish=stop / 回显模型名正确 /
    content==OK）。
  - `muse-spark-1.3-contributor`：400 `server_error "This Go model trains on
    request data. Allow paid endpoints that train on request data in your
    workspace's Privacy settings"`——**opencode 工作区隐私开关，账号侧，待用户
    开启**；目录已注册（readiness 绿），生成在该开关开启前不可用。

## 用户需知的残余事实

- opencode zen `go` 端点从用户本机网络（CN 出口）对 muse 报 RegionError，VPS
  （美国出口）无此限制；CPA 经 VPS 出口不受影响。
- `zen/v1`（非 go）路径为另一计费通道（Insufficient account funds），勿迁。
- deepseek 官方渠道与 DS 缓存基线随本次退役；GLM 缓存基线（0.9956）不变。
- refs：CLIProxyAPI v8.0.23 tarball
  `https://github.com/router-for-me/CLIProxyAPI/archive/refs/tags/v8.0.23.tar.gz`
  （sha256 见下载缓存 `~/.zcode/tmp/cpa-v8023/`，MIT，只读）。
