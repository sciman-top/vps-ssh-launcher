"""Tests for scripts/cockpit_provider_health.py.

The health check exists because the failure it detects is silent: when the
start predicate for the local provider gateway (port 10909) flips, the port
stops existing and the only visible symptom is a 503 from the client, with
nothing in the app log. Each invariant is pinned here so the check cannot
silently stop matching the real configuration shapes.
"""

from __future__ import annotations

import json
import runpy
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any, cast

MODULE = runpy.run_path(
    str(Path(__file__).parents[1] / "scripts" / "cockpit_provider_health.py")
)

# Pure helpers -- the discriminating rules.
key_kind = cast(Any, MODULE["key_kind"])
endpoint_expects = cast(Any, MODULE["endpoint_expects"])
check_key_endpoint_fit = cast(Any, MODULE["check_key_endpoint_fit"])
parse_bind_account = cast(Any, MODULE["parse_bind_account"])
account_md5 = cast(Any, MODULE["account_md5"])
load_provider_keys = cast(Any, MODULE["load_provider_keys"])
read_config_target = cast(Any, MODULE["read_config_target"])
build_report = cast(Any, MODULE["build_report"])
render = cast(Any, MODULE["render"])
main = cast(Any, MODULE["main"])
classify_desktop_target = cast(Any, MODULE["classify_desktop_target"])
load_route_aliases = cast(Any, MODULE["load_route_aliases"])
load_desktop_catalog_slugs = cast(Any, MODULE["load_desktop_catalog_slugs"])
load_selected_provider_catalog_slugs = cast(
    Any, MODULE["load_selected_provider_catalog_slugs"]
)
load_sidecar_upstream_models = cast(Any, MODULE["load_sidecar_upstream_models"])
check_desktop_catalog_routable = cast(Any, MODULE["check_desktop_catalog_routable"])
check_provider_catalogs = cast(Any, MODULE["check_provider_catalogs"])
DESKTOP_LOCAL_ONLY_MODELS = cast(Any, MODULE["DESKTOP_LOCAL_ONLY_MODELS"])

OK = cast(int, MODULE["OK"])
FINDINGS = cast(int, MODULE["FINDINGS"])
CANNOT_CHECK = cast(int, MODULE["CANNOT_CHECK"])
TARGET_LOCAL = cast(str, MODULE["TARGET_LOCAL"])
TARGET_PUBLIC = cast(str, MODULE["TARGET_PUBLIC"])
TARGET_OTHER = cast(str, MODULE["TARGET_OTHER"])
TARGET_UNKNOWN = cast(str, MODULE["TARGET_UNKNOWN"])

LOCAL_KEY = "agt_codex_" + "A" * 34 + "IhCT"
REMOTE_KEY = "agt_gw_" + "B" * 33 + "fE_m"


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _minimal_cockpit(
    root: Path, *, providers: Any, bind: str, port: int = 10909
) -> None:
    _write(root / "codex_model_providers.json", providers)
    _write(
        root / "codex_instances.json",
        {"instances": [], "defaultSettings": {"bindAccountId": bind}},
    )
    _write(
        root / "codex_provider_gateway_sidecars" / "36218dcc01e4" / "config.json",
        {
            "port": port,
            "api-keys": [LOCAL_KEY],
            "codex-api-key": [
                {"base-url": "https://fq.sciman.top:8443/abc/v1", "api-key": REMOTE_KEY}
            ],
        },
    )


def _hermetic_targets(root: Path) -> list[str]:
    """Pin the desktop config/catalog/route inputs inside the temp tree.

    Without these, `main` falls back to the real `~/.codex` and the repository
    manifest, so a test that means to describe a clean synthetic cockpit would
    start reporting findings from the operator's live machine.
    """
    config = root / "config.toml"
    config.write_text(
        "[model_providers.codex_local_access]\n"
        'base_url = "http://localhost:10909/v1"\n',
        encoding="utf-8",
    )
    catalog = root / "catalog.json"
    _write(catalog, {"models": []})
    routes = root / "routes.json"
    _write(routes, {"providers": [], "oauth_routes": []})
    return [
        "--codex-config",
        str(config),
        "--desktop-catalog",
        str(catalog),
        "--routes",
        str(routes),
    ]


