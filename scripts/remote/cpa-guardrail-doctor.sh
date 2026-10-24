set -u
STRICT=1
DOCTOR_FAILED=0
DIR=/opt/cliproxyapi
ADMISSION_INTEGRITY_CHECK=/usr/local/libexec/cpa-admission-integrity-check
ADMISSION_INTEGRITY_DROPIN=/etc/systemd/system/cpa-admission.service.d/10-integrity.conf
ADMISSION_INTEGRITY_PIN=/etc/vps-ssh-launcher/cpa-admission.sha256

mark_fail() {
  echo "$1=FAIL"
  DOCTOR_FAILED=1
}

echo "==cpa-doctor=="
date -u +%FT%TZ
hostname
echo "==container=="
if docker inspect --format "status={{.State.Status}} restart={{.RestartCount}} started={{.State.StartedAt}} image={{.Config.Image}}" cli-proxy-api; then
  :
else
  mark_fail container
fi
RUNNING_IMAGE=$(docker inspect --format "{{.Config.Image}}" cli-proxy-api 2>/dev/null || true)
if [ -n "$RUNNING_IMAGE" ] && docker image inspect --format "repo_digests={{json .RepoDigests}}" "$RUNNING_IMAGE" 2>/dev/null; then
  echo image-available=OK
else
  mark_fail image-available
fi
if docker inspect --format '{{.HostConfig.LogConfig.Config}}' cli-proxy-api 2>/dev/null | grep -Fq 'max-size:32m'; then
  echo container-log-rotation=OK
else
  mark_fail container-log-rotation
fi
if grep -Fq 'umask 077' "$DIR/compose.yml" &&
   grep -Fq 'exec ./CLIProxyAPI' "$DIR/compose.yml"; then
  echo compose-umask=OK
else
  mark_fail compose-umask
fi
echo "==listeners=="
if ss -ltnp | grep -E ":(8317|8318|8443)\b"; then
  :
else
  mark_fail listeners
fi
echo "==admission-integrity=="
# Clear any log an earlier run left behind: a check that fails before it ever
# reaches the checker must not print a stale success line as its reason.
rm -f /tmp/cpa-admission-integrity.log
# The unit ships no ExecStartPre of its own, so a value there can only come
# from the drop-in having been loaded: a file on disk is not a loaded unit.
# ExecStartPre then runs as the unit's own User=, so a checker or pin that
# only root can read would pass a root-only probe here and still block every
# future start of the service.
ADMISSION_SERVICE_USER=$(systemctl show cpa-admission.service -p User --value)
[ -n "$ADMISSION_SERVICE_USER" ] || ADMISSION_SERVICE_USER=root
if [ -x "$ADMISSION_INTEGRITY_CHECK" ] &&
   [ -f "$ADMISSION_INTEGRITY_DROPIN" ] &&
   grep -Fq 'ExecStartPre=/usr/local/libexec/cpa-admission-integrity-check' "$ADMISSION_INTEGRITY_DROPIN" &&
   systemctl show cpa-admission.service -p ExecStartPre --value |
     grep -Fq "$ADMISSION_INTEGRITY_CHECK" &&
   [ -f "$ADMISSION_INTEGRITY_PIN" ] &&
   [ "$(awk 'NF {print $1; exit}' "$ADMISSION_INTEGRITY_PIN")" = "__CPA_ADMISSION_PIN_VALUE__" ] &&
   runuser -u "$ADMISSION_SERVICE_USER" -- "$ADMISSION_INTEGRITY_CHECK" >/tmp/cpa-admission-integrity.log 2>&1; then
  echo admission-integrity=OK
else
  mark_fail admission-integrity
  echo "admission_integrity_service_user=$ADMISSION_SERVICE_USER"
  tail -n 3 /tmp/cpa-admission-integrity.log 2>/dev/null || true
fi
rm -f /tmp/cpa-admission-integrity.log
echo "==nginx-guardrails=="
if grep -Eq '^[[:space:]]*listen[[:space:]]+8443[[:space:]]+ssl;' /etc/nginx/conf.d/cpa-gateway.conf; then
  echo public-listen=OK
else
  mark_fail public-listen
fi
if grep -Eq 'location[[:space:]]+~[[:space:]]+\^/[0-9a-f]{16}/v1/' /etc/nginx/conf.d/cpa-gateway.conf; then
  echo random-path=OK
else
  mark_fail random-path
fi
if grep -Fq 'access_log /var/log/nginx/cpa_gateway.access.log cpa_safe;' /etc/nginx/conf.d/cpa-gateway.conf; then
  echo safe-access-log=OK
else
  mark_fail safe-access-log
fi
if grep -Fq 'time=[$time_local]' /etc/nginx/conf.d/cpa-gateway.conf; then
  echo safe-log-timestamp=OK
else
  mark_fail safe-log-timestamp
fi
if grep -Fq 'map $uri $cpa_route_class {' /etc/nginx/conf.d/cpa-gateway.conf &&
   grep -Fq 'route=$cpa_route_class' /etc/nginx/conf.d/cpa-gateway.conf; then
  echo safe-route-class=OK
else
  echo safe-route-class=LEGACY_UNPROJECTED
fi
if grep -Fq 'limit_req=$limit_req_status limit_conn=$limit_conn_status' /etc/nginx/conf.d/cpa-gateway.conf; then
  echo safe-limit-status=OK
else
  mark_fail safe-limit-status
fi
if grep -Fq 'retry_after=$cpa_retry_after_class' /etc/nginx/conf.d/cpa-gateway.conf; then
  echo safe-retry-after=OK
else
  mark_fail safe-retry-after
fi
# A 429 without Retry-After tells a well-behaved client nothing about how long to
# wait. The map keeps the header off every response the local limiters did not
# reject, so this asserts both halves of the contract: the header exists AND it
# is scoped by the throttle-status map rather than applied unconditionally.
if grep -Fq 'add_header Retry-After $cpa_throttle_retry_after always;' /etc/nginx/conf.d/cpa-gateway.conf &&
   grep -Fq 'map "$limit_req_status:$limit_conn_status" $cpa_throttle_retry_after {' /etc/nginx/conf.d/cpa-gateway.conf; then
  echo safe-throttle-retry-after=OK
else
  mark_fail safe-throttle-retry-after
fi
if grep -Fq 'client_max_body_size 32m;' /etc/nginx/conf.d/cpa-gateway.conf &&
   grep -Fq 'proxy_buffering off;' /etc/nginx/conf.d/cpa-gateway.conf &&
   grep -Fq 'proxy_read_timeout 300s;' /etc/nginx/conf.d/cpa-gateway.conf &&
   grep -Fq 'proxy_send_timeout 300s;' /etc/nginx/conf.d/cpa-gateway.conf; then
  echo gateway-transport=OK
else
  mark_fail gateway-transport
fi
if fail2ban-client get cpa-gateway logpath 2>/dev/null | grep -Fq '/var/log/nginx/cpa_gateway.access.log'; then
  echo fail2ban-file-monitor=OK
else
  mark_fail fail2ban-file-monitor
fi
if grep -Fq 'auth_request /_cpa_auth;' /etc/nginx/conf.d/cpa-gateway.conf &&
   grep -Fq 'auth_status=(401|403)' /etc/fail2ban/filter.d/cpa-gateway.conf; then
  echo client-auth-classification=OK
else
  mark_fail client-auth-classification
fi
if grep -Fq 'failregex = ^<HOST> method=[A-Z]+ status=(401|403) .* auth_status=(401|403)\s*$' /etc/fail2ban/filter.d/cpa-gateway.conf &&
   grep -Fq 'backend = polling' /etc/fail2ban/jail.d/cpa-gateway.conf &&
   grep -Fq 'logpath = /var/log/nginx/cpa_gateway.access.log tail' /etc/fail2ban/jail.d/cpa-gateway.conf; then
  echo fail2ban-contract=OK
else
  mark_fail fail2ban-contract
fi
# The jail must keep loopback out of its own ban list. nginx reaches CPA over
# 127.0.0.1:8317, so banning 127.0.0.1 for the 401s that this doctor itself
# generates on the public route would cut the gateway off from its upstream and
# take the whole service down. The thresholds are asserted too: they are the
# documented contract in the incident-response runbook, and a silent drift here
# changes both the self-ban window and the brute-force tolerance.
if grep -Fq 'ignoreip = 127.0.0.1/8 ::1' /etc/fail2ban/jail.d/cpa-gateway.conf &&
   grep -Fq 'maxretry = 20' /etc/fail2ban/jail.d/cpa-gateway.conf &&
   grep -Fq 'findtime = 600' /etc/fail2ban/jail.d/cpa-gateway.conf &&
   grep -Fq 'bantime = 3600' /etc/fail2ban/jail.d/cpa-gateway.conf &&
   grep -Fq 'bantime.increment = true' /etc/fail2ban/jail.d/cpa-gateway.conf &&
   grep -Fq 'bantime.factor = 2' /etc/fail2ban/jail.d/cpa-gateway.conf &&
   grep -Fq 'bantime.maxtime = 604800' /etc/fail2ban/jail.d/cpa-gateway.conf; then
  echo fail2ban-ban-scope=loopback_exempt_incremental
else
  mark_fail fail2ban-ban-scope
fi
# 20 = the sum of the three admission lanes' held-connection budgets:
# OAuth (2+4=6), GLM (3+4=7), and DeepSeek (3+4=7). The earlier values 6 and
# 12 were both reached by four concurrent desktop sessions because each session
# also opens auxiliary response/model connections; nginx answered 429 before
# admission ever saw the request. Raised 2026-10-02 after a second rejection
# at exactly 12 active connections; see
# docs/change-evidence/20261002-bwg-gateway-connection-budget.md.
if grep -Fq 'limit_conn cpa_cc 20;' /etc/nginx/conf.d/cpa-gateway.conf; then
  echo gateway-per-ip-concurrency=20
else
  mark_fail gateway-per-ip-concurrency
fi
# The request limiter directive itself, not just its zone declaration or the
# log-format variable, must survive on the deployed file: a dropped
# `limit_req` silently removes per-IP rate limiting while every log field
# still reports the (never-triggered) status variable.
if grep -Fq 'limit_req zone=cpa_rl burst=10;' /etc/nginx/conf.d/cpa-gateway.conf; then
  echo gateway-per-ip-rate-limit=OK
else
  mark_fail gateway-per-ip-rate-limit
fi
# Local throttling must answer 429, not nginx's default 503: a 503 makes a
# self-inflicted limit indistinguishable from upstream overload and gives
# clients no back-off signal. Set 2026-09-09; asserted here since 2026-09-26.
if grep -Fq 'limit_req_status 429;' /etc/nginx/conf.d/cpa-gateway.conf &&
   grep -Fq 'limit_conn_status 429;' /etc/nginx/conf.d/cpa-gateway.conf; then
  echo gateway-throttle-status=429
else
  mark_fail gateway-throttle-status
fi
# The 2026-09-30 443-fallback lane (00-cpa-443-http.conf + subscribe.conf on
# port 33387) is the entry path the ChatGPT desktop actually takes:
# fq.sciman.top:443 -> xray dokodemo-door -> VLESS REALITY -> target
# fq.sciman.top:33387 -> that server block. Its local limiters must stay
# equivalent to the canonical lane on the properties that were found drifted:
# the per-IP connection budget (a stale 6 let a single caller hold the whole
# desktop lane), the 429 rejection status (nginx's default 503 makes a
# self-inflicted rejection look like upstream overload and carries no back-off
# signal), and the request-body/SSE transport shape (a body above the buffer
# is written to a temporary file, and a missing proxy_buffering off or a
# default 60s read timeout truncates or stalls a stream). Every value is
# compared against the canonical lane's own directive instead of a pinned
# literal so the two cannot drift apart again. Lane presence is signalled by
# the private log_format marker; when the lane is absent the canonical
# expectations elsewhere stay at 1 and this block reports ABSENT, so no new
# fail-closed requirement is introduced for an unprojected state.
LANE443_CONF=/etc/nginx/conf.d/00-cpa-443-http.conf
LANE443_SERVER=/etc/nginx/conf.d/subscribe.conf
if [ -f "$LANE443_CONF" ] && grep -Fq 'log_format cpa_safe443' "$LANE443_CONF"; then
  canonical_conn_budget=$(grep -oE 'limit_conn cpa_cc [0-9]+;' /etc/nginx/conf.d/cpa-gateway.conf | head -n 1 | awk '{print $3}' | tr -d ';')
  lane443_conn_budget=$(grep -oE 'limit_conn cpa_cc443 [0-9]+;' "$LANE443_SERVER" 2>/dev/null | head -n 1 | awk '{print $3}' | tr -d ';')
  canonical_body_buffer=$(grep -oE 'client_body_buffer_size [0-9]+[a-z]?;' /etc/nginx/conf.d/cpa-gateway.conf | head -n 1 | awk '{print $2}' | tr -d ';')
  lane443_body_buffer=$(grep -oE 'client_body_buffer_size [0-9]+[a-z]?;' "$LANE443_SERVER" 2>/dev/null | head -n 1 | awk '{print $2}' | tr -d ';')
  if [ -n "$canonical_conn_budget" ] && [ "$lane443_conn_budget" = "$canonical_conn_budget" ] &&
     [ -n "$canonical_body_buffer" ] && [ "$lane443_body_buffer" = "$canonical_body_buffer" ] &&
     grep -Fq 'limit_req zone=cpa_rl443 burst=10;' "$LANE443_SERVER" &&
     grep -Fq 'limit_req_status 429;' "$LANE443_SERVER" &&
     grep -Fq 'limit_conn_status 429;' "$LANE443_SERVER" &&
     grep -Fq 'add_header Retry-After $cpa_throttle_retry_after443 always;' "$LANE443_SERVER" &&
     grep -Fq 'proxy_buffering off;' "$LANE443_SERVER" &&
     grep -Fq 'proxy_read_timeout 300s;' "$LANE443_SERVER" &&
     grep -Fq 'proxy_send_timeout 300s;' "$LANE443_SERVER" &&
     grep -Fq 'limit_req_zone $binary_remote_addr zone=cpa_rl443:1m rate=10r/s;' "$LANE443_CONF" &&
     grep -Fq 'limit_conn_zone $binary_remote_addr zone=cpa_cc443:1m;' "$LANE443_CONF"; then
    echo "gateway-443-lane=OK conn-budget=$lane443_conn_budget body-buffer=$lane443_body_buffer"
  else
    mark_fail gateway-443-lane
    echo "gateway_443_conn_budget=canonical:${canonical_conn_budget:-absent} lane:${lane443_conn_budget:-absent}"
    echo "gateway_443_body_buffer=canonical:${canonical_body_buffer:-absent} lane:${lane443_body_buffer:-absent}"
  fi
  # Same route contract as the canonical lane, probed over the desktop entry
  # itself: the capability prefix is the only way in, a bare /v1 path must not
  # reach CPA, and the auth_request must still gate the valid prefix. Probes go
  # to 127.0.0.1 so they land in the fail2ban ignoreip set and consume no
  # upstream quota (no key, and the 401 comes from the auth subrequest).
  lane443_prefix=$(grep -oE '/[0-9a-f]{16}/v1/' "$LANE443_SERVER" | head -n 1 | cut -d/ -f2)
  lane443_server=$(awk '/^[[:space:]]*server_name[[:space:]]/{gsub(";", "", $2); print $2; exit}' "$LANE443_SERVER")
  if [ -n "$lane443_prefix" ] && [ -n "$lane443_server" ]; then
    lane443_base="https://$lane443_server:33387"
    lane443_valid=$(curl --noproxy '*' -k -sS --connect-timeout 3 --max-time 6 -o /dev/null -w '%{http_code}' --resolve "$lane443_server:33387:127.0.0.1" "$lane443_base/$lane443_prefix/v1/models" 2>/dev/null || echo 000)
    lane443_bare=$(curl --noproxy '*' -k -sS --connect-timeout 3 --max-time 6 -o /dev/null -w '%{http_code}' --resolve "$lane443_server:33387:127.0.0.1" "$lane443_base/v1/models" 2>/dev/null || echo 000)
    lane443_wrong=$(curl --noproxy '*' -k -sS --connect-timeout 3 --max-time 6 -o /dev/null -w '%{http_code}' --resolve "$lane443_server:33387:127.0.0.1" "$lane443_base/0000000000000000/v1/models" 2>/dev/null || echo 000)
    if [ "$lane443_valid" = "401" ] && [ "$lane443_bare" = "404" ] && [ "$lane443_wrong" = "404" ]; then
      echo gateway-443-lane-route=OK
    else
      mark_fail gateway-443-lane-route
      echo "gateway_443_route_status=valid:$lane443_valid bare:$lane443_bare wrong:$lane443_wrong"
    fi
  else
    mark_fail gateway-443-lane-route-unguessable
  fi
