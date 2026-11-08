# 2026-11-08 BWG CPA Gemini thought streaming fix

## Scope

- Fixed the Gemini/Antigravity Responses translator on the local fork so unsigned thought chunks immediately open a reasoning item, emit both `reasoning_summary_text.delta` and `reasoning_text.delta`, and attach the later thought signature at item completion.
- Runtime fork commit: `5d495e1`; regression-test commit: `4d48e31` on `codex/gemini-raw-reasoning-deltas`.
- Upstream issue [#7338](https://github.com/router-for-me/CLIProxyAPI/issues/7338) was closed as not planned because upstream prioritizes strict compatibility for `encrypted_content`; no upstream translator PR was submitted.

## Runtime deployment

- Transaction: `outputs/deploy-cpa-v8059-raw-thought-streamfix-20261108.sh`.
- Live binary SHA256: `330b7a559ad3956bdbe090190ec822982ca26eb8817839bfe32d31a1724ed17f`.
- Previous live binary SHA256: `95452348a3e3192cd17e3a12027e4becc75fb6071aeee568d2ffb06655c26fd5`.
- Backup: `/opt/cliproxyapi/backups/20261108T045502.790634318Z-cpa-v8059-raw-thought-streamfix/CLIProxyAPI.pre-streamfix`.
- Official rollback: `/opt/cliproxyapi/backups/20261108T015900Z-cpa-v8059-raw-thought/CLIProxyAPI.official-v8.0.59`, SHA256 `6891aa84142e7de4b2d036d4784bf06a28fc9884f7e3eff0c6b0e583edb2223e`.
- WorkBuddy plugin remained SHA256 `6b2b2284ddd35a4461f39130fe95772c86744b59f36406a96d509c7dec8b9135` and registered as version `0.9.88`.
- The first transaction completed the binary swap, restart, readiness, plugin and log checks, then stopped on `AUTH_BYTES_CHANGED`. Inspection showed one expected Antigravity OAuth token-file refresh; the deployment remained loaded. The script was subsequently corrected to distinguish runtime token refresh from credential-file set changes and to guarantee rollback through an EXIT trap.

## Durable projection

- After the running-container deployment, the accepted binary was projected to host path `/opt/cliproxyapi/CLIProxyAPI-patched` and mounted read-only as `/CLIProxyAPI/CLIProxyAPI`, so it survives CPA container recreation.
- Transaction: `outputs/project-cpa-v8059-raw-thought-streamfix-20261108.sh`.
- A first drain attempt safely timed out after 60 seconds and made no changes. The retry drained successfully in 180 seconds, projected the compose volume, recreated CPA once, and passed readiness, plugin registration, host/container hash, read-only mount, auth-file-set, and lane-hold checks.
- Projection backup: `/opt/cliproxyapi/backups/20261108T061746.401321956Z-cpa-v8059-raw-thought-projection`.
- Post-projection hash check: host and container both `330b7a559ad3956bdbe090190ec822982ca26eb8817839bfe32d31a1724ed17f`.
- Post-projection mount: `bind=/opt/cliproxyapi/CLIProxyAPI-patched:RW=false`.
- A fresh controlled probe after recreation returned `RAW_THOUGHT_PROBE=PASS`.

## Verification

- Focused Go tests passed for the Gemini and Antigravity Responses translators; `git diff --check` passed in the fork.
- Controlled CPA probe: `RAW_THOUGHT_PROBE=PASS`, HTTP 200, completed terminal, two summary and raw deltas.
- Short timing probes exposed an upstream burst shape: all reasoning events and terminal arrived within 1–7 ms after the first reasoning payload. This confirms that very short prompts cannot prove visual streaming.
- Natural Gemini thread `01a1b992-20aa-78a2-806b-3323a5f7a7ec`, model `gemini-3.8-flash-high`, effort `xhigh`, summary `detailed`:
  - task started `2026-11-08T05:12:05.381Z`; time to first token `9202 ms`;
  - reasoning item started `2026-11-08T05:12:14.581Z` and completed `2026-11-08T05:12:44.955Z`, a `30374 ms` streaming window;
  - persisted reasoning contained both non-empty summary and raw reasoning content;
  - CPA journal request `fb245f5ff2634ca380e75ed0da0c7a3c` reported upstream first byte `6636 ms`, first visible text `36831 ms`, 35 SSE events, `usage_out=5625`, `reasoning_output_tokens=5616`, and a completed terminal;
  - final answer was `1131` and `7488`.

## Result

`CONTROLLED_PROTOCOL_ACCEPTANCE=PASS` and `NATURAL_RUNTIME_STREAMING_ACCEPTANCE=PASS`. The natural acceptance is bounded to this prompt shape and thread; very short upstream bursts may still render at completion because Antigravity emits them as one burst.