class KeyKindTests(unittest.TestCase):
    """Key kind is decided by issuer prefix, which is what endpoints check."""

    def test_local_sidecar_key_recognised(self) -> None:
        self.assertEqual(key_kind(LOCAL_KEY), "local_sidecar")

    def test_remote_gateway_key_recognised(self) -> None:
        self.assertEqual(key_kind(REMOTE_KEY), "remote_gateway")

    def test_other_keys_are_unknown_not_guessed(self) -> None:
        # Upstream vendor keys must not be classified as either local kind;
        # guessing here would produce false findings on every other provider.
        self.assertEqual(key_kind("sk-" + "c" * 40), "unknown")


class EndpointExpectationTests(unittest.TestCase):
    def test_loopback_wants_local_sidecar_key(self) -> None:
        self.assertEqual(endpoint_expects("http://127.0.0.1:10909/v1"), "local_sidecar")
        self.assertEqual(endpoint_expects("http://localhost:10909/v1"), "local_sidecar")

    def test_public_gateway_wants_remote_key(self) -> None:
        self.assertEqual(
            endpoint_expects("https://fq.sciman.top:8443/fc3003d5715fbdf6/v1"),
            "remote_gateway",
        )

    def test_vendor_endpoint_is_not_conflated(self) -> None:
        # A vendor endpoint has its own auth scheme; the check must abstain.
        self.assertEqual(endpoint_expects("https://api.deepseek.com"), "either")


class KeyEndpointFitTests(unittest.TestCase):
    """The recorded 401 cause: a remote key attached to the local entry."""

    def test_remote_key_on_local_entry_is_a_finding(self) -> None:
        finding = check_key_endpoint_fit(
            "CPA (local 10909)", "http://127.0.0.1:10909/v1", REMOTE_KEY
        )
        self.assertIsNotNone(finding)
        assert finding is not None
        self.assertEqual(finding.code, "key-endpoint-mismatch")

    def test_local_key_on_public_gateway_is_a_finding(self) -> None:
        finding = check_key_endpoint_fit(
            "fq.sciman.top", "https://fq.sciman.top:8443/abc/v1", LOCAL_KEY
        )
        self.assertIsNotNone(finding)
        assert finding is not None
        self.assertEqual(finding.code, "key-endpoint-mismatch")

    def test_correct_pairings_are_silent(self) -> None:
        self.assertIsNone(
            check_key_endpoint_fit(
                "CPA (local 10909)", "http://127.0.0.1:10909/v1", LOCAL_KEY
            )
        )
        self.assertIsNone(
            check_key_endpoint_fit(
                "fq.sciman.top", "https://fq.sciman.top:8443/abc/v1", REMOTE_KEY
            )
        )

    def test_vendor_key_is_not_flagged(self) -> None:
        self.assertIsNone(
            check_key_endpoint_fit(
                "DeepSeek", "https://api.deepseek.com", "sk-" + "d" * 30
            )
        )

    def test_empty_key_reported(self) -> None:
        finding = check_key_endpoint_fit("X", "https://api.deepseek.com", "")
        self.assertIsNotNone(finding)
        assert finding is not None
        self.assertEqual(finding.code, "empty-key")


class BindAccountTests(unittest.TestCase):
    """The binding decides whether 10909 starts at all."""

    def test_prefix_marks_gateway_requirement(self) -> None:
        account, needs = parse_bind_account(
            "__provider_gateway__:codex_apikey_deadbeef"
        )
        self.assertEqual(account, "codex_apikey_deadbeef")
        self.assertTrue(needs)

    def test_bare_id_does_not_require_gateway(self) -> None:
        account, needs = parse_bind_account("codex_apikey_deadbeef")
        self.assertEqual(account, "codex_apikey_deadbeef")
        self.assertFalse(needs)

    def test_missing_binding_is_handled(self) -> None:
        self.assertEqual(parse_bind_account(None), (None, False))

    def test_account_md5_extracts_digest(self) -> None:
        self.assertEqual(
            account_md5("codex_apikey_ec280ff6de0ffaf6ea280e3be6041d68"),
            "ec280ff6de0ffaf6ea280e3be6041d68",
        )

    def test_oauth_account_has_no_digest(self) -> None:
        # OAuth ids are not derived from a key, so they cannot strand.
        self.assertIsNone(account_md5("codex_549794138e26af5d101f2307cdce7f41"))


