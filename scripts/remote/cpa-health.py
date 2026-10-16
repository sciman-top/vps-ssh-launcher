"""Bounded CPA readiness/generation check; never print response bodies or keys."""

import json
import sys
import time
import urllib.error
import urllib.request
import os
import uuid
from pathlib import Path
from urllib.parse import urlparse


TRANSIENT_HTTP_CODES = {
    408,
    429,
    500,
    502,
    503,
    504,
    520,
    521,
    522,
    523,
    524,
    525,
    526,
}
PROBE_LOCK_MODES = frozenset(
    {
        "generation",
        "generation-all",
        "quality-canary",
        "quality-eval",
        "cache-canary",
        "relay-soft",
    }
)


class UpstreamProtocolError(ValueError):
    """The upstream answered, but not with the JSON contract CPA requires."""


_CACHE_CANARY_PREFIX = "\n".join(
    "CPA cache canary invariant: preserve this exact public, non-sensitive sentence."
    for _ in range(256)
)

# The canary spends real provider quota, so its model set stays pinned to the
# non-OAuth lanes -- the shared Plus account remains probe-exempt by default
# (the same discipline as the generation matrix), and an unknown or OAuth name
# must fail locally instead of consuming a lane that was never reviewed for it.
_CACHE_CANARY_MODELS = frozenset({"glm-5.3", "glm-5.3-flash"})
_CACHE_CANARY_DEFAULT_MODEL = "glm-5.3-flash"

_QUALITY_EVAL_CASES = (
    {
        "id": "reasoning-json-v1",
        "messages": [
            {
                "role": "user",
                "content": (
                    'Return only JSON with keys "result" and "label". '
                    "Compute (19 * 23) + 7, set label to reasoning, and do not add fields."
                ),
            }
        ],
        "expected": {"result": 444, "label": "reasoning"},
    },
    {
        "id": "instruction-json-v1",
        "messages": [
            {
                "role": "user",
                "content": (
                    'Return only JSON with keys "first" and "count". '
                    "Set first to the first Greek letter and count to the number of letters in alpha."
                ),
            }
        ],
        "expected": {"first": "alpha", "count": 5},
    },
    {
        "id": "tool-structure-v1",
        "messages": [
            {
                "role": "user",
                "content": "Use the lookup tool exactly once for id canary-42.",
            }
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "description": "Return a test record by id.",
                    "parameters": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}},
                        "required": ["id"],
                        "additionalProperties": False,
                    },
                },
            }
        ],
        "tool_choice": {"type": "function", "function": {"name": "lookup"}},
        "expected_tool": "lookup",
        "expected_arguments": {"id": "canary-42"},
    },
    {
        "id": "long-context-json-v1",
        "messages": [
            {
                "role": "system",
                "content": "\n".join(
                    f"record-{index:03d}=value-{index:03d}" for index in range(1, 161)
                ),
            },
            {
                "role": "user",
                "content": (
                    'Return only JSON with keys "record" and "value". '
                    "Read the supplied records and return the value for record-127."
                ),
            },
        ],
        "expected": {"record": "record-127", "value": "value-127"},
    },
)


# Thinking-mode upstreams reject a FORCED tool_choice outright (2026-09-19
# deepseek-flash 400 "Thinking mode does not support this tool_choice") while
# still supporting model-chosen tool calls. For these models the case drops
# the forced selection and keeps asserting the well-formed tool call itself.
_FORCED_TOOL_CHOICE_UNSUPPORTED = frozenset({"deepseek-flash"})


def _parse_quality_payload(content):
    """Accept raw JSON or one Markdown JSON fence without accepting prose."""
    if not isinstance(content, str):
        return None
    candidate = content.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if len(lines) < 3 or lines[0].strip().lower() not in ("```", "```json"):
            return None
        if lines[-1].strip() != "```":
            return None
        candidate = "\n".join(lines[1:-1]).strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def _responses_output_text(data):
    """Extract plain text from a non-streaming Responses API result."""
    if not isinstance(data, dict):
        return None
    output_text = data.get("output_text")
    if isinstance(output_text, str):
        return output_text.strip()
    output = data.get("output")
    if not isinstance(output, list):
        return None
    chunks = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if (
                isinstance(part, dict)
                and part.get("type")
                in {
                    "output_text",
                    "text",
                }
                and isinstance(part.get("text"), str)
            ):
                chunks.append(part["text"])
    return "".join(chunks).strip() if chunks else None


def _responses_function_call(data):
    """Return the first Responses function-call item, if present."""
    if not isinstance(data, dict) or not isinstance(data.get("output"), list):
        return None
    for item in data["output"]:
        if isinstance(item, dict) and item.get("type") == "function_call":
            return item
    return None


def _responses_case_body(model, case):
    """Translate the small quality cases to the Responses request shape."""
    body = {
        "model": model,
        "input": case["messages"],
        "max_output_tokens": 1024,
    }
    tools = case.get("tools")
    if tools:
        body["tools"] = [
            {
                "type": "function",
                "name": tool["function"]["name"],
                "description": tool["function"].get("description", ""),
                "parameters": tool["function"].get("parameters", {}),
            }
            for tool in tools
        ]
        if "tool_choice" in case:
            body["tool_choice"] = {
                "type": "function",
                "name": case["tool_choice"]["function"]["name"],
            }
    return body


def _claude_probe_headers(config):
    """Anthropic-native probe headers.

    The client key rides both auth shapes: CPA's config access accepts the
    Anthropic header as well as Bearer, and a fixture without access keys
    (test harnesses build the request mock themselves) degrades to the
    version header alone instead of raising.
    """
    headers = {"anthropic-version": "2023-06-01"}
    try:
        client_key = _client_api_key(config)
    except (AttributeError, KeyError, TypeError):
        client_key = None
    if client_key:
        headers["x-api-key"] = client_key
    return headers


