# Credential-free catalog UID and persisted-snapshot replay. This creates no
# account, sends no provider request, and does not modify production files.
param(
  [string]$Config
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot -Config $Config
$command = @'
set -Eeuo pipefail
umask 077
ROOT=$(mktemp -d /root/wb-0987-snapshot-audit-XXXXXXXX)
cleanup() { rm -rf --one-file-system -- "$ROOT"; }
trap cleanup EXIT
mkdir -m 700 "$ROOT/src"
[ "$(sha256sum /opt/cliproxyapi/plugins/workbuddy.so | cut -d ' ' -f 1)" = \
  7093a190e906a872f7362e1bc225eaf6f0fba30388cdd9066af6d0d8636a89e4 ] ||
  { echo REFUSE_PRODUCTION_NOT_0987; exit 20; }
cp -a /root/cpa-workbuddy-candidate-0987/plugins/workbuddy/. "$ROOT/src/"
cd "$ROOT/src"
cat >zz_snapshot_audit_test.go <<'GO'
package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"testing"
	"time"

	"github.com/router-for-me/CLIProxyAPI/v7/sdk/pluginapi"
)

func Test0987SnapshotCoordinateContract(t *testing.T) {
	fmt.Println("SNAPSHOT_CHECKPOINT=start")
	uid, peerUID := "snapshot-account", "snapshot-peer"
	authID := "auth/workbuddy-intl-" + uid + ".json"
	peerAuthID := "auth/workbuddy-intl-" + peerUID + ".json"
	storage := []byte(`{"auth":{"accessToken":"fixture-only-no-network","region":"intl"},"account":{"uid":"snapshot-account","nickname":"snapshot-account"},"model_cache":{"realm":"intl","fetched_at":"2026-11-05T00:00:00Z","models":[{"ID":"snapshot-audit-model","Name":"snapshot-audit-model","ContextLength":1000}]}}`)
	peerStored := []byte(`{"auth":{"accessToken":"fixture-only-no-network","region":"intl"},"account":{"uid":"snapshot-peer","nickname":"snapshot-peer"},"model_cache":{"realm":"intl","fetched_at":"2026-11-05T00:00:01Z","models":[{"ID":"snapshot-audit-model","Name":"snapshot-audit-model","ContextLength":1000}]}}`)
	origDiscover := discoverModelsFn
	discoverModelsFn = func(string, string, string) ([]pluginapi.ModelInfo, error) {
		return nil, errors.New("fixture discovery disabled")
	}
	t.Cleanup(func() { discoverModelsFn = origDiscover })
	var raw map[string]any
	if err := json.Unmarshal(storage, &raw); err != nil {
		t.Fatal(err)
	}
	stored, err := json.Marshal(raw)
	if err != nil {
		t.Fatal(err)
	}
	fmt.Println("SNAPSHOT_CHECKPOINT=account_first")
	envelope, err := handleModelForAuth(mustMarshal(t, pluginapi.AuthModelRequest{AuthID: authID, AuthProvider: providerName, StorageJSON: stored}))
	if err != nil {
		t.Fatal(err)
	}
	var response struct {
		OK     bool `json:"ok"`
		Result pluginapi.ModelResponse `json:"result"`
	}
	if err := json.Unmarshal(envelope, &response); err != nil || !response.OK {
		t.Fatalf("catalog envelope: %v %s", err, envelope)
	}
	found := false
	for _, model := range response.Result.Models {
		if model.ID == "snapshot-audit-model" {
			found = true
		}
	}
	if !found {
		t.Fatal("fixture did not select the snapshot branch")
	}
	noteModelRateLimit(uid, "snapshot-audit-model", time.Now().Add(time.Hour))
	t.Cleanup(func() {
		wbModelRateLimits.mu.Lock()
		delete(wbModelRateLimits.entries, modelRateLimitKey(uid, "snapshot-audit-model"))
		wbModelRateLimits.mu.Unlock()
	})
	fmt.Println("SNAPSHOT_CHECKPOINT=account_filtered")
	envelope, err = handleModelForAuth(mustMarshal(t, pluginapi.AuthModelRequest{AuthID: authID, AuthProvider: providerName, StorageJSON: []byte(`{"auth":{"accessToken":"fixture-only-no-network","region":"intl"},"account":{"uid":"snapshot-account","nickname":"snapshot-account"},"model_cache":{"realm":"intl","fetched_at":"2026-11-05T00:00:00Z","models":[{"ID":"snapshot-audit-model","Name":"snapshot-audit-model","ContextLength":1000}]}}`)}))
	if err != nil {
		t.Fatal(err)
	}
	response = struct {
		OK     bool `json:"ok"`
		Result pluginapi.ModelResponse `json:"result"`
	}{}
	if err := json.Unmarshal(envelope, &response); err != nil || !response.OK {
		t.Fatalf("filtered catalog envelope: %v %s", err, envelope)
	}
	if len(response.Result.Models) != 0 {
		t.Fatalf("snapshot model must be filtered for exact UID, got %s", envelope)
	}
	fmt.Println("SNAPSHOT_EXACT_UID_FILTER=PASS")
	resetDynamicModelsCache()
	fmt.Println("SNAPSHOT_CHECKPOINT=peer_own_snapshot")
	peerEnvelope, err := handleModelForAuth(mustMarshal(t, pluginapi.AuthModelRequest{
		AuthID: peerAuthID, AuthProvider: providerName, StorageJSON: peerStored,
	}))
	if err != nil {
		t.Fatal(err)
	}
	var peerResponse struct {
		OK     bool `json:"ok"`
		Result pluginapi.ModelResponse `json:"result"`
	}
	if err := json.Unmarshal(peerEnvelope, &peerResponse); err != nil || !peerResponse.OK {
		t.Fatalf("peer catalog envelope: %v %s", err, peerEnvelope)
	}
	found = false
	for _, model := range peerResponse.Result.Models {
		if model.ID == "snapshot-audit-model" {
			found = true
		}
	}
	if !found {
		t.Fatal("same-realm peer discovery must not inherit the first credential's 6004")
	}
	fmt.Println("SNAPSHOT_UID_COORDINATE=PASS")
	peerStorage := []byte(`{"auth":{"accessToken":"fixture-only-no-network","region":"intl"},"account":{"uid":"snapshot-peer","nickname":"snapshot-peer"}}`)
	resetDynamicModelsCache()
	origList, origGet := hostAuthListFn, hostAuthGetPhysicalFn
	hostAuthListFn = func() ([]pluginapi.HostAuthFileEntry, error) {
		return []pluginapi.HostAuthFileEntry{{AuthIndex: "peer", Name: peerAuthID}}, nil
	}
	hostAuthGetPhysicalFn = func(authIndex string) (*hostAuthPhysical, error) {
		if authIndex != "peer" {
			return nil, errors.New("unexpected peer index")
		}
		return &hostAuthPhysical{AuthIndex: "peer", Name: peerAuthID, JSON: peerStored}, nil
	}
	t.Cleanup(func() {
		hostAuthListFn, hostAuthGetPhysicalFn = origList, origGet
		resetDynamicModelsCache()
	})
	fmt.Println("SNAPSHOT_CHECKPOINT=peer_fallback")
	peerEnvelope, err = handleModelForAuth(mustMarshal(t, pluginapi.AuthModelRequest{
		AuthID: peerAuthID, AuthProvider: providerName, StorageJSON: peerStorage,
	}))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(peerEnvelope, &peerResponse); err != nil || !peerResponse.OK {
		t.Fatalf("peer fallback envelope: %v %s", err, peerEnvelope)
	}
	if len(peerResponse.Result.Models) == 0 {
		t.Fatal("same-realm peer snapshot fallback must remain available")
	}
	noteModelRateLimit(peerUID, peerResponse.Result.Models[0].ID, time.Now().Add(time.Hour))
	t.Cleanup(func() {
		wbModelRateLimits.mu.Lock()
		delete(wbModelRateLimits.entries, modelRateLimitKey(peerUID, peerResponse.Result.Models[0].ID))
		wbModelRateLimits.mu.Unlock()
	})
	fmt.Println("SNAPSHOT_CHECKPOINT=account_from_peer_snapshot")
	snapshotEnvelope, err := handleModelForAuth(mustMarshal(t, pluginapi.AuthModelRequest{
		AuthID: peerAuthID, AuthProvider: providerName, StorageJSON: peerStored,
	}))
	if err != nil {
		t.Fatal(err)
	}
	var snapshotResponse struct {
		OK     bool `json:"ok"`
		Result pluginapi.ModelResponse `json:"result"`
	}
	if err := json.Unmarshal(snapshotEnvelope, &snapshotResponse); err != nil || !snapshotResponse.OK {
		t.Fatalf("snapshot filter envelope: %v %s", err, snapshotEnvelope)
	}
	for _, model := range snapshotResponse.Result.Models {
		if model.ID == peerResponse.Result.Models[0].ID {
			t.Fatal("same-realm snapshot fallback must keep the asking credential's 6004")
		}
	}
	fmt.Println("SNAPSHOT_FALLBACK_UID_COORDINATE=PASS")
}
GO
docker run --rm --cpus=1 --memory=1800m --memory-swap=2800m \
  -v "$ROOT/src:/src:rw" \
  -v /root/wb-0987-go-cache:/go:rw \
  -e GOPATH=/go -e GOMODCACHE=/go/pkg/mod -e GOCACHE=/go/build \
  -e GOMAXPROCS=1 -e GOMEMLIMIT=1600MiB \
  -e GOFLAGS=-p=1 \
  --network none -e GOPROXY=off -e GOTOOLCHAIN=local -e CGO_ENABLED=1 -e GOOS=linux -e GOARCH=amd64 \
  -w /src golang:1.26-bookworm bash -c '
    set -Eeuo pipefail
    gofmt -w zz_snapshot_audit_test.go
    timeout 240s go test -p 1 -count=1 -timeout=60s -run Test0987SnapshotCoordinateContract -v .
  '
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile bwg -Command $command `
  -IdleTimeoutSeconds 300 -HardTimeoutSeconds 600 -ViaTempFile
