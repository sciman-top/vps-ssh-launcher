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
    ALL_OAUTH_ROUTE_ALIASES,
    OAUTH_ROUTE_ALIASES,
    PROVIDER_MATRIX_TAIL,
)

from script_validation_support import (
    CLAUDE_TEST_ROUTE_ALIASES,
    CPA_TEST_PROVIDER_ALIASES,
    CODEX_TEST_ROUTE_ALIASES,
    HEALTH_FIXTURE_CATALOG_IDS,
    ScriptValidationMixin,
    cpa_test_generation_response,
    cpa_test_probe_budget,
    read_guardrail_source,
)


class CpaHealthDoctorTests(ScriptValidationMixin, unittest.TestCase):
    def test_visible_output_state_keeps_reasoning_and_text_distinct(self) -> None:
        import runpy

        state = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa-health.py")
        )["_visible_output_state"]
        self.assertEqual(
            state(
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "OK"}],
                        }
                    ]
                }
            ),
            "text",
        )
        self.assertEqual(
            state(
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "  "}],
                        }
                    ]
                }
            ),
            "empty",
        )
        self.assertEqual(
            state({"output": [{"type": "reasoning", "summary": []}]}),
            "reasoning_only",
        )

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

    def test_cpa_health_readiness_allows_forked_plugin_alias(self) -> None:
        """A forked plugin alias must be catalog-allowed, not an unknown ID.

        The 2026-11-17 CN/INTL split projected the alias into CPA's catalog
        while readiness derived its allowed set from plugin_routes raw names
        only, so the apply battery failed closed twelve times and rolled the
        projection back. Expectations derive from the real manifest instead
        of a copied fixture, so the next alias cannot repeat the gap.
        """
        import runpy

        repo_root = Path(__file__).parents[1]
        module = runpy.run_path(str(repo_root / "scripts/remote/cpa-health.py"))
        check = module["check"]
        manifest = json.loads(
            (repo_root / "scripts/remote/cpa_provider_routes.json").read_text(
                encoding="utf-8"
            )
        )
        forked = [
            entry["alias"]
            for entries in manifest.get("oauth_model_aliases", {}).values()
            if isinstance(entries, list)
            for entry in entries
            if isinstance(entry, dict) and isinstance(entry.get("alias"), str)
        ]
        self.assertTrue(forked, "manifest fixture must carry a forked alias")
        for alias in forked:
            self.assertIn(alias.strip().lower(), module["_PLUGIN_MODEL_NAMES"])
        forked_catalog = {
            "data": [{"id": item} for item in HEALTH_FIXTURE_CATALOG_IDS]
            + [{"id": alias} for alias in forked]
        }
        request = mock.Mock(return_value=forked_catalog)
        self.assertEqual(check({}, "readiness", request, mock.Mock()), 0)
        # A lookalike the manifest does not map stays fail-closed.
        request = mock.Mock(
            return_value={
                "data": forked_catalog["data"] + [{"id": "deepseek-v4.1-flash-eu"}]
            }
        )
        self.assertEqual(check({}, "readiness", request, mock.Mock()), 20)

    def test_cpa_health_loopback_request_bypasses_ambient_proxy(self) -> None:
        import io
        import json
        import runpy
        import urllib.request

        check = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa-health.py")
        )["check"]
        catalog = {
            "data": [
                {"id": model}
                for model in ["gemini-4-hydrogen-xhigh", *HEALTH_FIXTURE_CATALOG_IDS]
            ]
        }
        response = mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(json.dumps(catalog).encode())
        response.__exit__.return_value = False
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch("urllib.request.build_opener", return_value=opener) as build:
            self.assertEqual(
                check({"access": {"api-keys": ["SECRET"]}}, "readiness"),
                0,
            )
        build.assert_called_once()
        proxy_handler = build.call_args.args[0]
        self.assertIsInstance(proxy_handler, urllib.request.ProxyHandler)
        self.assertEqual(proxy_handler.proxies, {})
        opener.open.assert_called_once()
        sent_request = opener.open.call_args.args[0]
        self.assertEqual(
            sent_request.get_header("User-agent"),
            "cli-proxy-openai-compat",
        )

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
                check({"access": {"api-keys": ["SECRET"]}}, "readiness"),
                10,
            )

        catalog = {
            "data": [
                {"id": model}
                for model in ["gemini-4-hydrogen-xhigh", *HEALTH_FIXTURE_CATALOG_IDS]
            ]
        }
        malformed = mock.Mock(side_effect=[catalog, protocol_error("bad body")])
        self.assertEqual(check({}, "generation-all", malformed, mock.Mock()), 10)
        self.assertEqual(malformed.call_count, 2)

        relay = mock.Mock(side_effect=[catalog, protocol_error("bad body")])
        self.assertEqual(check({}, "relay-soft", relay, mock.Mock()), 11)
        self.assertEqual(relay.call_count, 2)
        production = mock.Mock(return_value=catalog)
        production.is_production_probe = True
        self.assertEqual(check({}, "relay-soft", production, mock.Mock()), 11)
        self.assertNotIn("catalog", production.mock_calls[0].kwargs)

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

        missing_baseline_catalog = {
            "data": [
                {"id": model}
                for model in PROVIDER_MATRIX_TAIL
                if model != "glm-5.3-flash"
            ]
        }
        missing_full_catalog = {
            "data": [
                {"id": model}
                for model in HEALTH_FIXTURE_CATALOG_IDS
                if model != "glm-5.3-flash"
            ]
        }
        production = mock.Mock(return_value=missing_baseline_catalog)
        production.is_production_probe = True
        production.catalog = mock.Mock(return_value=missing_baseline_catalog)
        with mock.patch.dict(os.environ, {"CPA_HEALTH_PREFLIGHT_WAIT_SECONDS": "0"}):
            self.assertEqual(check({}, "generation", production, mock.Mock()), 10)
        # Zero means no additional catalog wait. The initial attempts may adopt
        # the valid catalog immediately; the absence of the scheduled baseline
        # still defers before any generation is sent.
        self.assertEqual(
            [call.args[0] for call in production.call_args_list],
            ["models"],
        )

        missing_baseline = mock.Mock(return_value=missing_baseline_catalog)
        missing_baseline.is_production_probe = True
        missing_baseline.catalog = mock.Mock(return_value=missing_baseline_catalog)
        missing_reports: list[str] = []
        with (
            mock.patch.dict(os.environ, {"CPA_HEALTH_PREFLIGHT_WAIT_SECONDS": "15"}),
            mock.patch.object(script["time"], "monotonic", return_value=0.0),
        ):
            self.assertEqual(
                check(
                    {},
                    "generation",
                    missing_baseline,
                    mock.Mock(),
                    missing_reports.append,
                ),
                10,
            )
        # The baseline gets its own bounded preflight after the initial catalog
        # attempt; it is not rejected before that wait and no generation is sent.
        self.assertGreaterEqual(
            missing_baseline.catalog.call_args_list[0].args[0], 0.25
        )
        self.assertEqual(
            [call.args[0] for call in missing_baseline.call_args_list],
            ["models"],
        )
        self.assertIn(
            "PROBE_PREFLIGHT model=glm-5.3-flash status=skipped reason=catalog_absent",
            missing_reports,
        )

        main_missing_catalog = {
            **missing_full_catalog,
            "data": [
                {"id": model["id"]}
                for model in missing_full_catalog["data"]
                if model["id"] != "glm-5.3-flash"
            ],
        }
        refreshed_catalog = {
            **missing_full_catalog,
            "data": [{"id": model} for model in required_models]
            + [{"id": "glm-5.3-flash"}],
        }
        refused = mock.Mock(side_effect=[main_missing_catalog, generation])
        refused.is_production_probe = True
        refused.catalog = mock.Mock(side_effect=[refreshed_catalog])
        refreshed_reports: list[str] = []
        # The main catalog used its complete three-attempt convergence window
        # before returning 10. Only the extra preflight read starts at zero.
        readiness_deadline = [0.0, 0.0, 0.0]
        preflight = iter((2.0, 29.5, 30.0, 30.0, 30.0))

        def clock_value() -> float:
            if readiness_deadline:
                return readiness_deadline.pop(0)
            return next(preflight)

        with (
            mock.patch.dict(os.environ, {"CPA_HEALTH_PREFLIGHT_WAIT_SECONDS": "30"}),
            mock.patch.object(script["time"], "monotonic", clock_value),
        ):
            self.assertEqual(
                check({}, "generation", refused, mock.Mock(), refreshed_reports.append),
                0,
                {
                    "reports": refreshed_reports,
                    "calls": str(refused.mock_calls),
                    "catalog_calls": str(refused.catalog.mock_calls),
                },
            )
        self.assertEqual(
            [call.args[0] for call in refused.call_args_list],
            ["models", "chat/completions"],
        )
        self.assertEqual(refused.call_args_list[-1].args[1]["model"], "glm-5.3-flash")

        # One live alias stays out of the upstream catalog on purpose so the
        # not_listed oauth=unverified path keeps being exercised. It used to be
        # the literal "gpt-6.1-sol", retired 2026-11-13: the filter then matched
        # nothing and the not_listed path silently stopped being covered.
        unlisted_alias = OAUTH_ROUTE_ALIASES[-1]
        matrix_targets = [
            alias for alias in OAUTH_ROUTE_ALIASES if alias != unlisted_alias
        ] + list(PROVIDER_MATRIX_TAIL)
        full_catalog = {
            "data": [
                {"id": model}
                for model in required_models
                + [alias for alias in OAUTH_ROUTE_ALIASES if alias != unlisted_alias]
            ]
        }
        responses: list[object] = [full_catalog]
        responses.extend(
            cpa_test_generation_response({"model": model}) for model in matrix_targets
        )
        claude_in_matrix = sum(
            1 for model in matrix_targets if model in CLAUDE_TEST_ROUTE_ALIASES
        )
        # The claude payload-rule net appends two regression probes per
        # claude-route model after the main matrix loop.
        responses.extend(
            cpa_test_generation_response({"model": "claude-haiku-5-5"})
            for _ in range(2 * claude_in_matrix)
        )
        request = mock.Mock(side_effect=responses)
        # The matrix targets include the subscription lane aliases, so this
        # deliberately reviewed run opts the OAuth lane back in.
        with mock.patch.dict(os.environ, {"CPA_HEALTH_INCLUDE_OAUTH": "1"}):
            self.assertEqual(check({}, "generation-all", request, mock.Mock()), 0)
        self.assertEqual(
            request.call_count, 1 + len(matrix_targets) + 2 * claude_in_matrix
        )
        self.assertEqual(
            {call.args[1]["model"] for call in request.call_args_list[1:]},
            set(matrix_targets),
        )

        closed_responses: list[object] = [full_catalog]
        closed_responses.extend(
            urllib.error.HTTPError("", 404, "", Message(), None)
            if model == "gpt-6-luna"
            else cpa_test_generation_response({"model": model})
            for model in matrix_targets
        )
        closed_responses.extend(
            cpa_test_generation_response({"model": "claude-haiku-5-5"})
            for _ in range(2 * claude_in_matrix)
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
        self.assertEqual(
            closed_request.call_count, 1 + len(matrix_targets) + 2 * claude_in_matrix
        )
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
            "model": "gpt-6.2-astra",
            "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
        }
        request = mock.Mock(side_effect=[catalog, ok_astra])
        self.assertEqual(check({}, "relay-soft", request, mock.Mock()), 0)
        disabled_request = mock.Mock()
        self.assertEqual(
            check(
                {
                    "api-keys": {
                        "openai-compatibility": [
                            {"name": "ai.input.im", "disabled": True}
                        ]
                    }
                },
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
            cpa_test_generation_response({"model": model}) for model in models
        )
        # Two claude payload-rule regression probes ride along for the one
        # claude-route model in the matrix.
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
            call.args[1]["model"]: cpa_test_probe_budget(call.args[1])
            for call in request.call_args_list[1 : 1 + len(models)]
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
                cpa_test_generation_response({"model": model}) for model in models
            )
            responses.extend(
                cpa_test_generation_response({"model": model})
                for model in models
                if model in CLAUDE_TEST_ROUTE_ALIASES
                for _ in range(2)
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
                cpa_test_generation_response({"model": model}) for model in models
            )
            responses.extend(
                cpa_test_generation_response({"model": model})
                for model in models
                if model in CLAUDE_TEST_ROUTE_ALIASES
                for _ in range(2)
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
            if model == "glm-5.4"
            else cpa_test_generation_response({"model": model})
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
        # Every generation route is reported, plus every suppressed OAuth
        # route (both lanes), optional absent provider models, and the probe-budget line.
        absent_optional = 1  # glm-5.3-flash-start (optional zcode-start-plan)
        self.assertEqual(
            len(lines),
            len(models) + len(ALL_OAUTH_ROUTE_ALIASES) + absent_optional + 1,
        )
        self.assertEqual(
            sorted(
                line.split(" ", 1)[0]
                for line in lines
                if not line.startswith("GENERATION ")
            ),
            ["PROBE_BUDGET"]
            + ["ROUTE_PREPARED"] * (len(ALL_OAUTH_ROUTE_ALIASES) + absent_optional),
        )
        self.assertTrue(
            any(
                line.startswith("PROBE_BUDGET mode=generation-all ")
                and "oauth_lane=excluded:default_opt_out" in line
                for line in lines
            )
        )
        for alias in ALL_OAUTH_ROUTE_ALIASES:
            self.assertTrue(
                any(
                    line.startswith(f"ROUTE_PREPARED model={alias} ")
                    and "kind=oauth_lane_suppressed" in line
                    for line in lines
                )
            )
        for model in ("gpt-6.2-astra",):
            self.assertTrue(
                any(
                    line.startswith(f"GENERATION model={model} status=200 ")
                    for line in lines
                )
            )
        self.assertTrue(
            any(
                line.startswith("GENERATION model=glm-5.4 status=502 latency_ms=")
                and line.endswith("error_class=transient_upstream")
                for line in lines
            )
        )
        for model in models:
            self.assertTrue(any(f"model={model} " in line for line in generation_lines))
        self.assertTrue(
            any(
                "model=glm-5.3-flash status=200" in line and "finish=stop" in line
                for line in lines
            )
        )

    def test_cpa_health_generation_probes_codex_routes_with_desktop_tool_shapes(
        self,
    ) -> None:
        import runpy

        check = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa-health.py")
        )["check"]
        models = list(PROVIDER_MATRIX_TAIL)
        catalog = {"data": [{"id": model} for model in models]}
        responses: list[object] = [catalog]
        responses.extend(
            cpa_test_generation_response({"model": model}) for model in models
        )
        request = mock.Mock(side_effect=responses)

        self.assertEqual(check({}, "generation-all", request, mock.Mock()), 0)
        codex_calls = {
            call.args[1]["model"]: call.args[1]
            for call in request.call_args_list[1:]
            if call.args[0] == "responses"
        }
        self.assertEqual(set(codex_calls), CODEX_TEST_ROUTE_ALIASES)
        # The muse-spark codex-route tool-shape asserts retired 2026-10-18
        # with the opencode.ai zen channel: no managed codex route remains,
        # so the responses-protocol probe machinery is dormant (and the
        # requests.payload pin derives empty until one returns).
        # The codex shapes never leak into chat-completions probes.
        for call in request.call_args_list[1:]:
            if call.args[0] == "chat/completions":
                self.assertNotIn("tools", call.args[1])

    def _probe_incomplete_responses_turn(self, reason: str) -> tuple[int, list[str]]:
        """Run one generation probe whose Responses turn ends response.incomplete.

        The managed codex route family is dormant (retired 2026-10-18), so a
        live glm gate reaches chat/completions and never the Responses branch.
        This loads the gate module through runpy and binds the dormant blank
        model to the Responses family in that namespace copy, so the finish
        classification decides the verdict.
        """
        import runpy

        namespace = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa-health.py")
        )
        check = namespace["check"]
        globals_ = check.__globals__
        model = "glm-5.3-flash"
        globals_["_CODEX_MODEL_ALIASES"] = frozenset({model})
        catalog = {"data": [{"id": alias} for alias in HEALTH_FIXTURE_CATALOG_IDS]}
        request = mock.Mock(
            side_effect=[
                catalog,
                {
                    "model": model,
                    "status": "incomplete",
                    "incomplete_details": {"reason": reason},
                    "output": [],
                },
            ]
            * 50
        )
        report = mock.Mock()
        with mock.patch.dict(
            globals_["os"].environ, {"CPA_HEALTH_NO_OAUTH": "1"}, clear=False
        ):
            result = check(
                {"access": {"api-keys": ["FIXTURE_CLIENT_KEY"]}},
                "generation",
                request,
                mock.Mock(),
                report,
            )
        lines = []
        for call in report.call_args_list:
            if call.args:
                lines.append(call.args[0])
            else:
                lines.extend(str(arg) for arg in call.kwargs.values())
        return result, lines

    def test_cpa_health_generation_reports_incomplete_budget_as_probe_budget(
        self,
    ) -> None:
        """A Responses turn cut by max_output_tokens is a budget, not a route, verdict.

        2026-11-20: live ONE_SHOT probes on glm-5.3-flash ended
        response.incomplete with reason max_output_tokens (31 of 32 output
        tokens were reasoning). The scheduled gate documents that a too-tight
        cap reads as a contract failure for chat/completions; the Responses
        branch must classify it the same way so a budget regression cannot pass
        as a provider fault.
        """
        result, lines = self._probe_incomplete_responses_turn("max_output_tokens")
        joined = " ".join(lines)
        self.assertIn("probe_budget", joined)
        self.assertNotIn("error_class=contract", joined)
        self.assertNotEqual(result, 0)

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
        semantic_text = json.dumps({"sum": 42, "word": "canary"})

        def canary_text(model: str) -> str:
            # models[0] answers inside a Markdown fence: the canary accepts one
            # fence, so the parsers stay honest about what they accept.
            return (
                "```json\n" + semantic_text + "\n```"
                if model == models[0]
                else semantic_text
            )

        def canary_response(model: str) -> dict[str, Any]:
            if model in CODEX_TEST_ROUTE_ALIASES:
                return {
                    "model": model,
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": canary_text(model)}
                            ],
                        }
                    ],
                }
            if model in CLAUDE_TEST_ROUTE_ALIASES:
                return {
                    "model": model,
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": canary_text(model)}],
                }
            return {
                "model": CPA_TEST_PROVIDER_ALIASES.get(model, model),
                "choices": [
                    {
                        "message": {"content": canary_text(model)},
                        "finish_reason": "stop",
                    }
                ],
            }

        responses.extend(canary_response(model) for model in models)
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

    def test_antigravity_accepts_only_its_exact_catalog_echo(self) -> None:
        """The legal -n echo passes while a prefix-like substitution fails."""

        import json
        import runpy
        import tempfile

        repo_remote = Path(__file__).parents[1] / "scripts/remote"
        manifest = json.loads(
            (repo_remote / "cpa_provider_routes.json").read_text(encoding="utf-8")
        )
        alias = "gemini-3.8-flash-high"
        for route in manifest["oauth_routes"]:
            if route.get("name") == "antigravity-oauth":
                route["models"] = [{"name": alias, "alias": alias}]
        manifest["antigravity_oauth_exclusions"] = [
            name
            for name in manifest.get("antigravity_oauth_exclusions", [])
            if name != alias
        ]

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            health_copy = tmp_dir / "cpa-health.py"
            health_copy.write_text(
                (repo_remote / "cpa-health.py").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (tmp_dir / "cpa_provider_routes.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            health = runpy.run_path(str(health_copy))
            echo_map = health["_ANTIGRAVITY_ECHO_MODEL"]
            self.assertIsInstance(echo_map, dict)
            echo_set = echo_map[alias]
            self.assertIsInstance(echo_set, set)
            self.assertIn("gemini-3.8-flash-n", echo_set)
            self.assertIn("gemini-3.8-flash-control", echo_set)
            self.assertNotIn("gemini-3.8-flash-n-plus", echo_set)
            self.assertEqual(health["_ROUTE_MANIFEST_ERROR"], None)

            catalog = {
                "data": [
                    {"id": model} for model in [alias, *HEALTH_FIXTURE_CATALOG_IDS]
                ]
            }

            def request(
                path: str, body: dict[str, Any] | None = None
            ) -> dict[str, Any]:
                if path == "models":
                    return catalog
                assert body is not None
                if body["model"] != alias:
                    content = (
                        '{"sum":42,"word":"canary"}'
                        if "19 + 23" in body["messages"][0]["content"]
                        else "OK"
                    )
                    return {
                        "model": CPA_TEST_PROVIDER_ALIASES.get(
                            body["model"], body["model"]
                        ),
                        "choices": [
                            {"message": {"content": content}, "finish_reason": "stop"}
                        ],
                    }
                return {
                    "model": "gemini-3.8-flash-n",
                    "choices": [
                        {
                            "message": {"content": '{"sum":42,"word":"canary"}'},
                            "finish_reason": "stop",
                        }
                    ],
                }

            with mock.patch.dict(os.environ, {"CPA_HEALTH_INCLUDE_OAUTH": "1"}):
                check = runpy.run_path(str(health_copy))["check"]
                self.assertEqual(check({}, "quality-canary", request, mock.Mock()), 0)

    def test_oauth_monitor_reports_target_provider_metadata_without_codex(self) -> None:
        import json
        import tempfile

        block = (
            read_guardrail_source()
            .split('echo "==oauth-monitor=="', 1)[1]
            .split("<<'PY'\n", 1)[1]
            .split("\nPY\n", 1)[0]
        )
        for expiry, expected in (
            ("2035-01-01T00:00:00Z", "METADATA_OK_NOT_PROVIDER_ACCEPTANCE"),
            ("2020-01-01T00:00:00Z", "WARN_ACCESS_TOKEN_EXPIRED_REFRESH_REQUIRED"),
            (float("inf"), "WARN_EXPIRY_UNKNOWN"),
            (float("nan"), "WARN_EXPIRY_UNKNOWN"),
            (10**100, "WARN_EXPIRY_UNKNOWN"),
        ):
            with self.subTest(expiry=expiry), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                for provider in ("claude", "antigravity"):
                    (root / f"{provider}.json").write_text(
                        json.dumps(
                            {
                                "type": provider,
                                "token": {"expiry": expiry},
                                "refresh_token": "SECRET_MUST_NOT_APPEAR",
                            }
                        ),
                        encoding="utf-8",
                    )
                result = subprocess.run(
                    [sys.executable, "-", str(root), str(root)],
                    input=block,
                    text=True,
                    capture_output=True,
                    timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                for provider in ("claude", "antigravity"):
                    self.assertIn(f"oauth_{provider}_files=1", result.stdout)
                    self.assertIn(f"oauth_{provider}_monitor={expected}", result.stdout)
                self.assertIn("oauth_monitor=ABSENT_OPTIONAL", result.stdout)
                self.assertNotIn("SECRET_MUST_NOT_APPEAR", result.stdout)

    def test_oauth_monitor_multiple_files_report_per_credential_visibility(
        self,
    ) -> None:
        import json
        import re
        import tempfile

        block = (
            read_guardrail_source()
            .split('echo "==oauth-monitor=="', 1)[1]
            .split("<<'PY'\n", 1)[1]
            .split("\nPY\n", 1)[0]
        )
        with self.subTest(), tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, expiry in (
                ("antigravity-one@gmail.com.json", "2035-01-01T00:00:00Z"),
                ("antigravity-two@gmail.com.json", "2020-01-01T00:00:00Z"),
            ):
                (root / name).write_text(
                    json.dumps(
                        {
                            "type": "antigravity",
                            "token": {"expiry": expiry},
                            "refresh_token": "SECRET_MUST_NOT_APPEAR",
                        }
                    ),
                    encoding="utf-8",
                )
            result = subprocess.run(
                [sys.executable, "-", str(root), str(root)],
                input=block,
                text=True,
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("oauth_antigravity_files=2", result.stdout)
            self.assertIn(
                "oauth_antigravity_monitor=WARN_MULTIPLE_ACTIVE_FILES",
                result.stdout,
            )
            self.assertEqual(
                len(
                    re.findall(
                        r"oauth_antigravity_credential_[0-9a-f]{8}_", result.stdout
                    )
                ),
                6,
            )
            self.assertIn("_expiry_known=true", result.stdout)
            self.assertIn("_status=METADATA_OK", result.stdout)
            self.assertIn("_status=EXPIRED_REFRESH_REQUIRED", result.stdout)
            self.assertNotIn("gmail.com", result.stdout)
            self.assertNotIn("antigravity-one", result.stdout)
            self.assertNotIn("SECRET_MUST_NOT_APPEAR", result.stdout)

    def test_oauth_monitor_parses_antigravity_top_level_timestamp_ms(self) -> None:
        import datetime as dt
        import hashlib
        import json
        import tempfile

        block = (
            read_guardrail_source()
            .split('echo "==oauth-monitor=="', 1)[1]
            .split("<<'PY'\n", 1)[1]
            .split("\nPY\n", 1)[0]
        )
        recent_ms = int(
            (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=30)).timestamp()
            * 1000
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = (
                (
                    "antigravity-a@gmail.com.json",
                    {
                        "type": "antigravity",
                        "expired": "2035-01-01T00:00:00Z",
                        "timestamp": recent_ms,
                    },
                ),
                (
                    "antigravity-b@gmail.com.json",
                    {
                        "type": "antigravity",
                        "expired": "2035-01-01T00:00:00Z",
                        "nested": {"timestamp": recent_ms},
                    },
                ),
                (
                    "claude-a@example.com.json",
                    {
                        "type": "claude",
                        "expired": "2035-01-01T00:00:00Z",
                        "timestamp": recent_ms,
                    },
                ),
            )
            for name, payload in files:
                (root / name).write_text(json.dumps(payload), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-", str(root), str(root)],
                input=block,
                text=True,
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            label_a = hashlib.sha256(b"antigravity-a@gmail.com.json").hexdigest()[:8]
            label_b = hashlib.sha256(b"antigravity-b@gmail.com.json").hexdigest()[:8]
            self.assertIn(
                f"oauth_antigravity_credential_{label_a}_refresh_age_hours=0.5",
                result.stdout,
            )
            self.assertIn(
                f"oauth_antigravity_credential_{label_b}_refresh_age_hours=unknown",
                result.stdout,
            )
            self.assertIn("oauth_claude_refresh_age_hours=unknown", result.stdout)

    def test_cpa_doctor_oauth_monitor_block_fails_on_expired_and_refresh_signals(
        self,
    ) -> None:
        import datetime as dt
        import json
        import tempfile

        source = read_guardrail_source()
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

    def test_oauth_monitor_attributes_refresh_logs_without_leaking_or_cross_blame(
        self,
    ) -> None:
        import contextlib
        import io

        block = (
            read_guardrail_source()
            .split('echo "==oauth-monitor=="', 1)[1]
            .split("<<'PY'\n", 1)[1]
            .split("\nPY\n", 1)[0]
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for provider in ("codex", "claude", "antigravity"):
                (root / f"{provider}.json").write_text(
                    json.dumps(
                        {
                            "type": provider,
                            "expired": "2035-01-01T00:00:00Z",
                            "refresh_token": "SECRET_TOKEN",
                        }
                    ),
                    encoding="utf-8",
                )
            (root / "error-mixed.log").write_text(
                '=== RESPONSE ===\n{"error":{"message":"invalid_grant"}}',
                encoding="utf-8",
            )
            for diagnostic, expected in (
                (
                    "credential refresh failed for claude (SECRET_ID): invalid_grant",
                    "claude",
                ),
                (
                    "credential refresh before fallback failed for antigravity (SECRET_ID): invalid_grant",
                    "antigravity",
                ),
                ("invalid_grant SECRET_ID", None),
            ):
                with self.subTest(diagnostic=expected):
                    output = io.StringIO()
                    result = subprocess.CompletedProcess([], 0, diagnostic, "")
                    with (
                        mock.patch.object(sys, "argv", ["-", tmp, tmp]),
                        mock.patch("subprocess.run", return_value=result),
                        contextlib.redirect_stdout(output),
                    ):
                        exec(compile(block, "oauth-monitor", "exec"), {})
                    text = output.getvalue()
                    self.assertIn("oauth_monitor=OK", text)
                    self.assertIn("oauth_refresh_failures_7d=0", text)
                    self.assertIn("oauth_refresh_unattributed_dump_signals_7d=1", text)
                    if expected:
                        self.assertIn(f"oauth_{expected}_refresh_failures_7d=1", text)
                        self.assertIn(
                            f"oauth_{expected}_monitor=WARN_REFRESH_SIGNAL_REVIEW_REQUIRED",
                            text,
                        )
                    else:
                        self.assertIn(
                            "oauth_refresh_unattributed_container_signals_7d=1", text
                        )
                    self.assertNotIn("SECRET", text)

    def test_cpa_health_cli_rejects_bare_and_unknown_modes(self) -> None:
        """Bare/unknown argv modes fail closed before any config read or probe.

        2026-11-24: a bare run raised a class-only IndexError and `--help`
        silently ran the baseline generation probe and printed HEALTH_OK --
        both read as a healthy gateway without checking what the caller
        intended. The rejection must happen before the /opt config open so
        this test stays cross-platform.
        """
        script = Path(__file__).parents[1] / "scripts/remote/cpa-health.py"
        cases: tuple[tuple[list[str], str], ...] = (
            ([], "CPA_HEALTH_MODE_REQUIRED"),
            (["--help"], "CPA_HEALTH_MODE_UNSUPPORTED mode=--help"),
            (["generaton-all"], "CPA_HEALTH_MODE_UNSUPPORTED mode=generaton-all"),
        )
        for argv, expected in cases:
            with self.subTest(argv=argv):
                result = subprocess.run(
                    [sys.executable, str(script), *argv],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(result.returncode, 20, result.stderr)
                self.assertIn(expected, result.stderr)
                self.assertNotIn("HEALTH_OK", result.stdout)

    def test_cpa_health_cli_modes_are_the_documented_surface(self) -> None:
        import runpy

        modes = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa-health.py")
        )["CLI_MODES"]
        self.assertEqual(
            modes,
            {
                "readiness",
                "generation",
                "generation-all",
                "quality-canary",
                "quality-eval",
                "relay-soft",
                "cache-canary",
            },
        )
        self.assertIn("readiness", modes)
