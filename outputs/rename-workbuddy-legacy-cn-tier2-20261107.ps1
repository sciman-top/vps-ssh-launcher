#requires -Version 7
# Restore the user-mandated WorkBuddy serving order (INTL daily-quota first,
# CN credit-billed overflow behind the workbuddy-tier2- filename prefix,
# 2026-11-02): three CN credentials were imported with the legacy bare
# workbuddy-<uuid>.json name, which sorts before workbuddy-intl-* and broke
# the lexicographic order the sticky picker falls back to (doctor
# workbuddy-realm-order=DRIFT). Each file is rename-only: bytes are preserved
# and the transaction asserts auth.domain == www.codebuddy.cn before touching
# anything, so a Global account can never be demoted by mistake.
param(
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$mode = if ($DryRun) { 'dry-run' } else { 'apply' }
$remote = @'
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
AUTH=/opt/cliproxyapi/auth
MODE='__MODE__'
TS=$(date -u +%Y%m%dT%H%M%S.%NZ)
BK=/opt/cliproxyapi/backups/$TS-workbuddy-tier2-rename
TRAFFIC=/run/cpa-admission/requests.lock
MUTATED=0

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo 'UPDATE_ALREADY_RUNNING'; exit 75; }

# Classification + plan: only legacy bare names whose auth domain is the CN
# platform are candidates, and the rename must actually fix the order.
python3 - "$AUTH" <<'PY'
import glob, json, os, sys

auth_dir = sys.argv[1]
names = sorted(
    os.path.basename(p)
    for p in glob.glob(os.path.join(auth_dir, "workbuddy-*.json"))
)
cn = [n for n in names if not n.startswith("workbuddy-intl-")]
intl = [n for n in names if n.startswith("workbuddy-intl-")]
print("ORDER_BEFORE cn=%d intl=%d min_cn=%s max_intl=%s"
      % (len(cn), len(intl), min(cn), max(intl)))
if min(cn) > max(intl):
    raise SystemExit("ALREADY_ORDERED no_rename_needed")
plan = []
for name in names:
    if name.startswith(("workbuddy-intl-", "workbuddy-tier2-")):
        continue
    data = json.loads(open(os.path.join(auth_dir, name), encoding="utf-8").read())
    auth = data.get("auth") if isinstance(data.get("auth"), dict) else {}
    domain = auth.get("domain")
    assert domain == "www.codebuddy.cn", (
        "REFUSE non-CN legacy file %s domain=%r" % (name, domain)
    )
    plan.append((name, "workbuddy-tier2-" + name[len("workbuddy-"):]))
assert plan, "no rename plan"
for src, dst in plan:
    print("PLAN %s -> %s" % (src, dst))
open("/tmp/.wb-tier2-plan", "w", encoding="utf-8").write(
    "\n".join("%s %s" % pair for pair in plan) + "\n"
)
PY

check_idle() {
  python3 - <<'PY'
import json, urllib.request
with urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5) as r:
    health = json.load(r)
assert health.get('status') == 'ok', 'REFUSE healthz_not_ok'
for name, lane in health['lanes'].items():
    state = lane.get('state') or {}
    if state.get('inflight') or state.get('pending'):
        raise SystemExit(
            'REFUSE active_requests lane=%s inflight=%s pending=%s'
            % (name, state.get('inflight'), state.get('pending'))
        )
    assert state.get('hold_reason') is None, 'REFUSE hold lane=%s' % name
print('ADMISSION_IDLE_OK')
PY
}

if [ "$MODE" = 'dry-run' ]; then
  check_idle || { echo 'DRY_RUN_BUSY no_rename=true'; exit 75; }
  echo 'DRY_RUN_OK no_rename=true'
  exit 0
fi

mkdir -m 700 "$BK"
while read -r src dst; do
  cp -a "$AUTH/$src" "$BK/"
  printf '%s %s\n' "$src" "$dst" >> "$BK/plan.txt"
done < /tmp/.wb-tier2-plan
rm -f /tmp/.wb-tier2-plan
chmod 600 "$BK"/*
log "BACKUP dir=$BK"

rollback() {
  local rc=${1:-$?} failed=0
  trap '' ERR HUP INT TERM
  if [ "$MUTATED" = 1 ]; then
    while read -r src dst; do
      [ ! -f "$AUTH/$dst" ] || mv -f "$AUTH/$dst" "$AUTH/$src" || failed=1
    done < "$BK/plan.txt"
    python3 /opt/cliproxyapi/cpa-health.py readiness 2>/dev/null || true
    if [ "$failed" = 0 ]; then
      log "ROLLBACK_OK restored_names=true"
    else
      log "ROLLBACK_FAILED backup=$BK"
    fi
  fi
  exit "$rc"
}
trap rollback ERR HUP INT TERM

exec 8<"$TRAFFIC"
acquired=0
for attempt in $(seq 1 240); do
  if flock -x -w 1 8; then acquired=1; break; fi
  if [ $((attempt % 15)) -eq 0 ]; then
    log "DRAIN_WAIT attempt=$attempt live_streams_still_open=true"
  fi
done
[ "$acquired" = 1 ] || { echo 'REFUSE live_traffic_active'; exit 75; }
log 'REQUEST_GUARD_EXCLUSIVE_OK'
check_idle

MUTATED=1
while read -r src dst; do
  mv "$AUTH/$src" "$AUTH/$dst"
  log "RENAMED $src -> $dst"
done < "$BK/plan.txt"

# The plugin re-registers credentials incrementally from the auth watcher;
# give the watcher a moment, then assert health and the restored order.
sleep 4
python3 /opt/cliproxyapi/cpa-health.py readiness || { log 'REFUSE_READINESS'; rollback 21; }
python3 - "$AUTH" <<'PY'
import glob, os, sys

auth_dir = sys.argv[1]
names = sorted(
    os.path.basename(p)
    for p in glob.glob(os.path.join(auth_dir, "workbuddy-*.json"))
)
cn = [n for n in names if not n.startswith("workbuddy-intl-")]
intl = [n for n in names if n.startswith("workbuddy-intl-")]
assert min(cn) > max(intl), "ORDER_STILL_BROKEN min_cn=%s max_intl=%s" % (min(cn), max(intl))
assert not [n for n in cn if not n.startswith("workbuddy-tier2-")], "legacy bare name remains"
print('ORDER_RESTORED cn=%d intl=%d INTL_THEN_CN' % (len(cn), len(intl)))
PY
check_idle
MUTATED=0
trap - ERR HUP INT TERM
exec 8<&- 2>/dev/null || true
log "RENAME_OK backup=$BK"
'@
$remote = $remote.Replace('__MODE__', $mode)
if ($remote -match '__MODE__') {
  throw 'Unfilled rename transaction mode.'
}
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile bwg -Command $remote `
  -IdleTimeoutSeconds 300 -HardTimeoutSeconds 900 -RemoteTempPrefix "wb-tier2-rename-20261107"
