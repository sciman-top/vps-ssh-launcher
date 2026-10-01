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

# Admission lane models exactly as frozen in scripts/remote/cpa-admission.json.
ADMISSION_LANE_MODELS = {
    "chatgpt-oauth": list(OAUTH_ROUTE_ALIASES),
    "zhipu-coding-plan": ["glm-5.3", "glm-5.3-flash"],
    "deepseek-official": ["deepseek-flash"],
}

# Every model cpa-admission must register across the three shared-account lanes.
ADMISSION_MODEL_LANES = frozenset().union(*ADMISSION_LANE_MODELS.values())

# Provider aliases in the order cpa-health builds a generation-all matrix
# after the OAuth block: slot-1 channel aliases, the two scheduled-gate
# baselines, then remaining provider aliases in manifest order.
PROVIDER_MATRIX_TAIL = [
    "gpt-6-astra",
    "deepseek-v4.1-flash",
    "gpt-6.1-sol-input",
    "glm-5.3-flash",
    "deepseek-flash",
    "gpt-6-astra-cii",
    "gpt-6.1-sol-91",
    "glm-5.3",
]
