set -Eeuo pipefail
BK=/opt/cliproxyapi/backups/20261026T164704.863011062Z-from-v8.0.43-canary-v8.0.44
printf 'auth_check_log='; test ! -e "$BK/auth-check.log" && echo absent || { grep -c 'FAILED' "$BK/auth-check.log" || true; }
printf 'image='; docker inspect --format '{{.Config.Image}} status={{.State.Status}}' cli-proxy-api
printf 'plugin='; sha256sum /opt/cliproxyapi/plugins/workbuddy.so
tail -16 /opt/cliproxyapi/canary-v8044.log
python3 - <<'PY'
import yaml
c=yaml.safe_load(open('/opt/cliproxyapi/config.yaml'))
w=c['plugins']['configs']['workbuddy']
print('identity_switch_on_429_present='+str('identity_switch_on_429' in w))
print('account_max_concurrent='+str(w.get('account_max_concurrent')))
PY
