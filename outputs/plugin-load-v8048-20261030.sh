set -Eeuo pipefail
umask 077
V=/root/cpa-v8048-plugin-0981-20261030
P=/opt/cliproxyapi/plugins/workbuddy.so
test -f "$P"
sha256sum "$P"
unshare --mount --net bash -c '
set -Eeuo pipefail
mount --make-rprivate /
V=/root/cpa-v8048-plugin-0981-20261030
mount --bind "$V" /opt/cliproxyapi
mount --bind "$V/tmp" /tmp
mount --bind "$V/run" /run
mount --bind "$V/state" /var/lib/vps-ssh-launcher
ip link set lo up
test -z "$(ip route show default)"
cd /opt/cliproxyapi
python3 - <<"PY"
import yaml
from pathlib import Path
p=Path("config.yaml"); c=yaml.safe_load(p.read_text())
c["plugins"]={"enabled":True,"dir":"plugins","configs":{"workbuddy":{"enabled":True,"scheduler_mode":"credits","account_max_concurrent":1,"identity_switch_on_429":False,"checkin_auto":False,"tasks_auto":False,"lifecycle_auto":False,"token_keepalive":False,"models_cn":"deepseek-v4.1-flash","models_intl":"deepseek-v4.1-flash"}}}
Path("plugin-config.yaml").write_text(yaml.safe_dump(c))
PY
./CLIProxyAPI -config plugin-config.yaml >plugin-load.log 2>&1 &
pid=$!
trap "kill $pid 2>/dev/null || true; wait $pid 2>/dev/null || true" EXIT
sleep 4
kill -0 "$pid"
grep "plugin registered plugin_id=workbuddy .*version=0.9.81" plugin-load.log
grep "account_max_concurrent=1 applied" plugin-load.log
! grep -Ei "panic|fatal|failed to load" plugin-load.log
echo ISOLATED_PLUGIN_LOAD=PASS
'
