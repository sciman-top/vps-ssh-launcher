#!/usr/bin/env bash
# Isolated differential simulation for CLIProxyAPI v8.0.40 + PR #6784.
# Uses a reasoning-only OpenAI-compatible fixture. It never touches production
# config, auth, plugins, admission, public ports, or the production container.
set -Eeuo pipefail
umask 077

BASE_IMAGE='eceasy/cli-proxy-api:v8.0.40@sha256:f7a424d78498dc413808414a3143c8b23cddf78eecdea69979cf88adf3bfa9ef'
BASE_COMMIT='d946c4fba6fc9a5cfbca1f3d85184d62ad2fb683'
PATCH_URL='https://patch-diff.githubusercontent.com/raw/router-for-me/CLIProxyAPI/pull/6784.diff'
PATCH_SHA256='44ab2b314eae0df6d634b74f385518bdd6319a9a7353c3c0531061c2a409345f'
SIM_CLIENT_KEY='sim-client-key'
SIM_UPSTREAM_KEY='sim-upstream-key'
SIM_PORT=18317
MOCK_PORT=18318
MOCK_BIND=''
RUN_STAMP="$(date -u +%Y%m%dT%H%M%S.%NZ)"
TMP="$(mktemp -d "/root/cpa-pr6784-sim-${RUN_STAMP}.XXXXXX")"
BASE_NAME="cpa-pr6784-baseline-${RUN_STAMP}"
CAND_NAME="cpa-pr6784-candidate-${RUN_STAMP}"
CAND_IMAGE='bwg-local/cpa:v8.0.40-pr6784-sim-v2'
MOCK_PID=''
BASE_ID=''
CAND_ID=''
LAST_SIM_ID=''
PROD_BEFORE=''

docker() {
  timeout --signal=TERM --kill-after=15s 600s docker "$@"
}

cleanup() {
  rc=$?
  trap - EXIT INT TERM
  if [[ -n "$CAND_ID" ]]; then docker rm -f "$CAND_NAME" >/dev/null 2>&1 || true; fi
  if [[ -n "$BASE_ID" ]]; then docker rm -f "$BASE_NAME" >/dev/null 2>&1 || true; fi
  if [[ -n "$MOCK_PID" ]]; then kill "$MOCK_PID" >/dev/null 2>&1 || true; fi
  rm -rf -- "$TMP"
  PROD_AFTER="$(docker inspect -f '{{.Id}} {{.State.StartedAt}}' cli-proxy-api 2>/dev/null || true)"
  if [[ -n "$PROD_BEFORE" && "$PROD_AFTER" != "$PROD_BEFORE" ]]; then
    echo 'PRODUCTION_CONTAINER_CHANGED_DURING_SIMULATION'
    exit 1
  fi
  exit "$rc"
}
trap cleanup EXIT INT TERM

[[ "$(uname -s)" == 'Linux' ]]
PROD_BEFORE="$(docker inspect -f '{{.Id}} {{.State.StartedAt}}' cli-proxy-api)"
MOCK_BIND="$(docker network inspect bridge -f '{{(index .IPAM.Config 0).Gateway}}')"
[[ "$MOCK_BIND" =~ ^[0-9.]+$ ]]
if ss -ltnH "( sport = :$SIM_PORT or sport = :$MOCK_PORT )" | grep -q .; then
  echo "REFUSE_SIMULATION_PORTS_BUSY ports=$SIM_PORT,$MOCK_PORT"
  exit 20
fi

clone_source() {
  local attempt
  for attempt in 1 2 3; do
    rm -rf -- "$TMP/source"
    if git -c http.version=HTTP/1.1 clone --quiet --depth 1 --branch v8.0.40 \
      https://github.com/router-for-me/CLIProxyAPI.git "$TMP/source"; then
      return 0
    fi
    echo "SOURCE_CLONE_RETRY attempt=$attempt"
    sleep 2
  done
  return 1
}

fetch_patch() {
  local attempt
  for attempt in 1 2 3; do
    if curl -fsSL --http1.1 --max-time 60 "$PATCH_URL" -o "$TMP/pr6784.diff"; then
      return 0
    fi
    echo "PATCH_DOWNLOAD_RETRY attempt=$attempt"
    sleep 2
  done
  return 1
}

