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
