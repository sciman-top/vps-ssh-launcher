"""test_cpa_status_view.py - domain: CPA per-model/channel status view.

Covers the synthesis contract of scripts/cpa_status.py with fixture surfaces:
verdict precedence across the admission/core/risk layers, fail-closed parsing,
manifest alias extraction, catch-all rows for unmanifested cooldowns, and the
redaction contract (secrets and key-like material never reach the output).
"""

from __future__ import annotations

import base64
import importlib.util
import json
import unittest
from unittest import mock
from pathlib import Path
from typing import Any

_MODULE_PATH = Path(__file__).parents[1] / "scripts" / "cpa_status.py"
_spec = importlib.util.spec_from_file_location("cpa_status", _MODULE_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - import plumbing
    raise RuntimeError("cannot load cpa_status module")
cpa_status = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cpa_status)


def healthz_lane(**state_overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "inflight": 0,
        "strict_backoff": False,
        "hold_reason": None,
        "state_persisted": True,
        "state_write_failed": False,
        "pending": 0,
        "failure_streak": 0,
        "cooldown_active": False,
        "cooldown_remaining": 0,
        "cooldown_scope": "none",
        "model_cooldowns": {},
        "early_probe_in": 0,
        "half_open_probe": 0,
        "failure_generation": 0,
        "server_retry_after_remaining": 0,
        "reset_probe_in": 0,
    }
    state.update(state_overrides)
    return {"models": ["gpt-6-astra"], "state": state}


def telemetry_entry(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "samples": 42,
        "usage_samples": 40,
        "cached_tokens": 9700,
        "uncached_input_tokens": 300,
        "cache_hit_ratio": 0.9701,
        "first_text_ms": {"n": 30, "p50": 812, "p90": 2100, "max": 5000},
    }
    entry.update(overrides)
    return entry


def oauth_entry(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": "chatgpt-plus-oauth.json",
        "provider": "chatgpt",
        "status": "active",
        "disabled": False,
        "unavailable": False,
        "status_message": "",
        "quota": {"exceeded": False},
        "cooldowns": [],
        "success": 10,
        "failed": 0,
        "email": "someone@example.com",
    }
    entry.update(overrides)
    return entry


def surfaces(**overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "config_present": True,
        "healthz": {
            "status": "ok",
            "telemetry": {
                "window_seconds": 3600,
                "series_limit": 64,
                "dropped_series_total": 0,
                "models": {
                    "gpt-6-astra": telemetry_entry(),
                    "ghost-model": telemetry_entry(samples=3),
                },
            },
            "lanes": {"chatgpt-oauth": healthz_lane()},
        },
        "auth_files": {
            "observed_at": "now",
            "files": [oauth_entry()],
        },
        "manifest": {
            "version": 1,
            "oauth_routes": [
                {
                    "name": "chatgpt-plus-oauth",
                    "shared_consumption": True,
                    "admission_lane": "chatgpt-oauth",
                    "models": [{"alias": "gpt-6-astra"}, {"alias": "gpt-6.1-sol"}],
                }
            ],
            "plugin_routes": [],
            "providers": [],
        },
        "risk": {},
        "errors": [],
    }
    doc.update(overrides)
    return doc


def model_row(document: dict[str, Any], model: str) -> dict[str, Any]:
    rows = document["models"]
    if not isinstance(rows, list):
        raise AssertionError("document models is not a list")
    for row in rows:
        if isinstance(row, dict) and row.get("model") == model:
            return row
    raise AssertionError(f"model row missing: {model}")


