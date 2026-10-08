set -Eeuo pipefail

# Same lock as auto-update.sh: the daily timer and guardrail transactions
# refuse to overlap instead of interleaving restarts and rollbacks.
exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo "REFUSE cpa_busy vps-ssh-launcher-maintenance.lock held"; exit 1; }

DIR=/opt/cliproxyapi
CONFIG="$DIR/config.yaml"
AUTH_DIR="$DIR/auth"
MARKER="$DIR/oauth-quarantine.json"
MODE=__CPA_OAUTH_QUARANTINE_MODE__
BK=/root/cpa-oauth-quarantine-backup-$(date -u +%Y%m%dT%H%M%S.%NZ)

prune_backup_history() {
  local keep=8 entry removed=0
  while IFS= read -r entry; do
    [ -n "$entry" ] || continue
    if rm -rf -- "$entry"; then
      removed=$((removed + 1))
    else
      echo "PRUNE_FAILED scope=cpa_oauth_quarantine_backups path=$entry"
    fi
  done < <(
    find /root -mindepth 1 -maxdepth 1 -type d \
      -name 'cpa-oauth-quarantine-backup-*' -printf '%T@ %p\n' 2>/dev/null |
      sort -rn | tail -n +$((keep + 1)) | cut -d' ' -f2-
  )
  echo "PRUNE scope=cpa_oauth_quarantine_backups removed=$removed policy=keep_$keep"
}

# This transaction is a reversible traffic stop for the OAuth lane, not a
# credential operation and not a risk-control "reset": the Codex OAuth JSON is
# never read, copied, deleted or replayed, background token refresh keeps
# running so the slot does not expire, and no quota/cooldown state is cleared.
# It works by removing the OAuth route aliases from the routed catalog through
# oauth-excluded-models.codex, which is the same mechanism -Apply uses in the
# opposite direction, and records the decision in a marker file that the
# semantic policy and strict doctor both read.
case "$MODE" in
  quarantine|restore) : ;;
  *) echo "REFUSE unknown quarantine mode"; exit 1 ;;
esac

if ! mkdir -m 700 "$BK"; then
  echo "REFUSE backup_exists_or_create_failed path=$BK"
  exit 1
fi
cp -a "$CONFIG" "$BK/config.yaml"
MARKER_PREEXISTED=0
if [ -f "$MARKER" ]; then
  cp -a "$MARKER" "$BK/oauth-quarantine.json"
  MARKER_PREEXISTED=1
fi
chmod 700 "$BK"

ROLLBACK_DONE=0
restore_all() {
  if [ "$ROLLBACK_DONE" -eq 1 ]; then return 0; fi
  ROLLBACK_DONE=1
  trap - EXIT INT TERM
  set +e
  # A refusal (unknown mode, already quarantined, no marker to restore, drifted
  # marker) must not cost a container restart. When config.yaml is byte-identical
  # to the backup nothing was mutated and the running container still holds the
  # pre-transaction config, so restarting would only add an outage and blur the
  # refusal signal into an apparently-verified rollback.
  config_mutated=0
  if ! cmp -s "$BK/config.yaml" "$CONFIG"; then
    config_mutated=1
  fi
  rollback_failed=0
  cp -a "$BK/config.yaml" "$CONFIG" || rollback_failed=1
  chmod 600 "$CONFIG" || rollback_failed=1
  if [ "$MARKER_PREEXISTED" -eq 1 ]; then
    cp -a "$BK/oauth-quarantine.json" "$MARKER" || rollback_failed=1
    chmod 600 "$MARKER" || rollback_failed=1
  else
    rm -f "$MARKER" || rollback_failed=1
  fi
  if [ "$config_mutated" -eq 0 ]; then
    if [ "$rollback_failed" -eq 0 ]; then
      echo "ROLLBACK_SKIPPED no_mutation"
    else
      echo "ROLLBACK_FAILED"
    fi
    set -e
    return 0
  fi
  docker restart cli-proxy-api >/dev/null 2>&1 || rollback_failed=1
  if [ -f "$DIR/cpa-health.py" ]; then
    python3 "$DIR/cpa-health.py" readiness >/dev/null 2>&1 || rollback_failed=1
  else
    rollback_failed=1
  fi
  if [ "$rollback_failed" -eq 0 ]; then
    echo "ROLLBACK_VERIFIED"
  else
    echo "ROLLBACK_FAILED"
  fi
  set -e
  return 0
}

