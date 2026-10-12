set -Eeuo pipefail
umask 077
DIR=/opt/cliproxyapi
BK="$DIR/backups/$(date -u +%Y%m%dT%H%M%S.%NZ)-config-streaming-keepalive"
cp -a "$DIR/config.yaml" "$BK"
chmod 600 "$BK"
echo "BACKUP=$BK"
python3 - "$DIR/config.yaml" <<'PY'
import os, re, sys
from pathlib import Path
import yaml
p = Path(sys.argv[1])
text = p.read_text(encoding="utf-8")
cfg = yaml.safe_load(text)
req = cfg.get("requests")
if not isinstance(req, dict):
    raise SystemExit("REFUSE requests_not_mapping")
if "streaming" in req:
    raise SystemExit("REFUSE streaming_already_present")
new_text, n = re.subn(
    r"(?m)^requests:\s*\n",
    "requests:\n  streaming:\n    keepalive-seconds: 15\n    send-keepalive-before-ft: true\n",
    text,
)
if n != 1:
    raise SystemExit(f"REFUSE anchor_count={n}")
expected = {**cfg, "requests": {**req, "streaming": {"keepalive-seconds": 15, "send-keepalive-before-ft": True}}}
parsed = yaml.safe_load(new_text)
if parsed != expected:
    raise SystemExit("REFUSE post_parse_mismatch")
tmp = p.with_suffix(".yaml.new")
tmp.write_text(new_text, encoding="utf-8")
os.chmod(tmp, p.stat().st_mode & 0o777)
os.replace(tmp, p)
print("CONFIG_KEEPALIVE_EDITED keys=requests.streaming.keepalive-seconds=15,requests.streaming.send-keepalive-before-ft=true")
PY
cd "$DIR" && docker compose restart cli-proxy-api
sleep 4
docker inspect --format 'status={{.State.Status}} restart={{.RestartCount}} started={{.State.StartedAt}} image={{.Config.Image}}' cli-proxy-api