clone_source
[[ "$(git -C "$TMP/source" rev-parse HEAD)" == "$BASE_COMMIT" ]]
echo 'SOURCE_CLONED_OK'
fetch_patch
echo 'PATCH_DOWNLOADED_OK'
echo "$PATCH_SHA256  $TMP/pr6784.diff" | sha256sum -c - >/dev/null
echo 'PATCH_HASH_OK'

# v8.0.40 requires Go >= 1.26.10, but its Dockerfile's golang:1.26-bookworm
#浮动 tag now resolves to 1.26.9. Pin the exact builder ref for this simulation.
BUILDER_REF="$(docker image inspect -f '{{range .RepoDigests}}{{println .}}{{end}}' golang:1.26.10-bookworm 2>/dev/null | awk '$1 ~ /^golang@sha256:/ {print $1; exit}')"
if [[ -z "$BUILDER_REF" ]]; then
  docker pull --quiet golang:1.26.10-bookworm
  BUILDER_REF="$(docker image inspect -f '{{range .RepoDigests}}{{println .}}{{end}}' golang:1.26.10-bookworm | awk '$1 ~ /^golang@sha256:/ {print $1; exit}')"
fi
printf 'BUILDER_REF_QUOTED=%q\n' "$BUILDER_REF"
[[ "$BUILDER_REF" == golang@sha256:* ]]
echo 'BUILDER_REF_PATTERN_OK'
python3 - "$TMP/source/Dockerfile" "$BUILDER_REF" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
builder = sys.argv[2]
text = path.read_text()
old = "FROM golang:1.26-bookworm"
if text.count(old) != 1:
    raise SystemExit("REFUSE builder FROM replacement count")
path.write_text(text.replace(old, "FROM " + builder, 1))
PY
echo 'BUILDER_DOCKERFILE_REPLACED'
grep -Fx "FROM $BUILDER_REF AS builder" "$TMP/source/Dockerfile" >/dev/null
echo 'BUILDER_GREP_OK'
echo "GO_BUILDER_PINNED ref=$BUILDER_REF"
git -C "$TMP/source" apply --check "$TMP/pr6784.diff"
git -C "$TMP/source" apply "$TMP/pr6784.diff"
python3 - "$TMP/source/internal/runtime/executor/openai_compat_executor.go" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
replacements = [
    (
        "\t\tvar seenDone bool\n",
        "\t\tvar seenDone bool\n\t\tvar syntheticFinishSent bool\n",
    ),
    (
        "\t\t\t\t\tdataPayload = synthetic\n",
        "\t\t\t\t\tdataPayload = synthetic\n\t\t\t\t\tsyntheticFinishSent = true\n",
    ),
    (
        "\t\t\tfor i := range chunks {\n\t\t\t\tselect {\n\t\t\t\tcase out <- cliproxyexecutor.StreamChunk{Payload: chunks[i]}:\n\t\t\t\tcase <-ctx.Done():\n\t\t\t\t\tstreamAborted = true\n\t\t\t\t\treturn true\n\t\t\t\t}\n\t\t\t}\n\t\t\tif helps.ApplyPatchTranslationError(param) != nil {\n",
        "\t\t\tfor i := range chunks {\n\t\t\t\tselect {\n\t\t\t\tcase out <- cliproxyexecutor.StreamChunk{Payload: chunks[i]}:\n\t\t\t\tcase <-ctx.Done():\n\t\t\t\t\tstreamAborted = true\n\t\t\t\t\treturn true\n\t\t\t\t}\n\t\t\t}\n\t\t\tif syntheticFinishSent {\n\t\t\t\tdoneChunks := helps.TranslateStreamWithClaudeInputTokens(ctx, to, responseFormat, req.Model, helps.ApplyPatchOriginalRequest(req, opts), translated, []byte(\"data: [DONE]\"), &param, claudeInputTokens)\n\t\t\t\tfor i := range doneChunks {\n\t\t\t\t\tselect {\n\t\t\t\t\tcase out <- cliproxyexecutor.StreamChunk{Payload: doneChunks[i]}:\n\t\t\t\t\tcase <-ctx.Done():\n\t\t\t\t\t\tstreamAborted = true\n\t\t\t\t\t\treturn true\n\t\t\t\t\t}\n\t\t\t\t}\n\t\t\t}\n\t\t\tif helps.ApplyPatchTranslationError(param) != nil {\n",
    ),
]
for number, (old, new) in enumerate(replacements, 1):
    if text.count(old) != 1:
        raise SystemExit(f"REFUSE v8.0.40 adapter replacement index={number} count={text.count(old)}")
    text = text.replace(old, new, 1)