def _claude_case_body(model, case):
    """Translate the small quality cases to the Anthropic Messages shape."""
    system_chunks = [
        str(message.get("content", ""))
        for message in case["messages"]
        if isinstance(message, dict) and message.get("role") == "system"
    ]
    body = {
        "model": model,
        "max_tokens": 1024,
        "messages": [
            message
            for message in case["messages"]
            if not (isinstance(message, dict) and message.get("role") == "system")
        ],
    }
    if system_chunks:
        body["system"] = "\n".join(system_chunks)
    tools = case.get("tools")
    if tools:
        body["tools"] = [
            {
                "name": tool["function"]["name"],
                "description": tool["function"].get("description", ""),
                "input_schema": tool["function"].get("parameters", {"type": "object"}),
            }
            for tool in tools
        ]
        choice = case.get("tool_choice")
        if isinstance(choice, dict) and isinstance(choice.get("function"), dict):
            body["tool_choice"] = {
                "type": "tool",
                "name": choice["function"]["name"],
            }
    return body


def _anthropic_message_text(data):
    """Return the concatenated text blocks of an Anthropic Messages result."""
    if not isinstance(data, dict) or not isinstance(data.get("content"), list):
        return None
    chunks = [
        block["text"]
        for block in data["content"]
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ]
    return "".join(chunks).strip() if chunks else None


def _anthropic_tool_use(data):
    """Return the first Anthropic tool_use block, if present."""
    if not isinstance(data, dict) or not isinstance(data.get("content"), list):
        return None
    for block in data["content"]:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            return block
    return None