class VerdictTests(unittest.TestCase):
    def test_healthy_surfaces_render_ok(self) -> None:
        document = cpa_status.synthesize(surfaces(), now=1000.0)
        self.assertEqual(model_row(document, "gpt-6-astra")["verdict"], "OK")
        self.assertEqual(document["surfaces"]["errors"], [])

    def test_lane_cooldown_shadows_credential_health(self) -> None:
        doc = surfaces(
            healthz={
                "lanes": {
                    "chatgpt-oauth": healthz_lane(
                        cooldown_active=True,
                        cooldown_remaining=25902,
                        cooldown_scope="lane",
                    )
                }
            },
            auth_files={
                "files": [
                    oauth_entry(
                        status="error",
                        unavailable=True,
                        status_message="unauthorized (refresh token invalid)",
                    )
                ]
            },
        )
        row = model_row(cpa_status.synthesize(doc, now=1000.0), "gpt-6-astra")
        self.assertEqual(row["verdict"], "COOLING_LANE")
        self.assertIn("remaining=25902s", row["detail"])

    def test_model_scoped_cooldown_names_the_model(self) -> None:
        doc = surfaces(
            healthz={
                "lanes": {
                    "chatgpt-oauth": healthz_lane(
                        cooldown_active=True,
                        cooldown_remaining=300,
                        cooldown_scope="model",
                        model_cooldowns={"gpt-6.1-sol": 300},
                    )
                }
            },
        )
        rows = cpa_status.synthesize(doc, now=1000.0)["models"]
        self.assertEqual(
            model_row({"models": rows}, "gpt-6.1-sol")["verdict"], "COOLING_MODEL"
        )
        self.assertEqual(model_row({"models": rows}, "gpt-6-astra")["verdict"], "OK")

    def test_manual_hold_wins_over_everything(self) -> None:
        doc = surfaces(
            healthz={
                "lanes": {
                    "chatgpt-oauth": healthz_lane(
                        hold_reason="operator",
                        cooldown_active=True,
                        cooldown_remaining=100,
                    )
                }
            },
        )
        row = model_row(cpa_status.synthesize(doc, now=1000.0), "gpt-6-astra")
        self.assertEqual(row["verdict"], "HOLD")
        self.assertIn("hold_reason=operator", row["detail"])

    def test_state_write_failure_is_visible(self) -> None:
        doc = surfaces(
            healthz={"lanes": {"chatgpt-oauth": healthz_lane(state_write_failed=True)}},
        )
        row = model_row(cpa_status.synthesize(doc, now=1000.0), "gpt-6-astra")
        self.assertEqual(row["verdict"], "STATE_WRITE_FAILED")

    def test_all_credentials_erroring_gives_cred_error(self) -> None:
        doc = surfaces(
            auth_files={
                "files": [
                    oauth_entry(
                        status="error",
                        unavailable=True,
                        status_message="unauthorized (refresh token invalid)",
                    )
                ]
            },
        )
        row = model_row(cpa_status.synthesize(doc, now=1000.0), "gpt-6-astra")
        self.assertEqual(row["verdict"], "CRED_ERROR")
        self.assertIn("refresh token invalid", row["detail"])

    def test_credential_quota_cooldown_view_gives_quota(self) -> None:
        doc = surfaces(
            auth_files={
                "files": [
                    oauth_entry(
                        cooldowns=[
                            {
                                "scope": "credential",
                                "reason": "credential_quota",
                                "remaining_seconds": 3600,
                                "retry_at": "2026-11-14T01:00:00Z",
                            }
                        ]
                    )
                ]
            },
        )
        row = model_row(cpa_status.synthesize(doc, now=1000.0), "gpt-6-astra")
        self.assertEqual(row["verdict"], "QUOTA")
        self.assertIn("remaining=3600s", row["detail"])
        self.assertIn("1/1 credential(s) exhausted", row["detail"])

    def test_non_quota_credential_cooldown_reads_cred_error(self) -> None:
        doc = surfaces(
            auth_files={
                "files": [
                    oauth_entry(
                        cooldowns=[
                            {
                                "scope": "credential",
                                "reason": "cloudflare_challenge",
                                "remaining_seconds": 120,
                                "retry_at": "2026-11-14T00:30:00Z",
                            }
                        ]
                    )
                ]
            },
        )
        row = model_row(cpa_status.synthesize(doc, now=1000.0), "gpt-6-astra")
        self.assertEqual(row["verdict"], "CRED_ERROR")
        self.assertIn("cloudflare_challenge", row["detail"])

    def test_expired_quota_window_does_not_block(self) -> None:
        # CooldownSnapshotForAuth emits only unexpired timers, so an expired
        # quota window appears as no view at all and the model reads OK.
        doc = surfaces(
            auth_files={
                "files": [
                    oauth_entry(
                        quota={"observed_at": "2026-11-13T00:00:00Z", "signals": {}}
                    )
                ]
            },
        )
        row = model_row(cpa_status.synthesize(doc, now=1000.0), "gpt-6-astra")
        self.assertEqual(row["verdict"], "OK")

    def test_partial_credential_failure_degrades(self) -> None:
        doc = surfaces(
            auth_files={
                "files": [
                    oauth_entry(),
                    oauth_entry(
                        name="chatgpt-plus-oauth-2.json",
                        status="error",
                        unavailable=True,
                    ),
                ]
            },
        )
        row = model_row(cpa_status.synthesize(doc, now=1000.0), "gpt-6-astra")
        self.assertEqual(row["verdict"], "CRED_DEGRADED")

    def test_persisted_risk_anchor_outranks_transient_reads(self) -> None:
        doc = surfaces(
            risk={"chatgpt": {"version": 1, "not_before": 5000.0, "hold_reason": None}}
        )
        row = model_row(cpa_status.synthesize(doc, now=1000.0), "gpt-6-astra")
        self.assertEqual(row["verdict"], "PERSISTED_COOLDOWN")
        self.assertIn("until=", row["detail"])

    def test_expired_risk_anchor_is_ignored(self) -> None:
        doc = surfaces(
            risk={"chatgpt": {"version": 1, "not_before": 500.0, "hold_reason": None}}
        )
        row = model_row(cpa_status.synthesize(doc, now=1000.0), "gpt-6-astra")
        self.assertEqual(row["verdict"], "OK")

    def test_missing_auth_files_surface_degrades_to_unknown(self) -> None:
        doc = surfaces(auth_files=None)
        doc["errors"] = ["auth-files: management.secret-key unavailable"]
        row = model_row(cpa_status.synthesize(doc, now=1000.0), "gpt-6-astra")
        self.assertEqual(row["verdict"], "UNKNOWN")

    def test_plugin_route_without_auth_files_is_also_unknown(self) -> None:
        # Plugin credentials live in the same auth-files surface; letting the
        # plugin lane read OK without it would be a guess, not an observation.
        doc = surfaces(
            auth_files=None,
            auth_note=(
                "credential state unobservable: management key unavailable; "
                "write the plaintext key to /root/.cpa-management-key"
            ),
            manifest={
                "version": 1,
                "oauth_routes": [],
                "plugin_routes": [
                    {
                        "provider": "workbuddy",
                        "realm": "cn",
                        "shared_consumption": True,
                        "admission_lane": "workbuddy-oauth",
                        "models": ["kimi-k2.8-preview"],
                    }
                ],
                "providers": [],
            },
        )
        row = model_row(cpa_status.synthesize(doc, now=1000.0), "kimi-k2.8-preview")
        self.assertEqual(row["verdict"], "UNKNOWN")
        self.assertIn("management key unavailable", row["detail"])

    def test_route_without_matching_credential_is_unknown(self) -> None:
        doc = surfaces(auth_files={"files": [oauth_entry(name="something-else.json")]})
        row = model_row(cpa_status.synthesize(doc, now=1000.0), "gpt-6-astra")
        self.assertEqual(row["verdict"], "UNKNOWN")
        self.assertIn("no credential matched", row["detail"])

    def test_oauth_route_matches_by_provider_family(self) -> None:
        # Real deployments name OAuth credentials after the account, not the
        # route; oauth_settings carries the authoritative model->family map.
        doc = surfaces(
            auth_files={
                "files": [
                    oauth_entry(
                        name="codex-1c3cf6c3-someone-plus.json",
                        provider="codex",
                    )
                ]
            },
            manifest={
                "version": 1,
                "oauth_routes": [
                    {
                        "name": "chatgpt-plus-oauth",
                        "shared_consumption": True,
                        "admission_lane": "chatgpt-oauth",
                        "models": [{"alias": "gpt-6-astra"}],
                    }
                ],
                "oauth_settings": {
                    "codex": [{"name": "gpt-6-astra", "max-context-length": 272000}]
                },
                "plugin_routes": [],
                "providers": [],
            },
        )
        row = model_row(cpa_status.synthesize(doc, now=1000.0), "gpt-6-astra")
        self.assertEqual(row["verdict"], "OK")

    def test_ambiguous_provider_families_do_not_guess(self) -> None:
        doc = surfaces(
            auth_files={
                "files": [
                    oauth_entry(name="codex-x.json", provider="codex"),
                    oauth_entry(name="antigravity-y.json", provider="antigravity"),
                ]
            },
            manifest={
                "version": 1,
                "oauth_routes": [
                    {
                        "name": "chatgpt-plus-oauth",
                        "shared_consumption": True,
                        "admission_lane": "chatgpt-oauth",
                        "models": [
                            {"alias": "gpt-6-astra"},
                            {"alias": "gpt-5.6-luna"},
                        ],
                    }
                ],
                "oauth_settings": {
                    "codex": [{"name": "gpt-6-astra"}],
                    "antigravity": [{"name": "gpt-5.6-luna"}],
                },
                "plugin_routes": [],
                "providers": [],
            },
        )
        row = model_row(cpa_status.synthesize(doc, now=1000.0), "gpt-6-astra")
        self.assertEqual(row["verdict"], "UNKNOWN")
        self.assertIn("no credential matched", row["detail"])

    def test_direct_upstream_models_are_unobserved_not_ok(self) -> None:
        doc = surfaces(
            manifest={
                "version": 1,
                "oauth_routes": [],
                "plugin_routes": [],
                "providers": [
                    {
                        "slot": 1,
                        "name": "ai.input.im",
                        "models": [{"alias": "gpt-6-astra"}],
                    }
                ],
            },
        )
        row = model_row(cpa_status.synthesize(doc, now=1000.0), "gpt-6-astra")
        self.assertEqual(row["verdict"], "UNOBSERVED")

    def test_plugin_route_matches_credentials_by_provider(self) -> None:
        doc = surfaces(
            healthz={
                "lanes": {"workbuddy-oauth": healthz_lane(models=["kimi-k2.8-preview"])}
            },
            manifest={
                "version": 1,
                "oauth_routes": [],
                "plugin_routes": [
                    {
                        "provider": "workbuddy",
                        "realm": "cn",
                        "shared_consumption": True,
                        "admission_lane": "workbuddy-oauth",
                        "models": ["kimi-k2.8-preview"],
                    }
                ],
                "providers": [],
            },
            auth_files={
                "files": [
                    oauth_entry(
                        name="workbuddy-cn-01.json",
                        provider="workbuddy",
                        cooldowns=[
                            {
                                "scope": "model",
                                "model_key": "kimi-k2.8-preview",
                                "reason": "daily_quota",
                                "remaining_seconds": 4100,
                                "retry_at": "2026-11-14T00:00:00Z",
                            }
                        ],
                    )
                ]
            },
        )
        row = model_row(cpa_status.synthesize(doc, now=1000.0), "kimi-k2.8-preview")
        self.assertEqual(row["verdict"], "COOLING_MODEL")
        self.assertIn("daily_quota", row["detail"])

    def test_unmanifested_core_cooldown_gets_catch_all_row(self) -> None:
        doc = surfaces(
            auth_files={
                "files": [
                    oauth_entry(
                        cooldowns=[
                            {
                                "scope": "model",
                                "model_key": "surprise-model",
                                "reason": "capacity",
                                "remaining_seconds": 60,
                                "retry_at": "2026-11-14T00:00:00Z",
                            }
                        ]
                    )
                ]
            },
        )
        row = model_row(cpa_status.synthesize(doc, now=1000.0), "surprise-model")
        self.assertEqual(row["verdict"], "COOLING_MODEL")
        self.assertEqual(row["kind"], "core-extra")


