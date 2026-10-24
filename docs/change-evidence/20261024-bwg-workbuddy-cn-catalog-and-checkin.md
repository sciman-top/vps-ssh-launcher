# BWG WorkBuddy CN catalog pin and check-in policy (2026-10-24)

## Scope

The user authorized continuous WorkBuddy CN/INTL optimization while retaining
every OAuth account and API-key channel. This change does not create, delete,
rotate, disable, import, or export any credential. It does not add generation
retries, increase request concurrency, clear cooldowns, or switch realm after
an error.

## Applied configuration

The existing CN account's plugin model pin was set to
`deepseek-v4.1-flash`, matching the approved INTL pin. CPA's public catalog
therefore contains only the approved ten IDs and no dynamic CN discovery
spillover. `scheduler_mode=selected` remains in force, so a selected
credential that is unavailable or cooling is rejected rather than being
silently replaced. Because both realms now advertise the same model ID, that
single public ID does not let a client choose CN versus INTL; it must not be
treated as realm-specific routing.

`checkin_auto=true` is enabled for CN accounts. The plugin's in-container
clock is CST (UTC+08:00), so it attempts the idempotent CN check-in at 09:00
and 21:00 China time, and refreshes the account credit cache after a
successful check-in. This is the only automatic account activity enabled.

The following remain explicitly disabled:

| Setting | Reason |
| --- | --- |
| `tasks_auto` | Its daily loop emits multi-client task events, can run short model chats, and may accept, claim, travel, redeem, or lottery tasks. It is not needed for the independent check-in credit grant. |
| `lifecycle_auto` | It can disable an exhausted CN auth and delete an exhausted Global auth, conflicting with the requirement to preserve every channel. |
| `token_keepalive` | It makes a daily refresh-token request for every WorkBuddy auth and marks an auth disabled on offline-session error 12153. It has no demonstrated credit or latency benefit. |

`stream_head_timeout=60` remains unchanged. It converts a pre-answer upstream
failure into a status-bearing response. Lowering it would only shorten the
silent-upstream wait; it cannot improve normal first-token latency and would
reduce the period in which a delayed pre-answer failure is classified cleanly.

## Transaction and verification

Each configuration mutation was made after copying `config.yaml`, followed by
the repository `cpa_bwg_guardrails.ps1 -Apply` transaction. That transaction
validated the semantic policy and compose configuration, restarted CPA, read
the authenticated model catalog, restarted admission, and verified projected
files. Its backup directories are retained under `/root/cpa-guardrails-backup-*`
with the normal keep-eight policy.

Post-apply result: `READY_STATUS=200`, `HEALTH_OK`, and a ten-model catalog
containing `deepseek-v4.1-flash` with no unapproved dynamic models. A later
strict doctor read showed the WorkBuddy lane `OPEN`; an unrelated pre-existing
`chatgpt-oauth` `unverified_request` state made the aggregate strict doctor
fail. That state was left intact and no OAuth replay or risk-lock clearance was
performed.

No CN generation request was issued for this change. The next scheduled
check-in is the first natural observation of the enabled job; its success and
any credit change are not yet claimed.
