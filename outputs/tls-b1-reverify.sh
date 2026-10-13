#!/usr/bin/env bash
# B1 rerun: proper renewal stub (bakes the temp cert path so the file really
# changes), proving the changed-file branch fires reload + RENEWTLS_EARLY.
set -Eeuo pipefail
TMP=$(mktemp -d /tmp/tls-b1-verify.XXXXXX)
chmod 700 "$TMP"
trap 'rm -rf "$TMP"' EXIT

awk '/^ACME_HOME=/{flag=1} flag && /^\/bin\/bash \/etc\/v2ray-agent\/install\.sh/{exit} flag'   /usr/local/sbin/vps-launcher-v2ray-agent-renewtls.sh > "$TMP/early_block.sh"
sed -i 's|GATEWAY_CERT="/etc/v2ray-agent/tls/fq.sciman.top.crt"|GATEWAY_CERT="$1"|; s|ACME_HOME="/root/.acme.sh"|ACME_HOME="$2"|' "$TMP/early_block.sh"

TARGET="$TMP/cert_b1.pem"
mkdir -p "$TMP/acme_renew"
printf '#!/usr/bin/env bash
echo "simulated renewal: new cert installed"
cat /etc/v2ray-agent/tls/fq.sciman.top.key > "%s.new"
mv "%s.new" "%s"
'   "$TARGET" "$TARGET" "$TARGET" > "$TMP/acme_renew/acme.sh"
chmod 755 "$TMP/acme_renew/acme.sh"

cat > "$TMP/run_early.sh" <<'EOF'
set -Eeuo pipefail
LOG_FILE="$3"
EOF
cat "$TMP/early_block.sh" >> "$TMP/run_early.sh"
echo 'echo SIM_EARLY_EXIT=$?' >> "$TMP/run_early.sh"

cp /etc/v2ray-agent/tls/fq.sciman.top.crt "$TARGET"
bash "$TMP/run_early.sh" "$TARGET" "$TMP/acme_renew" "$TMP/early_b1.log"
echo "b1_log_lines:"
sed "s|$TMP|TMP|g" "$TMP/early_b1.log"
if grep -q "RENEWTLS_EARLY" "$TMP/early_b1.log"; then echo B1_RELOAD_BRANCH_FIRED; else echo B1_RELOAD_BRANCH_MISSING; exit 1; fi
