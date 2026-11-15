"""Contract and behaviour tests for scripts/nginx_tls_stealth.ps1.

The lane removes two observable fingerprints: the exact nginx build in the
Server header and any TLS port answering an unknown SNI with this host's
own certificate. It must stay read-only until -Apply, reload rather than
restart, and roll back if the real SNI stops working.

The 2026-11-15 review found zz subscription port 12378 answered any SNI with
CN=zz.sciman.top while this lane reported stealth=applied, because the port
list was derived from the REALITY fallback target alone. The derivation now
also follows the nginx snippets that serve those ports.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from script_validation_support import ScriptValidationMixin

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPTS / "nginx_tls_stealth.ps1"
DRIFT = SCRIPTS / "proxy_hardening_drift.ps1"
HELPER = SCRIPTS / "lib" / "proxy_tls_ports.py"


def _fact_block(script: Path) -> str:
    """Return the shared TLS port derivation source used by the lanes."""
    del script
    return HELPER.read_text(encoding="utf-8")


class NginxTlsStealthTests(ScriptValidationMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.text = SCRIPT.read_text(encoding="utf-8")

    def test_is_read_only_until_apply(self) -> None:
        text = self.text
        self.assertIn("LOCK_FILE", text)
        # A reload keeps existing tunnels alive; a restart would not.
        self.assertIn("systemctl reload nginx", text)
        self.assertNotIn("systemctl restart nginx", text)

    def test_ports_come_from_live_reality_config(self) -> None:
        text = HELPER.read_text(encoding="utf-8")
        # bwg falls back to 33387 and zz to 35622; deriving both cores avoids
        # a per-profile table that would rot.
        self.assertIn("realitySettings", text)
        self.assertIn("handshake", text)
        self.assertIn("serverNames", text)
        self.assertIn("facts=not-applicable", text)
        # One name mounted with two different certificates is the real
        # conflict; two names sharing a port is the alt-SNI design.
        self.assertIn("conflict sni=", text)
        self.assertIn("fallbacks", text)

    def test_ports_also_come_from_the_snippets_that_serve_them(self) -> None:
        text = HELPER.read_text(encoding="utf-8")
        # zz subscription port is not a REALITY target, so a fallback-only
        # derivation left it answering unknown SNI with the host certificate.
        self.assertIn("serving_ports", text)
        self.assertIn("excluded", text)
        self.assertIn("cert_by_name", text)
        # A name mounted with two different certificates is a real conflict;
        # the alt-SNI vhost shares a port with a different cert on purpose.
        self.assertIn("conflict sni=", text)

    def test_renders_version_and_unknown_sni_fixes(self) -> None:
        text = self.text
        self.assertIn("server_tokens off;", text)
        self.assertIn("ssl_reject_handshake on;", text)
        self.assertIn("ssl default_server;", text)
        self.assertIn("vps-launcher-stealth.conf", text)
        self.assertIn("listen [::]:%s ssl default_server;", text)
        self.assertIn("UNKNOWN_SNI_EXEMPT", text)
        self.assertIn("is_unknown_sni_exempt", text)

    def test_verifies_both_directions_before_and_after_apply(self) -> None:
        text = self.text
        self.assertIn("real_sni_broken", text)
        self.assertIn("unknown_sni_rejected", text)
        self.assertIn("unknown_sni_allowed", text)
        self.assertIn("server_version_leak", text)
        self.assertIn("STEALTH_VERIFY_FAILED", text)
        self.assertIn("nginx -t", text)


class DriftGateUnknownSniFloorTests(unittest.TestCase):
    """The gate needs a floor independent of the derived port list.

    A derived list can only cover what someone remembered to derive. The
    review that found port 12378 unguarded also showed the gate reporting
    stealth=applied on the same host, so an honest audit must probe the
    ports nginx actually serves.
    """

    def setUp(self) -> None:
        self.text = DRIFT.read_text(encoding="utf-8")

    def test_probes_every_listening_tls_port(self) -> None:
        text = self.text
        self.assertIn("nginx -T", text)
        self.assertIn("unknown_sni_answered_unguarded", text)
        self.assertIn("unknown_sni_floor=checked:", text)
        # The floor must feed the same lane verdict, not print and pass.
        floor = text.index("unknown_sni_answered_unguarded")
        self.assertGreater(text.index("stealth_bad=1", floor), floor)

    def test_floor_supports_a_declared_exception(self) -> None:
        self.assertIn("unknown_sni_answered_allowed", self.text)

    def test_declared_exception_is_honoured_in_both_checks(self) -> None:
        # 8443 (CPA gateway) and 28317 (CPAMC panel) are reached by IP literal,
        # so they answer 404/401 for the real name instead of a decoy page.
        # Listing them only in the floor left the per-port 200 rule red on
        # every pass, which made the whole lane permanently drifted.
        text = self.text
        self.assertIn("VPS_DRIFT_UNKNOWN_SNI_EXEMPT", text)
        self.assertIn("stealth_port_exempt", text)
        exempt = text.index("stealth_port_exempt")
        live = text.index("true_sni_broken")
        self.assertLess(exempt, live, "the exemption must skip the 200 rule")

    def test_floor_reads_one_dump_and_stays_read_only(self) -> None:
        text = self.text
        # `nginx -t` would make this read-only gate fail on a broken config
        # instead of reporting the break as drift.
        self.assertIn('floor_dump="$(nginx -T 2>/dev/null)"', text)
        self.assertNotIn("nginx -t", text)


class StealthPortDerivationTests(unittest.TestCase):
    """Run the lane own derivation against fixture trees."""

    def _run(self, xray: dict | None, snippets: dict[str, str]) -> str:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "xray").mkdir()
            (root / "sb").mkdir()
            (root / "conf.d").mkdir()
            if xray is not None:
                (root / "xray" / "07.json").write_text(
                    json.dumps(xray), encoding="utf-8"
                )
            for name, body in snippets.items():
                (root / "conf.d" / name).write_text(body, encoding="utf-8")
            script = root / "derive.py"
            script.write_text(_fact_block(SCRIPT), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    str(root / "xray"),
                    str(root / "sb"),
                    str(root / "conf.d"),
                    "vps-launcher-stealth,alone,reality-handshake",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            self.assertEqual(
                completed.returncode,
                0,
                f"derivation failed: {completed.stdout}{completed.stderr}",
            )
            return completed.stdout

    @staticmethod
    def _vhost(port: int, name: str, cert: str) -> str:
        return (
            "server {\n"
            f"    listen {port} ssl;\n"
            f"    server_name {name};\n"
            f"    ssl_certificate {cert};\n"
            "    location / { }\n"
            "}\n"
        )

    @staticmethod
    def _reality(target: str, names: list[str]) -> dict:
        return {
            "inbounds": [
                {
                    "port": 45987,
                    "streamSettings": {
                        "security": "reality",
                        "realitySettings": {
                            "target": target,
                            "serverNames": names,
                        },
                    },
                }
            ]
        }

    def test_fallback_target_is_covered(self) -> None:
        xray = self._reality(
            "zz.example.test:35622", ["zz.example.test", "static-zz.example.test"]
        )
        snippets = {
            "subscribe.conf": self._vhost(35622, "zz.example.test", "/tls/zz.crt"),
        }
        out = self._run(xray, snippets)
        self.assertIn("port=35622", out)
        self.assertIn("sni=zz.example.test", out)

    def test_subscription_port_without_a_reality_target_is_covered(self) -> None:
        # The 2026-11-15 defect: 12378 serves TLS but is not a REALITY target.
        xray = self._reality("zz.example.test:35622", ["zz.example.test"])
        snippets = {
            "subscribe.conf": self._vhost(35622, "zz.example.test", "/tls/zz.crt"),
            "cpa-443-http.conf": self._vhost(12378, "zz.example.test", "/tls/zz.crt"),
        }
        out = self._run(xray, snippets)
        self.assertIn("port=35622", out)
        self.assertIn("sni=zz.example.test", out)

    def test_alt_sni_vhost_does_not_invent_a_conflict(self) -> None:
        xray = self._reality(
            "zz.example.test:35622", ["zz.example.test", "static-zz.example.test"]
        )
        snippets = {
            "subscribe.conf": self._vhost(35622, "zz.example.test", "/tls/zz.crt"),
            "vps-launcher-alt-sni-sub.conf": self._vhost(
                12378, "static-zz.example.test", "/tls/wildcard.crt"
            ),
        }
        self._run(xray, snippets)

    def test_same_name_with_two_certificates_is_a_conflict(self) -> None:
        snippets = {
            "a.conf": self._vhost(33387, "fq.example.test", "/tls/a.crt"),
            "b.conf": self._vhost(9999, "fq.example.test", "/tls/b.crt"),
        }
        with self.assertRaises(AssertionError):
            self._run(None, snippets)

    def test_no_reality_and_no_snippets_is_not_applicable(self) -> None:
        self.assertIn("facts=not-applicable", self._run(None, {}))


if __name__ == "__main__":
    unittest.main()
