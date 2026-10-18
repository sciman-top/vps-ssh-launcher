"""Shared catalog literals for tests that pin the CPA route contract.

These are deliberately independent copies of the expected catalog: deriving
them at assert time from scripts/remote/cpa_provider_routes.json (the source
of truth they check) would make the checks circular. A catalog change updates
this file once, the manifest, and the guardrails oracle -- see
docs/runbooks/cpa-catalog-change-checklist.md -- instead of a dozen fixture
literals.
"""

# ChatGPT Plus OAuth lane aliases in route-manifest order.
OAUTH_ROUTE_ALIASES = ["gpt-6-luna", "gpt-5.6-luna", "gpt-6.1-sol"]

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
# lanes can re-register them.
ANTIGRAVITY_OAUTH_EXCLUSIONS = [
    "gemini-3-flash",
    "gemini-3.1-flash-image",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "claude-haiku-5-5",
    "claude-opus-4-6-thinking",
    "claude-sonnet-4-6",
    "gpt-oss-120b-medium",
]

# Claude (Anthropic Pro) OAuth lane aliases in route-manifest order,
# declared 2026-10-17: only the three 5.5-tier names are claimed (user
# decision); every other model the credential exposes is pinned into the
# provider-level claude_oauth_exclusions list. Gated by the claude-oauth
# admission lane from day one (shared subscription invariant).
CLAUDE_OAUTH_ROUTE_ALIASES = [
    "claude-opus-5-5",
    "claude-sonnet-5-5",
    "claude-haiku-5-5",
]

# Every OAuth alias across the three routes, manifest order.
ALL_OAUTH_ROUTE_ALIASES = (
    OAUTH_ROUTE_ALIASES + ANTIGRAVITY_OAUTH_ROUTE_ALIASES + CLAUDE_OAUTH_ROUTE_ALIASES
)

# Admission lane models exactly as frozen in scripts/remote/cpa-admission.json.
# deepseek-official retired 2026-10-11 with the api.deepseek.com channel.
ADMISSION_LANE_MODELS = {
    "workbuddy-oauth": ["deepseek-v4.1-flash"],
    "chatgpt-oauth": list(OAUTH_ROUTE_ALIASES),
    "zhipu-coding-plan": ["glm-5.3", "glm-5.3-flash"],
    "antigravity-oauth": list(ANTIGRAVITY_OAUTH_ROUTE_ALIASES),
    "claude-oauth": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"],
}

# Every model cpa-admission must register across the shared-account lanes.
ADMISSION_MODEL_LANES = frozenset().union(*ADMISSION_LANE_MODELS.values())

# Provider aliases in the order cpa-health builds a generation-all matrix
# after the OAuth block: slot-1 channel aliases, the scheduled-gate baseline,
# then remaining provider aliases in manifest order.
PROVIDER_MATRIX_TAIL = [
    "gpt-6-astra",
    "gpt-6.1-sol-input",
    "glm-5.3-flash",
    "gpt-5.6-terra",
    "glm-5.3",
    # codex_api_key_routes follow the provider block in the matrix order.
    # muse-spark-1.3-contributor retired 2026-10-18 with the opencode.ai zen
    # channel (user decision). deepseek-v4.1-flash moved the same day to the
    # workbuddy plugin credential — a shared-subscription-style lane that is
    # not matrix-probed (same policy as the claude/antigravity OAuth lanes).
    # claude_api_key_routes retired 2026-10-17 (zen claude route); the Claude
    # subscription OAuth lane models are not matrix-probed (no scheduled
    # generation probes on shared-subscription lanes).
]
