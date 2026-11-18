#!/bin/sh
# HG-03 aliyun VM tuning: bbr + fq + large TCP buffers (replayable projection).
# Applied live on 2026-11-18 by the OFFICE session through the BWG jump;
# see docs/change-evidence/20261118-aliyun-vm-bbr-tuning-bwg-vnstat.md.
# Reversible: restore the dated /etc/sysctl.conf.bak-* or delete the HG-03
# marker block, then run `sysctl -p`.
set -eu

CONF=/etc/sysctl.conf
BACKUP="$CONF.bak-$(date +%Y%m%d-%H%M%S)"

cp "$CONF" "$BACKUP"
modprobe tcp_bbr 2>/dev/null || true
mkdir -p /etc/modules-load.d
echo tcp_bbr > /etc/modules-load.d/bbr.conf

if ! grep -q 'HG-03 VM tuning' "$CONF"; then
  cat >> "$CONF" <<'EOF'

# HG-03 VM tuning 2026-11-18 (reversible: delete block + restore backup above)
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
net.ipv4.tcp_rmem = 4096 87380 16777216
net.ipv4.tcp_wmem = 4096 65536 16777216
EOF
fi

sysctl -p >/dev/null
echo "cc=$(sysctl -n net.ipv4.tcp_congestion_control) qdisc=$(sysctl -n net.core.default_qdisc) rmem_max=$(sysctl -n net.core.rmem_max) wmem_max=$(sysctl -n net.core.wmem_max)"
