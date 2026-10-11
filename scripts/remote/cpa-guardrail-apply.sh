set -Eeuo pipefail

# Same lock as auto-update.sh: the daily timer and guardrail transactions
# refuse to overlap instead of interleaving backups, restarts, and rollbacks.
exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo "REFUSE cpa_busy vps-ssh-launcher-maintenance.lock held"; exit 1; }

DIR=/opt/cliproxyapi
ADMISSION_INTEGRITY_CHECK=/usr/local/libexec/cpa-admission-integrity-check
ADMISSION_INTEGRITY_DROPIN=/etc/systemd/system/cpa-admission.service.d/10-integrity.conf
ADMISSION_INTEGRITY_PIN=/etc/vps-ssh-launcher/cpa-admission.sha256
EXPECTED_ADMISSION_SHA256="__CPA_ADMISSION_SHA256__"
# -Apply recomputes the OAuth exclusion list from the route manifest, which
# would silently re-expose a quarantined lane. Refuse instead of undoing an
# explicit risk-control decision; the operator restores the lane first.
if [ -f /opt/cliproxyapi/oauth-quarantine.json ]; then
  echo "REFUSE OAuth lane quarantine is active; run -RestoreOAuthLuna before -Apply"
  exit 1
fi
# The deployed pin names the admission generation this host already runs. A
# full apply only re-projects that same generation: a pinned hash that is not
# the code this run would write means either a stale source tree about to roll
# the host back, or a new generation whose pin must rotate in one reviewed
# transaction (outputs/deploy-admission-*.ps1). Refuse instead of deciding
# that inside a bulk re-projection.
if [ ! -f "$ADMISSION_INTEGRITY_PIN" ]; then
  echo "REFUSE admission_integrity_pin_missing integrity guard not installed; use the reviewed admission deploy transaction"
  exit 1
fi
CURRENT_ADMISSION_PIN=$(awk 'NF {print $1; exit}' "$ADMISSION_INTEGRITY_PIN")
if [ "$CURRENT_ADMISSION_PIN" != "$EXPECTED_ADMISSION_SHA256" ]; then
  echo "REFUSE admission_integrity_pin_mismatch want=$EXPECTED_ADMISSION_SHA256 got=$CURRENT_ADMISSION_PIN; rotate admission code with a reviewed deploy transaction, not with -Apply"
  exit 1
fi
NGINX_CONF=/etc/nginx/conf.d/cpa-gateway.conf
FAIL2BAN_FILTER=/etc/fail2ban/filter.d/cpa-gateway.conf
FAIL2BAN_JAIL=/etc/fail2ban/jail.d/cpa-gateway.conf
ADMISSION_SCRIPT="$DIR/cpa-admission.py"
ADMISSION_CONFIG="$DIR/cpa-admission.json"
ADMISSION_UNIT=/etc/systemd/system/cpa-admission.service
LEGACY_ADMISSION_SCRIPT="$DIR/cpa-luna-admission.py"
LEGACY_ADMISSION_CONFIG="$DIR/cpa-luna-admission.json"
LEGACY_ADMISSION_UNIT=/etc/systemd/system/cpa-luna-admission.service
BK=/root/cpa-guardrails-backup-$(date -u +%Y%m%dT%H%M%S.%NZ)

prune_backup_history() {
  local keep=8 entry removed=0
  while IFS= read -r entry; do
    [ -n "$entry" ] || continue
    if rm -rf -- "$entry"; then
      removed=$((removed + 1))
    else
      echo "PRUNE_FAILED scope=cpa_guardrails_backups path=$entry"
    fi
  done < <(
    find /root -mindepth 1 -maxdepth 1 -type d \
      -name 'cpa-guardrails-backup-*' -printf '%T@ %p\n' 2>/dev/null |
      sort -rn | tail -n +$((keep + 1)) | cut -d' ' -f2-
  )
  echo "PRUNE scope=cpa_guardrails_backups removed=$removed policy=keep_$keep"
}
ADMISSION_WAS_ENABLED=0
if systemctl is-enabled --quiet cpa-admission.service 2>/dev/null; then
  ADMISSION_WAS_ENABLED=1
fi
ADMISSION_WAS_ACTIVE=0
if systemctl is-active --quiet cpa-admission.service 2>/dev/null; then
  ADMISSION_WAS_ACTIVE=1
fi
LEGACY_ADMISSION_WAS_ENABLED=0
if systemctl is-enabled --quiet cpa-luna-admission.service 2>/dev/null; then
  LEGACY_ADMISSION_WAS_ENABLED=1
fi
LEGACY_ADMISSION_WAS_ACTIVE=0
if systemctl is-active --quiet cpa-luna-admission.service 2>/dev/null; then
  LEGACY_ADMISSION_WAS_ACTIVE=1
fi
NGINX_ADMISSION_ROUTE_BEFORE=0
if grep -Fq 'proxy_pass http://127.0.0.1:8318/v1/$1$is_args$args;' "$NGINX_CONF"; then
  NGINX_ADMISSION_ROUTE_BEFORE=1
fi

if ! mkdir -m 700 "$BK"; then
  echo "REFUSE backup_exists_or_create_failed path=$BK"
  exit 1
fi
cp -a "$DIR/config.yaml" "$BK/config.yaml"
cp -a "$DIR/compose.yml" "$BK/compose.yml"
cp -a "$DIR/auto-update.sh" "$BK/auto-update.sh"
if [ -f "$DIR/cpa_provider_routes.json" ]; then
  cp -a "$DIR/cpa_provider_routes.json" "$BK/cpa_provider_routes.json"
else
  : > "$BK/cpa_provider_routes.json.missing"
fi
if [ -f "$DIR/cpa-health.py" ]; then
  cp -a "$DIR/cpa-health.py" "$BK/cpa-health.py"
else
  : > "$BK/cpa-health.py.missing"
fi
if [ -f "$DIR/cpa_policy.py" ]; then
  cp -a "$DIR/cpa_policy.py" "$BK/cpa_policy.py"
else
  : > "$BK/cpa_policy.py.missing"
fi
if [ -f "$ADMISSION_SCRIPT" ]; then
  cp -a "$ADMISSION_SCRIPT" "$BK/cpa-admission.py"
else
  : > "$BK/cpa-admission.py.missing"
fi
if [ -f "$ADMISSION_CONFIG" ]; then
  cp -a "$ADMISSION_CONFIG" "$BK/cpa-admission.json"
else
  : > "$BK/cpa-admission.json.missing"
fi
if [ -f "$ADMISSION_UNIT" ]; then
  cp -a "$ADMISSION_UNIT" "$BK/cpa-admission.service"
else
  : > "$BK/cpa-admission.service.missing"
fi
if [ -f "$ADMISSION_INTEGRITY_CHECK" ]; then
  cp -a "$ADMISSION_INTEGRITY_CHECK" "$BK/cpa-admission-integrity-check"
else
  : > "$BK/cpa-admission-integrity-check.missing"
fi
if [ -f "$ADMISSION_INTEGRITY_DROPIN" ]; then
  cp -a "$ADMISSION_INTEGRITY_DROPIN" "$BK/cpa-admission-integrity.conf"
else
  : > "$BK/cpa-admission-integrity.conf.missing"
fi
if [ -f "$ADMISSION_INTEGRITY_PIN" ]; then
  cp -a "$ADMISSION_INTEGRITY_PIN" "$BK/cpa-admission-integrity-pin.txt"
else
  : > "$BK/cpa-admission-integrity-pin.txt.missing"
fi
if [ -f "$LEGACY_ADMISSION_SCRIPT" ]; then
  cp -a "$LEGACY_ADMISSION_SCRIPT" "$BK/cpa-luna-admission.py"
else
  : > "$BK/cpa-luna-admission.py.missing"
fi
if [ -f "$LEGACY_ADMISSION_CONFIG" ]; then
  cp -a "$LEGACY_ADMISSION_CONFIG" "$BK/cpa-luna-admission.json"
else
  : > "$BK/cpa-luna-admission.json.missing"
fi
if [ -f "$LEGACY_ADMISSION_UNIT" ]; then
  cp -a "$LEGACY_ADMISSION_UNIT" "$BK/cpa-luna-admission.service"
else
  : > "$BK/cpa-luna-admission.service.missing"
fi
cp -a "$NGINX_CONF" "$BK/cpa-gateway.conf"
if [ -f "$FAIL2BAN_FILTER" ]; then cp -a "$FAIL2BAN_FILTER" "$BK/cpa-gateway-filter.conf"; fi
if [ -f "$FAIL2BAN_JAIL" ]; then cp -a "$FAIL2BAN_JAIL" "$BK/cpa-gateway-jail.conf"; fi
chmod 700 "$BK"

