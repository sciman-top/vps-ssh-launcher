#requires -Version 7
# Read-only natural-traffic summary. It distinguishes real upstream 6004
# attempts from local CN account-window and INTL shared-window replays.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -Eeuo pipefail
docker logs --since 2h cli-proxy-api 2>&1 |
  python3 -c '
import collections
import re
import sys

counts = collections.Counter()
by_request = collections.defaultdict(collections.Counter)
for line in sys.stdin:
    match = re.search(
        r"\[([0-9a-f]{8})\].*conductor_execution.go:2038.*"
        r"provider=workbuddy model=([^ ]+).*duration=([0-9]+)ms err=(.*)",
        line,
    )
    if not match:
        continue
    request_id, model, duration, error = match.groups()
    duration = int(duration)
    if "WorkBuddy INTL 共享" in error:
        kind = "intl_local_shared"
    elif "该模型处于上游模型级限流" in error:
        kind = "cn_local_account"
    elif "code 6004" in error:
        kind = "real_6004"
    else:
        kind = "other"
    counts[kind] += 1
    by_request[request_id][kind] += 1
print("NATURAL_WINDOW=2h")
print("EVENT_COUNTS=" + repr(dict(counts)))
for request_id, values in list(by_request.items())[-12:]:
    print(f"REQUEST={request_id} " + repr(dict(values)))
'
echo NATURAL_SNAPSHOT_DONE
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile bwg -Command $command `
  -IdleTimeoutSeconds 60 -HardTimeoutSeconds 120
