#!/usr/bin/env bash
# Isolated branch verification for the 2026-10-13 TLS hardening. The doctor
# block is extracted from the repo source (the doctor is streamed, never
# persisted remotely); the wrapper block is extracted from the deployed file
# (sha-matched to the last -Apply receipt). Stub acme.sh + temp certs only;
# no production state touched beyond an idempotent nginx reload.
set -Eeuo pipefail
TMP=$(mktemp -d /tmp/tls-gate-verify.XXXXXX)
chmod 700 "$TMP"
trap 'rm -rf "$TMP"' EXIT

echo "==deployed artifact identity=="
sha256sum /usr/local/sbin/vps-launcher-v2ray-agent-renewtls.sh | cut -c1-64
echo "expected_wrapper_sha=74a55a7c3c1b20c534b4326e2a37db28d2e20ec66aaf97e117894cce8e1f5a7c"

echo "==A: doctor tls-cert block branches (from repo source)=="
cat > "$TMP/run_block.sh" <<'EOF'
DOCTOR_FAILED=0
mark_fail() { echo "$1=FAIL"; DOCTOR_FAILED=1; }
TLS_CERT_FILE="$1"
# 2026-10-13 incident: the vasma fork's RenewTLS renews on the literal day of
# expiry, so its heartbeat stays green while the gateway cert ages into a
# multi-hour TLS outage window. Gate on the served cert's remaining validity
# instead of renewal exit codes; 7 days leaves two wrapper cycles of slack.
TLS_CERT_FILE="$1"
TLS_CERT_END="$(openssl x509 -enddate -noout -in "$TLS_CERT_FILE" 2>/dev/null | cut -d= -f2)"
if [ -n "$TLS_CERT_END" ] && TLS_CERT_END_EPOCH="$(date -d "$TLS_CERT_END" +%s 2>/dev/null)"; then
  TLS_CERT_DAYS_LEFT=$(( ( TLS_CERT_END_EPOCH - $(date +%s) ) / 86400 ))
  echo "gateway_tls_cert_days_left=$TLS_CERT_DAYS_LEFT"
  if [ "$TLS_CERT_DAYS_LEFT" -ge 7 ]; then
    echo tls-cert=OK
  else
    echo tls-cert=EXPIRING
    mark_fail tls-cert
  fi
else
  echo tls-cert=UNREADABLE
  mark_fail tls-cert
fi

echo SIM_DOCTOR_FAILED=$DOCTOR_FAILED
EOF

openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1   -keyout "$TMP/k.pem" -out "$TMP/expiring.pem" -days 3 -nodes   -subj "/CN=*.sciman.top" >/dev/null 2>&1

echo "--A1 expiring cert (3d): expect EXPIRING + SIM_DOCTOR_FAILED=1"
bash "$TMP/run_block.sh" "$TMP/expiring.pem" | sed "s|$TMP|TMP|g"

echo "--A2 missing cert: expect UNREADABLE + SIM_DOCTOR_FAILED=1"
bash "$TMP/run_block.sh" "$TMP/missing.pem" | sed "s|$TMP|TMP|g"

echo "--A3 production cert: expect OK + SIM_DOCTOR_FAILED=0"
bash "$TMP/run_block.sh" /etc/v2ray-agent/tls/fq.sciman.top.crt

echo "==B: wrapper early-renewal block branches (from deployed file)=="
awk '/^ACME_HOME=/{flag=1} flag && /^\/bin\/bash \/etc\/v2ray-agent\/install\.sh/{exit} flag'   /usr/local/sbin/vps-launcher-v2ray-agent-renewtls.sh > "$TMP/early_block.sh"
sed -i 's|GATEWAY_CERT="/etc/v2ray-agent/tls/fq.sciman.top.crt"|GATEWAY_CERT="$1"|; s|ACME_HOME="/root/.acme.sh"|ACME_HOME="$2"|' "$TMP/early_block.sh"

mkdir -p "$TMP/acme_renew" "$TMP/acme_skip"
printf '#!/usr/bin/env bash
echo "simulated renewal: new cert installed"
cat /etc/v2ray-agent/tls/fq.sciman.top.crt > "$1.new"
mv "$1.new" "$1"
' > "$TMP/acme_renew/acme.sh"
printf '#!/usr/bin/env bash
echo "Simulating renewal: skip, next renewal time is far away"
' > "$TMP/acme_skip/acme.sh"
chmod 755 "$TMP/acme_renew/acme.sh" "$TMP/acme_skip/acme.sh"

cat > "$TMP/run_early.sh" <<'EOF'
set -Eeuo pipefail
LOG_FILE="$3"
EOF
cat "$TMP/early_block.sh" >> "$TMP/run_early.sh"
echo 'echo SIM_EARLY_EXIT=$?' >> "$TMP/run_early.sh"

cp /etc/v2ray-agent/tls/fq.sciman.top.crt "$TMP/cert_b1.pem"
echo "--B1 stub acme renews (file changes): expect RENEWTLS_EARLY log + exit 0"
bash "$TMP/run_early.sh" "$TMP/cert_b1.pem" "$TMP/acme_renew" "$TMP/early_b1.log"
echo "b1_log_lines:"
sed "s|$TMP|TMP|g" "$TMP/early_b1.log"

cp /etc/v2ray-agent/tls/fq.sciman.top.crt "$TMP/cert_b2.pem"
echo "--B2 stub acme skips (file unchanged): expect empty log + exit 0"
: > "$TMP/early_b2.log"
bash "$TMP/run_early.sh" "$TMP/cert_b2.pem" "$TMP/acme_skip" "$TMP/early_b2.log"
echo "b2_log_line_count=$(wc -l < "$TMP/early_b2.log")"

echo "==REAL_NGINX_RELOAD_CHECK=="
nginx -s reload && echo REAL_RELOAD_OK
echo BRANCH_VERIFY_DONE
