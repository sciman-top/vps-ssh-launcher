<!--
Target: deepseek-ai/deepseek-harness discussions/12696 (Q&A: "is /v1/files upload intended?")
Kind: answer with call-site evidence + small feature ask. NOT a bug report —
      the behavior is by design and degrades gracefully.
Baseline checked: desktop app 0.2.2-rc.1 (win32 x64), app.asar SHA-256
      5fa8a6b21bceb8f3e99e30efa2270efd0cc773a11e42aa0488f804a64b34e637, extracted 2026-11-09.
      (An earlier draft quoted payloadDigest 0881fb..., absent from the archive;
      that claim was withdrawn in the follow-up comment.)
Why filed: the discussion could not find a call site because grepping
      `files.upload(` only hits @anthropic-ai/sdk docs/helpers. The actual sender
      is the adapter's own Files transport, and the SDK is dead weight in the bundle.
Ops note: we point the desktop client at an Anthropic-compatible gateway that
      intentionally does not implement Files API; the gateway answers 404 locally
      so the attempts do not consume its bounded error-dump ring. The "1908 x 404
      in 24h" figure first written here was WITHDRAWN: it could not be
      corroborated from access logs (format omits the URI and the
      `route=other status=404` count mixes in internet scanners). Only 31
      admission-answered probes exist in the whole retention window. No
      credentials or endpoints are included below.
Posted: https://github.com/deepseek-ai/deepseek-harness/discussions/12696#discussioncomment-19483858
      (node DC_kwDOT3T1g84BKUzS), 2026-11-09, as sciman-top via gh api graphql
      (`addDiscussionComment`; body = this file minus the filing header).
Correction: https://github.com/deepseek-ai/deepseek-harness/discussions/12696#discussioncomment-19483915
      (node DC_kwDOT3T1g84BKU0L) — closes the inline-fallback question
      (unconditional `FileResolutionFailure` -> `inline = true`) and withdraws both
      the wrong digest and the unverifiable 1908 figure.
-->

## Yes — it is intended, and here is the actual call site (0.2.2-rc.1)

The upload is the **DeepSeek Messages adapter's Files-API-first image pipeline**,
not an Anthropic SDK side effect. Per the package README (`@deepseek-ai/dsh-llm-deepseek`):

> DeepSeek input normally uses the Files API and uses inline base64 only for
> per-request recovery.

So the inline-base64 chain you found is the **recovery leg**; the primary leg is
the Files upload.

### Why grepping `files.upload(` finds nothing in `@deepseek-ai/dsh-*`

The sender is the adapter's own hand-rolled transport:

- `@deepseek-ai/dsh-llm-deepseek` (upstream `packages/llm/llm-deepseek`),
  `DeepSeekFilesClient.upload()` (`src/files-api.ts`), called from the request
  loop as `client.upload({...})` — the literal text `files.upload(` never occurs,
  so a package-wide grep for it only matches `@anthropic-ai/sdk` docs/helpers.
- `@anthropic-ai/sdk` is present inside `app.asar`, but **no `@deepseek-ai/dsh-*`
  module imports it** (verified by grepping all extracted modules for the import
  specifier), and the only `anthropic-beta` header writer in the whole bundle is
  `dsh-llm-deepseek`. The SDK's `client.beta.files.upload()` (which does post
  `/v1/files?beta=true`) is a plausible-looking red herring here.

### Wire shape (from the 0.2.2-rc.1 bundle)

```
POST {messagesApiRoot(baseURL)}/files            # no query string
content-type: multipart/form-data
  expires_after[anchor] = created_at
  expires_after[seconds] = <fileExpiresAfterSeconds, default 604800>
  file = <image bytes, media-type blob>
anthropic-version: 2023-06-01
anthropic-beta: files-api-2025-04-14
user-agent: deepseek-harness/0.2.2-rc.1          # from @deepseek-ai/dsh-llm attributionHeaders()
```

### When it fires

Any request whose messages retain images on an image-capable model route
(`inputModalities` includes `image`). On failure the request is rebuilt inline
(`FileResolutionFailure` → `inline = true`), so chat keeps working — the cost is
one futile upload per turn, because the reuse index
(`$DSH_HOME/llm-deepseek/files-v3.json`) is only written on a successful upload
and therefore never fills when the endpoint has no Files API; every subsequent
turn retries. That matches the rate shape we measured on our gateway
(event-driven, tied to turns with images; zero requests when idle).

### Small ask

Against an endpoint that answers 404 for `/v1/files`, the adapter re-attempts on
every image turn for the life of the process. Two options, either would do:

1. **Sticky per-endpoint degrade** — after an upload fails with a status that
   classifies as "endpoint has no Files API" (e.g. 404/501) for a given
   `baseURL` + credential scope, remember it (persistently, like the upload
   index) and skip Files until the endpoint or credential changes.
2. **A config knob** (`filesApi: auto | off`) — the 0.2.2-rc.1 schema only
   carries budgets/timeouts (`maxRequestFilesBytes`, `filesApiTimeoutMs`,
   `fileExpiresAfterSeconds`, ...), none of which can express "this endpoint
   has no Files API".

Happy to file this as a feature issue instead if the maintainers prefer.
