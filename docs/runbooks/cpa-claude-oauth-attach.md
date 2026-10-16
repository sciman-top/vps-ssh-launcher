# Claude subscription OAuth on BWG CPA: closed

This file is a retired runbook retained as a policy guard. It is **not** an
OAuth login, token import, callback, or route-activation procedure.

## Current decision

- Do not run CLIProxyAPI `--claude-login`, import Claude Code / Free / Pro / Max
  subscription credentials into CPA, or create a Claude subscription OAuth
  route, admission lane, public key, or account probe.
- Keep `oauth.excluded-models.claude: ["claude-*"]` and
  `claude-header-defaults.stabilize-device-profile: false`. The former blocks
  Claude subscription OAuth models; the latter avoids pinning a synthetic or
  unrelated client profile. Neither setting proves account eligibility or
  prevents provider-side enforcement.
- Claude Code and Claude Desktop subscription use stays in the official apps
  with the account's own supported access. Programmatic use must be reviewed
  separately against current official API / authorized-cloud terms and region
  eligibility; a third-party API-key route is a distinct credential path.

## If risk signals appear

Stop new requests to the affected route. Do not retry, clear cooldowns, switch
accounts, rotate IPs, alter User-Agent / device fingerprints, or create a
generation probe to test whether an account is restricted. Preserve the
existing credentials and use the ordinary CPA incident runbook for read-only
triage and any authorized reversible configuration change.

Official references: [Anthropic Supported Regions](https://www.anthropic.com/legal/supported-regions),
[Claude Code legal and compliance](https://code.claude.com/docs/en/legal-and-compliance),
and [CPA Claude account-risk boundary](cpa-claude-account-risk.md).
