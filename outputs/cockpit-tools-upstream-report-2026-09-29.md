# Upstream report draft — Cockpit Tools sidecar buffers the whole Responses SSE stream

**Target repo**: `cockpit-tools`（含 `sidecars/cockpit-cliproxy`，即 CLIProxyAPI 的 Cockpit fork）
**Checked against**: `v1.3.57-7-gdbe56a1e`
**Prepared**: 2026-09-29
**Component**: `sidecars/cockpit-cliproxy/provider_gateway.go`

---

## Title

`writeProviderGatewayResponsesStream` never flushes, so `wireApi: responses`
provider-gateway streams are delivered as one buffered burst at the end

---

## Summary

When a provider gateway is configured with `wireApi: "responses"`, the streaming
passthrough writes each SSE line to `c.Writer` but **never calls `Flusher.Flush()`**.
`gin`'s `c.Status()` only records the status code, and `c.Writer.Write` lands in
`net/http`'s internal `bufio` writer, so nothing reaches the client until the
buffer fills or the handler returns.

The observable effect: a client talking to the local sidecar sees **no response
headers and no body for the entire generation**, then everything arrives at once.
It looks like "tokens trickle out slowly / the model is very slow", but the
upstream was never slow.

The sibling functions in the same file already do this correctly, which suggests
this one was simply missed:

| function | lines | flushes? |
|---|---|---|
| `writeProviderGatewayChatStream` | 501–593 | ✅ 546 / 552 / 574 |
| `writeProviderGatewayTranslatedChatStream` | 595–635 | ✅ 625 / 630 |
| **`writeProviderGatewayResponsesStream`** | **636–652** | **❌ none** |

---

## Impact

Any user whose provider gateway uses `wireApi: "responses"` (the natural choice for
a Codex / Responses upstream) gets a **non-streaming** experience regardless of how
correctly the upstream streams:

- short replies (smaller than the `bufio` size): the client receives nothing at all
  until generation finishes;
- longer replies: data arrives in buffer-sized bursts instead of per event.

It also removes the point of `manifest.immediateSseResponse` for this path
(that flag is only consulted in `handleStream`, not in
`handleProviderGatewayRequest`).

---

## Root cause

`sidecars/cockpit-cliproxy/provider_gateway.go:636-652`

```go
func (s *relayServer) writeProviderGatewayResponsesStream(c *gin.Context, body io.Reader) {
	if body == nil {
		return
	}
	reader := bufio.NewReaderSize(body, 64*1024)
	for {
		line, err := reader.ReadBytes('\n')
		if len(line) > 0 {
			if _, writeErr := c.Writer.Write(normalizeResponsesReasoningContentSSELine(line)); writeErr != nil {
				return
			}
		}
		if err != nil {
			return
		}
	}
}
```

Call chain: `provider_gateway.go:113` / `:124` → `handleProviderGatewayRequest`
→ `provider_gateway.go:401-402`:

```go
c.Status(http.StatusOK)
s.writeProviderGatewayResponsesStream(c, resp.Body)
```

`resp` comes from `http.DefaultClient.Do(req)` (line 361), i.e. the upstream
response headers are already in hand — the delay is entirely downstream of that.

---

## Reproduction

Environment: ChatGPT desktop → Cockpit local sidecar (`cockpit-cliproxy.exe`) →
HTTPS to a remote CLIProxyAPI-compatible gateway.

Two probe scripts (stdlib only, no credentials printed) are attached in
`outputs/` of the reporting project:

- `sse_framing_probe.py` — POSTs one streaming `/v1/responses` turn and reports
  `headers_ms`, `socket_reads`, `read_gap_p50`, plus SSE event/delta counts.
- `stream_local_chain_probe.py` — same measurement across sidecar / public-direct /
  via-proxy hops.

Run against the sidecar and, for comparison, directly against the same upstream
gateway (bypassing the sidecar):

| target | headers_ms | total_ms | bytes | socket_reads | read_gap_p50 |
|---|---|---|---|---|---|
| local sidecar (`wireApi: responses`) | **22507** | 23038 | 10863 | **8** | **0 ms** |
| same upstream, direct | **2220** | 13672 | **10863** | **21** | 12 ms |

Identical payload size (10863 bytes), identical SSE content
(15 × `response.output_text.delta`, same 8 event types) — **only the delivery
differs**. Repeating the sidecar measurement 7× gave the same signature every
time (`headers_ms ≈ total_ms`, few socket reads, `read_gap_p50 = 0`), including one
run where a 3145-byte body arrived with `headers_ms == total_ms == 11752`.