else
  echo gateway-443-lane=ABSENT
fi
if grep -Eq '^[[:space:]]*error-logs-max-files:[[:space:]]*5[[:space:]]*$' "$DIR/config.yaml"; then
  echo error-logs-max-files=5
else
  mark_fail error-logs-max-files
fi
if grep -Eq '^[[:space:]]*logs-max-total-size-mb:[[:space:]]*32[[:space:]]*$' "$DIR/config.yaml"; then
  echo logs-max-total-size-mb=32
else
  mark_fail logs-max-total-size-mb
fi
if grep -Fq 'client_body_buffer_size 128k;' /etc/nginx/conf.d/cpa-gateway.conf; then
  echo client-body-buffer=OK
else
  mark_fail client-body-buffer
fi
if test -f /etc/logrotate.d/nginx && grep -Fq '/var/log/nginx/*.log' /etc/logrotate.d/nginx; then
  echo nginx-logrotate=OK
else
  mark_fail nginx-logrotate
fi
if test -e /etc/logrotate.d/cpa-gateway; then
  mark_fail duplicate-cpa-logrotate
else
  echo duplicate-cpa-logrotate=ABSENT
fi
if ss -ltn | grep -Eq '127\.0\.0\.1:8317[[:space:]]'; then
  echo cpa-loopback=OK
else
  mark_fail cpa-loopback
fi
if ss -ltn | grep -Eq '127\.0\.0\.1:8318[[:space:]]'; then
  echo admission-loopback=OK
else
  mark_fail admission-loopback
fi
if ss -ltn | grep -Eq '0\.0\.0\.0:8443[[:space:]]'; then
  echo nginx-public-socket=OK
else
  mark_fail nginx-public-socket
fi
if ss -ltn | grep -Eq '\[::\]:8443[[:space:]]|:::8443[[:space:]]'; then
  mark_fail nginx-public-ipv6-socket
else
  echo nginx-public-ipv6-socket=ABSENT
fi
if ss -ltn | grep -Eq '\[::\]:8317[[:space:]]|:::8317[[:space:]]'; then
  mark_fail cpa-ipv6-socket
else
  echo cpa-ipv6-socket=ABSENT
fi
if ss -ltn | grep -Eq '\[::\]:8318[[:space:]]|:::8318[[:space:]]'; then
  mark_fail admission-ipv6-socket
else
  echo admission-ipv6-socket=ABSENT
fi
if systemctl is-enabled --quiet cpa-admission.service &&
   systemctl is-active --quiet cpa-admission.service; then
  echo admission-service=enabled-active
else
  mark_fail admission-service
fi
if systemctl is-active --quiet cpa-luna-admission.service 2>/dev/null ||
   systemctl is-enabled --quiet cpa-luna-admission.service 2>/dev/null ||
   [ -e /etc/systemd/system/cpa-luna-admission.service ] ||
   [ -e "$DIR/cpa-luna-admission.py" ] ||
   [ -e "$DIR/cpa-luna-admission.json" ]; then
  mark_fail legacy-admission-residue
else
  echo legacy-admission=absent
fi
if curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8318/healthz |
   python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    raise SystemExit(1)
if data.get("status") != "ok":
    raise SystemExit(1)
if data.get("traffic_guard") != "/run/cpa-admission/requests.lock":
    raise SystemExit(1)
if data.get("retry_after_max_seconds") != 86400:
    raise SystemExit(1)
lanes = data.get("lanes")
if not isinstance(lanes, dict):
    raise SystemExit(1)
expected = {
    "chatgpt-oauth": ["gpt-6-luna", "gpt-5.6-luna", "gpt-6.1-sol"],
    "zhipu-coding-plan": ["glm-5.3", "glm-5.3-flash"],
    "antigravity-oauth": [
        "gemini-3.8-flash-high",
    ],
    "workbuddy-oauth": ["deepseek-v4.1-flash"],
}
expected_max_inflight = {
    "chatgpt-oauth": 2,
    "zhipu-coding-plan": 3,
    "antigravity-oauth": 2,
    "workbuddy-oauth": 1,
}
if set(lanes) != set(expected):
    raise SystemExit(1)
for name, models in expected.items():
    state = lanes.get(name)
    if (
        not isinstance(state, dict)
        or state.get("models") != models
        or state.get("max_inflight") != expected_max_inflight[name]
        or state.get("max_pending") != 4
        or state.get("queue_timeout_seconds") != 120
    ):
        raise SystemExit(1)
    # The deployed generation must expose the per-model breaker. A snapshot
    # without it is an admission build that parks the whole lane when the
    # upstream names one capped model, so the siblings of that model on the
    # same credential are refused too (measured 2026-10-10).
    snapshot = state.get("state")
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("cooldown_scope") not in {"lane", "model", "none"}
        or not isinstance(snapshot.get("model_cooldowns"), dict)
    ):
        raise SystemExit(1)
    if name in {"antigravity-oauth", "workbuddy-oauth"} and (
        snapshot.get("strict_backoff") is not True
        or snapshot.get("state_persisted") is not True
        or snapshot.get("state_write_failed") is not False
    ):
        raise SystemExit(1)
    if name == "chatgpt-oauth" and (
        snapshot.get("state_persisted") is not True
        or snapshot.get("state_write_failed") is not False
    ):
        raise SystemExit(1)
    if name in {"antigravity-oauth", "workbuddy-oauth", "chatgpt-oauth"}:
        # Report the running breaker, not the write-ahead disk marker which
        # legitimately says unverified_request while a request is in flight.
        reason = snapshot.get("hold_reason")
        known_reasons = {
            "authentication_required", "account_restricted",
            "retry_window_review_required", "unverified_request",
            "state_write_failed",
        }
        safe_reason = reason if reason in known_reasons else "unknown"
        posture = "OPERATOR_REVIEW_REQUIRED" if reason else (
            "WAIT_UPSTREAM_WINDOW"
            if snapshot.get("cooldown_active") or snapshot.get("model_cooldowns")
            else "OPEN"
        )
        safe_reason = safe_reason if reason else "none"
        print(f"oauth_lane={name} posture={posture} hold_reason={safe_reason}")
'; then
  echo admission-health=OK
else
  mark_fail admission-health
fi
if python3 - <<'PY'
import grp, pathlib, pwd
p = pathlib.Path('/var/lib/cpa-admission/antigravity-risk.json')
uid = pwd.getpwnam('nobody').pw_uid
gid = grp.getgrnam('nogroup').gr_gid
assert not p.is_symlink() and p.is_file()
assert p.stat().st_uid == uid and p.stat().st_gid == gid and p.stat().st_mode & 0o777 == 0o600
assert p.parent.stat().st_uid == uid and p.parent.stat().st_gid == gid and p.parent.stat().st_mode & 0o777 == 0o700
PY
then
  echo antigravity-risk-state-permissions=OK
else
  mark_fail antigravity-risk-state-permissions
fi
if python3 - <<'PY'
import grp, pathlib, pwd
p = pathlib.Path('/var/lib/cpa-admission/claude-risk.json')
uid = pwd.getpwnam('nobody').pw_uid
gid = grp.getgrnam('nogroup').gr_gid
assert not p.is_symlink()
# The claude-oauth lane retired 2026-10-20: the persisted risk file is dead
# residue, so absence is the healthy end state and only a present file is
# permission-checked.
if not p.is_file():
    print("claude-risk-state-permissions=ABSENT lane retired")
    raise SystemExit(0)
assert p.stat().st_uid == uid and p.stat().st_gid == gid and p.stat().st_mode & 0o777 == 0o600
assert p.parent.stat().st_uid == uid and p.parent.stat().st_gid == gid and p.parent.stat().st_mode & 0o777 == 0o700
PY
then
  echo claude-risk-state-permissions=OK
else
  mark_fail claude-risk-state-permissions
fi
if python3 - <<'PY'
import grp, pathlib, pwd
p = pathlib.Path('/var/lib/cpa-admission/chatgpt-risk.json')
uid = pwd.getpwnam('nobody').pw_uid
gid = grp.getgrnam('nogroup').gr_gid
assert not p.is_symlink() and p.is_file()
assert p.stat().st_uid == uid and p.stat().st_gid == gid and p.stat().st_mode & 0o777 == 0o600
assert p.parent.stat().st_uid == uid and p.parent.stat().st_gid == gid and p.parent.stat().st_mode & 0o777 == 0o700
PY
then
  echo chatgpt-risk-state-permissions=OK
else
  mark_fail chatgpt-risk-state-permissions
fi
if python3 - <<'PY'
import grp, pathlib, pwd
p = pathlib.Path('/var/lib/cpa-admission/workbuddy-risk.json')
uid = pwd.getpwnam('nobody').pw_uid
gid = grp.getgrnam('nogroup').gr_gid
assert not p.is_symlink() and p.is_file()
assert p.stat().st_uid == uid and p.stat().st_gid == gid and p.stat().st_mode & 0o777 == 0o600
assert p.parent.stat().st_uid == uid and p.parent.stat().st_gid == gid and p.parent.stat().st_mode & 0o777 == 0o700
PY
then
  echo workbuddy-risk-state-permissions=OK
else
  mark_fail workbuddy-risk-state-permissions
fi
MGMT_ALLOW=$(grep -E '^[[:space:]]*allow-remote:' "$DIR/config.yaml" | head -1 | sed 's/.*:[[:space:]]*//')
MGMT_KEY=$(grep -A3 '^management:' "$DIR/config.yaml" | grep 'secret-key:' | sed 's/.*secret-key:[[:space:]]*//;s/"//g')
# Two acceptable states: fully disabled, or keyed management behind the
# loopback-only 8317 binding (docker-proxy forwards non-loopback source IPs,
# so tunnel/panel access needs allow-remote=true with a strong key; the
# binding assertion below keeps it off the public network either way).
if [ "$MGMT_ALLOW" = "false" ]; then
  echo management-remote=DISABLED
elif [ "$MGMT_ALLOW" = "true" ] && [ "${#MGMT_KEY}" -ge 32 ]; then
  echo management-remote=LOOPBACK_KEYED
else
  mark_fail management-remote
fi
if grep -qi 'management' /etc/nginx/conf.d/cpa-gateway.conf; then
  mark_fail nginx-no-management-route
else
  echo nginx-no-management-route=OK
fi
if python3 - "$DIR/config.yaml" "$DIR/auth" <<'PY'
import json
import sys
from pathlib import Path
import yaml

def contains_enabled(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).replace('_', '-') == 'identity-confuse' and child is True:
                return True
            if contains_enabled(child):
                return True
    elif isinstance(value, list):
        return any(contains_enabled(child) for child in value)
    return False

config = yaml.safe_load(Path(sys.argv[1]).read_text(encoding='utf-8'))
if contains_enabled(config):
    raise SystemExit(1)
for path in Path(sys.argv[2]).glob('*.json'):
    if contains_enabled(json.loads(path.read_text(encoding='utf-8'))):
        raise SystemExit(1)
PY
then
  echo identity-confuse=ABSENT
