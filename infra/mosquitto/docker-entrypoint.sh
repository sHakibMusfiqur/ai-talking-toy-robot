set -eu

CONF=/mosquitto/config/mosquitto.conf

mosquitto -c "$CONF" &
BROKER_PID=$!
trap 'kill -TERM "$BROKER_PID" 2>/dev/null || true' TERM INT EXIT

CTRL="mosquitto_ctrl -h 127.0.0.1 -u admin -P ${MOSQUITTO_DYNSEC_PASSWORD:?MOSQUITTO_DYNSEC_PASSWORD must be set}"

fail() {
    echo "mosquitto bootstrap: FAILED - $1" >&2
    kill -TERM "$BROKER_PID" 2>/dev/null || true
    exit 1
}

# Run one dynsec command. mosquitto_ctrl exits 0 even when the command
# fails, so the OUTPUT decides the outcome:
#   - no "Error" in output                       -> success
#   - expected idempotent response (role/ACL
#     already exists, or cleanup target not
#     found)                                     -> success (safe re-run)
#   - any other "Error"                          -> genuine failure, abort
# This keeps re-running the bootstrap against an already-initialised volume
# safe while making sure real Dynamic Security failures are never hidden.
run_dynsec() {
    rc=0
    out=$($CTRL dynsec "$@" 2>&1) || rc=$?
    case "$out" in
        *"Role already exists"*|*"ACL with this topic already exists"*|*"Client not found"*)
            return 0
            ;;
        *Error*)
            fail "dynsec $* -> $out"
            ;;
    esac
    [ "$rc" = "0" ] || fail "dynsec $* (exit $rc) -> $out"
}

# expect_in <haystack> <fixed-needle> <context>: needle must appear with
# whitespace squeezed (getRole output is column-padded).
expect_in() {
    printf '%s\n' "$1" | tr -s ' ' | grep -qF "$2" || fail "$3: missing '$2'"
}

# expect_clean <output> <context>: dynsec read commands must not report an error.
expect_clean() {
    case "$1" in
        *Error*) fail "$2: $1" ;;
    esac
}

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
run_dynsec createRole device
# Device publishes: exactly its own status/heartbeat/event (Section 11.1).
run_dynsec addRoleACL device publishClientSend "robots/%u/status" allow 10
run_dynsec addRoleACL device publishClientSend "robots/%u/heartbeat" allow 10
run_dynsec addRoleACL device publishClientSend "robots/%u/event" allow 10
# Device subscribes: exactly its own command + the three notify topics.
run_dynsec addRoleACL device subscribePattern "robots/%u/command" allow 10
run_dynsec addRoleACL device subscribePattern "robots/%u/config/notify" allow 10
run_dynsec addRoleACL device subscribePattern "robots/%u/ota/notify" allow 10
run_dynsec addRoleACL device subscribePattern "robots/%u/content/notify" allow 10
# Device receives: only on its own command/notify topics.
run_dynsec addRoleACL device publishClientReceive "robots/%u/command" allow 10
run_dynsec addRoleACL device publishClientReceive "robots/%u/config/notify" allow 10
run_dynsec addRoleACL device publishClientReceive "robots/%u/ota/notify" allow 10
run_dynsec addRoleACL device publishClientReceive "robots/%u/content/notify" allow 10

# --- Role "django-service": the bridge's exact Section 11.2 rights ---------
run_dynsec createRole django-service
run_dynsec addRoleACL django-service publishClientSend "robots/+/command" allow 20
run_dynsec addRoleACL django-service publishClientSend "robots/+/config/notify" allow 20
run_dynsec addRoleACL django-service publishClientSend "robots/+/ota/notify" allow 20
run_dynsec addRoleACL django-service publishClientSend "robots/+/content/notify" allow 20
run_dynsec addRoleACL django-service subscribePattern "robots/+/status" allow 20
run_dynsec addRoleACL django-service subscribePattern "robots/+/heartbeat" allow 20
run_dynsec addRoleACL django-service subscribePattern "robots/+/event" allow 20
run_dynsec addRoleACL django-service publishClientReceive "robots/+/status" allow 20
run_dynsec addRoleACL django-service publishClientReceive "robots/+/heartbeat" allow 20
run_dynsec addRoleACL django-service publishClientReceive "robots/+/event" allow 20

