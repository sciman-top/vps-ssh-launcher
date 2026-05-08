# 2026-05-09 bwg Xray weekly wrapper fix

## Scope

- Target profile: `bwg`
- Risk level: medium, remote wrapper repair and Xray service restore
- Real SSH triggered: yes
- Secrets: no credentials, private keys, or target config content recorded

## Root Cause

The project-generated weekly Xray wrapper was not equivalent to a careful manual
`vasma` run. It piped:

```text
16
1
1
y
```

into `/usr/bin/vasma`. On 2026-05-08 14:00 UTC, the VPS-local `install.sh`
looked only at the latest five Xray releases. Those entries were prereleases, so
the stable Xray version resolved to an empty string. The wrapper's pre-supplied
`y` confirmed the empty-version update path, causing the existing Xray binary to
be removed before the missing archive could be unpacked.

Observed failure markers:

```text
最新版本:
Xray-core版本:
unzip: cannot find or open /etc/v2ray-agent/xray/Xray-linux-64.zip
chmod: cannot access '/etc/v2ray-agent/xray/xray': No such file or directory
Xray启动失败
xray.service: Main process exited, status=203/EXEC
```

## Changes

- Updated `scripts/vasma_kernel_update_cron.ps1` so generated Xray wrappers:
  - query the same stable-release window that the current `vasma` script can see;
  - skip updates when that stable version is empty;
  - skip same-version reinstalls;
  - still verify the current Xray service and config when skipping.
- Updated `test_scripts.py` to assert the safe preflight markers.
- Updated `README.md` with the no-empty-version and no-same-version-reinstall
  boundary.
- Re-applied the fixed wrapper to `bwg`.
- Restored `/etc/v2ray-agent/xray/xray` from
  `/etc/v2ray-agent/xray/xray.backup-before-vasma-mHS`.

## Commands

```powershell
python .\ssh_tool.py --config "$env:APPDATA\vps-ssh-launcher\target.json" --profile bwg run --command "<read-only diagnosis>" --command-timeout 180
```

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\vasma_kernel_update_cron.ps1 -Profile bwg -Kernel xray -Apply
```

```powershell
python .\ssh_tool.py --config "$env:APPDATA\vps-ssh-launcher\target.json" --profile bwg run --command "<restore xray binary and restart service>" --command-timeout 180
```

```powershell
python .\ssh_tool.py --config "$env:APPDATA\vps-ssh-launcher\target.json" --profile bwg run --command "/bin/bash /etc/v2ray-agent/auto_update_xray.sh; <post-check>" --command-timeout 180
```

## Verification

Local regression:

```text
python -m pytest -q test_scripts.py -k "vasma or powershell_scripts_parse"
2 passed, 16 deselected, 5 subtests passed
```

Remote restore:

```text
Xray 26.3.27
Configuration OK.
xray=active
nginx=active
listeners: *:443, *:15374, *:22835, 127.0.0.1:45987
```

Fixed wrapper manual proof:

```text
WARN: vasma-visible stable Xray version is empty; skip update to avoid empty download URL
Configuration OK.
========== vasma Xray-core update skipped ==========
xray=active
config-ok
```

## Rollback

Repo-side rollback:

```powershell
git restore README.md scripts\vasma_kernel_update_cron.ps1 test_scripts.py docs\change-evidence\20260509-bwg-xray-weekly-wrapper-fix.md
```

Remote wrapper rollback should not be used unless explicitly required, because
the previous wrapper can reproduce the empty-version destructive path. If needed,
remove only the weekly Xray cron entry and wrapper:

```bash
tmp="$(mktemp)"
crontab -l 2>/dev/null | grep -v -E 'auto_update_xray\.sh' > "$tmp" || true
crontab "$tmp"
rm -f "$tmp" /etc/v2ray-agent/auto_update_xray.sh
systemctl restart xray nginx
```
