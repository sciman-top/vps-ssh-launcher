#!/bin/bash
# Start the broker before admission has any request threads. The broker is the
# only owner of request-lock descriptors, so a retired reader cannot hold a
# maintenance lock. The admission executable itself stays unchanged.
set -Eeuo pipefail

# The bootstrap forks the only lock owner while still single-threaded. Raise
# Python's soft RLIMIT_NOFILE back to the unit's hard ceiling first so the
# forked broker does not inherit Python's much smaller startup ceiling.
fd_low=
fd_high=
line=
while IFS= read -r line; do
  if [[ $line =~ Max[[:space:]]+open[[:space:]]+files[[:space:]]+([0-9]+)[[:space:]]+([0-9]+) ]]; then
    fd_low=${BASH_REMATCH[1]}
    fd_high=${BASH_REMATCH[2]}
    break
  fi
done < /proc/self/limits
[[ ${fd_low:-} =~ ^[0-9]+$ && ${fd_high:-} =~ ^[0-9]+$ ]] || {
  echo ADMISSION_LAUNCHER_REFUSE invalid_fd_limit >&2
  exit 1
}
[[ $fd_high -ge 4096 ]] || {
  echo ADMISSION_LAUNCHER_REFUSE fd_limit_below_reviewed_baseline >&2
  exit 1
}

unset PYTHONSTARTUP PYTHONHOME
export PYTHONPATH=/usr/local/libexec
exec /usr/bin/python3 -P -c '
import resource, sys
from pathlib import Path
resource.setrlimit(resource.RLIMIT_NOFILE, (int(sys.argv[1]), int(sys.argv[2])))
namespace = __import__("runpy").run_path(str(Path(sys.argv[3])), run_name="bootstrap")
sys.argv = sys.argv[3:]
main = namespace["main"]
raise SystemExit(main())
' "$fd_low" "$fd_high" /usr/local/libexec/cpa_admission_bootstrap.py \
  /usr/bin/python3 /opt/cliproxyapi/cpa-admission.py \
  /opt/cliproxyapi/cpa-admission.json \
  --state-file /var/lib/cpa-admission/antigravity-risk.json \
  --claude-state-file /var/lib/cpa-admission/claude-risk.json \
  --chatgpt-state-file /var/lib/cpa-admission/chatgpt-risk.json \
  --workbuddy-state-file /var/lib/cpa-admission/workbuddy-risk.json