class ProviderKeyIndexTests(unittest.TestCase):
    def test_md5_index_maps_digest_to_provider(self) -> None:
        providers = [
            {
                "id": "cmp_local",
                "name": "CPA (local 10909)",
                "baseUrl": "http://127.0.0.1:10909/v1",
                "apiKeys": [{"id": "k1", "apiKey": LOCAL_KEY}],
            }
        ]
        summary, by_md5, _ = load_provider_keys(providers)
        self.assertEqual(len(summary), 1)
        ref = by_md5[cast(Any, MODULE["_md5"])(LOCAL_KEY)]
        self.assertEqual(ref.provider_name, "CPA (local 10909)")
        self.assertEqual(ref.tail, "IhCT")

    def test_keys_are_never_returned_in_full(self) -> None:
        providers = [
            {
                "id": "c",
                "name": "n",
                "baseUrl": "http://127.0.0.1:10909/v1",
                "apiKeys": [{"id": "k1", "apiKey": LOCAL_KEY}],
            }
        ]
        summary, by_md5, md5_of_secret = load_provider_keys(providers)
        # Only length and tail may surface in the summary.
        dumped = json.dumps(summary)
        self.assertNotIn(LOCAL_KEY, dumped)
        for ref in by_md5.values():
            self.assertLessEqual(ref.length, len(LOCAL_KEY))
        self.assertIn(LOCAL_KEY, md5_of_secret)


class ConfigTargetTests(unittest.TestCase):
    def test_follows_the_active_model_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(
                'model_provider = "fq_sciman_top"\n'
                "\n"
                "[model_providers.codex_local_access]\n"
                'name = "Codex API Service"\n'
                'base_url = "http://localhost:10909/v1"\n'
                "\n"
                "[model_providers.fq_sciman_top]\n"
                'base_url = "https://fq.sciman.top:8443/deadbeefdeadbeef/v1"\n',
                encoding="utf-8",
            )
            self.assertEqual(
                read_config_target(path),
                "https://fq.sciman.top:8443/deadbeefdeadbeef/v1",
            )

    def test_falls_back_to_local_access_without_the_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(
                "[model_providers.other]\n"
                'base_url = "https://example.invalid/v1"\n'
                "\n"
                "[model_providers.codex_local_access]\n"
                'name = "Codex API Service"\n'
                'base_url = "http://localhost:10909/v1"\n',
                encoding="utf-8",
            )
            self.assertEqual(read_config_target(path), "http://localhost:10909/v1")

    def test_unknown_active_provider_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(
                'model_provider = "missing_block"\n'
                "\n"
                "[model_providers.codex_local_access]\n"
                'base_url = "http://localhost:10909/v1"\n',
                encoding="utf-8",
            )
            self.assertEqual(read_config_target(path), "http://localhost:10909/v1")

    def test_missing_file_is_not_an_error(self) -> None:
        self.assertIsNone(read_config_target(Path("does-not-exist.toml")))