class ManagementKeyTests(unittest.TestCase):
    def test_bcrypt_hash_in_config_is_rejected_as_key(self) -> None:
        key, note = cpa_status._management_secret(
            {"management": {"secret-key": "$2a$10$abcdefghijklmnopqrstuv"}}
        )
        self.assertIsNone(key)
        self.assertIn("bcrypt", note or "")

    def test_plaintext_in_config_is_accepted(self) -> None:
        key, note = cpa_status._management_secret(
            {"management": {"secret-key": " plain-key "}}
        )
        self.assertEqual(key, "plain-key")
        self.assertIsNone(note)

    def test_missing_config_section_has_no_key(self) -> None:
        self.assertEqual(cpa_status._management_secret({}), (None, None))

    def test_key_file_missing_is_silent(self) -> None:
        with mock.patch.object(
            cpa_status.pathlib.Path,
            "stat",
            side_effect=FileNotFoundError,
        ):
            self.assertEqual(cpa_status._management_key_file(), (None, None))

    def test_key_file_requires_0600(self) -> None:
        fake_stat = mock.MagicMock()
        fake_stat.st_mode = 0o100644
        with mock.patch.object(cpa_status.pathlib.Path, "stat", return_value=fake_stat):
            key, note = cpa_status._management_key_file()
        self.assertIsNone(key)
        self.assertIn("chmod 600", note or "")

    def test_key_file_reads_when_private(self) -> None:
        fake_stat = mock.MagicMock()
        fake_stat.st_mode = 0o100600
        with (
            mock.patch.object(cpa_status.pathlib.Path, "stat", return_value=fake_stat),
            mock.patch.object(
                cpa_status.pathlib.Path, "read_text", return_value=" secret-key\n"
            ),
        ):
            key, note = cpa_status._management_key_file()
        self.assertEqual(key, "secret-key")
        self.assertIsNone(note)

    def test_duplicate_model_lane_rows_are_deduped(self) -> None:
        doc = surfaces(
            manifest={
                "version": 1,
                "oauth_routes": [],
                "plugin_routes": [
                    {
                        "provider": "workbuddy",
                        "realm": "cn",
                        "shared_consumption": True,
                        "admission_lane": "workbuddy-oauth",
                        "models": ["deepseek-v4.1-flash"],
                    },
                    {
                        "provider": "workbuddy",
                        "realm": "intl",
                        "shared_consumption": True,
                        "admission_lane": "workbuddy-oauth",
                        "models": ["deepseek-v4.1-flash"],
                    },
                ],
                "providers": [],
            },
        )
        rows = cpa_status.synthesize(doc, now=1000.0)["models"]
        flash_rows = [row for row in rows if row["model"] == "deepseek-v4.1-flash"]
        self.assertEqual(len(flash_rows), 1)