def _local_opener() -> urllib.request.OpenerDirector:
    """Keep loopback health traffic off ambient HTTP(S)_PROXY settings."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _client_api_key(config):
    """Client key from the v8 file layout (access.api-keys)."""
    access = config.get("access") if isinstance(config, dict) else None
    keys = access.get("api-keys") if isinstance(access, dict) else None
    if isinstance(keys, list) and keys and isinstance(keys[0], str) and keys[0]:
        return keys[0]
    raise KeyError("access.api-keys")


def _loopback_request(config):
    def request(path, body=None, headers=None):
        request_headers = {
            "Authorization": "Bearer " + _client_api_key(config),
            "Content-Type": "application/json",
        }
        if headers:
            request_headers.update(headers)
        req = urllib.request.Request(
            "http://127.0.0.1:8317/v1/" + path,
            data=json.dumps(body).encode() if body else None,
            headers=request_headers,
        )
        with _local_opener().open(req, timeout=120 if body else 5) as response:
            try:
                return json.load(response)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                # A 2xx status is not enough for this OpenAI-compatible data
                # plane. Do not let malformed upstream output look like a
                # local contract failure or trigger a retry.
                raise UpstreamProtocolError(
                    "upstream returned a non-JSON response"
                ) from exc

    return request


def _nonnegative_int(value):
    return value if type(value) is int and value >= 0 else None


def _first_metric(mappings, names):
    for mapping in mappings:
        if not isinstance(mapping, dict):
            continue
        for name in names:
            value = _nonnegative_int(mapping.get(name))
            if value is not None:
                return value
    return None


def _cache_metrics(data):
    usage = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        return None
    prompt_details = usage.get("prompt_tokens_details")
    input_details = usage.get("input_tokens_details")
    mappings = (usage, prompt_details, input_details)
    metrics = {
        "input_tokens": _first_metric(mappings, ("prompt_tokens", "input_tokens")),
        "cache_read_tokens": _first_metric(
            mappings,
            (
                "prompt_cache_hit_tokens",
                "cache_read_tokens",
                "cached_tokens",
            ),
        ),
        "cache_write_tokens": _first_metric(
            mappings,
            ("cache_creation_tokens", "cache_write_tokens"),
        ),
        "cache_miss_tokens": _first_metric(
            mappings,
            ("prompt_cache_miss_tokens",),
        ),
    }
    return metrics if any(value is not None for value in metrics.values()) else None


def _format_cache_metrics(sample, metrics, model=None):
    fields = ["CACHE_CANARY", f"sample={sample}"]
    if model is not None:
        fields.append(f"model={model}")
    for name in (
        "input_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "cache_miss_tokens",
    ):
        value = metrics.get(name)
        fields.append(f"{name}={value if value is not None else 'unknown'}")
    input_tokens = metrics.get("input_tokens")
    cache_read_tokens = metrics.get("cache_read_tokens")
    if input_tokens and cache_read_tokens is not None:
        fields.append(f"hit_ratio={cache_read_tokens / input_tokens:.4f}")
    else:
        fields.append("hit_ratio=unknown")
    return " ".join(fields)


try:
    _ROUTE_MANIFEST = json.loads(
        Path(__file__).with_name("cpa_provider_routes.json").read_text(encoding="utf-8")
    )
    _ROUTE_MANIFEST_ERROR = None
except Exception as exc:
    _ROUTE_MANIFEST = {}
    _ROUTE_MANIFEST_ERROR = type(exc).__name__
if (
    not isinstance(_ROUTE_MANIFEST, dict)
    or type(_ROUTE_MANIFEST.get("version")) is not int
    or _ROUTE_MANIFEST.get("version") != 1
    or not isinstance(_ROUTE_MANIFEST.get("providers"), list)
    or not isinstance(_ROUTE_MANIFEST.get("oauth_routes"), list)
):
    _ROUTE_MANIFEST_ERROR = _ROUTE_MANIFEST_ERROR or "InvalidManifest"
_PROVIDERS = (
    _ROUTE_MANIFEST.get("providers", []) if isinstance(_ROUTE_MANIFEST, dict) else []
)
_OAUTH_ROUTES = (
    _ROUTE_MANIFEST.get("oauth_routes", []) if isinstance(_ROUTE_MANIFEST, dict) else []
)
_CODEX_ROUTES = (
    _ROUTE_MANIFEST.get("codex_api_key_routes", [])
    if isinstance(_ROUTE_MANIFEST, dict)
    else []
)
_CLAUDE_ROUTES = (
    _ROUTE_MANIFEST.get("claude_api_key_routes", [])
    if isinstance(_ROUTE_MANIFEST, dict)
    else []
)
_CODEX_MODEL_ALIASES = frozenset(
    model["alias"]
    for route in _CODEX_ROUTES
    if isinstance(route, dict)
    for model in route.get("models", [])
    if isinstance(model, dict) and isinstance(model.get("alias"), str)
)
# Claude-family routes speak native Anthropic Messages on the upstream side,
# so their generation probes use /v1/messages (not chat/completions, which
# reaches this family only through a translation layer).
_CLAUDE_MODEL_ALIASES = frozenset(
    model["alias"]
    for route in _CLAUDE_ROUTES
    if isinstance(route, dict)
    for model in route.get("models", [])
    if isinstance(model, dict) and isinstance(model.get("alias"), str)
)
# The managed codex-family upstream rejects the raw Codex desktop tool
# declarations (2026-10-11): tool_search with `required` omitting the
# optional `limit`, web_search with `search_content_types`, and `custom`
# freeform tools. Gateway requests.payload rules rewrite exactly these
# shapes (pinned by cpa_policy), so every codex-route generation probe
# carries them: if the rules ever disappear, health fails instead of
# letting desktop traffic regress to the strict-schema 400s.
_DESKTOP_TOOL_SHAPES = (
    {
        "type": "tool_search",
        "execution": "client",
        "description": "Health probe: deferred tool discovery declaration.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "number",
                    "description": "Maximum number of tools to return.",
                },
                "query": {
                    "type": "string",
                    "description": "Search query for deferred tools.",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "type": "web_search",
        "external_web_access": True,
        "search_content_types": ["text", "image"],
    },
    {
        "type": "custom",
        "name": "apply_patch",
        "description": "Health probe: freeform grammar tool declaration.",
        "format": {
            "type": "grammar",
            "syntax": "lark",
            "definition": "start: WORD\nWORD: /\\w+/",
        },
    },
)
# Claude Code 2.1.300 sends three fields the zen upstream does not declare —
# a top-level `safeguards` classifier block, a top-level
# `context_management` block, and `output_config` (the effort control, which
# rides on the system message) — each answered with 400 "Extra inputs are not
# permitted". The gateway strips exactly those (pinned in cpa_policy), so
# every claude-route generation probe carries them: if a rule ever
# disappears, health fails instead of letting full Claude Code sessions
# regress to the raw 400s.
_CLAUDE_CONTEXT_MANAGEMENT_SHAPE = {
    "edits": [{"type": "clear_tool_uses_20250919"}],
}
_CLAUDE_OUTPUT_CONFIG_SHAPE = {"effort": "medium"}
_CLAUDE_SAFEGUARD_SHAPES = (
    {
        "type": "dangerous_tool_use",
        "classifier_context": {
            "v": 1,
            "permission_mode": "auto",
            "platform": "linux",
            "probe": "cpa-health",
        },
    },
)
# The pinned claude-route payload rules carry two behavioral regression
# probes here (2026-10-16 Codex Desktop outage): a replayed thinking block
# whose signature no longer binds to this conversation only passes when the
# override injected `thinking.block_binding.prefix_mismatch_behavior` and
# the `betas` key (which the executor converts into the anthropic-beta
# header), and a trailing assistant message only passes when the
# conditional `messages.-1` filter deleted it. Both fail with the measured
# 400s if a rule disappears or the executor stops applying payload rules,
# which the structural policy pin cannot see.
_CLAUDE_RULE_PROBE_SIGNATURE = (
    "cHJvYmUtc3RhbGUtdGhpbmtpbmctc2lnbmF0dXJlLTIwMjYxMDE2LWRyb3BfYmxvY2s"
)
_CLAUDE_RULE_PROBE_CASES = ("binding", "prefill")


def _claude_payload_rule_cases(model):
    """Rule-regression request shapes for one claude-route model."""
    return (
        (
            "binding",
            {
                "model": model,
                "max_tokens": 64,
                # thinking.type must be present: the override's exist gate
                # only injects the escape hatch into thinking-bearing
                # requests (a bare injected thinking object would otherwise
                # 400 "thinking.type: Field required"), and translated Codex
                # requests always carry adaptive thinking.
                "thinking": {"type": "adaptive", "display": "summarized"},
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Health probe: respond briefly."}
                        ],
                    },
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "thinking",
                                "thinking": "stale probe reasoning block",
                                "signature": _CLAUDE_RULE_PROBE_SIGNATURE,
                            },
                            {"type": "text", "text": "Understood."},
                        ],
                    },
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": "Reply with exactly: OK"}],
                    },
                ],
            },
        ),
        (
            "prefill",
            {
                "model": model,
                "max_tokens": 64,
                "messages": [
                    {
                        "role": "user",
                        "content": "Reply with exactly: OK",
                    },
                    {
                        "role": "assistant",
                        "content": "ack",
                    },
                ],
            },
        ),
    )


def _claude_payload_rule_eval(request, generation_targets, expected_models, config):
    """Probe the pinned claude-route payload rules behaviorally.

    Returns 0 when every case answers end_turn/OK, 10 on upstream/transport
    trouble, and 20 on a contract failure (a 400 here means a pinned rule is
    gone or inert -- the exact regression this net exists to catch).
    """
    targets = [model for model in generation_targets if model in _CLAUDE_MODEL_ALIASES]
    if not targets:
        return 0
    result = 0
    try:
        for model in targets:
            for _, body in _claude_payload_rule_cases(model):
                data = request("messages", body, _claude_probe_headers(config))
                text = _anthropic_message_text(data)
                if (
                    data.get("error")
                    or data.get("stop_reason") != "end_turn"
                    or data.get("model") not in expected_models[model]
                    or text != "OK"
                ):
                    result = max(result, 10 if data.get("error") else 20)
        return result
    except UpstreamProtocolError:
        return 10
    except urllib.error.HTTPError as error:
        return (
            10
            if error.code == 403
            or error.code in TRANSIENT_HTTP_CODES
            or 500 <= error.code < 600
            else 20
        )
    except (urllib.error.URLError, TimeoutError):
        return 10
    except Exception:
        return 20


_CHANNEL_ROUTE = next(
    (
        provider
        for provider in _PROVIDERS
        if isinstance(provider, dict) and provider.get("host") == "ai.input.im"
    ),
    {},
)
_CHANNEL_MODELS = tuple(
    model.get("alias")
    for model in _CHANNEL_ROUTE.get("models", [])
    if isinstance(model, dict) and isinstance(model.get("alias"), str)
)
# Aliased channel routes relay the upstream body verbatim, so the responded
# model is the upstream name (e.g. gpt-6.1-sol for alias gpt-6.1-sol-input), not
# the client-facing alias. Mirror the generation contract's name-based check.
_CHANNEL_ECHO_MODEL = {
    model["alias"]: model["name"]
    for model in _CHANNEL_ROUTE.get("models", [])
    if isinstance(model, dict)
    and isinstance(model.get("alias"), str)
    and isinstance(model.get("name"), str)
}
_OAUTH_ROUTE_MODELS = tuple(
    (model["name"], model["alias"])
    for route in _OAUTH_ROUTES
    if isinstance(route, dict)
    for model in route.get("models", [])
    if isinstance(model, dict)
    and isinstance(model.get("name"), str)
    and isinstance(model.get("alias"), str)
)
_BASE_ALLOWED_MODELS = (
    {
        model["alias"]
        for provider in _PROVIDERS
        if isinstance(provider, dict) and provider.get("host") != "ai.input.im"
        for model in provider.get("models", [])
        if isinstance(model, dict)
        if isinstance(model.get("alias"), str)
    }
    | {alias for _, alias in _OAUTH_ROUTE_MODELS}
    | {
        model["alias"]
        for route in _CODEX_ROUTES
        if isinstance(route, dict)
        for model in route.get("models", [])
        if isinstance(model, dict) and isinstance(model.get("alias"), str)
    }
    | {
        model["alias"]
        for route in _CLAUDE_ROUTES
        if isinstance(route, dict)
        for model in route.get("models", [])
        if isinstance(model, dict) and isinstance(model.get("alias"), str)
    }
)
_OPTIONAL_PROVIDER_MODELS = frozenset(
    model
    for provider in _PROVIDERS
    if isinstance(provider, dict)
    for model in provider.get("optional_models", [])
    if isinstance(model, str)
)
_OPTIONAL_OAUTH_MODELS = frozenset(alias for _, alias in _OAUTH_ROUTE_MODELS)
_OPTIONAL_CHANNEL_MODELS = frozenset(
    _CHANNEL_ROUTE.get("optional_models", [])
    if isinstance(_CHANNEL_ROUTE.get("optional_models", []), list)
    else []
)
# Image routes are catalog-exposed but never chat-smoked: the generation
# contract (exact "OK", stop, echoed model) is meaningless for them, so the
# matrices skip them and only report catalog presence.
_IMAGE_PROVIDER_MODEL_ALIASES = frozenset(
    model
    for provider in _PROVIDERS
    if isinstance(provider, dict) and isinstance(provider.get("image_models"), list)
    for model in provider["image_models"]
    if isinstance(model, str)
)


def _channel_enabled(config):
    """Return ai.input.im state; absent metadata stays fixture-compatible."""
    api_keys = config.get("api-keys") if isinstance(config, dict) else None
    providers = (
        api_keys.get("openai-compatibility") if isinstance(api_keys, dict) else None
    )
    if not isinstance(providers, list):
        return True
    entries = [
        item
        for item in providers
        if isinstance(item, dict)
        and (
            item.get("name") == "ai.input.im"
            or urlparse(str(item.get("base-url", ""))).hostname == "ai.input.im"
        )
    ]
    if not entries:
        return False
    return entries[0].get("disabled") is not True


def _generation_report_line(model, status, latency_ms, finish=None, error_class=None):
    fields = [
        "GENERATION",
        f"model={model}",
        f"status={status}",
        f"latency_ms={latency_ms}",
    ]
    if finish is not None:
        fields.append(f"finish={finish}")
    if error_class is not None:
        fields.append(f"error_class={error_class}")
    return " ".join(fields)


def _emit_generation_report(
    report, model, started, status, finish=None, error_class=None
):
    if report is not None:
        report(
            _generation_report_line(
                model,
                status,
                int((time.monotonic() - started) * 1000),
                finish=finish,
                error_class=error_class,
            )
        )


def check(config, mode, request=None, sleep=time.sleep, report=None):
    if _ROUTE_MANIFEST_ERROR is not None:
        return 20
    if request is None:
        request = _loopback_request(config)

    # Approved catalog IDs come from the same checked-in manifest as the
    # projector and semantic policy. Only explicit matrix modes generate
    # against every provider; scheduled checks retain one non-OAuth route.
    channel_enabled = _channel_enabled(config)
    # OAuth lane admission is opt-in (2026-09-26 review). The explicit matrices
    # exist to check every route, but the only OAuth credential is the owner's
    # single subscription account, so a default that includes it turns any
    # manual quality run into avoidable account exposure. The default is
    # therefore exclusion; CPA_HEALTH_INCLUDE_OAUTH=1 re-admits the lane for a
    # deliberately reviewed run, and CPA_HEALTH_NO_OAUTH=1 remains a hard
    # suppression that wins over the opt-in. Scheduled paths never touch OAuth.
    oauth_no_oauth_env = os.environ.get("CPA_HEALTH_NO_OAUTH") == "1"
    oauth_include_requested = os.environ.get("CPA_HEALTH_INCLUDE_OAUTH") == "1"
    oauth_lane_suppressed = oauth_no_oauth_env or not oauth_include_requested
    oauth_suppression_reason = (
        "CPA_HEALTH_NO_OAUTH" if oauth_no_oauth_env else "default_opt_out"
    )
    allowed = set(_BASE_ALLOWED_MODELS)
    if channel_enabled:
        allowed.update(_CHANNEL_MODELS)
    optional = set(_OPTIONAL_OAUTH_MODELS)
    if channel_enabled:
        optional.update(_OPTIONAL_CHANNEL_MODELS)
    optional.update(_OPTIONAL_PROVIDER_MODELS)
    required = allowed - optional
    # The legacy relay-soft mode is retained as a compatibility entry point;
    # it now observes ai.input.im only. It is never an update decision.
    # When the provider is disabled, return 13 without a network request.
    # Otherwise the updater logs RELAY_DEGRADED and moves on without deferring
    # or rolling back. Single-shot per model; transient responses are never
    # retried.
    if mode == "relay-soft":
        if not channel_enabled:
            return 13
        relay_models = tuple(
            model for model in _CHANNEL_MODELS if model not in _OPTIONAL_CHANNEL_MODELS
        )
        try:
            catalog = request("models")
            ids = (
                {m["id"] for m in catalog["data"]}
                if isinstance(catalog.get("data"), list)
                else set()
            )
            if not set(relay_models) <= ids:
                return 11
            for model in relay_models:
                data = request(
                    "chat/completions",
                    {
                        "model": model,
                        "messages": [
                            {"role": "user", "content": "Reply with exactly: OK"}
                        ],
                        "max_tokens": 1024,
                    },
                )
                if data.get("error"):
                    return 11
                choice = data["choices"][0]
                if (
                    choice["message"]["content"].strip() != "OK"
                    or choice.get("finish_reason") != "stop"
                    or data.get("model") != _CHANNEL_ECHO_MODEL.get(model, model)
                ):
                    return 11
            return 0
        except Exception:
            return 11
    catalog_complete = False
    # A 200 catalog can lag while credentials finish registering, so allow a
    # short bounded convergence window. Provider-side transient responses are
    # deliberately single-shot: retrying them here turns a rate-limit signal
    # into a health-check amplification loop.
    for attempt in range(3):
        try:
            catalog = request("models")
            if not isinstance(catalog, dict) or not isinstance(
                catalog.get("data"), list
            ):
                return 20
            if not all(
                isinstance(model, dict) and isinstance(model.get("id"), str)
                for model in catalog["data"]
            ):
                return 20
            ids = {model["id"] for model in catalog["data"]}
            # The current BWG contract exposes only explicitly approved model
            # IDs. Unknown aliases are not an alternate namespace; they are
            # unexpected routes and must fail closed.
            if ids - allowed:
                return 20
            # An unopened, preconfigured model may be absent from /models. Keep
            # it out of the required readiness set, while still rejecting any
            # unknown model or prefix.
            if required <= ids and ids <= allowed:
                catalog_complete = True
                break
        except urllib.error.HTTPError as error:
            if error.code not in TRANSIENT_HTTP_CODES and not 500 <= error.code < 600:
                return 20
            return 10
        except UpstreamProtocolError:
            return 10
        except (urllib.error.URLError, TimeoutError):
            # A catalog endpoint that is unreachable is an upstream/readiness
            # problem, not proof that the local model contract is malformed.
            return 10
        except (KeyError, TypeError, ValueError):
            return 20
        if attempt < 2:
            sleep(2)
    if not catalog_complete:
        # Cooling credentials can disappear from /models. A valid, non-
        # exposing catalog proves local readiness, not provider availability.
        return 0 if mode == "readiness" else 10
    if mode == "readiness":
        return 0
    # Keep scheduled maintenance low-frequency and fully off the OAuth
    # pipeline (user decision 2026-09-22, replacing the 2026-09-18 luna
    # default): the owner's ChatGPT Plus slot showed a suspected risk-control
    # signal (cockpit turn-state length 312) plus capacity errors, so
    # scheduled luna generation is paused and the default gate target is
    # glm-5.3-flash, the non-OAuth route selected for the periodic gate. The
    # gate couples to the GLM coding-plan state by design: plan exhaustion
    # surfacing here is a real client-visible signal, not a false alarm. Luna generation
    # remains opt-in through the explicit matrix below (generation-all /
    # quality modes / CPA_HEALTH_ALL_ROUTES). The catalog gate still
    # degrades gracefully: before re-enrollment Luna is absent, so the
    # catalog never completes and generation defers with 10 instead of
    # misreading a provider absence as a local failure. Transient
    # 408/429/5xx are never retried (runbook contract). ai.input.im stays
    # out of the scheduled gate and its Sol/Terra/Astra probes remain
    # opt-in through the explicit matrix below.
    generation_targets = ("glm-5.3-flash",)
    if (
        mode in ("generation-all", "quality-canary", "quality-eval")
        or os.environ.get("CPA_HEALTH_ALL_ROUTES") == "1"
    ):
        matrix_targets: list[str] = []
        for _, alias in _OAUTH_ROUTE_MODELS:
            if oauth_lane_suppressed:
                if report is not None:
                    report(
                        f"ROUTE_PREPARED model={alias} status=skipped "
                        "kind=oauth_lane_suppressed "
                        f"reason={oauth_suppression_reason}"
                    )
                continue
            if alias in ids:
                matrix_targets.append(alias)
            elif report is not None:
                report(
                    f"ROUTE_PREPARED model={alias} status=not_listed oauth=unverified"
                )
        if channel_enabled:
            for alias in _CHANNEL_MODELS:
                if alias in _IMAGE_PROVIDER_MODEL_ALIASES:
                    if alias in ids and report is not None:
                        report(
                            f"ROUTE_PREPARED model={alias} status=skipped kind=image"
                        )
                    continue
                if alias in ids or alias not in _OPTIONAL_CHANNEL_MODELS:
                    matrix_targets.append(alias)
                elif report is not None:
                    report(
                        f"ROUTE_PREPARED model={alias} "
                        "status=not_listed upstream=unverified"
                    )
        matrix_targets.extend(("glm-5.3-flash",))
        for provider in _PROVIDERS:
            if not isinstance(provider, dict) or provider.get("host") == "ai.input.im":
                continue
            for model in provider.get("models", []):
                if not isinstance(model, dict) or not isinstance(
                    model.get("alias"), str
                ):
                    continue
                alias = model["alias"]
                if alias in {"glm-5.3-flash"}:
                    continue
                if alias in _IMAGE_PROVIDER_MODEL_ALIASES:
                    if alias in ids and report is not None:
                        report(
                            f"ROUTE_PREPARED model={alias} status=skipped kind=image"
                        )
                    continue
                if alias in ids:
                    matrix_targets.append(alias)
                elif report is not None:
                    report(
                        f"ROUTE_PREPARED model={alias} "
                        "status=not_listed upstream=unverified"
                    )
        for route in _CODEX_ROUTES:
            if not isinstance(route, dict):
                continue
            for model in route.get("models", []):
                if not isinstance(model, dict) or not isinstance(
                    model.get("alias"), str
                ):
                    continue
                alias = model["alias"]
                if alias in ids:
                    matrix_targets.append(alias)
                elif report is not None:
                    report(
                        f"ROUTE_PREPARED model={alias} "
                        "status=not_listed upstream=unverified"
                    )
        for route in _CLAUDE_ROUTES:
            if not isinstance(route, dict):
                continue
            for model in route.get("models", []):
                if not isinstance(model, dict) or not isinstance(
                    model.get("alias"), str
                ):
                    continue
                alias = model["alias"]
                if alias in ids:
                    matrix_targets.append(alias)
                elif report is not None:
                    report(
                        f"ROUTE_PREPARED model={alias} "
                        "status=not_listed upstream=unverified"
                    )
        generation_targets = tuple(matrix_targets)
    # Bound what one invocation can spend, and make the OAuth admission decision
    # explicit in the output: an operator reading a matrix run should be able to
    # see the planned request budget and whether the subscription lane was
    # touched without inferring it from the absence of a per-route line.
    oauth_lane_state = (
        "included"
        if not oauth_lane_suppressed
        else f"excluded:{oauth_suppression_reason}"
    )
    cases_per_model = len(_QUALITY_EVAL_CASES) if mode == "quality-eval" else 1
    if report is not None:
        report(
            f"PROBE_BUDGET mode={mode} models={len(generation_targets)} "
            f"cases_per_model={cases_per_model} "
            f"planned_generation_requests={len(generation_targets) * cases_per_model} "
            f"oauth_lane={oauth_lane_state}"
        )
    expected_models: dict[str, set[str]] = {}
    for model_name, alias in _OAUTH_ROUTE_MODELS:
        expected_models[alias] = {model_name}
    for provider in _PROVIDERS:
        if not isinstance(provider, dict):
            continue
        for model in provider.get("models", []):
            if (
                isinstance(model, dict)
                and isinstance(model.get("name"), str)
                and isinstance(model.get("alias"), str)
            ):
                expected_models[model["alias"]] = {model["name"]}
    for route in _CODEX_ROUTES:
        if not isinstance(route, dict):
            continue
        for model in route.get("models", []):
            if (
                isinstance(model, dict)
                and isinstance(model.get("name"), str)
                and isinstance(model.get("alias"), str)
            ):
                expected_models[model["alias"]] = {model["name"]}
    for route in _CLAUDE_ROUTES:
        if not isinstance(route, dict):
            continue
        for model in route.get("models", []):
            if (
                isinstance(model, dict)
                and isinstance(model.get("name"), str)
                and isinstance(model.get("alias"), str)
            ):
                expected_models[model["alias"]] = {model["name"]}
    if mode == "quality-eval":
        return _quality_eval(request, generation_targets, expected_models, config)
    continue_after_failure = mode == "generation-all" and report is not None
    result = 0
    for model in generation_targets:
        started = time.monotonic()
        try:
            # Flat 1024 budget: gpt-5.6/deepseek family models can spend the
            # whole allowance on hidden reasoning before emitting content
            # (2026-09-18 deepseek verdict); a too-tight cap reads as an empty
            # finish=length reply and misclassifies as a contract failure.
            budget = 1024
            body = {
                "model": model,
                "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                "max_tokens": budget,
            }
            if mode == "quality-canary":
                body["messages"] = [
                    {
                        "role": "user",
                        "content": (
                            'Return only JSON: {"sum":42,"word":"canary"}. '
                            "Compute 19 + 23 before responding."
                        ),
                    }
                ]
            if model in _CODEX_MODEL_ALIASES:
                response_body = {
                    "model": model,
                    "input": body["messages"],
                    "max_output_tokens": budget,
                    "tools": list(_DESKTOP_TOOL_SHAPES),
                }
                data = request("responses", response_body)
                content = _responses_output_text(data)
                finish = (
                    "stop" if data.get("status") == "completed" else data.get("status")
                )
            elif model in _CLAUDE_MODEL_ALIASES:
                # Native Anthropic Messages face: the same executor path a
                # client uses for this family, so the probe cannot pass on a
                # translated shape the route does not actually serve.
                data = request(
                    "messages",
                    {
                        "model": model,
                        "messages": [
                            *body["messages"],
                            {
                                "role": "system",
                                "content": "Health probe: effort-bearing turn.",
                                "output_config": _CLAUDE_OUTPUT_CONFIG_SHAPE,
                            },
                        ],
                        "max_tokens": budget,
                        "safeguards": list(_CLAUDE_SAFEGUARD_SHAPES),
                        "context_management": _CLAUDE_CONTEXT_MANAGEMENT_SHAPE,
                    },
                    _claude_probe_headers(config),
                )
                content = _anthropic_message_text(data)
                finish = (
                    "stop"
                    if data.get("stop_reason") == "end_turn"
                    else data.get("stop_reason")
                )
            else:
                data = request("chat/completions", body)
                choice = data["choices"][0] if not data.get("error") else {}
                finish = choice.get("finish_reason")
                content = (
                    choice.get("message", {}).get("content", "")
                    if isinstance(choice, dict)
                    else ""
                )
                content = content.strip() if isinstance(content, str) else None
            if data.get("error"):
                _emit_generation_report(
                    report,
                    model,
                    started,
                    200,
                    error_class="provider_payload",
                )
                result = max(result, 10)
                if not continue_after_failure:
                    return result
                continue
            content_matches = content == "OK"
            if mode == "quality-canary":
                semantic_result = _parse_quality_payload(content)
                content_matches = semantic_result == {"sum": 42, "word": "canary"}
            if (
                not content_matches
                or finish != "stop"
                or data.get("model") not in expected_models[model]
            ):
                _emit_generation_report(
                    report,
                    model,
                    started,
                    200,
                    finish=finish,
                    error_class="contract",
                )
                result = max(result, 20)
                if not continue_after_failure:
                    return result
                continue
            _emit_generation_report(report, model, started, 200, finish=finish)
        except UpstreamProtocolError:
            _emit_generation_report(
                report,
                model,
                started,
                200,
                error_class="upstream_protocol",
            )
            result = max(result, 10)
            if not continue_after_failure:
                return result
        except urllib.error.HTTPError as error:
            # The catalog already accepted the local client key. A per-route
            # denial for an optional prepared model is an upstream route
            # availability decision, not proof that CPA's local client
            # contract is malformed. Catalog failures remain local-contract
            # failures; an optional model's 404 commonly means it is not open.
            optional_route_unavailable = model in optional and error.code in {
                403,
                404,
            }
            error_result = (
                10
                if optional_route_unavailable
                or error.code == 403
                or error.code in TRANSIENT_HTTP_CODES
                or 500 <= error.code < 600
                else 20
            )
            if optional_route_unavailable:
                error_class = "optional_route_unavailable"
            elif error.code in TRANSIENT_HTTP_CODES or 500 <= error.code < 600:
                error_class = "transient_upstream"
            else:
                error_class = "http_error"
            _emit_generation_report(
                report,
                model,
                started,
                error.code,
                error_class=error_class,
            )
            result = max(result, error_result)
            if not continue_after_failure:
                return result
        except (urllib.error.URLError, TimeoutError):
            _emit_generation_report(
                report,
                model,
                started,
                "unavailable",
                error_class="network",
            )
            result = max(result, 10)
            if not continue_after_failure:
                return result
        except Exception:
            _emit_generation_report(
                report,
                model,
                started,
                "error",
                error_class="local_exception",
            )
            result = max(result, 20)
            if not continue_after_failure:
                return result
    if any(model in _CLAUDE_MODEL_ALIASES for model in generation_targets):
        # Behavioral net for the pinned claude-route payload rules (binding
        # escape hatch + trailing-assistant filter); folds into the same
        # result code so a lost rule fails the gate like any other probe.
        result = max(
            result,
            _claude_payload_rule_eval(
                request, generation_targets, expected_models, config
            ),
        )
    return result


def _quality_eval(request, generation_targets, expected_models, config=None):
    try:
        for model in generation_targets:
            for case in _QUALITY_EVAL_CASES:
                if model in _CODEX_MODEL_ALIASES:
                    body = _responses_case_body(model, case)
                    data = request("responses", body)
                    response_status = data.get("status")
                    if (
                        data.get("error")
                        or data.get("model") not in expected_models[model]
                    ):
                        return 10 if data.get("error") else 20
                    if "expected" in case:
                        if (
                            response_status != "completed"
                            or _parse_quality_payload(_responses_output_text(data))
                            != case["expected"]
                        ):
                            return 20
                        continue
                    function = _responses_function_call(data)
                    if (
                        response_status != "completed"
                        or not isinstance(function, dict)
                        or function.get("name") != case["expected_tool"]
                        or json.loads(function.get("arguments", ""))
                        != case["expected_arguments"]
                    ):
                        return 20
                    continue
                if model in _CLAUDE_MODEL_ALIASES:
                    data = request(
                        "messages",
                        _claude_case_body(model, case),
                        _claude_probe_headers(config),
                    )
                    if (
                        data.get("error")
                        or data.get("model") not in expected_models[model]
                    ):
                        return 10 if data.get("error") else 20
                    if "expected" in case:
                        if (
                            data.get("stop_reason") != "end_turn"
                            or _parse_quality_payload(_anthropic_message_text(data))
                            != case["expected"]
                        ):
                            return 20
                        continue
                    tool_use = _anthropic_tool_use(data)
                    if (
                        data.get("stop_reason") != "tool_use"
                        or not isinstance(tool_use, dict)
                        or tool_use.get("name") != case["expected_tool"]
                        or tool_use.get("input") != case["expected_arguments"]
                    ):
                        return 20
                    continue
                body = {
                    "model": model,
                    "messages": case["messages"],
                    "max_tokens": 1024,
                }
                if "tools" in case:
                    body["tools"] = case["tools"]
                    if model not in _FORCED_TOOL_CHOICE_UNSUPPORTED:
                        body["tool_choice"] = case["tool_choice"]
                data = request("chat/completions", body)
                if data.get("error") or data.get("model") not in expected_models[model]:
                    return 10 if data.get("error") else 20
                choice = data["choices"][0]
                if "expected" in case:
                    if (
                        choice.get("finish_reason") != "stop"
                        or _parse_quality_payload(choice["message"]["content"])
                        != case["expected"]
                    ):
                        return 20
                    continue
                tool_calls = choice["message"].get("tool_calls")
                if (
                    choice.get("finish_reason") != "tool_calls"
                    or not isinstance(tool_calls, list)
                    or len(tool_calls) != 1
                ):
                    return 20
                function = tool_calls[0].get("function")
                if (
                    not isinstance(function, dict)
                    or function.get("name") != case["expected_tool"]
                ):
                    return 20
                if (
                    json.loads(function.get("arguments", ""))
                    != case["expected_arguments"]
                ):
                    return 20
        return 0
    except UpstreamProtocolError:
        return 10
    except urllib.error.HTTPError as error:
        return (
            10
            if error.code == 403
            or error.code in TRANSIENT_HTTP_CODES
            or 500 <= error.code < 600
            else 20
        )
    except (urllib.error.URLError, TimeoutError):
        return 10
    except Exception:
        return 20


def cache_canary(
    config, request=None, sleep=time.sleep, model=_CACHE_CANARY_DEFAULT_MODEL
):
    """Measure one provider's cache metadata without exposing request content."""

    if model not in _CACHE_CANARY_MODELS:
        raise ValueError("unsupported cache canary model")
    if request is None:
        request = _loopback_request(config)
    readiness = check(config, "readiness", request, sleep)
    if readiness != 0:
        return readiness, []
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": _CACHE_CANARY_PREFIX},
            {"role": "user", "content": "Reply with exactly: OK"},
        ],
        # Same ephemeral session for both calls makes routing affinity explicit
        # without deriving or logging a reusable cross-user cache key.
        "session_id": f"cpa-cache-canary-{uuid.uuid4().hex}",
        # Keep the same ceiling as the other semantic checks: some models can
        # spend a small cap entirely on hidden reasoning and return a false
        # finish=length failure before emitting the fixed "OK" response.
        "max_tokens": 1024,
    }
    metrics = []
    try:
        for _ in range(2):
            data = request("chat/completions", body)
            if data.get("error"):
                return 10, metrics
            choice = data["choices"][0]
            if (
                choice["message"]["content"].strip() != "OK"
                or choice.get("finish_reason") != "stop"
                or data.get("model") != model
            ):
                return 20, metrics
            sample_metrics = _cache_metrics(data)
            if sample_metrics is None:
                return 12, metrics
            metrics.append(sample_metrics)
        return 0, metrics
    except UpstreamProtocolError:
        return 10, metrics
    except urllib.error.HTTPError as error:
        return (10 if error.code in TRANSIENT_HTTP_CODES else 20), metrics
    except (urllib.error.URLError, TimeoutError):
        return 10, metrics
    except Exception:
        return 20, metrics


