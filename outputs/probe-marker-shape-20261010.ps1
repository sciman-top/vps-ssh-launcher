#requires -Version 7
# Read-only: classify the newest CPA error dumps by which admission capacity
# marker their response-side sections would hit. Booleans/counts only - no
# dump text, no request bodies, no upstream URLs are printed.
param(
  [string]$Profile = "bwg",
  [int]$IdleTimeoutSeconds = 90,
  [int]$HardTimeoutSeconds = 240
)
$ErrorActionPreference = "Stop"
if ($Profile -cne "bwg") { throw "restricted to bwg" }
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts\lib\project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot

$remote = @'
set -Eeuo pipefail
python3 - <<'PY'
import re, time
from pathlib import Path

SECTIONS = {'=== api response ===', '=== response ==='}
MARKERS = [
    'selected model is at capacity', 'model_at_capacity',
    'server_is_overloaded', 'rate limit', 'rate_limit',
    'usage limit', 'usage_limit_reached', 'too many requests',
    'auth_unavailable',
]

def response_sections(raw):
    keep = False
    lines = []
    for line in raw.splitlines():
        marker = line.strip().lower()
        if marker.startswith('=== ') and marker.endswith(' ==='):
            keep = marker in SECTIONS
            continue
        if keep:
            lines.append(line)
    return '\n'.join(lines)

candidates = []
for path in Path('/opt/cliproxyapi/auth/logs').glob('error-*.log'):
    try:
        st = path.stat()
    except OSError:
        continue
    candidates.append((path, st.st_mtime, st.st_size))
candidates.sort(key=lambda item: item[1], reverse=True)
now = time.time()
printed = 0
for path, mtime, size in candidates:
    if mtime < now - 48 * 3600 or printed >= 3 or size > 20_000_000:
        continue
    text = response_sections(path.read_text(errors='replace'))
    hits = {m: text.lower().count(m) for m in MARKERS if m in text.lower()}
    ts = ''
    for line in path.read_text(errors='replace').splitlines():
        if line.startswith('Timestamp:'):
            ts = line.split(':', 1)[1].strip()
            break
    print(f"dump mtime_age_h={ (now - mtime) / 3600:.1f} ts={ts} "
          f"status_hint={' '.join(sorted(set(re.findall(r'status[\"\s::=]+([0-9]{3})', text)))[:4])} "
          f"markers={hits}")
    printed += 1
print(f"scanned_newest={printed}")
PY
echo '== container log marker counts (3h) =='
CNAME=$(docker ps --format '{{.Names}}' | grep -i -m1 cliproxy || docker ps --format '{{.Names}}' | head -1)
echo "container=$CNAME"
for kw in 'server_is_overloaded' 'at capacity' 'usage_limit_reached' 'rate limit' 'auth_unavailable'; do
  n=$(docker logs --since 3h "$CNAME" 2>&1 | grep -c -i "$kw" || true)
  echo "kw=$kw count=$n"
done
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config `
  -Profile $Profile -Command $remote `
  -IdleTimeoutSeconds $IdleTimeoutSeconds -HardTimeoutSeconds $HardTimeoutSeconds
Write-Host "MARKER_SHAPE_PROBE=DONE"
