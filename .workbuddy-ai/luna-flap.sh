DIR=/opt/cliproxyapi
KEY=$(python3 -c "import yaml;print(yaml.safe_load(open('$DIR/config.yaml'))['api-keys'][0])")
for i in $(seq 1 12); do
  BODY=$(curl --noproxy '*' -fsS --max-time 8 -H "Authorization: Bearer $KEY" http://127.0.0.1:8317/v1/models 2>/dev/null || echo '')
  if [ -z "$BODY" ]; then
    echo "SAMPLE=$i catalog=request_failed"
  else
    printf '%s' "$BODY" | python3 -c 'import json,sys
d=json.load(sys.stdin)
ids={x["id"] for x in d.get("data",[]) if isinstance(x,dict)}
print("SAMPLE=%s luna6=%s luna56=%s n=%d" % (sys.argv[1], "gpt-6-luna" in ids, "gpt-5.6-luna" in ids, len(ids)))' "$i"
  fi
  sleep 2
done
echo "--- auth dir ---"
ls -1 "$DIR/auth" 2>/dev/null | sed 's/^/authfile=/'
echo "--- oauth error-ish log line count, last 20m ---"
docker logs cli-proxy-api --since 20m 2>&1 | grep -ciE 'invalid_grant|refresh_token_reused' || true
