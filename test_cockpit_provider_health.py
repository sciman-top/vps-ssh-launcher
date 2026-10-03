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
from pathlib import Path
from typing import Any, cast

MODULE = runpy.run_path(
    str(Path(__file__).parent / "scripts" / "cockpit_provider_health.py")
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

OK = cast(int, MODULE["OK"])
FINDINGS = cast(int, MODULE["FINDINGS"])
CANNOT_CHECK = cast(int, MODULE["CANNOT_CHECK"])

LOCAL_KEY = "agt_codex_" + "A" * 34 + "IhCT"
REMOTE_KEY = "agt_gw_" + "B" * 33 + "fE_m"


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _minimal_cockpit(root: Path, *, providers: Any, bind: str, port: int = 10909) -> None:
    _write(root / "codex_model_providers.json", providers)
    _write(root / "codex_instances.json", {"instances": [], "defaultSettings": {"bindAccountId": bind}})
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
            check_key_endpoint_fit("CPA (local 10909)", "http://127.0.0.1:10909/v1", LOCAL_KEY)
        )
        self.assertIsNone(
            check_key_endpoint_fit(
                "fq.sciman.top", "https://fq.sciman.top:8443/abc/v1", REMOTE_KEY
            )
        )

    def test_vendor_key_is_not_flagged(self) -> None:
        self.assertIsNone(
            check_key_endpoint_fit("DeepSeek", "https://api.deepseek.com", "sk-" + "d" * 30)
        )

    def test_empty_key_reported(self) -> None:
        finding = check_key_endpoint_fit("X", "https://api.deepseek.com", "")
        self.assertIsNotNone(finding)
        assert finding is not None
        self.assertEqual(finding.code, "empty-key")


class BindAccountTests(unittest.TestCase):
    """The binding decides whether 10909 starts at all."""

    def test_prefix_marks_gateway_requirement(self) -> None:
        account, needs = parse_bind_account("__provider_gateway__:codex_apikey_deadbeef")
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
    def test_reads_only_the_local_access_block(self) -> None:
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
            _minimal_cockpit(root, providers=providers, bind="codex_apikey_" + "0" * 32)
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
        self.assertTrue(
            any(f.code == "gateway-not-required" for f in report.findings)
        )
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
                root / "codex_provider_gateway_sidecars" / "36218dcc01e4" / "config.json",
                {
                    "port": 10909,
                    "api-keys": [REMOTE_KEY],
                    "codex-api-key": [
                        {"base-url": "https://fq.sciman.top:8443/abc/v1", "api-key": REMOTE_KEY}
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
            _write(root / "codex_instances.json", {"instances": [], "defaultSettings": {"bindAccountId": None}})
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


class RenderAndCliTests(unittest.TestCase):
    def test_render_reports_clean_state(self) -> None:
        report = cast(Any, MODULE["Report"])()
        text = render(report, "http://localhost:10909/v1")
        self.assertIn("未发现配置层面的已知故障形态", text)

    def test_render_flags_non_10909_target(self) -> None:
        report = cast(Any, MODULE["Report"])()
        text = render(report, "https://fq.sciman.top:8443/abc/v1")
        self.assertIn("别名重写层被绕过", text)

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
            rc = main(["--cockpit-dir", str(root), "--json"])
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
            rc = main(["--cockpit-dir", str(root), "--json"])
        self.assertEqual(rc, OK)

    def test_main_cannot_check_without_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rc = main(["--cockpit-dir", tmp])
        self.assertEqual(rc, CANNOT_CHECK)


if __name__ == "__main__":
    unittest.main()
