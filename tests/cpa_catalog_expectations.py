"""Shared catalog literals for tests that pin the CPA route contract.

These are deliberately independent copies of the expected catalog: deriving
them at assert time from scripts/remote/cpa_provider_routes.json (the source
of truth they check) would make the checks circular. A catalog change updates
this file once, the manifest, and the guardrails oracle -- see
docs/runbooks/cpa-catalog-change-checklist.md -- instead of a dozen fixture
literals.
"""

# ChatGPT Plus OAuth lane aliases in route-manifest order. gpt-6.1-terra
# adopted 2026-10-27 (user directive; morning-same-day tombstone reversed).
# gpt-reserve retired 2026-11-04 (user directive): the OAuth route and the
# gpt-5.6-luna rename are gone, while the bare name stays in
# codex_api_key_exclusions as a tombstone so API-key channels cannot claim it.
OAUTH_ROUTE_ALIASES = [
    "gpt-6-luna",
    "gpt-5.6-luna",
    "gpt-6.1-sol",
    "gpt-6.1-terra",
]

# Antigravity (Google AI Pro) OAuth lane aliases in route-manifest order,
# declared 2026-10-14, restricted to the single 3.8-flash tier the same day
# (user decision: the personal AI Pro credential serves gemini-3.8-flash-high
# only; every other model the antigravity catalog exposes is pinned into the
# provider-level antigravity_oauth_exclusions list). Gated by the
# antigravity-oauth admission lane from day one: cpa_policy.py rejects a
# shared subscription OAuth route that is missing its reviewed lane (ungated
# shared credential = ban-shaped).
ANTIGRAVITY_OAUTH_ROUTE_ALIASES = [
    "gemini-3.8-flash-high",
]

# Antigravity-exposed models retired from the lane on 2026-10-14, plus
# claude-haiku-5-5 tombstoned 2026-10-16: the bare name is a Claude-family name
# the antigravity catalog may carry, so the antigravity lane must never
# auto-register it. The name is claimed by the claude-oauth subscription
# lane (2026-10-17) — the exclusion is what
# keeps the antigravity OAuth credential from claiming the same alias. The
# 2026-10-14 set stays in the manifest exclusion list (and in
# codex_api_key_exclusions) as tombstones so neither OAuth nor Codex API-key
# lanes can re-register them. Three more tombstones added 2026-10-17
# (claude-opus-5-5-high, claude-sonnet-5-5-high, gemini-4-argon-high) —
# kept in manifest order; the manifest remains the enforced source of truth.
ANTIGRAVITY_OAUTH_EXCLUSIONS = [
    "gemini-3-flash",
    "gemini-3.1-flash-image",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    # 2026-11-11 tombstone: the upstream catalog re-discovery surfaced the
    # tiered routing alias after the stabilization restart; the lane posture
    # stays flash-high-only (user decision), so the alias is pinned out of
    # the client catalog instead of being admitted past lane admission.
    "gemini-3.8-flash-tiered",
    "claude-haiku-5-5",
    "claude-opus-4-6-thinking",
    "claude-opus-5-5-high",
    "claude-sonnet-4-6",
    "claude-sonnet-5-5-high",
    "gemini-4-argon-high",
    "gpt-oss-120b-medium",
]

# Claude (Anthropic Pro) OAuth lane aliases: declared 2026-10-17, retired
# 2026-10-20 when Anthropic suspended the account (account_on_hold); the
# credential was removed by the operator and the route left the manifest.
# Kept as an empty tombstone so a resurrection without a reviewed re-attach
# transaction fails the catalog and doctor gates.
CLAUDE_OAUTH_ROUTE_ALIASES: list[str] = []

# Every OAuth alias across the routes, manifest order.
ALL_OAUTH_ROUTE_ALIASES = (
    OAUTH_ROUTE_ALIASES + ANTIGRAVITY_OAUTH_ROUTE_ALIASES + CLAUDE_OAUTH_ROUTE_ALIASES
)

# Admission lane models exactly as frozen in scripts/remote/cpa-admission.json.
# deepseek-official retired 2026-10-11 with the api.deepseek.com channel.
# claude-oauth retired 2026-10-20 (see above).
ADMISSION_LANE_MODELS = {
    "workbuddy-oauth": ["deepseek-v4.1-flash", "deepseek-v4.1-flash-sg", "hy4free"],
    "chatgpt-oauth": list(OAUTH_ROUTE_ALIASES),
    "zhipu-coding-plan": ["glm-5.3", "glm-5.3-flash"],
    "antigravity-oauth": list(ANTIGRAVITY_OAUTH_ROUTE_ALIASES),
}

# WorkBuddy plugin pins are realm-specific. Keep this independent from the
# route manifest so a CN/INTL swap is caught instead of silently accepted by
# the catalog union check.
WORKBUDDY_PLUGIN_MODELS = {
    "cn": ["deepseek-v4.1-flash", "hy4free"],
    "intl": ["deepseek-v4.1-flash", "deepseek-v4.1-flash-sg", "hy4free"],
}

# Every model cpa-admission must register across the shared-account lanes.
ADMISSION_MODEL_LANES = frozenset().union(*ADMISSION_LANE_MODELS.values())

# Provider aliases in the order cpa-health builds a generation-all matrix
# after the OAuth block: slot-1 channel aliases, the scheduled-gate baseline,
# then remaining provider aliases in manifest order.
PROVIDER_MATRIX_TAIL = [
    "gpt-6-astra",
    # gpt-6.1-sol-input retired 2026-11-11 (user directive). The bare name
    # stays in oauth_exclusions and codex_api_key_exclusions as a tombstone,
    # so an accidental re-registration fails the readiness catalog gate.
    "glm-5.3-flash",
    # deepseek-v4.1-flash stays OUT of the provider matrix: it rides the
    # workbuddy plugin lane (shared-subscription policy, matrix probes
    # forbidden). It was dual-homed with the opencode slot-2 bare alias
    # until 2026-11-11, when the lapsed OpenCode Go subscription (user
    # decision: not renewed) retired the slot and its deterministic
    # deepseek-v4.1-flash-oc alias from PROVIDER_MATRIX_TAIL; a renewal
    # restores both via the slot-2 revert plus -Apply.
    "gpt-5.6-terra",
    "glm-5.3",
    # codex_api_key_routes follow the provider block in the matrix order.
    # muse-spark-1.3-contributor retired 2026-10-18 with the slot-2 channel
    # (user decision). deepseek-v4.1-flash moved the same day to the
    # workbuddy plugin credential — a shared-subscription-style lane that is
    # not matrix-probed (same policy as the claude/antigravity OAuth lanes).
    # claude_api_key_routes retired 2026-10-17 (zen claude route); the Claude
    # subscription OAuth lane models are not matrix-probed (no scheduled
    # generation probes on shared-subscription lanes).
]
