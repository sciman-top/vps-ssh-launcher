#requires -Version 7
# Full disposable acceptance for the CPA v8.0.58 generation.
# Uses a no-credential, no-default-network mount namespace and the real
# production helper/updater with a fixture-local synthetic upstream.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot

function Get-Base64Text {
  param([Parameter(Mandatory = $true)][string]$Path)
  $text = (Get-Content -LiteralPath $Path -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
  return [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($text))
}

$acceptanceB64 = Get-Base64Text (Join-Path $repoRoot 'scripts/remote/cpa-acceptance.py')
$updateAcceptanceB64 = Get-Base64Text (Join-Path $repoRoot 'scripts/remote/cpa-update-acceptance.py')
$command = @"
set -Eeuo pipefail
umask 077
IMAGE='eceasy/cli-proxy-api:v8.0.58@sha256:060f352248b413d2edac58d816aa5437972ce764a07250d2b9aef82db549cb6e'
V=`$(mktemp -d /root/cpa-v8058-accept-XXXXXXXX)
cleanup() {
  rc=`$?
  if [ "`$rc" -eq 0 ]; then
    real=`$(realpath "`$V")
    case "`$real" in
      /root/cpa-v8058-accept-*) ;;
      *) echo "REFUSE cleanup target=`$real"; exit 31 ;;
    esac
    test -f "`$real/FIXTURE_ONLY"
    rm -rf -- "`$real"
    echo FIXTURE_CLEANED=YES
  else
    echo "FIXTURE_RETAINED=`$V"
  fi
  exit "`$rc"
}
trap cleanup EXIT
mkdir -p "`$V/tmp" "`$V/run" "`$V/state" "`$V/auth"
touch "`$V/FIXTURE_ONLY"
cid=`$(docker create "`$IMAGE")
docker cp "`$cid:/CLIProxyAPI/CLIProxyAPI" "`$V/CLIProxyAPI"
docker rm "`$cid" >/dev/null
chmod +x "`$V/CLIProxyAPI"
cp -a /opt/cliproxyapi/cpa-health.py "`$V/cpa-health.py"
cp -a /opt/cliproxyapi/cpa_provider_routes.json "`$V/cpa_provider_routes.json"
cp -a /opt/cliproxyapi/auto-update.sh "`$V/auto-update.sh"
printf '%s' '$acceptanceB64' | base64 -d >"`$V/cpa-acceptance.py"
printf '%s' '$updateAcceptanceB64' | base64 -d >"`$V/cpa-update-acceptance.py"
chmod 600 "`$V/cpa-acceptance.py" "`$V/cpa-update-acceptance.py"
sha256sum "`$V/CLIProxyAPI" "`$V/cpa-health.py" "`$V/cpa_provider_routes.json" "`$V/auto-update.sh" "`$V/cpa-acceptance.py" "`$V/cpa-update-acceptance.py"
unshare --mount --net --fork bash -c '
set -Eeuo pipefail
mount --make-rprivate /
V='"`$V"'
mount --bind "`$V" /opt/cliproxyapi
mount --bind "`$V/tmp" /tmp
mount --bind "`$V/run" /run
mount --bind "`$V/state" /var/lib/vps-ssh-launcher
ip link set lo up
test -z "`$(ip route show default)"
cd /opt/cliproxyapi
python3 cpa-acceptance.py
' >"`$V/acceptance.log" 2>&1 || {
  tail -60 "`$V/acceptance.log"
  exit 1
}
grep -F 'ACCEPTANCE_RESULT=PASS' "`$V/acceptance.log"
grep -E '^\{"scenario"|^\{".*scenario|^\{".*overload|^\{".*cooldown|^\{".*recovered|^\{".*actual_health|ACCEPTANCE_RESULT' "`$V/acceptance.log" | tail -20
tail -20 "`$V/acceptance.log"
"@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 600 -HardTimeoutSeconds 1800
