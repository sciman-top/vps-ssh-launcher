<!-- 提交记录：2026-10-17 由 sciman-top 提交为 router-for-me/CLIProxyAPI#6692
     https://github.com/router-for-me/CLIProxyAPI/issues/6692
     标题：[Bug] Responses-to-Claude relayed sessions 400: haiku-5-5 missing from
     the prefill-reject list, and replayed thinking blocks fail binding validation
     该仓不允许外部贡献者设置 label，故 issue 无标签。
     以下为实际提交正文，按该仓 .github/ISSUE_TEMPLATE/bug_report.md 结构。 -->

**Is it a request payload issue?**
[x] Yes, this is a request payload issue. I am using a client/cURL to send a request payload, but I received an unexpected error.

**Describe the bug**

Any relayed `Responses → Claude` session starts returning 400 as soon as the client resends a reshaped history — stream reconnect, a "continue" turn, or the turn after a server-side tool round-trip. Two independent causes, both reproduced against a custom `claude-api-key` base URL (Anthropic Messages face) on v8.0.30 and on v8.0.31 (still failing on v8.0.31):

**1. Trailing assistant prefill is not dropped for `claude-haiku-5-5`.**

```
400 invalid_request_error
This model does not support assistant message prefill. The conversation must end with a user message.
```

`claudeModelRejectsAssistantPrefill` (`claude_openai-responses_request.go`) covers fable / opus>=5 / sonnet>=4.6 and returns false for haiku, so the trailing assistant message is forwarded upstream. This is the same defect class as #5279 and #5319 — the model predicate is incomplete again. Note that #5319 measured `claude-haiku-4-5` as *accepting* prefill; `claude-haiku-5-5` rejects it, so a substring check on the older family name will not cover the new generation.

**2. Replayed thinking blocks fail Anthropic's creation-time binding validation.**

```
400 invalid_request_error
messages.1.content.0: Invalid `signature` in `thinking` block. The block is bound to a different conversation.
  variant: The `tools` list differs from the one this block was created with.
  variant: The content before this block was not present.
```

Anthropic validates the signature not only cryptographically but against the conversation prefix and the tool list at creation time. A relayed session breaks that by construction: the reconnect resend was observed going out with **0 tools** while the blocks were created under **48**, and the reconstructed `server_tool_use` / `web_search_tool_result` blocks are not byte-faithful to creation time. Any client that replays history therefore fails, which for Codex is every reconnect/continue path.

**CLI Type**
`claude` (managed API-key group, custom Anthropic Messages base URL)

**Model Name**
`claude-haiku-5-5`

**LLM Client**
OpenAI Codex Desktop 0.163.0-alpha.10.2 → `POST /v1/responses` (`store=false`, `include=["reasoning.encrypted_content"]`, effort `xhigh`), translated by `internal/translator/claude/openai/responses` into the Claude executor.

**Request Information**

The reproducer shape below is **reconstructed from the failing turns, not a captured wire payload** — trimmed to the discriminating fields. Say the word and I will attach a sanitized capture taken with `request-log: true` in `config.yaml`.

Trigger 1 — history ending on an assistant item:

```json
{
  "model": "claude-haiku-5-5",
  "stream": true,
  "input": [
    {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]},
    {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "hello"}]}
  ]
}
```

Trigger 2 — replay of a signed thinking block under a tool list different from creation time:

```json
{
  "model": "claude-haiku-5-5",
  "stream": true,
  "tools": [],
  "input": [
    {"type": "reasoning", "encrypted_content": "<block created under a different tools list>"},
    {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "continue"}]}
  ]
}
```

**Expected behavior**

A relayed Responses session should not be killed by transport-level reshaping of history:

1. Add `claude-haiku-5-5` to `claudeModelRejectsAssistantPrefill` — or better, drive the predicate off an explicit capability list rather than substring matching — and/or synthesize a user continuation for assistant-ending input instead of forwarding it.
2. For relayed sessions that cannot guarantee byte-faithful replay, inject `thinking.block_binding.prefix_mismatch_behavior=drop_block` plus the `thinking-binding-controls-2026-08-01` beta (that beta exists exactly for this case), or expose a per-credential opt-in knob. The executor does not apply that beta today.

**OS Type**
 - OS: Windows (client) / Linux container (gateway)
 - Version: Codex Desktop 0.163.0-alpha.10.2

**Additional context**

Working config-level mitigation, in case it helps other relayed deployments — verified live on the same stale block (400 without → 200 with): a `requests.payload` override scoped to the model that sets `thinking.block_binding.prefix_mismatch_behavior=drop_block` plus the body key `betas=["thinking-binding-controls-2026-08-01"]`, and a conditional filter deleting the trailing assistant message (match `messages.@reverse.0.role == "assistant"` → delete `messages.-1`).

Two gotchas worth documenting regardless of how this gets fixed:

- The override **must** be gated on `thinking.type` already existing; otherwise sjson synthesizes a bare `thinking.block_binding` object on thinking-less requests, and every such request comes back `400 thinking.type: Field required`.
- `gjson` has no negative index, so the trailing-role match has to read through `@reverse`; the delete still uses sjson's documented `messages.-1`.

Happy to provide sanitized error dumps and the probe script.