ROLLBACK_DONE=0
restore_all() {
  if [ "$ROLLBACK_DONE" -eq 1 ]; then return 0; fi
  ROLLBACK_DONE=1
  trap - EXIT INT TERM
  set +e
  rollback_failed=0
  # Both generations bind 8318. Stop the current process before restoring the
  # next file set, then recreate exactly the pre-transaction enable/active state.
  systemctl stop cpa-admission.service >/dev/null 2>&1 || true
  systemctl stop cpa-luna-admission.service >/dev/null 2>&1 || true
  cp -a "$BK/config.yaml" "$DIR/config.yaml" || rollback_failed=1
  cp -a "$BK/compose.yml" "$DIR/compose.yml" || rollback_failed=1
  cp -a "$BK/auto-update.sh" "$DIR/auto-update.sh" || rollback_failed=1
  if [ -f "$BK/cpa_provider_routes.json" ]; then
    cp -a "$BK/cpa_provider_routes.json" "$DIR/cpa_provider_routes.json" || rollback_failed=1
  else
    rm -f "$DIR/cpa_provider_routes.json" || rollback_failed=1
  fi
  if [ -f "$BK/cpa-health.py" ]; then
    cp -a "$BK/cpa-health.py" "$DIR/cpa-health.py" || rollback_failed=1
  else
    rm -f "$DIR/cpa-health.py" || rollback_failed=1
  fi
  if [ -f "$BK/cpa_policy.py" ]; then
    cp -a "$BK/cpa_policy.py" "$DIR/cpa_policy.py" || rollback_failed=1
  else
    rm -f "$DIR/cpa_policy.py" || rollback_failed=1
  fi
  if [ -f "$BK/cpa-admission.py" ]; then
    cp -a "$BK/cpa-admission.py" "$ADMISSION_SCRIPT" || rollback_failed=1
  else
    rm -f "$ADMISSION_SCRIPT" || rollback_failed=1
  fi
  if [ -f "$BK/cpa-admission.json" ]; then
    cp -a "$BK/cpa-admission.json" "$ADMISSION_CONFIG" || rollback_failed=1
  else
    rm -f "$ADMISSION_CONFIG" || rollback_failed=1
  fi
  if [ -f "$BK/cpa-admission.service" ]; then
    cp -a "$BK/cpa-admission.service" "$ADMISSION_UNIT" || rollback_failed=1
  else
    rm -f "$ADMISSION_UNIT" || rollback_failed=1
  fi
  if [ -f "$BK/cpa-admission-integrity-check" ]; then
    cp -a "$BK/cpa-admission-integrity-check" "$ADMISSION_INTEGRITY_CHECK" || rollback_failed=1
  else
    rm -f "$ADMISSION_INTEGRITY_CHECK" || rollback_failed=1
  fi
  if [ -f "$BK/cpa-admission-integrity.conf" ]; then
    mkdir -p "$(dirname "$ADMISSION_INTEGRITY_DROPIN")" || rollback_failed=1
    cp -a "$BK/cpa-admission-integrity.conf" "$ADMISSION_INTEGRITY_DROPIN" || rollback_failed=1
  else
    rm -f "$ADMISSION_INTEGRITY_DROPIN" || rollback_failed=1
  fi
  if [ -f "$BK/cpa-admission-integrity-pin.txt" ]; then
    mkdir -p "$(dirname "$ADMISSION_INTEGRITY_PIN")" || rollback_failed=1
    cp -a "$BK/cpa-admission-integrity-pin.txt" "$ADMISSION_INTEGRITY_PIN" || rollback_failed=1
  else
    rm -f "$ADMISSION_INTEGRITY_PIN" || rollback_failed=1
  fi
  if [ -f "$BK/cpa-luna-admission.py" ]; then
    cp -a "$BK/cpa-luna-admission.py" "$LEGACY_ADMISSION_SCRIPT" || rollback_failed=1
  else
    rm -f "$LEGACY_ADMISSION_SCRIPT" || rollback_failed=1
  fi
  if [ -f "$BK/cpa-luna-admission.json" ]; then
    cp -a "$BK/cpa-luna-admission.json" "$LEGACY_ADMISSION_CONFIG" || rollback_failed=1
  else
    rm -f "$LEGACY_ADMISSION_CONFIG" || rollback_failed=1
  fi
  if [ -f "$BK/cpa-luna-admission.service" ]; then
    cp -a "$BK/cpa-luna-admission.service" "$LEGACY_ADMISSION_UNIT" || rollback_failed=1
  else
    rm -f "$LEGACY_ADMISSION_UNIT" || rollback_failed=1
  fi
  cp -a "$BK/cpa-gateway.conf" "$NGINX_CONF" || rollback_failed=1
  if [ -f "$BK/cpa-gateway-filter.conf" ]; then
    cp -a "$BK/cpa-gateway-filter.conf" "$FAIL2BAN_FILTER" || rollback_failed=1
  else
    rm -f "$FAIL2BAN_FILTER" || rollback_failed=1
  fi
  if [ -f "$BK/cpa-gateway-jail.conf" ]; then
    cp -a "$BK/cpa-gateway-jail.conf" "$FAIL2BAN_JAIL" || rollback_failed=1
  else
    rm -f "$FAIL2BAN_JAIL" || rollback_failed=1
  fi
  chmod 600 "$DIR/config.yaml" || rollback_failed=1
  chmod 700 "$DIR/auto-update.sh" || rollback_failed=1
  if [ -f "$ADMISSION_SCRIPT" ]; then chmod 755 "$ADMISSION_SCRIPT" || rollback_failed=1; fi
  if [ -f "$ADMISSION_CONFIG" ]; then chmod 644 "$ADMISSION_CONFIG" || rollback_failed=1; fi
  if [ -f "$ADMISSION_UNIT" ]; then chmod 644 "$ADMISSION_UNIT" || rollback_failed=1; fi
  if [ -f "$ADMISSION_INTEGRITY_CHECK" ]; then chmod 755 "$ADMISSION_INTEGRITY_CHECK" || rollback_failed=1; fi
  if [ -f "$ADMISSION_INTEGRITY_DROPIN" ]; then chmod 644 "$ADMISSION_INTEGRITY_DROPIN" || rollback_failed=1; fi
  if [ -f "$ADMISSION_INTEGRITY_PIN" ]; then chmod 644 "$ADMISSION_INTEGRITY_PIN" || rollback_failed=1; fi
  if [ -f "$LEGACY_ADMISSION_SCRIPT" ]; then chmod 755 "$LEGACY_ADMISSION_SCRIPT" || rollback_failed=1; fi
  if [ -f "$LEGACY_ADMISSION_CONFIG" ]; then chmod 644 "$LEGACY_ADMISSION_CONFIG" || rollback_failed=1; fi
  if [ -f "$LEGACY_ADMISSION_UNIT" ]; then chmod 644 "$LEGACY_ADMISSION_UNIT" || rollback_failed=1; fi
  systemctl daemon-reload >/dev/null 2>&1 || rollback_failed=1
  docker compose -f "$DIR/compose.yml" config --quiet || rollback_failed=1
  docker restart cli-proxy-api >/dev/null 2>&1 || rollback_failed=1
  if [ "$ADMISSION_WAS_ENABLED" -eq 1 ]; then
    systemctl enable cpa-admission.service >/dev/null 2>&1 || rollback_failed=1
  else
    systemctl disable cpa-admission.service >/dev/null 2>&1 || true
  fi
  if [ "$ADMISSION_WAS_ACTIVE" -eq 1 ]; then
    systemctl start cpa-admission.service >/dev/null 2>&1 || rollback_failed=1
  else
    systemctl stop cpa-admission.service >/dev/null 2>&1 || true
  fi
  if [ "$LEGACY_ADMISSION_WAS_ENABLED" -eq 1 ]; then
    systemctl enable cpa-luna-admission.service >/dev/null 2>&1 || rollback_failed=1
  else
    systemctl disable cpa-luna-admission.service >/dev/null 2>&1 || true
  fi
  if [ "$LEGACY_ADMISSION_WAS_ACTIVE" -eq 1 ]; then
    systemctl start cpa-luna-admission.service >/dev/null 2>&1 || rollback_failed=1
  else
    systemctl stop cpa-luna-admission.service >/dev/null 2>&1 || true
  fi
  if nginx -t >/dev/null 2>&1; then
    systemctl reload nginx >/dev/null 2>&1 || rollback_failed=1
  else
    rollback_failed=1
  fi
  fail2ban-client -t >/dev/null 2>&1 || rollback_failed=1
  fail2ban-client reload --restart cpa-gateway >/dev/null 2>&1 || rollback_failed=1
  if [ -f "$DIR/cpa-health.py" ]; then
    python3 "$DIR/cpa-health.py" readiness >/dev/null 2>&1 || rollback_failed=1
  else
    rollback_failed=1
  fi
  if [ -f "$DIR/cpa_policy.py" ]; then
    python3 "$DIR/cpa_policy.py" "$DIR/config.yaml" >/dev/null 2>&1 || rollback_failed=1
  else
    rollback_failed=1
  fi
  assert_merged_nginx_route_contract || rollback_failed=1
  if [ "$NGINX_ADMISSION_ROUTE_BEFORE" -eq 1 ]; then
    assert_admission_nginx_route_contract || rollback_failed=1
  elif assert_admission_nginx_route_contract; then
    rollback_failed=1
  fi
  assert_public_route_contract || rollback_failed=1
  ss -ltn | grep -Eq '127\.0\.0\.1:8317[[:space:]]' || rollback_failed=1
  if [ "$ADMISSION_WAS_ACTIVE" -eq 1 ] || [ "$LEGACY_ADMISSION_WAS_ACTIVE" -eq 1 ]; then
    ss -ltn | grep -Eq '127\.0\.0\.1:8318[[:space:]]' || rollback_failed=1
  fi
  if ss -ltn | grep -Eq '\[::\]:8317[[:space:]]|:::8317[[:space:]]'; then
    rollback_failed=1
  fi
  if { [ "$ADMISSION_WAS_ACTIVE" -eq 1 ] || [ "$LEGACY_ADMISSION_WAS_ACTIVE" -eq 1 ]; } &&
     ss -ltn | grep -Eq '\[::\]:8318[[:space:]]|:::8318[[:space:]]'; then
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

migrate_legacy_admission() {
  if systemctl is-active --quiet cpa-luna-admission.service 2>/dev/null; then
    systemctl stop cpa-luna-admission.service >/dev/null 2>&1 || return 1
  fi
  if systemctl is-enabled --quiet cpa-luna-admission.service 2>/dev/null; then
    systemctl disable cpa-luna-admission.service >/dev/null 2>&1 || return 1
  fi
  rm -f "$LEGACY_ADMISSION_UNIT" "$LEGACY_ADMISSION_SCRIPT" "$LEGACY_ADMISSION_CONFIG" || return 1
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

if ! grep -Eq '^[[:space:]]*listen[[:space:]]+8443[[:space:]]+ssl;' "$NGINX_CONF"; then
  echo "REFUSE unexpected Nginx public listener"
  exit 1
fi
RANDOM_PATH_BEFORE=$(grep -oE '/[0-9a-f]{16}/v1/' "$NGINX_CONF" | head -n 1 | cut -d/ -f2)
if [ -z "$RANDOM_PATH_BEFORE" ]; then
  echo "REFUSE missing Nginx random capability path"
  exit 1
fi
if ! grep -Eq 'location[[:space:]]+~[[:space:]]+\^/[0-9a-f]{16}/v1/' "$NGINX_CONF"; then
  echo "REFUSE unexpected Nginx route shape"
  exit 1
fi
if ! test -f /etc/logrotate.d/nginx || ! grep -Fq '/var/log/nginx/*.log' /etc/logrotate.d/nginx; then
  echo "REFUSE existing Nginx logrotate does not cover gateway logs"
  exit 1
fi
if test -e /etc/logrotate.d/cpa-gateway; then
  echo "REFUSE duplicate cpa-gateway logrotate file exists"
  exit 1
fi
MGMT_APPLY_ALLOW=$(grep -E '^[[:space:]]*allow-remote:' "$DIR/config.yaml" | head -1 | sed 's/.*:[[:space:]]*//')
MGMT_APPLY_KEY=$(grep -A2 '^remote-management:' "$DIR/config.yaml" | grep 'secret-key:' | sed 's/.*secret-key:[[:space:]]*//;s/"//g')
if [ "$MGMT_APPLY_ALLOW" = "true" ] && [ "${#MGMT_APPLY_KEY}" -lt 32 ]; then
  echo "REFUSE remote management enabled without a strong (>=32 char) key"
  exit 1
fi
if python3 - "$DIR/config.yaml" "$DIR/auth" <<'PY'
import json
import sys
from pathlib import Path
import yaml

def contains_enabled(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).replace('_', '-') == 'identity-confuse' and child is True:
                return True
            if contains_enabled(child):
                return True
    elif isinstance(value, list):
        return any(contains_enabled(child) for child in value)
    return False

config = yaml.safe_load(Path(sys.argv[1]).read_text(encoding='utf-8'))
if contains_enabled(config):
    raise SystemExit(1)
for path in Path(sys.argv[2]).glob('*.json'):
    if contains_enabled(json.loads(path.read_text(encoding='utf-8'))):
        raise SystemExit(1)
PY
then
  :
else
  echo "REFUSE identity-confuse is enabled"
  exit 1
fi
if ! python3 - "$DIR/auth" <<'PY'
import stat
import sys
from pathlib import Path

auth = Path(sys.argv[1])
if not auth.is_dir() or stat.S_IMODE(auth.stat().st_mode) != 0o700:
    raise SystemExit(1)
for path in auth.iterdir():
    if path.is_file() and path.suffix in {'.json', '.cds'}:
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise SystemExit(1)
PY
then
  echo "REFUSE auth permissions"
  exit 1
fi
PORT_JSON=$(docker inspect --format '{{json .HostConfig.PortBindings}}' cli-proxy-api 2>/dev/null || true)
if [ -z "$PORT_JSON" ] || ! python3 - "$PORT_JSON" <<'PY'
import json
import sys

try:
    bindings = json.loads(sys.argv[1])
except (IndexError, json.JSONDecodeError):
    raise SystemExit(1)
expected = {"8317/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8317"}]}
if bindings != expected:
    raise SystemExit(1)