class FailClosedTests(unittest.TestCase):
    def test_malformed_surfaces_never_crash(self) -> None:
        doc = surfaces(
            healthz={"lanes": {"chatgpt-oauth": "not-a-mapping"}},
            auth_files={"files": ["not-a-mapping", 42]},
            manifest={"version": "bogus"},
            risk={"chatgpt": "not-a-mapping"},
        )
        document = cpa_status.synthesize(doc, now=1000.0)
        self.assertEqual(document["models"], [])
        self.assertEqual(document["channels"], [])

    def test_missing_everything_yields_empty_but_valid_document(self) -> None:
        document = cpa_status.synthesize(
            {
                "config_present": False,
                "healthz": None,
                "auth_files": None,
                "manifest": None,
                "risk": {},
                "errors": ["healthz: URLError"],
            },
            now=1000.0,
        )
        self.assertEqual(document["schema"], 1)
        self.assertIn("healthz: URLError", document["surfaces"]["errors"])


class RedactionTests(unittest.TestCase):
    def test_mask_text_strips_key_like_material(self) -> None:
        self.assertEqual(
            cpa_status.mask_text("failed key sk-abcdefghijklmnop1234"),
            "failed key ***",
        )
        self.assertEqual(
            cpa_status.mask_text("auth Bearer 超级secret"),
            "auth ***",
        )

    def test_mask_email_hides_identity(self) -> None:
        masked = cpa_status.mask_email("someone@example.com")
        self.assertNotIn("someone", masked)
        self.assertNotIn("example.com", masked)

    def test_document_and_render_never_contain_secrets(self) -> None:
        secret = "super-secret-management-key-9876"
        doc = surfaces(
            auth_files={
                "files": [
                    oauth_entry(
                        status_message=f"bad key sk-{secret} in echo",
                        email="real.person@example.com",
                    )
                ]
            }
        )
        rendered = "\n".join(cpa_status.render(cpa_status.synthesize(doc, now=1000.0)))
        self.assertNotIn(secret, rendered)
        self.assertNotIn("real.person@example.com", rendered)
        self.assertNotIn("sk-", rendered.replace("sk-***", ""))


