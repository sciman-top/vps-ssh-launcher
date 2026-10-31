#requires -Version 7
# Build WorkBuddy 0.9.82-local from the reviewed 0.9.81 tree plus the
# exhausted-pool-delegate patch. Runs gofmt/vet/test/build inside an isolated
# golang container on bwg and prints the new artifact hash plus the canonical
# patch that the repository must carry. No CPA restart, no config or
# credential mutation.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -Eeuo pipefail
umask 077
SRC=/root/cpa-workbuddy-0981-20261028
DST=/root/cpa-workbuddy-0982-20261031
[ -d "$SRC/plugins/workbuddy" ]
if [ ! -d "$DST/plugins/workbuddy" ]; then
  cp -a "$SRC" "$DST"
fi
for f in scheduler.go scheduler_test.go VERSION main.go cooldown_test.go; do
  if [ ! -f "/root/$f.pre0982" ]; then
    cp -a "$SRC/plugins/workbuddy/$f" "/root/$f.pre0982"
  fi
  cp -a "/root/$f.pre0982" "$DST/plugins/workbuddy/$f"
done
rm -f "$DST/plugins/workbuddy/scheduler_delegate_test.go"
cd "$DST/plugins/workbuddy"
python3 - <<'PY'
from pathlib import Path


def tabs(block):
    out = []
    for line in block.strip("\n").split("\n"):
        stripped = line.lstrip(" ")
        n = len(line) - len(stripped)
        if n % 4:
            raise SystemExit("bad indent: " + line)
        out.append("\t" * (n // 4) + stripped)
    return "\n".join(out)


def gofile(block):
    out = []
    for line in block.strip("\n").split("\n"):
        if not line.strip():
            out.append("")
            continue
        if not line.startswith("    "):
            raise SystemExit("missing 4-space base: " + line)
        out.append(tabs(line[4:]))
    return "\n".join(out) + "\n"


path = Path("scheduler.go")
text = path.read_text(encoding="utf-8")


def sub(old, new):
    global text
    old = tabs(old)
    new = tabs(new)
    if text.count(old) != 1:
        raise SystemExit("context count %d for: %r" % (text.count(old), old[:80]))
    text = text.replace(old, new)


sub("""
    sawWorkbuddy := false
""", """
    sawWorkbuddy := false
    withheldDisabled, withheldCooling, withheldVeto := 0, 0, 0
""")

sub("""
        if candidateDisabled(c) {
            continue
        }
""", """
        if candidateDisabled(c) {
            withheldDisabled++
            withheldVeto++
            continue
        }
""")

sub("""
        if reqModel != "" && modelIsCooling(c.ID, reqModel) {
            continue
        }
""", """
        if reqModel != "" && modelIsCooling(c.ID, reqModel) {
            withheldCooling++
            if coolingVetoesDelegation(c.ID, reqModel) {
                withheldVeto++
            }
            continue
        }
""")

sub("""
        if accountRestrictionCooling(c.ID) {
            continue
        }
""", """
        if accountRestrictionCooling(c.ID) {
            withheldVeto++
            continue
        }
""")

sub("""
        if guardSkipCredential(c.ID) {
            continue
        }
""", """
        if guardSkipCredential(c.ID) {
            withheldVeto++
            continue
        }
""")

sub("""
        if mode == schedulerModeCredits && hasWorkBuddy {
            return rejectAutomaticPool()
        }
""", """
        if mode == schedulerModeCredits && hasWorkBuddy {
            if withheldVeto > 0 {
                log.Printf(
                    "workbuddy: scheduler pick rejected - withheld veto=%d cooling=%d disabled=%d",
                    withheldVeto, withheldCooling, withheldDisabled,
                )
                return rejectAutomaticPool()
            }
            // Cooling and hard credit exhaustion are recoverable: the host
            // turns Reject into a request-stop 5xx, and the client then
            // retries the same request. Hand the pick back so a mixed request
            // can still reach another provider (OpenCode) for this model.
            // Channel-risk rejects (11128), account-restriction quarantine
            // (403+11140) and credit guardrails keep the hard stop above.
            log.Printf(
                "workbuddy: scheduler pick deferred - withheld cooling=%d disabled=%d; host selector may try another provider",
                withheldCooling, withheldDisabled,
            )
            return okEnvelope(pluginapi.SchedulerPickResponse{Handled: false})
        }
""")

sub("""
    if mode == schedulerModeCredits {
        if _, exhausted := cachedCreditsScore(picked); exhausted {
            return rejectAutomaticPool()
        }
    }
""", """
    if mode == schedulerModeCredits {
        if _, exhausted := cachedCreditsScore(picked); exhausted {
            // remain==0 with package evidence is a hard "cannot spend" state:
            // delegating cannot consume quota, and failing the request here
            // only makes the client retry. Let the selector pick another
            // provider instead.
            log.Printf("workbuddy: scheduler pick deferred - every %s candidate is credit-exhausted; host selector may try another provider", providerName)
            return okEnvelope(pluginapi.SchedulerPickResponse{Handled: false})
        }
    }
""")

helper = tabs("""
// coolingVetoesDelegation reports whether the active cooldown on this
// pair is a channel-risk rejection instead of a transient capacity
// signal. Channel rejections keep the hard stop; capacity cooling may
// fall through to another provider for the same model.
func coolingVetoesDelegation(authID, model string) bool {
    authID = strings.TrimSpace(authID)
    model = strings.ToLower(strings.TrimSpace(model))
    if authID == "" {
        return false
    }
    now := cooldownNowFn()
    cooldownMu.Lock()
    defer cooldownMu.Unlock()
    for _, key := range []modelCooldownKey{{AuthID: authID, ModelID: model}, {AuthID: authID, ModelID: "*"}} {
        entry, ok := cooldownTable[key]
        if !ok || !entry.Until.After(now) {
            continue
        }
        if entry.Reason == cooldownReason("channel_rejected") {
            return true
        }
    }
    return false
}
""")

anchor = tabs("""
// candidateDisabled reports host-disabled auth from Status/metadata.
func candidateDisabled(c pluginapi.SchedulerAuthCandidate) bool {
""")
if text.count(anchor) != 1:
    raise SystemExit("helper anchor not unique")
text = text.replace(anchor, helper + "\n\n" + anchor, 1)

path.write_text(text, encoding="utf-8")

tpath = Path("scheduler_test.go")
ttext = tpath.read_text(encoding="utf-8")


def tsub(old, new):
    global ttext
    if ttext.count(old) != 1:
        raise SystemExit("test context count %d for: %r" % (ttext.count(old), old[:80]))
    ttext = ttext.replace(old, new)


tsub(
    "func TestSchedulerPick_AllExhausted_KeepsCurrent(t *testing.T) {",
    "func TestSchedulerPick_AllCreditExhausted_DefersToHost(t *testing.T) {",
)
tsub(tabs("""
    resp := parsePickResponse(t, raw)
    if !resp.Handled || resp.AuthID != "" {
        t.Fatalf("all exhausted must be handled without an account, got %+v", resp)
    }
"""), tabs("""
    resp := parsePickResponse(t, raw)
    if resp.Handled {
        t.Fatalf("credit-exhausted pool must defer to the host, got %+v", resp)
    }
"""))
tpath.write_text(ttext, encoding="utf-8")

cpath = Path("cooldown_test.go")
ctext = cpath.read_text(encoding="utf-8")
old_assert = tabs("""
    if resp2 := parsePickResponse(t, raw2); !resp2.Handled || resp2.AuthID != "" {
        t.Fatal("all candidates cooling must reject without host fallback")
    }
""")
new_assert = tabs("""
    if resp2 := parsePickResponse(t, raw2); resp2.Handled {
        t.Fatal("all candidates cooling must defer to the host selector")
    }
""")
if ctext.count(old_assert) != 1:
    raise SystemExit("cooldown_test assertion not unique")
cpath.write_text(ctext.replace(old_assert, new_assert), encoding="utf-8")

mpath = Path("main.go")
mtext = mpath.read_text(encoding="utf-8")
if mtext.count('var version = "0.9.81"') != 1:
    raise SystemExit("main.go version pin not unique")
mpath.write_text(mtext.replace('var version = "0.9.81"', 'var version = "0.9.82"'), encoding="utf-8")

new_test = gofile("""
    package main

    import (
        "encoding/json"
        "testing"
        "time"

        "github.com/router-for-me/CLIProxyAPI/v7/sdk/pluginapi"
    )

    // parsePickReject reads the raw envelope because pluginapi v7.2.30 does
    // not carry the Reject fields; the host ABI is only visible on the wire.
    func parsePickReject(t *testing.T, raw []byte) (bool, bool) {
        t.Helper()
        var env struct {
            OK     bool `json:"ok"`
            Result struct {
                Handled bool
                Reject  bool
            } `json:"result"`
        }
        if err := json.Unmarshal(raw, &env); err != nil {
            t.Fatalf("unmarshal envelope: %v", err)
        }
        if !env.OK {
            t.Fatal("envelope not ok")
        }
        return env.Result.Handled, env.Result.Reject
    }

    func resetAccountRestrictions(t *testing.T) {
        t.Helper()
        reset := func() {
            wbAccountRestrictions.mu.Lock()
            wbAccountRestrictions.entries = map[string]*accountRestrictionEntry{}
            wbAccountRestrictions.mu.Unlock()
        }
        reset()
        t.Cleanup(reset)
    }

    // TestSchedulerPick_AllCoolingDefersToHost pins the 2026-10-31 fallback
    // contract: a lane that is only transiently cooling must hand the pick
    // back to the host instead of aborting a mixed-provider request.
    func TestSchedulerPick_AllCoolingDefersToHost(t *testing.T) {
        withFrozenCooldown(t, time.Now())
        resetActiveAuth(t)
        const model = "deepseek-v4.1-flash"
        markModelCooldown("wb-a", model, cooldownReasonRateLimit)
        markModelCooldown("wb-b", model, cooldownReasonRateLimit)
        setActiveAuthID("wb-a")
        raw, err := handleSchedulerPick(mustMarshal(t, pluginapi.SchedulerPickRequest{
            Provider: providerName,
            Model:    model,
            Candidates: []pluginapi.SchedulerAuthCandidate{
                {ID: "wb-a", Provider: providerName},
                {ID: "wb-b", Provider: providerName},
            },
        }))
        if err != nil {
            t.Fatalf("err: %v", err)
        }
        if handled, reject := parsePickReject(t, raw); handled || reject {
            t.Fatalf("cooling-only pool must defer to the host, got handled=%v reject=%v", handled, reject)
        }
    }

    // TestSchedulerPick_AllRestrictedKeepsReject keeps the account veto: a
    // 403+11140 quarantine must stop the request instead of delegating.
    func TestSchedulerPick_AllRestrictedKeepsReject(t *testing.T) {
        resetActiveAuth(t)
        resetAccountRestrictions(t)
        noteAccountRestrictionFromPayload("wb-x", nil, 403, restrictionFieldPayload)
        noteAccountRestrictionFromPayload("wb-y", nil, 403, restrictionFieldPayload)
        raw, err := handleSchedulerPick(mustMarshal(t, pluginapi.SchedulerPickRequest{
            Provider: providerName,
            Candidates: []pluginapi.SchedulerAuthCandidate{
                {ID: "wb-x", Provider: providerName},
                {ID: "wb-y", Provider: providerName},
            },
        }))
        if err != nil {
            t.Fatalf("err: %v", err)
        }
        if handled, reject := parsePickReject(t, raw); !handled || !reject {
            t.Fatalf("restricted pool must keep the hard stop, got handled=%v reject=%v", handled, reject)
        }
    }
""")

Path("scheduler_delegate_test.go").write_text(new_test, encoding="utf-8")
Path("VERSION").write_text("0.9.82\n", encoding="utf-8")
print("SOURCES_PATCHED version=0.9.82")
PY
docker run --rm --cpus=1 --memory=768m -v "$DST":/src -w /src/plugins/workbuddy golang:1.26-bookworm bash -c \
  'test -z "$(gofmt -l scheduler.go scheduler_test.go scheduler_delegate_test.go)" || { echo GOFMT_DIRTY; gofmt -l scheduler.go scheduler_test.go scheduler_delegate_test.go; exit 1; }; go vet ./... && go test -count=1 ./... && CGO_ENABLED=1 go build -buildmode=c-shared -o workbuddy.so . && sha256sum workbuddy.so && ls -l workbuddy.so'
cd "$DST"
P=/root/workbuddy-0982-20261031.patch
: >"$P"
for f in scheduler.go scheduler_test.go cooldown_test.go main.go VERSION; do
  diff -u --label "a/plugins/workbuddy/$f" --label "b/plugins/workbuddy/$f" "/root/$f.pre0982" "plugins/workbuddy/$f" >>"$P" || true
done
diff -u --label a/plugins/workbuddy/scheduler_delegate_test.go --label b/plugins/workbuddy/scheduler_delegate_test.go /dev/null plugins/workbuddy/scheduler_delegate_test.go >>"$P" || true
printf 'PATCH_SHA=%s PATCH_BYTES=%s\n' "$(sha256sum "$P" | cut -d " " -f 1)" "$(wc -c <"$P")"
printf 'PATCH_BASE64_BEGIN\n'
base64 -w0 "$P"
printf '\nPATCH_BASE64_END\n'
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 300 -HardTimeoutSeconds 1200
