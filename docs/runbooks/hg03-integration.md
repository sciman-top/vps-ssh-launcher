# HG-03 integration: evidence, product gates and rollback

**Version**: HG-03 v4.1

This runbook is the current working contract for the HOME/OFFICE integration.
It supersedes HG03_Final_v4's claim that a report filename is sufficient
acceptance. It does not establish current live health, office authorization,
home reachability, or ECS capacity; those require fresh evidence.

## Scope and ownership

| Role | Owns | Must not infer |
|---|---|---|
| HOME | HOME baseline, BWG/ECS inspection, server changes after approval, final route receipt | Office wired success from the HOME link |
| OFFICE | Local client configuration, wired/hotspot evidence, actual browser/Git/Codex/GLM checks | Server changes or approval from observing a working local endpoint |
| BWG | Existing protected Nginx entry, Admission and CPA lanes | Provider quota safety or long-term availability |
| ECS | QQ runtime and separately approved fixed application | General web proxy or traffic tunneling |

The workspace client may discover a private target path, but it must never print
the path contents. A route is authorized before it is tested for acceptance.

## Product ladder

Use the following ladder independently for each evidence class:

```text
repo_verified -> filesystem_projected -> host_loaded -> live_accepted
```

`repo_verified` means the tested repository version is known.
`filesystem_projected` means the intended files and private evidence are on the
target filesystem in the reviewed shape. `host_loaded`
requires a fresh host observation, deployment receipt and loaded configuration
identity. `live_accepted` requires the real application path—not a TCP handshake,
HTTP 200, packet loss or a remote-desktop substitute. For OFFICE access, the user
must explicitly confirm that the hotspot is disconnected; one failed check
prevents live acceptance.

Record every business item as one of `PASS`, `FAIL`, `BLOCKED`, `NOT_RUN` or
`UNVERIFIED`. Only `PASS` completes an item. D/E is the first route family:
existing approved v2rayN/BWG access, then a formally approved office entry.
HOME access and an ECS application are only evaluated after an actual approved
gap; they do not replace actual office browser or Git testing.

## Required products

1. `HOME-BASELINE` — fresh identity, service, network and client snapshots with
   observed/README/inferred boundaries.
2. `ROUTE-DECISION` — authorization, both link segments, business result, cost,
   fault domain and next action for D/E/H/C/M. Missing authorization is `BLOCKED`.
3. `SERVER-READY` — actual protected endpoint reference, loaded version/hash,
   model/protocol support and rollback; capability paths stay private.
4. `ACCESS-ACCEPTANCE` — WEB, GIT, CPA/SSE, CODEX, GLM, INTRANET and regression
   outcomes with `WIRED_ONLY` evidence.
5. `ECS-OPTIMIZATION` — resource baseline, off-host recovery proof, precisely
   approved mutation, after-state and retained rollback.

Validate evidence with `scripts/hg03_contract.py`. The checker rejects an access
bundle without an explicit hotspot-off confirmation, a successful business check
without `actual_business=true`, or a CPA claim without a request ID. It never
prints input values on failure. JSON output is a private local file; on Windows
the file ACLs inherit from the parent directory, so keep the output under a
private user directory.
Bind it outside the repository; the writer refuses a repository-local target.
The bundle records a top-level `status`; a locally generated readiness bundle
keeps `UNVERIFIED` even when the protected endpoint reference was read successfully.
Bundles imported with `--validate` retain their documented status.
A controlled one-shot request is still initially `UNVERIFIED`; attach fresh
server-side evidence with the same request ID and `server_seen=true` before
calling it accepted. A local terminal alone does not claim server correlation.
Each endpoint check demands host/port plus `/<protected>`; all other URL paths,
queries and fragments are rejected. CPA checks keep one consistent request ID,
and `server_seen=true` is still required for a server-side acceptance claim:
a live access acceptance bundle recorded with `status: PASS` must carry that
consistency for any CPA/SSE/Codex check, and without it the check stays
`UNVERIFIED`.

## HOME sequence

Read the current root `AGENTS.md`, code, runbook and fresh host state. Inspect the
profile and strict host key before a fresh read-only SSH probe. `vps-maint
inventory` is not a local dry run: it requires explicit integration opt-in at
[`vps_ssh_launcher/maintenance_cli.py`](../../vps_ssh_launcher/maintenance_cli.py).
Do not treat its refusal as a host diagnosis.

For HOME client evidence, first use the existing read-only provider diagnostic:

```powershell
.\.venv\Scripts\python.exe scripts\cockpit_provider_health.py | Out-File -LiteralPath "$env:TEMP\HG03-evidence\home-provider-health.txt" -Encoding utf8
```

