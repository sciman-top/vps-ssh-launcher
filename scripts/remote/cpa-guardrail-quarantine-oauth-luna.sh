set -Eeuo pipefail

# Same lock as auto-update.sh: the daily timer and guardrail transactions
# refuse to overlap instead of interleaving restarts and rollbacks.
exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo "REFUSE cpa_busy vps-ssh-launcher-maintenance.lock held"; exit 1; }

DIR=/opt/cliproxyapi
CONFIG="$DIR/config.yaml"
AUTH_DIR="$DIR/auth"
export CPA_OAUTH_QUARANTINE_PROVIDER=__CPA_OAUTH_QUARANTINE_PROVIDER__
case "$CPA_OAUTH_QUARANTINE_PROVIDER" in
  codex) MARKER="$DIR/oauth-quarantine.json" ;;
  antigravity) MARKER="$DIR/antigravity-quarantine.json" ;;
  claude) MARKER="$DIR/claude-oauth-quarantine.json" ;;
  *) echo "REFUSE unknown quarantine provider"; exit 1 ;;
esac
MARKER_BASENAME=$(basename -- "$MARKER")
MODE=__CPA_OAUTH_QUARANTINE_MODE__
BK=/root/cpa-oauth-quarantine-backup-$(date -u +%Y%m%dT%H%M%S.%NZ)
MARKER_PREEXISTED=0

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

check_claude_catalog_state() {
  local expected_state="$1" catalog_path="$2"
  python3 - "$DIR" "$expected_state" "$catalog_path" <<'PY' >/dev/null 2>&1
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
expected = sys.argv[2]
catalog = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
manifest = json.loads((root / "cpa_provider_routes.json").read_text(encoding="utf-8"))
aliases = {
    model["alias"].lower()
    for route in manifest.get("oauth_routes", [])
    if isinstance(route, dict) and route.get("name") == "claude-oauth"
    for model in route.get("models", [])
    if isinstance(model, dict) and isinstance(model.get("alias"), str)
}
ids = {
    item["id"].lower()
    for item in catalog.get("data", [])
    if isinstance(item, dict) and isinstance(item.get("id"), str)
}
if not aliases:
    raise SystemExit(1)
if expected == "blocked":
    raise SystemExit(1 if aliases & ids else 0)
if expected == "active":
    raise SystemExit(0 if aliases <= ids else 1)
raise SystemExit(1)
PY
}

claude_lane_idle_sample() {
  curl --noproxy '*' -fsS --max-time 4 http://127.0.0.1:8318/healthz |
    python3 -c '
import json, sys
data = json.load(sys.stdin)
lanes = data.get("lanes")
lane = lanes.get("claude-oauth") if isinstance(lanes, dict) else None
state = lane.get("state") if isinstance(lane, dict) else None
if data.get("status") != "ok": raise SystemExit(1)
if not isinstance(state, dict): raise SystemExit(1)
required = ("inflight", "pending", "half_open_probe", "state_write_failed")
if any(key not in state for key in required): raise SystemExit(1)
if state.get("inflight") != 0 or state.get("pending") != 0: raise SystemExit(1)
if state.get("half_open_probe") is not False: raise SystemExit(1)
if state.get("state_write_failed") is not False: raise SystemExit(1)
'
}

# This transaction is a reversible traffic stop for the OAuth lane, not a
# credential operation and not a risk-control "reset": the OAuth JSON is
# only inspected for topology, never changed, copied, deleted or replayed;
# background token refresh keeps
# running so the slot does not expire, and no quota/cooldown state is cleared.
# It works by removing the OAuth route aliases from the routed catalog through
# the selected provider's excluded-models, the same mechanism -Apply uses in the
# opposite direction, and records the decision in a marker file that the
# semantic policy and strict doctor both read.
case "$MODE" in
  quarantine|restore) : ;;
  *) echo "REFUSE unknown quarantine mode"; exit 1 ;;
esac