rollback_on_exit() {
  rc=$?
  trap - EXIT INT TERM
  if [ "$rc" -ne 0 ]; then
    restore_all
    echo "ROLLBACK transaction_failed"
  fi
  exit "$rc"
}

if [ ! -f "$CONFIG" ]; then
  echo "REFUSE config.yaml missing"
  exit 1
fi
if ! grep -Eq '^[[:space:]]*force-model-prefix:[[:space:]]*true[[:space:]]*$' "$CONFIG"; then
  echo "REFUSE unexpected CPA routing policy"
  exit 1
fi
# Require exactly one active Codex OAuth credential: the quarantine must stop
# traffic while leaving a reversible, refreshable slot behind. Anything else is
# an unexpected topology and is refused before the first mutation.
if ! python3 - "$AUTH_DIR" <<'PY'
import json
import sys
from pathlib import Path

active = 0
for path in sorted(Path(sys.argv[1]).glob("*.json")):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        raise SystemExit("REFUSE unreadable auth JSON: %s" % path.name)
    if not isinstance(data, dict):
        raise SystemExit("REFUSE non-object auth JSON")
    keys = {str(key).lower() for key in data}
    if {"access_token", "refresh_token"} & keys:
        if str(data.get("type", "")).lower() != "codex":
            raise SystemExit("REFUSE unexpected OAuth auth type")
        active += 1
if active != 1:
    raise SystemExit("REFUSE expected exactly one active Codex OAuth credential")
print("ACTIVE_OAUTH_FILES=%d" % active)
PY
then
  echo "OAUTH_TOPOLOGY_CHECK_FAILED"
  exit 1
fi

# Do not use the quarantine transaction to repair an already-drifted config.
# The semantic policy is the same source of truth used by strict doctor and
# must pass before this transaction is allowed to mutate config.yaml. In
# restore mode it also validates the active marker before any write.
if [ ! -f "$DIR/cpa_policy.py" ]; then
  echo "REFUSE cpa_policy.py missing"
  exit 1
fi
if ! python3 "$DIR/cpa_policy.py" "$CONFIG" >/tmp/cpa-quarantine-preflight.log 2>&1; then
  echo "REFUSE baseline_policy"
  tail -n 20 /tmp/cpa-quarantine-preflight.log
  rm -f /tmp/cpa-quarantine-preflight.log
  exit 1
fi
rm -f /tmp/cpa-quarantine-preflight.log

trap rollback_on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if ! python3 - "$CONFIG" "$MARKER" "$MODE" <<'PY'
import json
import os
import sys
import tempfile
from base64 import b64decode
from datetime import datetime, timezone
from pathlib import Path

import yaml

config_path = Path(sys.argv[1])
marker_path = Path(sys.argv[2])
mode = sys.argv[3]
manifest = json.loads(
    b64decode("__CPA_OAUTH_QUARANTINE_MANIFEST_B64__").decode("utf-8")
)
oauth_aliases = [
    model["alias"]
    for route in manifest.get("oauth_routes", [])
    if isinstance(route, dict)
    for model in route.get("models", [])
    if isinstance(model, dict) and isinstance(model.get("alias"), str)
]
if not oauth_aliases:
    raise SystemExit("REFUSE invalid route manifest")

config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
if not isinstance(config, dict) or config.get("force-model-prefix") is not True:
    raise SystemExit("REFUSE unexpected CPA routing policy")
