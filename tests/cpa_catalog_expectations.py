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
# Tombstoned in oauth_exclusions too on 2026-11-14 (user directive: never
# use): the upstream codex catalog re-advertised gpt-6-sol / gpt-reserve /
# codex-auto-review after the 2026-11-13 drop; all three stay undeclared and
# double-tombstoned so neither OAuth nor Codex API-key lanes can ever claim
# them.
# gpt-6.1-sol / gpt-6.1-terra retired 2026-11-13 after the upstream catalog
# dropped both names (400 model_not_found); restored 2026-11-14 (user
# directive) after a GET /backend-api/codex/models probe served both slugs
# again. Catalog retirement now requires an explicit user directive
# (docs/runbooks/cpa-catalog-change-checklist.md 前置铁律).
OAUTH_ROUTE_ALIASES = [
    "gpt-6-luna",
    "gpt-5.6-luna",
    "gpt-6.1-sol",
    "gpt-6.1-terra",
]

# Antigravity (Google AI Pro) OAuth lane aliases in route-manifest order,
# declared 2026-10-14, restricted to the single 3.8-flash tier the same day,
# widened 2026-11-16 by user decision to three models: gemini-3.8-flash-high
# is served pool-wide by the 4-credential pool, while gemini-4-hydrogen-xhigh
# (upstream registers the xhigh tier; 1M unified window) and
# claude-sonnet-5-5-high (1M window) are available on only one pro
# credential each — single-source, no failover. Gated by the
# antigravity-oauth admission lane from day one: cpa_policy.py rejects a
# shared subscription OAuth route that is missing its reviewed lane (ungated
# shared credential = ban-shaped).
ANTIGRAVITY_OAUTH_ROUTE_ALIASES = [
    "gemini-3.8-flash-high",
    "gemini-4-hydrogen-xhigh",
    "claude-sonnet-5-5-high",
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
# claude-sonnet-5-5-high left the exclusion list 2026-11-16 (user decision):
# it is now a declared antigravity route model, still tombstoned in
# codex_api_key_exclusions so the API-key lane can never claim it.
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
    "workbuddy-oauth": ["deepseek-v4.1-flash"],
    "workbuddy-intl-oauth": [
        "deepseek-v4.1-flash-intl",
        "deepseek-v4.1-flash-sg",
        "hy4flash-free",
    ],
    "chatgpt-oauth": list(OAUTH_ROUTE_ALIASES),
    "zhipu-coding-plan": ["glm-5.3", "glm-5.3-flash"],
    "antigravity-oauth": list(ANTIGRAVITY_OAUTH_ROUTE_ALIASES),
}

# WorkBuddy plugin pins are realm-specific. Keep this independent from the
# route manifest so a CN/INTL swap is caught instead of silently accepted by
# the catalog union check.
WORKBUDDY_PLUGIN_MODELS = {
    # 2026-11-17 luode0320 cutover: pins are the upstream ids each realm's
    # dynamic catalog actually serves. hy4flash-free (x0.00 credits) is the
    # INTL zero-cost fallback and, since the 2026-11-17 user-directed rename,
    # surfaces under its bare upstream id: the interim client alias hy4free
    # named the retired upstream id and was retired with it. The CN pool
    # still carries only the paid primary.
    "cn": ["deepseek-v4.1-flash"],
    "intl": ["deepseek-v4.1-flash-sg", "hy4flash-free"],
}
WORKBUDDY_PLUGIN_ADMISSION_MODELS = {
    "cn": ["deepseek-v4.1-flash"],
    "intl": [
        "deepseek-v4.1-flash-intl",
        "deepseek-v4.1-flash-sg",
        "hy4flash-free",
    ],
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
    # slot-2 OpenCode single-homed alias, re-admitted 2026-11-14 after the
    # OpenCode Go subscription renewal (user directive: opencode exposes
    # ONLY this alias). deepseek-v4.1-flash stays OUT of the provider
    # matrix: it rides the workbuddy plugin lane (shared-subscription
    # policy, matrix probes forbidden; cpa-health.py skips it explicitly).
    "deepseek-v4.1-flash-oc",
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