ROLLBACK_DONE=0
restore_claude_config_in_place() {
  python3 - "$BK/config.yaml" "$CONFIG" <<'PY'
import os
import sys
from pathlib import Path

source = Path(sys.argv[1]).read_bytes()
flags = os.O_WRONLY | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
fd = os.open(sys.argv[2], flags)
try:
    os.fchmod(fd, 0o600)
    pending = memoryview(source)
    while pending:
        written = os.write(fd, pending)
        if written <= 0:
            raise OSError("short write while restoring watched CPA config")
        pending = pending[written:]
    os.fsync(fd)
finally:
    os.close(fd)
PY
}

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
  policy_mutated=0
  if [ -f "$BK/cpa_policy.py" ] && ! cmp -s "$BK/cpa_policy.py" "$DIR/cpa_policy.py"; then
    policy_mutated=1
  fi
  marker_mutated=0
  if [ "$MARKER_PREEXISTED" -eq 1 ]; then
    if ! cmp -s "$BK/$MARKER_BASENAME" "$MARKER"; then
      marker_mutated=1
    fi
  elif [ -e "$MARKER" ]; then
    marker_mutated=1
  fi
  rollback_failed=0
  if [ "$config_mutated" -eq 1 ]; then
    if [ "$CPA_OAUTH_QUARANTINE_PROVIDER" = "claude" ]; then
      restore_claude_config_in_place || rollback_failed=1
      cmp -s "$BK/config.yaml" "$CONFIG" || rollback_failed=1
    else
      cp -a "$BK/config.yaml" "$CONFIG" || rollback_failed=1
      chmod 600 "$CONFIG" || rollback_failed=1
    fi
  fi
  if [ "$MARKER_PREEXISTED" -eq 1 ]; then
    cp -a "$BK/$MARKER_BASENAME" "$MARKER" || rollback_failed=1
    cmp -s "$BK/$MARKER_BASENAME" "$MARKER" || rollback_failed=1
    chmod 600 "$MARKER" || rollback_failed=1
  else
    rm -f "$MARKER" || rollback_failed=1
  fi
  if [ -f "$BK/cpa_policy.py" ]; then
    cp -a "$BK/cpa_policy.py" "$DIR/cpa_policy.py" || rollback_failed=1
    cmp -s "$BK/cpa_policy.py" "$DIR/cpa_policy.py" || rollback_failed=1
  fi
  if [ "$config_mutated" -eq 0 ]; then
    if [ "$rollback_failed" -eq 0 ]; then
      if [ "$policy_mutated" -eq 0 ] && [ "$marker_mutated" -eq 0 ]; then
        echo "ROLLBACK_SKIPPED no_mutation"
      else
        echo "ROLLBACK_VERIFIED helper_state_restored"
      fi
    else
      echo "ROLLBACK_FAILED"
    fi
    set -e
    return 0
  fi
  if [ "$CPA_OAUTH_QUARANTINE_PROVIDER" = "claude" ]; then
    rollback_state=active
    [ "$MODE" = "quarantine" ] || rollback_state=blocked
    rollback_verified=0
    for _ in $(seq 1 30); do
      rollback_status=$(curl --noproxy '*' -sS --max-time 5 \
        -o "$BK/cpa-quarantine-rollback-catalog.json" -w '%{http_code}' \
        -H "Authorization: Bearer ${KEY:-}" http://127.0.0.1:8317/v1/models || true)
      if [ "$rollback_status" = "200" ] && \
         check_claude_catalog_state "$rollback_state" "$BK/cpa-quarantine-rollback-catalog.json"; then
        rollback_verified=1
        break
      fi
      sleep 1
    done
    rm -f "$BK/cpa-quarantine-rollback-catalog.json"
    if [ "$rollback_verified" -ne 1 ]; then
      rollback_failed=1
    fi
  else
    docker restart cli-proxy-api >/dev/null 2>&1 || rollback_failed=1
    if [ -f "$DIR/cpa-health.py" ]; then
      python3 "$DIR/cpa-health.py" readiness >/dev/null 2>&1 || rollback_failed=1
    else
      rollback_failed=1
    fi
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

if [ ! -f "$CONFIG" ] || [ -L "$CONFIG" ]; then
  echo "REFUSE config.yaml missing_or_not_regular"
  exit 1
fi
KEY=$(python3 - "$CONFIG" <<'PY'
import sys
import yaml

config = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
access = config.get("access") if isinstance(config, dict) else None
keys = access.get("api-keys") if isinstance(access, dict) else None
if not isinstance(keys, list) or not keys or not isinstance(keys[0], str) or not keys[0]:
    raise SystemExit(1)
print(keys[0])
PY
) || { echo "REFUSE missing_client_key"; exit 1; }
if ! grep -Eq '^[[:space:]]*force-model-prefix:[[:space:]]*true[[:space:]]*$' "$CONFIG"; then
  echo "REFUSE unexpected CPA routing policy"
  exit 1
fi
# Require exactly one credential for the selected provider: quarantine must stop
# traffic while leaving a reversible, refreshable slot behind. Anything else is
# an unexpected topology and is refused before the first mutation.
if ! python3 - "$AUTH_DIR" <<'PY'
import json
import os
import sys
from pathlib import Path

active = 0
provider = os.environ.get("CPA_OAUTH_QUARANTINE_PROVIDER", "codex")
for path in sorted(Path(sys.argv[1]).glob("*.json")):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        raise SystemExit("REFUSE unreadable auth JSON")
    if not isinstance(data, dict):
        raise SystemExit("REFUSE non-object auth JSON")
    keys = {str(key).lower() for key in data}
    if {"access_token", "refresh_token"} & keys:
        auth_type = str(data.get("type", "")).lower()
        if auth_type not in ("codex", "antigravity", "claude"):
            raise SystemExit("REFUSE unexpected OAuth auth type")
        if auth_type == provider:
            active += 1
if active != 1:
    raise SystemExit("REFUSE expected exactly one active credential for selected OAuth provider")
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
if [ ! -f "$DIR/cpa_policy.py" ] || [ -L "$DIR/cpa_policy.py" ]; then
  echo "REFUSE cpa_policy.py missing_or_not_regular"
  exit 1
fi
if [ -L "$MARKER" ]; then
  echo "REFUSE OAuth quarantine marker must not be a symlink"
  exit 1
fi
PREFLIGHT_LOG=$(mktemp /tmp/cpa-quarantine-preflight.XXXXXXXXXX) || exit 1
if ! python3 "$DIR/cpa_policy.py" "$CONFIG" >"$PREFLIGHT_LOG" 2>&1; then
  echo "REFUSE baseline_policy"
  tail -n 20 "$PREFLIGHT_LOG"
  rm -f "$PREFLIGHT_LOG"
  exit 1
fi
rm -f "$PREFLIGHT_LOG"

if [ "$CPA_OAUTH_QUARANTINE_PROVIDER" = "claude" ]; then
  expected_before=active
  [ "$MODE" = "quarantine" ] || expected_before=blocked
  BEFORE_CATALOG=$(mktemp /tmp/cpa-quarantine-before.XXXXXXXXXX) || exit 1
  before_status=$(curl --noproxy '*' -sS --max-time 10 \
    -o "$BEFORE_CATALOG" -w '%{http_code}' \
    -H "Authorization: Bearer $KEY" http://127.0.0.1:8317/v1/models || true)
  if [ "$before_status" != "200" ] || \
     ! check_claude_catalog_state "$expected_before" "$BEFORE_CATALOG"; then
    rm -f "$BEFORE_CATALOG"
    echo "REFUSE Claude catalog does not match the requested transition state"
    exit 1
  fi
  rm -f "$BEFORE_CATALOG"
fi

if ! mkdir -m 700 "$BK"; then
  echo "REFUSE backup_exists_or_create_failed path=$BK"
  exit 1
fi
cp -a "$CONFIG" "$BK/config.yaml"
cmp -s "$CONFIG" "$BK/config.yaml" || { echo "REFUSE config backup verification failed"; exit 1; }
if [ -f "$MARKER" ]; then
  cp -a "$MARKER" "$BK/$MARKER_BASENAME"
  cmp -s "$MARKER" "$BK/$MARKER_BASENAME" || { echo "REFUSE marker backup verification failed"; exit 1; }
  MARKER_PREEXISTED=1
fi
if [ "$CPA_OAUTH_QUARANTINE_PROVIDER" = "claude" ]; then
  cp -a "$DIR/cpa_policy.py" "$BK/cpa_policy.py"
  cmp -s "$DIR/cpa_policy.py" "$BK/cpa_policy.py" || { echo "REFUSE policy backup verification failed"; exit 1; }
fi
chmod 700 "$BK"

trap rollback_on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [ "$CPA_OAUTH_QUARANTINE_PROVIDER" = "claude" ]; then
  if ! python3 - "$DIR/cpa_policy.py" <<'PY'
import base64
import hashlib
import os
import sys
import tempfile
from pathlib import Path

path = Path(sys.argv[1])
source = base64.b64decode("__CPA_CLAUDE_POLICY_B64__", validate=True)
expected = "__CPA_CLAUDE_POLICY_SHA256__"
actual = hashlib.sha256(source).hexdigest()
if len(expected) != 64 or actual != expected:
    raise SystemExit("REFUSE Claude policy source hash mismatch")
text = source.decode("utf-8")
compile(text, str(path), "exec")
if path.read_bytes() == source:
    print("CLAUDE_POLICY_PROJECTION=NO_CHANGE sha256=" + actual)
    raise SystemExit(0)
fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
temp = Path(name)
try:
    with os.fdopen(fd, "wb") as handle:
        handle.write(source)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temp, 0o644)
    os.replace(temp, path)
finally:
    if temp.exists():
        temp.unlink()
print("CLAUDE_POLICY_PROJECTION=UPDATED sha256=" + actual)
PY
  then
    echo "CLAUDE_POLICY_PROJECTION_FAILED"
    exit 1
  fi
  if ! python3 "$DIR/cpa_policy.py" "$CONFIG" >"$BK/cpa-quarantine-policy-upgrade.log" 2>&1; then
    echo "REFUSE projected Claude policy baseline"
    tail -n 20 "$BK/cpa-quarantine-policy-upgrade.log"
    rm -f "$BK/cpa-quarantine-policy-upgrade.log"
    exit 1
  fi
  rm -f "$BK/cpa-quarantine-policy-upgrade.log"
fi

if [ "$CPA_OAUTH_QUARANTINE_PROVIDER" = "claude" ]; then
  # Take the final lane samples after all slower preparation. This narrows the
  # live-traffic race but does not lock ingress; callers must pause dispatch.
  for sample in 1 2 3; do
    if ! claude_lane_idle_sample; then
      echo "REFUSE Claude OAuth lane is active or admission health is unavailable sample=$sample"
      exit 1
    fi
    [ "$sample" = 3 ] || sleep 1
  done
  echo "CLAUDE_OAUTH_IDLE_SAMPLES=3"
fi

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
provider = os.environ.get("CPA_OAUTH_QUARANTINE_PROVIDER", "codex")
route_name = {
    "codex": "chatgpt-plus-oauth",
    "antigravity": "antigravity-oauth",
    "claude": "claude-oauth",
}.get(provider)
if route_name is None:
    raise SystemExit("REFUSE unknown quarantine provider")
previous_field = {
    "codex": "previous_codex_exclusions",
    "antigravity": "previous_provider_exclusions",
    "claude": "previous_claude_exclusions",
}[provider]
applied_field = {
    "codex": "applied_codex_exclusions",
    "antigravity": "applied_provider_exclusions",
    "claude": "applied_claude_exclusions",
}[provider]
manifest = json.loads(
    b64decode("__CPA_OAUTH_QUARANTINE_MANIFEST_B64__").decode("utf-8")
)
# Quarantine owns one provider; sibling providers retain their own state.
oauth_aliases = [
    model["alias"]
    for route in manifest.get("oauth_routes", [])
    if isinstance(route, dict)
    and route.get("name") == route_name
    for model in route.get("models", [])
    if isinstance(model, dict) and isinstance(model.get("alias"), str)
]
if not oauth_aliases:
    raise SystemExit("REFUSE invalid route manifest")

config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
routing = config.get("routing") if isinstance(config, dict) else None
if not isinstance(routing, dict) or routing.get("force-model-prefix") is not True:
    raise SystemExit("REFUSE unexpected CPA routing policy")
oauth_block = config.get("oauth") if isinstance(config, dict) else None
exclusions = oauth_block.get("excluded-models") if isinstance(oauth_block, dict) else None
if exclusions is None:
    exclusions = {}
if not isinstance(exclusions, dict):
    raise SystemExit("REFUSE oauth.excluded-models must be a mapping")
codex = exclusions.get(provider, [])
if not isinstance(codex, list) or not all(
    isinstance(pattern, str) and pattern.strip() for pattern in codex
):
    raise SystemExit("REFUSE provider exclusions must be a list of strings")

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


def write_config_in_place(path, text, mode_bits):
    # CPA v8 watches config.yaml by inode. Preserve it so this quarantine and a
    # later restore both generate file-write events on the active watch.
    flags = os.O_WRONLY | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        os.fchmod(fd, mode_bits)
        pending = memoryview(text.encode("utf-8"))
        while pending:
            written = os.write(fd, pending)
            if written <= 0:
                raise OSError("short write while updating watched CPA config")
            pending = pending[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


if mode == "quarantine":
    if marker_path.exists():
        raise SystemExit("REFUSE OAuth lane is already quarantined")
    previous = list(codex)
    if provider == "claude":
        # Quarantine the complete subscription lane, including future model
        # aliases. This provider-level OAuth exclusion does not affect
        # separately configured Claude API-key routes.
        if not all(alias.lower().startswith("claude-") for alias in oauth_aliases):
            raise SystemExit("REFUSE Claude OAuth route aliases are unexpected")
        after = list(codex) + ([] if "claude-*" in present else ["claude-*"])
    else:
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
    if marker_before.get("provider", "codex") != provider:
        raise SystemExit("REFUSE quarantine marker provider mismatch")
    marker_aliases = marker_before.get("aliases")
    if not isinstance(marker_aliases, list) or {
        str(alias).strip().lower() for alias in marker_aliases
    } != targets:
        raise SystemExit("REFUSE quarantine marker aliases do not match route manifest")
    previous = marker_before.get(previous_field)
    applied = marker_before.get(applied_field)
    if not isinstance(previous, list) or not all(
        isinstance(pattern, str) and pattern.strip() for pattern in previous
    ):
        raise SystemExit("REFUSE quarantine marker has no valid previous exclusions")
    if not isinstance(applied, list) or codex != applied:
        raise SystemExit("REFUSE OAuth quarantine config drift detected")
    after = list(previous)

config_after = dict(config)
oauth_block_after = dict(config_after.get("oauth") or {})
exclusions_after = dict(exclusions)
exclusions_after[provider] = after
oauth_block_after["excluded-models"] = exclusions_after
config_after["oauth"] = oauth_block_after
candidate = yaml.safe_dump(
    config_after, allow_unicode=True, default_flow_style=False, sort_keys=False
)
if yaml.safe_load(candidate) != config_after:
    raise SystemExit("candidate YAML semantic round-trip failed")
if provider == "claude":
    write_config_in_place(config_path, candidate, 0o600)
else:
    atomic_write(config_path, candidate, 0o600)

if mode == "quarantine":
    marker = {
        "version": 1,
        "state": "quarantined",
        "provider": provider,
        "aliases": sorted(oauth_aliases),
        previous_field: previous,
        applied_field: after,
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

if [ "$CPA_OAUTH_QUARANTINE_PROVIDER" != "claude" ]; then
  if ! docker restart cli-proxy-api >/dev/null; then
    restore_all
    echo "ROLLBACK cpa_restart"
    exit 1
  fi
fi

READY=000
CLAUDE_CATALOG_READY=0
for _ in $(seq 1 30); do
  READY=$(curl --noproxy '*' -sS --max-time 5 -o "$BK/cpa-quarantine-catalog.json" -w '%{http_code}' \
    -H "Authorization: Bearer $KEY" http://127.0.0.1:8317/v1/models || true)
  if [ "$READY" = "200" ]; then
    if [ "$CPA_OAUTH_QUARANTINE_PROVIDER" != "claude" ]; then
      break
    fi
    expected_after=blocked
    [ "$MODE" = "quarantine" ] || expected_after=active
    if check_claude_catalog_state "$expected_after" "$BK/cpa-quarantine-catalog.json"; then
      CLAUDE_CATALOG_READY=1
      break
    fi
  fi
  sleep 1
done
if [ "$READY" != "200" ] || \
   { [ "$CPA_OAUTH_QUARANTINE_PROVIDER" = "claude" ] && [ "$CLAUDE_CATALOG_READY" -ne 1 ]; }; then
  rm -f "$BK/cpa-quarantine-catalog.json"
  restore_all
  if [ "$CPA_OAUTH_QUARANTINE_PROVIDER" = "claude" ]; then
    echo "ROLLBACK cpa_config_hot_reload status=$READY"
  else
    echo "ROLLBACK cpa_readiness status=$READY"
  fi
  exit 1
fi

if ! python3 - "$DIR" "$MODE" "$BK/cpa-quarantine-catalog.json" <<'PY'
import json
import os
import sys
from fnmatch import fnmatchcase
from pathlib import Path

import yaml

root = Path(sys.argv[1])
mode = sys.argv[2]
provider = os.environ.get("CPA_OAUTH_QUARANTINE_PROVIDER", "codex")
route_name = {
    "codex": "chatgpt-plus-oauth",
    "antigravity": "antigravity-oauth",
    "claude": "claude-oauth",
}[provider]
catalog = json.load(open(sys.argv[3], encoding="utf-8"))
ids = {
    item["id"]
    for item in catalog.get("data", [])
    if isinstance(item, dict) and isinstance(item.get("id"), str)
}
manifest = json.loads((root / "cpa_provider_routes.json").read_text(encoding="utf-8"))
# Quarantine/restore owns the selected route. Other OAuth providers keep serving, so
# their aliases are known survivors for the unknown-ID check and are out of
# scope for the survived/still_blocked verdicts.
oauth_aliases = sorted(
    {
        model["alias"]
        for route in manifest.get("oauth_routes", [])
        if isinstance(route, dict)
        and route.get("name") == route_name
        for model in route.get("models", [])
        if isinstance(model, dict) and isinstance(model.get("alias"), str)
    }
)
other_oauth_aliases = {
    model["alias"]
    for route in manifest.get("oauth_routes", [])
    if isinstance(route, dict)
    and route.get("name") != route_name
    for model in route.get("models", [])
    if isinstance(model, dict) and isinstance(model.get("alias"), str)
}
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
codex_aliases = {
    model["alias"]
    for route in manifest.get("codex_api_key_routes", [])
    for model in route.get("models", [])
}
claude_aliases = {
    model["alias"]
    for route in manifest.get("claude_api_key_routes", [])
    for model in route.get("models", [])
}
unknown = sorted(
    ids
    - provider_aliases
    - other_oauth_aliases
    - codex_aliases
    - claude_aliases
    - set(oauth_aliases)
)
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
    oauth_block = config.get("oauth") if isinstance(config, dict) else None
    exclusions = (
        oauth_block.get("excluded-models") if isinstance(oauth_block, dict) else None
    )
    codex = exclusions.get(provider, []) if isinstance(exclusions, dict) else []
    patterns = {
        pattern.strip().lower()
        for pattern in codex
        if isinstance(pattern, str) and pattern.strip()
    }
    still_blocked = sorted(
        alias
        for alias in oauth_aliases
        if any(fnmatchcase(alias.lower(), pattern) for pattern in patterns)
    )
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
  rm -f "$BK/cpa-quarantine-catalog.json"
  restore_all
  echo "ROLLBACK quarantine_verification"
  exit 1
fi
rm -f "$BK/cpa-quarantine-catalog.json"

if ! python3 "$DIR/cpa_policy.py" "$CONFIG" >"$BK/cpa-quarantine-policy.log" 2>&1; then
  restore_all
  echo "ROLLBACK quarantine_policy"
  tail -n 20 "$BK/cpa-quarantine-policy.log"
  exit 1
fi

trap - EXIT INT TERM
echo "BACKUP_DIR=$BK"
echo "QUARANTINE_MODE=$MODE"
echo "QUARANTINE_PROVIDER=$CPA_OAUTH_QUARANTINE_PROVIDER"
echo "OAUTH_CREDENTIAL_RETAINED=yes"
echo "QUOTA_STATE_RESET=no"
echo "READY_STATUS=$READY"
prune_backup_history