exclusions = config.get("oauth-excluded-models")
if exclusions is None:
    exclusions = {}
if not isinstance(exclusions, dict):
    raise SystemExit("REFUSE oauth-excluded-models must be a mapping")
codex = exclusions.get("codex", [])
if not isinstance(codex, list) or not all(
    isinstance(pattern, str) and pattern.strip() for pattern in codex
):
    raise SystemExit("REFUSE oauth-excluded-models.codex must be a list of strings")

targets = {alias.lower() for alias in oauth_aliases}
present = {pattern.strip().lower() for pattern in codex}


def atomic_write(path, text, mode_bits):
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.chmod(temp, mode_bits)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


if mode == "quarantine":
    if marker_path.exists():
        raise SystemExit("REFUSE OAuth lane is already quarantined")
    previous = list(codex)
    after = list(codex) + [
        alias for alias in oauth_aliases if alias.lower() not in present
    ]
else:
    if not marker_path.exists():
        raise SystemExit("REFUSE no OAuth quarantine marker to restore")
    try:
        marker_before = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit("REFUSE unreadable quarantine marker: %s" % type(exc).__name__)
    if not isinstance(marker_before, dict) or marker_before.get("state") != "quarantined":
        raise SystemExit("REFUSE unexpected quarantine marker state")
    marker_aliases = marker_before.get("aliases")
    if not isinstance(marker_aliases, list) or {
        str(alias).strip().lower() for alias in marker_aliases
    } != targets:
        raise SystemExit("REFUSE quarantine marker aliases do not match route manifest")
    previous = marker_before.get("previous_codex_exclusions")
    applied = marker_before.get("applied_codex_exclusions")
    if not isinstance(previous, list) or not all(
        isinstance(pattern, str) and pattern.strip() for pattern in previous
    ):
        raise SystemExit("REFUSE quarantine marker has no valid previous exclusions")
    if not isinstance(applied, list) or codex != applied:
        raise SystemExit("REFUSE OAuth quarantine config drift detected")
    after = list(previous)

config_after = dict(config)
config_after["oauth-excluded-models"] = dict(exclusions)
config_after["oauth-excluded-models"]["codex"] = after
candidate = yaml.safe_dump(
    config_after, allow_unicode=True, default_flow_style=False, sort_keys=False
)
if yaml.safe_load(candidate) != config_after:
    raise SystemExit("candidate YAML semantic round-trip failed")
atomic_write(config_path, candidate, 0o600)

if mode == "quarantine":
    marker = {
        "version": 1,
        "state": "quarantined",
        "aliases": sorted(oauth_aliases),
        "previous_codex_exclusions": previous,
        "applied_codex_exclusions": after,
        "since": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reason": "operator_requested_risk_control",
    }
    atomic_write(marker_path, json.dumps(marker, indent=2) + "\n", 0o600)
    print("QUARANTINE_APPLIED aliases=" + ",".join(sorted(oauth_aliases)))
else:
    marker_path.unlink()
    print("QUARANTINE_RELEASED aliases=" + ",".join(sorted(oauth_aliases)))
PY
then
  restore_all
  echo "ROLLBACK quarantine_state_change"
  exit 1
fi

if ! docker restart cli-proxy-api >/dev/null; then
  restore_all
  echo "ROLLBACK cpa_restart"
  exit 1
fi

KEY=$(python3 - "$CONFIG" <<'PY'
import sys
import yaml

config = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
keys = config.get("api-keys") if isinstance(config, dict) else None
if not isinstance(keys, list) or not keys or not isinstance(keys[0], str) or not keys[0]:
    raise SystemExit(1)
print(keys[0])
PY
)
if [ -z "$KEY" ]; then
  restore_all
  echo "ROLLBACK missing_client_key"
  exit 1
fi

