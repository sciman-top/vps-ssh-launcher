# BWG wb 6004 模型 pin 增补与出口判别实验（2026-11-05）

## 范围

- 唯一持久远端写入：bwg `/opt/cliproxyapi/config.yaml` 的 `models_intl`，
  `deepseek-v4.1-flash` → `deepseek-v4.1-flash,deepseek-v4.1-flash-sg`
  （L1 模型分散：6004 模型级 ⇒ 第二个独立桶）。
- 实验性临时变更（**全部已回滚/拆除**）：CPA `proxy-url` 翻转/回滚
  （判别实验机械，same-inode）；bwg↔zz 一次性 ed25519 隧道键与
  `ssh -D -p 31854`；zz `/etc/ssh/sshd_config`
  `PubkeyAuthentication no→yes→no`。
- 未改凭据、账号池、并发、RPM、路由顺序、冷却参数。

## 事务与验证

- pin 写入：备份 `/root/wbpin-config-backup.yaml`；same-inode 单行替换，
  pre `dc5969f1fc31864e…` / post `0e943ac67fe8ceb2…`；watcher 热加载；
  `/v1/models` 广告校验 flash 与 flash-sg 均在列；flash-sg 8318 单发探针
  200/1.02s、SSE 5 事件 `[DONE]` 终态（PONG 分片跨事件，拼接完整）。
- **受控实战预演（flip→探针→revert）抓出两个设计缺陷并触发一次事故**：
  1. `ssh -D 127.0.0.1:18080` 绑宿主 loopback，**CPA 容器有自己的
     loopback**——容器内该地址无监听，代理连接秒失败被 CPA 渲染成
     200+`upstream_error`（假绿形态，时长 20ms 可判）。改绑 docker 网桥
     网关 `172.19.0.1:18080` 后宿主侧 socks 出口实测通（38.244.39.xx）。
  2. guard 表 input 链 `policy drop` 且无桥接口放行（仅 `iif lo accept`）
     ——容器→宿主网桥端口的 NEW 连接被静默丢弃（SYN 黑洞悬挂，探针 60s
     无输出）。解除需动 `firewall_baseline.ps1` lane，未实施。
  3. **事故**：翻转窗口内 config.yaml 被第三方写入者改动（写入者未归因，
     CPA 无 config 写回日志；15s 内 reload 计数 20 指示 watcher 抖动），
     字节计数回滚在被改写文件上截断，吃掉尾行
     `scheduler_priority_reclaim: true`（wb 粘性棘轮 0f0e33b 的承重键）。
     修复：由 pre-pin 备份 + pin 行重建字节级预期态，same-inode 写回，
     sha 精确回到 `0e943ac67fe8ceb2…`，尾行完整，CPA 重载。
- **投影欠账已收口（提交 `ff3c912`）**：初始放大 pin 后 strict doctor
  以 `client-model-catalog-contract=FAIL` 正确拦截未注册的 flash-sg。
  注册随后补齐到 `cpa_provider_routes.json` 的 model context 与 INTL
  plugin route、`cpa-admission.json` 的 WorkBuddy lane，以及 apply/doctor
  内嵌期望；执行 `-Apply` 后远端 manifest 与 admission 分别为
  `108324db…`、`18a2eda5…`，最终 config `2b6bc82c…`，
  `cpa-health readiness` 与 strict doctor 均通过。
- 本次 guarded apply 回执：`GUARDRAILS_APPLIED`、`READY_STATUS=200`，
  备份目录为
  `/root/cpa-guardrails-backup-20261105T000748.192732309Z`；Apply 在维护锁和
  `/run/cpa-admission/requests.lock` 独占排空下执行，未轮换凭据或清理冷却。
- 投影后公网 8443 随机路径认证目录：HTTP `200`、12 个模型，
  `deepseek-v4.1-flash` 与 `deepseek-v4.1-flash-sg` 均出现；随后 strict
  doctor 仍为 `MODEL_IDS_UNKNOWN=none`、`MODEL_CONTEXT_DRIFT=none`、
  `DOCTOR_CONTRACT_OK`，WorkBuddy lane `OPEN`。
- 投影后追加一次低频、零重试的 `deepseek-v4.1-flash-sg` 受控实战：
  HTTP 200、10 个 SSE 事件、完整终态、正文精确 `PONG`。
- 为验证旧 pin 未受影响，随后对 `deepseek-v4.1-flash` 做了一次同样的单发：
  HTTP `200` 但 `protocol_result=upstream_error`、`sse_last_event=other`，无
  capacity/6004 marker；请求后 lane 仍为 `hold=null,cooldown=false,inflight=0,pending=0`。
  紧接着自然日志中的同模型请求在约 37 秒后 `response.completed`，因此该单次是
  上游流内瞬时失败，不归因于本地 catalog/admission 修复，也未触发自动重试或清理冷却。
- 实验判据（IP 键 vs 登录面）未取；**config 翻转式设计就此废弃**
  （容器 loopback / guard drop / watcher 竞态三重危险），守望器未再
  拉起。未来如需判据：宿主侧直连探针（读凭据出站、零 CPA 参与、
  零 config 变更）是唯一安全设计。

## 事故（脱敏）