# Deny-by-default for message receipt (defaults allow receive); subscribe
# and publish-outside-ACL are already deny-by-default.
run_dynsec setDefaultACLAccess publishClientReceive deny
# The auto-generated demo client (2.1 default config) must never exist.
run_dynsec deleteClient democlient

# --- Final validation: confirm the required state is actually present -------
# admin client state, both roles, every required ACL pattern, deny defaults,
# and democlient absence are checked before bootstrap may report success.
validate_bootstrap() {
    roles=$($CTRL dynsec listRoles 2>&1) || true
    expect_clean "$roles" "listRoles"
    printf '%s\n' "$roles" | grep -q "^device[[:space:]]*$" || fail "role 'device' missing"
    printf '%s\n' "$roles" | grep -q "^django-service[[:space:]]*$" || fail "role 'django-service' missing"

    dev=$($CTRL dynsec getRole device 2>&1) || true
    expect_clean "$dev" "getRole device"
    # 11 device ACLs: own status/heartbeat/event, own command + 3 notifies.
    expect_in "$dev" "publishClientSend : allow : robots/%u/status" "device ACL"
    expect_in "$dev" "publishClientSend : allow : robots/%u/heartbeat" "device ACL"
    expect_in "$dev" "publishClientSend : allow : robots/%u/event" "device ACL"
    expect_in "$dev" "subscribePattern : allow : robots/%u/command" "device ACL"
    expect_in "$dev" "subscribePattern : allow : robots/%u/config/notify" "device ACL"
    expect_in "$dev" "subscribePattern : allow : robots/%u/ota/notify" "device ACL"
    expect_in "$dev" "subscribePattern : allow : robots/%u/content/notify" "device ACL"
    expect_in "$dev" "publishClientReceive : allow : robots/%u/command" "device ACL"
    expect_in "$dev" "publishClientReceive : allow : robots/%u/config/notify" "device ACL"
    expect_in "$dev" "publishClientReceive : allow : robots/%u/ota/notify" "device ACL"
    expect_in "$dev" "publishClientReceive : allow : robots/%u/content/notify" "device ACL"

    svc=$($CTRL dynsec getRole django-service 2>&1) || true
    expect_clean "$svc" "getRole django-service"
    # 10 bridge ACLs: publish command + 3 notifies, subscribe/receive status
    # heartbeat event (Section 11.2).
    expect_in "$svc" "publishClientSend : allow : robots/+/command" "django-service ACL"
    expect_in "$svc" "publishClientSend : allow : robots/+/config/notify" "django-service ACL"
    expect_in "$svc" "publishClientSend : allow : robots/+/ota/notify" "django-service ACL"
    expect_in "$svc" "publishClientSend : allow : robots/+/content/notify" "django-service ACL"
    expect_in "$svc" "subscribePattern : allow : robots/+/status" "django-service ACL"
    expect_in "$svc" "subscribePattern : allow : robots/+/heartbeat" "django-service ACL"
    expect_in "$svc" "subscribePattern : allow : robots/+/event" "django-service ACL"
    expect_in "$svc" "publishClientReceive : allow : robots/+/status" "django-service ACL"
    expect_in "$svc" "publishClientReceive : allow : robots/+/heartbeat" "django-service ACL"
    expect_in "$svc" "publishClientReceive : allow : robots/+/event" "django-service ACL"

    clients=$($CTRL dynsec listClients 2>&1) || true
    expect_clean "$clients" "listClients"
    printf '%s\n' "$clients" | grep -q "^admin[[:space:]]*$" || fail "admin client missing"
    if printf '%s\n' "$clients" | grep -q "^democlient[[:space:]]*$"; then
        fail "democlient must not exist"
    fi

    defaults=$($CTRL dynsec getDefaultACLAccess 2>&1) || true
    expect_clean "$defaults" "getDefaultACLAccess"
    expect_in "$defaults" "publishClientSend : deny" "defaults"
    expect_in "$defaults" "publishClientReceive : deny" "defaults"
    expect_in "$defaults" "subscribe : deny" "defaults"
}

validate_bootstrap

echo "mosquitto bootstrap: roles ready, dynsec state validated"

wait "$BROKER_PID"
