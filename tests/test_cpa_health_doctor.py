"""test_cpa_health_doctor.py - split from test_scripts.py (domain: health)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from typing import Any
from cpa_catalog_expectations import (
    OAUTH_ROUTE_ALIASES,
    PROVIDER_MATRIX_TAIL,
)

from script_validation_support import (
    CPA_TEST_PROVIDER_ALIASES,
    HEALTH_FIXTURE_CATALOG_IDS,
    ScriptValidationMixin,
)


class CpaHealthDoctorTests(ScriptValidationMixin, unittest.TestCase):
    def test_cpa_health_classifies_overload_and_model_exposure(self) -> None:
        import runpy
        import urllib.error
        from email.message import Message

        check = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa-health.py")
        )["check"]
        catalog = {"data": [{"id": m} for m in HEALTH_FIXTURE_CATALOG_IDS]}
        for code, expected in [
            (401, 20),
            # The catalog already accepted CPA's local client key; a per-route
            # 403 is therefore an upstream account/route decision.
            (403, 10),
            (429, 10),
            (503, 10),
            (520, 10),
            (526, 10),
            (400, 20),
        ]:
            with self.subTest(code=code):
                req = mock.Mock(
                    side_effect=[
                        catalog,
                        urllib.error.HTTPError("", code, "", Message(), None),
                    ]
                )
                self.assertEqual(check({}, "generation", req, mock.Mock()), expected)
        for code in [408, 429, 500, 502, 503, 504, 520, 526]:
            with self.subTest(catalog_code=code):
                req = mock.Mock(
                    side_effect=[urllib.error.HTTPError("", code, "", Message(), None)]
                )
                self.assertEqual(check({}, "generation", req, mock.Mock()), 10)
                self.assertEqual(req.call_count, 1)
                req = mock.Mock(
                    side_effect=[urllib.error.HTTPError("", code, "", Message(), None)]
                )
                self.assertEqual(check({}, "readiness", req, mock.Mock()), 10)
                self.assertEqual(req.call_count, 1)
        req = mock.Mock(
            return_value={"data": catalog["data"] + [{"id": "gpt-unexpected"}]}
        )
        self.assertEqual(check({}, "readiness", req, mock.Mock()), 20)
        self.assertEqual(req.call_count, 1)
        req = mock.Mock(
            return_value={"data": catalog["data"] + [{"id": "r1/unknown-model"}]}
        )
        self.assertEqual(check({}, "readiness", req, mock.Mock()), 20)
        self.assertEqual(req.call_count, 1)
        for mode, expected in [("readiness", 0), ("generation", 10)]:
            request = mock.Mock(return_value={"data": []})
            self.assertEqual(check({}, mode, request, mock.Mock()), expected)
        request = mock.Mock(return_value={"error": "invalid response"})
        self.assertEqual(check({}, "readiness", request, mock.Mock()), 20)

    def test_cpa_health_loopback_request_bypasses_ambient_proxy(self) -> None:
        import io
        import json
        import runpy
        import urllib.request

        check = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa-health.py")
        )["check"]
        catalog = {"data": [{"id": model} for model in HEALTH_FIXTURE_CATALOG_IDS]}
        response = mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(json.dumps(catalog).encode())
        response.__exit__.return_value = False
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch("urllib.request.build_opener", return_value=opener) as build:
            self.assertEqual(
                check({"api-keys": ["SECRET"]}, "readiness"),
                0,
            )
        build.assert_called_once()
        proxy_handler = build.call_args.args[0]
        self.assertIsInstance(proxy_handler, urllib.request.ProxyHandler)
        self.assertEqual(proxy_handler.proxies, {})
        opener.open.assert_called_once()

    def test_cpa_health_classifies_malformed_2xx_as_upstream_unavailable(self) -> None:
        import io
        import runpy
        import urllib.error
        from email.message import Message

        script = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa-health.py")
        )
        check = script["check"]
        protocol_error = script["UpstreamProtocolError"]

        response = mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(b"not-json")
        response.__exit__.return_value = False
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch("urllib.request.build_opener", return_value=opener):
            self.assertEqual(
                check({"api-keys": ["SECRET"]}, "readiness"),
                10,
            )

        catalog = {"data": [{"id": model} for model in HEALTH_FIXTURE_CATALOG_IDS]}
        malformed = mock.Mock(side_effect=[catalog, protocol_error("bad body")])
        self.assertEqual(check({}, "generation-all", malformed, mock.Mock()), 10)
        self.assertEqual(malformed.call_count, 2)

        relay = mock.Mock(side_effect=[catalog, protocol_error("bad body")])
        self.assertEqual(check({}, "relay-soft", relay, mock.Mock()), 11)
        self.assertEqual(relay.call_count, 2)

        upstream_512 = mock.Mock(
            side_effect=[
                catalog,
                urllib.error.HTTPError("fixture", 512, "upstream", Message(), None),
            ]
        )
        self.assertEqual(check({}, "generation", upstream_512, mock.Mock()), 10)

    def test_cpa_health_prepared_gpt6_models_are_optional_until_cataloged(self) -> None:
        import runpy
        import urllib.error
        from email.message import Message

        script = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa-health.py")
        )
        check = script["check"]
        required_models = list(PROVIDER_MATRIX_TAIL)
        missing_optional_catalog = {
            "data": [{"id": model} for model in required_models]
        }
        request = mock.Mock(return_value=missing_optional_catalog)
        self.assertEqual(check({}, "readiness", request, mock.Mock()), 0)
        self.assertEqual(request.call_count, 1)

        generation = {
            "model": "glm-5.3-flash",
            "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
        }
        request = mock.Mock(side_effect=[missing_optional_catalog, generation])
        self.assertEqual(check({}, "generation", request, mock.Mock()), 0)
        self.assertEqual(request.call_args_list[1].args[1]["model"], "glm-5.3-flash")

        # gpt-6.1-sol stays out of the catalog on purpose so the
        # not_listed oauth=unverified path keeps being exercised.
        matrix_targets = [
            alias for alias in OAUTH_ROUTE_ALIASES if alias != "gpt-6.1-sol"
        ] + list(PROVIDER_MATRIX_TAIL)
        full_catalog = {
            "data": [
                {"id": model}
                for model in required_models
                + [alias for alias in OAUTH_ROUTE_ALIASES if alias != "gpt-6.1-sol"]
            ]
        }
        responses: list[object] = [full_catalog]
        responses.extend(
            {
                "model": CPA_TEST_PROVIDER_ALIASES.get(model, model),
                "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            }
            for model in matrix_targets
        )
        request = mock.Mock(side_effect=responses)
        # The matrix targets include the subscription lane aliases, so this
        # deliberately reviewed run opts the OAuth lane back in.
        with mock.patch.dict(os.environ, {"CPA_HEALTH_INCLUDE_OAUTH": "1"}):
            self.assertEqual(check({}, "generation-all", request, mock.Mock()), 0)
        self.assertEqual(request.call_count, 1 + len(matrix_targets))
        self.assertEqual(
            {call.args[1]["model"] for call in request.call_args_list[1:]},
            set(matrix_targets),
        )

        closed_responses: list[object] = [full_catalog]
        closed_responses.extend(
            urllib.error.HTTPError("", 404, "", Message(), None)
            if model == "gpt-6-luna"
            else {
                "model": CPA_TEST_PROVIDER_ALIASES.get(model, model),
                "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            }
            for model in matrix_targets
        )
        closed_request = mock.Mock(side_effect=closed_responses)
        closed_lines: list[str] = []
        with mock.patch.dict(os.environ, {"CPA_HEALTH_INCLUDE_OAUTH": "1"}):
            self.assertEqual(
                check(
                    {},
                    "generation-all",
                    closed_request,
                    mock.Mock(),
                    closed_lines.append,
                ),
                10,
            )
        self.assertEqual(closed_request.call_count, 1 + len(matrix_targets))
        self.assertTrue(
            any(
                line.startswith("GENERATION model=gpt-6-luna status=404 ")
                and "error_class=optional_route_unavailable" in line
                for line in closed_lines
            )
        )

    def test_cpa_health_relay_soft_is_observability_only(self) -> None:
        import runpy
        import urllib.error
        from email.message import Message

        script = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa-health.py")
        )
        check = script["check"]
        self.assertEqual(script["_EXIT_LABELS"][11], "RELAY_DEGRADED")
        self.assertEqual(script["_EXIT_LABELS"][13], "RELAY_DISABLED")
        catalog = {"data": [{"id": m} for m in HEALTH_FIXTURE_CATALOG_IDS]}
        ok_astra = {
            "model": "gpt-6-astra",
            "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
        }
        ok_ds41 = {
            "model": "deepseek-v4.1-flash",
            "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
        }
        ok_sol_input = {
            "model": "gpt-6.1-sol",
            "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
        }
        request = mock.Mock(side_effect=[catalog, ok_astra, ok_ds41, ok_sol_input])
        self.assertEqual(check({}, "relay-soft", request, mock.Mock()), 0)
        disabled_request = mock.Mock()
        self.assertEqual(
            check(
                {"openai-compatibility": [{"name": "ai.input.im", "disabled": True}]},
                "relay-soft",
                disabled_request,
                mock.Mock(),
            ),
            13,
        )
        disabled_request.assert_not_called()
        for case_number, responses in enumerate(
            [
                [catalog, {"error": {"code": "upstream"}}],
                [catalog, urllib.error.HTTPError("", 503, "", Message(), None)],
                [catalog, TimeoutError()],
                [
                    catalog,
                    {
                        "model": "gpt-6-astra",
                        "choices": [
                            {"message": {"content": "nope"}, "finish_reason": "stop"}
                        ],
                    },
                ],
                [{"data": [{"id": "glm-5.3-flash"}]}],
                [TimeoutError()],
            ],
            start=1,
        ):
            with self.subTest(case=case_number):
                request = mock.Mock(side_effect=responses)
                self.assertEqual(check({}, "relay-soft", request, mock.Mock()), 11)

    def test_cpa_health_all_routes_is_explicit_and_budgeted(self) -> None:
        import runpy

        check = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa-health.py")
        )["check"]
        models = list(PROVIDER_MATRIX_TAIL)
        catalog = {
            "data": [
                {"id": m}
                for m in [
                    *models,
                ]
            ]
        }
        responses: list[object] = [catalog]
        responses.extend(
            {
                "model": CPA_TEST_PROVIDER_ALIASES.get(model, model),
                "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            }
            for model in models
        )
        request = mock.Mock(side_effect=responses)
        self.assertEqual(check({}, "generation-all", request, mock.Mock()), 0)
        self.assertEqual(request.call_count, 1 + len(models))
        self.assertEqual(
            {call.args[1]["model"] for call in request.call_args_list[1:]},
            {item["id"] for item in catalog["data"]},
        )
        broken_last_route = mock.Mock(
            side_effect=[*responses[:-1], RuntimeError("route unavailable")]
        )
        self.assertEqual(
            check({}, "generation-all", broken_last_route, mock.Mock()), 20
        )

        budgets = {
            call.args[1]["model"]: call.args[1]["max_tokens"]
            for call in request.call_args_list[1:]
        }
        self.assertTrue(budgets)
        self.assertEqual(set(budgets.values()), {1024})

    def test_cpa_health_no_oauth_suppresses_oauth_routes(self) -> None:
        import runpy

        check = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa-health.py")
        )["check"]
        # The same ordered provider matrix as the all-routes test, plus the
        # OAuth aliases present in the catalog: the guard must drop them even
        # though they are listed and would otherwise be probed.
        provider_models = list(PROVIDER_MATRIX_TAIL)
        oauth_models = list(OAUTH_ROUTE_ALIASES)
        catalog = {
            "data": [{"id": model} for model in [*oauth_models, *provider_models]]
        }

        def build_request(models: list[str]) -> mock.Mock:
            responses: list[object] = [catalog]
            responses.extend(
                {
                    "model": CPA_TEST_PROVIDER_ALIASES.get(model, model),
                    "choices": [
                        {"message": {"content": "OK"}, "finish_reason": "stop"}
                    ],
                }
                for model in models
            )
            return mock.Mock(side_effect=responses)

        suppressed_request = build_request(provider_models)
        lines: list[str] = []
        with mock.patch.dict(os.environ, {"CPA_HEALTH_NO_OAUTH": "1"}):
            self.assertEqual(
                check(
                    {}, "generation-all", suppressed_request, mock.Mock(), lines.append
                ),
                0,
            )
        probed = {
            call.args[1]["model"] for call in suppressed_request.call_args_list[1:]
        }
        self.assertEqual(probed, set(provider_models))
        for alias in oauth_models:
            self.assertTrue(
                any(
                    line.startswith(f"ROUTE_PREPARED model={alias} ")
                    and "kind=oauth_lane_suppressed" in line
                    for line in lines
                )
            )

        # Control: the hard suppression wins even when the lane is opted in, so
        # the absence of OAuth traffic is attributable to the flag alone.
        control_request = build_request(provider_models)
        control_lines: list[str] = []
        with mock.patch.dict(
            os.environ, {"CPA_HEALTH_NO_OAUTH": "1", "CPA_HEALTH_INCLUDE_OAUTH": "1"}
        ):
            self.assertEqual(
                check(
                    {},
                    "generation-all",
                    control_request,
                    mock.Mock(),
                    control_lines.append,
                ),
                0,
            )
        control_probed = {
            call.args[1]["model"] for call in control_request.call_args_list[1:]
        }
        self.assertEqual(control_probed, set(provider_models))
        for alias in oauth_models:
            self.assertTrue(
                any(
                    line.startswith(f"ROUTE_PREPARED model={alias} ")
                    and "reason=CPA_HEALTH_NO_OAUTH" in line
                    for line in control_lines
                )
            )

    def test_cpa_health_oauth_matrix_default_is_opt_out(self) -> None:
        import runpy

        check = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa-health.py")
        )["check"]
        provider_models = list(PROVIDER_MATRIX_TAIL)
        oauth_models = list(OAUTH_ROUTE_ALIASES)
        catalog = {
            "data": [{"id": model} for model in [*oauth_models, *provider_models]]
        }

        def build_request(models: list[str]) -> mock.Mock:
            responses: list[object] = [catalog]
            responses.extend(
                {
                    "model": CPA_TEST_PROVIDER_ALIASES.get(model, model),
                    "choices": [
                        {"message": {"content": "OK"}, "finish_reason": "stop"}
                    ],
                }
                for model in models
            )
            return mock.Mock(side_effect=responses)

        # Default matrix admission excludes the subscription lane even though
        # the OAuth aliases are listed, so a quality run cannot touch it by
        # accident.
        default_request = build_request(provider_models)
        lines: list[str] = []
        with mock.patch.dict(
            os.environ, {"CPA_HEALTH_NO_OAUTH": "0", "CPA_HEALTH_INCLUDE_OAUTH": "0"}
        ):
            self.assertEqual(
                check({}, "generation-all", default_request, mock.Mock(), lines.append),
                0,
            )
        self.assertEqual(
            {call.args[1]["model"] for call in default_request.call_args_list[1:]},
            set(provider_models),
        )
        for alias in oauth_models:
            self.assertTrue(
                any(
                    line.startswith(f"ROUTE_PREPARED model={alias} ")
                    and "kind=oauth_lane_suppressed" in line
                    and "reason=default_opt_out" in line
                    for line in lines
                )
            )
        self.assertTrue(
            any(
                line.startswith("PROBE_BUDGET ")
                and f"models={len(provider_models)}" in line
                and "cases_per_model=1" in line
                and f"planned_generation_requests={len(provider_models)}" in line
                and "oauth_lane=excluded:default_opt_out" in line
                for line in lines
            )
        )

        # Explicit opt-in re-admits the lane for a deliberately reviewed run.
        opted_in_request = build_request([*oauth_models, *provider_models])
        with mock.patch.dict(
            os.environ, {"CPA_HEALTH_NO_OAUTH": "0", "CPA_HEALTH_INCLUDE_OAUTH": "1"}
        ):
            self.assertEqual(
                check({}, "generation-all", opted_in_request, mock.Mock()), 0
            )
        self.assertEqual(
            {call.args[1]["model"] for call in opted_in_request.call_args_list[1:]},
            {*oauth_models, *provider_models},
        )

        # quality-eval multiplies the budget per model instead of hiding it.
        quality_lines: list[str] = []
        with mock.patch.dict(
            os.environ, {"CPA_HEALTH_NO_OAUTH": "0", "CPA_HEALTH_INCLUDE_OAUTH": "0"}
        ):
            self.assertEqual(
                check(
                    {},
                    "quality-eval",
                    mock.Mock(side_effect=[catalog]),
                    mock.Mock(),
                    quality_lines.append,
                ),
                20,
            )
        self.assertTrue(
            any(
                line.startswith("PROBE_BUDGET mode=quality-eval ")
                and "cases_per_model=4" in line
                for line in quality_lines
            )
        )

    def test_cpa_health_generation_all_reports_every_route_after_failure(self) -> None:
        import runpy
        import urllib.error
        from email.message import Message

        check = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa-health.py")
        )["check"]
        models = list(PROVIDER_MATRIX_TAIL)
        catalog = {"data": [{"id": model} for model in models]}
        responses: list[object] = [catalog]
        responses.extend(
            urllib.error.HTTPError("", 502, "", Message(), None)
            if model == "gpt-6.1-sol-91"
            else {
                "model": CPA_TEST_PROVIDER_ALIASES.get(model, model),
                "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            }
            for model in models
        )
        request = mock.Mock(side_effect=responses)
        lines: list[str] = []

        self.assertEqual(
            check({}, "generation-all", request, mock.Mock(), lines.append),
            10,
        )
        self.assertEqual(request.call_count, 1 + len(models))
        generation_lines = [line for line in lines if line.startswith("GENERATION ")]
        self.assertEqual(len(generation_lines), len(models))
        # Every generation route is reported, plus the suppressed
        # subscription-lane routes and the probe-budget line.
        self.assertEqual(len(lines), len(models) + len(OAUTH_ROUTE_ALIASES) + 1)
        self.assertEqual(
            sorted(
                line.split(" ", 1)[0]
                for line in lines
                if not line.startswith("GENERATION ")
            ),
            ["PROBE_BUDGET"] + ["ROUTE_PREPARED"] * len(OAUTH_ROUTE_ALIASES),
        )
        self.assertTrue(
            any(
                line.startswith("PROBE_BUDGET mode=generation-all ")
                and "oauth_lane=excluded:default_opt_out" in line
                for line in lines
            )
        )
        for alias in OAUTH_ROUTE_ALIASES:
            self.assertTrue(
                any(
                    line.startswith(f"ROUTE_PREPARED model={alias} ")
                    and "kind=oauth_lane_suppressed" in line
                    for line in lines
                )
            )
        for model in (
            "gpt-6-astra-ciii",
            "gpt-6.1-sol-ciii",
            "gpt-6.1-sol-input",
        ):
            self.assertTrue(
                any(
                    line.startswith(f"GENERATION model={model} status=200 ")
                    for line in lines
                )
            )
        self.assertTrue(
            any(
                line.startswith(
                    "GENERATION model=gpt-6.1-sol-91 status=502 latency_ms="
                )
                and line.endswith("error_class=transient_upstream")
                for line in lines
            )
        )
        for model in models:
            self.assertTrue(any(f"model={model} " in line for line in generation_lines))
        self.assertTrue(
            any(
                "model=deepseek-flash status=200" in line and "finish=stop" in line
                for line in lines
            )
        )

    def test_cpa_health_quality_canary_requires_semantic_response_per_route(
        self,
    ) -> None:
        import json
        import runpy
        import urllib.error
        from email.message import Message

        check = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa-health.py")
        )["check"]
        models = list(PROVIDER_MATRIX_TAIL)
        catalog = {
            "data": [
                {"id": model}
                for model in [
                    *models,
                ]
            ]
        }
        responses: list[object] = [catalog]
        responses.extend(
            {
                "model": CPA_TEST_PROVIDER_ALIASES.get(model, model),
                "choices": [
                    {
                        "message": {
                            "content": (
                                "```json\n"
                                + json.dumps({"sum": 42, "word": "canary"})
                                + "\n```"
                                if model == models[0]
                                else json.dumps({"sum": 42, "word": "canary"})
                            )
                        },
                        "finish_reason": "stop",
                    }
                ],
            }
            for model in models
        )
        request = mock.Mock(side_effect=responses)
        self.assertEqual(check({}, "quality-canary", request, mock.Mock()), 0)
        self.assertEqual(request.call_count, 1 + len(models))
        self.assertEqual(
            {call.args[1]["model"] for call in request.call_args_list[1:]},
            {item["id"] for item in catalog["data"]},
        )
        broken_last_route = mock.Mock(
            side_effect=[*responses[:-1], RuntimeError("route unavailable")]
        )
        self.assertEqual(
            check({}, "quality-canary", broken_last_route, mock.Mock()), 20
        )

        self.assertIn(
            "19 + 23", request.call_args_list[1].args[1]["messages"][0]["content"]
        )

        invalid = mock.Mock(
            side_effect=[
                catalog,
                {
                    "model": models[0],
                    "choices": [
                        {
                            "message": {"content": '{"sum":41,"word":"canary"}'},
                            "finish_reason": "stop",
                        }
                    ],
                },
            ]
        )
        self.assertEqual(check({}, "quality-canary", invalid, mock.Mock()), 20)

        relay_denied = mock.Mock(
            side_effect=[
                catalog,
                urllib.error.HTTPError("", 403, "", Message(), None),
            ]
        )
        self.assertEqual(check({}, "quality-canary", relay_denied, mock.Mock()), 10)

    def test_cpa_doctor_oauth_monitor_block_fails_on_expired_and_refresh_signals(
        self,
    ) -> None:
        import datetime as dt
        import json
        import tempfile

        source = (
            Path(__file__).parents[1] / "scripts/cpa_bwg_guardrails.ps1"
        ).read_text(encoding="utf-8")
        block = (
            source.split('echo "==oauth-monitor=="', 1)[1]
            .split("<<'PY'\n", 1)[1]
            .split("\nPY\n", 1)[0]
        )

        def oauth_record(
            expired: str, last_refresh: str = "2026-09-19T20:38:59+08:00"
        ) -> dict[str, Any]:
            # Placeholder token values: the monitor only reads metadata fields.
            return {
                "type": "codex",
                "access_token": "TEST_ACCESS_TOKEN",
                "refresh_token": "TEST_REFRESH_TOKEN",
                "expired": expired,
                "last_refresh": last_refresh,
            }

        def error_dump(
            body: str = "", response: str = "", age_hours: float = 0.0
        ) -> str:
            # Mirror the CLIProxyAPI dump layout: REQUEST BODY is plaintext
            # untrusted client text; only response-side sections are signal
            # evidence (2026-09-21 prompt-contamination regression).
            stamp = (
                dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=age_hours)
            ).isoformat()
            return (
                "=== REQUEST INFO ===\n"
                "Version: v7.3.7\n"
                "URL: /v1/responses\n"
                "Method: POST\n"
                f"Timestamp: {stamp}\n"
                "\n"
                "\n=== HEADERS ===\n"
                "Authorization: Bearer abcd...ef01\n"
                "\n=== REQUEST BODY ===\n"
                f"{body}\n"
                "=== RESPONSE ===\n"
                f"{response}\n"
            )

        now = dt.datetime.now(dt.timezone.utc)
        renewal_window_expiry = (now + dt.timedelta(days=3)).isoformat()
        grace_window_expiry = (now + dt.timedelta(hours=30)).isoformat()
        not_rolled_expiry = (now + dt.timedelta(hours=20)).isoformat()
        refresh_point_expiry = (now + dt.timedelta(hours=12)).isoformat()
        cases: tuple[
            tuple[str, dict[str, dict[str, Any]], dict[str, str], int, str], ...
        ] = (
            (
                "healthy",
                {"codex-ok.json": oauth_record("2030-01-01T00:00:00+00:00")},
                {},
                0,
                "oauth_monitor=OK",
            ),
            (
                "renewal_window_is_not_blocking",
                # 72h out the auto-refresh point is far away: WARN only.
                {"codex-window.json": oauth_record(renewal_window_expiry)},
                {},
                0,
                "oauth_monitor=WARN_RENEWAL_WINDOW",
            ),
            (
                "refresh_grace_not_exhausted_is_not_blocking",
                # Before the 24h auto-refresh point: WARN, not a failure.
                {"codex-grace.json": oauth_record(grace_window_expiry)},
                {},
                0,
                "oauth_monitor=WARN_RENEWAL_WINDOW",
            ),
            (
                "refresh_window_grace_exhausted_requires_action",
                # Under 22h left (24h refresh lead minus 2h grace) without an
                # expiry roll: the auto-refresh failed to fire: blocking.
                {"codex-stale.json": oauth_record(not_rolled_expiry)},
                {},
                1,
                "oauth_monitor=ACTION_REQUIRED_REENROLL_OR_VERIFY_REFRESH",
            ),
            (
                "refresh_point_requires_action",
                # Well past the refresh point without an expiry roll: blocking.
                {"codex-due.json": oauth_record(refresh_point_expiry)},
                {},
                1,
                "oauth_monitor=ACTION_REQUIRED_REENROLL_OR_VERIFY_REFRESH",
            ),
            (
                "expired",
                {"codex-old.json": oauth_record("2020-01-01T00:00:00+00:00")},
                {},
                1,
                "oauth_monitor=FAIL_EXPIRED",
            ),
            (
                "refresh_signal_in_response_section",
                {"codex-ok.json": oauth_record("2030-01-01T00:00:00+00:00")},
                {
                    "error-20260921a.log": error_dump(
                        body="routine prompt text",
                        response='{"error":{"message":"invalid_grant"}}',
                    )
                },
                1,
                "oauth_monitor=FAIL_REFRESH_SIGNAL",
            ),
            (
                "prompt_keywords_in_request_body_are_not_signals",
                # A failed request whose prompt merely discusses OAuth failure
                # keywords must never count as a credential failure.
                {"codex-ok.json": oauth_record("2030-01-01T00:00:00+00:00")},
                {
                    "error-20260921b.log": error_dump(
                        body="review text: invalid_grant refresh_token_reused "
                        "refresh token expired oauth returned 401 upstream",
                        response='{"error":{"message":"server_is_overloaded"}}',
                    )
                },
                0,
                "oauth_monitor=OK",
            ),
            (
                "signals_resolved_by_later_refresh",
                # A refresh-failure signal older than the last successful
                # refresh is reported but no longer blocking.
                {
                    "codex-ok.json": oauth_record(
                        "2030-01-01T00:00:00+00:00",
                        last_refresh=dt.datetime.now(dt.timezone.utc).isoformat(),
                    )
                },
                {
                    "error-20260921c.log": error_dump(
                        response='{"error":{"message":"invalid_grant"}}',
                        age_hours=2.0,
                    )
                },
                0,
                "oauth_refresh_signals_resolved=true",
            ),
            (
                "absent",
                {},
                {},
                0,
                "oauth_monitor=ABSENT_OPTIONAL",
            ),
        )
        for name, auth_files, log_files, expected_code, marker in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as directory:
                auth_dir = Path(directory) / "auth"
                logs_dir = auth_dir / "logs"
                logs_dir.mkdir(parents=True)
                for file_name, record in auth_files.items():
                    (auth_dir / file_name).write_text(
                        json.dumps(record), encoding="utf-8"
                    )
                for file_name, content in log_files.items():
                    (logs_dir / file_name).write_text(content, encoding="utf-8")
                completed = subprocess.run(
                    [sys.executable, "-", str(auth_dir), str(logs_dir)],
                    input=block.encode("utf-8"),
                    capture_output=True,
                    timeout=30,
                )
                output = completed.stdout.decode()
                self.assertEqual(
                    completed.returncode, expected_code, completed.stderr.decode()
                )
                self.assertIn(marker, output)

    def test_cpa_doctor_catalog_check_fails_closed_on_unknown_ids(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        text = (repo_root / "scripts" / "cpa_bwg_guardrails.ps1").read_text(
            encoding="utf-8"
        )
        # Execute the doctor's embedded catalog checker exactly as the remote
        # shell does: program from -c, manifest path as argv[1], catalog on
        # stdin. String assertions alone cannot prove the fail-closed branch.
        marker = "if printf '%s' \"$MODEL_CATALOG\" | python3 -c '"
        program = text.split(marker, 1)[1].split(
            '\' "$DIR/cpa_provider_routes.json"; then', 1
        )[0]
        manifest = repo_root / "scripts" / "remote" / "cpa_provider_routes.json"
        allowed_ids = [*PROVIDER_MATRIX_TAIL, *OAUTH_ROUTE_ALIASES]
        for ids, expected_code, expected_unknown in (
            (allowed_ids, 0, "none"),
            ([*allowed_ids, "gpt-5.5"], 1, "gpt-5.5"),
            ([*allowed_ids, "r1/unknown-model"], 1, "r1/unknown-model"),
            # A legitimate cooldown only removes IDs: still a clean contract.
            (allowed_ids[:-1], 0, "none"),
            ([], 0, "none"),
        ):
            with self.subTest(ids=ids):
                completed = subprocess.run(
                    [sys.executable, "-c", program, str(manifest)],
                    input=json.dumps({"data": [{"id": item} for item in ids]}),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, expected_code, completed.stderr)
                self.assertIn(f"MODEL_IDS_UNKNOWN={expected_unknown}", completed.stdout)
                self.assertIn("MODEL_IDS=", completed.stdout)
        # Malformed catalog and absent manifest both fail closed without a
        # traceback reaching the doctor output.
        for raw, argv in (
            ("{not json", str(manifest)),
            ("{}", str(manifest)),
            ('{"data": []}', str(manifest) + ".absent"),
        ):
            with self.subTest(raw=raw, argv=argv):
                completed = subprocess.run(
                    [sys.executable, "-c", program, argv],
                    input=raw,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 1)
                self.assertNotIn("Traceback", completed.stderr)

    def test_cpa_doctor_luna_state_covers_the_whole_oauth_route(self) -> None:
        # Upstream/account entitlement churn can drop the bare `gpt-6-luna`
        # while other OAuth aliases keep serving (the 2026-09-22 incident was
        # keyed on the since-retired `gpt-5.6-luna`). Keying the
        # OAuth lane state off that single name made one doctor run contradict
        # itself: ==client-model-catalog== listed the route while
        # ==cooldown-state== reported luna_state=unavailable_unclassified. The
        # state must come from the manifest's whole OAuth alias set.
        import http.server
        import socket
        import threading

        import yaml

        repo_root = Path(__file__).resolve().parents[1]
        text = (repo_root / "scripts" / "cpa_bwg_guardrails.ps1").read_text(
            encoding="utf-8"
        )
        doctor = text.split("$doctorScript = @'\n", 1)[1].split("\n'@", 1)[0]
        block = next(
            chunk.split("\nPY\n", 1)[0]
            for chunk in doctor.split("python3 - \"$DIR\" <<'PY'\n")[1:]
            if "cooldown_state_coverage=" in chunk
        )
        manifest_path = repo_root / "scripts" / "remote" / "cpa_provider_routes.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        oauth_aliases = sorted(
            {
                model["alias"]
                for route in manifest["oauth_routes"]
                for model in route["models"]
            }
        )
        self.assertEqual(
            oauth_aliases,
            sorted(OAUTH_ROUTE_ALIASES),
        )

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        # The block targets the real loopback gateway; retarget it at the stub
        # so the catalog branch is exercised without a live CPA.
        self.assertIn("http://127.0.0.1:8317/v1/models", block)
        block = block.replace(
            "http://127.0.0.1:8317/v1/models", f"http://127.0.0.1:{port}/v1/models"
        )

        served: list[str] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                body = json.dumps({"data": [{"id": model} for model in served]}).encode(
                    "utf-8"
                )
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:
                return

        server = http.server.HTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        # addCleanup is LIFO: register the close first so shutdown runs before
        # the listening socket disappears from under serve_forever.
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "auth").mkdir()
            (root / "config.yaml").write_text(
                yaml.safe_dump({"api-keys": ["TEST_KEY"]}), encoding="utf-8"
            )
            (root / "cpa_provider_routes.json").write_bytes(manifest_path.read_bytes())

            def readings(models: list[str]) -> dict[str, str]:
                served[:] = models
                completed = subprocess.run(
                    [sys.executable, "-c", block, str(root)],
                    capture_output=True,
                    timeout=60,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                parsed: dict[str, str] = {}
                for line in completed.stdout.decode("utf-8").splitlines():
                    if "=" in line:
                        key, _, value = line.partition("=")
                        parsed[key] = value
                return parsed

            # Only gpt-6.1-sol is advertised: the lane is alive.
            partial = readings(["gpt-6.1-sol", "glm-5.3-flash"])
            self.assertEqual(partial["luna_state"], "available_partial")
            self.assertEqual(partial["catalog_gpt6_luna"], "absent")
            self.assertEqual(partial["catalog_oauth_aliases"], "gpt-6.1-sol")
            self.assertEqual(
                partial["catalog_oauth_missing"],
                ",".join(sorted(a for a in OAUTH_ROUTE_ALIASES if a != "gpt-6.1-sol")),
            )

            # Every OAuth alias advertised: fully available.
            full = readings(list(OAUTH_ROUTE_ALIASES))
            self.assertEqual(full["luna_state"], "available")
            self.assertEqual(full["catalog_oauth_missing"], "none")
            self.assertEqual(full["catalog_gpt6_luna"], "present")

            # Nothing from the OAuth route: a genuine unclassified absence.
            absent = readings(["glm-5.3-flash"])
            self.assertEqual(absent["luna_state"], "unavailable_unclassified")
            self.assertEqual(absent["catalog_oauth_aliases"], "none")
            self.assertEqual(
                absent["catalog_oauth_missing"],
                ",".join(sorted(OAUTH_ROUTE_ALIASES)),
            )

    def test_cpa_health_generation_matrix_covers_full_manifest_without_image(
        self,
    ) -> None:
        import runpy

        health = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa-health.py")
        )
        check = health["check"]
        # gpt-image-2.5 retired 2026-10-04: no manifest declares image_models,
        # so the skip-kind=image path must stay dormant (empty alias set) and
        # the generation matrices probe every declared chat model.
        self.assertEqual(health["_IMAGE_PROVIDER_MODEL_ALIASES"], frozenset())
        required_models = list(PROVIDER_MATRIX_TAIL)
        catalog = {
            "data": [{"id": model} for model in required_models + OAUTH_ROUTE_ALIASES]
        }
        self.assertEqual(
            check({}, "readiness", mock.Mock(return_value=catalog), mock.Mock()), 0
        )
        probed = required_models + list(OAUTH_ROUTE_ALIASES)

        def echo(_path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
            if body is None:
                return catalog
            return {
                "model": CPA_TEST_PROVIDER_ALIASES.get(body["model"], body["model"]),
                "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            }

        request = mock.Mock(side_effect=echo)
        lines: list[str] = []
        # The image-model filter is independent of the OAuth lane opt-out, so
        # opt the lane back in to keep the full expected probe set observable.
        with mock.patch.dict(os.environ, {"CPA_HEALTH_INCLUDE_OAUTH": "1"}):
            self.assertEqual(
                check({}, "generation-all", request, mock.Mock(), lines.append), 0
            )
        requested = {call.args[1]["model"] for call in request.call_args_list[1:]}
        self.assertEqual(request.call_count, 1 + len(probed))
        self.assertEqual(requested, set(probed))
        self.assertFalse(any("kind=image" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
