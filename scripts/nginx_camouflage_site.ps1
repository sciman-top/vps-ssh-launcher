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
# this root). It also projects a dpkg path-exclude for those stock files:
# nginx package upgrades (monthly maintenance) otherwise unpack them back and
# silently revert the promotion on every release. It only rewrites static
# files under the root; it never touches
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
NOT_FOUND_PAGE="$NGINX_ROOT/404.html"
ERROR_CONF="$NGINX_CONF_DIR/conf.d/vps-launcher-camouflage-errors.conf"
DPKG_EXCLUDE_CONF="${VPS_CAMOUFLAGE_DPKG_CONF:-/etc/dpkg/dpkg.cfg.d/vps-launcher-camouflage}"
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

# The stock pages are package payload, not conffiles: every nginx upgrade
# unpacks them back into the root and silently reverts the promotion (first
# seen 2026-11-01: monthly maintenance upgraded nginx 1.31.7/1.31.6 -> 1.31.8
# and both hosts flipped back to "Welcome to nginx!"). The lane therefore owns
# a dpkg path-exclude for exactly those files, so package upgrades stop
# touching the lane-managed root. Applied below; audited here like the rest of
# the lane state.
dpkg_hits=0
for exclude_line in \
    'path-exclude=/usr/share/nginx/html/index.html' \
    'path-exclude=/usr/share/nginx/html/50x.html' \
    'path-exclude=/usr/share/nginx/html/index.nginx-debian.html'; do
    if [ -f "$DPKG_EXCLUDE_CONF" ] && grep -Fqx -- "$exclude_line" "$DPKG_EXCLUDE_CONF"; then
        dpkg_hits=$((dpkg_hits + 1))
    fi
done
case "$dpkg_hits" in
    3) echo 'dpkg_exclude=present' ;;
    0) echo 'dpkg_exclude=missing' ;;
    *) echo 'dpkg_exclude=partial' ;;
esac

# Template-distribution residue (PSD sources, the original theme zip bundles,
# macOS archiver sidecars, and the builder's own Dockerfile/README/manifest/PWA
# service-worker files) is not a managed-nginx fingerprint like 50x.html, but a
# real manufacturer's website never exposes it either. An active prober that
# requests /psd/index.psd, /html4.zip or /README.md and receives a raw asset is
# looking at a hand-assembled decoy. These are quarantined on Apply only when
# they are top-level, unreferenced by any served HTML, and not live content
# that this lane's own promotion placed (the /s subscription path is excluded
# from every scan), so a promoted decoy that one day legitimately ships a theme
# zip is kept instead of silently deleted.
residue_find() {
    # Print one match per line: *.psd/*.zip/README.md/Dockerfile, psd/ asset
    # dirs, macOS/Linux desktop sidecars (__MACOSX, ._*, .DS_Store, Thumbs.db),
    # PWA manifest/precache/service-worker files, and the empty builder `check`
    # marker. `.psd` images of any size are always build sources. `-iname`
    # covers case variants; the `Thumbs.db`/`.DS_Store` names are matched
    # case-insensitively by the same flag. Scans the whole tree (not just the
    # top level) because the decoy template ships e.g. img/pro.psd and
    # __MACOSX/ sidecars beside the real assets.
    find "$NGINX_ROOT" -path "$NGINX_ROOT/s" -prune -o \
        \( -iname '*.psd' -o -iname '*.zip' -o -iname 'README.md' \
           -o -iname 'Dockerfile' -o -iname 'manifest.json' \
           -o -iname 'precache-manifest.*' -o -iname '*.worker.js' \
           -o -iname '.DS_Store' -o -iname 'Thumbs.db' -o -name '._*' \
           -o -iname '__MACOSX' -o -iname 'psd' -o -iname 'check' \) -print 2>/dev/null
}