path.write_text(text)
PY
echo 'V8040_BACKPORT_ADAPTER_APPLIED'
if ! docker image inspect "$CAND_IMAGE" >/dev/null 2>&1; then
  if ! docker build \
    --build-arg VERSION="v8.0.40-pr6784-sim" \
    --build-arg COMMIT="$BASE_COMMIT+pr6784" \
    --build-arg BUILD_DATE="$(date -u +%FT%TZ)" \
    -t "$CAND_IMAGE" "$TMP/source" >"$TMP/build.log" 2>&1; then
    echo 'CANDIDATE_BUILD_FAILED log_tail:'
    tail -120 "$TMP/build.log"
    exit 1
  fi
fi
CAND_IMAGE_ID="$(docker image inspect -f '{{.Id}}' "$CAND_IMAGE")"
echo "CANDIDATE_IMAGE=$CAND_IMAGE id=$CAND_IMAGE_ID"

cat > "$TMP/mock.py" <<'PY'
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path != "/healthz":
            self.send_error(404)
            return
        body = b'{"status":"ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        except (ValueError, TypeError):
            body = {}
        if body.get("model") != "deepseek-v4.1-flash":
            self.send_error(400)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        base = {
            "id": "chatcmpl-reasoning-only",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "deepseek-v4.1-flash",
        }
        chunks = [
            {**base, "choices": [{"index": 0, "delta": {"role": "assistant", "reasoning_content": "step one "}, "finish_reason": None}]},
            {**base, "choices": [{"index": 0, "delta": {"reasoning_content": "step two"}, "finish_reason": None}]},
        ]
        for chunk in chunks:
            self.wfile.write(b"data: " + json.dumps(chunk, separators=(",", ":")).encode() + b"\n\n")
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


ThreadingHTTPServer(("__MOCK_BIND__", 18318), Handler).serve_forever()
PY

python3 - "$TMP/mock.py" "$MOCK_BIND" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
path.write_text(path.read_text().replace("__MOCK_BIND__", sys.argv[2], 1))
PY

cat > "$TMP/config.yaml" <<YAML
host: 0.0.0.0
port: 8317
auth-dir: /tmp/sim-auth
access:
  api-keys:
    - "$SIM_CLIENT_KEY"
routing:
  strategy: fill-first
  retry:
    request-retry: 0
    max-retry-credentials: 0
  cooldown:
    disable-cooling: false
    save-cooldown-status: false
    transient-error-cooldown-seconds: 60
openai-compatibility:
  - name: reasoning-only-sim
    base-url: http://host.docker.internal:$MOCK_PORT/v1
    api-key-entries:
      - api-key: "$SIM_UPSTREAM_KEY"
    models:
      - name: deepseek-v4.1-flash
        alias: deepseek-v4.1-flash
YAML

python3 "$TMP/mock.py" >"$TMP/mock.log" 2>&1 &
MOCK_PID=$!
for _ in $(seq 1 20); do
  if curl -fsS --noproxy '*' --max-time 1 "http://$MOCK_BIND:$MOCK_PORT/healthz" >/dev/null; then
    break
  fi
  sleep 0.2
done
if ! curl -fsS --noproxy '*' --max-time 2 "http://$MOCK_BIND:$MOCK_PORT/healthz" >/dev/null; then
  echo 'MOCK_NOT_READY log:'
  cat "$TMP/mock.log"
  exit 1
fi

mkdir -p "$TMP/auth"
start_sim() {
  local name="$1" image="$2"
  LAST_SIM_ID="$(docker run -d --name "$name" \
    --add-host host.docker.internal:host-gateway \
    -p "127.0.0.1:$SIM_PORT:8317" \
    -v "$TMP/config.yaml:/CLIProxyAPI/config.yaml:ro" \
    -v "$TMP/auth:/tmp/sim-auth" \
    "$image")"
}

