set -Eeuo pipefail
DIR=/opt/cliproxyapi
echo '== access log last sol-input lines (status/upstream/route fields only) =='
docker exec cli-proxy-api sh -c 'grep -a "gpt-6.1-sol-input" /app/logs/*.log 2>/dev/null | tail -6' | sed -E 's/[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}/CLIENT_IP_MASKED/g' | cut -c1-220
echo '== newest error dumps =='
ls -t "$DIR"/auth/logs/error-*.log 2>/dev/null | head -3
echo '== error dump non-sensitive headers =='
for f in $(ls -t "$DIR"/auth/logs/error-*.log 2>/dev/null | head -2); do
  echo "-- $f"
  grep -aE '"status"|"error"|"type"|"code"|status:|upstream' "$f" | head -8 | cut -c1-200
done