residue_referenced() {
    # $1 = one residue path. True when a served HTML page or the
    # subscription tree references it. The /s/ merged subscription must never
    # be mistaken for residue, so `--exclude-dir=s` protects it.
    local rel="${1#"$NGINX_ROOT/"}" base
    base="$(basename "$1")"
    # PSD sources, macOS archiver sidecars, thumbnail caches and the builder
    # `check` marker are never served content, so a textual "reference" to
    # them cannot make them live; prose that merely contains the word "check"
    # must not protect the 0-byte marker file.
    case "$1" in
        *.psd|*.PSD|*/.DS_Store|*/Thumbs.db|*/__MACOSX|*/check|*/.|*/._*) return 1 ;;
    esac
    if grep -rIl --exclude-dir=s -- "$rel" "$NGINX_ROOT"/*.html "$NGINX_ROOT"/en 2>/dev/null | head -n1 | grep -q . ; then
        return 0
    fi
    if grep -rIl --exclude-dir=s -- "$base" "$NGINX_ROOT"/*.html "$NGINX_ROOT"/en 2>/dev/null | head -n1 | grep -q . ; then
        return 0
    fi
    return 1
}

residue_list=''
residue_kept=''
# Newline-joined, not space-joined: the removal loop reads entries with
# `while IFS= read -r`, so a hypothetical filename with spaces stays one
# entry instead of being word-split into wrong rm paths.
while IFS= read -r item; do
    [ -n "$item" ] || continue
    if residue_referenced "$item"; then
        residue_kept="${residue_kept}${item#"$NGINX_ROOT/"}"$'\n'
    else
        residue_list="${residue_list}${item#"$NGINX_ROOT/"}"$'\n'
    fi
done < <(residue_find)
echo "residue_removable=${residue_list:-none}"
echo "residue_kept_referenced=${residue_kept:-none}"

# A real corporate site answers an unknown path with its own branded 404, not
# nginx's stock "<center>404 Not Found</center>...nginx" page. That default
# page is a stable active-probe tell: the home page is a polished template, but
# one crafted request drops to a bare nginx error, proving a managed proxy sits
# behind the name. We write a site-consistent 404 and wire it through an
# http-level error_page drop-in (inherited by every vhost serving this root,
# including the vasma-generated subscribe server we must not edit).
if [ -f "$NOT_FOUND_PAGE" ]; then
    echo 'notfound_page=present'
else
    echo 'notfound_page=missing'
fi
if [ -f "$ERROR_CONF" ] && grep -qE 'error_page[[:space:]]+404([[:space:]]+=[0-9]+)?[[:space:]]+/404\.html' "$ERROR_CONF" 2>/dev/null; then
    echo 'errorpage_wired=yes'
else
    echo 'errorpage_wired=no'
fi

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
        # The error_page drop-in lives outside the document root, so it is not
        # covered by the root backup. Restore it if this run overwrote one, or
        # remove it if this run created it.
        if [ -f "$backup_dir/conf/vps-launcher-camouflage-errors.conf" ]; then
            if ! cp -a "$backup_dir/conf/vps-launcher-camouflage-errors.conf" "$ERROR_CONF"; then
                rollback_failed=1
            fi
        elif [ -e "$ERROR_CONF" ]; then
            rm -f -- "$ERROR_CONF" || rollback_failed=1
        fi
        # The dpkg exclusion lives outside the document root as well; restore
        # the exact previous bytes, or remove it if this run created it.
        if [ -f "$backup_dir/dpkg/vps-launcher-camouflage" ]; then
            if ! cp -a "$backup_dir/dpkg/vps-launcher-camouflage" "$DPKG_EXCLUDE_CONF"; then
                rollback_failed=1
            fi
        elif [ -e "$DPKG_EXCLUDE_CONF" ]; then
            rm -f -- "$DPKG_EXCLUDE_CONF" || rollback_failed=1
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
# Back up the error_page drop-in too so a failed apply can restore the exact
# previous bytes (or confirm it did not exist) before rollback removes it.
if [ -f "$ERROR_CONF" ]; then
    if ! mkdir -p "$backup_dir/conf" || ! cp -a "$ERROR_CONF" "$backup_dir/conf/"; then
        echo 'BACKUP_COPY_FAILED' >&2
        rm -rf -- "$backup_dir"
        write_status failed 1
        exit 1
    fi
fi
# Same for the dpkg exclusion file: it lives outside the document root and is
# restored byte-exact (or removed when this run created it) on rollback.
if [ -f "$DPKG_EXCLUDE_CONF" ]; then
    if ! mkdir -p "$backup_dir/dpkg" || ! cp -a "$DPKG_EXCLUDE_CONF" "$backup_dir/dpkg/vps-launcher-camouflage"; then
        echo 'BACKUP_COPY_FAILED' >&2
        rm -rf -- "$backup_dir"
        write_status failed 1
        exit 1
    fi
fi
backup_ready=1

# Project the dpkg exclusion that stops nginx package upgrades from rewriting
# the stock pages (the recurring monthly wipe vector). A dpkg.cfg.d fragment,
# not a conffile, so package operations never overwrite it either.
mkdir -p "$(dirname "$DPKG_EXCLUDE_CONF")"
cat > "$DPKG_EXCLUDE_CONF" <<'DPKGCONF'
# Managed by vps-ssh-launcher scripts/nginx_camouflage_site.ps1.
# Do not edit by hand; re-run the lane instead.
# nginx package upgrades unpack stock pages into /usr/share/nginx/html and
# silently revert the camouflage promotion. Exclude exactly the package-owned
# stock files so upgrades leave the lane-managed root untouched.
path-exclude=/usr/share/nginx/html/index.html
path-exclude=/usr/share/nginx/html/50x.html
path-exclude=/usr/share/nginx/html/index.nginx-debian.html
DPKGCONF
echo "DPKG_EXCLUDE_PROJECTED conf=$DPKG_EXCLUDE_CONF"

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

# Quarantine the template-distribution residue computed above. Deletion is
# undone by the same full-root backup used for every other write in this lane;
# anything referenced by a served page was excluded from residue_list so live
# content is never removed. Only `psd/` is removed as a directory; the rest
# are files/symlinks, and rm -f on a symlink removes the link, not its target.
while IFS= read -r rel; do
    [ -n "$rel" ] || continue
    target="$NGINX_ROOT/$rel"
    # Directory residues (psd/, __MACOSX/) need -r; a file/symlink is removed
    # with -f (a symlink is unlinked, never followed). Test -d first so the
    # backup-restored tree is handled regardless of which kind it is.
    if [ -d "$target" ] && [ ! -L "$target" ]; then
        rm -rf -- "$target" || { echo "RESIDUE_REMOVAL_FAILED file=$rel" >&2; rollback; }
    else
        rm -f -- "$target" || { echo "RESIDUE_REMOVAL_FAILED file=$rel" >&2; rollback; }
    fi
    echo "RESIDUE_REMOVED file=$rel"
done <<<"$residue_list"

if ! cmp -s "$DECOY_DIR/index.html" "$ROOT_INDEX"; then
    if ! cp -a "$DECOY_DIR/." "$NGINX_ROOT/"; then
        echo 'CAMOUFLAGE_COPY_FAILED' >&2
        rollback
    fi
fi

# Branded 404: same corporate identity as the promoted home page (title,
# header links, footer copyright) so an unknown path looks like a normal
# "page moved" response from a real manufacturer site. Self-contained; only
# uses the css/style.css that the decoy promotion already placed at the root.
cat > "$NOT_FOUND_PAGE" <<'HTML404'
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta http-equiv="Content-Type" content="text/html; charset=utf-8" />
<title>Page Not Found - geili Forging</title>
<meta name="keywords" content="geili Forging" />
<meta name="description" content="geili Forging" />
<link type="text/css" href="/css/style.css" rel="stylesheet" />
</head>
<body>
	<div class="container header">
		<a class="f-l logo" href="/index.html"><img src="/images/logo.gif" width="56" height="72" alt="geili Forging" /></a>
		<div class="f-r quickbtn"><a href="/index.html">Home</a><span>|</span><a href="/en/contact.html">Contact Us</a><span>|</span><a href="/en/sitemap.html">Sitemap</a></div>
		<div class="clear"></div>
	</div>
	<div class="container content">
		<div class="blank20"></div>
		<h2>Page Not Found</h2>
		<p>The page you requested could not be found. It may have been moved or is temporarily unavailable.</p>
		<p>Please return to the <a href="/index.html">home page</a> or contact us if you need assistance.</p>
	</div>
	<div class="footer"><div class="container">
		<p>&copy;Copyright Wenzhou geili Forging Co.,Ltd. Address: Ruian City, Zhejiang Province, China Tong Pu Industrial Zone</p>
	</div></div>
</body>
</html>
HTML404
if [ ! -f "$NOT_FOUND_PAGE" ]; then
    echo 'NOTFOUND_PAGE_WRITE_FAILED' >&2
    rollback
fi

# http-level error_page (this file is included inside nginx.conf's http{}),
# so every server that serves this root inherits it without editing the
# vasma-generated subscribe server. `=404` keeps the response status a real
# not-found while serving the branded body; a `location` block is illegal at
# http scope, so no `internal` here (a direct /404.html returning 200 is
# harmless and matches how most real sites expose their error page).
mkdir -p "$NGINX_CONF_DIR/conf.d"
cat > "$ERROR_CONF" <<'ERRCONF'
# Managed by vps-ssh-launcher scripts/nginx_camouflage_site.ps1.
# Do not edit by hand; re-run the lane instead.
# Presented at http scope (included from nginx.conf http{}) so all vhosts that
# serve the camouflage root answer an unknown path with the branded 404 rather
# than nginx's stock error page.
error_page 404 =404 /404.html;
ERRCONF
if [ ! -f "$ERROR_CONF" ]; then
    echo 'ERROR_PAGE_CONF_WRITE_FAILED' >&2
    rollback
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
