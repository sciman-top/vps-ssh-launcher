#requires -Version 7
# 2026-11-17 antigravity-oauth lane operator-review latch release (single
# transaction, 10-31 precedent): idle check -> verified backup -> stop
# cpa-admission -> assert + null hold_reason -> start -> healthz recheck ->
# wait out the 90s stale write-ahead window -> ONE loopback single-shot on
# gemini-3.8-flash-high ONLY (hydrogen/sonnet must NOT be probed while the
# mamo VALIDATION_REQUIRED challenge is unresolved: a single upstream 403
# re-latches the whole lane). Keys/tokens never echoed; any assertion
# failure restores the backup and restarts admission before exiting.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -u
set -o pipefail
TS=$(date -u +%Y%m%dT%H%M%SZ)
BK=/root/antigravity-risk-backup-$TS.json
RF=/var/lib/cpa-admission/antigravity-risk.json
ROOT=$(mktemp -d /root/ag-rel-XXXXXXXX)
trap 'rm -rf --one-file-system -- "$ROOT"' EXIT
echo "== phase 1: all-lane idle check"
curl -s --max-time 5 http://127.0.0.1:8318/healthz > "$ROOT/healthz.json"
python3 - "$ROOT/healthz.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
busy = []
for name, lane in sorted(d.get("lanes", {}).items()):
    st = lane.get("state", {})
    print("LANE %s inflight=%s pending=%s hold=%r" % (name, st.get("inflight"), st.get("pending"), st.get("hold_reason")))
    if st.get("inflight", 0) or st.get("pending", 0):
        busy.append(name)
if busy:
    print("IDLE_CHECK=FAIL busy=%s" % ",".join(busy)); sys.exit(3)
print("IDLE_CHECK=PASS")
PY
if [ $? -ne 0 ]; then echo "ABORT=not_idle"; exit 3; fi
echo "== phase 2: verified backup"
cp -p "$RF" "$BK"
python3 - "$RF" "$BK" <<'PY'
import sys, os, hashlib
rf, bk = sys.argv[1], sys.argv[2]
a = open(rf, "rb").read(); b = open(bk, "rb").read()
assert a == b, "BACKUP_MISMATCH"
sr, sb = os.stat(rf), os.stat(bk)
assert (sr.st_uid, sr.st_gid, sr.st_mode & 0o777) == (sb.st_uid, sb.st_gid, sb.st_mode & 0o777), "BACKUP_META_MISMATCH"
print("BACKUP_VERIFIED bytes=%d sha256_16=%s owner=%d:%d mode=%o" % (len(a), hashlib.sha256(a).hexdigest()[:16], sr.st_uid, sr.st_gid, sr.st_mode & 0o777))
PY
if [ $? -ne 0 ]; then echo "ABORT=backup_failed"; rm -f "$BK"; exit 3; fi
echo "== phase 3: stop -> assert -> null hold_reason -> start"
systemctl stop cpa-admission
sleep 1
RC=0
python3 - "$RF" <<'PY' || RC=$?
import json, os, sys
rf = sys.argv[1]
st = os.stat(rf)
assert (st.st_mode & 0o777) == 0o600, "MODE_UNEXPECTED %o" % (st.st_mode & 0o777)
data = json.loads(open(rf).read())
assert data.get("version") == 1 and data.get("not_before") == 0 and data.get("hold_reason") == "authentication_required", "STATE_UNEXPECTED %r" % (data,)
data["hold_reason"] = None
tmp = rf + ".rel-tmp"
open(tmp, "w").write(json.dumps(data))
os.chmod(tmp, st.st_mode & 0o777)
os.chown(tmp, st.st_uid, st.st_gid)
os.replace(tmp, rf)
print("RELEASE_OK now=%s" % (open(rf).read(),))
PY
systemctl start cpa-admission
if [ "$RC" -ne 0 ]; then
  echo "RELEASE_ASSERT_FAILED rc=$RC restoring backup"
  cp -p "$BK" "$RF"
  systemctl restart cpa-admission
  echo "RESTORED_AND_STARTED"
  exit 4
fi
echo "== phase 4: immediate healthz"
sleep 8
curl -s --max-time 5 http://127.0.0.1:8318/healthz | python3 - <<'PY'
import json, sys
d = json.load(sys.stdin)
bad = []
for name, lane in sorted(d.get("lanes", {}).items()):
    h = lane.get("state", {}).get("hold_reason")
    print("LANE %s hold=%r" % (name, h))
    if h is not None:
        bad.append((name, h))
print("HOLDS_PRESENT=%s" % bad if bad else "ALL_HOLDS_NULL")
PY
echo "== phase 5: wait out stale write-ahead window (90s) then recheck"
sleep 90
curl -s --max-time 5 http://127.0.0.1:8318/healthz > "$ROOT/healthz2.json"
python3 - "$ROOT/healthz2.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
ag = d["lanes"]["antigravity-oauth"]["state"]
print("AG_HOLD=%r strict_backoff=%r failure_generation=%r inflight=%s pending=%s" % (ag.get("hold_reason"), ag.get("strict_backoff"), ag.get("failure_generation"), ag.get("inflight"), ag.get("pending")))
bad = [(n, l["state"].get("hold_reason")) for n, l in d.get("lanes", {}).items() if l["state"].get("hold_reason") is not None]
if bad:
    print("HOLDS_PRESENT=%s" % bad); sys.exit(5)
print("STABLE_ALL_NULL")
PY
if [ $? -ne 0 ]; then echo "ABORT=hold_reappeared_no_probe"; exit 5; fi
echo "== phase 6: loopback single-shot gemini-3.8-flash-high ONLY"
API_KEY=$(python3 -c 'import yaml,pathlib;print(yaml.safe_load(pathlib.Path("/opt/cliproxyapi/config.yaml").read_text())["access"]["api-keys"][0])')
st=$(curl --silent --show-error --max-time 120 --output "$ROOT/flash.body" --write-out "%{http_code}" \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  --data '{"model":"gemini-3.8-flash-high","stream":true,"messages":[{"role":"user","content":"Return the word PONG only."}]}' \
  http://127.0.0.1:8318/v1/chat/completions)
echo "FLASH_STATUS=$st"
python3 - "$ROOT/flash.body" <<'PY'
import json, sys
text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
parts = []; terminal = False; events = 0
for line in text.splitlines():
    if not line.startswith("data: "):
        continue
    payload = line[6:].strip()
    if payload == "[DONE]":
        terminal = True; continue
    try:
        ev = json.loads(payload)
    except ValueError:
        continue
    events += 1
    ch = (ev.get("choices") or [{}])[0]
    parts.append(str((ch.get("delta") or {}).get("content") or ""))
print("FLASH_SSE_EVENTS=%d TERMINAL=%s TEXT=%r" % (events, str(terminal).lower(), "".join(parts).strip()))
for marker in ("VALIDATION_REQUIRED", "PERMISSION_DENIED", "operator_review_required"):
    if marker in text:
        print("FLASH_SIGNAL=%s" % marker)
if not terminal:
    print("FLASH_RAW_HEAD=%r" % text[:300])
PY
echo "== phase 7: admission journal tail (probe window)"
journalctl -u cpa-admission --since '2 minutes ago' --no-pager | grep 'antigravity' | tail -8
echo REL_SCRIPT_DONE
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 240 -HardTimeoutSeconds 400
