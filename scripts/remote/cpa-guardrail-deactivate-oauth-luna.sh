set -euo pipefail

# Same lock as auto-update.sh: the daily timer and guardrail transactions
# refuse to overlap instead of interleaving backups, restarts, and rollbacks.
exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo "REFUSE cpa_busy vps-ssh-launcher-maintenance.lock held"; exit 1; }

DIR=/opt/cliproxyapi
CONFIG="$DIR/config.yaml"
AUTH_DIR="$DIR/auth"

# No backup of OAuth JSON is made: this operation intentionally removes all
# locally retained, refreshable OAuth material from the VPS.
# Bare gpt-6-luna (and gpt-6.1-sol) is served ONLY by the ChatGPT Plus OAuth
# auth file. ai.input.im does not serve Luna, so deleting OAuth material
# removes the Luna alias from the catalog. config.yaml is NOT edited here,
# so there is no config rollback; recovery is a fresh device login per
# docs/runbooks/cpa-oauth-luna-slot.md. The OAuth exclusion list pins the
# same-name GPT-6 Sol/Astra routes to ai.input.im. The post-removal catalog
# contract derives from the checked-in route manifest: a hardcoded name list
# stranded silently when the 2026-09-23 route projection stopped exposing
# gpt-5.6-sol as a bare client name.
if ! docker stop cli-proxy-api >/dev/null; then
  echo "REFUSE cpa_stop_failed; OAuth files retained"
  exit 1
fi

if ! python3 - "$CONFIG" "$AUTH_DIR" <<'PY'
import json
import sys
from pathlib import Path

import yaml

config_path, auth_dir = Path(sys.argv[1]), Path(sys.argv[2])
config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
if not isinstance(config, dict) or config.get("force-model-prefix") is not True:
    raise SystemExit("REFUSE unexpected CPA routing policy")
active = []
for path in sorted(auth_dir.glob("*.json")):
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
        active.append(path)
if len(active) < 1:
    raise SystemExit("REFUSE no active Codex OAuth auth to deactivate")
print("ACTIVE_OAUTH_FILES=%d" % len(active))
PY
then
  docker start cli-proxy-api >/dev/null 2>&1 || true
  echo "OAUTH_TOPOLOGY_CHECK_FAILED; OAuth files retained"
  exit 1
fi

if ! python3 - "$DIR" "$AUTH_DIR" <<'PY'
import json
import sys
from pathlib import Path

# The active auth dir MUST be scanned: it holds the live OAuth JSON that this
# transaction exists to remove; /root and backups only hold historical copies.
roots = [Path("/root"), Path(sys.argv[1]) / "backups", Path(sys.argv[2])]
removed = 0
for root in roots:
    if not root.exists():
        continue
    for path in root.rglob("*.json"):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        keys = {str(key).lower() for key in data}
        if {"access_token", "refresh_token"} & keys:
            if str(data.get("type", "")).lower() != "codex":
                raise SystemExit("REFUSE unexpected OAuth JSON type")
            path.unlink()
            removed += 1
if removed < 1:
    raise SystemExit("REFUSE no OAuth material removed")
print(f"OAUTH_MATERIAL_REMOVED count={removed}")
PY
then
  echo "OAUTH_REMOVAL_FAILED; CPA remains stopped"
  exit 1
fi

if ! docker start cli-proxy-api >/dev/null; then
  echo "CPA_START_FAILED after OAuth removal"
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
READY=000
for _ in $(seq 1 30); do
  READY=$(curl --noproxy '*' -sS --max-time 5 -o /tmp/cpa-oauth-retire-catalog.json -w '%{http_code}' \
    -H "Authorization: Bearer $KEY" http://127.0.0.1:8317/v1/models || true)
  if [ "$READY" = "200" ]; then
    break
  fi
  sleep 1
done
if [ "$READY" != "200" ] || ! python3 - /tmp/cpa-oauth-retire-catalog.json <<'PY'
import json
import sys
from base64 import b64decode

# Survival contract is derived from the checked-in route manifest (the same
# bytes the projector writes); never a literal copy, which drifts silently on
# every catalog re-projection. Semantics mirror cpa-health readiness: unknown
# IDs and surviving OAuth aliases fail closed, while a cooled-down channel may
# temporarily hide a required alias from the availability-filtered catalog --
# that signal belongs to the doctor gate, not this transaction.
manifest = json.loads(b64decode("__CPA_OAUTH_RETIRE_MANIFEST_B64__").decode("utf-8"))
providers = [p for p in manifest.get("providers", []) if isinstance(p, dict)]
provider_aliases = {
    model["alias"]
    for provider in providers
    for model in provider.get("models", [])
    if isinstance(model, dict) and isinstance(model.get("alias"), str)
}
oauth_aliases = {
    model["alias"]
    for route in manifest.get("oauth_routes", [])
    if isinstance(route, dict)
    for model in route.get("models", [])
    if isinstance(model, dict) and isinstance(model.get("alias"), str)
}
optional = {
    model
    for provider in providers
    for model in provider.get("optional_models", [])
    if isinstance(model, str)
}
if not provider_aliases or not oauth_aliases:
    raise SystemExit("REFUSE invalid route manifest")
catalog = json.load(open(sys.argv[1]))
ids = {
    item.get("id")
    for item in catalog.get("data", [])
    if isinstance(item, dict) and isinstance(item.get("id"), str)
}
survived = sorted(oauth_aliases & ids)
unknown = sorted(ids - provider_aliases)
if survived or unknown:
    raise SystemExit(
        "CPA_ROUTE_VERIFICATION_FAILED survived=%s unknown=%s"
        % (",".join(survived) or "none", ",".join(unknown) or "none")
    )
missing = sorted((provider_aliases - optional) - ids)
if missing:
    print("CATALOG_INCOMPLETE missing=%s" % ",".join(missing))
raise SystemExit(0)
PY
then
  rm -f /tmp/cpa-oauth-retire-catalog.json
  echo "CPA_ROUTE_VERIFICATION_FAILED"
  exit 1
fi
rm -f /tmp/cpa-oauth-retire-catalog.json

if ! python3 - "$DIR" "$AUTH_DIR" <<'PY'
import json
import sys
from pathlib import Path

remaining = 0
for root in (Path("/root"), Path(sys.argv[1]) / "backups", Path(sys.argv[2])):
    if not root.exists():
        continue
    for path in root.rglob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and {"access_token", "refresh_token"} & {str(key).lower() for key in data}:
            remaining += 1
raise SystemExit(0 if remaining == 0 else 1)
PY
then
  echo "OAUTH_REMOVAL_VERIFICATION_FAILED"
  exit 1
fi

echo "OAUTH_SLOT_RETAINED=metadata_only"
echo "BARE_LUNA_ROUTES=oauth_removed"
echo "OAUTH_REMOVAL_VERIFIED=yes"
echo "HEALTH_NOTE=generation_defers_exit10_until_reenroll"
