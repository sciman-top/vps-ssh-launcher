# 上下文长度与吐字速度：可调与不可调（2026-11-08 审查）

客户端的上下文窗口是**首字延迟的主导变量**，也是唯一由调用方控制的变量。本页记录
一次实测拆解与结论，避免下次再从"网关慢"开始排查。

## 1. 实测拆解

2026-11-08 只读复核，24h 窗口：

| 指标 | 数值 | 来源 |
|---|---|---|
| admission 排队等待 `waited_ms` | p50 0–2 ms，p90 2–18 ms | doctor `==admission-latency==` |
| 上游响应头 `upstream_header_ms` | 全部车道 p50 2.6 s / p90 9.1 s | 同上 |
| 上游首字节 `upstream_first_byte_ms` | p50 3.5 s / p90 11.8 s | 同上 |
| 首个可见文本 `first_text_ms`（**仅可测的 9%**） | p50 4.1 s / p90 18.8 s | 同上，2026-11-08 重新拆分 |
| 单请求输入 token | 每轮新增约 200，历史前缀约 108k | `usage_in`/`usage_cached` |
| 单请求输出 token（`gpt-6.1-sol`） | 130 次中 77 次仅 16 token，但**平均输入仅 53 token** | 同上（2026-11-08 更正） |

结论：网关自身几乎不排队（p90 个位毫秒），**首字延迟 99% 花在上游处理一段
10 万级 token 的前缀上**。吐字慢不是网关复制、缓冲或排队造成的；`stream-bootstrap-buffering=false`、
Nginx `proxy_buffering off`、CPA 15 s SSE keep-alive 与 admission 早头+心跳都已就位。

### 1.1 `first_text_ms` 的分母陷阱（2026-11-08 修正）

上表的 `first_text_ms` 只有在**这一轮真的会产出可见文本**时才存在。24h 实测：
9739 条 `upstream_result` 中只有 **908 条（9%）** 能得到该样本 —— 非 SSE 请求与
纯推理轮（`text_finish=reasoning_only`）在构造上就不会有「第一个字」。
把两种总体混在一起算 p90，等于用一堆**永远不会有首个字**的轮次去回答「首字多慢」。

doctor 现已拆成 `first_text_ms_text_turns`（有数值的可见文本样本）、
`first_text_ms_no_text_turns`（工具/推理/空文本/非 SSE 等结构上无文字的轮次）
与 `first_text_ms_unknown_turns`（无法分类的缺失），并输出
`first_text_ms_measurable_share_pct` 声明支持度。拆分后各车道差异极大：

| 车道 | `first_text_ms` p50 | p90 |
|---|---|---|
| `workbuddy-oauth/deepseek-v4.1-flash` | 3.3 s | **4.2 s** |
| `workbuddy-oauth/deepseek-v4.1-flash-sg` | 3.8 s | 5.5 s |
| `zhipu-coding-plan/glm-5.3-flash` | 12.2 s | 33.7 s |
| `chatgpt-oauth/gpt-6.1-sol` | 3.3 s | 10.1 s |
| `chatgpt-oauth/gpt-6.1-terra` | 8.0 s | 16.4 s |
| `chatgpt-oauth/gpt-6-luna` | 10.0 s | **43.0 s** |
| `antigravity-oauth/gemini-3.8-flash-high` | 9.7 s | 25.3 s |

**「吐字慢」不是全局现象，是分车道的**：WorkBuddy 是 4 秒级，ChatGPT/GLM 是
30–43 秒级。此前用单一 p90 汇报，既掩盖了这个 10 倍差距，也让人无法判断该动哪条
车道 —— 而真实杠杆是**该车道的上下文长度**，不是网关。

## 2. 关键推论：上下文越长，吐字越慢，而且是指数级的

推理模型的首 token 来自整段前缀的 prefill。前缀从 2 万涨到 10 万，prefill 成本
近似线性增长，但注意力计算的二次项与批处理竞争会让实测出现超线性；同时长上下文
更容易撞上 `server_is_overloaded` / `selected model is at capacity`，于是出现
"等很久 → 429 → 重试 → 再等"的恶性循环。**这类慢不是故障，是容量**。

## 3. 客户端可做的（按收益排序）

1. **新任务开新会话**：唯一能把 10 万级前缀降回千级的操作。
2. **只在确实需要时压缩/compact**：压缩本身会让缓存前缀失效并重算；频繁自动压缩
   比不压缩更慢。
3. **把大文件检索交给工具**：让模型按需读取片段，而不是整文件进上下文。
4. **别为"小产出"做优化**（2026-11-08 更正）。本栈实测那 77 次 16-token 调用的
   平均输入只有 53 token，是轻量探活/标题类请求，几乎不占预算；旧文称其"消耗
   10 万级输入"是把两个不同请求集合的数字相除，**已作废**。真正吃掉预算的仍是
   长上下文会话本身，对应第 1、3 条。
5. **不要**通过改用更轻的模型/别名来"提速"：那改变的是生成质量与账号画像，
   不是延迟，且会让 A/B 结论失效。

## 4. 为什么"压缩上下文"不能由本仓代做（2026-11-08 结论）

实测的缓存收益（2026-11-08 修复分母后：各 OAuth 车道 **90–99%** 命中，
WorkBuddy 99%；修复前误报 45–49%，见 [cpa-gateway](cpa-gateway.md) 的更正段）
来自一个事实：**每轮请求的前缀逐字节稳定**，所以每轮只新增约 200 token。
任何中间层改写 prompt 前缀（截断旧消息、重排 system/tools、注入摘要）都会
改变缓存键，命中率直接归零，单账号每分钟 token 消耗上升一个数量级——那才是
加速触发上游限流的自伤动作。admission 因此坚持**不透明转发**，并由
`-` 一致性测试钉住"请求体不改写"。

**推论被加强了，不是削弱**：既然命中率已在 90–99%，"为了提升命中率而改写前缀"
的收益上界不足 10%，而风险是把 90% 打到 0%。这个交换在任何情况下都不划算，
所以本仓永久拒绝该类改动，不再逐次评估。

真正能承压缩的是官方 API 的 `previous_response_id` / 服务端会话（会在服务端
保留前缀并重新计费）；消费级 OAuth 通道没有等价契约，不应模拟。

## 5. 已否决的"优化"

| 候选 | 否决理由 |
|---|---|
| CPA 开启 `routing.strategy: fill-first` 换取"更快命中缓存" | 该策略会把新请求钉在池内第一个就绪账号，与 2026-11-06 记录的"单账号过载"形态相同；缓存局部性已由 session-affinity 保住 |
| 提高 OAuth lane `max_inflight` 以"并行吐字" | 并发不改变单个请求的 prefill 时间，只增加同账号瞬时压力 |
| 给 WorkBuddy/Codex 开 `support-prompt-cache-key` | 该键只为 openai-compat provider 派生；Codex 执行器本就用 `prompt_cache_key` / session 派生键，开启后无收益，却会改变发往上游的请求形态 |
| 调大 `queue_timeout` 或 Nginx `read_timeout` | 排队不发生在 admission；调大只会拉长最坏等待 |
| 上游 `v8.0.60` 之外的本地补丁 | 上游已有正式修复（缓存前缀保持、连接归还），本地打补丁会与下次换芯冲突 |
