set -Eeuo pipefail
V=/root/cpa-workbuddy-0973-20261027
ps -eo comm,etime,args | awk '/[g]o vet|[g]o test|[g]o build|[c]ompile/ {print $1,$2}' | head -10
docker ps --format '{{.Image}} {{.Status}}' | head -5
test ! -f "$V/plugins/workbuddy/workbuddy.so" || sha256sum "$V/plugins/workbuddy/workbuddy.so"
