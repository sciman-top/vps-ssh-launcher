# Sync Codex model context settings to another Windows computer

This runbook installs the versioned context overrides in
`config/codex-model-context-overrides.json` while preserving the target
computer's current Codex model definitions and all unrelated `config.toml`
settings. It does not copy API keys or the full machine-specific model catalog.

## Deploy

1. Pull the current repository revision on the target computer.
2. Confirm Codex CLI is installed and `CODEX_HOME\config.toml` exists.
3. Preview the result:

   ```powershell
   pwsh -NoProfile -File scripts/sync-codex-model-context.ps1 -WhatIf
   ```

4. Apply it after reviewing the printed model list:

   ```powershell
   pwsh -NoProfile -File scripts/sync-codex-model-context.ps1
   ```

The script creates timestamped backups beside `config.toml` and the generated
catalog when those files already exist. It updates only the top-level
`model_catalog_json` setting and the three context fields for each model listed
in the manifest. If a required model is missing or duplicated, it stops before
writing. After a successful readback, restart ChatGPT Desktop to load the new
catalog.

## Roll back

Restore the matching `*.before-codex-model-context-*.bak` files from the
Codex home directory. If there was no previous generated catalog, remove the
new `fq-model-context-catalog.json` after restoring `config.toml`.

The context values describe client metadata and compaction thresholds. They do
not prove that a provider accepts a request at the full advertised token
limit.
