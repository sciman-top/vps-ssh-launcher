"""SOCKS5 no-auth CONNECT tunnel establishment."""

from __future__ import annotations

import socket
from contextlib import suppress

_SOCKS5_REPLY_MESSAGES = {
    0: "succeeded",
    1: "general SOCKS server failure",
    2: "connection not allowed by ruleset",
    3: "network unreachable",
    4: "host unreachable",
    5: "connection refused",
    6: "TTL expired",
    7: "command not supported",
    8: "address type not supported",
}


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    """Read exactly ``count`` bytes; a short read means the peer hung up."""
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            break
        data += chunk
    return data


def open_tunnel(
    proxy: tuple[str, int],
    dst_host: str,
    dst_port: int,
    timeout: float,
) -> socket.socket:
    """Open a TCP connection to ``dst_host`` through a SOCKS5 proxy.

    This implements no-auth SOCKS5 CONNECT without an additional dependency.
    Failures raise ``OSError`` so callers can reuse their network error and
    retry handling.
    """
    proxy_host, proxy_port = proxy
    sock = socket.create_connection((proxy_host, proxy_port), timeout=timeout)
    try:
        sock.settimeout(timeout)
        sock.sendall(b"\x05\x01\x00")
        greeting = _recv_exact(sock, 2)
        if len(greeting) < 2 or greeting[0] != 0x05 or greeting[1] != 0x00:
            raise OSError(
                f"SOCKS5 proxy {proxy_host}:{proxy_port} rejected the "
                f"no-auth handshake: {greeting!r}"
            )

        try:
            addr_bytes = socket.inet_pton(socket.AF_INET, dst_host)
            atyp, addr_field = 0x01, addr_bytes
        except OSError:
            try:
                addr_bytes = socket.inet_pton(socket.AF_INET6, dst_host)
                atyp, addr_field = 0x04, addr_bytes
            except OSError:
                atyp = 0x03
                try:
                    addr_field = dst_host.encode("idna")
                except UnicodeError as exc:
                    # Preserve the OSError contract so callers classify a
                    # malformed hostname with other network failures.
                    raise OSError(
                        f"cannot represent destination host {dst_host!r} as "
                        f"a SOCKS5 domain address: {exc}"
                    ) from exc

        if atyp == 0x03:
            if not addr_field or len(addr_field) > 255:
                raise OSError(
                    f"destination host {dst_host!r} cannot be represented as a "
                    "SOCKS5 domain address: encoded length must be between "
                    "1 and 255 bytes."
                )
            encoded_addr = bytes([len(addr_field)]) + addr_field
        else:
            encoded_addr = addr_field
        request = (
            b"\x05\x01\x00" + bytes([atyp]) + encoded_addr + dst_port.to_bytes(2, "big")
        )
        sock.sendall(request)

        reply = _recv_exact(sock, 4)
        if len(reply) < 4 or reply[0] != 0x05:
            raise OSError(
                f"SOCKS5 proxy {proxy_host}:{proxy_port} sent a malformed "
                f"CONNECT reply: {reply!r}"
            )
        code = reply[1]
        if code != 0x00:
            detail = _SOCKS5_REPLY_MESSAGES.get(code, f"reply code {code}")
            raise OSError(
                f"SOCKS5 proxy {proxy_host}:{proxy_port} failed to reach "
                f"{dst_host}:{dst_port}: {detail}"
            )
        # Drain the bound address so the stream stays aligned for SSH.
        truncated = (
            f"SOCKS5 proxy {proxy_host}:{proxy_port} closed the connection "
            "inside the CONNECT reply."
        )
        atyp_reply = reply[3]
        if atyp_reply == 0x01:
            remainder = 4 + 2
        elif atyp_reply == 0x04:
            remainder = 16 + 2
        elif atyp_reply == 0x03:
            # BND.ADDR for a domain is a length-prefixed variable-length
            # field; the length byte itself is part of the reply.
            length_field = _recv_exact(sock, 1)
            if not length_field:
                raise OSError(truncated)
            remainder = length_field[0] + 2
        else:
            raise OSError(
                f"SOCKS5 proxy {proxy_host}:{proxy_port} returned an unknown "
                f"address type {atyp_reply}."
            )
        if len(_recv_exact(sock, remainder)) < remainder:
            raise OSError(truncated)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return sock
    except Exception:
        with suppress(OSError):
            sock.close()
        raise
