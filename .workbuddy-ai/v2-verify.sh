echo "== hashes =="
sha256sum /opt/cliproxyapi/config.yaml /etc/nginx/conf.d/cpa-gateway.conf
echo "== marker =="
ls -l /opt/cliproxyapi/oauth-quarantine.json 2>&1 | sed -E 's/[0-9a-f]{16}/<PATH16>/g'
echo "== guardrails backup dirs =="
ls -1d /root/cpa-guardrails-backup-* 2>/dev/null | tail -3
echo "== oauth-quarantine backup dirs =="
ls -1d /root/cpa-oauth-quarantine-backup-* 2>/dev/null | tail -3
echo "== container started =="
docker inspect -f '{{.State.StartedAt}} restartcount={{.RestartCount}}' cli-proxy-api