else
  mark_fail identity-confuse
fi
if [ -f "$DIR/cpa_policy.py" ] && python3 "$DIR/cpa_policy.py" "$DIR/config.yaml"; then
  echo semantic-policy=OK
else
  mark_fail semantic-policy
fi
if python3 - "$DIR/auth" <<'PY'
import stat
import sys
from pathlib import Path

auth = Path(sys.argv[1])
if not auth.is_dir() or stat.S_IMODE(auth.stat().st_mode) != 0o700:
    raise SystemExit(1)
for path in auth.iterdir():
    if path.is_file() and path.suffix in {'.json', '.cds'}:
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise SystemExit(1)
PY
then
  echo auth-permissions=OK
else
  mark_fail auth-permissions
fi
if python3 - "$DIR/config.yaml" <<'PY'
import stat
import sys
from pathlib import Path

# config.yaml carries every provider API key in cleartext, so owner-only is the
# invariant -Apply enforces. It was previously only printed by `stat` and never
# gated, letting a permission drift leak credentials while the doctor passed.
mode = stat.S_IMODE(Path(sys.argv[1]).stat().st_mode)
raise SystemExit(0 if mode & 0o077 == 0 else 1)
PY
then
  echo config-permissions=owner-only
else
  mark_fail config-permissions
fi
echo "==oauth-monitor=="
if python3 - "$DIR/auth" "$DIR/auth/logs" <<'PY'
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

auth_dir = Path(sys.argv[1])
logs_dir = Path(sys.argv[2])
now = dt.datetime.now(dt.timezone.utc)
expiry_keys = {
    "expired",
    "expires",
    "expiry",
    "expires_at",
    "expiresat",
    "access_token_expires",
    "access_token_expires_at",
}
refresh_keys = {
    "last_refresh",
    "last_refresh_at",
    "last_refreshed",
    "refreshed_at",
}