- 探针误用 `grep '"note"'` 于单行 auth JSON：整行（含 access/refresh
  token）进入会话输出（31.6KB）。落盘临时副本已当场删除；token 未进仓库与
  远端变更。后续一律 jq 白名单逐文件提取（runbook §4.2 已固化纪律）。
  建议下个维护窗对 wb 凭据执行 refresh/re-login 轮换 token。

## 回滚

- pin：恢复 `/root/wbpin-config-backup.yaml`（pre-pin 态）或反向单行替换，
  watcher 热加载；勿用 Git 回滚代替远端恢复。
- 实验面已清（2026-11-05 深夜）：bwg `/root/wbexp_*`/`wbsg_*`/`wbrepair*`
  与 `/root/.ssh/wb_probe_zz{,.pub}` 已删（仅留 pin 备份与 sha 记录），
  18080 隧道已停；zz authorized_keys 清空（原始态）、
  `PubkeyAuthentication no` 恢复（`sshd -t` + reload 验证）、临时备份
  已删。zz ssh_tool 密码登录全程未受影响。

## 边界

- flash-sg 在役只达单发探针与广告校验（host_loaded），长期质量/额度验收由
  自然流量观察；上游退役 id 时按 runbook 从 pin 移除。
- 共享桶限制键判据（IP vs 登录面）未定，不作为任何出口拆分建设的依据。
- `client-model-catalog-contract` 的中间态已通过提交 `ff3c912` 与受控
  `-Apply` 收口；当前 doctor 为 `DOCTOR_CONTRACT_OK`。

## 判别实验终局（2026-11-05 上午补记，用户授权烧积分后完成）

- **判据定案：6004 键 =（token，model）独立 24h 日窗，出口 IP（源/目标
  边缘）无关。** 同 token 跨 bwg/zz 出口同秒同 reset（
  `2026-11-06 08:21:28 UTC+8` 分毫不差）；跨两 CDN 边缘同秒；跨 token
  reset 相位互异（A=11-06 08:21、B=11-05 10:45、其余 11:25–11:47 +08）。
  11-04"共享桶"结论正式修正为"同一突发内各 token 日窗**相关开启**"；
  "加 intl 凭据不增容"撤回——每账号独立日窗，failover 真扩容；"没用过的
  账号也 6004"= CPA 全扇出把每个 token 打满各自的窗。
- 模型轴独立实证：凭据 A flash 在窗内同时刻 flash-sg 200；宿主侧验证
  `kimi-k2.8-preview` 200/2.34s/36 事件（第三 pin 候选，扩 pin 走标准
  投影路径）；`gpt-6-astra` ide 族凭据 400（仅 CLI 花名册）。
- flash-sg 受控实战验收：8318 链路 3×200（1.38/1.46/4.15s，SSE 完整
  6 completion/发）。
- 实验成本：~110 发小额探针（intl 免费池）；凭据 0faaff85 flash 日窗被
  并发突发打穿至 2026-11-06 08:21+08；其余 6 凭据当日 10:45–11:47+08
  自然释放；**CN 积分零动用**。
- 实验机械全拆除：bwg 探针/守望/矩阵脚本与一次性密钥已删、隧道已停；
  zz authorized_keys 清空还原、`PubkeyAuthentication no` 恢复（
  `sshd -t`+reload 验证）。`/root/wbpin-config-backup.yaml` 与
  `/root/wbpin-config-{before,after}.sha` 按 pin 回滚契约保留。
- 探针线形状、两机 DNS 黑洞钉 IP 法、`grep -c` 双打印坑等可复用细节已
  固化 runbook §4.2；CPA 内 `proxy-url` 翻转式设计维持废弃。

## kimi-k2.8-preview 第三模型 pin 落地（2026-11-05 上午，同窗续作）

- 判据定案后按"模型轴独立"结论扩容：models_intl 三模型
  `deepseek-v4.1-flash,deepseek-v4.1-flash-sg,kimi-k2.8-preview`（每凭据
  第三条独立日窗）。上下文保守钉 1000000（models.dev 报 1048576；
  `gpt-6-astra` 在 ide 族凭据 400=仅 CLI 花名册，未注册）。
- 仓库面（`13f0c39`，镜像 ff3c912 的 flash-sg 模式）：admission lane
  models、route manifest（provider 元数据+intl plugin route）、
  guardrail doctor/apply lane 表、catalog/health/guardrails 测试期望。
  focused tests 394 passed/180 subtests。
- 远端面：config.yaml same-inode pin 编辑（备份
  `/root/wbkimi-config-backup.yaml`、前后 sha
  `2b6bc82c7e0e4cc1…/3c90a7f20dbc31b9…`、watcher 热加载 9 reload）；
  `-Apply` `GUARDRAILS_APPLIED` 投影 manifest；strict doctor
  `DOCTOR_CONTRACT_OK`、`MODEL_CONTEXT_DRIFT=none`（kimi:1000000 在列）、
  `/v1/models` 广告含 kimi 与 flash-sg。
- 受控实战验收：8318 全链单发×2——200/7.25s/29 事件/[DONE] 终态；
  200/2.64s 且 delta content 拼接可见文本精确 `PONG`。
- 回滚：config 恢复 `/root/wbkimi-config-backup.yaml`（或反向单行替换）
  + 从 manifest/admission/期望表移除 kimi 并重 `-Apply`；Git 回滚不代替
  远端恢复。
