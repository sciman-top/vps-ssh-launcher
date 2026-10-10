#requires -Version 7
# Redaction-safe comparison of the Cockpit provider client keys against the CPA
# gateway's accepted api-keys. Prints key ids and truncated SHA-256 only; never
# the key material. Read-only.
param(
  [string]$Profile = "bwg",
  [int]$IdleTimeoutSeconds = 60,
  [int]$HardTimeoutSeconds = 180
)
$ErrorActionPreference = "Stop"
if ($Profile -cne "bwg") { throw "restricted to bwg" }

$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$localProbe = @'
import hashlib, json, pathlib

registry = json.loads(
    (pathlib.Path.home() / ".antigravity_cockpit" / "codex_model_providers.json").read_bytes()
)
for provider in registry:
    if provider.get("name") != "fq.sciman.top":
        continue
    for entry in provider.get("apiKeys", []):
        secret = entry.get("apiKey") or entry.get("key") or ""
        digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()[:12]
        print("LOCAL_KEY", entry.get("id"), "sha12=" + digest, "len=" + str(len(secret)))
'@
$localProbe | & $venvPython -

. (Join-Path $repoRoot "scripts\lib\project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot

$remote = @'
set -Eeuo pipefail
python3 - <<'PY'
import hashlib
import yaml

config = yaml.safe_load(open("/opt/cliproxyapi/config.yaml", encoding="utf-8"))
keys = config.get("api-keys") or []
print("REMOTE_API_KEY_COUNT=" + str(len(keys)))
for index, key in enumerate(keys):
    print(
        "REMOTE_KEY",
        index,
        "sha12=" + hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:12],
        "len=" + str(len(str(key))),
    )
PY
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config `
  -Profile $Profile -Command $remote `
  -IdleTimeoutSeconds $IdleTimeoutSeconds -HardTimeoutSeconds $HardTimeoutSeconds
Write-Host "KEY_ID_PROBE=DONE"