def parse_time(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            seconds = value / 1000 if value > 10_000_000_000 else value
            return dt.datetime.fromtimestamp(seconds, dt.timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def values_for_keys(value, wanted):
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in wanted:
                found.append(child)
            found.extend(values_for_keys(child, wanted))
    elif isinstance(value, list):
        for child in value:
            found.extend(values_for_keys(child, wanted))
    return found


active = []
active_providers = set()
provider_records = {"claude": [], "antigravity": []}
for path in sorted(auth_dir.glob("*.json")):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        continue
    if not isinstance(data, dict):
        continue
    provider = str(data.get("type", "")).lower()
    if data.get("disabled") is not True:
        active_providers.add(provider)
    if provider in provider_records and data.get("disabled") is not True:
        provider_records[provider].append(data)
    keys = {str(key).lower() for key in data}
    if str(data.get("type", "")).lower() == "codex" and {
        "access_token",
        "refresh_token",
    } & keys:
        active.append(data)

# Only provider-labelled refresh diagnostics establish ownership. Error text
# may contain identifiers, so emit counts and never raw lines or auth IDs.
provider_refresh_signals = {name: 0 for name in ("codex", "claude", "antigravity")}
unattributed_container_signals = 0
container_coverage = "unavailable"
try:
    completed = subprocess.run(
        ["docker", "logs", "--since", "168h", "--tail", "20000", "cli-proxy-api"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    if completed.returncode == 0:
        container_coverage = "retained_container_tail_only_incomplete"
        for line in (completed.stdout + "\n" + completed.stderr).splitlines():
            labelled = re.search(
                r"credential refresh (?:before fallback )?failed for (codex|claude|antigravity) \(",
                line, re.IGNORECASE,
            )
            if labelled:
                provider_refresh_signals[labelled[1].lower()] += 1
            elif re.search(r"credential refresh failed|invalid_grant|refresh_token_reused", line, re.IGNORECASE):
                unattributed_container_signals += 1
except (OSError, subprocess.TimeoutExpired):
    pass
print(f"oauth_refresh_unattributed_container_signals_7d={unattributed_container_signals}")
print(f"oauth_refresh_container_coverage={container_coverage}")

# Short-lived access-token expiry is a refresh requirement, not a ban verdict.
# Do not infer provider acceptance or attribute generic log errors to an account.
for provider, records in provider_records.items():
    prefix = f"oauth_{provider}"
    print(f"{prefix}_files={len(records)}")
    print(f"{prefix}_refresh_failure_coverage=provider_labelled_container_only; dumps_unattributed")
    print(f"{prefix}_refresh_failures_7d={provider_refresh_signals[provider]}")
    if not records:
        status = "ABSENT_OPTIONAL"
    elif len(records) != 1:
        status = "WARN_MULTIPLE_ACTIVE_FILES"
    else:
        record = records[0]
        expiry = next(
            (parsed for value in values_for_keys(record, expiry_keys)
             if (parsed := parse_time(value))), None,
        )
        refresh = next(
            (parsed for value in values_for_keys(record, refresh_keys)
             if (parsed := parse_time(value))), None,
        )
        print(f"{prefix}_expiry_known={'true' if expiry else 'false'}")
        print(f"{prefix}_refresh_age_hours=" + (
            f"{max(0.0, (now - refresh).total_seconds() / 3600):.1f}"
            if refresh else "unknown"
        ))
        status = (
            "WARN_EXPIRY_UNKNOWN" if expiry is None
            else "WARN_ACCESS_TOKEN_EXPIRED_REFRESH_REQUIRED" if expiry <= now
            else "METADATA_OK_NOT_PROVIDER_ACCEPTANCE"
        )
        if provider_refresh_signals[provider]:
            status = "WARN_REFRESH_SIGNAL_REVIEW_REQUIRED"
    print(f"{prefix}_monitor={status}")

print(f"oauth_codex_files={len(active)}")
print("oauth_monitor_scope=codex_only; other_providers_have_separate_status")
if not active:
    print("oauth_codex=absent")
    print("oauth_refresh_dump_coverage=not_scanned_without_codex")
    print("oauth_monitor=ABSENT_OPTIONAL")
    raise SystemExit(0)
if len(active) != 1:
    print("oauth_codex=ambiguous")
    print("oauth_monitor=FAIL_MULTIPLE_ACTIVE_FILES")
    raise SystemExit(1)

record = active[0]
expiry = next(
    (parsed for value in values_for_keys(record, expiry_keys) if (parsed := parse_time(value))),
    None,
)
explicit_expired = next(
    (value for value in values_for_keys(record, {"expired"}) if isinstance(value, bool)),
    None,
)
if expiry is not None:
    expired = expiry <= now
    days_left = int((expiry - now).total_seconds() // 86400)
    print(f"oauth_expired={'true' if expired else 'false'}")
    print(f"oauth_days_left={days_left}")
    print(f"oauth_hours_left={(expiry - now).total_seconds() / 3600:.1f}")
    print("oauth_refresh_policy=lead24h_grace2h")
else:
    expired = explicit_expired
    print(
        "oauth_expired="
        + ("true" if expired is True else "false" if expired is False else "unknown")
    )
    print("oauth_days_left=unknown")

refresh = next(
    (parsed for value in values_for_keys(record, refresh_keys) if (parsed := parse_time(value))),
    None,
)
if refresh is None:
    print("oauth_refresh_age_hours=unknown")
else:
    age_hours = max(0.0, (now - refresh).total_seconds() / 3600)
    print(f"oauth_refresh_age_hours={age_hours:.1f}")

# Request bodies are untrusted client text: a failed request whose prompt
# merely mentions oauth failure keywords must never count as a credential
# failure (2026-09-21 false positive). Only response-side dump sections
# (API ERROR RESPONSE / API RESPONSE / RESPONSE) carry upstream error
# evidence; one matching file is one event, never one per regex hit.
refresh_signal_re = re.compile(
    r"invalid_grant|refresh_token_reused|refresh[_ ]token[^\n]{0,80}expired|oauth[^\n]{0,80}\b401\b",
    re.IGNORECASE,
)
error_section_markers = {
    "=== api error response ===",
    "=== api response ===",
    "=== response ===",
}


def dump_error_evidence(path):
    # Split a CLIProxyAPI error dump into (timestamp, error-section text).
    # REQUEST INFO/HEADERS/REQUEST BODY/API REQUEST sections are dropped:
    # headers are masked by CPA, but request bodies are plaintext client
    # payloads and the single source of the 2026-09-21 contamination.
    timestamp = None
    error_lines = []
    in_error_section = False
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None, ""
    for line in text.splitlines():
        marker = line.strip().lower()
        if marker.startswith("=== ") and marker.endswith(" ==="):
            in_error_section = marker in error_section_markers
            continue
        if in_error_section:
            error_lines.append(line)
        elif timestamp is None and line.startswith("Timestamp:"):
            timestamp = line.split(":", 1)[1].strip()
    return timestamp, "\n".join(error_lines)


def parse_dump_time(value, fallback_ts):
    # Dump timestamps carry nanosecond fractions that fromisoformat rejects.
    if value:
        trimmed = re.sub(r"(\.\d{6})\d+", r"\1", value).replace("Z", "+00:00")
        try:
            parsed = dt.datetime.fromisoformat(trimmed)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            pass
    return dt.datetime.fromtimestamp(fallback_ts, dt.timezone.utc)


dump_signals = 0
unattributed_dump_signals = 0
newest_signal_time = None
cutoff = now.timestamp() - 7 * 86400
for path in logs_dir.glob("error-*.log"):
    try:
        stat = path.stat()
    except OSError:
        continue
    if stat.st_mtime < cutoff or stat.st_size > 20_000_000:
        continue
    timestamp, error_text = dump_error_evidence(path)
    if not error_text or not refresh_signal_re.search(error_text):
        continue
    # Dumps have no trustworthy credential/provider ownership field. With
    # mixed OAuth credentials do not blame Codex for another provider error.
    if active_providers != {"codex"}:
        unattributed_dump_signals += 1
        continue
    dump_signals += 1
    event_time = parse_dump_time(timestamp, stat.st_mtime)
    if newest_signal_time is None or event_time > newest_signal_time:
        newest_signal_time = event_time

container_signals = provider_refresh_signals["codex"]
if active_providers == {"codex"}:
    container_signals += unattributed_container_signals
print(f"oauth_refresh_unattributed_dump_signals_7d={unattributed_dump_signals}")

signals = dump_signals + container_signals
# A successful refresh after the newest retained signal means the failure
# was transient and already recovered: report it, but do not keep blocking
# until the dump ages out of the bounded retention window.
resolved = (
    signals > 0
    and container_signals == 0
    and refresh is not None
    and newest_signal_time is not None
    and refresh > newest_signal_time
)
print(f"oauth_refresh_failures_7d={signals}")
if resolved:
    print("oauth_refresh_signals_resolved=true")
print("oauth_refresh_coverage=retained_error_dumps_and_container_log_only; incomplete_bounded_sample")
if signals and not resolved:
    print("oauth_monitor=FAIL_REFRESH_SIGNAL")
    raise SystemExit(1)
if expired is True:
    print("oauth_monitor=FAIL_EXPIRED")
    raise SystemExit(1)
if expiry is None:
    print("oauth_monitor=FAIL_EXPIRY_UNKNOWN")
    raise SystemExit(1)
hours_left = (expiry - now).total_seconds() / 3600
# CLIProxyAPI refreshes codex OAuth at expiry-24h (sdk/auth RefreshLead). Its
# expiry-aware scheduler bounds timer waits to 30s and uses a 5m failure
# backoff. Grade on exact remaining hours, not floored days: integer-day
# thresholds still block up to ~24h before the refresh point. Blocking starts
# only after the refresh window is entered AND a 2h scheduling grace has
# passed without the expiry rolling; 72h out is a non-blocking renewal notice.
if hours_left <= 22:
    print("oauth_monitor=ACTION_REQUIRED_REENROLL_OR_VERIFY_REFRESH")
    raise SystemExit(1)
if hours_left <= 72:
    print("oauth_monitor=WARN_RENEWAL_WINDOW")
else:
    print("oauth_monitor=OK")
PY
then
  :
else
  mark_fail oauth-monitor
fi
echo "==oauth-quarantine=="
if python3 - "$DIR" <<'PY'
import json
import sys
from pathlib import Path

import yaml

root = Path(sys.argv[1])
marker_path = root / "oauth-quarantine.json"
try:
    config = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
except Exception as exc:
    print("oauth_quarantine=UNAVAILABLE exc=" + type(exc).__name__)
    raise SystemExit(0)
oauth_block = config.get("oauth") if isinstance(config, dict) else None
exclusions = oauth_block.get("excluded-models") if isinstance(oauth_block, dict) else None
codex = exclusions.get("codex", []) if isinstance(exclusions, dict) else []
patterns = {
    pattern.strip().lower()
    for pattern in codex
    if isinstance(pattern, str) and pattern.strip()
}
try:
    manifest = json.loads(
        (root / "cpa_provider_routes.json").read_text(encoding="utf-8")
    )
    oauth_aliases = sorted(
        {
            model["alias"].lower()
            for route in manifest.get("oauth_routes", [])
            if isinstance(route, dict)
            and route.get("name") == "chatgpt-plus-oauth"
            for model in route.get("models", [])
            if isinstance(model, dict) and isinstance(model.get("alias"), str)
        }
    )
except Exception:
    oauth_aliases = []
blocked = sorted(alias for alias in oauth_aliases if alias in patterns)
if not marker_path.exists():
    # A blocked OAuth route without the operator marker is an unreviewed lane
    # change; the semantic policy also fails closed on it, and the doctor must
    # not read the state as a clean "no quarantine" contract.
    if blocked:
        print("oauth_quarantine=UNMARKED_OAUTH_BLOCK blocked=" + ",".join(blocked))
        raise SystemExit(1)
    print("oauth_quarantine=none")
    raise SystemExit(0)
try:
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
except Exception as exc:
    print("oauth_quarantine=INVALID_MARKER exc=" + type(exc).__name__)
    raise SystemExit(1)
if (
    not isinstance(marker, dict)
    or marker.get("version") != 1
    or marker.get("state") != "quarantined"
):
    print("oauth_quarantine=INVALID_MARKER")
    raise SystemExit(1)
aliases_value = marker.get("aliases")
declared = []
if isinstance(aliases_value, list):
    declared = sorted(
        {
            alias.strip().lower()
            for alias in aliases_value
            if isinstance(alias, str) and alias.strip()
        }
    )
if not declared or not set(declared) <= set(oauth_aliases):
    print("oauth_quarantine=INVALID_MARKER")
    raise SystemExit(1)
print("oauth_quarantine=active aliases=" + ",".join(declared))
print("oauth_quarantine_since=" + str(marker.get("since", "unknown")))
print("oauth_quarantine_reason=" + str(marker.get("reason", "unknown")))
if declared != blocked:
    print(
        "oauth_quarantine=INCONSISTENT declared="
        + ",".join(declared)
        + " blocked="
        + ",".join(blocked)
    )
    raise SystemExit(1)
print(
    "oauth_quarantine_note=credential_retained; background_refresh_continues; "
    "not_a_reset_mechanism"
)
raise SystemExit(0)
PY
then
  :
else
  mark_fail oauth-quarantine
fi
echo "==antigravity-lane=="
# The Antigravity (Google AI Pro) shared lane must stay pinned to the aliases
# declared in its route: the deployed provider-level exclusion list has to
# match the manifest exactly and must never block a declared alias. Drift here
# means the lane silently widened (missing exclusion) or narrowed to nothing
# (blocked alias).
python3 - "$DIR/config.yaml" "$DIR/cpa_provider_routes.json" <<'PY'
import json
import sys
from pathlib import Path

import yaml

root = Path(sys.argv[1]).parent
try:
    config = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
    manifest = json.loads(open(sys.argv[2], encoding="utf-8").read())
except Exception as exc:
    print("antigravity_lane=UNAVAILABLE exc=" + type(exc).__name__)
    raise SystemExit(1)
oauth_block = config.get("oauth") if isinstance(config, dict) else None
exclusions = (
    oauth_block.get("excluded-models") if isinstance(oauth_block, dict) else None
)
entries = exclusions.get("antigravity") if isinstance(exclusions, dict) else None
if entries is None:
    entries = []
if not isinstance(entries, list) or not all(
    isinstance(pattern, str) and pattern.strip() for pattern in entries
):
    print("antigravity_lane=INVALID_TYPE")
    raise SystemExit(1)
actual = {
    pattern.strip().lower()
    for pattern in entries
    if isinstance(pattern, str) and pattern.strip()
}
route = next(
    (
        item
        for item in manifest.get("oauth_routes", [])
        if isinstance(item, dict) and item.get("name") == "antigravity-oauth"
    ),
    None,
)
allowed = {
    model["alias"].strip().lower()
    for model in (route or {}).get("models", [])
    if isinstance(model, dict) and isinstance(model.get("alias"), str)
}
expected = {
    pattern.strip().lower()
    for pattern in manifest.get("antigravity_oauth_exclusions", [])
    if isinstance(pattern, str) and pattern.strip()
}
sys.path.insert(0, str(root))
from cpa_policy import load_quarantine_aliases

quarantined, quarantine_issues = load_quarantine_aliases(provider="antigravity")
if quarantine_issues:
    print("antigravity_lane=INVALID_QUARANTINE_MARKER")
    raise SystemExit(1)
expected.update(quarantined)
if not allowed:
    print("antigravity_lane=UNDECLARED_ROUTE")
    raise SystemExit(1)
if actual != expected:
    print(
        "antigravity_lane=EXCLUSION_DRIFT"
        + " want=" + (",".join(sorted(expected)) or "none")
        + " got=" + (",".join(sorted(actual)) or "none")
    )
    raise SystemExit(1)
if actual & (allowed - quarantined):
    print("antigravity_lane=BLOCKED_DECLARED_ALIAS aliases=" + ",".join(sorted(actual & allowed)))
    raise SystemExit(1)
print(
    "antigravity_lane=" + ("QUARANTINED" if quarantined else "OK")
    + " allowed=" + (",".join(sorted(allowed - quarantined)) or "none")
    + " excluded=" + (",".join(sorted(actual)) or "none")
)
raise SystemExit(0)
PY
ANTIGRAVITY_LANE_STATUS=$?
if [ "$ANTIGRAVITY_LANE_STATUS" -eq 0 ]; then
  :
else
  mark_fail antigravity-lane
fi
unset ANTIGRAVITY_LANE_STATUS
echo "==claude-lane=="
# The claude-oauth lane retired 2026-10-20 (account suspended by Anthropic,
# credential removed by the operator). The route must stay out of the
# manifest: a resurrection without a reviewed re-attach transaction fails
# here instead of silently re-advertising the models.
python3 - "$DIR/cpa_provider_routes.json" <<'PY'
import json
import sys

manifest = json.loads(open(sys.argv[1], encoding="utf-8").read())
route = next(
    (
        item
        for item in manifest.get("oauth_routes", [])
        if isinstance(item, dict) and item.get("name") == "claude-oauth"
    ),
    None,
)
if route is not None:
    print("claude_lane=RESURRECTED route must be removed or re-attached via a reviewed transaction")
    raise SystemExit(1)
print("claude_lane=RETIRED no_oauth_route")
raise SystemExit(0)
PY
CLAUDE_LANE_STATUS=$?
if [ "$CLAUDE_LANE_STATUS" -eq 0 ]; then
  :
else
  mark_fail claude-lane
fi
unset CLAUDE_LANE_STATUS
PORT_JSON=$(docker inspect --format '{{json .HostConfig.PortBindings}}' cli-proxy-api 2>/dev/null || true)
if [ -n "$PORT_JSON" ] && python3 - "$PORT_JSON" <<'PY'
import json
import sys

try:
    bindings = json.loads(sys.argv[1])
except (IndexError, json.JSONDecodeError):
    raise SystemExit(1)
expected = {"8317/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8317"}]}
if bindings != expected:
    raise SystemExit(1)
PY
then
  echo cpa-port-binding=exact-loopback-only
else
  mark_fail cpa-port-binding
fi
echo "==nginx-merged-contract=="
NGINX_DUMP=$(mktemp)
if nginx -T >"$NGINX_DUMP" 2>&1; then
  echo merged-config=OK
  # The 2026-09-30 443-fallback lane (00-cpa-443-http.conf + subscribe.conf)
  # mirrors the canonical 8443 location/proxy shapes with private 443 names.
  # Its log_format marker is the presence signal: every mirrored-shape count
  # is expected once for the canonical lane plus once per fallback lane.
  lane_count=$(grep -Ec 'log_format[[:space:]]+cpa_safe443' "$NGINX_DUMP")
  expected_routes=$((1 + lane_count))
  echo "gateway-443-fallback-lane=$lane_count"
  listen_count=$(grep -Ec '^[[:space:]]*listen[[:space:]]+8443[[:space:]]+ssl;' "$NGINX_DUMP")
  ipv6_listen_count=$(grep -Ec '^[[:space:]]*listen[[:space:]]+\[::\]:8443[[:space:]]+ssl;' "$NGINX_DUMP")
  route_count=$(grep -Ec 'location[[:space:]]+~[[:space:]]+\^/[0-9a-f]{16}/v1/\(\.\*\)\$' "$NGINX_DUMP")
  proxy_count=$(grep -Ec 'proxy_pass[[:space:]]+http://127\.0\.0\.1:8318/v1/\$1\$is_args\$args;' "$NGINX_DUMP")
  auth_proxy_count=$(grep -Ec 'proxy_pass[[:space:]]+http://127\.0\.0\.1:8317/v1/models\$is_args\$args;' "$NGINX_DUMP")
  fallback_count=$(grep -Ec 'location[[:space:]]*/[[:space:]]*\{|return[[:space:]]+404;' "$NGINX_DUMP")
  if [ "$listen_count" -eq 1 ]; then echo public-listen-count=1; else mark_fail public-listen-count; fi
  if [ "$ipv6_listen_count" -eq 0 ]; then echo public-ipv6-listen=ABSENT; else mark_fail public-ipv6-listen; fi
  if [ "$route_count" -eq "$expected_routes" ]; then echo random-route-count="$route_count"; else mark_fail random-route-count; fi
  if [ "$proxy_count" -eq "$expected_routes" ]; then echo shared-admission-proxy-count="$proxy_count"; else mark_fail shared-admission-proxy-count; fi
  if [ "$fallback_count" -ge 2 ]; then echo fallback-404=present; else mark_fail fallback-404; fi
  retry_after_map_count=$(grep -Ec 'map[[:space:]]+\$upstream_http_retry_after[[:space:]]+\$cpa_retry_after_class[[:space:]]+\{' "$NGINX_DUMP")
  if [ "$retry_after_map_count" -eq 1 ]; then echo retry-after-map-count=1; else mark_fail retry-after-map-count; fi
  throttle_map_count=$(grep -Ec 'map[[:space:]]+"\$limit_req_status:\$limit_conn_status"[[:space:]]+\$cpa_throttle_retry_after' "$NGINX_DUMP")
  if [ "$throttle_map_count" -eq "$expected_routes" ]; then echo throttle-retry-after-map-count="$throttle_map_count"; else mark_fail throttle-retry-after-map-count; fi
  if [ "$proxy_count" -eq "$expected_routes" ] && [ "$auth_proxy_count" -eq "$expected_routes" ]; then
    echo "shared-admission-proxy=$proxy_count auth-proxy=$auth_proxy_count"
  else
    mark_fail shared-account-admission-proxy-contract
  fi
  if grep -Eq 'limit_conn_zone[[:space:]].*cpa_total|limit_conn[[:space:]]+cpa_total[[:space:]]+[0-9]+' "$NGINX_DUMP"; then
    mark_fail unexpected-global-account-concurrency
  else
    echo global-account-concurrency=ABSENT
  fi
else
  mark_fail merged-config
fi
rm -f "$NGINX_DUMP"
echo "==public-route-contract=="
SERVER_NAME=$(awk '/^[[:space:]]*server_name[[:space:]]/{gsub(";", "", $2); print $2; exit}' /etc/nginx/conf.d/cpa-gateway.conf)
PREFIX=$(grep -oE '/[0-9a-f]{16}/v1/' /etc/nginx/conf.d/cpa-gateway.conf | head -n 1 | cut -d/ -f2)
if [ -n "$SERVER_NAME" ] && [ -n "$PREFIX" ]; then
  PUBLIC_BASE="https://$SERVER_NAME:8443"
  valid_status=$(curl --noproxy '*' -sS --connect-timeout 5 --max-time 10 --resolve "$SERVER_NAME:8443:127.0.0.1" -o /dev/null -w '%{http_code}' "$PUBLIC_BASE/$PREFIX/v1/models" 2>/dev/null || echo 000)
  bare_status=$(curl --noproxy '*' -sS --connect-timeout 5 --max-time 10 --resolve "$SERVER_NAME:8443:127.0.0.1" -o /dev/null -w '%{http_code}' "$PUBLIC_BASE/v1/models" 2>/dev/null || echo 000)
  WRONG_PREFIX=0000000000000000
  if [ "$WRONG_PREFIX" = "$PREFIX" ]; then WRONG_PREFIX=ffffffffffffffff; fi
  wrong_status=$(curl --noproxy '*' -sS --connect-timeout 5 --max-time 10 --resolve "$SERVER_NAME:8443:127.0.0.1" -o /dev/null -w '%{http_code}' "$PUBLIC_BASE/$WRONG_PREFIX/v1/models" 2>/dev/null || echo 000)
  echo "valid_path_unauth=$valid_status"
  echo "bare_path=$bare_status"
  echo "wrong_path=$wrong_status"
  if [ "$valid_status" = "401" ]; then :; else mark_fail valid-path; fi
  if [ "$bare_status" = "404" ]; then :; else mark_fail bare-path; fi
  if [ "$wrong_status" = "404" ]; then :; else mark_fail wrong-path; fi
else
  mark_fail public-route-inputs
fi
echo "==cpa-policy=="
# The structure grep prints key lines only; the sed strips any value that a
# foreign re-serialization may have folded onto the same line as an api-keys
# key, so a credential cannot reach doctor output ahead of the fail-closed
# catalog check below.
grep -nE "^(server:|  host:|  port:|management:|  allow-remote:|access:|  api-keys:|routing:|  strategy:|  session-affinity:|  session-affinity-ttl:|  session-affinity-subagents:|  force-model-prefix:|  retry:|    request-retry:|    max-retry-credentials:|  cooldown:|    disable-cooling:|    save-cooldown-status:|    transient-error-cooldown-seconds:|upstream:|  codex:|    stream-bootstrap-buffering:|    stream-bootstrap-timeout:|oauth:|  auth-dir:|  excluded-models:|observability:|  logs:|  usage:|api-keys:|  openai-compatibility:|  claude:|claude-header-defaults:|  stabilize-device-profile:|config-version:)" "$DIR/config.yaml" | sed -E 's/^([0-9]+:[[:space:]]*api-keys:).*/\1 <redacted>/' || true
# Remind operator of any HTTP (cleartext) provider slots from the deployed
# route manifest. The slot-3 http://35.213.82.91:8003 is a user-authorised
# exception; no mark_fail, but the reminder prevents relying on memory alone.
python3 - "$DIR/cpa_provider_routes.json" <<'PY'
import json, sys
from pathlib import Path
try:
    manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    http_slots = [
        f"slot={p['slot']} host={p['host']} port={p.get('port','')}"
        for p in manifest.get("providers", [])
        if isinstance(p, dict) and p.get("scheme") == "http"
    ]
    if http_slots:
        print("insecure_http_providers=" + "; ".join(http_slots))
    else:
        print("insecure_http_providers=none")
except Exception as exc:
    print("insecure_http_providers=UNAVAILABLE exc=" + type(exc).__name__)
PY
echo "==models-configured=="
grep -nE "^[[:space:]]+(name|prefix|alias|input-modalities):" "$DIR/config.yaml" || true
if python3 - "$DIR/config.yaml" "$DIR/cpa_provider_routes.json" <<'PY'
import json
import sys
import yaml

try:
    config = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
    manifest = json.load(open(sys.argv[2], encoding="utf-8"))
    expected = {
        model["alias"]: model.get("input-modalities")
        for provider in manifest.get("providers", [])
        for model in provider.get("models", [])
    }
    providers = config.get("api-keys", {}).get("openai-compatibility", [])
    configured = {
        model["alias"]: model.get("input-modalities")
        for provider in providers
        for model in provider.get("models", [])
    }
    if configured != expected:
        raise SystemExit(1)
except Exception:
    raise SystemExit(1)
PY
then
  echo "model-input-modalities=OK"
else
  mark_fail model-input-modalities
fi
echo "==client-model-catalog=="
KEY=$(python3 - "$DIR/config.yaml" <<'PY'
import sys
import yaml

config = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
access = config.get("access") if isinstance(config, dict) else None
keys = access.get("api-keys") if isinstance(access, dict) else None
if not isinstance(keys, list) or not keys or not isinstance(keys[0], str) or not keys[0]:
    raise SystemExit(1)
print(keys[0])
PY
)
MODEL_CATALOG=$(curl --noproxy '*' -fsS --max-time 20 \
  -H "Authorization: Bearer $KEY" http://127.0.0.1:8317/v1/models || true)
if [ -n "$MODEL_CATALOG" ]; then
  # Fail closed on any model ID the checked-in route manifest does not declare.
  # The doctor previously only printed MODEL_IDS and left the manifest
  # comparison to the reader, so a catalog that grew an unregistered (or
  # resurrected retired) alias still exited zero. A legitimate credential
  # cooldown only removes IDs, so an unknown-ID check cannot false-positive.
  if printf '%s' "$MODEL_CATALOG" | python3 -c '
import json, sys
from pathlib import Path
try:
    manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    catalog = json.load(sys.stdin)
except (OSError, ValueError):
    raise SystemExit(1)
allowed = {
    model["alias"]
    for provider in manifest.get("providers", [])
    if isinstance(provider, dict)
    for model in provider.get("models", [])
    if isinstance(model, dict) and isinstance(model.get("alias"), str)
} | {
    model["alias"]
    for route in manifest.get("oauth_routes", [])
    if isinstance(route, dict)
    for model in route.get("models", [])
    if isinstance(model, dict) and isinstance(model.get("alias"), str)
} | {
    model["alias"]
    for route in manifest.get("codex_api_key_routes", [])
    if isinstance(route, dict)
    for model in route.get("models", [])
    if isinstance(model, dict) and isinstance(model.get("alias"), str)
} | {
    model["alias"]
    for route in manifest.get("claude_api_key_routes", [])
    if isinstance(route, dict)
    for model in route["models"]
    if isinstance(model, dict) and isinstance(model.get("alias"), str)
} | {
    # Plugin credentials (e.g. the workbuddy plugin) advertise the models
    # pinned in their plugin config; the manifest carries that list so the
    # catalog contract can allow their contribution.
    model_name
    for route in manifest.get("plugin_routes", [])
    if isinstance(route, dict)
    for model_name in route.get("models", [])
    if isinstance(model_name, str)
}
if not allowed:
    raise SystemExit(1)
if not isinstance(catalog, dict) or not isinstance(catalog.get("data"), list):
    raise SystemExit(1)
ids = sorted({item["id"] for item in catalog["data"] if isinstance(item, dict) and isinstance(item.get("id"), str)})
print("MODEL_IDS=" + ",".join(ids))
unknown = sorted(set(ids) - allowed)
print("MODEL_IDS_UNKNOWN=" + (",".join(unknown) if unknown else "none"))
claude_aliases = {
    model["alias"].lower()
    for route in manifest.get("oauth_routes", [])
    if isinstance(route, dict) and route.get("name") == "claude-oauth"
    for model in route.get("models", [])
    if isinstance(model, dict) and isinstance(model.get("alias"), str)
}
if (Path(sys.argv[1]).parent / "claude-oauth-quarantine.json").exists():
    leaked = sorted(set(ids) & claude_aliases)
    print("CLAUDE_OAUTH_CATALOG=" + ("leak" if leaked else "blocked"))
    if leaked:
        raise SystemExit(1)
raise SystemExit(1 if unknown else 0)
' "$DIR/cpa_provider_routes.json"; then
    :
  else
    mark_fail client-model-catalog-contract
  fi
else
  mark_fail client-model-catalog
fi
unset KEY MODEL_CATALOG
echo "==files=="
stat -c "%a %U %G %s %n" "$DIR/config.yaml" "$DIR/compose.yml" "$DIR/auto-update.sh" "$DIR/cpa-health.py" "$DIR/cpa_policy.py" "$DIR/cpa_provider_routes.json" "$DIR/cpa-admission.py" "$DIR/cpa-admission.json" /etc/systemd/system/cpa-admission.service /etc/nginx/conf.d/cpa-gateway.conf
sha256sum "$DIR/config.yaml" "$DIR/compose.yml" "$DIR/auto-update.sh" "$DIR/cpa-health.py" "$DIR/cpa_policy.py" "$DIR/cpa_provider_routes.json" "$DIR/cpa-admission.py" "$DIR/cpa-admission.json" /etc/systemd/system/cpa-admission.service /etc/nginx/conf.d/cpa-gateway.conf
echo "==projection-drift=="
# The pair list is injected from the repo at invocation time (same bytes the
# -Apply projector writes), so any mismatch means the repo moved ahead of or
# behind the last -Apply projection and one of the two must be reconciled.
for pair in __CPA_PROJECTION_HASH_PAIRS__; do
  drift_path="${pair%%=*}"
  drift_want="${pair##*=}"
  drift_name=$(basename "$drift_path")
  if [ ! -f "$drift_path" ]; then
    echo "drift=$drift_name LIVE_MISSING want=$drift_want"
    mark_fail "projection-drift-$drift_name"
    continue
  fi
  drift_got=$(sha256sum "$drift_path" | awk '{print $1}')
  if [ "$drift_got" = "$drift_want" ]; then
    echo "drift=$drift_name MATCH"
  else
    echo "drift=$drift_name MISMATCH want=$drift_want got=$drift_got"
    mark_fail "projection-drift-$drift_name"
  fi
done
echo "==timer=="
systemctl is-enabled cliproxyapi-update.timer || true
systemctl is-active cliproxyapi-update.timer || true
systemctl show cliproxyapi-update.timer -p NextElapseUSecRealtime --value || true
LAST_TRIGGER=$(systemctl show cliproxyapi-update.timer -p LastTriggerUSec --value 2>/dev/null || true)
case "$LAST_TRIGGER" in
  ''|0|no)
    echo "timer_last_trigger=UNKNOWN"
    ;;
  *)
    # Some systemd builds render this USec property as a human-readable
    # timestamp instead of an integer; accept both shapes and never let a
    # bare word reach arithmetic (set -u turns that into a fatal error).
    trigger_epoch=0
    case "$LAST_TRIGGER" in
      *[!0-9]*)
        trigger_epoch=$(date -d "$LAST_TRIGGER" +%s 2>/dev/null || echo 0)
        ;;
      *)
        trigger_epoch=$(( LAST_TRIGGER / 1000000 ))
        ;;
    esac
    if [ "$trigger_epoch" -gt 0 ]; then
      trigger_age_hours=$(( ($(date +%s) - trigger_epoch) / 3600 ))
      echo "timer_last_trigger_age_hours=$trigger_age_hours"
      if [ "$trigger_age_hours" -ge 72 ]; then
        echo "timer_last_trigger=STALE (daily timer has not fired in >=72h; check systemctl list-timers cliproxyapi-update.timer)"
      fi
    else
      echo "timer_last_trigger=UNKNOWN"
    fi
    ;;
