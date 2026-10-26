set -Eeuo pipefail
V=/root/cpa-v8044-isolated-20261027
for pattern in 'identity_switch_on_429' 'account_max_concurrent' 'plugin registered'; do
  echo "pattern=$pattern"
  grep "$pattern" "$V/plugin-load.log" | head -4 || true
done
