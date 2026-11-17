# luode 插件反馈草稿（2026-11-18 落盘；目标仓 luode0320/cpa-workbuddy-plugin 的 Issues 已被作者禁用，GitHub 渠道不可用，请经其他联系渠道转达）

## wb 上游 2026-11-17 起拒绝 OpenAI developer 角色回 11128（实测定位）

**时间线**：2026-11-17 ~20:50+08 起，CodeBuddy 网关（copilot.tencent.com /v2/chat/completions）开始对含 `role:"developer"` 消息的请求返回 `11128 Illegal API invocation from an unapproved channel`（HTTP 400，displayMsg 仅泛化的 "Request blocked. Please send it again"）。CPA 宿主把它包装成 500，客户端侧表现为 stream disconnected。

**判别实验**（同内容对照，loopback /v1/responses 经网关翻译）：
- `role:"developer"` 一条消息 → 上游 11128（必现）
- 同内容 `role:"system"` → 200
- 凭据身份/端点形状/流式/客户端头族均被对照探针排除，唯一触发面是消息角色。

**影响路径**：Codex Desktop 0.169.0-alpha 把桌面系统提示词作为 developer 角色 input item 随 responses 请求发送（17.7KB 首条），responses→chat 翻译原样透传，因此仅 codex/desktop 类流量中招，其他客户端不受影响。

**网关级缓解（已验证生效）**：CLIProxyAPI `requests.payload.override` 加 `messages.#(role=="developer")#.role: system`（from-protocol: responses 门控），与 zhipu 车道 1214 的处置同构（CLIProxyAPI #7202 曾确认该 knob 为受认可方案）。应用后 codex→wb 流量恢复。

**两个问题**：
1. 是否有其他用户同期遇到 11128？官方错误码文档未收录该码，displayMsg 也不分子条件，定位成本很高。
2. 插件是否考虑在 workbuddy 侧内建 developer→system 归一化（或在文档提示用户配置 override）？对不支持该角色的上游更稳，也能保护不会配网关层的用户。

## 日志卫生缺陷（建议修复）

插件在 channel-level block 日志里把完整 bearer JWT 打进了 `auth=` 字段（base64 解码可见 email/uid/sid 等 claims）。建议改打 auth_index 或掩码。

**环境**：workbuddy-provider 0.21.27 + workbuddy-ai-provider 0.1.40，CLIProxyAPI v8.0.75（fork 镜像）。
