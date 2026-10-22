#!/usr/bin/env bash
# Isolated build sandbox for workbuddy 0.9.64-local + truncation guard.
#
# Tree: /root/cpa-workbuddy-truncation-20261022 (v0.8.78 source + selected
# account + account-failover + workbuddy-v0.8.78-truncation-guard.patch).
# Same sandbox discipline as the earlier 0.9.6x builds: golang:1.26-bookworm,
# --cpus=1 --memory=768m, no production mounts, --rm auto-cleanup.
set -Eeuo pipefail
V=/root/cpa-workbuddy-truncation-20261022
T=$V/plugins/workbuddy
test -f "$T/go.mod"
test -f "$T/selected_mode_test.go"
test -f "$T/account_failover_test.go"
test -f "$T/stream_termination_test.go"
grep -q '0.9.64' "$T/VERSION"
grep -q 'truncated_stream: workbuddy' "$T/policy.go"
grep -q 'sawTerminator' "$T/stream.go"
echo "SANDBOX_PRE_OK tree=$V"
docker run --rm --cpus=1 --memory=768m -v "$V":/src -w /src/plugins/workbuddy golang:1.26-bookworm bash -c \
  "gofmt -l stream.go policy.go stream_termination_test.go && go vet ./... && go test -count=1 ./... && CGO_ENABLED=1 go build -buildmode=c-shared -o workbuddy.so . && sha256sum workbuddy.so && ls -la workbuddy.so"