esac
echo "==timer-result=="
updater_timeout=$(systemctl show cliproxyapi-update.service -p TimeoutStartUSec --value 2>/dev/null || true)
updater_stop_timeout=$(systemctl show cliproxyapi-update.service -p TimeoutStopUSec --value 2>/dev/null || true)
echo "updater_timeout=${updater_timeout:-UNKNOWN}"
echo "updater_stop_timeout=${updater_stop_timeout:-UNKNOWN}"
# Threshold, not merely bounded: the updater budgets a bounded 600s pull and
# 180s compose step, and its rollback needs a real stop window. The unit's 90s
# default (a lost drop-in) passes a bounded-only check while guaranteeing every
# real update is killed mid-pull.
systemd_timeout_seconds() {
  case "$1" in
    ''|infinity|0) echo 0 ;;
    *min) echo $(( ${1%min} * 60 )) ;;
    *ms) echo $(( ${1%ms} / 1000 )) ;;
    *us) echo $(( ${1%us} / 1000000 )) ;;
    *s) echo ${1%s} ;;
    *) echo 0 ;;
  esac
}
if [ "$(systemd_timeout_seconds "$updater_timeout")" -lt 1200 ] ||
  [ "$(systemd_timeout_seconds "$updater_stop_timeout")" -lt 240 ]; then
  mark_fail updater-timeout
fi
systemctl show cliproxyapi-update.service -p Result --value
systemctl show cliproxyapi-update.service -p ExecMainStatus --value
systemctl show cliproxyapi-update.service -p ExecMainExitTimestamp --value
grep -E '(BACKUP_HEALTH|CANDIDATE|PRUNE|OK:|UNVERIFIED|DEFER|WAIT:|ROLLBACK|REFRESH_SIGNALS)' "$DIR/auto-update.log" 2>/dev/null | tail -n 6 || true
echo "==maintenance-heartbeats=="
STATUS_DIR=/var/lib/vps-ssh-launcher/maintenance-status
heartbeat_value() {
  awk -F= -v wanted="$1" '$1 == wanted {print substr($0, index($0, "=") + 1); exit}' "$2" 2>/dev/null || true
}
heartbeat_limit_hours() {
  case "$1" in
    cpa-update) printf '72\n' ;;
    monthly-maintenance) printf '1080\n' ;;
    kernel-xray|kernel-sing-box|v2ray-agent-update) printf '336\n' ;;
    renewtls) printf '72\n' ;;
    *) printf '0\n' ;;
  esac
}
for heartbeat in cpa-update monthly-maintenance kernel-xray kernel-sing-box v2ray-agent-update renewtls; do
  heartbeat_required=0
  case "$heartbeat" in
    cpa-update)
      if systemctl cat cliproxyapi-update.timer >/dev/null 2>&1; then heartbeat_required=1; fi
      ;;
    monthly-maintenance)
      if [ -x /usr/local/sbin/monthly-maintenance.sh ]; then heartbeat_required=1; fi
      ;;
    kernel-xray)
      if [ -x /etc/v2ray-agent/auto_update_xray.sh ]; then heartbeat_required=1; fi
      ;;
    kernel-sing-box)
      if [ -x /etc/v2ray-agent/auto_update_singbox.sh ]; then heartbeat_required=1; fi
      ;;
    v2ray-agent-update)
      if [ -e /etc/cron.d/vps-launcher-v2ray-agent-update ]; then heartbeat_required=1; fi
      ;;
    renewtls)
      if [ -e /etc/cron.d/vps-launcher-v2ray-agent-renewtls ]; then heartbeat_required=1; fi
      ;;
  esac
  heartbeat_file="$STATUS_DIR/$heartbeat.status"
  if [ "$heartbeat_required" -eq 0 ]; then
    echo "heartbeat=$heartbeat status=NOT_REQUIRED required=false"
    continue
  fi
  if [ ! -f "$heartbeat_file" ]; then
    echo "heartbeat=$heartbeat status=MISSING required=true"
    mark_fail "heartbeat-$heartbeat-missing"
    continue
  fi
  heartbeat_result=$(heartbeat_value result "$heartbeat_file")
  heartbeat_code=$(heartbeat_value exit_code "$heartbeat_file")
  heartbeat_finished=$(heartbeat_value finished_at "$heartbeat_file")
  heartbeat_started=$(heartbeat_value started_at "$heartbeat_file")
  heartbeat_stamp="$heartbeat_finished"
  [ -n "$heartbeat_stamp" ] || heartbeat_stamp="$heartbeat_started"
  heartbeat_epoch=$(date -d "$heartbeat_stamp" +%s 2>/dev/null || echo 0)
  now_epoch=$(date +%s)
  heartbeat_invalid=0
  case "$heartbeat_result" in
    success|failed|unverified|busy|deferred|running) ;;
    *) heartbeat_invalid=1 ;;
  esac
  case "$heartbeat_code" in
    ''|*[!0-9]*) heartbeat_invalid=1 ;;
  esac
  case "$heartbeat_result:$heartbeat_code" in
    success:0|running:0|busy:75|unverified:10|deferred:76|deferred:77) ;;
    failed:0|failed:) heartbeat_invalid=1 ;;
    failed:*) ;;
    *) heartbeat_invalid=1 ;;
  esac
  if [ "$heartbeat_invalid" -eq 1 ] ||
     [ "$heartbeat_epoch" -le 0 ] || [ "$heartbeat_epoch" -gt "$now_epoch" ]; then
    echo "heartbeat=$heartbeat status=INVALID required=$heartbeat_required"
    mark_fail "heartbeat-$heartbeat-invalid"
    continue
  fi
  heartbeat_age_hours=$(( (now_epoch - heartbeat_epoch) / 3600 ))
  printf 'heartbeat=%s result=%s exit_code=%s age_hours=%s\n' \
    "$heartbeat" "${heartbeat_result:-UNKNOWN}" "${heartbeat_code:-UNKNOWN}" "$heartbeat_age_hours"
  heartbeat_limit=$(heartbeat_limit_hours "$heartbeat")
  if [ "$heartbeat_result" = failed ] ||
     [ "$heartbeat_result" = unverified ] ||
     [ "$heartbeat_result" = deferred ]; then
    mark_fail "heartbeat-$heartbeat"
  elif [ "$heartbeat_limit" -gt 0 ] && [ "$heartbeat_age_hours" -ge "$heartbeat_limit" ]; then
    echo "heartbeat=$heartbeat status=STALE limit_hours=$heartbeat_limit"
    mark_fail "heartbeat-$heartbeat-stale"
  fi
