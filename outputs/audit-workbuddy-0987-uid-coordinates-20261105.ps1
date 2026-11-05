# Read-only production attribution: join current executor auth files to their
# stored UIDs and aggregate public request IDs. Secrets are never printed.
param(
  [string]$Config
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot -Config $Config
$command = @'
set -Eeuo pipefail
umask 077
ROOT=$(mktemp -d /root/wb-0987-coordinate-audit-XXXXXXXX)
cleanup() { rm -rf --one-file-system -- "$ROOT"; }
trap cleanup EXIT
[ "$(sha256sum /opt/cliproxyapi/plugins/workbuddy.so | cut -d ' ' -f 1)" = \
  7093a190e906a872f7362e1bc225eaf6f0fba30388cdd9066af6d0d8636a89e4 ] ||
  { echo REFUSE_NOT_0987; exit 20; }
python3 - /opt/cliproxyapi/auth "$ROOT/auth-map.tsv" <<'PY'
import json
import sys
from pathlib import Path

base = Path(sys.argv[1])
lines = []
for path in sorted(base.glob('workbuddy*.json')):
    data = json.loads(path.read_text(encoding='utf-8'))
    account = data.get('account') or {}
    uid = account.get('uid') if isinstance(account, dict) else None
    if not isinstance(uid, str) or not uid.strip():
        raise SystemExit('REFUSE_AUTH_UID_UNREADABLE')
    suffixes = (
        'workbuddy-intl-' + uid + '.json',
        'workbuddy-' + uid + '.json',
        'workbuddy-' + 'tier2-' + uid + '.json',
    )
    if not any(path.name == suffix for suffix in suffixes):
        raise SystemExit('REFUSE_NONCANONICAL_AUTH_FILE')
    lines.append(f'{path.name}\t{uid}')
Path(sys.argv[2]).write_text('\n'.join(lines) + '\n', encoding='utf-8')
print(f'AUTH_COORDINATES={len(lines)}')
PY
docker logs --since 2026-11-05T16:15:23Z cli-proxy-api 2>&1 |
  python3 -c '
import collections
import json
import re
import sys
from pathlib import Path

auth_uid = {}
for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    name, uid = line.split("\t", 1)
    auth_uid[name] = uid
by_request = collections.defaultdict(list)
for line in sys.stdin:
    match = re.search(
        r"\[([0-9a-f]{8})\].*conductor_execution.go:2038.*provider=workbuddy "
        r"model=([^ ]+).*auth_file=([^ ]+).*duration=([0-9]+)ms err=(.*)",
        line,
    )
    if not match:
        continue
    rid, model, filename, delay, error = match.groups()
    kind = "real_6004" if "code 6004" in error else (
        "cn_local_account" if "上游模型级限流" in error else "other"
    )
    by_request[rid].append({
        "kind": kind,
        "uid": auth_uid.get(filename),
        "model": model,
        "duration_ms": int(delay),
    })
for rid, events in by_request.items():
    pairs = {(e["uid"], e["model"]) for e in events}
    print(
        "REQUEST=" + rid,
        "EVENTS=" + str(len(events)),
        "REAL=" + str(sum(e["kind"] == "real_6004" for e in events)),
        "LOCAL=" + str(sum(e["kind"] == "cn_local_account" for e in events)),
        "DISTINCT_UID_MODEL_PAIRS=" + str(len(pairs)),
        "UIDS=" + ",".join(sorted(str(e["uid"]) for e in events)),
        "MODELS=" + ",".join(sorted({e["model"] for e in events})),
    )
print("FAILURE_REQUESTS=" + str(len(by_request)))
' "$ROOT/auth-map.tsv"
echo UID_COORDINATE_AUDIT_DONE
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile bwg -Command $command `
  -IdleTimeoutSeconds 60 -HardTimeoutSeconds 120
