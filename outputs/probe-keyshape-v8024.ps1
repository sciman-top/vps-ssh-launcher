#requires -Version 7
param([string]$Profile = "bwg")
$ErrorActionPreference = "Stop"
if ($Profile -cne "bwg") { throw "restricted to bwg" }
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts\lib\project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$remote = @'
set -Eeuo pipefail
python3 - <<'PYK'
import yaml
config = yaml.safe_load(open("/opt/cliproxyapi/config.yaml"))
oc = config["api-keys"]["openai-compatibility"]
for entry in oc:
    name = entry.get("name")
    shape = {k: type(v).__name__ for k, v in entry.items()}
    print("ENTRY", name, shape)
    if "ai.input.im" in name or "ai.input.im" in str(entry.get("base-url", "")):
        ake = entry.get("api-key-entries")
        if ake:
            print("  api-key-entries[0] keys:", sorted(ake[0].keys()), "api-key len:", len(str(ake[0].get("api-key", ""))))
PYK
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $remote
Write-Host "KEYSHAPE_DONE"