class BuildReportTests(unittest.TestCase):
    def test_healthy_configuration_has_no_findings(self) -> None:
        providers = [
            {
                "id": "cmp_local",
                "name": "CPA (local 10909)",
                "baseUrl": "http://127.0.0.1:10909/v1",
                "apiKeys": [{"id": "k_local", "apiKey": LOCAL_KEY}],
            },
            {
                "id": "cmp_fq",
                "name": "fq.sciman.top",
                "baseUrl": "https://fq.sciman.top:8443/abc/v1",
                "apiKeys": [{"id": "k_fq", "apiKey": REMOTE_KEY}],
            },
        ]
        digest = cast(Any, MODULE["_md5"])(REMOTE_KEY)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_cockpit(
                root,
                providers=providers,
                bind=f"__provider_gateway__:codex_apikey_{digest}",
            )
            report = build_report(root)
        self.assertEqual(report.findings, [])
        self.assertTrue(report.bind_needs_gateway)
        self.assertIsNotNone(report.bound_key)

    def test_stray_key_on_local_entry_is_detected(self) -> None:
        providers = [
            {
                "id": "cmp_local",
                "name": "CPA (local 10909)",
                "baseUrl": "http://127.0.0.1:10909/v1",
                "apiKeys": [
                    {"id": "k_local", "apiKey": LOCAL_KEY},
                    {"id": "k_stray", "apiKey": REMOTE_KEY},
                ],
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_cockpit(
                root,
                providers=providers,
                bind="__provider_gateway__:codex_apikey_" + MODULE["_md5"](REMOTE_KEY),
            )
            report = build_report(root)
        codes = {f.code for f in report.findings}
        self.assertIn("key-endpoint-mismatch", codes)

    def test_stranded_binding_is_detected(self) -> None:
        providers = [
            {
                "id": "cmp_local",
                "name": "CPA (local 10909)",
                "baseUrl": "http://127.0.0.1:10909/v1",
                "apiKeys": [{"id": "k_local", "apiKey": LOCAL_KEY}],
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_cockpit(root, providers=providers, bind="codex_apikey_" + "f" * 32)
            report = build_report(root)
        codes = {f.code for f in report.findings}
        self.assertIn("binding-strands", codes)
        self.assertIsNone(report.bound_key)

    def test_bare_binding_is_a_warning_not_an_error(self) -> None:
        providers = [
            {
                "id": "cmp_local",
                "name": "CPA (local 10909)",
                "baseUrl": "http://127.0.0.1:10909/v1",
                "apiKeys": [{"id": "k_local", "apiKey": LOCAL_KEY}],
            }
        ]
        digest = cast(Any, MODULE["_md5"])(LOCAL_KEY)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_cockpit(root, providers=providers, bind=f"codex_apikey_{digest}")
            report = build_report(root)
        self.assertTrue(any(f.code == "gateway-not-required" for f in report.findings))
        self.assertFalse(any(f.severity == "error" for f in report.findings))

    def test_sidecar_accepting_remote_key_is_detected(self) -> None:
        providers = [
            {
                "id": "cmp_local",
                "name": "CPA (local 10909)",
                "baseUrl": "http://127.0.0.1:10909/v1",
                "apiKeys": [{"id": "k_local", "apiKey": LOCAL_KEY}],
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_cockpit(root, providers=providers, bind="codex_apikey_" + "0" * 32)
            # Overwrite the sidecar config to accept the wrong kind of key.
            _write(
                root
                / "codex_provider_gateway_sidecars"
                / "36218dcc01e4"
                / "config.json",
                {
                    "port": 10909,
                    "api-keys": [REMOTE_KEY],
                    "codex-api-key": [
                        {
                            "base-url": "https://fq.sciman.top:8443/abc/v1",
                            "api-key": REMOTE_KEY,
                        }
                    ],
                },
            )
            report = build_report(root)
        codes = {f.code for f in report.findings}
        self.assertIn("sidecar-accepts-wrong-kind", codes)

    def test_non_default_port_is_ignored(self) -> None:
        # Only the gateway the desktop client uses is diagnosed; the per-account
        # sidecars on other ports must not produce findings.
        providers = [
            {
                "id": "cmp_ds",
                "name": "DeepSeek",
                "baseUrl": "https://api.deepseek.com",
                "apiKeys": [{"id": "k", "apiKey": "sk-d"}],
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "codex_model_providers.json", providers)
            _write(
                root / "codex_instances.json",
                {"instances": [], "defaultSettings": {"bindAccountId": None}},
            )
            _write(
                root / "codex_provider_gateway_sidecars" / "other" / "config.json",
                {
                    "port": 3944,
                    "api-keys": [REMOTE_KEY],
                    "codex-api-key": [
                        {"base-url": "https://api.deepseek.com", "api-key": "sk-d"}
                    ],
                },
            )
            report = build_report(root)
        codes = {f.code for f in report.findings}
        self.assertNotIn("sidecar-accepts-wrong-kind", codes)


class DesktopTargetTests(unittest.TestCase):
    """Both gateway targets are accepted; only an unknown one is a problem."""

    def test_loopback_is_the_local_gateway(self) -> None:
        self.assertEqual(
            classify_desktop_target("http://127.0.0.1:10909/v1"), TARGET_LOCAL
        )
        self.assertEqual(
            classify_desktop_target("http://localhost:10909/v1"), TARGET_LOCAL
        )

    def test_public_capability_path_is_the_public_gateway(self) -> None:
        self.assertEqual(
            classify_desktop_target("https://fq.sciman.top:8443/fc3003d5715fbdf6/v1"),
            TARGET_PUBLIC,
        )

    def test_unrelated_host_is_other_not_silently_accepted(self) -> None:
        self.assertEqual(
            classify_desktop_target("https://api.deepseek.com"), TARGET_OTHER
        )

    def test_missing_target_is_unknown(self) -> None:
        self.assertEqual(classify_desktop_target(None), TARGET_UNKNOWN)
        self.assertEqual(classify_desktop_target(""), TARGET_UNKNOWN)


class DesktopCatalogRoutabilityTests(unittest.TestCase):
    """The failure this check exists for: a picker entry the gateway cannot serve.

    Cockpit builds the desktop model list from the provider catalogs, so a name
    that only ever existed as a provider-side alias keeps being offered after the
    desktop stops pointing at the gateway that rewrote it.
    """

    def test_alias_loader_reads_provider_and_oauth_routes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            routes = Path(tmp) / "routes.json"
            _write(
                routes,
                {
                    "providers": [
                        {"slot": 1, "name": "a", "models": [{"alias": "m-one"}]}
                    ],
                    "oauth_routes": [{"name": "o", "models": [{"alias": "m-two"}]}],
                },
            )
            self.assertEqual(load_route_aliases(routes), {"m-one", "m-two"})

    def test_alias_loader_returns_empty_for_unreadable_manifest(self) -> None:
        self.assertEqual(load_route_aliases(Path("does-not-exist.json")), set())

    def test_catalog_loader_reads_slugs_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog = Path(tmp) / "catalog.json"
            _write(catalog, {"models": [{"slug": "b"}, {"slug": "a"}, {"nope": 1}]})
            self.assertEqual(load_desktop_catalog_slugs(catalog), ["b", "a"])

    def test_selected_provider_catalog_reads_upstream_names_only(self) -> None:
        providers = [
            {
                "name": "fq.sciman.top",
                "baseUrl": "HTTPS://FQ.SCIMAN.TOP:8443/abc/v1/",
                "modelCatalog": ["gpt-6-luna", "gpt-6.1-sol"],
            },
            {
                "name": "other",
                "baseUrl": "https://other.invalid/v1",
                "modelCatalog": ["gpt-5.5"],
            },
        ]
        self.assertEqual(
            load_selected_provider_catalog_slugs(
                providers, "https://fq.sciman.top:8443/abc/v1"
            ),
            ["gpt-6-luna", "gpt-6.1-sol"],
        )

    def test_selected_provider_catalog_returns_none_when_unmatched(self) -> None:
        self.assertIsNone(
            load_selected_provider_catalog_slugs(
                [{"baseUrl": "https://other.invalid/v1", "modelCatalog": ["x"]}],
                "https://fq.sciman.top:8443/abc/v1",
            )
        )

    def test_sidecar_upstream_models_include_aliases_and_local_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_cockpit(root, providers=[], bind="codex_apikey_" + "0" * 32)
            _write(
                root
                / "codex_provider_gateway_sidecars"
                / "36218dcc01e4"
                / "manifest.json",
                {
                    "apiKeys": [
                        {"providerGateway": {"upstreamModels": ["up-one", "up-two"]}}
                    ],
                    "modelAliases": [{"alias": "alias-one"}],
                    "modelIds": ["codex-auto-review"],
                },
            )
            self.assertEqual(
                load_sidecar_upstream_models(root),
                {"up-one", "up-two", "alias-one", "codex-auto-review"},
            )

    def test_routable_catalog_has_no_findings(self) -> None:
        self.assertEqual(
            check_desktop_catalog_routable(TARGET_PUBLIC, ["a", "b"], {"a", "b", "c"}),
            [],
        )

    def test_unroutable_slugs_are_reported_with_names(self) -> None:
        findings = check_desktop_catalog_routable(
            TARGET_PUBLIC, ["a", "gpt-5.5", "gpt-5.6-sol"], {"a"}
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].code, "desktop-model-unroutable")
        self.assertIn("gpt-5.5", findings[0].detail)
        self.assertIn("gpt-5.6-sol", findings[0].detail)

    def test_local_only_models_are_allowlisted(self) -> None:
        findings = check_desktop_catalog_routable(
            TARGET_PUBLIC, ["a", "codex-auto-review"], {"a"}
        )
        self.assertEqual(findings, [])
        self.assertIn("codex-auto-review", DESKTOP_LOCAL_ONLY_MODELS)

    def test_check_abstains_when_the_target_is_unknown(self) -> None:
        self.assertEqual(
            check_desktop_catalog_routable(TARGET_OTHER, ["ghost"], {"a"}), []
        )

    def test_check_abstains_when_the_routable_set_is_empty(self) -> None:
        # An unreadable manifest must not turn into "everything is unroutable".
        self.assertEqual(
            check_desktop_catalog_routable(TARGET_PUBLIC, ["ghost"], set()), []
        )


class ProviderCatalogTests(unittest.TestCase):
    """Stale names that live in one provider's own catalog, not the picker.

    Measured case: a retired slot-2 alias stayed in one provider's catalog and
    produced 13 gateway `400`s over 72h while the repository no longer referenced
    it anywhere. The desktop catalog is a union, so this class of name never
    reaches the picker and is invisible to the desktop-catalog check.
    """

    def test_stale_name_in_a_loopback_provider_catalog_is_reported(self) -> None:
        providers = [
            {
                "name": "CPA (local 10909)",
                "baseUrl": "http://127.0.0.1:10909/v1",
                "modelCatalog": ["good", "retired-alias"],
            }
        ]
        findings = check_provider_catalogs(providers, {"good"}, {"good"})
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].code, "provider-catalog-stale")
        self.assertEqual(findings[0].severity, "warn")
        self.assertIn("retired-alias", findings[0].detail)
        self.assertIn("local_gateway", findings[0].detail)

    def test_public_provider_catalog_is_checked_against_the_route_manifest(
        self,
    ) -> None:
        providers = [
            {
                "name": "fq.sciman.top",
                "baseUrl": "https://fq.sciman.top:8443/abc/v1",
                "modelCatalog": ["routed", "ghost"],
            }
        ]
        findings = check_provider_catalogs(providers, {"local-only"}, {"routed"})
        self.assertEqual(len(findings), 1)
        self.assertIn("ghost", findings[0].detail)

    def test_clean_provider_catalogs_produce_nothing(self) -> None:
        providers = [
            {
                "name": "fq.sciman.top",
                "baseUrl": "https://fq.sciman.top:8443/abc/v1",
                "modelCatalog": ["a", "b"],
            }
        ]
        self.assertEqual(check_provider_catalogs(providers, set(), {"a", "b"}), [])

    def test_unknown_endpoint_and_empty_routable_set_abstain(self) -> None:
        providers = [
            {
                "name": "vendor",
                "baseUrl": "https://api.deepseek.com",
                "modelCatalog": ["whatever"],
            },
            {
                "name": "fq.sciman.top",
                "baseUrl": "https://fq.sciman.top:8443/abc/v1",
                "modelCatalog": ["whatever"],
            },
        ]
        # The vendor endpoint is not one of the two accepted targets, and an
        # unreadable manifest must not turn into "everything is stale".
        self.assertEqual(check_provider_catalogs(providers, set(), set()), [])

    def test_local_only_allowlist_applies_here_too(self) -> None:
        providers = [
            {
                "name": "CPA (local 10909)",
                "baseUrl": "http://127.0.0.1:10909/v1",
                "modelCatalog": ["codex-auto-review"],
            }
        ]
        self.assertEqual(check_provider_catalogs(providers, {"x"}, {"x"}), [])

    def test_missing_catalog_is_skipped(self) -> None:
        providers = [{"name": "p", "baseUrl": "http://127.0.0.1:10909/v1"}]
        self.assertEqual(check_provider_catalogs(providers, {"x"}, {"x"}), [])


class RenderAndCliTests(unittest.TestCase):
    def test_render_reports_clean_state(self) -> None:
        report = cast(Any, MODULE["Report"])()
        text = render(report, "http://localhost:10909/v1")
        self.assertIn("未发现配置层面的已知故障形态", text)

    def test_render_names_the_active_target_mode(self) -> None:
        report = cast(Any, MODULE["Report"])()
        self.assertIn(
            f"桌面目标模式        = {TARGET_LOCAL}",
            render(report, "http://localhost:10909/v1"),
        )
        self.assertIn(
            f"桌面目标模式        = {TARGET_PUBLIC}",
            render(report, "https://fq.sciman.top:8443/abc/v1"),
        )

    def test_main_flags_unroutable_desktop_models_on_the_public_gateway(self) -> None:
        providers = [
            {
                "id": "cmp_public",
                "name": "fq.sciman.top",
                "baseUrl": "https://fq.sciman.top:8443/abc/v1",
                "apiKeys": [{"id": "k_public", "apiKey": REMOTE_KEY}],
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_cockpit(
                root,
                providers=providers,
                bind="__provider_gateway__:codex_apikey_" + MODULE["_md5"](REMOTE_KEY),
            )
            config = root / "config.toml"
            config.write_text(
                "[model_providers.codex_local_access]\n"
                'base_url = "https://fq.sciman.top:8443/abc/v1"\n',
                encoding="utf-8",
            )
            catalog = root / "catalog.json"
            _write(catalog, {"models": [{"slug": "routed"}, {"slug": "ghost"}]})
            routes = root / "routes.json"
            _write(
                routes, {"providers": [{"slot": 1, "models": [{"alias": "routed"}]}]}
            )
            rc = main(
                [
                    "--cockpit-dir",
                    str(root),
                    "--codex-config",
                    str(config),
                    "--desktop-catalog",
                    str(catalog),
                    "--routes",
                    str(routes),
                    "--json",
                ]
            )
        self.assertEqual(rc, FINDINGS)

    def test_main_attributes_projected_shells_without_blaming_provider_list(
        self,
    ) -> None:
        providers = [
            {
                "id": "cmp_public",
                "name": "fq.sciman.top",
                "baseUrl": "https://fq.sciman.top:8443/abc/v1",
                "modelCatalog": ["gpt-6-luna", "gpt-6.1-sol", "gpt-image-2.5"],
                "apiKeys": [{"id": "k_public", "apiKey": REMOTE_KEY}],
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_cockpit(
                root,
                providers=providers,
                bind="__provider_gateway__:codex_apikey_" + MODULE["_md5"](REMOTE_KEY),
            )
            config = root / "config.toml"
            config.write_text(
                "[model_providers.codex_local_access]\n"
                'base_url = "https://fq.sciman.top:8443/abc/v1"\n',
                encoding="utf-8",
            )
            # Cockpit writes display labels from the provider but different slugs.
            catalog = root / "catalog.json"
            _write(
                catalog,
                {
                    "models": [
                        {"slug": "gpt-6-luna"},
                        {"slug": "gpt-5.6-sol", "display_name": "gpt-image-2.5"},
                    ]
                },
            )
            routes = root / "routes.json"
            _write(
                routes,
                {
                    "oauth_routes": [
                        {"name": "oauth", "models": [{"alias": "gpt-6-luna"}]}
                    ],
                    "providers": [
                        {
                            "slot": 1,
                            "models": [
                                {"alias": "gpt-6.1-sol"},
                                {"alias": "gpt-image-2.5"},
                            ],
                        }
                    ],
                },
            )
            output = StringIO()
            with redirect_stdout(output):
                rc = main(
                    [
                        "--cockpit-dir",
                        str(root),
                        "--codex-config",
                        str(config),
                        "--desktop-catalog",
                        str(catalog),
                        "--routes",
                        str(routes),
                        "--json",
                    ]
                )
        self.assertEqual(rc, FINDINGS)
        findings = json.loads(output.getvalue())["findings"]
        self.assertEqual(
            [f["code"] for f in findings], ["desktop-catalog-shell-unroutable"]
        )
        self.assertIn("gpt-5.6-sol → gpt-image-2.5", findings[0]["detail"])

    def test_main_reports_a_warning_for_an_unrecognised_target(self) -> None:
        providers = [
            {
                "id": "cmp_local",
                "name": "CPA (local 10909)",
                "baseUrl": "http://127.0.0.1:10909/v1",
                "apiKeys": [{"id": "k_local", "apiKey": LOCAL_KEY}],
            }
        ]
        digest = cast(Any, MODULE["_md5"])(LOCAL_KEY)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_cockpit(root, providers=providers, bind=f"codex_apikey_{digest}")
            config = root / "config.toml"
            config.write_text(
                "[model_providers.codex_local_access]\n"
                'base_url = "https://example.invalid/v1"\n',
                encoding="utf-8",
            )
            buffer = StringIO()
            with redirect_stdout(buffer):
                rc = main(
                    [
                        "--cockpit-dir",
                        str(root),
                        "--codex-config",
                        str(config),
                        "--json",
                    ]
                )
        # A warning is reported but does not fail the check.
        self.assertEqual(rc, OK)
        self.assertIn("desktop-target-unrecognised", buffer.getvalue())

    def test_main_returns_findings_on_mismatch(self) -> None:
        providers = [
            {
                "id": "cmp_local",
                "name": "CPA (local 10909)",
                "baseUrl": "http://127.0.0.1:10909/v1",
                "apiKeys": [{"id": "k_stray", "apiKey": REMOTE_KEY}],
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_cockpit(root, providers=providers, bind="codex_apikey_" + "0" * 32)
            rc = main(["--cockpit-dir", str(root), "--json", *_hermetic_targets(root)])
        self.assertEqual(rc, FINDINGS)

    def test_main_returns_ok_on_clean_configuration(self) -> None:
        providers = [
            {
                "id": "cmp_local",
                "name": "CPA (local 10909)",
                "baseUrl": "http://127.0.0.1:10909/v1",
                "apiKeys": [{"id": "k_local", "apiKey": LOCAL_KEY}],
            }
        ]
        digest = cast(Any, MODULE["_md5"])(LOCAL_KEY)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_cockpit(root, providers=providers, bind=f"codex_apikey_{digest}")
            rc = main(["--cockpit-dir", str(root), "--json", *_hermetic_targets(root)])
        self.assertEqual(rc, OK)

    def test_main_cannot_check_without_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rc = main(["--cockpit-dir", tmp])
        self.assertEqual(rc, CANNOT_CHECK)

    def test_main_cannot_check_when_state_files_are_unreadable(self) -> None:
        # A missing registry existence-check passes, but a malformed or absent
        # instances file must exit 2 (CANNOT_CHECK). An unhandled exception
        # would exit 1, which callers read as FINDINGS: a crash masquerading
        # as a diagnosed configuration.
        providers = [
            {
                "id": "cmp_local",
                "name": "CPA (local 10909)",
                "baseUrl": "http://127.0.0.1:10909/v1",
                "apiKeys": [{"id": "k_local", "apiKey": LOCAL_KEY}],
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_cockpit(root, providers=providers, bind="codex_apikey_x")
            (root / "codex_instances.json").unlink()
            rc = main(["--cockpit-dir", str(root), "--json", *_hermetic_targets(root)])
        self.assertEqual(rc, CANNOT_CHECK)

    def test_main_cannot_check_when_the_registry_is_not_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "codex_model_providers.json").write_text(
                "not json", encoding="utf-8"
            )
            rc = main(["--cockpit-dir", str(root), "--json", *_hermetic_targets(root)])
        self.assertEqual(rc, CANNOT_CHECK)

    def test_main_cannot_check_when_the_registry_is_a_scalar(self) -> None:
        # A JSON scalar decodes fine but has neither list nor dict shape; the
        # report builder must refuse it instead of raising AttributeError.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "codex_model_providers.json", "just a string")
            rc = main(["--cockpit-dir", str(root), "--json", *_hermetic_targets(root)])
        self.assertEqual(rc, CANNOT_CHECK)


if __name__ == "__main__":
    unittest.main()
