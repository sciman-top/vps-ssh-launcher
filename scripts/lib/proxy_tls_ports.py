#!/usr/bin/env python3
"""Derive nginx TLS ports and their REALITY fallback names.

The three proxy lanes use this same contract.  The script emits line-oriented
facts so shell callers can consume only the fields they need:

    port=<number>          TLS port that needs an unknown-SNI guard
    sni=<name>             fallback name for the preceding port
    serve=<name>|<port>    name/port mounted by an nginx serving vhost
    facts=not-applicable   no relevant TLS surface was found
    facts=invalid          an input file could not be read or was inconsistent

Exit status 4 means the inputs are invalid.  The caller owns any remote
projection; this file has no network or filesystem side effects beyond reading
its four input directories.
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys
from collections.abc import Iterable

SERVER_BLOCK_RE = re.compile(r"server\s*\{(.*?)\n\}", re.DOTALL)
LISTEN_RE = re.compile(r"listen\s+(?:[^;\s]*:)?(\d+)\b[^;]*;")
CERT_RE = re.compile(r"ssl_certificate\s+([^;]+);")
SERVER_NAME_RE = re.compile(r"server_name\s+([^;]+);")


def split_port(value: object) -> int:
    text = str(value or "").strip()
    if text.startswith("[") and "]" in text:
        text = text[text.index("]") + 1 :]
    if text.count(":") != 1:
        return 0
    try:
        return int(text.split(":", 1)[1] or 0)
    except ValueError:
        return 0


def add_fallback(fallbacks: dict[int, str], port: object, name: object) -> None:
    if isinstance(port, bool) or not isinstance(port, int) or port <= 0:
        return
    fallback_name = str(name or "").strip()
    if fallback_name:
        fallbacks.setdefault(port, fallback_name)


def iter_json(directory: str) -> Iterable[dict]:
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            raise ValueError(path) from None
        if isinstance(data, dict):
            yield data


def read_snippets(
    directory: str, excluded: set[str]
) -> tuple[dict[int, str], dict[str, set[int]], set[int], dict[str, str]]:
    serving_ports: dict[int, str] = {}
    name_ports: dict[str, set[int]] = {}
    reject_defaults: set[int] = set()
    cert_by_name: dict[str, str] = {}

    for path in sorted(glob.glob(os.path.join(directory, "*.conf"))):
        if os.path.splitext(os.path.basename(path))[0] in excluded:
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
        except OSError:
            raise ValueError(path) from None
        for match in SERVER_BLOCK_RE.finditer(text):
            body = match.group(1)
            ports = [int(value) for value in LISTEN_RE.findall(body)]
            if "default_server" in body and "ssl_reject_handshake on" in body:
                reject_defaults.update(port for port in ports if port > 0)

            certificate = CERT_RE.search(body)
            names_match = SERVER_NAME_RE.findall(body)
            if certificate is None or not names_match:
                continue
            cert_path = certificate.group(1).strip()
            names = names_match[0].split()
            for name in names:
                previous = cert_by_name.setdefault(name, cert_path)
                if previous != cert_path:
                    raise ValueError(
                        f"conflict sni={name} cert={previous} vs {cert_path}"
                    )
                name_ports.setdefault(name, set()).update(ports)
            primary_name = names[0]
            for port in ports:
                if port > 0:
                    serving_ports.setdefault(port, primary_name)
    return serving_ports, name_ports, reject_defaults, cert_by_name


def read_reality(directory: str, fallbacks: dict[int, str]) -> None:
    for data in iter_json(directory):
        for inbound in data.get("inbounds", []) or []:
            if not isinstance(inbound, dict):
                continue
            settings = (inbound.get("streamSettings") or {}).get(
                "realitySettings"
            ) or {}
            if settings:
                names = [
                    str(name) for name in settings.get("serverNames", []) or [] if name
                ]
                add_fallback(
                    fallbacks,
                    split_port(settings.get("target") or settings.get("dest") or ""),
                    names[0] if names else "",
                )


def read_sing_box(directory: str, fallbacks: dict[int, str]) -> None:
    for data in iter_json(directory):
        for inbound in data.get("inbounds", []) or []:
            if not isinstance(inbound, dict):
                continue
            tls = inbound.get("tls") or {}
            reality = tls.get("reality") or {}
            if reality and reality.get("enabled"):
                handshake = reality.get("handshake") or {}
                add_fallback(
                    fallbacks,
                    handshake.get("server_port"),
                    tls.get("server_name"),
                )


def emit(
    serving_ports: dict[int, str],
    name_ports: dict[str, set[int]],
    reject_defaults: set[int],
    fallbacks: dict[int, str],
) -> None:
    # REALITY names are authoritative when a snippet and a fallback share a
    # port.  Serving-only ports still need coverage and use their primary name.
    ports = dict(serving_ports)
    ports.update(fallbacks)
    ports = {port: name for port, name in ports.items() if port not in reject_defaults}
    if not ports:
        print("facts=not-applicable")
        return
    for port, name in sorted(ports.items()):
        print(f"port={port}")
        print(f"sni={name}")
    for name in sorted(name_ports):
        for port in sorted(name_ports[name]):
            print(f"serve={name}|{port}")


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        print(
            "usage: proxy_tls_ports.py XRAY_DIR SING_BOX_DIR NGINX_SNIPPET_DIR EXCLUDED",
            file=sys.stderr,
        )
        return 2
    xray_dir, sing_box_dir, snippet_dir, excluded = argv[1:]
    try:
        serving_ports, name_ports, reject_defaults, _ = read_snippets(
            snippet_dir, {name for name in excluded.split(",") if name}
        )
        fallbacks: dict[int, str] = {}
        read_reality(xray_dir, fallbacks)
        read_sing_box(sing_box_dir, fallbacks)
        emit(serving_ports, name_ports, reject_defaults, fallbacks)
    except ValueError as error:
        print(f"facts=invalid: {error}", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
