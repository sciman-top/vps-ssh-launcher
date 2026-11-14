"""test_cpa_status_deploy_script.py - deployment script safety contract.

Text-assertion level only (the script performs real remote writes; its
runtime behaviour is exercised in live acceptance). The contract checks the
invariants the repo relies on: backup before overwrite, sha256 verification,
the nginx -t gate before reload with automatic restore, a documented
rollback path, reuse of the shared remote transport, and no embedded
secrets.
"""

from __future__ import annotations

import pathlib
import unittest

_SCRIPT = pathlib.Path(__file__).parents[1] / "scripts" / "cpa_status_deploy.ps1"


class CpaStatusDeployScriptTests(unittest.TestCase):
    text: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = _SCRIPT.read_text(encoding="utf-8")

    def test_uses_shared_remote_transport(self) -> None:
        # Wrappers must not hand-roll remote transfer (CRLF/base64/chunking
        # live in scripts/lib/project_environment.ps1).
        self.assertIn("Invoke-LauncherRemoteCommand", self.text)
        self.assertIn("project_environment.ps1", self.text)

    def test_push_backs_up_before_overwrite(self) -> None:
        backup_index = self.text.index("cp -a '$remoteFile' '$remoteFile.bak.")
        push_index = self.text.index("base64 -d > '$remoteFile'")
        self.assertLess(backup_index, push_index)

    def test_backup_stamp_is_expanded_by_the_remote_shell(self) -> None:
        # Regression: the stamp used to sit in a PowerShell single-quoted
        # segment as a literal "$(date ...)", so every deploy wrote the same
        # backup name and the second one destroyed the rollback point. The
        # stamp must be a remote shell expansion and the copy must be
        # verified before the overwrite.
        self.assertIn("stamp=`$(date +%Y%m%d%H%M%S)", self.text)
        self.assertNotIn("'$remoteFile.bak.`$(date", self.text)
        self.assertIn("test -f '$remoteFile.bak.'\"", self.text)
        self.assertIn("status page backup failed before overwrite", self.text)

    def test_install_restarts_an_already_active_unit(self) -> None:
        # `systemctl enable --now` does nothing to a running unit, so the
        # source push alone left the old process serving the old code.
        self.assertIn("systemctl restart cpa-status.service", self.text)
        self.assertNotIn("enable --now cpa-status.service", self.text)
        self.assertIn("CPA_STATUS_SERVICE_SOURCE=", self.text)

    def test_push_verifies_sha256(self) -> None:
        self.assertIn("sha256 mismatch after push", self.text)
        self.assertIn("Get-FileHash", self.text)

    def test_nginx_gate_reloads_only_after_successful_test(self) -> None:
        gate_index = self.text.index("if nginx -t 2>/dev/null")
        reload_index = self.text.index("systemctl reload nginx && echo RELOADED")
        self.assertLess(gate_index, reload_index)
        self.assertIn("RESTORED", self.text)

    def test_rollback_removes_service_dir_and_restores_nginx(self) -> None:
        self.assertIn("systemctl disable --now cpa-status.service", self.text)
        self.assertIn("rm -rf '$remoteDir'", self.text)
        self.assertIn("CPA_STATUS_ROLLBACK=DONE", self.text)

    def test_service_unit_hardening_and_loopback(self) -> None:
        self.assertIn("--remote-serve", self.text)
        self.assertIn("servicePort = 8319", self.text)
        self.assertIn("NoNewPrivileges=true", self.text)
        self.assertIn("ProtectSystem=full", self.text)
        self.assertIn("ProtectHome=read-only", self.text)
        self.assertNotIn("0.0.0.0", self.text)

    def test_no_embedded_secrets(self) -> None:
        # The management key lives in /root/.cpa-management-key on the host;
        # the deploy script must not carry any key material or htpasswd.
        self.assertNotIn("sk-", self.text)
        self.assertIn("/root/.cpa-management-key", self.text)
        self.assertNotIn("htpasswd contents", self.text.lower())

    def test_audit_is_default_mode(self) -> None:
        self.assertIn("Invoke-StatusAudit", self.text)
        audit_index = self.text.index("if (-not $Deploy -and -not $ConfigureNginx)")
        self.assertIn("Invoke-StatusAudit", self.text[audit_index:])


if __name__ == "__main__":
    unittest.main()