class RenderTests(unittest.TestCase):
    def test_render_lists_sections_and_surface_errors(self) -> None:
        doc = surfaces(errors=["healthz: URLError"])
        rendered = "\n".join(cpa_status.render(cpa_status.synthesize(doc, now=1000.0)))
        self.assertIn("== CPA model availability ==", rendered)
        self.assertIn("== channels ==", rendered)
        self.assertIn("== surface errors ==", rendered)
        self.assertIn("healthz: URLError", rendered)
        self.assertIn("state view", rendered)

    def test_json_document_is_serializable(self) -> None:
        document = cpa_status.synthesize(surfaces(), now=1000.0)
        decoded = json.loads(json.dumps(document))
        self.assertEqual(decoded["schema"], 1)


class MetricsTests(unittest.TestCase):
    def test_telemetry_joins_on_model_name(self) -> None:
        document = cpa_status.synthesize(surfaces(), now=1000.0)
        metrics = model_row(document, "gpt-6-astra")["metrics"]
        self.assertEqual(metrics["cache_hit_ratio"], 0.9701)
        self.assertEqual(document["metrics_window_seconds"], 3600)

    def test_unmatched_telemetry_series_stay_visible(self) -> None:
        document = cpa_status.synthesize(surfaces(), now=1000.0)
        self.assertEqual(document["telemetry_only_models"], ["ghost-model"])
        rendered = "\n".join(cpa_status.render(document))
        self.assertIn("ghost-model", rendered)

    def test_render_includes_ratio_ttft_and_sample_count(self) -> None:
        rendered = "\n".join(
            cpa_status.render(cpa_status.synthesize(surfaces(), now=1000.0))
        )
        self.assertIn("cache=97.0%", rendered)
        self.assertIn("cached_tok=9700", rendered)
        self.assertIn("ttft p50=812ms p90=2100ms", rendered)
        self.assertIn("n=42", rendered)
        self.assertIn("metrics window: 3600s", rendered)

    def test_metrics_absent_when_telemetry_missing(self) -> None:
        doc = surfaces()
        del doc["healthz"]["telemetry"]
        document = cpa_status.synthesize(doc, now=1000.0)
        self.assertIsNone(model_row(document, "gpt-6-astra")["metrics"])
        self.assertEqual(document["telemetry_only_models"], [])

    def test_ttft_none_quantiles_do_not_render(self) -> None:
        text = cpa_status._metrics_text(
            {
                "cache_hit_ratio": 0.5,
                "first_text_ms": {"n": 1, "p50": None, "p90": None},
            }
        )
        self.assertIn("cache=50.0%", text)
        self.assertNotIn("ttft", text)

    def test_destructive_usage_queue_is_never_referenced(self) -> None:
        # /observability/usage/queue is a destructive POP: a status readout
        # that consumed queued events would corrupt the upstream consumer.
        # The module docstring documents why it is off-limits, so the guard
        # strips it and asserts no *code* references the endpoint.
        import ast

        tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
        docstring = ast.get_docstring(tree) or ""
        source = _MODULE_PATH.read_text(encoding="utf-8").replace(docstring, "")
        self.assertNotIn("usage/queue", source)


