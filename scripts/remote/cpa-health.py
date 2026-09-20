"""Bounded CPA readiness/generation check; never print response bodies or keys."""

import json
import sys
import time
import urllib.error
import urllib.request
import os
import uuid
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

_QUALITY_EVAL_CASES = (
    {
        "id": "reasoning-json-v1",
        "messages": [
            {
                "role": "user",
                "content": (
                    'Return only JSON: {"result":444,"label":"reasoning"}. '
                    "Compute (19 * 23) + 7 before responding."
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
                    'Return only JSON: {"first":"alpha","count":3}. Do not add '
                    "fields, prose, Markdown, or explanations."
                ),
            }
        ],
        "expected": {"first": "alpha", "count": 3},
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
                    'Return only JSON: {"record":"record-127","value":"value-127"}. '
                    "Read the supplied records before responding."
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


def _local_opener() -> urllib.request.OpenerDirector:
    """Keep loopback health traffic off ambient HTTP(S)_PROXY settings."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _loopback_request(config):
    def request(path, body=None):
        req = urllib.request.Request(
            "http://127.0.0.1:8317/v1/" + path,
            data=json.dumps(body).encode() if body else None,
            headers={
                "Authorization": "Bearer " + config["api-keys"][0],
                "Content-Type": "application/json",
            },
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


def _format_cache_metrics(sample, metrics):
    fields = ["CACHE_CANARY", f"sample={sample}"]
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


_BASE_ALLOWED_MODELS = {
    "glm-5.3-flash",
    "gpt-5.6-luna",
    "deepseek-flash",
}
_CHANNEL_MODELS = ("gpt-5.6-sol", "gpt-5.6-terra")


def _channel_enabled(config):
    """Return ai.input.im state; absent metadata stays fixture-compatible."""
    providers = config.get("openai-compatibility") if isinstance(config, dict) else None
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


def _generation_report_line(
    model, status, latency_ms, finish=None, error_class=None
):
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


def _emit_generation_report(report, model, started, status, finish=None, error_class=None):
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
    if request is None:
        request = _loopback_request(config)

    # Bare-catalog contract: Luna is the only open model on the ChatGPT Plus
    # OAuth slot, GLM comes from the official GLM Coding Plan, and
    # DeepSeek-flash is the only open model on the official DeepSeek API.
    # ai.input.im is an explicit secondary channel for Sol/Terra. OAuth is not
    # a hard dependency for every non-OAuth route, but the scheduled
    # generation gate deliberately exercises Luna as its default representative
    # route. Explicit matrix modes cover the other providers.
    channel_enabled = _channel_enabled(config)
    allowed = set(_BASE_ALLOWED_MODELS)
    if channel_enabled:
        allowed.update(_CHANNEL_MODELS)
    # The legacy relay-soft mode is retained as a compatibility entry point;
    # it now observes ai.input.im only. It is never an update decision.
    # When the provider is disabled, return 13 without a network request.
    # Otherwise the updater logs RELAY_DEGRADED and moves on without deferring
    # or rolling back. Single-shot per model; transient responses are never
    # retried.
    if mode == "relay-soft":
        if not channel_enabled:
            return 13
        try:
            catalog = request("models")
            ids = (
                {m["id"] for m in catalog["data"]}
                if isinstance(catalog.get("data"), list)
                else set()
            )
            if not {"gpt-5.6-sol", "gpt-5.6-terra"} <= ids:
                return 11
            for model in ("gpt-5.6-sol", "gpt-5.6-terra"):
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
                    or data.get("model") != model
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
            # The current BWG contract exposes exactly the approved bare
            # IDs. Unknown prefixes are not an alternate namespace; they are
            # unexpected routes and must fail closed.
            if ids - allowed:
                return 20
            if ids == allowed:
                catalog_complete = True
                break
        except urllib.error.HTTPError as error:
            if error.code not in TRANSIENT_HTTP_CODES:
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
    # Keep scheduled maintenance low-frequency. The default gate target is
    # Luna on the owner's ChatGPT Plus OAuth slot (user decision 2026-09-18):
    # low latency, and it exercises the OAuth pipeline that most needs
    # monitoring. It also degrades gracefully: before re-enrollment Luna is
    # absent, so the catalog never completes and generation defers with 10
    # instead of misreading a provider absence as a local failure. Transient
    # 408/429/5xx are never retried (runbook contract). ai.input.im stays out
    # of the scheduled gate and its Sol/Terra probes remain opt-in through the
    # explicit matrix below.
    generation_targets = ("gpt-5.6-luna",)
    if (
        mode in ("generation-all", "quality-canary", "quality-eval")
        or os.environ.get("CPA_HEALTH_ALL_ROUTES") == "1"
    ):
        generation_targets = ("gpt-5.6-luna", "glm-5.3-flash", "deepseek-flash")
        if channel_enabled:
            generation_targets = (
                "gpt-5.6-luna",
                "gpt-5.6-sol",
                "gpt-5.6-terra",
                "glm-5.3-flash",
                "deepseek-flash",
            )
    expected_models = {
        "gpt-5.6-luna": {"gpt-5.6-luna"},
        "glm-5.3-flash": {"glm-5.3-flash"},
        "deepseek-flash": {"deepseek-flash"},
    }
    if channel_enabled:
        expected_models.update(
            {
                "gpt-5.6-sol": {"gpt-5.6-sol"},
                "gpt-5.6-terra": {"gpt-5.6-terra"},
            }
        )
    if mode == "quality-eval":
        return _quality_eval(request, generation_targets, expected_models)
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
            data = request("chat/completions", body)
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
            choice = data["choices"][0]
            finish = choice.get("finish_reason")
            content = choice["message"]["content"].strip()
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
            # 403 is therefore an upstream route/account decision, not proof
            # that CPA's local client contract is malformed. Catalog 403s are
            # handled above and remain local-contract failures.
            error_result = (
                10 if error.code == 403 or error.code in TRANSIENT_HTTP_CODES else 20
            )
            _emit_generation_report(
                report,
                model,
                started,
                error.code,
                error_class=(
                    "transient_upstream"
                    if error.code in TRANSIENT_HTTP_CODES
                    else "http_error"
                ),
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
    return result


def _quality_eval(request, generation_targets, expected_models):
    try:
        for model in generation_targets:
            for case in _QUALITY_EVAL_CASES:
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
        return 10 if error.code == 403 or error.code in TRANSIENT_HTTP_CODES else 20
    except (urllib.error.URLError, TimeoutError):
        return 10
    except Exception:
        return 20


def cache_canary(config, request=None, sleep=time.sleep):
    """Measure one provider's cache metadata without exposing request content."""

    if request is None:
        request = _loopback_request(config)
    readiness = check(config, "readiness", request, sleep)
    if readiness != 0:
        return readiness, []
    body = {
        "model": "deepseek-flash",
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
                or data.get("model") != "deepseek-flash"
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

        path = os.environ.get(
            "CPA_HEALTH_LOCK", "/opt/cliproxyapi/health-probe.lock"
        )
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
                result, metrics = cache_canary(config)
                for sample, metric in enumerate(metrics, start=1):
                    print(_format_cache_metrics(sample, metric))
            else:
                report = print if mode == "generation-all" else None
                result = check(config, mode, report=report)
    except Exception:
        result = 20
    finally:
        _release_probe_lock(lock)
    print(_EXIT_LABELS.get(result, "LOCAL_CONTRACT_FAILED"))
    raise SystemExit(result)
