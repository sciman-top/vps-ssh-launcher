"""Focused contract checks for the reviewed BWG-hosted frp relay."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.remote import frp_bwg_relay


def _valid_config() -> str:
    return (
        'serverAddr = "8.163.41.198"\n'
        "serverPort = 27000\n"
        'auth.method = "token"\n'
        'auth.token = "fixture-value-not-live"\n'
        "transport.tls.enable = true\n"
        "loginFailExit = false\n"
        "\n[[proxies]]\n"
        'name = "relay-52443"\n'
        'type = "tcp"\n'
        'localIP = "127.0.0.1"\n'
        "localPort = 443\n"
        "remotePort = 52443\n"
    )


def test_reviewed_config_is_one_exact_tcp_mapping() -> None:
    assert frp_bwg_relay._parse_proxy(_valid_config()) == {  # noqa: SLF001
        "name": "relay-52443",
        "type": "tcp",
        "localIP": "127.0.0.1",
        "localPort": "443",
        "remotePort": "52443",
    }


def test_nonloopback_landing_is_rejected() -> None:
    with pytest.raises(frp_bwg_relay.RelayCheckError):
        frp_bwg_relay._parse_proxy(  # noqa: SLF001
            _valid_config().replace(
                'localIP = "127.0.0.1"', 'localIP = "144.34.229.116"'
            )
        )


def test_config_contract_reports_failures_instead_of_a_constant_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    # The entry point used to print FRP_CONFIG_CONTRACT=PASS without reading
    # anything, so a widened proxy scope or a malformed token still reported a
    # green contract.
    protected = tmp_path / "frpc.toml"
    protected.write_text(_valid_config(), encoding="utf-8")
    monkeypatch.setattr(frp_bwg_relay, "PROTECTED_PATH", str(protected))
    monkeypatch.setattr(
        frp_bwg_relay.sys, "argv", ["frp_bwg_relay.py", "config-contract"]
    )
    frp_bwg_relay.main()
    assert "FRP_CONFIG_CONTRACT=PASS" in capsys.readouterr().out

    protected.write_text(
        _valid_config().replace("remotePort = 52443", "remotePort = 52444"),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as exit_info:
        frp_bwg_relay.main()
    assert exit_info.value.code == 1
    assert "FRP_CONFIG_CONTRACT=FAIL" in capsys.readouterr().out


def test_config_contract_fails_closed_on_a_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(
        frp_bwg_relay, "PROTECTED_PATH", str(tmp_path / "absent-frpc.toml")
    )
    monkeypatch.setattr(
        frp_bwg_relay.sys, "argv", ["frp_bwg_relay.py", "config-contract"]
    )
    with pytest.raises(SystemExit) as exit_info:
        frp_bwg_relay.main()
    assert exit_info.value.code == 1
    assert "FRP_CONFIG=UNREADABLE" in capsys.readouterr().out


def test_bwg_and_aliyun_modes_resolve_a_client_without_a_phantom_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Both modes used to import scripts._remote_ops, which never existed in this
    # repository (git log --all finds no such path), so they died with
    # ImportError before opening a socket. The launcher now supplies the same
    # strict, host-key-pinned connection.
    import sys
    import types

    calls: list[tuple[str, object]] = []

    fake_config = types.ModuleType("vps_ssh_launcher.config")
    fake_config.load_config = lambda path: {  # type: ignore[attr-defined]
        "profiles": {"bwg": {"host": "10.0.0.1", "user": "root", "password": "s"}}
    }
    fake_config.validate_profile = lambda entry, name: None  # type: ignore[attr-defined]

    def _namespace(entry: dict, **kwargs: object) -> str:
        calls.append(("namespace", kwargs))
        return "ns"

    fake_config.build_connect_namespace = _namespace  # type: ignore[attr-defined]

    class _Client:
        def close(self) -> None:
            calls.append(("close", None))

    def _connect(ns: object) -> _Client:
        calls.append(("connect", ns))
        return _Client()

    fake_connection = types.ModuleType("vps_ssh_launcher.connection")
    fake_connection.connect_with_retry = _connect  # type: ignore[attr-defined]
    fake_connection.classify_connection_error = lambda exc, **kw: types.SimpleNamespace(  # type: ignore[attr-defined]
        category="connect_error"
    )
    fake_package = types.ModuleType("vps_ssh_launcher")
    fake_package.config = fake_config  # type: ignore[attr-defined]
    fake_package.connection = fake_connection  # type: ignore[attr-defined]

    saved = {
        name: sys.modules.get(name)
        for name in (
            "vps_ssh_launcher",
            "vps_ssh_launcher.config",
            "vps_ssh_launcher.connection",
        )
    }
    sys.modules["vps_ssh_launcher"] = fake_package
    sys.modules["vps_ssh_launcher.config"] = fake_config
    sys.modules["vps_ssh_launcher.connection"] = fake_connection
    # Never depend on whatever happens to be staged in %TEMP%: an operator who
    # ran the checker once would otherwise turn this assertion into a coin flip.
    monkeypatch.setattr(
        frp_bwg_relay,
        "_reviewed_target_path",
        lambda: tmp_path / "absent-review-target.json",
    )
    try:
        with pytest.raises(frp_bwg_relay.RelayCheckError) as missing:
            frp_bwg_relay._open_reviewed_client("bwg")  # noqa: SLF001
        assert str(missing.value) == "PRIVATE_RELAY_TARGET=MISSING"
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def test_reviewed_client_never_inherits_a_profile_host_key_opt_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A profile may not downgrade the audit to TOFU."""
    import sys
    import types

    observed: dict = {}

    fake_config = types.ModuleType("vps_ssh_launcher.config")
    fake_config.load_config = lambda path: {  # type: ignore[attr-defined]
        "profiles": {
            "bwg": {
                "host": "10.0.0.1",
                "user": "root",
                "password": "s",
                "strict_host_key_checking": False,
            }
        }
    }
    fake_config.validate_profile = lambda entry, name: None  # type: ignore[attr-defined]
    fake_config.build_connect_namespace = (  # type: ignore[attr-defined]
        lambda entry, **kwargs: observed.update(kwargs) or "ns"
    )

    class _Client:
        def close(self) -> None:
            pass

    fake_connection = types.ModuleType("vps_ssh_launcher.connection")
    fake_connection.connect_with_retry = lambda ns: _Client()  # type: ignore[attr-defined]
    fake_connection.classify_connection_error = lambda exc, **kw: types.SimpleNamespace(  # type: ignore[attr-defined]
        category="connect_error"
    )
    fake_package = types.ModuleType("vps_ssh_launcher")
    fake_package.config = fake_config  # type: ignore[attr-defined]
    fake_package.connection = fake_connection  # type: ignore[attr-defined]

    target = tmp_path / "frp-relay-review-target.json"
    target.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(frp_bwg_relay, "_reviewed_target_path", lambda: target)

    saved = {
        name: sys.modules.get(name)
        for name in (
            "vps_ssh_launcher",
            "vps_ssh_launcher.config",
            "vps_ssh_launcher.connection",
        )
    }
    sys.modules["vps_ssh_launcher"] = fake_package
    sys.modules["vps_ssh_launcher.config"] = fake_config
    sys.modules["vps_ssh_launcher.connection"] = fake_connection
    try:
        frp_bwg_relay._open_reviewed_client("bwg")  # noqa: SLF001
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    assert observed["strict_host_key_checking"] is True


def test_no_phantom_module_import_remains() -> None:
    # Pins the actual defect: the checker may not import a module that does not
    # exist in this repository. Only executable lines count -- the docstring is
    # allowed to explain the history.
    import ast
    import importlib.util

    tree = ast.parse(Path(frp_bwg_relay.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert "scripts._remote_ops" not in imported
    assert importlib.util.find_spec("scripts._remote_ops") is None


def test_health_check_fails_closed_on_inactive_service() -> None:
    class Branch:
        def recv_exit_status(self) -> int:
            return 0

    class Stream:
        channel = Branch()

        def read(self) -> bytes:
            return b""

    class Stdout(Stream):
        def read(self) -> bytes:
            return b"inactive\n"

    class Client:
        def exec_command(
            self, command: str, timeout: int
        ) -> tuple[object, Stream, object]:
            del command, timeout
            return object(), Stdout(), Stream()

    with pytest.raises(frp_bwg_relay.RelayCheckError):
        frp_bwg_relay.check_bwg(Client())