class ChannelMetricTests(unittest.TestCase):
    def test_latest_recent_request_bucket_is_reported(self) -> None:
        doc = surfaces(
            auth_files={
                "files": [
                    oauth_entry(
                        recent_requests=[
                            {"time": "2026-11-14T00:10:00Z", "success": 2, "failed": 0},
                            {"time": "2026-11-14T00:20:00Z", "success": 5, "failed": 1},
                        ]
                    )
                ]
            },
        )
        document = cpa_status.synthesize(doc, now=1000.0)
        recent = document["channels"][0]["requests_recent"]
        self.assertEqual(recent["time"], "2026-11-14T00:20:00Z")
        self.assertEqual(recent["success"], 5)
        self.assertEqual(recent["failed"], 1)

    def test_channel_without_buckets_reports_none(self) -> None:
        document = cpa_status.synthesize(surfaces(), now=1000.0)
        self.assertIsNone(document["channels"][0]["requests_recent"])


class ServePageTests(unittest.TestCase):
    def test_render_html_lists_models_channels_and_refresh(self) -> None:
        html_page = cpa_status.render_html(
            cpa_status.synthesize(surfaces(), now=1000.0)
        )
        self.assertIn('charset="utf-8"', html_page)
        self.assertIn('http-equiv="refresh" content="30"', html_page)
        self.assertIn("gpt-6-astra", html_page)
        self.assertIn("chatgpt-plus-oauth.json", html_page)
        self.assertIn("cache=97.0%", html_page)
        self.assertIn("近1h无流量", html_page)
        self.assertIn("/json", html_page)

    def test_render_html_recent_bucket_and_no_traffic_semantics(self) -> None:
        doc = surfaces(
            auth_files={
                "files": [
                    oauth_entry(
                        recent_requests=[
                            {"time": "2026-11-14T00:20:00Z", "success": 5, "failed": 1}
                        ]
                    )
                ]
            },
        )
        html_page = cpa_status.render_html(cpa_status.synthesize(doc, now=1000.0))
        self.assertIn("<th>近期</th>", html_page)
        self.assertIn("ok=5 fail=1", html_page)
        # Direct upstream rows have no telemetry surface and must not claim
        # a traffic window they never participate in.
        direct_doc = surfaces(
            manifest={
                "version": 1,
                "oauth_routes": [],
                "plugin_routes": [],
                "providers": [
                    {
                        "slot": 1,
                        "name": "ai.input.im",
                        "models": [{"alias": "gpt-6-astra"}],
                    }
                ],
            },
        )
        direct_page = cpa_status.render_html(
            cpa_status.synthesize(direct_doc, now=1000.0)
        )
        direct_row = direct_page.split("<td><code>gpt-6-astra</code></td>")[1].split(
            "</tr>"
        )[0]
        self.assertNotIn("近1h无流量", direct_row)

    def test_cli_render_annotates_empty_metrics(self) -> None:
        rendered = "\n".join(
            cpa_status.render(cpa_status.synthesize(surfaces(), now=1000.0))
        )
        self.assertIn("(no traffic in 1h window)", rendered)
        self.assertIn("cache=97.0%", rendered)

    def test_render_html_escapes_dynamic_strings(self) -> None:
        doc = surfaces(
            auth_files={
                "files": [
                    oauth_entry(
                        name='x.json",</td><script>alert(1)</script>',
                        status_message="<b>bold</b> claim",
                    )
                ]
            }
        )
        html_page = cpa_status.render_html(cpa_status.synthesize(doc, now=1000.0))
        self.assertNotIn("<script>alert(1)</script>", html_page)
        self.assertIn("&lt;b&gt;bold&lt;/b&gt;", html_page)

    def test_render_html_surfaces_errors_and_degraded_channel_view(self) -> None:
        doc = surfaces(auth_files=None)
        doc["errors"] = ["auth-files: something"]
        html_page = cpa_status.render_html(cpa_status.synthesize(doc, now=1000.0))
        self.assertIn("采集面错误", html_page)
        self.assertIn("auth-files: something", html_page)

    def test_serve_rejects_non_loopback_bind(self) -> None:
        with self.assertRaises(ValueError):
            cpa_status.serve(None, 8642, host="0.0.0.0")

    def test_snapshot_cache_keeps_last_good_view_on_failure(self) -> None:
        cache = cpa_status._SnapshotCache(None, ttl_seconds=0.0)
        with mock.patch.object(
            cpa_status, "collect_document", return_value={"schema": 1, "models": []}
        ):
            first = cache.get()
        self.assertEqual(first["schema"], 1)
        with mock.patch.object(
            cpa_status,
            "collect_document",
            side_effect=ValueError("ssh died"),
        ):
            second = cache.get()
        self.assertEqual(second["schema"], 1)
        self.assertTrue(
            any(
                "collection failed: ssh died" in str(e)
                for e in second["surfaces"]["errors"]
            )
        )

    def test_remote_serve_serves_in_process_documents(self) -> None:
        # The on-host daemon swaps the document loader for in-process
        # collection; everything else (page, /json, loopback guard) is shared.
        import threading
        import urllib.request

        calls: list[int] = []

        def loader() -> dict[str, Any]:
            calls.append(1)
            return dict(cpa_status._collection_failure_document("probe-marker"))

        server = cpa_status.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            cpa_status._make_handler(
                cpa_status._SnapshotCache(None, ttl_seconds=0.0, loader=loader)
            ),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json", timeout=5
            ) as response:
                document = json.load(response)
            self.assertIn(
                "collection failed: probe-marker", document["surfaces"]["errors"][0]
            )
            self.assertEqual(len(calls), 1)
            # The nginx shell may forward the /cpa-status/ prefix; both
            # spellings must render instead of 404ing a gated request.
            for probe_path in ("/cpa-status/", "/cpa-status"):
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}{probe_path}", timeout=5
                ) as response:
                    self.assertEqual(response.status, 200)
        finally:
            server.shutdown()
            server.server_close()

    def test_remote_serve_rejects_non_loopback_bind(self) -> None:
        with self.assertRaises(ValueError):
            cpa_status.serve(None, 8319, host="0.0.0.0", loader=lambda: {})

    def test_remote_serve_mode_has_no_local_imports(self) -> None:
        # The daemon runs on a host without scripts/audit_cli; the mode must
        # be reachable before any local-only import executes.
        import ast

        tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
        main_func = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "main"
        )
        source_segment = (
            ast.get_source_segment(_MODULE_PATH.read_text(encoding="utf-8"), main_func)
            or ""
        )
        self.assertIn("args.remote_serve", source_segment)
        remote_pos = source_segment.index("args.remote_serve")
        lossy_pos = source_segment.index("_make_streams_lossy_safe")
        self.assertLess(remote_pos, lossy_pos)

    def test_snapshot_cache_failure_without_history(self) -> None:
        cache = cpa_status._SnapshotCache(None, ttl_seconds=0.0)
        with mock.patch.object(
            cpa_status,
            "collect_document",
            side_effect=ValueError("ssh died"),
        ):
            document = cache.get()
        self.assertEqual(document["models"], [])
        self.assertIn("collection failed: ssh died", document["surfaces"]["errors"][0])


