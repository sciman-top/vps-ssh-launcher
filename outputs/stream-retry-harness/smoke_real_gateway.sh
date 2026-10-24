#!/usr/bin/env bash
# One-request smoke of the real gateway with the client's retry settings.
# Builds a minimal CODEX_HOME (the Desktop config carries app-only sections the
# CLI refuses) and prints only the tail of the run; the token is never echoed.
set -Eeuo pipefail
SRC=/mnt/c/Users/sciman/.codex/config.toml
HOME_DIR=${1:-/tmp/sretry/realhome2}
mkdir -p "$HOME_DIR"
python3 - "$SRC" "$HOME_DIR/config.toml" <<'PY'
import re
import sys

source, target = sys.argv[1], sys.argv[2]
text = open(source, encoding="utf-8").read()
token = re.search(r'experimental_bearer_token\s*=\s*"([^"]+)"', text).group(1)
base = re.search(r'base_url\s*=\s*"([^"]+)"', text).group(1)
with open(target, "w", encoding="utf-8") as handle:
    handle.write(
        'model_provider = "real"\n'
        'model = "gpt-5.6-terra"\n\n'
        '[model_providers.real]\n'
        'name = "real"\n'
        f'base_url = "{base}"\n'
        'wire_api = "responses"\n'
        "request_max_retries = 0\n"
        "stream_max_retries = 2\n"
        "stream_idle_timeout_ms = 300000\n"
        f'http_headers = {{ "Authorization" = "Bearer {token}" }}\n'
    )
PY
cd "$HOME_DIR"
CODEX_HOME="$HOME_DIR" timeout 180 codex exec --skip-git-repo-check -s read-only \
  'Reply with exactly: OK' 2>&1 | tail -10