PY
then
  echo "REFUSE CPA host port is not loopback-only"
  exit 1
fi

assert_merged_nginx_route_contract() {
  dump_file=$(mktemp)
  if ! nginx -T >"$dump_file" 2>&1; then
    rm -f "$dump_file"
    return 1
  fi
  # Lane-aware counts: the 2026-09-30 443-fallback lane mirrors the canonical
  # shapes once, signalled by its private log_format marker.
  lane_count=$(grep -Ec 'log_format[[:space:]]+cpa_safe443' "$dump_file")
  expected_routes=$((1 + lane_count))
  listen_count=$(grep -Ec '^[[:space:]]*listen[[:space:]]+8443[[:space:]]+ssl;' "$dump_file")
  ipv6_listen_count=$(grep -Ec '^[[:space:]]*listen[[:space:]]+\[::\]:8443[[:space:]]+ssl;' "$dump_file")
  route_count=$(grep -Ec 'location[[:space:]]+~[[:space:]]+\^/[0-9a-f]{16}/v1/\(\.\*\)\$' "$dump_file")
  proxy_count=$(grep -Ec 'proxy_pass[[:space:]]+http://127\.0\.0\.1:831[78]/v1/\$1\$is_args\$args;' "$dump_file")
  auth_proxy_count=$(grep -Ec 'proxy_pass[[:space:]]+http://127\.0\.0\.1:8317/v1/models\$is_args\$args;' "$dump_file")
  fallback_count=$(grep -Ec 'location[[:space:]]*/[[:space:]]*\{|return[[:space:]]+404;' "$dump_file")
  merged_path=$(grep -oE '/[0-9a-f]{16}/v1/' "$dump_file" | head -n 1 | cut -d/ -f2)
  rm -f "$dump_file"
  if [ "$listen_count" -eq 1 ] &&
     [ "$ipv6_listen_count" -eq 0 ] &&
     [ "$route_count" -eq "$expected_routes" ] &&
     [ "$proxy_count" -eq "$expected_routes" ] &&
     [ "$auth_proxy_count" -eq "$expected_routes" ] &&
     [ "$fallback_count" -ge 2 ] &&
     [ "$merged_path" = "$RANDOM_PATH_BEFORE" ]; then
    return 0
  fi
  echo "NGINX_CONTRACT_COUNTS listen=$listen_count ipv6=$ipv6_listen_count lane=$lane_count route=$route_count expected=$expected_routes proxy=$proxy_count auth_proxy=$auth_proxy_count fallback=$fallback_count path_match=$([ "$merged_path" = "$RANDOM_PATH_BEFORE" ] && echo yes || echo no)"
  return 1
}

assert_admission_nginx_route_contract() {
  dump_file=$(mktemp)
  if ! nginx -T >"$dump_file" 2>&1; then
    rm -f "$dump_file"
    return 1
  fi
  lane_count=$(grep -Ec 'log_format[[:space:]]+cpa_safe443' "$dump_file")
  expected_routes=$((1 + lane_count))
  admission_proxy_count=$(grep -Ec 'proxy_pass[[:space:]]+http://127\.0\.0\.1:8318/v1/\$1\$is_args\$args;' "$dump_file")
  auth_proxy_count=$(grep -Ec 'proxy_pass[[:space:]]+http://127\.0\.0\.1:8317/v1/models\$is_args\$args;' "$dump_file")
  rm -f "$dump_file"
  [ "$admission_proxy_count" -eq "$expected_routes" ] && [ "$auth_proxy_count" -eq "$expected_routes" ]
}

if ! assert_merged_nginx_route_contract; then
  echo "REFUSE unexpected merged Nginx route contract"
  exit 1
fi

assert_public_route_contract() {
  server_name=$(awk '/^[[:space:]]*server_name[[:space:]]/{gsub(";", "", $2); print $2; exit}' "$NGINX_CONF")
  prefix=$(grep -oE '/[0-9a-f]{16}/v1/' "$NGINX_CONF" | head -n 1 | cut -d/ -f2)
  if [ -z "$server_name" ] || [ -z "$prefix" ]; then
    return 1
  fi
  public_base="https://$server_name:8443"
  valid_status=$(curl --noproxy '*' -sS --connect-timeout 5 --max-time 10 \
    --resolve "$server_name:8443:127.0.0.1" -o /dev/null -w '%{http_code}' \
    "$public_base/$prefix/v1/models" 2>/dev/null || echo 000)
  authenticated_status=$(curl --noproxy '*' -sS --connect-timeout 5 --max-time 10 \
    --resolve "$server_name:8443:127.0.0.1" -H "Authorization: Bearer $KEY" \
    -o /dev/null -w '%{http_code}' "$public_base/$prefix/v1/models" 2>/dev/null || echo 000)
  bare_status=$(curl --noproxy '*' -sS --connect-timeout 5 --max-time 10 \
    --resolve "$server_name:8443:127.0.0.1" -o /dev/null -w '%{http_code}' \
    "$public_base/v1/models" 2>/dev/null || echo 000)
  wrong_prefix=0000000000000000
  if [ "$wrong_prefix" = "$prefix" ]; then wrong_prefix=ffffffffffffffff; fi
  wrong_status=$(curl --noproxy '*' -sS --connect-timeout 5 --max-time 10 \
    --resolve "$server_name:8443:127.0.0.1" -o /dev/null -w '%{http_code}' \
    "$public_base/$wrong_prefix/v1/models" 2>/dev/null || echo 000)
  [ "$valid_status" = "401" ] &&
    [ "$authenticated_status" = "200" ] &&
    [ "$bare_status" = "404" ] &&
    [ "$wrong_status" = "404" ] &&
    ss -ltn | grep -Eq '0\.0\.0\.0:8443[[:space:]]' &&
    ! ss -ltn | grep -Eq '\[::\]:8443[[:space:]]|:::8443[[:space:]]'
}