class TransportTests(unittest.TestCase):
    def test_remote_command_streams_payload_without_persistence(self) -> None:
        command = cpa_status._remote_command()
        self.assertIn("mktemp /tmp/cpa-status.", command)
        self.assertIn("trap 'rm -f", command)
        self.assertIn("--remote --json", command)
        # The payload must be single-line base64 so the printf quoting holds.
        self.assertNotIn("\n", cpa_status._remote_payload())

    def test_compressed_payload_round_trips_and_fits_argv_budget(self) -> None:
        import zlib

        payload = cpa_status._remote_payload()
        decoded = zlib.decompress(base64.b64decode(payload))
        self.assertIn(b"def main(", decoded)
        # Windows CreateProcess caps the whole command line at 32767 chars;
        # the compressed payload must leave headroom inside it.
        self.assertLessEqual(len(payload), cpa_status._PAYLOAD_ARGV_BUDGET)
        self.assertLessEqual(len(cpa_status._remote_command()), 32000)

    def test_oversized_payload_fails_locally_without_spawning_ssh(self) -> None:
        with (
            mock.patch.object(
                cpa_status,
                "_remote_payload",
                return_value="A" * (cpa_status._PAYLOAD_ARGV_BUDGET + 1),
            ),
            mock.patch.object(
                cpa_status.subprocess,
                "run",
                side_effect=AssertionError("ssh must not be spawned"),
            ),
        ):
            returncode, output = cpa_status.run_remote(None)
        self.assertEqual(returncode, 2)
        self.assertIn("CreateProcess", output)

    def test_parse_document_skips_ssh_banner_noise(self) -> None:
        document = cpa_status._parse_document('banner line\n{"schema": 1}\n')
        self.assertEqual(document["schema"], 1)

    def test_parse_document_rejects_empty_output(self) -> None:
        with self.assertRaises(ValueError):
            cpa_status._parse_document("")


if __name__ == "__main__":
    unittest.main()
