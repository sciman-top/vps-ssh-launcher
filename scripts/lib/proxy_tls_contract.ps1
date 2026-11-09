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
