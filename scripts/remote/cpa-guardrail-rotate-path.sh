set -Eeuo pipefail

# Same lock as auto-update.sh: the daily timer and guardrail transactions
# refuse to overlap instead of interleaving backups, restarts, and rollbacks.
exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo "REFUSE cpa_busy vps-ssh-launcher-maintenance.lock held"; exit 1; }

NGINX_CONF=/etc/nginx/conf.d/cpa-gateway.conf
BK=/root/cpa-guardrails-path-backup-$(date -u +%Y%m%dT%H%M%S.%NZ)

prune_backup_history() {
  local keep=8 entry removed=0
  while IFS= read -r entry; do
    [ -n "$entry" ] || continue
    if rm -rf -- "$entry"; then
      removed=$((removed + 1))
    else
      echo "PRUNE_FAILED scope=cpa_path_backups path=$entry"
    fi
  done < <(
    find /root -mindepth 1 -maxdepth 1 -type d \
      -name 'cpa-guardrails-path-backup-*' -printf '%T@ %p\n' 2>/dev/null |
      sort -rn | tail -n +$((keep + 1)) | cut -d' ' -f2-
  )
  echo "PRUNE scope=cpa_path_backups removed=$removed policy=keep_$keep"
}

if ! mkdir -m 700 "$BK"; then
  echo "ROTATION_REFUSED backup_exists_or_create_failed path=$BK"
  exit 1
fi
cp -a "$NGINX_CONF" "$BK/cpa-gateway.conf"
chmod 700 "$BK"

OLD_PATH=$(grep -oE '/[0-9a-f]{16}/v1/' "$NGINX_CONF" | head -n 1 | cut -d/ -f2)
if [ -z "$OLD_PATH" ] || ! grep -Eq '^[[:space:]]*listen[[:space:]]+8443[[:space:]]+ssl;' "$NGINX_CONF"; then
  echo "ROTATION_REFUSED unexpected Nginx contract"
  exit 1
fi

NEW_PATH=$(openssl rand -hex 8)
if ! printf '%s' "$NEW_PATH" | grep -Eq '^[0-9a-f]{16}$' || [ "$NEW_PATH" = "$OLD_PATH" ]; then
  echo "ROTATION_REFUSED invalid generated path"
  exit 1
fi

if ! python3 - "$NGINX_CONF" "$OLD_PATH" "$NEW_PATH" <<'PY'
import os
import sys
import tempfile
from pathlib import Path

path = Path(sys.argv[1])
old = "/" + sys.argv[2] + "/v1/"
new = "/" + sys.argv[3] + "/v1/"
text = path.read_text(encoding="utf-8")
if text.count(old) != 1:
    raise SystemExit("expected exactly one random path")
candidate = text.replace(old, new, 1)
fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
temp = Path(name)
try:
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(candidate)
    os.chmod(temp, path.stat().st_mode & 0o777)
    os.replace(temp, path)
finally:
    if temp.exists():
        temp.unlink()
PY
then
  echo "ROTATION_REFUSED path replacement"
  exit 1
fi

assert_path_route_contract() {
  active_path=${1:-$NEW_PATH}
  revoked_path=${2:-$OLD_PATH}
  server_name=$(awk '/^[[:space:]]*server_name[[:space:]]/{gsub(";", "", $2); print $2; exit}' "$NGINX_CONF")
  if [ -z "$server_name" ]; then
    return 1
  fi
  public_base="https://$server_name:8443"
  active_status=$(curl --noproxy '*' -sS --connect-timeout 5 --max-time 10 \
    --resolve "$server_name:8443:127.0.0.1" -o /dev/null -w '%{http_code}' \
    "$public_base/$active_path/v1/models" 2>/dev/null || echo 000)
  revoked_status=$(curl --noproxy '*' -sS --connect-timeout 5 --max-time 10 \
    --resolve "$server_name:8443:127.0.0.1" -o /dev/null -w '%{http_code}' \
    "$public_base/$revoked_path/v1/models" 2>/dev/null || echo 000)
  bare_status=$(curl --noproxy '*' -sS --connect-timeout 5 --max-time 10 \
    --resolve "$server_name:8443:127.0.0.1" -o /dev/null -w '%{http_code}' \
    "$public_base/v1/models" 2>/dev/null || echo 000)
  wrong_prefix=0000000000000000
  if [ "$wrong_prefix" = "$active_path" ] || [ "$wrong_prefix" = "$revoked_path" ]; then
    wrong_prefix=ffffffffffffffff
  fi
  wrong_status=$(curl --noproxy '*' -sS --connect-timeout 5 --max-time 10 \
    --resolve "$server_name:8443:127.0.0.1" -o /dev/null -w '%{http_code}' \
    "$public_base/$wrong_prefix/v1/models" 2>/dev/null || echo 000)
  [ "$active_status" = "401" ] &&
    [ "$revoked_status" = "404" ] &&
    [ "$bare_status" = "404" ] &&
    [ "$wrong_status" = "404" ] &&
    ss -ltn | grep -Eq '0\.0\.0\.0:8443[[:space:]]' &&
    ! ss -ltn | grep -Eq '\[::\]:8443[[:space:]]|:::8443[[:space:]]'
}

restore_path() {
  set +e
  rollback_failed=0
  cp -a "$BK/cpa-gateway.conf" "$NGINX_CONF" || rollback_failed=1
  if nginx -t >/dev/null 2>&1; then
    systemctl reload nginx >/dev/null 2>&1 || rollback_failed=1
  else
    rollback_failed=1
  fi
  assert_path_route_contract "$OLD_PATH" "$NEW_PATH" || rollback_failed=1
  if [ "$rollback_failed" -eq 0 ]; then
    echo "ROLLBACK_VERIFIED"
  else
    echo "ROLLBACK_FAILED"
  fi
  set -e
  return 0
}

if ! nginx -t >/tmp/cpa-path-nginx-test.log 2>&1; then
  restore_path
  echo "ROLLBACK path_nginx_syntax"
  tail -n 5 /tmp/cpa-path-nginx-test.log
  exit 1
fi
if ! systemctl reload nginx >/tmp/cpa-path-nginx-reload.log 2>&1; then
  restore_path
  echo "ROLLBACK path_nginx_reload"
  tail -n 5 /tmp/cpa-path-nginx-reload.log
  exit 1
fi

if ! assert_path_route_contract; then
  restore_path
  echo "ROLLBACK path_probe"
  exit 1
fi

echo "BACKUP_DIR=$BK"
echo "OLD_PATH_REVOKED=yes"
echo "NEW_PATH_ACTIVE=yes"
echo "NEW_PATH_NOT_PRINTED=yes"
prune_backup_history
