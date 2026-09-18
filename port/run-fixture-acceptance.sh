#!/usr/bin/env bash
# One-shot fixture acceptance orchestration (runs ON bwg as root).
# Sequence: reset upstream-mode to http503 -> start acceptance in isolated
# mount+net ns in background -> once the "waiting" stage is logged (overload
# and cooldown assertions already passed, 62s recovery sleep begins) flip
# upstream-mode to ok so the recovery probe sees 200 -> wait and report.
set -u
FTS=20260918T004305Z
FIX=/tmp/cpa-fixture-$FTS
LOG=/tmp/acc-$FTS.log

echo http503 > "$FIX/upstream-mode"
rm -f "$LOG"
export FTS
(
  unshare -m -n --propagation private bash -c \
    'ip link set lo up; mount --bind /tmp/cpa-fixture-$FTS /opt/cliproxyapi; exec python3 /opt/cliproxyapi/cpa-acceptance.py' \
    >"$LOG" 2>&1
  echo "SCRIPT_EXIT=$?" >>"$LOG"
) &
for i in $(seq 1 60); do
  grep -q '"stage": "waiting"' "$LOG" 2>/dev/null && break
  sleep 1
done
echo ok > "$FIX/upstream-mode"
wait
echo "=== tail of acceptance log ==="
tail -30 "$LOG"