wait_ready() {
  local attempt body
  for attempt in $(seq 1 60); do
    body="$(curl -sS --noproxy '*' --max-time 2 2>/dev/null \
      -H "Authorization: Bearer $SIM_CLIENT_KEY" \
      "http://127.0.0.1:$SIM_PORT/v1/models")" || body=''
    if [[ "$body" == *'deepseek-v4.1-flash'* ]]; then
      return 0
    fi
    sleep 0.5
  done
  echo "SIM_NOT_READY container=$1"
  printf 'SIM_MODELS_RESPONSE='; printf '%s' "$body" | head -c 1200; echo
  echo 'MOCK_LOG:'
  cat "$TMP/mock.log"
  docker logs --tail 80 "$1" 2>&1 | sed -E 's/(Authorization|api-key|api_key):.*/\1=<redacted>/Ig'
  return 1
}

execute_case() {
  local label="$1" image="$2" container="$3" response="$4" status
  start_sim "$container" "$image"
  if [[ "$label" == baseline ]]; then BASE_ID="$LAST_SIM_ID"; else CAND_ID="$LAST_SIM_ID"; fi
  wait_ready "$container"
  status="$(curl -sS --noproxy '*' --max-time 30 -o "$response" \
    -w '%{http_code}' -X POST "http://127.0.0.1:$SIM_PORT/v1/responses" \
    -H "Authorization: Bearer $SIM_CLIENT_KEY" \
    -H 'Content-Type: application/json' \
    -H 'Accept: text/event-stream' \
    --data '{"model":"deepseek-v4.1-flash","input":"Reason briefly.","max_output_tokens":256,"stream":true}')"
  if [[ "$status" != 200 ]]; then
    echo "SIM_REQUEST_FAILED container=$container http=$status response_head:"
    head -c 1200 "$response" 2>/dev/null || true
    echo
    echo 'MOCK_LOG:'
    cat "$TMP/mock.log"
    echo "CONTAINER_LOG container=$container:"
    docker logs --tail 80 "$container" 2>&1 | sed -E 's/(Authorization|api-key|api_key):.*/\1=<redacted>/Ig'
    return 1
  fi
  python3 - "$label" "$status" "$response" <<'PY'
import json
import sys
from collections import Counter

label, status, path = sys.argv[1:]
event = ""
parts = []
types = Counter()
final = None
completed_status = None
errors = []
error_details = []
with open(path, encoding="utf-8") as stream:
    for raw in stream:
        line = raw.rstrip("\r\n")
        if not line:
            if parts:
                data = "\n".join(parts)
                if data == "[DONE]":
                    final = "[DONE]"
                else:
                    payload = json.loads(data)
                    kind = payload.get("type")
                    types[kind] += 1
                    final = kind
                    if kind == "response.completed":
                        completed_status = (payload.get("response") or {}).get("status")
                    if kind in {"error", "response.failed", "response.incomplete"}:
                        errors.append(kind)
                        error_details.append(data[:600])
            event = ""
            parts = []
            continue
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            parts.append(line.split(":", 1)[1].lstrip(" "))

print(
    f"{label} http={status} final={final} completed_status={completed_status} "
    f"types={dict(sorted(types.items()))} errors={errors or 'none'}"
)
if error_details:
    print("ERROR_DETAIL=" + error_details[0].replace("\n", " "))
if label == "baseline":
    baseline_detail = error_details[0] if error_details else ""
    ok = (
        status == "200"
        and "upstream stream closed before a terminal event" in baseline_detail
        and "response.output_item.done" in baseline_detail
        and "response.completed" not in types
    )
    print("BASELINE_DEFECT_REPRO=" + ("PASS" if ok else "FAIL"))
    raise SystemExit(0 if ok else 1)
ok = status == "200" and completed_status == "completed" and "response.completed" in types and not errors
print("CANDIDATE_TERMINAL_FIX=" + ("PASS" if ok else "FAIL"))
raise SystemExit(0 if ok else 1)
PY
}

execute_case baseline "$BASE_IMAGE" "$BASE_NAME" "$TMP/baseline.sse"
docker rm -f "$BASE_NAME" >/dev/null
BASE_ID=''
execute_case candidate "$CAND_IMAGE" "$CAND_NAME" "$TMP/candidate.sse"

PROD_AFTER="$(docker inspect -f '{{.Id}} {{.State.StartedAt}}' cli-proxy-api)"
[[ "$PROD_AFTER" == "$PROD_BEFORE" ]]
echo "PRODUCTION_UNCHANGED id_state=$PROD_AFTER"
echo "ISOLATED_DIFFERENTIAL_ACCEPTANCE=PASS"
