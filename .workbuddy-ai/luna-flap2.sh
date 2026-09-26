DIR=/opt/cliproxyapi
KEY=$(python3 -c "import yaml;print(yaml.safe_load(open('$DIR/config.yaml'))['api-keys'][0])")
dump() {
  curl --noproxy '*' -fsS --max-time 8 -H "Authorization: Bearer $KEY" http://127.0.0.1:8317/v1/models 2>/dev/null | python3 -c 'import json,sys
d=json.load(sys.stdin)
ids=sorted(x["id"] for x in d.get("data",[]) if isinstance(x,dict))
print("IDS[%s] n=%d %s" % (sys.argv[1], len(ids), ",".join(ids)))' "$1"
}
dump A
sleep 15
dump B
echo "--- cds files ---"
ls -1 "$DIR"/auth/*.cds 2>/dev/null | wc -l