done
if [ -f "$STATUS_DIR/cpa-update.pending" ]; then
  pending_version=$(awk -F= '$1 == "version" {print $2; exit}' "$STATUS_DIR/cpa-update.pending")
  pending_digest=$(awk -F= '$1 == "digest" {print $2; exit}' "$STATUS_DIR/cpa-update.pending")
  echo "cpa_update_acceptance=UNVERIFIED version=${pending_version:-UNKNOWN} digest=${pending_digest:-UNKNOWN}"
  mark_fail "cpa-update-pending-verification"
fi
echo "==host-hygiene=="
# /run is tmpfs, so the marker's age is how long this boot has been pending a
# reboot. The monthly job deliberately never reboots; without a readout here
# the "patched but not running the patched kernel/libc" state stays invisible.
# It is an operator action item, not a contract failure, so it never marks fail.
if [ -f /run/reboot-required ]; then
  reboot_age_days=$(( ( $(date +%s) - $(stat -c %Y /run/reboot-required) ) / 86400 ))
  echo "reboot_required=present age_days=$reboot_age_days"
  sed 's/^/reboot_required_pkg=/' /run/reboot-required.pkgs 2>/dev/null || true
  if [ "$reboot_age_days" -ge 30 ]; then
    echo "reboot_required_advisory=STALE_REBOOT_PENDING"
  fi
else
  echo "reboot_required=absent"
fi
# Maintenance transactions create one timestamped backup directory each. The
# successful CPA/guardrail/kernel/adapter lanes prune their own backup family
# to a bounded keep-8 window; the doctor still reports counts so failed or
# legacy transactions remain visible instead of silently filling / or /var.
for backup_root in /root /var/backups; do
  backup_count=$(find "$backup_root" -mindepth 1 -maxdepth 1 -type d \( -name 'cpa-guardrails-*' -o -name 'cpa-oauth-quarantine-*' -o -name 'v2ray-agent-*' -o -name 'vps-ssh-launcher-*' -o -name 'google-ipv4-routing-*' \) -printf . 2>/dev/null | wc -c)
  echo "maintenance_backups root=$backup_root count=$backup_count"
done
echo "==inventory=="
df -h / | awk 'NR == 2 {print "root_total="$2" used="$3" avail="$4" use_pct="$5}'
find "$DIR/backups" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l | awk '{print "update_backups=" $1}'
du -sk "$DIR/backups" 2>/dev/null | awk 'NR == 1 {print "update_backups_kib=" $1}'
docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | grep -c '^eceasy/cli-proxy-api:' | awk '{print "cpa_image_tags=" $1}'
echo "==cooldown-state=="
python3 - "$DIR" <<'PY'
import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

import yaml

root = Path(sys.argv[1])
auth_dir = root / "auth"
now = dt.datetime.now(dt.timezone.utc)
retry_times = []