Cross-check on the upstream side: the gateway's own access log records
`upstream_header_time` of **0.531 / 0.561 / 1.262 / 1.691 / 1.976 s** for exactly
those requests. So the upstream committed headers in under 2 s while the sidecar
held them for 10–22 s.

### A workaround that does *not* work

Switching the provider gateway to `wireApi: "chat_completions"` (which does flush)
was tested on an isolated sidecar instance on a scratch port. It does not help,
and is worse at the gateway:

```
route=chat status=200 request_time=11.716 upstream_time=11.549 upstream_header_time=10.685
route=chat status=200 request_time=16.320 upstream_time=16.319 upstream_header_time=14.613
```

Chat Completions renders no handshake events, so the upstream proxy legitimately
holds its headers until the first generated token — a documented trade-off of that
format. The `responses` wire API is the right one; the flush is what's missing.

---

## Proposed patch

```diff
--- a/sidecars/cockpit-cliproxy/provider_gateway.go
+++ b/sidecars/cockpit-cliproxy/provider_gateway.go
@@ -637,6 +637,11 @@
 	if body == nil {
 		return
 	}
+	flusher, ok := c.Writer.(http.Flusher)
+	if !ok {
+		writeAPIError(c, http.StatusInternalServerError, "streaming not supported", "streaming_not_supported")
+		return
+	}
 	reader := bufio.NewReaderSize(body, 64*1024)
 	for {
 		line, err := reader.ReadBytes('\n')
@@ -644,8 +649,14 @@
 			if _, writeErr := c.Writer.Write(normalizeResponsesReasoningContentSSELine(line)); writeErr != nil {
 				return
 			}
+			// SSE events are terminated by a blank line; flush there so the client
+			// sees each event as it arrives instead of one bufio burst at the end.
+			if len(bytes.TrimRight(line, "\r\n")) == 0 {
+				flusher.Flush()
+			}
 		}
 		if err != nil {
+			flusher.Flush()
 			return
 		}
 	}
```

`bytes` and `net/http` are already imported in this file, so no import changes.
Flushing on the blank-line event boundary (rather than per line) mirrors the
behaviour of `writeProviderGatewayChatStream`.

### Suggested test

A Go test with `httptest` that feeds a body of two SSE events through
`writeProviderGatewayResponsesStream` and asserts the first event is observable on
the wire **before** the reader is drained. It should fail on the current code and
pass after the patch.

---

## Secondary observation (not a patch, a question)

`src-tauri/src/modules/codex_local_access_sidecar_config.rs:2419` derives

```rust
config.insert("codex".to_string(), json!({
    "optimize-multi-agent-v2": true,
    "stream-bootstrap-buffering": api_service,
}));
```

where `api_service` is `true` for the **local-access** gateway
(`codex_local_access_gateway_runtime.rs` → `prepare_sidecar_launch_config`) and
`false` for the **provider gateway**
(`codex_local_access_provider_gateway.rs` → `prepare_sidecar_launch_config_in_dir`).

So the local-access sidecar always runs with `stream-bootstrap-buffering: true`.
Per the upstream CLIProxyAPI docs that option exists so an in-stream
`server_is_overloaded` can be turned into a real 503 and retried on another
credential — but it delays the downstream response headers until the upstream
emits its first *generated* token (measured here as roughly +10 s on every turn),
and it is a no-op benefit for a single-credential pool
(`max-retry-credentials: 1`).

Two questions:

1. Is `api_service` the intended source for that value, or should the local-access
   gateway get `false` (matching how the same setting is configured on the remote
   gateway in this setup)?
2. If it is intentional, is there a supported way for a user to turn it off?
   Editing `codex_local_access_sidecar/config.json` is overwritten on every launch.

The test at `codex_local_access_tests_takeover.rs:226` currently asserts
`config["codex"]["stream-bootstrap-buffering"] == json!(api_service)`, so this is
pinned behaviour rather than an accident — hence asking rather than patching.

---

## Evidence bundle

- `outputs/cpa-streaming-rootcause-audit-2026-09-29.md` — full layered audit
  (sidecar / public / proxy / CPA-direct / admission / nginx), with the
  `upstream_header_time` cross-check.
- `outputs/sse_framing_probe.py`, `outputs/stream_local_chain_probe.py` — the
  probes used above.
- `docs/runbooks/cockpit-sidecar-sse-flush.md` — apply + verification runbook,
  including the acceptance criteria (`headers_ms` < 2 s, `socket_reads` ~50–150,
  `read_gap_p50` > 0, delta counts unchanged).
