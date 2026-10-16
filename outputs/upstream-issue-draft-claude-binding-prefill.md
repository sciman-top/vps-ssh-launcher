# Upstream issue draft (router-for-me/CLIProxyAPI)

> 草稿供 review 后手动提交（外部发布需用户执行）。英文正文如下。

---

**Title**: Claude executor: haiku-5-5 rejects assistant prefill and binding-validates replayed thinking blocks — Responses→Claude relayed sessions 400 on reconnect/continue turns

**Body**:

## Environment

- CLIProxyAPI v8.0.30/v8.0.31 (docker), upstream = custom Claude `claude-api-key` base URL (Anthropic Messages face)
- Client: OpenAI Codex Desktop 0.163.0-alpha.10.2 → `/v1/responses` (store=false, `include=["reasoning.encrypted_content"]`), model claude-haiku-5-5, effort xhigh → translated via `internal/translator/claude/openai/responses` → ClaudeExecutor

## Two 400 families (upstream `invalid_request_error`, measured 2026-10-16)

1. **Thinking-block binding**: on any turn whose request history was reshaped
   (stream-reconnect resend, "continue" turn, post-`web_search` server-tool
   round-trip), replayed thinking blocks fail upstream validation:

   ```
   messages.1.content.0: Invalid `signature` in `thinking` block. The block is
   bound to a different conversation. ... The `tools` list differs from the one
   this block was created with.
   ```

   Measured contributors: a resume turn sent with **0 tools** while the blocks
   were created under 48; the reconstructed `server_tool_use`/
   `web_search_tool_result` blocks are not byte-faithful to creation time.

2. **Assistant prefill**: reconnect resends end with an assistant message →

   ```
   This model does not support assistant message prefill. The conversation must
   end with a user message.
   ```

   `claudeModelRejectsAssistantPrefill` (claude_openai-responses_request.go)
   covers fable / opus>=5 / sonnet>=4.6 but returns false for **haiku**, which
   this model rejects.

## Working config-level mitigation (verified live, same stale block 400 → 200)

`requests.payload` rules scoped to the model:

- override (gated on `thinking.type` existing):
  `thinking.block_binding.prefix_mismatch_behavior=drop_block` + body key
  `betas=["thinking-binding-controls-2026-08-01"]` (ClaudeExecutor's
  `extractAndRemoveBetas` converts it to the `anthropic-beta` header);
- conditional filter: match `messages.@reverse.0.role == "assistant"` → delete
  `messages.-1` (sjson supports -1 delete; gjson read needs @reverse).

Gotcha worth documenting: the injection **must** be gated on `thinking.type` —
otherwise sjson synthesizes a bare `thinking.block_binding` object on
thinking-less requests and upstream answers `400 thinking.type: Field required`
for all of them.

## Requested upstream fix

1. Add haiku (or all adaptive-thinking families) to
   `claudeModelRejectsAssistantPrefill`, and/or synthesize a user continuation
   for assistant-ending input instead of dropping it.
2. For Responses→Claude relayed sessions, auto-inject
   `thinking.block_binding.prefix_mismatch_behavior=drop_block` + the
   `thinking-binding-controls-2026-08-01` beta (that beta exists exactly for
   proxies that cannot guarantee byte-faithful replay), or expose a
   per-credential opt-in knob.

Happy to provide sanitized error dumps and probe scripts.
