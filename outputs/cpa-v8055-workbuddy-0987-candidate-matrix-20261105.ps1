#requires -Version 7
# Controlled, credential-free scheduler replay for the WorkBuddy 0.9.87
# candidate. Generates a local Go harness from the patched source. No token,
# production config, CPA process or remote endpoint is accessed.
param(
  [string]$Config
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$resolvedConfig = Resolve-LauncherConfigPath -ProjectRoot $repoRoot -Config $Config
$matrixFixture = @'
package main

import (
	"strings"
	"testing"
	"time"

	"github.com/router-for-me/CLIProxyAPI/v7/sdk/pluginapi"
)

// This matrix only exercises process-local selection and filtering. It never
// sends an upstream request or modifies a production registry.
func Test0987CandidateSchedulerMatrix(t *testing.T) {
	a, b := "candidate-matrix-a", "candidate-matrix-b"
	model := "candidate-matrix-model"
	ka, kb := modelRateLimitKey(a, model), modelRateLimitKey(b, model)
	t.Cleanup(func() {
		wbModelRateLimits.mu.Lock()
		delete(wbModelRateLimits.entries, ka)
		delete(wbModelRateLimits.entries, kb)
		wbModelRateLimits.mu.Unlock()
	})
	noteModelRateLimit(a, model, time.Now().Add(time.Hour))
	restoreSchedulerMode := setSchedulerMode(schedulerModeCredits)
	t.Cleanup(restoreSchedulerMode)
	pick := func() pluginapi.SchedulerPickResponse {
		raw, err := handleSchedulerPick(mustMarshal(t, pluginapi.SchedulerPickRequest{
			Provider: providerName, Model: model,
			Candidates: []pluginapi.SchedulerAuthCandidate{
				{ID: "auth/workbuddy-intl-" + a + ".json", Provider: providerName, Status: "active"},
				{ID: "auth/workbuddy-intl-" + b + ".json", Provider: providerName, Status: "active"},
			},
		}))
		if err != nil {
			t.Fatal(err)
		}
		return parsePickResponse(t, raw)
	}
	if got := pick(); !got.Handled || got.AuthID != "auth/workbuddy-intl-"+b+".json" {
		t.Fatalf("matrix must select independent B, got %+v", got)
	}
	noteModelRateLimit(b, model, time.Now().Add(time.Hour))
	if got := pick(); got.Handled && got.AuthID != "" {
		t.Fatalf("all explicitly limited candidates must not select a dead pair: %+v", got)
	}
}

func Test0987CandidateCatalogFiltersExactCredential(t *testing.T) {
	a, b := "catalog-matrix-a", "catalog-matrix-b"
	model := "catalog-matrix-model"
	ka := modelRateLimitKey(a, model)
	t.Cleanup(func() {
		wbModelRateLimits.mu.Lock()
		delete(wbModelRateLimits.entries, ka)
		wbModelRateLimits.mu.Unlock()
	})
	noteModelRateLimit(a, model, time.Now().Add(time.Hour))
	models := []pluginapi.ModelInfo{
		{ID: model, Name: model, OwnedBy: providerName},
		{ID: "catalog-matrix-sibling", Name: "catalog-matrix-sibling", OwnedBy: providerName},
	}
	filtered := make([]pluginapi.ModelInfo, 0, len(models))
	for _, m := range models {
		if modelRateLimitErrorFor("auth/workbuddy-intl-"+a+".json", a, m.ID) == nil {
			filtered = append(filtered, m)
		}
	}
	if len(filtered) != 1 || filtered[0].ID != "catalog-matrix-sibling" {
		t.Fatalf("catalog must hide only A's limited model, got %+v", filtered)
	}
	if modelRateLimitErrorFor("auth/workbuddy-intl-"+b+".json", b, model) != nil {
		t.Fatal("independent B model must remain in catalog")
	}
	if modelRateLimitErrorFor("auth/workbuddy-intl-"+a+"-unrelated.json", "", model) != nil {
		t.Fatal("unknown suffix must not inherit A's window")
	}
	if modelRateLimitErrorFor("auth/workbuddy-intl-"+a+".json", "", "catalog-matrix-other") != nil {
		t.Fatal("6004 on one model must not suppress its sibling model")
	}
}

func Test0987CandidateRequestScoped11115(t *testing.T) {
	body := `{"code":11115,"msg":"prompt is too long: 1280 tokens > 1000 maximum"}`
	if strings.Contains(body, "6004") {
		t.Fatal("request-scoped fixture must not carry 6004")
	}
}
'@
$fixtureText = $matrixFixture.Replace("`r`n", "`n").Replace("`r", "`n") + "`n"
$fixtureHex = [Convert]::ToHexString([Text.Encoding]::UTF8.GetBytes($fixtureText)).ToLowerInvariant()
$command = @'
set -Eeuo pipefail
umask 077
ROOT=/root/cpa-workbuddy-candidate-0987
# Reuse the reviewed, dependency-only cache populated by the isolated build
# and snapshot audit. A fresh named volume is empty while GOPROXY=off is
# intentional, which would make this credential-free matrix fail before it
# reaches the candidate tests.
GO_CACHE=/root/wb-0987-go-cache
ART="$ROOT/plugins/workbuddy/workbuddy-0987-canonical.so"
ART_SHA=7093a190e906a872f7362e1bc225eaf6f0fba30388cdd9066af6d0d8636a89e4
MATRIX_HEX='__MATRIX_HEX__'
[ -s "$ART" ] && [ "$(sha256sum "$ART" | cut -d ' ' -f1)" = "$ART_SHA" ] ||
  { echo REFUSE_CANDIDATE_ARTIFACT; exit 20; }
[ -s "$ROOT/review/canonical.patch" ]
[ -d "$GO_CACHE/pkg/mod" ] || { echo REFUSE_GO_CACHE_MISSING; exit 20; }
WORK=$(mktemp -d /root/wb-0987-matrix-XXXXXXXX)
cleanup() {
  "rm" -rf --one-file-system -- "$WORK"
}
trap cleanup EXIT
cp -a "$ROOT/plugins/workbuddy" "$WORK/src"
cd "$WORK/src"
python3 -c 'import sys; from pathlib import Path; Path(sys.argv[2], "zz_candidate_matrix_test.go").write_bytes(bytes.fromhex(sys.argv[1]))' "$MATRIX_HEX" "$WORK/src"
if [ ! -s zz_candidate_matrix_test.go ]; then
  echo REFUSE_MATRIX_FIXTURE_MISSING
  exit 1
fi
echo MATRIX_FIXTURE_READY
docker run --rm --network none --cpus=1 --memory=1536m \
  -v "$WORK/src:/src:rw" \
  -v "$GO_CACHE:/cache:rw" -v "$GO_CACHE:/go:rw" \
  -e GOPATH=/go -e GOMODCACHE=/go/pkg/mod -e GOCACHE=/root/.cache/go-build \
  -e GOMAXPROCS=1 -e GOMEMLIMIT=1200MiB \
  -e GOPROXY=off -e GOTOOLCHAIN=local -w /src \
  -e CGO_ENABLED=1 -e GOOS=linux -e GOARCH=amd64 \
  golang:1.26-bookworm bash -c '
    set -Eeuo pipefail
    echo MATRIX_ENV_READY
    go mod download
    echo MATRIX_DEPENDENCIES_READY
    format_suspects="$(gofmt -l zz_candidate_matrix_test.go)"
    if [ -n "$format_suspects" ]; then
      echo MATRIX_FORMAT_INVALID
      gofmt -d zz_candidate_matrix_test.go
      exit 23
    fi
    echo MATRIX_FORMAT_READY
    go test -count=1 -run "Test0987Candidate" -v .
    echo CANDIDATE_MATRIX=PASS
  '
'@
$command = $command.Replace('__MATRIX_HEX__', $fixtureHex)
if ($command -match '__MATRIX_HEX__') {
  throw 'Unfilled candidate matrix fixture.'
}
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $resolvedConfig -Profile bwg -Command $command `
  -IdleTimeoutSeconds 300 -HardTimeoutSeconds 600 -ViaTempFile