_EXIT_LABELS = {
    0: "HEALTH_OK",
    10: "UPSTREAM_UNAVAILABLE",
    11: "RELAY_DEGRADED",
    12: "CACHE_TELEMETRY_UNAVAILABLE",
    13: "RELAY_DISABLED",
    14: "PROBE_ALREADY_RUNNING",
    20: "LOCAL_CONTRACT_FAILED",
}


def _acquire_probe_lock(mode):
    if mode not in PROBE_LOCK_MODES:
        return None, None
    try:
        import fcntl

        path = os.environ.get("CPA_HEALTH_LOCK", "/opt/cliproxyapi/health-probe.lock")
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return None, "PROBE_ALREADY_RUNNING"
        except OSError:
            os.close(fd)
            return None, "PROBE_LOCK_UNAVAILABLE"
        return (fd, fcntl), None
    except (ImportError, OSError):
        return None, "PROBE_LOCK_UNAVAILABLE"


def _release_probe_lock(lock):
    if lock is None:
        return
    fd, fcntl = lock
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


if __name__ == "__main__":
    import yaml

    lock = None
    try:
        with open("/opt/cliproxyapi/config.yaml") as handle:
            config = yaml.safe_load(handle)
        mode = sys.argv[1]
        lock, lock_failure = _acquire_probe_lock(mode)
        if lock_failure:
            print(lock_failure)
            result = 14 if lock_failure == "PROBE_ALREADY_RUNNING" else 20
        else:
            if mode == "cache-canary":
                # Optional argv[2] selects the sampled lane model; the
                # allowlist keeps the canary off the OAuth lane by default.
                canary_model = (
                    sys.argv[2] if len(sys.argv) > 2 else _CACHE_CANARY_DEFAULT_MODEL
                )
                if canary_model not in _CACHE_CANARY_MODELS:
                    print("CACHE_CANARY_MODEL_UNSUPPORTED")
                    result = 20
                else:
                    result, metrics = cache_canary(config, model=canary_model)
                    for sample, metric in enumerate(metrics, start=1):
                        print(_format_cache_metrics(sample, metric, model=canary_model))
            else:
                report = (
                    print
                    if mode in ("generation-all", "quality-canary", "quality-eval")
                    else None
                )
                result = check(config, mode, report=report)
    except Exception as exc:
        # A bare 20 with no reason is a diagnostic dead end (config unreadable
        # vs malformed argv vs unexpected local error). Print the exception
        # class only: the message can carry config paths.
        print(f"LOCAL_CONTRACT_ERROR class={type(exc).__name__}", file=sys.stderr)
        result = 20
    finally:
        _release_probe_lock(lock)
    print(_EXIT_LABELS.get(result, "LOCAL_CONTRACT_FAILED"))
    raise SystemExit(result)
