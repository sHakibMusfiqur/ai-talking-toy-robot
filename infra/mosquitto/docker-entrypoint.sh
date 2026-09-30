set -eu

CONF=/mosquitto/config/mosquitto.conf

mosquitto -c "$CONF" &
BROKER_PID=$!
trap 'kill -TERM "$BROKER_PID" 2>/dev/null || true' TERM INT

CTRL="mosquitto_ctrl -h 127.0.0.1 -u admin -P ${MOSQUITTO_DYNSEC_PASSWORD:?MOSQUITTO_DYNSEC_PASSWORD must be set}"

ready=0
i=0
while [ "$i" -lt 60 ]; do
    if $CTRL dynsec listRoles >/dev/null 2>&1; then
        ready=1
        break
    fi
    i=$((i + 1))
    sleep 0.5
done
if [ "$ready" != "1" ]; then
    echo "mosquitto bootstrap: broker did not become ready" >&2
    kill "$BROKER_PID" 2>/dev/null || true
    exit 1
fi

# --- Role "device": per-device scoping via %u (username = device_id) -------
$CTRL dynsec createRole device 2>/dev/null || true
# Device publishes: exactly its own status/heartbeat/event (Section 11.1).
$CTRL dynsec addRoleACL device publishClientSend "robots/%u/status" allow 10 2>/dev/null || true
$CTRL dynsec addRoleACL device publishClientSend "robots/%u/heartbeat" allow 10 2>/dev/null || true
$CTRL dynsec addRoleACL device publishClientSend "robots/%u/event" allow 10 2>/dev/null || true
# Device subscribes: exactly its own command + the three notify topics.
$CTRL dynsec addRoleACL device subscribePattern "robots/%u/command" allow 10 2>/dev/null || true
$CTRL dynsec addRoleACL device subscribePattern "robots/%u/config/notify" allow 10 2>/dev/null || true
$CTRL dynsec addRoleACL device subscribePattern "robots/%u/ota/notify" allow 10 2>/dev/null || true
$CTRL dynsec addRoleACL device subscribePattern "robots/%u/content/notify" allow 10 2>/dev/null || true
# Device receives: only on its own command/notify topics.
$CTRL dynsec addRoleACL device publishClientReceive "robots/%u/command" allow 10 2>/dev/null || true
$CTRL dynsec addRoleACL device publishClientReceive "robots/%u/config/notify" allow 10 2>/dev/null || true
$CTRL dynsec addRoleACL device publishClientReceive "robots/%u/ota/notify" allow 10 2>/dev/null || true
$CTRL dynsec addRoleACL device publishClientReceive "robots/%u/content/notify" allow 10 2>/dev/null || true

# --- Role "django-service": the bridge's exact Section 11.2 rights ---------
$CTRL dynsec createRole django-service 2>/dev/null || true
$CTRL dynsec addRoleACL django-service publishClientSend "robots/+/command" allow 20 2>/dev/null || true
$CTRL dynsec addRoleACL django-service publishClientSend "robots/+/config/notify" allow 20 2>/dev/null || true
$CTRL dynsec addRoleACL django-service publishClientSend "robots/+/ota/notify" allow 20 2>/dev/null || true
$CTRL dynsec addRoleACL django-service publishClientSend "robots/+/content/notify" allow 20 2>/dev/null || true
$CTRL dynsec addRoleACL django-service subscribePattern "robots/+/status" allow 20 2>/dev/null || true
$CTRL dynsec addRoleACL django-service subscribePattern "robots/+/heartbeat" allow 20 2>/dev/null || true
$CTRL dynsec addRoleACL django-service subscribePattern "robots/+/event" allow 20 2>/dev/null || true
$CTRL dynsec addRoleACL django-service publishClientReceive "robots/+/status" allow 20 2>/dev/null || true
$CTRL dynsec addRoleACL django-service publishClientReceive "robots/+/heartbeat" allow 20 2>/dev/null || true
$CTRL dynsec addRoleACL django-service publishClientReceive "robots/+/event" allow 20 2>/dev/null || true

# Deny-by-default for message receipt (defaults allow receive); subscribe
# and publish-outside-ACL are already deny-by-default.
$CTRL dynsec setDefaultACLAccess publishClientReceive deny 2>/dev/null || true
# The auto-generated demo client (2.1 default config) must never exist.
$CTRL dynsec deleteClient democlient 2>/dev/null || true

echo "mosquitto bootstrap: roles ready"

wait "$BROKER_PID"
