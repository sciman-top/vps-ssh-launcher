#requires -Version 7
# Two claims from the last round were asserted but never demonstrated:
#   1. prune_images() now protects the right images;
#   2. a "complete" rollback set actually restores.
# Both are checked here, read-only, against the live backup inventory.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -uo pipefail
DIR=/opt/cliproxyapi
echo '=== A. projection drift'
for pair in \
  "cpa-auto-update.sh:$DIR/auto-update.sh:268a2ef22603e773e481ea883085796c905629148da3e513a2f82f290c5b3834" \
  "cpa-health.py:$DIR/cpa-health.py:418eb770a0478a3458cf21c6846e69907155c04221a6496eb143b3890b13a5c7" \
  "cpa_policy.py:$DIR/cpa_policy.py:ad31c0f6b8e6f8c05ddb42e9af33175344bc7720ac41787a1decbaf3984cd19d" \
  "cpa-admission.py:$DIR/cpa-admission.py:285b4a638be90aac0b8f413b7859ca772cfc40a7d1670215dcfc332a5b99f03b"; do
  name=${pair%%:*}; rest=${pair#*:}; path=${rest%:*}; want=${rest##*:}
  got=$(sha256sum "$path" 2>/dev/null | cut -d' ' -f1)
  if [ "$got" = "$want" ]; then echo "drift $name MATCH"; else echo "drift $name MISMATCH got=${got:-missing}"; fi
done

echo
echo '=== B. prune protection: old logic (tag) vs new logic (recorded id)'
python3 - <<'PY'
import subprocess
from pathlib import Path

base = Path('/opt/cliproxyapi/backups')
def short(x):
    x = x.replace('sha256:', '')
    return x[:12]

running = short(subprocess.run(['docker','inspect','--format','{{.Image}}','cli-proxy-api'],
                               capture_output=True, text=True).stdout.strip())
protected_old, protected_new = {running}, {running}
resolved_same_as_running = 0
for d in sorted(p for p in base.iterdir() if p.is_dir()):
    compose = d / 'compose.yml'
    if not compose.is_file():
        continue
    rec = d / 'image-id'
    if rec.is_file():
        val = rec.read_text().strip()
        if val.startswith('sha256:'):
            protected_new.add(short(val))
        continue
    try:
        import yaml
        ref = yaml.safe_load(compose.read_text())['services']['cli-proxy-api']['image']
    except Exception:
        continue
    out = subprocess.run(['docker','image','inspect','--format','{{.ID}}',ref],
                         capture_output=True, text=True).stdout.strip()
    if not out:
        continue
    sid = short(out)
    protected_old.add(sid)
    if sid == running:
        resolved_same_as_running += 1
print('running=%s' % running)
print('protected_old(tag logic)=%d ids' % len(protected_old))
print('protected_new(recorded ids)=%d ids' % len(protected_new))
print('legacy_backups_resolving_to_running=%d (these are the ones the old logic mis-protected)' % resolved_same_as_running)
PY

echo
echo '=== C. restore rehearsal for one complete bind_mount rollback set'
SET=$(ls -1d "$DIR"/backups/*converge-v8.0.61*/ | tail -1); SET=${SET%/}
echo "set=$(basename "$SET")"
BIN_SHA=$(sha256sum "$SET/CLIProxyAPI-patched" | cut -d' ' -f1)
echo "set_binary=$BIN_SHA"
V=$(mktemp -d /root/restore-drill-XXXXXXXX)
trap 'rm -rf --one-file-system -- "$V"' EXIT
mkdir -p "$V/tmp" "$V/run" "$V/state" "$V/auth" "$V/plugins"
touch "$V/FIXTURE_ONLY"
install -m 755 "$SET/CLIProxyAPI-patched" "$V/CLIProxyAPI"
cp -a "$DIR/config.yaml" "$V/config.yaml"; chmod 600 "$V/config.yaml"
cp -a "$DIR/plugins/workbuddy.so" "$V/plugins/workbuddy.so"
unshare --mount --net bash -c '
set -Eeuo pipefail
mount --make-rprivate /
V='"$V"'
mount --bind "$V" /opt/cliproxyapi
mount --bind "$V/tmp" /tmp
mount --bind "$V/run" /run
mount --bind "$V/state" /var/lib/vps-ssh-launcher
ip link set lo up
cd /opt/cliproxyapi
./CLIProxyAPI -config config.yaml >restore.log 2>&1 &
pid=$!
sleep 8
if kill -0 "$pid"; then echo RESTORE_PROCESS_ALIVE=yes; else echo RESTORE_PROCESS_ALIVE=no; fi
grep -m1 "CLIProxyAPI Version" restore.log || echo NO_VERSION_LINE
grep -m1 "plugin registered plugin_id=workbuddy" restore.log || echo NO_PLUGIN_LINE
if grep -Eiq "panic|failed to load" restore.log; then echo RESTORE_FAULT=yes; else echo RESTORE_FAULT=no; fi
kill "$pid" 2>/dev/null || true
wait "$pid" 2>/dev/null || true
'
echo RESTORE_DRILL_DONE
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 400 -HardTimeoutSeconds 900
