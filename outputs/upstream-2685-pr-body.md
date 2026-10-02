Addresses #2685. (#2676 / #2677 are the prerequisite for the setting to reach
this sidecar; this PR is deliberately independent of them.)

## Problem

The Direct / fixed Provider Gateway request path forwards straight to the
upstream:

- **no per-account concurrency bound** — one bound account can be driven past
  its configured limit with nothing throttling the fan-in;
- **no memory of an upstream `Retry-After`** — an upstream 429/503 is forgotten
  the moment the response is written, so the next request goes straight back out.

`maxAccountConcurrency` and `accountConcurrencyWaitMs` already exist in the
collection and are projected into the sidecar manifest, but nothing on this path
consumes them.

## Change

Go sidecar only — no Rust/app changes. Five files.

- **`provider_gateway_concurrency.go` (new)** — `admitDirectProviderAccount`
  reserves a slot for the bound account before the request leaves the sidecar.
  If no slot is free it waits up to `accountConcurrencyWaitMs` and then answers
  `429 account_concurrency_exceeded` with `Retryable: false`, because retrying
  the same saturated account cannot succeed. `recordDirectProviderBackoff`
  remembers the upstream `Retry-After` per `(account, gateway base URL, model)`.
- **`provider_gateway.go`** — threads the bound account id from
  `handleExecutorBody` into `handleProviderGatewayRequest` (as a variadic
  argument, so existing call sites keep their shape), admits before
  `http.DefaultClient.Do`, and records the backoff from the response.
- **`manifest_policy.go`** — adds `providerBackoffs` to the request tracker plus
  `tryReserveAccountSlotLocked`, so the reservation and the backoff check happen
  under one lock and a backoff waiter never occupies an account slot.
- Two new test files.

## Behaviour worth reviewing

- Only `429` / `503` **carrying a positive `Retry-After`** establish a backoff.
  A shorter, past, zero, negative, fractional or unparsable header never
  shortens an active deadline; a newer longer one extends it.
- `Retry-After` is accepted as delta-seconds or an HTTP-date, with an overflow
  guard.
- A backoff is isolated per `(account, gateway base URL, model)`. An unrelated
  account, model or gateway is not blocked, and the base URL is normalised so a
  trailing slash does not create a second binding.
- Cancelled or timed-out waiters never reach the upstream.
- `MaxAccountConcurrency <= 0` leaves the gate fully disabled, so existing
  deployments are unaffected.

## Dependency

The gate stays inert until the setting actually reaches the provider-gateway
manifest: `apply_provider_gateway_template_settings` currently copies neither
`max_account_concurrency` nor `account_concurrency_wait_ms` (see #2676, fixed by
#2677). Until that lands this PR is a no-op at runtime by design.

## Verification

On a clean v1.3.65 base (`0b6514b`, the direct parent of `main`):

```
gofmt -l            -> clean for all five files
go vet ./...        -> clean
go test . -count=1  -> ok  github.com/router-for-me/CLIProxyAPI/v7/cockpit-cliproxy  27.5s
```

Tests cover: a slot held for the whole SSE stream; shared-account rejection;
waiter release / cancel / timeout; upstream rejection passthrough unchanged
(status, body, headers, exactly one upstream hit); `Retry-After` parsing;
per-binding isolation; and that a cooling waiter does not hold an account slot.