def collect_retry_times(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "next_retry_after" and isinstance(child, str):
                try:
                    parsed = dt.datetime.fromisoformat(child.replace("Z", "+00:00"))
                except ValueError:
                    continue
                retry_times.append(
                    parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
                )
            collect_retry_times(child)
    elif isinstance(value, list):
        for child in value:
            collect_retry_times(child)


for state_file in auth_dir.glob("*.cds"):
    try:
        collect_retry_times(json.loads(state_file.read_text(encoding="utf-8")))
    except (OSError, ValueError, json.JSONDecodeError):
        pass

if not retry_times:
    cooldown_state = "none"
    next_retry_after = "none"
elif any(retry_at > now for retry_at in retry_times):
    cooldown_state = "active"
    next_retry_after = max(retry_times).astimezone(dt.timezone.utc).isoformat()
else:
    cooldown_state = "expired"
    next_retry_after = max(retry_times).astimezone(dt.timezone.utc).isoformat()

catalog_read = "unavailable"
catalog_oauth_present = []
catalog_oauth_missing = []
manifest_oauth_aliases = []
try:
    manifest = json.loads(
        (root / "cpa_provider_routes.json").read_text(encoding="utf-8")
    )
    manifest_oauth_aliases = sorted(
        {
            model["alias"]
            for route in manifest.get("oauth_routes", [])
            if isinstance(route, dict)
            # Luna state tracks the ChatGPT Plus route only; other OAuth
            # routes (antigravity-oauth since 2026-10-14) have their own
            # catalog presence and must not mask or fake Luna availability.
            and route.get("name") == "chatgpt-plus-oauth"
            for model in route.get("models", [])
            if isinstance(model, dict) and isinstance(model.get("alias"), str)
        }
    )
except (OSError, ValueError):
    manifest_oauth_aliases = []

try:
    config = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    key = config["access"]["api-keys"][0]
    request = urllib.request.Request(
        "http://127.0.0.1:8317/v1/models",
        headers={"Authorization": f"Bearer {key}"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=5) as response:
        catalog = json.load(response)
    ids = {item.get("id") for item in catalog.get("data", []) if isinstance(item, dict)}
    catalog_read = "ok"
    catalog_oauth_present = sorted(
        alias for alias in manifest_oauth_aliases if alias in ids
    )
    catalog_oauth_missing = sorted(
        alias for alias in manifest_oauth_aliases if alias not in ids
    )
except Exception:
    pass

# Luna availability is a property of the whole OAuth route, not of one bare
# name. Upstream/account entitlement churn can drop the bare `gpt-6-luna`
# while other OAuth aliases keep serving (the 2026-09-22 incident was keyed
# on the since-retired `gpt-5.6-luna`), so keying the state off a
# single name produced a self-contradictory doctor (MODEL_IDS listing the OAuth
# route while luna_state reported it unavailable). The expected alias set comes
# from the same route manifest the projector and semantic policy use.
if catalog_read != "ok":
    catalog_gpt6_luna = "unknown"
elif "gpt-6-luna" in catalog_oauth_present:
    catalog_gpt6_luna = "present"
else:
    catalog_gpt6_luna = "absent"

if not manifest_oauth_aliases:
    luna_state = "unknown_route_manifest"
elif catalog_read != "ok":
    luna_state = "unknown_catalog_unreadable"
elif not catalog_oauth_missing:
    luna_state = "available"
elif catalog_oauth_present:
    luna_state = "available_partial"
elif cooldown_state == "active":
    luna_state = "active_cooldown"
elif cooldown_state == "expired":
    luna_state = "stale_cooldown_suspected"
else:
    luna_state = "unavailable_unclassified"

print(f"cds_files={len(list(auth_dir.glob('*.cds')))}")
print(f"cooldown_state={cooldown_state}")
print(f"cooldown_next_retry_after={next_retry_after}")
print(f"catalog_gpt6_luna={catalog_gpt6_luna}")
print(
    "catalog_oauth_aliases="
    + (",".join(catalog_oauth_present) if catalog_oauth_present else "none")
)
print(
    "catalog_oauth_missing="
    + (",".join(catalog_oauth_missing) if catalog_oauth_missing else "none")
)
print(f"luna_state={luna_state}")
print("cooldown_state_coverage=local_cooldown_and_catalog_only; not_provider_acceptance")
PY
echo "==auth-modes=="
find "$DIR/auth" -maxdepth 1 -type f -printf "%m\n" | sort | uniq -c
echo "==error-dump-permissions=="
if python3 - "$DIR/auth/logs" <<'PY'
import stat
import sys
from pathlib import Path

logs = Path(sys.argv[1])
if not logs.is_dir() or stat.S_IMODE(logs.stat().st_mode) != 0o700:
    raise SystemExit(1)
for path in logs.glob("error-*.log"):
    if path.is_file() and stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise SystemExit(1)
PY
then
  echo error-dump-permissions=OK
else
  mark_fail error-dump-permissions
fi
echo "==gateway-statuses-current-log-24h=="
# A missing log (logrotate race) must surface as a failed gate, not as a
# silently empty observation section that still reads DOCTOR_CONTRACT_OK.
if python3 - <<'PY'
import collections, datetime, json, re, statistics, time
from pathlib import Path
counts = collections.Counter()
upstream = collections.Counter()
admission_429_shape = collections.Counter()
status_upstream = collections.Counter()
limit_markers = collections.Counter()
retry_after_markers = collections.Counter()
route_classes = collections.Counter()
last_1h = collections.Counter()
five_xx_local_vs_upstream = collections.Counter()
five_xx_by_client = collections.Counter()
status_by_client_class = collections.Counter()
hourly_statuses = collections.defaultdict(collections.Counter)
client_503_times = collections.defaultdict(list)
client_503_times_by_plane = collections.defaultdict(list)
client_503_maintenance_shape = collections.Counter()
abort_request_times = []
cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=24)
cutoff_1h = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
unparsed = 0
# Bound the worst-case scan latency: only the most recent 64 MiB of the
# access log is analyzed (mirroring the 20 MiB error-dump cap); if the log
# grew past the cap within the 24h window, counts are a lower bound.
scan_cap_bytes = 64 * 1024 * 1024
log_handle = Path('/var/log/nginx/cpa_gateway.access.log').open()
log_total_bytes = log_handle.seek(0, 2)
if log_total_bytes > scan_cap_bytes:
    log_handle.seek(log_total_bytes - scan_cap_bytes)
    log_handle.readline()  # drop the partial line at the truncation boundary
    print('log_scan_truncated total_bytes=%d scanned_bytes=%d' % (log_total_bytes, scan_cap_bytes))
else:
    log_handle.seek(0)
for line in log_handle:
    match = re.search(
        r'^(?P<client>\S+) method=\S+(?: route=(?P<route>\S+))? '
        r'status=(?P<status>\d{3})(?: request_time=(?P<request_time>[0-9.]+))? .*'
        r'upstream_status=(?P<upstream>[^ ]+)'
        r'(?: upstream_time=(?P<upstream_time>[^ ]+) '
        r'bytes=(?P<body_bytes>\d+))? .*limit_req=(?P<limit_req>[^ ]+) '
        r'limit_conn=(?P<limit_conn>[^ ]+)'
        r'(?: retry_after=(?P<retry_after>[^ ]+))? .*time=\[(?P<time>[^]]+)\]',
        line,
    )
    if not match:
        unparsed += 1
        continue
    try:
        stamp = datetime.datetime.strptime(match.group('time'), '%d/%b/%Y:%H:%M:%S %z')
    except ValueError:
        unparsed += 1
        continue
    if stamp < cutoff:
        continue
    status = match.group('status')
    route_classes[match.group('route') or 'legacy_unknown'] += 1
    counts[status] += 1
    # Production vs. test-residue split, and the hour it happened in. The bare
    # 24h total once read as a clean gateway while 20 of its 429s were loopback
    # load-test leftovers and every real-client 429 sat inside a single
    # two-hour window; classifying the client plane and the hour makes that
    # distinction readable in one pass. Client addresses collapse to a class,
    # never an address.
    client_class = (
        'loopback' if match.group('client').startswith('127.') else 'external'
    )
    status_by_client_class[f'{client_class}/{status}'] += 1
    hour_bucket = hourly_statuses[stamp.strftime('%Y-%m-%dT%H')]
    hour_bucket['total'] += 1
    hour_bucket[status] += 1
    if 'REJECTED' in (match.group('limit_req'), match.group('limit_conn')):
        hour_bucket['limit_rejected'] += 1
    upstream[match.group('upstream')] += 1
    status_upstream[f'{status}/{match.group("upstream")}'] += 1
    limit_markers[f'{match.group("limit_req")}/{match.group("limit_conn")}'] += 1
    retry_after_markers[match.group('retry_after') or 'legacy_unknown'] += 1
    if status == '429':
        # Nginx's upstream_status is the admission service for protected
        # routes, not proof that the provider itself returned 429. The current
        # admission JSON is 203 bytes and returns in milliseconds; classify
        # that shape separately while keeping the journal as the authoritative
        # source for the inner rejection reason.
        upstream_time = match.group('upstream_time')
        body_bytes = match.group('body_bytes')
        try:
            fast_upstream = upstream_time is not None and float(upstream_time) < 0.5
        except ValueError:
            fast_upstream = False
        if (
            match.group('upstream') == '429'
            and match.group('limit_req') == 'PASSED'
            and match.group('limit_conn') == 'PASSED'
            and body_bytes == '203'
            and fast_upstream
        ):
            admission_429_shape['likely_admission_fast_203'] += 1
        elif match.group('upstream') == '429':
            admission_429_shape['numeric_upstream_429_other'] += 1
        elif match.group('upstream') in ('-', ''):
            admission_429_shape['local_nginx_429'] += 1
        else:
            admission_429_shape['other'] += 1
    if stamp >= cutoff_1h:
        last_1h[status] += 1
    if status == '499' and match.group('request_time'):
        abort_request_times.append(float(match.group('request_time')))
    if status in ('500', '502', '503'):
        # upstream_status is the primary plane discriminator. A numeric value
        # means the upstream hop returned the status; '-' means the response was
        # generated locally. request_time only refines the plane after that
        # distinction. IPs stay masked to /16 and retry patterns are aggregate
        # gap stats only, never an address.
        request_time = float(match.group('request_time')) if match.group('request_time') else -1.0
        upstream_known = match.group('upstream') not in ('-', '')
        # upstream_time is the admission hop's own service time. request_time is
        # the client-visible total and can exceed it (one 2026-10-23 maintenance
        # refusal took 0.646s wall time for a 0.002s admission reply), so the
        # shape must key on upstream_time.
        upstream_time = match.group('upstream_time')
        try:
            fast_admission = upstream_time is not None and float(upstream_time) < 0.5
        except ValueError:
            fast_admission = False
        # Admission refuses every non-healthz request while the updater holds
        # the traffic lock, answering with the fixed compact body
        # {"error":{"type":"maintenance_in_progress"}} (44 bytes, no upstream
        # call) in ~2ms. That shape is indistinguishable from a CPA credential
        # cooldown fast-fail unless it is named: a 1s client retry across a
        # planned upgrade then reads as a "retry storm". Keep it in its own
        # bucket and out of the retry-cadence stats.
        maintenance_shape = (
            status == '503'
            and upstream_known
            and match.group('body_bytes') == '44'
            and fast_admission
            and match.group('limit_req') == 'PASSED'
            and match.group('limit_conn') == 'PASSED'
        )
        if maintenance_shape:
            bucket = 'admission_maintenance_44B_upstream_lt_0_5s'
        elif upstream_known:
            bucket = ('fast_upstream_lt_0_5s' if 0 <= request_time < 0.5
                      else 'mid_upstream_0_5_to_3s' if request_time < 3
                      else 'slow_upstream_ge_3s')
        else:
            bucket = ('fast_local_lt_0_5s' if 0 <= request_time < 0.5
                      else 'mid_local_0_5_to_3s' if request_time < 3
                      else 'slow_local_ge_3s')
        five_xx_local_vs_upstream[f'{status}/{bucket}'] += 1
        octets = match.group('client').split('.')
        client = '.'.join(octets[:2]) + '.x.x' if len(octets) == 4 else 'masked'
        five_xx_by_client[f'{client}/{status}'] += 1
        if status == '503':
            if maintenance_shape:
                client_503_maintenance_shape[client] += 1
            else:
                client_503_times[client].append(stamp.timestamp())
                plane = 'upstream' if upstream_known else 'local'
                client_503_times_by_plane[f'{client}/{plane}'].append(stamp.timestamp())
abort_request_times.sort()
abort_summary = {'count': len(abort_request_times)}
if abort_request_times:
    abort_summary.update({
        'min_s': abort_request_times[0],
        'p50_s': abort_request_times[len(abort_request_times) // 2],
        'max_s': abort_request_times[-1],
    })
retry_pattern = {}
for client, times in client_503_times.items():
    if len(times) < 5:
        continue
    times.sort()
    gaps = [later - earlier for earlier, later in zip(times, times[1:]) if 0 <= later - earlier < 300]
    if gaps:
        retry_pattern[client] = {
            'n503': len(times),
            'median_gap_s': round(statistics.median(gaps), 1),
        }
retry_pattern_by_plane = {}
for client, times in client_503_times_by_plane.items():
    if len(times) < 5:
        continue
    times.sort()
    gaps = [later - earlier for earlier, later in zip(times, times[1:]) if 0 <= later - earlier < 300]
    if gaps:
        retry_pattern_by_plane[client] = {
            'n503': len(times),
            'median_gap_s': round(statistics.median(gaps), 1),
        }
print(json.dumps({'statuses': dict(counts), 'upstream_statuses': dict(upstream),
                  'status_upstream': dict(status_upstream),
                  'admission_429_shape': dict(admission_429_shape),
                  'limit_markers': dict(limit_markers),
                  'retry_after_classes': dict(retry_after_markers),
                  'route_classes': dict(route_classes),
                  'last_1h_statuses': dict(last_1h),
                  'statuses_by_client_class': dict(status_by_client_class),
                  'statuses_by_hour': {hour: dict(values)
                                       for hour, values in sorted(hourly_statuses.items())},
                  'five_xx_local_vs_upstream': dict(five_xx_local_vs_upstream),
                  'five_xx_by_client_masked': dict(five_xx_by_client),
                  'client_503_maintenance_shape_total': sum(client_503_maintenance_shape.values()),
                  'client_503_maintenance_shape_by_client_masked': dict(client_503_maintenance_shape),
                  'client_503_retry_pattern': retry_pattern,
                  'client_503_retry_pattern_by_plane': retry_pattern_by_plane,
                  'client_abort_request_time': abort_summary,
                  'unparsed_legacy_lines': unparsed,
                  'coverage': 'current access log only; rotated logs excluded; '
                              'five_xx_local_vs_upstream separates local cooldown fast-fails '
                              '(<0.5s) from upstream passthrough (>=3s); client IPs masked '
                              'to /16; '
                              'client_abort_request_time covers 499 lines carrying request_time '
                              'admission_429_shape is a bounded log-shape heuristic; '
                              'confirm inner reason in cpa-admission journal '
                              '(a tight cluster, e.g. ~45.0s, proves a fixed client-side total timeout); '
                              'statuses_by_client_class splits loopback (probe/load-test residue) '
                              'from external clients for every status, and statuses_by_hour carries '
                              'per-hour total/status/limit_rejected counts so a burst can be located '
                              'in time; neither field emits an address; '
                              '503/admission_maintenance_44B_upstream_lt_0_5s is the 44-byte, '
                              'upstream_time<0.5s, limiter-passed shape of admission '
                              'maintenance_in_progress and is '
                              'excluded from client_503_retry_pattern so a planned upgrade window '
                              'is not read as a client retry storm; confirm it in the admission '
                              'journal request_guard_refused aggregates'}))
error_section_markers = {
    '=== api error response ===',
    '=== api response ===',
    '=== response ===',
}


def dump_error_evidence(raw):
    # Response-side sections only: markers and timestamps taken from
    # REQUEST INFO / error sections never from plaintext request bodies
    # (2026-09-21 prompt-text contamination produced phantom markers and
    # quoted timestamps from earlier doctor output).
    timestamp = None
    error_lines = []
    keep = False
    for line in raw.splitlines():
        marker = line.strip().lower()
        if marker.startswith('=== ') and marker.endswith(' ==='):
            keep = marker in error_section_markers
            continue
        if keep:
            error_lines.append(line)
        elif timestamp is None and line.startswith('Timestamp:'):
            timestamp = line.split(':', 1)[1].strip()
    return timestamp, '\n'.join(error_lines)


events = []
overload_markers = 0
auth_unavailable_files = 0
auth_unavailable_lanes = collections.Counter()
candidates = []
for path in Path('/opt/cliproxyapi/auth/logs').glob('error-*.log'):
    try:
        st = path.stat()
    except OSError:
        continue
    candidates.append((path, st.st_mtime, st.st_size))
candidates.sort(key=lambda item: item[1], reverse=True)
scanned = 0
now_ts = time.time()
for path, mtime, size in candidates:
    # The updater prunes these after 48 hours and CPA itself keeps only the
    # newest error-logs-max-files dumps; the doctor additionally caps the
    # read count so a backlog can never repeat the 2026-09-17 doctor timeout.
    if mtime < now_ts - 7 * 86400 or scanned >= 30:
        break
    if size > 20_000_000:
        continue
    scanned += 1
    timestamp, error_text = dump_error_evidence(path.read_text(errors='replace'))
    if 'server_is_overloaded' in error_text:
        overload_markers += error_text.count('server_is_overloaded')
        events.append({'time_as_logged': timestamp,
                       'oauth_upstream': 'chatgpt.com/backend-api/codex' in error_text})
    if 'auth_unavailable' in error_text:
        auth_unavailable_files += 1
        lane = re.search(r'providers=([a-z0-9._-]+),\s*model=([a-z0-9._-]+)', error_text)
        auth_unavailable_lanes[(lane.group(1) + '/' + lane.group(2)) if lane else 'unclassified'] += 1
print(json.dumps({'retained_overload_request_files': len(events),
                  'overload_markers': overload_markers, 'events': events,
                  'scanned_error_files': scanned,
                  'auth_unavailable_retained_sample_count': auth_unavailable_files,
                  'auth_unavailable_by_lane': dict(auth_unavailable_lanes),
                  'coverage': 'newest retained error dumps only (CPA keeps newest '
                              'error-logs-max-files; updater prunes >24h): incomplete_bounded_error_dumps, '
                              'not full 24h/7d counts; markers/timestamps from response-side sections '
                              'only; not recovery proof'}))
PY
then
  :
else
  mark_fail gateway-status-scan
fi
echo "==admission-journal-24h=="
# Nginx's status/latency shape only identifies the producing hop.  The
# admission journal is the authoritative inner reason, so expose bounded
# aggregates here instead of making operators SSH in and grep raw request
# ids.  This is observation-only and never changes the gate or replays a
# provider request.
python3 - <<'PY'
import collections
import re
import subprocess

try:
    completed = subprocess.run(
        ["journalctl", "-u", "cpa-admission", "--since", "24 hours ago", "--no-pager"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
except (OSError, subprocess.TimeoutExpired):
    print("admission-journal=UNAVAILABLE")
    raise SystemExit(0)

if completed.returncode != 0:
    print("admission-journal=UNAVAILABLE")
    raise SystemExit(0)

text = completed.stdout
reject_reasons = collections.Counter()
reject_lanes = collections.Counter()
capacity_statuses = collections.Counter()
capacity_lanes = collections.Counter()
capacity_models = collections.Counter()
upstream_429_503_details = collections.Counter()
protocol_results = collections.Counter()
protocol_results_by_model = collections.Counter()
request_guard_refusals = collections.Counter()
upstream_results = 0
for line in text.splitlines():
    # The traffic-lock refusal is logged by admission itself, so it is the
    # authoritative inner reason behind a 44-byte maintenance 503. Aggregate by
    # stage/reason only: request ids, paths and exception text stay out.
    match = re.search(
        r"request_guard_refused stage=(?P<stage>[^ ]+) reason=(?P<reason>[^ ]+)",
        line,
    )
    if match:
        request_guard_refusals[f"{match.group('stage')}/{match.group('reason')}"] += 1
        continue
    match = re.search(
        r"lane_reject lane=(?P<lane>[^ ]+) model=(?P<model>[^ ]+) "
        r"reason=(?P<reason>[^ ]+) retry_after=(?P<retry>[^ ]+)",
        line,
    )
    if match:
        reject_reasons[match.group("reason")] += 1
        reject_lanes[match.group("lane")] += 1
        continue
    match = re.search(
        r"upstream_result lane=(?P<lane>[^ ]+) model=(?P<model>[^ ]+) "
        r"status=(?P<status>[0-9]+) capacity=(?P<capacity>true|false) "
        r"retry_after=(?P<retry>[^ ]+) waited_ms=(?P<waited>[0-9]+)",
        line,
    )
    if not match:
        continue
    upstream_results += 1
    status = match.group("status")
    # Older journals did not record protocol completion. Keep those unknown;
    # HTTP 200 alone cannot prove a completed JSON/SSE operation.
    completion = re.search(r"\bsuccessful=(true|false)\b", line[match.end():])
    outcome = (
        "completed" if completion.group(1) == "true" else "not_completed"
    ) if completion else "unknown"
    protocol_results[outcome] += 1
    protocol_results_by_model["/".join((
        match.group("lane"), match.group("model"), f"HTTP{status}", outcome
    ))] += 1
    if status in {"429", "503"}:
        detail = "/".join(
            (
                match.group("lane"),
                match.group("model"),
                f"HTTP{status}",
                "capacity" if match.group("capacity") == "true" else "noncapacity",
            )
        )
        upstream_429_503_details[detail] += 1
    if match.group("capacity") == "true":
        capacity_statuses[status] += 1
        capacity_lanes[match.group("lane")] += 1
        capacity_models[match.group("model")] += 1

def compact(counter):
    return ",".join(
        f"{key}:{counter[key]}" for key in sorted(counter)
    ) or "none"

print(f"admission-journal=OK upstream_results={upstream_results}")
print(f"admission_lane_rejects_24h={sum(reject_reasons.values())}")
print(f"admission_lane_reject_reasons={compact(reject_reasons)}")
print(f"admission_lane_reject_lanes={compact(reject_lanes)}")
print(f"admission_request_guard_refusals_24h={sum(request_guard_refusals.values())}")
print(f"admission_request_guard_refusal_reasons={compact(request_guard_refusals)}")
print(f"admission_capacity_events_24h={sum(capacity_statuses.values())}")
print(f"admission_capacity_statuses={compact(capacity_statuses)}")
print(f"admission_capacity_lanes={compact(capacity_lanes)}")
print(f"admission_capacity_models={compact(capacity_models)}")
print(f"admission_upstream_429_503_details={compact(upstream_429_503_details)}")
print(f"admission_protocol_results={compact(protocol_results)}")
print(f"admission_protocol_results_by_model={compact(protocol_results_by_model)}")
print(
    "admission-journal-coverage=journalctl_since_24h; aggregate_counts_only; "
    "request_ids_and_client_text_omitted; 429_503_include_capacity_and_noncapacity; "
    "upstream_result_is_authoritative_for_capacity; "
    "request_guard_refusals_separate_maintenance_busy_from_lock_errors; "
    "older_admission_builds_log_only_the_http_request_line; "
    "protocol_completed_requires_successful_true; missing_successful_is_unknown; "
    "not_completed_is_not_necessarily_capacity"
)
PY
echo "==lane-hold=="
# Durable admission holds live in /var/lib/cpa-admission/*-risk.json, and the
# 2026-10-20 chatgpt unverified_request and workbuddy 14003 latches were both
# discovered from user-reported symptoms because no probe read these files.
# A CONFIGURED lane holding a durable risk fails the strict gate. A retired
# lane's state file (claude-oauth) is reported informationally only: its stale
# hold_reason is an inert tombstone, and deleting it would crash-loop a future
# re-attach because the admission loader has no missing-file guard. not_before
# without hold_reason is the cooldown ladder's persisted mirror, not a hold.
#
# The risk file is a write-ahead marker: admission persists
# `hold_reason=unverified_request` before every upstream request and rewrites it
# to null once the lane drains. Reading the file alone therefore reports HELD
# for ordinary in-flight traffic (observed 2026-10-22: strict doctor failed
# `lane-hold` while /healthz reported hold_reason=null). The running snapshot is
# authoritative for the current process; the file stays authoritative only when
# /healthz is unreachable (crash-latch detection must still fail closed).
python3 - "$DIR" "/var/lib/cpa-admission" <<'PY'
import json
import sys
import time
import urllib.request
from pathlib import Path

STATE_FILES = {
    "chatgpt-oauth": "chatgpt-risk.json",
    "workbuddy-oauth": "workbuddy-risk.json",
    "antigravity-oauth": "antigravity-risk.json",
    "claude-oauth": "claude-risk.json",
}

try:
    config = json.loads(
        (Path(sys.argv[1]) / "cpa-admission.json").read_text(encoding="utf-8")
    )
    configured = {lane.get("name") for lane in config.get("lanes", [])}
except (OSError, ValueError):
    print("lane-hold-config=UNREADABLE")
    raise SystemExit(1)

healthz_url = (
    sys.argv[3] if len(sys.argv) > 3 else "http://127.0.0.1:8318/healthz"
)
running = {}
try:
    with urllib.request.urlopen(healthz_url, timeout=2) as response:
        payload = json.load(response)
    lanes = payload.get("lanes")
    if isinstance(lanes, dict):
        running = lanes
except Exception:
    running = {}

state_dir = Path(sys.argv[2])
held = []
for lane, filename in STATE_FILES.items():
    try:
        state = json.loads((state_dir / filename).read_text(encoding="utf-8"))
        hold = state["hold_reason"]
        not_before = float(state["not_before"])
    except (OSError, ValueError, KeyError, TypeError):
        print(f"lane-hold-{lane}=UNREADABLE")
        if lane in configured:
            held.append(f"{lane}:state-unreadable")
        else:
            print(f"lane-hold-{lane}-note=unconfigured-state-unreadable")
        continue
    entry = running.get(lane)
    live = entry.get("state") if isinstance(entry, dict) else None
    if isinstance(live, dict):
        live_hold = live.get("hold_reason")
        try:
            inflight = int(live.get("inflight") or 0)
        except (TypeError, ValueError):
            inflight = 0
        try:
            remaining = int(live.get("cooldown_remaining") or 0)
        except (TypeError, ValueError):
            remaining = 0
        if live_hold:
            print(f"lane-hold-{lane}=HELD reason={live_hold}")
            if lane in configured:
                held.append(f"{lane}:{live_hold}")
            else:
                print(f"lane-hold-{lane}-note=unconfigured-tombstone-inert")
            continue
        if hold == "unverified_request":
            if inflight > 0:
                print(
                    f"lane-hold-{lane}=OPEN "
                    f"note=write_ahead_inflight={inflight}"
                )
            else:
                print(
                    f"lane-hold-{lane}=OPEN "
                    "note=stale_write_ahead_marker"
                )
            continue
        if hold:
            # Disk carries a durable reason the running process does not hold.
            # That is drift or an out-of-band edit; keep the gate fail-closed.
            print(f"lane-hold-{lane}=HELD reason={hold} note=disk_only")
            if lane in configured:
                held.append(f"{lane}:{hold}")
            else:
                print(f"lane-hold-{lane}-note=unconfigured-tombstone-inert")
            continue
        mirror = f" cooldown_mirror_s={remaining}" if remaining else ""
        print(f"lane-hold-{lane}=OPEN{mirror}")
        continue
    if hold:
        print(f"lane-hold-{lane}=HELD reason={hold}")
        if lane in configured:
            held.append(f"{lane}:{hold}")
        else:
            print(f"lane-hold-{lane}-note=unconfigured-tombstone-inert")
    else:
        remaining = max(0, int(not_before - time.time()))
        mirror = f" cooldown_mirror_s={remaining}" if remaining else ""
        print(f"lane-hold-{lane}=OPEN{mirror}")

if held:
    print("lane-hold=HELD " + ",".join(held))
    raise SystemExit(1)
print("lane-hold=OK")
print(
    "lane-hold-coverage=state_files_cover_all_durable_oauth_lanes; "
    "zhipu-coding-plan_has_no_durable_state; "
    "not_before_without_hold_reason_is_cooldown_mirror_not_a_hold; "
    "running_healthz_is_authoritative_for_live_hold; "
    "disk_unverified_request_is_write_ahead_while_inflight"
)
PY
LANE_HOLD_RC=$?
if [ "$LANE_HOLD_RC" -ne 0 ]; then mark_fail lane-hold; fi
echo "==workbuddy-config=="
# The 2026-10-24 panel incident proved that PUT
# /v8/management/config/plugins/configs/workbuddy replaces the whole plugin
# section (PATCH merges) and the config differ never covers plugins.*, so a
# partial panel form silently drops keys with no change-log trace. Lost model
# pins eventually surface as MODEL_IDS_UNKNOWN in ==client-model-catalog==,
# but only after a /v1/models request triggers realm discovery; the
# automation switches have no indirect signal at all. usage_config.go
# pre-initializes all four next*Auto flags to true, so a wiped section
# silently re-enables the growth-center, lifecycle and keepalive loops this
# deployment runs with off. This gate reads the deployed policy directly.
python3 - "$DIR/config.yaml" <<'PY'
import sys
import yaml
from pathlib import Path

try:
    config = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
except Exception as exc:
    print("workbuddy-config=UNREADABLE exc=" + type(exc).__name__)
    raise SystemExit(1)

plugins = config.get("plugins") if isinstance(config, dict) else None
configs = plugins.get("configs") if isinstance(plugins, dict) else None
section = configs.get("workbuddy") if isinstance(configs, dict) else None
if not isinstance(section, dict) or not section.get("enabled"):
    print("workbuddy-config=DISABLED note=gate_applies_only_when_enabled")
    raise SystemExit(0)

# Pin presence is asserted, not the pinned value: the value is enforced where
# it is consumed (admission lane model list, ==client-model-catalog==), and
# restating it here would create a second source of truth to keep in sync.
# An absent switch is drift, not "off": reconfigure treats absent keys as the
# upstream default, which is true for all four switches.
SWITCHES = (
    ("checkin_auto", True),
    ("tasks_auto", False),
    ("lifecycle_auto", False),
    ("token_keepalive", False),
)
drift = []
for key, expected in SWITCHES:
    if key not in section:
        drift.append(f"{key}:absent")
        print(f"workbuddy-{key}=ABSENT expected={str(expected).lower()}")
        continue
    value = section.get(key)
    if not isinstance(value, bool):
        drift.append(f"{key}:non-bool")
        print(f"workbuddy-{key}=NON_BOOL expected={str(expected).lower()}")
        continue
    print(f"workbuddy-{key}={str(value).lower()}")
    if value != expected:
        drift.append(f"{key}:{str(value).lower()}")

for realm in ("models_cn", "models_intl"):
    pin = section.get(realm)
    if isinstance(pin, str) and pin.strip():
        print(f"workbuddy-{realm}=PINNED")
    else:
        drift.append(f"{realm}:unpinned")
        print(f"workbuddy-{realm}=UNPINNED note=pin_loss_reopens_realm_discovery")

# Sticky new-login realm: a leftover cn makes future logins enter the CN
# realm, but it is legitimately toggled during a CN login flow, so it is
# reported for eyes-on review instead of gated.
region = section.get("login_region")
if isinstance(region, str) and region.strip():
    print(
        f"workbuddy-login_region={region.strip()} "
        "note=sticky_new_logins_only_not_gated"
    )

if drift:
    print("workbuddy-config=DRIFT " + ",".join(drift))
    raise SystemExit(1)
print("workbuddy-config=OK")
print(
    "workbuddy-config-coverage=pins_block_realm_discovery_spill; "
    "absent_switch_reconfigures_to_upstream_default_true; "
    "policy_20261024=cn_checkin_on_tasks_lifecycle_keepalive_off; "
    "pin_value_enforced_by_admission_and_catalog_gates_not_here"
)
PY
WORKBUDDY_CONFIG_RC=$?
if [ "$WORKBUDDY_CONFIG_RC" -ne 0 ]; then mark_fail workbuddy-config; fi
echo "==model-substitution=="
# CLIProxyAPI >= v7.3.8 warns "codex executor: upstream served model %q for
# requested model %q (auth_index=%s)" on silent model substitution. Since
# v8.0.3x (usage_helpers.go) one warn is emitted per substituted request (the
# old per credential/model 10min throttle is gone), so the 7d count tracks
# request volume; spread is judged on the per-pair breakdown below. Only the
# quoted model names are kept; log lines and auth_index stay out of the
# doctor output.
RUNNING_CPA_TAG=$(docker inspect --format '{{.Config.Image}}' cli-proxy-api 2>/dev/null | sed -nE 's#.*:(v[0-9]+\.[0-9]+\.[0-9]+)(@sha256:[0-9a-f]+)?$#\1#p')
if [ -n "$RUNNING_CPA_TAG" ] && [ "$(printf '%s\n' 'v7.3.8' "$RUNNING_CPA_TAG" | sort -V | head -n 1)" = 'v7.3.8' ]; then
  SUBST_TMP=/tmp/cpa-doctor-subst-7d.log
  docker logs --since 168h cli-proxy-api 2>&1 | grep 'upstream served model' > "$SUBST_TMP" || true
  SUBSTITUTIONS_7D=$(wc -l < "$SUBST_TMP")
  echo "model_substitution_warnings_7d=$SUBSTITUTIONS_7D"
  SUBST_PAIRS=$(awk -F'"' 'NF >= 4 && $2 != "" { print $2 " <== " $4 }' "$SUBST_TMP" | sort -u | wc -l)
  echo "model_substitution_distinct_pairs=$SUBST_PAIRS"
  awk -F'"' 'NF >= 4 && $2 != "" { print $2 " <== " $4 }' "$SUBST_TMP" | sort | uniq -c | sort -rn | head -n 8 | awk '{ count = $1; $1 = ""; printf "model_substitution_pair=%s%s\n", count, $0 }'
  rm -f "$SUBST_TMP"
  if [ "$SUBSTITUTIONS_7D" -gt 5 ] 2>/dev/null; then
    # Not a gate, but warrants manual review: with per-request emission a high
    # count alone does not mean spread — a single recorded pair (e.g. the
    # antigravity -high naming observation) is fine, a NEW pair or a count
    # jumping without matching usage warrants a quality-canary review first.
    echo "model_substitution=WARN_SUBSTITUTION_ELEVATED"
  elif [ "$SUBSTITUTIONS_7D" -gt 0 ] 2>/dev/null; then
    echo "model_substitution=WARN_SUBSTITUTION_OBSERVED"
  else
    echo "model_substitution=OK"
  fi
else
  echo "model_substitution_warnings_7d=unavailable"
  echo "model_substitution=UNAVAILABLE_VERSION"
fi
echo "model_substitution_coverage=retained container logs only; container recreation resets coverage; current runtime emits per-request warnings so counts track request volume; zero warnings do not prove absence over a full 7d window; judge spread via model_substitution_distinct_pairs and model_substitution_pair lines; OAuth quality probes require separate admission; observation only, not a strict gate"
echo "==tls-cert=="
# 2026-10-13 incident: the vasma fork's RenewTLS renews on the literal day of
# expiry, so its heartbeat stays green while the gateway cert ages into a
# multi-hour TLS outage window. Gate on the served cert's remaining validity
# instead of renewal exit codes; 7 days leaves two wrapper cycles of slack.
TLS_CERT_FILE="/etc/v2ray-agent/tls/fq.sciman.top.crt"
TLS_CERT_END="$(openssl x509 -enddate -noout -in "$TLS_CERT_FILE" 2>/dev/null | cut -d= -f2)"
if [ -n "$TLS_CERT_END" ] && TLS_CERT_END_EPOCH="$(date -d "$TLS_CERT_END" +%s 2>/dev/null)"; then
  TLS_CERT_DAYS_LEFT=$(( ( TLS_CERT_END_EPOCH - $(date +%s) ) / 86400 ))
  echo "gateway_tls_cert_days_left=$TLS_CERT_DAYS_LEFT"
  if [ "$TLS_CERT_DAYS_LEFT" -ge 7 ]; then
    echo tls-cert=OK
  else
    echo tls-cert=EXPIRING
    mark_fail tls-cert
  fi
else
  echo tls-cert=UNREADABLE
  mark_fail tls-cert
fi
echo "==syntax=="
if bash -n "$DIR/auto-update.sh"; then echo updater=OK; else mark_fail updater; fi
if python3 -m py_compile "$DIR/cpa-health.py"; then echo health=OK; else mark_fail health; fi
if python3 -m py_compile "$DIR/cpa_policy.py"; then echo policy=OK; else mark_fail policy; fi
if python3 -m py_compile "$DIR/cpa-admission.py"; then echo admission=OK; else mark_fail admission; fi
if docker compose -f "$DIR/compose.yml" config --quiet; then echo compose=OK; else mark_fail compose; fi
if nginx -t >/tmp/cpa-doctor-nginx-test.log 2>&1; then
  tail -n 2 /tmp/cpa-doctor-nginx-test.log
  echo nginx-syntax=OK
else
  tail -n 5 /tmp/cpa-doctor-nginx-test.log
  mark_fail nginx-syntax
fi
rm -f /tmp/cpa-doctor-nginx-test.log
if [ "$STRICT" = "1" ] && [ "$DOCTOR_FAILED" -ne 0 ]; then
  echo "DOCTOR_CONTRACT_FAILED"
  exit 1
fi
if [ "$STRICT" = "1" ]; then
  echo "DOCTOR_CONTRACT_OK"
elif [ "$DOCTOR_FAILED" -ne 0 ]; then
  # Observe mode never exits non-zero, but a failing observe run must not be
  # readable as a clean contract: give it its own negative verdict marker.
  echo "DOCTOR_CONTRACT_OBSERVE_FAILED"
else
  echo "DOCTOR_CONTRACT_OBSERVE_OK"
fi