Do not paste its console output into a report or model session. The current
diagnostic was observed to print provider key tails and a binding ID derived
from a local API key. Remote key IDs are abbreviated, but the console report is
not a shareable artifact. Keep the private file, classify findings, and do not
change providers or credentials as part of HG-03.

When a validated single request is explicitly in scope, record its redacted
receipt without writing an endpoint capability, token or response body:

```powershell
.\.venv\Scripts\python.exe scripts\hg03_contract.py `
  --cpa-from-codex-config "$env:USERPROFILE\.codex\config.toml" `
  --cpa-probe --layer live_accepted --route D --role HOME `
  --commit '<current-commit>' `
  --output "$env:TEMP\HG03-evidence\home-cpa.json"
```

The tool emits one attempt with zero application retries. It must not be run in
a loop or used to clear cooldowns. A successful protocol terminal is a
controlled replay, not an OFFICE live acceptance. A missing request ID, failure
terminal or transport error remains `FAIL`/`UNVERIFIED`; it is never upgraded by
the existence of an HTTP status. After the request, compare its ID with fresh
Admission evidence before making a `server_seen` claim.

For an approved host, obtain a fresh read-only snapshot using the existing
launcher. Do not fan out a host-changing command with `-RunAll`. When relevant
and safe, use the QQ project's independently reviewed two-round sampler rather
than treating a single `docker stats` reading as an OOM or peak conclusion. Its
path and behavior remain outside this repository's trust boundary until copied
and verified under an explicit operation review.

## OFFICE sequence

From the explicitly selected kit directory, use the structured probe. Set the
kit directory explicitly and call its CmdletBinding command with `&`; this kit
script is not a new repository-root dependency:

The kit's home copy lives in the operator's private workspace
(`HG03-work\HG03_Final_v4`, v4.3); it was removed from this public repository
on 2026-11-14 (workplace-network metadata does not belong on a public repo).
Integrity anchor: `SHA256SUMS.txt`
sha256 `6976783cdeef263073a28333727ac674eb8f3a6d055b93769bded528583f3ef0` —
verify with `sha256sum -c SHA256SUMS.txt` inside the kit directory. Treat the
private copy as the single authoritative version anchor; the OFFICE receiving
side never commits its copy.

For syntax-only authoring checks, invoke the `T3`/`WIRED_ONLY` network probe
against a known loopback fixture and a private evidence directory. Read the
exact parameter names from the selected kit's `04_单位双网络与实战验收.md`;
the command is intentionally not duplicated as a repository-root dependency.
At OFFICE, substitute only the approved host and port after recording the route
authorization. A loopback fixture does not establish the script's network path.

`OBSERVED` only means that the declared interface shape matched this snapshot.
If Wi-Fi or a tunnel is up under `WIRED_ONLY`, the script emits `UNVERIFIED`.
After the user manually disconnects the hotspot, verify the actual application
request separately. The probe does not establish UDP feasibility, a Git remote,
the browser path or a local CPA result.

For an approved real endpoint, the acceptance request should have a
bounded model, a tiny non-sensitive prompt and one attempt. Resolve the CPA
request ID against fresh server-side Admission data before claiming a complete
request. For WEB and GIT, record the actual business action, target class and
timestamp with capability paths removed. For GLM and office intranet systems,
preserve the existing provider and test only the permitted surface.

## ECS optimization sequence

Start with the kit's read-only ECS audit and independent cloud monitoring.
Classify data before deletion. Persistent QQ data includes login state, context,
pending tasks and durable configuration; Docker `RECLAIMABLE` is an estimate,
not permission. No blank `docker system prune -a`, `volume prune`, `compose down
-v`, recursive deletion or reinstall is admitted.

Before any cleanup, prove the exact target's off-host recovery and retention.
Use a maintenance lock, single-host approval, a short window and after-state
comparison. `vps-maint` handles only registered Xray/Docker upgrade resources
([`resources.py`](../../vps_ssh_launcher/maintenance/resources.py)); it is not
a generic cleanup engine or a substitute for Caddy/QQ transaction recovery.

The ECS public Caddy entry is not a generic cross-network route. Its existing
80/443 surface has prior ICP edge-block evidence, so a fixed application route
remains blocked until formal domain/compliance and unit authorization exist.

## Stop and rollback

Stop on a host-identity mismatch, unknown strict-host-key state, absent route
authorization, missing recovery, a second host attempting the same write, secret
in output, or a business check changing from working to failing. Preserve the
prior files, service state, locks, risk holds and QQ login data.

Git rollback removes only this planned source/document slice. A server rollback
restores the exact backup and reloads its owning service; a Git revert cannot
restore a Caddyfile, cron entry, container volume or a deleted QQ asset. Live
acceptance remains incomplete until the user completes the required office-site
and hotspot actions.
