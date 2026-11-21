# Single source of truth for the TLS 1.2 cipher list every managed proxy vhost
# presents.
#
# The REALITY fallback target (vasma subscribe.conf) already pins an explicit
# list, while the lanes this repo projects (CDN origin, alternate-SNI vhosts,
# XHTTP front) left ssl_ciphers unset and inherited the nginx/OpenSSL default.
# Measured on 2026-11-09, that put two different TLS parameter sets behind
# ports of the same hostname:
#
#   fq.sciman.top:33387   3 suites (GCM only)
#   static.sciman.top:33387 / cdn.sciman.top:2087 / fq.sciman.top:10094
#                         5 suites (+ ECDHE-ECDSA-AES128-SHA256,
#                                     ECDHE-ECDSA-AES256-SHA384)
#
# An active prober only needs one ClientHello per IP:port to group them, and the
# exact-hostname fallback is the vhost REALITY relays unauthenticated traffic
# to - so the lane ports and the fallback must look like one deployment.
#
# The list is byte-identical to the one vasma writes into subscribe.conf, which
# keeps the fleet on the policy the fallback already enforces. It still
# negotiates for every TLS 1.2/1.3 client that supports GCM or ChaCha20; only
# CBC-only TLS 1.2 clients are dropped, and those already cannot complete an
# HTTP/2 handshake on this host.
$ProxyTlsCipherSuite = 'TLS13_AES_128_GCM_SHA256:TLS13_AES_256_GCM_SHA384:TLS13_CHACHA20_POLY1305_SHA256:ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305'

function Get-ProxyTlsCipherSuite {
  # Validated at the source so a tampered or truncated literal cannot be
  # rendered into an nginx config and only fail at `nginx -t` on the host.
  if ($ProxyTlsCipherSuite -notmatch '^[A-Za-z0-9_-]+(:[A-Za-z0-9_-]+)*$') {
    throw "Invalid proxy TLS cipher suite literal: $ProxyTlsCipherSuite"
  }
  return $ProxyTlsCipherSuite
}

