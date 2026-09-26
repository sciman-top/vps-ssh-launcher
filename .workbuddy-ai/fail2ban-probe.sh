echo "== effective ignoreip =="
fail2ban-client get cpa-gateway ignoreip 2>&1 || true
echo "== effective maxretry/findtime/bantime =="
for k in maxretry findtime bantime; do printf '%s=' "$k"; fail2ban-client get cpa-gateway "$k" 2>&1 || true; done
echo "== deployed jail file =="
cat /etc/fail2ban/jail.d/cpa-gateway.conf
echo "== default ignoreip in jail.conf/local =="
grep -Rhn '^[[:space:]]*ignoreip' /etc/fail2ban/jail.conf /etc/fail2ban/jail.local /etc/fail2ban/jail.d/*.conf 2>/dev/null || echo "(no ignoreip directive found)"
echo "== currently banned =="
fail2ban-client get cpa-gateway banip 2>&1 || true
