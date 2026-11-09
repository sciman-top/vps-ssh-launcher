#requires -Version 7
# Explain the 1-vs-54 gap precisely, because the previous quantification
# looked contradictory: protected_old=1 but old_logic_would_delete=1 too.
#
# The reason: compose refs mostly carry @sha256 digests, so resolving them
# still returns the right image. The mutable-tag hazard bites only where the
# ref has NO digest -- which is exactly the fork era we just entered
# (cli-proxy-api-fork:v8.0.61-gemini-raw). This measures that slice.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -uo pipefail
python3 - <<'PY'
import subprocess
from pathlib import Path
import yaml

base = Path('/opt/cliproxyapi/backups')
pinned = unpinned = 0
unpinned_refs = {}
for d in sorted(p for p in base.iterdir() if p.is_dir()):
    c = d / 'compose.yml'
    if not c.is_file():
        continue
    try:
        ref = yaml.safe_load(c.read_text())['services']['cli-proxy-api']['image']
    except Exception:
        continue
    if '@sha256:' in ref:
        pinned += 1
    else:
        unpinned += 1
        unpinned_refs[ref] = unpinned_refs.get(ref, 0) + 1
print('backup_compose_refs pinned_by_digest=%d unpinned=%d' % (pinned, unpinned))
for ref, n in sorted(unpinned_refs.items(), key=lambda kv: -kv[1]):
    print('  unpinned x%-3d %s' % (n, ref))
print()
print('Interpretation: pinned refs survive tag drift; unpinned refs do not.')
print('The fork era introduced unpinned refs, which is where the recorder matters.')
PY

DIR=/opt/cliproxyapi
echo '=== does the recorder cover the unpinned (fork) refs?'
for d in "$DIR"/backups/*fork-image*/; do
  d=${d%/}
  ref=$(python3 -c "import sys,yaml;print(yaml.safe_load(open(sys.argv[1]))['services']['cli-proxy-api']['image'])" "$d/compose.yml" 2>/dev/null || echo unreadable)
  has=$( [ -f "$d/image-id" ] && echo yes || echo no )
  echo "  $(basename "$d")  recorded=$has  ref=$ref"
done
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 240 -HardTimeoutSeconds 600