function Get-VerifiedLocalTlsCaPython {
  <#
  Generate the remote CA-selection helper used by loopback TLS checks. The
  helper takes the certificate path from the caller, checks its hostname and
  trusts only a system bundle or an explicitly configured, absolute bundle.
  Callers must use the output instead of `curl -k`; unverified transport is
  not a valid health or drift result.
  #>
  return @'
import os
import re
import ssl
import subprocess
import sys
import tempfile

CA_CANDIDATES = (
    "/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt",
)


def verified_tls_curl_args(host, cert_file):
    """Verify a locally served hostname using its configured trust bundle."""
    if (
        not isinstance(host, str)
        or re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", host)
        is None
    ):
        raise RuntimeError("unexpected local TLS hostname")
    if not isinstance(cert_file, str) or not cert_file:
        raise RuntimeError("local TLS certificate is unavailable")
    configured = os.environ.get("VPS_SSH_LAUNCHER_TLS_CA_BUNDLE", "")
    verify_root = os.environ.get("VPS_LOCAL_TLS_VERIFY_DIR", "")
    if configured and (
        " " in configured
        or any(char in configured for char in "\r\n'\"\\")
    ):
        raise RuntimeError("configured TLS CA bundle path is not shell-safe")
    if configured and not os.path.isabs(configured):
        raise RuntimeError("configured TLS CA bundle path is not shell-safe")
    if configured and not os.path.isfile(configured):
        raise RuntimeError("configured local CA bundle is unavailable")
    if not os.path.isfile(cert_file[:-4] + ".key"):
        raise RuntimeError("local TLS private key is unavailable")
    try:
        if not verify_root:
            raise RuntimeError("no private TLS verification directory")
        if (
            not os.path.isabs(verify_root)
            or " " in verify_root
            or any(char in verify_root for char in "\r\n'\"\\")
            or not os.path.isdir(verify_root)
        ):
            raise RuntimeError("private TLS verification directory is unavailable")
        # Keep private scratch inside the mode-700 wrapper directory, not the
        # shared system temp root. The caller removes this root after curl.
        work = tempfile.mkdtemp(dir=verify_root, prefix="tls.")
        try:
            # A CA bundle is not a server certificate. curl also accepts DER
            # here, so normalize the leaf and verify its name before asking any
            # bundle to authenticate it.
            leaf = os.path.join(work, "leaf.pem")
            checked = subprocess.run(
                [
                    "openssl", "x509", "-in", cert_file, "-out", leaf,
                    "-noout", "-checkhost", host,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if checked.returncode != 0:
                raise RuntimeError("local TLS certificate does not cover the host")
            # A copied trust bundle is passed only to curl, never used as a
            # server certificate or key. Keep it in this private directory.
            if not configured:
                source = next(
                    (path for path in CA_CANDIDATES if os.path.isfile(path)), ""
                )
                if not source:
                    raise RuntimeError("no local CA certificate bundle")
                trusted_bundle = os.path.join(work, "trusted.pem")
                with open(source, "rb") as input_file, open(
                    trusted_bundle, "wb"
                ) as output_file:
                    while True:
                        chunk = input_file.read(1 << 20)
                        if not chunk:
                            break
                        output_file.write(chunk)
                    output_file.flush()
                    os.fsync(output_file.fileno())
                bundle = trusted_bundle
            else:
                bundle = os.path.join(work, "configured-trust.pem")
                with open(configured, "rb") as input_file, open(
                    bundle, "wb"
                ) as output_file:
                    while True:
                        chunk = input_file.read(1 << 20)
                        if not chunk:
                            break
                        output_file.write(chunk)
                    output_file.flush()
                    os.fsync(output_file.fileno())
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = True
            context.verify_mode = ssl.CERT_REQUIRED
            context.load_verify_locations(cafile=bundle)
            if not context.get_ca_certs():
                raise RuntimeError("no local CA certificates are loaded")
            key_check = subprocess.run(
                [
                    "openssl", "pkey", "-in", cert_file[:-4] + ".key",
                    "-pubout", "-outform", "DER",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            cert_check = subprocess.run(
                ["openssl", "x509", "-in", cert_file, "-pubkey", "-noout"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            cert_der = subprocess.run(
                ["openssl", "pkey", "-pubin", "-outform", "DER"],
                input=cert_check.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
            ) if cert_check.returncode == 0 else None
            if (
                key_check.returncode != 0
                or cert_der is None
                or cert_der.returncode != 0
                or key_check.stdout != cert_der.stdout
            ):
                raise RuntimeError("local TLS certificate and key do not match")
            return "--cacert", os.path.abspath(bundle)
        except Exception:
            import shutil

            shutil.rmtree(work, ignore_errors=True)
            raise
    except (OSError, ValueError, ssl.SSLError, subprocess.SubprocessError) as exc:
        raise RuntimeError("local TLS CA verification failed") from exc
    raise RuntimeError(f"no CA bundle verifies the deployment certificate for {host}")


def verified_pinned_tls_curl_args(host, cert_candidates=()):
    """Verify a locally served hostname against its actual deployment leaf."""
    import os

    for candidate in cert_candidates:
        if candidate and os.path.isfile(candidate):
            return verified_tls_curl_args(host, candidate)
    raise RuntimeError("no pinned local deployment certificate is available")


def verified_live_tls_curl_args(host, port=None):
    """Verify a live loopback endpoint or a pinned nginx certificate."""
    import socket

    if (
        not isinstance(host, str)
        or re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", host)
        is None
    ):
        raise RuntimeError("unexpected local TLS hostname")
    configured = os.environ.get("VPS_SSH_LAUNCHER_TLS_CA_BUNDLE", "")
    verify_root = os.environ.get("VPS_LOCAL_TLS_VERIFY_DIR", "")
    if configured and not os.path.isabs(configured):
        raise RuntimeError("configured TLS CA bundle path is not shell-safe")
    if configured and (
        " " in configured
        or any(char in configured for char in "\r\n'\"\\")
        or not os.path.isfile(configured)
    ):
        raise RuntimeError("configured TLS CA bundle is not usable")
    if (
        not verify_root
        or not os.path.isabs(verify_root)
        or " " in verify_root
        or any(char in verify_root for char in "\r\n'\"\\")
        or not os.path.isdir(verify_root)
    ):
        raise RuntimeError("private TLS verification directory is unavailable")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    if port is None:
        return verified_tls_curl_args(host, sys.argv[1])
    try:
        numeric_port = int(port)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("invalid local TLS port") from exc
    if not 1 <= numeric_port <= 65535:
        raise RuntimeError("invalid local TLS port")
    source_bundle = configured or next(
        (candidate for candidate in CA_CANDIDATES if os.path.isfile(candidate)), ""
    )
    if not source_bundle:
        raise RuntimeError("no local CA certificate bundle")
    work = tempfile.mkdtemp(dir=verify_root, prefix="live.")
    try:
        # curl reads the normalized bundle from private storage, rather than a
        # file whose path is exposed through the streamed doctor environment.
        bundle = os.path.join(work, "trusted.pem")
        with open(source_bundle, "rb") as source, open(bundle, "wb") as output:
            while True:
                chunk = source.read(1 << 20)
                if not chunk:
                    break
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        context.load_verify_locations(cafile=bundle)
        if not context.get_ca_certs():
            raise RuntimeError("no local CA certificates are loaded")
    except BaseException:
        import shutil

        shutil.rmtree(work, ignore_errors=True)
        raise
    try:
        socket_handle = socket.create_connection(("127.0.0.1", numeric_port), timeout=5)
        with context.wrap_socket(socket_handle, server_hostname=host) as live:
            live_leaf = live.getpeercert(binary_form=True)
        if not live_leaf:
            raise RuntimeError("live TLS endpoint has no certificate")
        leaf_file = os.path.join(work, "live.der")
        with open(leaf_file, "wb") as output:
            output.write(live_leaf)
            output.flush()
            os.fsync(output.fileno())
        normalized = subprocess.run(
            ["openssl", "x509", "-in", leaf_file, "-noout", "-checkhost", host],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if normalized.returncode != 0:
            raise RuntimeError("live TLS certificate does not cover the host")
    except BaseException:
        import shutil

        shutil.rmtree(work, ignore_errors=True)
        raise
    return "--cacert", bundle


def verified_system_tls_curl_args(host):
    """Select a CA for one public leaf while retaining curl's hostname check."""
    import os
    import ssl

    if (
        not isinstance(host, str)
        or re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", host)
        is None
    ):
        raise RuntimeError("unexpected public TLS hostname")
    configured = os.environ.get("VPS_SSH_LAUNCHER_TLS_CA_BUNDLE", "")
    if configured:
        if (
            not os.path.isabs(configured)
            or " " in configured
            or any(char in configured for char in "\r\n'\"\\")
            or not os.path.isfile(configured)
        ):
            raise RuntimeError("configured TLS CA bundle is not usable")
        return "--cacert", os.path.abspath(configured)
    context = ssl.create_default_context()
    if not context.get_ca_certs():
        raise RuntimeError("no system CA certificates are loaded")
    return ()


# Older PowerShell releases normalize here-string line endings to CRLF, and
# the helper reaches remote Linux shells where CRLF is rejected (2026-09-15
# guardrails CRLF incident). Pin LF at the source so every consumer projects
# identical bytes on every PowerShell build.
'@.Replace("`r`n", "`n").Replace("`r", "`n")
}
