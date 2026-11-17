# BWG CPA workbuddy developer-role 11128 修复（2026-11-17/18 夜）

## 症状

ChatGPT desktop 经 CPA 调 `deepseek-v4.1-flash` 报
`stream disconnected before completion: ... Upstream status=500`。容器日志：
wb 上游 `11128 "Illegal API invocation from an unapproved channel"`（HTTP
400）被包装为 500；同波次命中 luode 双插件线（ai 线 20:50 首例、provider 线
21:35 起），跨凭据复现。codex 桌面 deepseek 全灭，其余客户端/车道正常。

## 根因链（控制探针逐层钉死）

1. wb 上游（copilot.tencent.com /v2/chat/completions）自 2026-11-17
   ~20:50+08 起**拒绝 OpenAI `developer` 角色**：同内容对照
   `role:"developer"` 必 11128、`role:"system"` 200（00:29+08 实测）。
2. Codex Desktop 0.169.0-alpha 把桌面系统提示词作为 developer 角色 input
   item（responses 首条 17.7KB）随请求发送；responses→chat 翻译原样透传 →
   仅 codex 流量中招。
3. 途中被证伪的假设（留痕防复发）：①"trio 日配额烧尽/短窗限流"（agy 车道，
   用量≈0 + 次日解除）；②"按凭据标记"（切主选后 codex 照样 11128）；
   ③"codex 客户端指纹"（端点/UA/originator/session_id/富载荷/会话三头/
   工具名/大体量九维对照探针全 200）。
4. 会话亲和放大器：面板主选（active_auth）钉在被波及凭据上时，新会话全部
   绑定它反复撞墙；亲和 rebind 只认 quota 类，11128/500 不换凭据。

## 修复

- **线上急救（00:33+08）**：config.yaml 保 inode 原地插入
  `requests.payload.override` 三条
  `messages.#(role=="developer")#.role: system`（deepseek-v4.1-flash /
  -intl / -sg，`from-protocol: responses` 门控），快照
  `config.manual-snapshot-20261117T163312Z-pre-wb-dev-role-override.yaml`；
  responses-wire developer 探针 deepseek/zhipu 双 200（zhipu 为既有规则
  对照）。热加载即时生效。
- **仓库化（c00ee8e）**：manifest `workbuddy_developer_role_override`
  + cpa-guardrail-apply.sh / cpa_policy.py 双侧派生 + 语义门 exact-match
  fail-closed + test_cpa_policy_runtime / test_cpa_updater_apply 适配；镜像
  zhipu 1214 修复 f2cad18 的受上游认可模式。Full gates `-RunParallel` 绿
  （1722 passed / 820 subtests）。
- **投影固化**：`GUARDRAILS_APPLIED` 从 manifest 重生成与手工版一致的块；
  strict doctor `DOCTOR_CONTRACT_OK`；全量 CI 绿；9b2fc9c 落 runbook 终态。
- **受控实战验收**：用户真机 codex deepseek 恢复（PASS）。

## 伴生处置（同夜，独立定性）

- **主选切换（00:06+08）**：`POST /v0/management/plugins/workbuddy-provider/
  select` 把 active_auth 从 workbuddy-072197f7… 切到 workbuddy-eb624ce3…
  （sciman逸居），快照
  `config.manual-snapshot-20261117T160612Z-pre-active-auth-switch.yaml`。
  当时是为验证"凭据标记"假设；**假设证伪，且 -Apply 的插件重启已把主选重置
  回 072197f7（01:4x 复核观察）**——根因修复后主选身份与触发无关（用户
  codex 验收即在重置后的主选上通过），此处置整体作废，无回滚义务。
- **agy 车道（与 wb 无关的同夜现象）**：gemini-3.8-flash-high 对凭据子集
  裸 429 RESOURCE_EXHAUSTED（用量≈0），2026-11-18 01:33+08 采样确认已自行
  解除；但 flash-high→flash-n 降级替换**扩散为全池**（6 凭据 auth_index 全
  替换）=上游把 -high 档整体降级，换凭据不可取回；`<system-conventions>`
  内容触发（oh-my-pi #12029 机制）经带/不带对照判否。质量敏感流量用
  `claude-sonnet-5-5-high`。详见 cpa-gateway runbook agy 节与
  outputs/probe-agy-flash-sample-20261118.ps1（563a435）。
- **验证姿势陷阱（复用）**：stream:true 的 curl HTTP 状态码不可信（流内
  block 仍 200）；payload 规则带 from-protocol 门时必须在 responses wire
  上验证；`--data-binary` 带文件必须 `@`；ssh_tool 60s 空闲超时——长间隔
  探针走 outputs/*.ps1 + Invoke-LauncherRemoteCommand。

## 回滚

- 线上：两份 config 快照（backups/ 下 pre-active-auth-switch /
  pre-wb-dev-role-override）原地恢复；主选 select 回切。
- 仓库：`git revert c00ee8e` 后重跑 `-Apply`（语义门会先 RED 失败——
  manifest 摘除后 live config 带规则即 exact-match 红，属 fail-closed 设计，
  revert -Apply 链一致）。

## 遗留

- luode 插件 channel-level 日志行把完整 bearer JWT 打进 `auth=` 字段（日志
  卫生缺陷）；目标仓 Issues 已禁用（410），反馈草稿在
  outputs/draft-luode-issue-wb-11128-developer-role-20261118.md，待经作者
  其他渠道转达。
- agy flash-n 全池替换为上游档位政策状态，doctor
  `==model-substitution-by-credential==` 常驻观察。

ACCEPTANCE_RESULT=PASS