KEY=$(python3 - "$DIR/config.yaml" <<'PY'
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
  echo "REFUSE missing_client_key"
  exit 1
fi
# Arm before the first mutation. EXIT also covers unguarded set -e failures;
# signals exit nonzero and follow the same rollback path.
trap rollback_on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if ! python3 - <<'PY'
from base64 import b64decode
from copy import deepcopy
import json
from pathlib import Path
import os
import tempfile
import yaml
from fnmatch import fnmatchcase
from urllib.parse import urlparse


def atomic_write(path: Path, text: str, mode: int) -> None:
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


config_path = Path("/opt/cliproxyapi/config.yaml")
config_before = yaml.safe_load(config_path.read_text(encoding="utf-8"))
if not isinstance(config_before, dict):
    raise SystemExit("config.yaml is not a mapping")

if config_before.get("request-retry") not in (0, 1):
    raise SystemExit("unexpected request-retry value")
if config_before.get("max-retry-credentials") != 1:
    raise SystemExit("unexpected max-retry-credentials value")
if config_before.get("force-model-prefix") is not True:
    raise SystemExit("force-model-prefix must remain true")
if config_before.get("save-cooldown-status") is not False:
    # false since 2026-09-16: persisted cooldowns (.cds) turned transient upstream
    # capacity cooldowns into stuck catalog absences (upstream #5639/#5770); see
    # docs/runbooks/cpa-stale-cooldown-recovery.md.
    raise SystemExit("save-cooldown-status must remain false")
if not isinstance(config_before.get("routing"), dict):
    raise SystemExit("routing must be a mapping")
if not isinstance(config_before.get("codex"), dict):
    raise SystemExit("codex must be a mapping")
compatibility = config_before.get("openai-compatibility")
if not isinstance(compatibility, list):
    raise SystemExit("openai-compatibility must be a list")


def parse_env(text):
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\\\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


env_values = parse_env(b64decode("__CPA_PROVIDER_ENV_B64__").decode("utf-8-sig"))
route_manifest = json.loads(b64decode("__CPA_PROVIDER_ROUTES_B64__").decode("utf-8"))
provider_slots = route_manifest.get("providers")
if not isinstance(provider_slots, list) or not provider_slots:
    raise SystemExit("REFUSE provider route manifest is empty")
forbidden_provider_keys = {
    "proxy",
    "proxy-url",
    "http-proxy",
    "https-proxy",
    "socks-proxy",
    "headers",
    "header",
    "custom-headers",
    "transport",
    "tls",
    "insecure-skip-verify",
    "skip-tls-verify",
}


def parse_required_slot(route):
    slot = int(route["slot"])
    expected_host = str(route["host"])
    default_path = str(route["path"])
    scheme = str(route.get("scheme", "https"))
    expected_port = route.get("port")
    allow_insecure_http = route.get("allow_insecure_http", False)
    if scheme == "http" and not (
        slot == 3
        and expected_host == "35.213.82.91"
        and expected_port == 8003
        and allow_insecure_http is True
    ):
        raise SystemExit("REFUSE HTTP is allowed only for explicitly authorized slot 3")
    if scheme not in ("https", "http") or (
        scheme == "https" and (expected_port is not None or allow_insecure_http is not False)
    ):
        raise SystemExit(f"REFUSE provider route slot {slot} has invalid transport policy")
    base_url = env_values.get(f"BASE_URL_{slot}", "")
    api_key = env_values.get(f"API_KEY_{slot}", "")
    try:
        parsed = urlparse(base_url)
        if (
            parsed.scheme != scheme
            or parsed.hostname != expected_host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port != expected_port
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError
        path = parsed.path.rstrip("/")
    except (TypeError, ValueError):
        raise SystemExit(
            f"REFUSE env BASE_URL_{slot} must match the approved slot {slot} route"
        )
    if not api_key:
        raise SystemExit(f"REFUSE env API_KEY_{slot} is empty")
    expected_path = default_path.rstrip("/")
    if path != expected_path:
        raise SystemExit(
            f"REFUSE env BASE_URL_{slot} must match the approved slot {slot} route"
        )
    authority = f"{expected_host}:{expected_port}" if expected_port is not None else expected_host
    return f"{scheme}://{authority}{path}", api_key


def provider_host(entry):
    if not isinstance(entry, dict):
        return None
    return urlparse(str(entry.get("base-url", ""))).hostname


def model_entry(name):
    if not isinstance(name, dict) or set(name) != {"name", "alias"}:
        raise SystemExit("REFUSE provider route model entry is invalid")
    return {"name": name["name"], "alias": name["alias"]}


def build_provider(existing, name, base_url, api_key, models):
    provider = deepcopy(existing) if isinstance(existing, dict) else {}
    if "api-key" in provider:
        raise SystemExit(f"REFUSE {name} uses legacy api-key field")
    for raw_key in provider:
        if str(raw_key).strip().replace("_", "-") in forbidden_provider_keys:
            raise SystemExit(f"REFUSE {name} has forbidden transport override")
    entries = provider.get("api-key-entries")
    if entries is not None and (
        not isinstance(entries, list)
        or len(entries) != 1
        or not isinstance(entries[0], dict)
        or set(entries[0]) != {"api-key"}
    ):
        raise SystemExit(f"REFUSE {name} must have exactly one plain api-key entry")
    provider["name"] = name
    provider["base-url"] = base_url
    provider.pop("prefix", None)
    provider.pop("disabled", None)
    provider.pop("api-key", None)
    provider["api-key-entries"] = [{"api-key": api_key}]
    provider["models"] = [model_entry(model) for model in models]
    provider["request-retry"] = 0
    provider["disable-cooling"] = False
    provider["support-prompt-cache-key"] = False
    return provider


target_hosts = {route["host"] for route in provider_slots}
legacy_hosts = set(route_manifest.get("retired_hosts", []))
target_providers = []
remaining_compatibility = []
for item in compatibility:
    host = provider_host(item)
    name = item.get("name") if isinstance(item, dict) else None
    if host in target_hosts or host in legacy_hosts or name == "relay-8003":
        continue
    remaining_compatibility.append(item)

for route in provider_slots:
    slot = int(route["slot"])
    host = str(route["host"])
    name = str(route["name"])
    models = route["models"]
    default_path = str(route["path"])
    base_url, api_key = parse_required_slot(route)
    if str(route.get("scheme", "https")) == "http":
        print(
            f"INSECURE_HTTP_PROVIDER slot={slot} host={host} "
            "api_key_transport=cleartext"
        )
    existing = next(
        (item for item in compatibility if provider_host(item) == host),
        None,
    )
    target_providers.append(build_provider(existing, name, base_url, api_key, models))

config_after = deepcopy(config_before)
config_after["request-retry"] = 0
config_after["error-logs-max-files"] = 5
config_after["logs-max-total-size-mb"] = 32
config_after["routing"].update({
    "strategy": "fill-first",
    "session-affinity": True,
    "session-affinity-ttl": "1h",
    # Avoid concentrating a concurrent subagent fan-out on its parent's
    # credential. Parent sessions still retain their normal affinity/cache
    # locality; only child work falls back to pool distribution.
    "session-affinity-subagents": False,
})
config_after["codex"].update({
    # Disabled on 2026-09-28. Holding the bootstrap keeps the downstream
    # response headers uncommitted until the upstream starts generating, which
    # measured 9.4-10.3s of dead air per Luna turn before the handshake was
    # released as a single burst. The classification it bought is redundant on
    # this host: the Codex pool holds one account (no credential to fail over
    # to) and cpa-admission classifies a capacity marker inside an HTTP 200
    # body by itself. The ceiling stays explicit so a re-enable is one edit.
    "stream-bootstrap-buffering": False,
    "stream-bootstrap-timeout": "0",
})
config_after["openai-compatibility"] = remaining_compatibility + target_providers
if isinstance(config_after.get("codex-api-key"), list):
    config_after["codex-api-key"] = [
        item
        for item in config_after["codex-api-key"]
        if provider_host(item) not in target_hosts | legacy_hosts
    ]

# Keep all surviving Codex API-key lanes from competing with active provider
# aliases or retired names. The native Codex OAuth lane owns Luna; ai.input.im
# owns its bare Sol/Astra routes. Preserve every other exclusion already present.
codex_api_keys = config_after.get("codex-api-key")
if codex_api_keys is not None and not isinstance(codex_api_keys, list):
    raise SystemExit("REFUSE codex-api-key must be a list when present")
if isinstance(codex_api_keys, list):
    for index, provider in enumerate(codex_api_keys):
        if not isinstance(provider, dict):
            raise SystemExit(f"REFUSE codex-api-key[{index}] must be a mapping")
        excluded_models = provider.get("excluded-models", [])
        if not isinstance(excluded_models, list) or not all(
            isinstance(model, str) and model.strip() for model in excluded_models
        ):
            raise SystemExit(
                f"REFUSE codex-api-key[{index}].excluded-models must be a list"
            )
        excluded_normalized = {model.strip().lower() for model in excluded_models}
        for model in route_manifest.get("codex_api_key_exclusions", []):
            if model not in excluded_normalized:
                excluded_models.append(model)
                excluded_normalized.add(model)

# Keep GPT-6 Luna on the existing Codex OAuth lane while pinning the
# same-name Sol/Astra aliases to ai.input.im. Replace only the old family
# wildcard; preserve all unrelated OAuth exclusions and refuse broader rules
# that would need an unsafe expansion to make Luna visible. Exact-name
# exclusions for aliases the manifest currently declares as OAuth routes are
# stale retirements and are dropped so the alias is served again.
oauth_route_aliases = {
    str(model["alias"]).strip().lower()
    for route in route_manifest.get("oauth_routes", [])
    if isinstance(route, dict)
    for model in route.get("models", [])
    if isinstance(model, dict) and isinstance(model.get("alias"), str)
}
oauth_exclusions = config_after.get("oauth-excluded-models")
if oauth_exclusions is None:
    oauth_exclusions = {}
if not isinstance(oauth_exclusions, dict):
    raise SystemExit("REFUSE oauth-excluded-models must be a mapping")
codex_exclusions = oauth_exclusions.get("codex", [])
if not isinstance(codex_exclusions, list) or not all(
    isinstance(pattern, str) and pattern.strip() for pattern in codex_exclusions
):
    raise SystemExit("REFUSE oauth-excluded-models.codex must be a list of strings")
codex_exclusions_after = []
legacy_gpt6_wildcards = {"gpt-6*", "gpt-6-*"}
for pattern in codex_exclusions:
    normalized_pattern = pattern.strip().lower()
    if fnmatchcase("gpt-6-luna", normalized_pattern):
        if normalized_pattern in legacy_gpt6_wildcards:
            continue
        raise SystemExit(
            "REFUSE unexpected Codex OAuth exclusion blocks gpt-6-luna"
        )
    if normalized_pattern in oauth_route_aliases:
        continue
    codex_exclusions_after.append(pattern)
for model in route_manifest.get("oauth_exclusions", []):
    if model not in {pattern.strip().lower() for pattern in codex_exclusions_after}:
        codex_exclusions_after.append(model)
oauth_exclusions["codex"] = codex_exclusions_after
config_after["oauth-excluded-models"] = oauth_exclusions

allowed_top_level_changes = {
    "request-retry",
    "routing",
    "codex",
    "openai-compatibility",
    "codex-api-key",
    "oauth-excluded-models",
    "error-logs-max-files",
    "logs-max-total-size-mb",
}
before_unapproved = deepcopy(config_before)
after_unapproved = deepcopy(config_after)
for key in allowed_top_level_changes:
    before_unapproved.pop(key, None)
    after_unapproved.pop(key, None)
if before_unapproved != after_unapproved:
    raise SystemExit("config change exceeded the approved field/block set")

candidate = yaml.safe_dump(
    config_after,
    allow_unicode=True,
    default_flow_style=False,
    sort_keys=False,
)
# safe_dump rewrites the whole file; hand-written comments in config.yaml do
# not survive, so config content stays tool-owned (no hand edits).
parsed_candidate = yaml.safe_load(candidate)
if parsed_candidate != config_after:
    raise SystemExit("candidate YAML semantic round-trip failed")
atomic_write(config_path, candidate, 0o600)

updater_path = Path("/opt/cliproxyapi/auto-update.sh")
updater = updater_path.read_text(encoding="utf-8")
old_key_line = (
    "KEY=$(sed -n '/^api-keys:/{n;s/.*\\\"\\([0-9a-f]*\\)\\\".*/\\1/p}' "
    '\"$DIR/config.yaml\")'
)
new_key_line = (
    'KEY=$(grep -A1 "^api-keys:" "$DIR/config.yaml" | '
    'tail -n 1 | sed -E "s/[^0-9a-f]//g")'
)
if old_key_line in updater:
    updater = updater.replace(old_key_line, new_key_line, 1)
elif new_key_line not in updater and not (
    'KEY=$(awk \'/^api-keys:/{getline; line=$0; '
    'gsub(/[^0-9a-f]/, "", line); print line; exit}\' '
    '"$DIR/config.yaml")' in updater
    or "key = yaml.safe_load(open(sys.argv[1]))['api-keys'][0]" in updater
    or 'health() { python3 "$DIR/cpa-health.py" "$1"; }' in updater
):
    raise SystemExit("updater key extraction anchor not found")
atomic_write(updater_path, updater, 0o700)

nginx_path = Path("/etc/nginx/conf.d/cpa-gateway.conf")
nginx = nginx_path.read_text(encoding="utf-8")
admission_proxy = "proxy_pass http://127.0.0.1:8318/v1/$1$is_args$args;"
legacy_proxy = "proxy_pass http://127.0.0.1:8317/v1/$1$is_args$args;"
if admission_proxy not in nginx:
    if legacy_proxy not in nginx:
        raise SystemExit("expected CPA public route proxy anchor missing")
    nginx = nginx.replace(legacy_proxy, admission_proxy, 1)
if nginx.count(admission_proxy) != 1:
    raise SystemExit("expected exactly one shared-account admission proxy route")
auth_proxy = "proxy_pass http://127.0.0.1:8317/v1/models$is_args$args;"
if nginx.count(auth_proxy) != 1:
    raise SystemExit("expected exactly one direct CPA auth proxy route")
new_limit_conn = "limit_conn cpa_cc 20;"
legacy_limit_conn = "limit_conn cpa_cc 6;"
previous_limit_conn = "limit_conn cpa_cc 12;"
if new_limit_conn not in nginx:
    if previous_limit_conn in nginx:
        nginx = nginx.replace(previous_limit_conn, new_limit_conn, 1)
    elif legacy_limit_conn not in nginx:
        raise SystemExit("expected Nginx per-IP connection budget missing")
    # Accept previously deployed budgets (6 or 12) as input states
    # as an input state; the required-anchor check below then validates the
    # migrated value before nginx -t runs.
    nginx = nginx.replace(legacy_limit_conn, new_limit_conn, 1)

required = [
    "limit_req_zone $binary_remote_addr zone=cpa_rl:1m rate=10r/s;",
    "limit_conn_zone $binary_remote_addr zone=cpa_cc:1m;",
    "limit_conn cpa_cc 20;",
    "client_max_body_size 32m;",
    "client_body_buffer_size 128k;",
    "proxy_buffering off;",
    "proxy_read_timeout 300s;",
    "proxy_send_timeout 300s;",
]
for anchor in required:
    if anchor not in nginx:
        raise SystemExit(f"expected Nginx guardrail missing: {anchor}")
new_limit_req = "limit_req zone=cpa_rl burst=10;"
legacy_limit_req = "limit_req zone=cpa_rl burst=20 nodelay;"
if new_limit_req not in nginx:
    if legacy_limit_req not in nginx:
        raise SystemExit("expected Nginx request limiter missing")
    # Accept the previously deployed limiter as an input state. The same
    # transaction below rewrites it to the queued burst policy before nginx -t.
    nginx = nginx.replace(legacy_limit_req, new_limit_req, 1)


def ensure_nginx_directive(text, directive, anchor):
    # nginx defaults both throttle statuses to 503, which re-conflates local
    # rate limiting with upstream overload and gives clients no back-off
    # signal. The 2026-09-09 hardening set them to 429, but no versioned anchor
    # asserted them, so a regeneration could silently drop them. Insert next to
    # the matching limiter when absent; never reorder an existing directive.
    if directive in text:
        return text
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if anchor in line:
            indent = line[: len(line) - len(line.lstrip())]
            lines.insert(index + 1, indent + directive + "\n")
            return "".join(lines)
    raise SystemExit("expected Nginx limiter anchor missing for " + directive)


nginx = ensure_nginx_directive(nginx, "limit_req_status 429;", new_limit_req)
nginx = ensure_nginx_directive(nginx, "limit_conn_status 429;", "limit_conn cpa_cc 20;")

# Local throttle rejections must carry an explicit back-off signal. nginx emits
# no Retry-After on its own 429, so a client that wants to behave cannot tell how
# long to wait and falls back to its own cadence - the ~1.0s-median retry storm
# the doctor observes. The header is added at server scope, which is safe here
# because the deployed file declares no other add_header (an inner add_header
# would replace the inherited set). The map is non-empty ONLY for requests the
# local limiters actually rejected, so upstream 429s keep passing through
# untouched and 200/401/404/5xx responses never grow a forged header. The value
# is a floor, not a prediction: with burst=10 at 10r/s a rejected slot refills
# within ~1s, and a concurrency rejection clears as soon as one request ends.
throttle_retry_after_map = (
    "map \"$limit_req_status:$limit_conn_status\" $cpa_throttle_retry_after {\n"
    "    default \"\";\n"
    "    \"~REJECTED\" 1;\n"
    "}\n"
)
if 'map "$limit_req_status:$limit_conn_status" $cpa_throttle_retry_after {' not in nginx:
    nginx = throttle_retry_after_map + nginx
elif throttle_retry_after_map not in nginx:
    raise SystemExit(
        "existing cpa_throttle_retry_after map differs from approved throttle map"
    )
nginx = ensure_nginx_directive(
    nginx,
    "add_header Retry-After $cpa_throttle_retry_after always;",
    "limit_conn_status 429;",
)

route_class_map = (
    "map $uri $cpa_route_class {\n"
    "    \"~^/[0-9a-f]{16}/v1/models$\" models;\n"
    "    \"~^/[0-9a-f]{16}/v1/chat/completions$\" chat;\n"
    "    \"~^/[0-9a-f]{16}/v1/responses$\" responses;\n"
    "    default other;\n"
    "}\n"
)
if "map $uri $cpa_route_class {" not in nginx:
    nginx = route_class_map + nginx
elif route_class_map not in nginx:
    raise SystemExit("existing cpa_route_class map differs from approved redacted map")

retry_after_map = (
    "map $upstream_http_retry_after $cpa_retry_after_class {\n"
    "    \"\" absent;\n"
    "    \"~^[0-9]{1,5}$\" seconds;\n"
    "    default other;\n"
    "}\n"
)
if "map $upstream_http_retry_after $cpa_retry_after_class {" not in nginx:
    nginx = retry_after_map + nginx
elif retry_after_map not in nginx:
    raise SystemExit("existing cpa_retry_after_class map differs from approved redacted map")

log_format = (
    "log_format cpa_safe '$remote_addr method=$request_method "
    "route=$cpa_route_class "
    "status=$status request_time=$request_time "
    "upstream_status=$upstream_status "
    "upstream_time=$upstream_response_time bytes=$body_bytes_sent "
    "limit_req=$limit_req_status limit_conn=$limit_conn_status "
    "retry_after=$cpa_retry_after_class "
    "time=[$time_local] auth_status=$cpa_auth_status "
    # Appended last on purpose: every parser in this file matches the fields
    # before it positionally or with a trailing `.*`, so a new field at the end
    # cannot shift `bytes=`/`limit_req=` out from under them.
    # $upstream_header_time is the only field that exposes the client-visible
    # time-to-first-byte of a stream; $upstream_response_time only reports the
    # completed response, which for SSE is the whole turn. Without it a
    # 10s-of-dead-air regression is indistinguishable from a slow generation.
    "upstream_header_time=$upstream_header_time';\n"
)
legacy_log_format = log_format.replace(" auth_status=$cpa_auth_status", "")
# The format projected before TTFB was observable. Kept as a migration source
# so -Apply can upgrade an already-deployed host instead of refusing on drift.
pre_ttfb_log_format = log_format.replace(
    " upstream_header_time=$upstream_header_time", ""
)
without_retry_log_format = log_format.replace(
    " retry_after=$cpa_retry_after_class", ""
)
previous_log_format = without_retry_log_format.replace(
    " limit_req=$limit_req_status limit_conn=$limit_conn_status", ""
)
previous_legacy_log_format = previous_log_format.replace(
    " auth_status=$cpa_auth_status", ""
)
# Older deployed logs did not include route classification. Accept them during
# the read/replace transition, but every new projected config must use the
# redacted route class field rather than the random public path.
legacy_route_log_format = without_retry_log_format.replace(" auth_status=$cpa_auth_status", "")
previous_route_log_format = legacy_route_log_format.replace(
    " route=$cpa_route_class", ""
)
previous_unclassified_log_format = without_retry_log_format.replace(
    " route=$cpa_route_class", ""
)
# Adding auth_status requires the separately verified auth_request location.
if 'auth_request /_cpa_auth;' not in nginx:
    log_format = legacy_log_format
if "log_format cpa_safe " not in nginx:
  nginx = log_format + nginx
elif log_format not in nginx:
    old_formats = [
        pre_ttfb_log_format,
        without_retry_log_format,
        legacy_route_log_format,
        previous_unclassified_log_format,
        previous_route_log_format,
        previous_legacy_log_format,
        previous_log_format,
    ] if 'auth_request /_cpa_auth;' not in nginx else [
        pre_ttfb_log_format,
        without_retry_log_format,
        legacy_route_log_format,
        previous_unclassified_log_format,
        previous_route_log_format,
        previous_log_format,
        previous_legacy_log_format,
    ]
    for old_format in old_formats:
        if old_format in nginx:
            nginx = nginx.replace(old_format, log_format, 1)
            break
    else:
        raise SystemExit("existing cpa_safe log format differs from approved redacted format")
nginx = nginx.replace(
    "access_log /var/log/nginx/cpa_gateway.access.log;",
    "access_log /var/log/nginx/cpa_gateway.access.log cpa_safe;",
    1,
)
if "access_log /var/log/nginx/cpa_gateway.access.log cpa_safe;" not in nginx:
    raise SystemExit("safe access log anchor not installed")
atomic_write(nginx_path, nginx, 0o644)

nginx_logrotate_path = Path("/etc/logrotate.d/nginx")
if not nginx_logrotate_path.exists():
    raise SystemExit("existing Nginx logrotate file is missing")
nginx_logrotate = nginx_logrotate_path.read_text(encoding="utf-8")
if "/var/log/nginx/*.log" not in nginx_logrotate:
    raise SystemExit("existing Nginx logrotate does not cover gateway logs")

print(
    "CONFIG_POLICY_READY "
    f"request_retry={config_after['request-retry']}"
)
print("CODEX_OAUTH_ROUTES_READY gpt-6-luna/gpt-5.6-luna/gpt-6.1-sol=allowed gpt-6.1-sol-input/gpt-6-astra=excluded")
PY
then
  restore_all
  echo "ROLLBACK config_or_updater"
  exit 1
fi

write_base64_file() {
  encoded=$1
  path=$2
  mode=$3
  expected_sha=$4
  temp=$(mktemp "${path}.XXXXXX")
  if ! printf '%s' "$encoded" | base64 -d >"$temp"; then
    rm -f "$temp"
    return 1
  fi
  chmod "$mode" "$temp" || { rm -f "$temp"; return 1; }
  if ! mv -f "$temp" "$path"; then
    rm -f "$temp"
    return 1
  fi
  # Write-then-verify: the projected bytes must equal the repo-side payload
  # exactly; a mismatch rolls back through the caller instead of surviving as
  # silent drift for a human to catch in a later sha listing.
  if [ -n "$expected_sha" ]; then
    written_sha=$(sha256sum "$path" | awk '{print $1}')
    if [ "$written_sha" != "$expected_sha" ]; then
      echo "PROJECTION_HASH_MISMATCH path=$path want=$expected_sha got=$written_sha"
      return 1
    fi
    echo "PROJECTION_HASH_VERIFIED path=$path"
  fi
}
write_base64_file "__CPA_FAIL2BAN_FILTER_B64__" "$FAIL2BAN_FILTER" 644 "__CPA_FAIL2BAN_FILTER_SHA256__" || {
  restore_all
  echo "ROLLBACK fail2ban_filter_write"
  exit 1
}
write_base64_file "__CPA_FAIL2BAN_JAIL_B64__" "$FAIL2BAN_JAIL" 644 "__CPA_FAIL2BAN_JAIL_SHA256__" || {
  restore_all
  echo "ROLLBACK fail2ban_jail_write"
  exit 1
}
write_base64_file "__CPA_UPDATER_B64__" "$DIR/auto-update.sh" 700 "__CPA_UPDATER_SHA256__" || {
  restore_all
  echo "ROLLBACK updater_projection"
  exit 1
}
write_base64_file "__CPA_HEALTH_B64__" "$DIR/cpa-health.py" 644 "__CPA_HEALTH_SHA256__" || {
  restore_all
  echo "ROLLBACK health_projection"
  exit 1
}
write_base64_file "__CPA_PROVIDER_ROUTES_B64__" "$DIR/cpa_provider_routes.json" 644 "__CPA_PROVIDER_ROUTES_SHA256__" || {
  restore_all
  echo "ROLLBACK provider_route_projection"
  exit 1
}
write_base64_file "__CPA_POLICY_B64__" "$DIR/cpa_policy.py" 644 "__CPA_POLICY_SHA256__" || {
  restore_all
  echo "ROLLBACK policy_projection"
  exit 1
}
write_base64_file "__CPA_ADMISSION_B64__" "$ADMISSION_SCRIPT" 755 "__CPA_ADMISSION_SHA256__" || {
  restore_all
  echo "ROLLBACK admission_projection"
  exit 1
}
write_base64_file "__CPA_ADMISSION_CONFIG_B64__" "$ADMISSION_CONFIG" 644 "__CPA_ADMISSION_CONFIG_SHA256__" || {
  restore_all
  echo "ROLLBACK admission_config_projection"
  exit 1
}
write_base64_file "__CPA_ADMISSION_UNIT_B64__" "$ADMISSION_UNIT" 644 "__CPA_ADMISSION_UNIT_SHA256__" || {
  restore_all
  echo "ROLLBACK admission_unit_projection"
  exit 1
}
mkdir -p "$(dirname "$ADMISSION_INTEGRITY_CHECK")" "$(dirname "$ADMISSION_INTEGRITY_DROPIN")" "$(dirname "$ADMISSION_INTEGRITY_PIN")"
write_base64_file "__CPA_ADMISSION_INTEGRITY_CHECK_B64__" "$ADMISSION_INTEGRITY_CHECK" 755 "__CPA_ADMISSION_INTEGRITY_CHECK_SHA256__" || {
  restore_all
  echo "ROLLBACK admission_integrity_check_projection"
  exit 1
}
write_base64_file "__CPA_ADMISSION_INTEGRITY_DROPIN_B64__" "$ADMISSION_INTEGRITY_DROPIN" 644 "__CPA_ADMISSION_INTEGRITY_DROPIN_SHA256__" || {
  restore_all
  echo "ROLLBACK admission_integrity_dropin_projection"
  exit 1
}
write_base64_file "__CPA_ADMISSION_INTEGRITY_PIN_B64__" "$ADMISSION_INTEGRITY_PIN" 644 "__CPA_ADMISSION_INTEGRITY_PIN_SHA256__" || {
  restore_all
  echo "ROLLBACK admission_integrity_pin_projection"
  exit 1
}
if ! "$ADMISSION_INTEGRITY_CHECK" >/tmp/cpa-admission-integrity.log 2>&1; then
  restore_all
  echo "ROLLBACK admission_integrity_check"
  cat /tmp/cpa-admission-integrity.log
  exit 1
fi
rm -f /tmp/cpa-admission-integrity.log
if ! python3 -m py_compile "$ADMISSION_SCRIPT"; then
  restore_all
  echo "ROLLBACK admission_syntax"
  exit 1
fi
if ! python3 - "$ADMISSION_CONFIG" <<'PY'
import json
import sys
from pathlib import Path
config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if config.get("version") != 1 or config.get("listen_host") != "127.0.0.1":
    raise SystemExit(1)
if config.get("listen_port") != 8318 or config.get("upstream_port") != 8317:
    raise SystemExit(1)
if config.get("max_body_bytes") != 33554432 or config.get("probe_bytes") != 262144:
    raise SystemExit(1)
if config.get("retry_after_max_seconds") != 86400:
    raise SystemExit(1)
expected = {
    "chatgpt-oauth": ["gpt-6-luna", "gpt-5.6-luna", "gpt-6.1-sol"],
    "zhipu-coding-plan": ["glm-5.3", "glm-5.3-flash"],
}
expected_max_inflight = {
    "chatgpt-oauth": 2,
    "zhipu-coding-plan": 3,
}
lanes = config.get("lanes")
if not isinstance(lanes, list) or len(lanes) != len(expected):
    raise SystemExit(1)
for lane in lanes:
    if not isinstance(lane, dict) or lane.get("name") not in expected:
        raise SystemExit(1)
    if lane.get("models") != expected[lane["name"]]:
        raise SystemExit(1)
    if (
        lane.get("max_inflight") != expected_max_inflight.get(lane.get("name"))
        or lane.get("max_pending") != 4
    ):
        raise SystemExit(1)
    if lane.get("queue_timeout_seconds") != 120:
        raise SystemExit(1)
    if lane.get("cooldown_schedule_seconds") != [60, 120, 240, 480, 900]:
        raise SystemExit(1)
    if lane.get("cooldown_cap_seconds") != 900:
        raise SystemExit(1)
    if lane.get("cooldown_cap_seconds") > config["retry_after_max_seconds"]:
        raise SystemExit(1)
    if 429 not in lane.get("capacity_statuses", []):
        raise SystemExit(1)
if {lane.get("name") for lane in lanes} != set(expected):
    raise SystemExit(1)
PY
then
  restore_all
  echo "ROLLBACK admission_config_contract"
  exit 1
fi
if ! grep -Fq 'ExecStart=/usr/bin/python3 /opt/cliproxyapi/cpa-admission.py /opt/cliproxyapi/cpa-admission.json' "$ADMISSION_UNIT"; then
  restore_all
  echo "ROLLBACK admission_unit_contract"
  exit 1
fi
if ! python3 -m py_compile "$DIR/cpa-health.py"; then
  restore_all
  echo "ROLLBACK health_syntax"
  exit 1
fi
if grep -Eq '^[[:space:]]*listen[[:space:]]+\[::\]:8443[[:space:]]+ssl;' "$NGINX_CONF"; then
  restore_all
  echo "ROLLBACK unexpected_ipv6_public_listener"
  exit 1
fi
if ! python3 -m py_compile "$DIR/cpa_policy.py"; then
  restore_all
  echo "ROLLBACK policy_syntax"
  exit 1
fi
if ! python3 "$DIR/cpa_policy.py" "$DIR/config.yaml" >/tmp/cpa-policy-test.log 2>&1; then
  restore_all
  echo "ROLLBACK semantic_policy"
  tail -n 20 /tmp/cpa-policy-test.log
  exit 1
fi

chmod 600 "$DIR/config.yaml"
chmod 700 "$DIR/auto-update.sh"
if [ -d "$DIR/auth/logs" ]; then
  chmod 700 "$DIR/auth/logs"
  find "$DIR/auth/logs" -maxdepth 1 -type f -name 'error-*.log' -exec chmod 600 -- {} +
fi

if ! bash -n "$DIR/auto-update.sh"; then
  restore_all
  echo "ROLLBACK updater_syntax"
  exit 1
fi
if ! docker compose -f "$DIR/compose.yml" config --quiet; then
  restore_all
  echo "ROLLBACK compose_config"
  exit 1
fi
if ! grep -Fq 'umask 077' "$DIR/compose.yml" ||
   ! grep -Fq 'exec ./CLIProxyAPI' "$DIR/compose.yml"; then
  restore_all
  echo "ROLLBACK compose_umask"
  exit 1
fi
if ! assert_merged_nginx_route_contract; then
  restore_all
  echo "ROLLBACK merged_nginx_route_contract"
  exit 1
fi
if ! grep -Fq 'access_log /var/log/nginx/cpa_gateway.access.log cpa_safe;' "$NGINX_CONF"; then
  restore_all
  echo "ROLLBACK safe_access_log"
  exit 1
fi
if ! grep -Fq 'map $uri $cpa_route_class {' "$NGINX_CONF" ||
   ! grep -Fq 'route=$cpa_route_class' "$NGINX_CONF"; then
  restore_all
  echo "ROLLBACK redacted_route_class_observability"
  exit 1
fi
for anchor in \
  'client_max_body_size 32m;' \
  'client_body_buffer_size 128k;' \
  'proxy_buffering off;' \
  'proxy_read_timeout 300s;' \
  'proxy_send_timeout 300s;'; do
  if ! grep -Fq "$anchor" "$NGINX_CONF"; then
    restore_all
    echo "ROLLBACK gateway_transport_contract anchor=$anchor"
    exit 1
  fi
done
for anchor in \
  'limit_req zone=cpa_rl burst=10;' \
  'limit_req_status 429;' \
  'limit_conn_status 429;' \
  'add_header Retry-After $cpa_throttle_retry_after always;' \
  'map "$limit_req_status:$limit_conn_status" $cpa_throttle_retry_after {'; do
  if ! grep -Fq "$anchor" "$NGINX_CONF"; then
    restore_all
    echo "ROLLBACK gateway_throttle_contract anchor=$anchor"
    exit 1
  fi
done
for anchor in \
  'proxy_pass http://127.0.0.1:8318/v1/$1$is_args$args;' \
  'proxy_pass http://127.0.0.1:8317/v1/models$is_args$args;'; do
  if ! grep -Fq "$anchor" "$NGINX_CONF"; then
    restore_all
    echo "ROLLBACK admission_route_contract anchor=$anchor"
    exit 1
  fi
done
if ! fail2ban-client -t >/dev/null 2>&1; then
  restore_all
  echo "ROLLBACK fail2ban_syntax"
  exit 1
fi
if ! migrate_legacy_admission; then
  restore_all
  echo "ROLLBACK legacy_admission_migration"
  exit 1
fi

if ! docker restart cli-proxy-api >/dev/null; then
  restore_all
  echo "ROLLBACK cpa_restart"
  exit 1
fi

READY=000
for _ in $(seq 1 30); do
  READY=$(curl --noproxy '*' -sS --max-time 5 -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer $KEY" \
    http://127.0.0.1:8317/v1/models || true)
  if [ "$READY" = "200" ]; then
    break
  fi
  sleep 1
done
if [ "$READY" != "200" ]; then
  restore_all
  echo "ROLLBACK cpa_readiness status=$READY"
  exit 1
fi
if ! python3 "$DIR/cpa-health.py" readiness; then
  restore_all
  echo "ROLLBACK cpa_model_catalog_contract"
  exit 1
fi
if ! systemctl daemon-reload >/tmp/cpa-admission-daemon-reload.log 2>&1; then
  restore_all
  echo "ROLLBACK admission_daemon_reload"
  tail -n 5 /tmp/cpa-admission-daemon-reload.log
  exit 1
fi
if ! systemctl enable cpa-admission.service >/tmp/cpa-admission-enable.log 2>&1; then
  restore_all
  echo "ROLLBACK admission_enable"
  tail -n 5 /tmp/cpa-admission-enable.log
  exit 1
fi
# restart, not enable --now: on an already enabled+active service
# "enable --now" is a no-op, so a re-apply would leave the previous
# generation's process running the old projected code.
if ! systemctl restart cpa-admission.service >/tmp/cpa-admission-start.log 2>&1; then
  restore_all
  echo "ROLLBACK admission_restart"
  tail -n 5 /tmp/cpa-admission-start.log
  exit 1
fi
ADMISSION_READY=000
for _ in $(seq 1 15); do
  ADMISSION_READY=$(curl --noproxy '*' -sS --max-time 3 -o /dev/null -w '%{http_code}' \
    http://127.0.0.1:8318/healthz || true)
  if [ "$ADMISSION_READY" = "200" ]; then
    break
  fi
  sleep 1
done
if [ "$ADMISSION_READY" != "200" ]; then
  restore_all
  echo "ROLLBACK admission_health status=$ADMISSION_READY"
  exit 1
fi
SERVER_NAME=$(awk '/^[[:space:]]*server_name[[:space:]]/{gsub(";", "", $2); print $2; exit}' "$NGINX_CONF")
PREFIX=$(grep -oE '/[0-9a-f]{16}/v1/' "$NGINX_CONF" | head -n 1 | cut -d/ -f2)
if [ -z "$SERVER_NAME" ] || [ -z "$PREFIX" ]; then
  restore_all
  echo "ROLLBACK public_route_inputs"
  exit 1
fi
PUBLIC_READY=$(curl --noproxy '*' -sS --connect-timeout 5 --max-time 10 \
  --resolve "$SERVER_NAME:8443:127.0.0.1" -H "Authorization: Bearer $KEY" \
  -o /dev/null -w '%{http_code}' "https://$SERVER_NAME:8443/$PREFIX/v1/models" 2>/dev/null || echo 000)
if [ "$PUBLIC_READY" != "200" ]; then
  restore_all
  echo "ROLLBACK public_authenticated_probe status=$PUBLIC_READY"
  exit 1
fi

if ! nginx -t >/tmp/cpa-nginx-test.log 2>&1; then
  restore_all
  echo "ROLLBACK nginx_syntax"
  tail -n 5 /tmp/cpa-nginx-test.log
  exit 1
fi
if ! assert_admission_nginx_route_contract; then
  restore_all
  echo "ROLLBACK admission_route_contract_merged"
  exit 1
fi
if ! grep -Eq '^[[:space:]]*listen[[:space:]]+8443[[:space:]]+ssl;' "$NGINX_CONF"; then
  restore_all
  echo "ROLLBACK nginx_public_listener_changed"
  exit 1
fi
RANDOM_PATH_AFTER=$(grep -oE '/[0-9a-f]{16}/v1/' "$NGINX_CONF" | head -n 1 | cut -d/ -f2)
if [ "$RANDOM_PATH_AFTER" != "$RANDOM_PATH_BEFORE" ]; then
  restore_all
  echo "ROLLBACK nginx_random_path_changed"
  exit 1
fi
if ! grep -Eq 'location[[:space:]]+~[[:space:]]+\^/[0-9a-f]{16}/v1/' "$NGINX_CONF"; then
  restore_all
  echo "ROLLBACK nginx_route_shape_changed"
  exit 1
fi
if ! systemctl reload nginx >/tmp/cpa-nginx-reload.log 2>&1; then
  restore_all
  echo "ROLLBACK nginx_reload"
  tail -n 5 /tmp/cpa-nginx-reload.log
  exit 1
fi
if ! fail2ban-client reload --restart cpa-gateway >/tmp/cpa-fail2ban-reload.log 2>&1; then
  restore_all
  echo "ROLLBACK fail2ban_reload"
  tail -n 5 /tmp/cpa-fail2ban-reload.log
  exit 1
fi
if ! assert_merged_nginx_route_contract; then
  restore_all
  echo "ROLLBACK merged_nginx_route_contract_after_reload"
  exit 1
fi
if ! assert_admission_nginx_route_contract; then
  restore_all
  echo "ROLLBACK admission_route_contract_after_reload"
  exit 1
fi
if ! assert_public_route_contract; then
  restore_all
  echo "ROLLBACK public_route_contract_after_reload"
  exit 1
fi
if ! ss -ltn | grep -Eq '0\.0\.0\.0:8443[[:space:]]'; then
  restore_all
  echo "ROLLBACK nginx_public_listener_missing"
  exit 1
fi
if ss -ltn | grep -Eq '\[::\]:8443[[:space:]]|:::8443[[:space:]]'; then
  restore_all
  echo "ROLLBACK unexpected_ipv6_public_listener_runtime"
  exit 1
fi
if ! ss -ltn | grep -Eq '127\.0\.0\.1:8317[[:space:]]'; then
  restore_all
  echo "ROLLBACK cpa_loopback_listener_missing"
  exit 1
fi
if ss -ltn | grep -Eq '\[::\]:8317[[:space:]]|:::8317[[:space:]]'; then
  restore_all
  echo "ROLLBACK unexpected_ipv6_cpa_listener_runtime"
  exit 1
fi
if ! ss -ltn | grep -Eq '127\.0\.0\.1:8318[[:space:]]'; then
  restore_all
  echo "ROLLBACK admission_listener_missing"
  exit 1
fi
if ss -ltn | grep -Eq '\[::\]:8318[[:space:]]|:::8318[[:space:]]'; then
  restore_all
  echo "ROLLBACK unexpected_ipv6_admission_listener_runtime"
  exit 1
fi
if ! systemctl is-enabled --quiet cpa-admission.service ||
   ! systemctl is-active --quiet cpa-admission.service; then
  restore_all
  echo "ROLLBACK admission_service_not_active"
  exit 1
fi
if systemctl is-active --quiet cpa-luna-admission.service 2>/dev/null ||
   systemctl is-enabled --quiet cpa-luna-admission.service 2>/dev/null ||
   [ -e "$LEGACY_ADMISSION_UNIT" ] ||
   [ -e "$LEGACY_ADMISSION_SCRIPT" ] ||
   [ -e "$LEGACY_ADMISSION_CONFIG" ]; then
  restore_all
  echo "ROLLBACK legacy_admission_residue"
  exit 1
fi

if ! logrotate -d /etc/logrotate.conf >/tmp/cpa-logrotate-test.log 2>&1; then
  restore_all
  echo "ROLLBACK logrotate_syntax"
  tail -n 5 /tmp/cpa-logrotate-test.log
  exit 1
fi

trap - EXIT INT TERM
echo "BACKUP_DIR=$BK"
echo "READY_STATUS=$READY"
sha256sum "$DIR/config.yaml" "$DIR/auto-update.sh" "$DIR/cpa-health.py" "$DIR/cpa_policy.py" "$DIR/cpa_provider_routes.json" "$DIR/cpa-admission.py" "$DIR/cpa-admission.json" "$ADMISSION_UNIT" "$NGINX_CONF" "$FAIL2BAN_FILTER" "$FAIL2BAN_JAIL" || \
  echo "WARNING checksum_summary_failed"
echo "==catalog_summary=="
if ! curl --noproxy '*' -sS --max-time 20 -H "Authorization: Bearer $KEY" \
  http://127.0.0.1:8317/v1/models |
  python3 -c '
import json, sys
d = json.load(sys.stdin)
ids = [item.get("id", "") for item in d.get("data", [])]
print("models=" + str(len(ids)))
print("has_deepseek=" + str(any(i.startswith("deepseek-") for i in ids)))
print("has_r1=" + str(any(i.startswith("r1/") for i in ids)))
print("has_bare_luna=" + str("gpt-5.6-luna" in ids))
print("has_bare_gpt6_luna=" + str("gpt-6-luna" in ids))
print("has_ai_input_im_bare_gpt61_sol=" + str("gpt-6.1-sol-input" in ids))
print("has_ai_input_im_bare_astra=" + str("gpt-6-astra" in ids))
print("has_retired_ai_input_im_bare_gpt56_sol=" + str("gpt-5.6-sol" in ids))
print("has_retired_slot1_gpt6_sol_input=" + str("gpt-6-sol-input" in ids))
print("has_ai_input_im_bare_deepseek_v41_flash=" + str("deepseek-v4.1-flash" in ids))
print("has_retired_slot1_image_gpt_2_5=" + str("gpt-image-2.5" in ids))
print("has_ciii_gpt6_astra=" + str("gpt-6-astra-ciii" in ids))
print("has_ciii_gpt61_sol=" + str("gpt-6.1-sol-ciii" in ids))
print("has_retired_ciii_gpt6_astra_cii=" + str("gpt-6-astra-cii" in ids))
print("has_retired_ciii_gpt6_sol=" + str("gpt-6-sol-cii" in ids))
print("has_slot3_gpt61_sol_91=" + str("gpt-6.1-sol-91" in ids))
print("has_slot3_gpt56_terra=" + str("gpt-5.6-terra" in ids))
print("has_previous_gpt56_sol_terra_aliases=" + str(bool({"gpt-5.6-sol-91", "gpt-5.6-terra-91"} & set(ids))))
print("has_retired_deepseek_v4_pro=" + str("deepseek-v4-pro" in ids))
print("has_ciii_retired_models=" + str(bool({"codex-auto-review", "gpt-5.5", "gpt-5.6", "gpt-reserve"} & set(ids))))
print("has_glm_5_3=" + str("glm-5.3" in ids))
print("has_glm_5_3_flash=" + str("glm-5.3-flash" in ids))
print("has_retired_glm_5_3_flashx=" + str("glm-5.3-flashx" in ids))
'; then
  echo "WARNING catalog_summary_failed"
fi
echo "GUARDRAILS_APPLIED"
prune_backup_history
