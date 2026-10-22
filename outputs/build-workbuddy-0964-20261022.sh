#!/usr/bin/env bash
# Isolated build sandbox for workbuddy 0.9.64 (upstream v0.8.78 tarball
# + selected-account + account-failover patches). Same sandbox discipline as
# the 2026-10-19/2026-10-21 builds: golang:1.26-bookworm, --cpus=1
# --memory=768m, no production mounts, --rm auto-cleanup.
set -Eeuo pipefail
V=/root/cpa-workbuddy-upgrade-20261022
TREE=$V/cpa-multi-plugins-0.8.78
test -f "$TREE/plugins/workbuddy/go.mod"
test -f "$TREE/plugins/workbuddy/selected_mode_test.go"
test -f "$TREE/plugins/workbuddy/account_failover_test.go"
grep -q '0.9.64' "$TREE/plugins/workbuddy/VERSION"
echo "SANDBOX_PRE_OK tree=$TREE"
docker run --rm --cpus=1 --memory=768m -v "$TREE":/src -w /src/plugins/workbuddy golang:1.26-bookworm bash -c "go test -count=1 ./... && CGO_ENABLED=1 go build -buildmode=c-shared -o workbuddy.so . && sha256sum workbuddy.so && ls -la workbuddy.so"