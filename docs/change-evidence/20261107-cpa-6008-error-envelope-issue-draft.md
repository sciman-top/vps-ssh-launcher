# Issue draft: upstream 6008 (Credits exhausted) loses its numeric code in the error envelope

> Status: DRAFT, not yet filed. Prepared 2026-11-07 from a controlled
> reproduction on a self-hosted deployment. Review before posting.

## Summary

When a WorkBuddy credential returns upstream `429 {"code":6008,"msg":"Credits
exhausted..."}`, CPA re-wraps it as a `rate_limit_error` /
`code: rate_limit_exceeded` envelope whose `message` merely quotes the
provider JSON as prose. The numeric provider code therefore exists only as
text inside `message`, so no downstream consumer can branch on it
structurally.

## Environment

| Item | Value |
|---|---|
| CPA image | `eceasy/cli-proxy-api:v8.0.58` |
| Image digest | `sha256:060f352248b413d2edac58d816aa5437972ce764a07250d2b9aef82db549cb6e` |
| Binary modified locally | no (image digest matches the published tag; `/CLIProxyAPI/CLIProxyAPI` untouched) |
| Provider | WorkBuddy OAuth (`workbuddy-oauth` lane) |
| Upstream model | `deepseek-v4.1-flash-sg` |

## Observed

CPA container log:

```
429 | upstream execution failed: provider=workbuddy model=deepseek-v4.1-flash-sg
auth_file=workbuddy-intl-<uid>.json
err=upstream 429: {"code":6008,"msg":"Credits exhausted. Please visit the link
below to purchase add-on packs and get more credits: https://www.codebuddy.ai/profile/usage "}
```

Body returned to the client:

```json
{"error":{"type":"rate_limit_error","code":"rate_limit_exceeded",
 "message":"upstream 429: {\"code\":6008,\"msg\":\"Credits exhausted...\"}"}}
```

## Why it matters

`rate_limit_error` describes a transient rate window. `6008` is a terminal
billing state for one credential. A consumer that trusts the envelope label
treats a drained account as momentary congestion and keeps retrying, while the
credential will never serve again until it is topped up.

In our admission layer we had to parse the JSON embedded in `message` to tell
the two apart:

```python
match = re.search(r"\{.*\}", candidate, re.DOTALL)
inner = json.loads(match.group(0))   # the only place the numeric code survives
```

That is a prose-scraping dependency on the wrapper's wording.

## Evidence that the code is present upstream but dropped here

Binary string counts (bytes present in the file):

| String | `CLIProxyAPI` (v8.0.58) | `workbuddy.so` (plugin 0.9.87) |
|---|---|---|
| `rate_limit_error` | 1 | 0 |
| `rate_limit_exceeded` | 2 | 0 |
| `credits exhausted` | 0 | 2 |
| `isCreditsExhausted` | 0 | 1 |

So the rate-limit labelling is introduced by CPA, while the plugin is the
component that recognizes the credits condition. The plugin classifies by the
message text (`credits exhausted`, `credit not enough`, `not enough credit`),
not by the numeric code.

## Suggestion

Preserve the provider's numeric code in the wrapper, e.g. add it alongside the
existing fields:

```json
{"error":{"type":"rate_limit_error","code":"rate_limit_exceeded",
 "upstream_code":6008,
 "message":"upstream 429: {...}"}}
```

or map known terminal codes onto their semantic type (`insufficient_quota` for
6008/14018, `model_busy` for 14003) so consumers stop needing to parse prose.
Keeping `message` unchanged preserves compatibility.

## Related prior art in this deployment

A sibling case was already handled locally: a `14003` wrapper label beat a
header-level cooldown ladder because the wrapper's `type` outranked the quoted
provider code. The same class of problem: the wrapper's generic label hides the
provider's actual classification.

## Reproduction notes

Reproduced by sending a single streaming Messages request for a model pinned
to a drained WorkBuddy credential and reading the CPA container log plus the
returned body. No account identifiers, tokens, or credential contents are
included above; `auth_file` is shown with the uid redacted.

## Open question for maintainers

Is the `upstream_code` passthrough acceptable, or is there a preferred
canonical field name already used elsewhere in v8 management/error surfaces?
