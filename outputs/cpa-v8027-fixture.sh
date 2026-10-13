#!/usr/bin/env bash
# BWG CPA fixture acceptance for v8.0.27 (2026-10-13 UTC): disposable
# mount+net namespace only; the production container and real providers are
# never touched. Success is judged by the literal ACCEPTANCE_RESULT=PASS
# line of cpa-acceptance.py, never by exit codes alone.
set -Eeuo pipefail
umask 077
if [[ "${VPS_SSH_LAUNCHER_PROFILE:-}" != "bwg" ]]; then
  echo BWG_PROFILE_REQUIRED >&2
  exit 64
fi
TARGET='eceasy/cli-proxy-api:v8.0.27@sha256:b7cf456bf6ef7f6489f18144e7cc166dbb8b2e5f46d6ee8881a321aaea563730'
ACC=/tmp/cpa-acceptance-v8027.py
UPD=/tmp/cpa-update-acceptance-v8027.py

echo '== step 2: pull target image (VPS-direct digest source) =='
docker pull "$TARGET"
docker image inspect "$TARGET" >/dev/null || { echo 'IMAGE_INSPECT_FAILED'; exit 2; }

echo '== step 3: fixture acceptance =='
FIX=$(mktemp -d /tmp/cpa-accept-v8027.XXXXXX)
chmod 700 "$FIX"
echo "FIXTURE_ROOT=$FIX"
mkdir -p "$FIX/auth"
for f in config.yaml compose.yml cpa-health.py auto-update.sh cpa_policy.py cpa_provider_routes.json; do
  cp -a "/opt/cliproxyapi/$f" "$FIX/$f"
done
printf 'fixture-only\n' > "$FIX/FIXTURE_ONLY"
install -m 700 "$ACC" "$FIX/cpa-acceptance.py"
install -m 700 "$UPD" "$FIX/cpa-update-acceptance.py"

CID=$(docker create "$TARGET")
docker cp "$CID:/CLIProxyAPI/CLIProxyAPI" "$FIX/CLIProxyAPI"
docker rm "$CID" >/dev/null
chmod 700 "$FIX/CLIProxyAPI"
echo '== fixture binary sha (prefix)'
sha256sum "$FIX/CLIProxyAPI" | cut -c1-16

RUNLOG=$(mktemp /tmp/cpa-accept-v8027-run.XXXXXX)
set +e
unshare -m -n bash -c '
  set -Eeuo pipefail
  mount --bind "$1" /opt/cliproxyapi
  cd /opt/cliproxyapi
  exec python3 /opt/cliproxyapi/cpa-acceptance.py
' fixture "$FIX" 2>&1 | tee "$RUNLOG"
rc=${PIPESTATUS[0]}
set -e

if grep -q '^ACCEPTANCE_RESULT=PASS$' "$RUNLOG"; then
  echo "ACCEPTANCE_EXIT=$rc"
else
  echo "FIXTURE_ACCEPTANCE_FAILED exit=$rc"
  tail -40 "$RUNLOG"
  rm -f "$RUNLOG"
  exit 1
fi

echo '== leftover fixture processes (absolute /opt/cliproxyapi path = leak) =='
scan_leaks() {
  local p exe
  for p in /proc/[0-9]*; do
    exe=$(readlink "$p/exe" 2>/dev/null) || continue
    case "$exe" in
      /opt/cliproxyapi/*) echo "${p#/proc/} $exe" ;;
    esac
  done
}
LEAKS=$(scan_leaks)
if [[ -n "$LEAKS" ]]; then
  sleep 3
  LEAKS=$(scan_leaks)
fi
if [[ -n "$LEAKS" ]]; then
  echo "$LEAKS"
  echo 'CLEANUP_FAILED leak=absolute-opt-path'
  rm -f "$RUNLOG"
  exit 1
fi
rm -rf "$FIX" /tmp/cpa-acceptance-v8027.py /tmp/cpa-update-acceptance-v8027.py
echo "CLEANUP_OK root=$FIX"