READY=000
for _ in $(seq 1 30); do
  READY=$(curl --noproxy '*' -sS --max-time 5 -o /tmp/cpa-quarantine-catalog.json -w '%{http_code}' \
    -H "Authorization: Bearer $KEY" http://127.0.0.1:8317/v1/models || true)
  if [ "$READY" = "200" ]; then
    break
  fi
  sleep 1
done
if [ "$READY" != "200" ]; then
  rm -f /tmp/cpa-quarantine-catalog.json
  restore_all
  echo "ROLLBACK cpa_readiness status=$READY"
  exit 1
fi

if ! python3 - "$DIR" "$MODE" /tmp/cpa-quarantine-catalog.json <<'PY'
import json
import sys
from pathlib import Path

import yaml

root = Path(sys.argv[1])
mode = sys.argv[2]
catalog = json.load(open(sys.argv[3], encoding="utf-8"))
ids = {
    item["id"]
    for item in catalog.get("data", [])
    if isinstance(item, dict) and isinstance(item.get("id"), str)
}
manifest = json.loads((root / "cpa_provider_routes.json").read_text(encoding="utf-8"))
oauth_aliases = sorted(
    {
        model["alias"]
        for route in manifest.get("oauth_routes", [])
        if isinstance(route, dict)
        for model in route.get("models", [])
        if isinstance(model, dict) and isinstance(model.get("alias"), str)
    }
)
provider_aliases = {
    model["alias"]
    for provider in manifest.get("providers", [])
    if isinstance(provider, dict)
    for model in provider.get("models", [])
    if isinstance(model, dict) and isinstance(model.get("alias"), str)
}
optional = {
    model
    for provider in manifest.get("providers", [])
    if isinstance(provider, dict)
    for model in provider.get("optional_models", [])
    if isinstance(model, str)
}
unknown = sorted(ids - provider_aliases)
if unknown:
    raise SystemExit("CPA_ROUTE_VERIFICATION_FAILED unknown=%s" % ",".join(unknown))
if mode == "quarantine":
    survived = sorted(set(oauth_aliases) & ids)
    if survived:
        raise SystemExit(
            "CPA_ROUTE_VERIFICATION_FAILED survived=%s" % ",".join(survived)
        )
    missing = sorted((provider_aliases - optional) - ids)
    if missing:
        print("CATALOG_INCOMPLETE missing=%s" % ",".join(missing))
else:
    config = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    exclusions = config.get("oauth-excluded-models") if isinstance(config, dict) else None
    codex = exclusions.get("codex", []) if isinstance(exclusions, dict) else []
    patterns = {
        pattern.strip().lower()
        for pattern in codex
        if isinstance(pattern, str) and pattern.strip()
    }
    still_blocked = sorted(alias for alias in oauth_aliases if alias.lower() in patterns)
    if still_blocked:
        raise SystemExit(
            "QUARANTINE_RESTORE_FAILED still_blocked=%s" % ",".join(still_blocked)
        )
    restored = sorted(set(oauth_aliases) & ids)
    print(
        "RESTORED_OAUTH_ALIASES="
        + (",".join(restored) if restored else "pending_catalog")
    )
PY
then
  rm -f /tmp/cpa-quarantine-catalog.json
  restore_all
  echo "ROLLBACK quarantine_verification"
  exit 1
fi
rm -f /tmp/cpa-quarantine-catalog.json

if ! python3 "$DIR/cpa_policy.py" "$CONFIG" >/tmp/cpa-quarantine-policy.log 2>&1; then
  restore_all
  echo "ROLLBACK quarantine_policy"
  tail -n 20 /tmp/cpa-quarantine-policy.log
  exit 1
fi

trap - EXIT INT TERM
echo "BACKUP_DIR=$BK"
echo "QUARANTINE_MODE=$MODE"
echo "OAUTH_CREDENTIAL_RETAINED=yes"
echo "QUOTA_STATE_RESET=no"
echo "READY_STATUS=$READY"
prune_backup_history
