#requires -Version 7
[CmdletBinding()]
param(
  [string]$Config,
  [Parameter(Mandatory = $true)]
  [ValidateSet("bwg", "zz")]
  [string]$Profile,
  [switch]$Apply
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
. (Join-Path $PSScriptRoot "lib\project_environment.ps1")

Initialize-WindowsProcessEnvironment
$script:Python = Resolve-ProjectPython -ProjectRoot $repoRoot -AllowPyLauncher
$Config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot -Config $Config
if (-not (Test-Path -LiteralPath $Config)) {
  throw "Config file not found: $Config"
}

$applyLiteral = if ($Apply) { "1" } else { "0" }

# NGINX_ROOT is the static site root that a plain HTTPS probe reaches (the
# REALITY fallback target on bwg, and the reality-handshake/subscribe vhost on
# zz). vasma ships a self-contained decoy blog under $NGINX_ROOT/en but the
# atomic root index.html is left as the stock "Welcome to nginx!" page, which
# is itself a recognisable censorship fingerprint. This lane promotes the
# existing decoy site to the document root and quarantines stock nginx
# artifacts left in the root (50x.html answers 200 on every vhost serving
# this root). It only rewrites static files under the root; it never touches
# the proxy core, TLS material, the CPA lanes, or the subscription location,
# all of which are resolved by nginx location/server config rather than the
# root.
$remoteCommand = @'
#!/usr/bin/env bash
set -Eeuo pipefail
APPLY="__APPLY__"
LOCK_FILE="${VPS_CAMOUFLAGE_LOCK_FILE:-/run/vps-ssh-launcher-maintenance.lock}"
NGINX_ROOT="${VPS_CAMOUFLAGE_NGINX_ROOT:-/usr/share/nginx/html}"
STATUS_DIR="${VPS_CAMOUFLAGE_STATUS_DIR:-/var/lib/vps-ssh-launcher/maintenance-status}"
BACKUP_PARENT="${VPS_CAMOUFLAGE_BACKUP_DIR:-/var/backups}"
NGINX_TEST_CMD="${VPS_CAMOUFLAGE_NGINX_TEST:-nginx -t}"
NGINX_CONF_DIR="${VPS_CAMOUFLAGE_NGINX_CONF_DIR:-/etc/nginx}"
DECOY_DIR="$NGINX_ROOT/en"
ROOT_INDEX="$NGINX_ROOT/index.html"
STATUS_FILE="$STATUS_DIR/nginx-camouflage.status"

write_status() {
    local result="$1" code="$2" finished tmp
    finished="$(date -u +%FT%TZ)"
    mkdir -m 700 -p "$STATUS_DIR" 2>/dev/null || return 0
    tmp="$(mktemp "$STATUS_FILE.XXXXXX" 2>/dev/null || true)"
    [ -n "$tmp" ] || return 0
    {
        printf 'name=nginx-camouflage\n'
        printf 'finished_at=%s\n' "$finished"
        printf 'result=%s\n' "$result"
        printf 'exit_code=%s\n' "$code"
    } > "$tmp" 2>/dev/null || true
    chmod 600 "$tmp" 2>/dev/null || true
    mv -f -- "$tmp" "$STATUS_FILE" 2>/dev/null || rm -f -- "$tmp"
}

echo '==camouflage-state=='
if [ ! -d "$DECOY_DIR" ] || [ ! -f "$DECOY_DIR/index.html" ]; then
    echo 'decoy_source=missing'
else
    echo 'decoy_source=present'
fi
root_sha='missing'
decoy_sha='missing'
if [ -f "$ROOT_INDEX" ]; then root_sha="$(sha256sum "$ROOT_INDEX" | awk '{print $1}')"; fi
if [ -f "$DECOY_DIR/index.html" ]; then decoy_sha="$(sha256sum "$DECOY_DIR/index.html" | awk '{print $1}')"; fi
echo "root_index_sha=$root_sha"
echo "decoy_index_sha=$decoy_sha"
if [ "$root_sha" = "$decoy_sha" ] && [ "$root_sha" != 'missing' ]; then
    echo 'camouflage=applied'
elif [ -f "$ROOT_INDEX" ] && grep -qE 'Welcome to nginx!|If you see this page' "$ROOT_INDEX" 2>/dev/null; then
    echo 'camouflage=stock-default'
else
    echo 'camouflage=custom'
fi
stock_extras=''
for stock_file in 50x.html index.nginx-debian.html; do
    if [ -e "$NGINX_ROOT/$stock_file" ] || [ -L "$NGINX_ROOT/$stock_file" ]; then
        stock_extras="$stock_extras $stock_file"
    fi
done
echo "stock_extras=${stock_extras:-none}"

if [ "$APPLY" != '1' ]; then
    write_status success 0
    exit 0
fi

if [ ! -f "$DECOY_DIR/index.html" ]; then
    echo 'REFUSE decoy_source=missing' >&2
    write_status failed 4
    exit 4
fi

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "REFUSE busy lock=$LOCK_FILE" >&2
    write_status busy 75
    exit 75
fi

backup_dir="$(mktemp -d "$BACKUP_PARENT/nginx-camouflage.XXXXXX")"
chmod 700 "$backup_dir"
backup_ready=0

remove_unbacked_paths() {
    # cp -a restores files from the backup but does not delete files introduced
    # by the failed decoy promotion (for example a previously absent index).
    # Remove only paths below the explicitly configured document root that are
    # absent from this run's backup tree, preserving unrelated system paths.
    while IFS= read -r -d '' current; do
        rel="${current#"$NGINX_ROOT/"}"
        if [ ! -e "$backup_dir/html/$rel" ] && [ ! -L "$backup_dir/html/$rel" ]; then
            rm -rf -- "$current" || return 1
        fi
    done < <(find "$NGINX_ROOT" -mindepth 1 -print0)
}

rollback() {
    trap - ERR INT TERM
    set +e
    rollback_failed=0
    if [ "$backup_ready" -eq 1 ]; then
        if ! remove_unbacked_paths; then
            rollback_failed=1
        fi
        if ! cp -a "$backup_dir/html/." "$NGINX_ROOT/"; then
            rollback_failed=1
        fi
        if [ -f "$backup_dir/html/index.html" ]; then
            if ! cmp -s "$backup_dir/html/index.html" "$ROOT_INDEX"; then
                rollback_failed=1
            fi
        elif [ -e "$ROOT_INDEX" ]; then
            # A missing original root page is valid state; the old unconditional
            # cmp treated that state as a rollback failure.
            rollback_failed=1
        fi
        if ! $NGINX_TEST_CMD >/dev/null 2>&1; then
            rollback_failed=1
        fi
        if [ "$rollback_failed" -eq 0 ]; then
            echo "ROLLBACK_VERIFIED backup=$backup_dir"
        else
            echo "ROLLBACK_FAILED backup=$backup_dir" >&2
        fi
    fi
    write_status failed 1
    exit 1
}
trap rollback ERR
trap 'exit 130' INT
trap 'exit 143' TERM

if ! mkdir -p "$backup_dir/html" || ! cp -a "$NGINX_ROOT/." "$backup_dir/html/"; then
    echo 'BACKUP_COPY_FAILED' >&2
    rm -rf -- "$backup_dir"
    write_status failed 1
    exit 1
fi
backup_ready=1

# Quarantine stock nginx artifacts before the promotion. 50x.html is the
# other half of the "Welcome to nginx!" fingerprint: it answers 200 on every
# vhost serving this root. Removal is guarded: if any nginx config still
# points an error_page at 50x.html the file must stay (removing it would turn
# 5xx bodies into 404s). The full-root backup above keeps the removal
# reversible like every other write in this lane.
if [ -e "$NGINX_ROOT/50x.html" ] || [ -L "$NGINX_ROOT/50x.html" ]; then
    if grep -rq -- '50x\.html' "$NGINX_CONF_DIR" 2>/dev/null; then
        echo "STOCK_FILE_KEEP_REFERENCED file=50x.html conf_dir=$NGINX_CONF_DIR"
    else
        rm -f -- "$NGINX_ROOT/50x.html" || { echo 'STOCK_FILE_REMOVAL_FAILED' >&2; rollback; }
        echo 'STOCK_FILE_REMOVED file=50x.html'
    fi
fi
if [ -e "$NGINX_ROOT/index.nginx-debian.html" ] || [ -L "$NGINX_ROOT/index.nginx-debian.html" ]; then
    rm -f -- "$NGINX_ROOT/index.nginx-debian.html" || { echo 'STOCK_FILE_REMOVAL_FAILED' >&2; rollback; }
    echo 'STOCK_FILE_REMOVED file=index.nginx-debian.html'
fi

if ! cmp -s "$DECOY_DIR/index.html" "$ROOT_INDEX"; then
    if ! cp -a "$DECOY_DIR/." "$NGINX_ROOT/"; then
        echo 'CAMOUFLAGE_COPY_FAILED' >&2
        rollback
    fi
fi

if ! $NGINX_TEST_CMD; then
    echo 'NGINX_TEST_FAILED' >&2
    rollback
fi
if ! cmp -s "$DECOY_DIR/index.html" "$ROOT_INDEX"; then
    echo 'CAMOUFLAGE_INDEX_MISMATCH' >&2
    rollback
fi

trap - ERR INT TERM
echo "APPLY_BACKUP_DIR=$backup_dir"
echo "ROOT_INDEX_SHA=$(sha256sum "$ROOT_INDEX" | awk '{print $1}')"
echo 'CAMOUFLAGE_APPLIED'
write_status success 0
'@.Replace("__APPLY__", $applyLiteral)

Invoke-LauncherRemoteCommand -Python $script:Python -ProjectRoot $repoRoot -Config $Config -Profile $Profile -Command $remoteCommand -RemoteTempPrefix "nginx-camouflage"
