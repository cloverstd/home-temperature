#!/bin/sh
# Collect OpenWrt metrics from sysfs and publish to Home Assistant over MQTT.
#
# Runs on the router (NanoPi R76S, OpenWrt 24.10). Pure shell so it works
# without python. Publishes HA MQTT Discovery configs plus state.

set -u

CONFIG_FILE="${HA_METRICS_CONFIG:-/etc/ha-metrics.conf}"

# Precedence: environment > config file > built-in defaults.
ENV_MQTT_HOST="${MQTT_HOST:-}"
ENV_MQTT_PORT="${MQTT_PORT:-}"
ENV_INTERVAL="${INTERVAL:-}"

MQTT_HOST="localhost"
MQTT_PORT="1883"
INTERVAL="60"

NODE="openwrt"
STATE_PREFIX="home"
DISCOVERY_PREFIX="homeassistant"

DRY_RUN=0
ONCE=0

log() { echo "$*" >&2; }

load_config() {
    # Config is KEY=value lines. Read them as data rather than sourcing the
    # file, so a stray command in the config cannot execute.
    [ -r "$CONFIG_FILE" ] || return 0
    while IFS='=' read -r key value; do
        case "$key" in
            ''|'#'*) continue ;;
            MQTT_HOST) MQTT_HOST="$value" ;;
            MQTT_PORT) MQTT_PORT="$value" ;;
            INTERVAL) INTERVAL="$value" ;;
        esac
    done < "$CONFIG_FILE"
}

apply_env_overrides() {
    [ -n "$ENV_MQTT_HOST" ] && MQTT_HOST="$ENV_MQTT_HOST"
    [ -n "$ENV_MQTT_PORT" ] && MQTT_PORT="$ENV_MQTT_PORT"
    [ -n "$ENV_INTERVAL" ] && INTERVAL="$ENV_INTERVAL"
}

pub() {
    # pub <topic> <payload> [retain]
    topic="$1"
    payload="$2"
    retain="${3:-0}"
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "[publish] $topic = '$payload' retain=$retain"
        return 0
    fi
    if [ "$retain" -eq 1 ]; then
        mosquitto_pub -h "$MQTT_HOST" -p "$MQTT_PORT" -t "$topic" -m "$payload" -r
    else
        mosquitto_pub -h "$MQTT_HOST" -p "$MQTT_PORT" -t "$topic" -m "$payload"
    fi
}

# ---------------------------------------------------------------- sensor reads

read_thermal() {
    # read_thermal <hwmon name> -> degrees C, empty when unavailable
    name="$1"
    for dir in /sys/class/hwmon/hwmon*; do
        [ -r "$dir/name" ] || continue
        if [ "$(cat "$dir/name" 2>/dev/null)" = "$name" ]; then
            raw=$(cat "$dir/temp1_input" 2>/dev/null) || return 0
            [ -n "$raw" ] && echo "$raw" | awk '{printf "%.1f", $1/1000}'
            return 0
        fi
    done
}

read_voltage() {
    raw=$(cat /sys/class/hwmon/hwmon6/in0_input 2>/dev/null) || return 0
    [ -n "$raw" ] && echo "$raw" | awk '{printf "%.2f", $1/1000}'
}

read_pwm_percent() {
    raw=$(cat /sys/class/hwmon/hwmon7/pwm1 2>/dev/null) || return 0
    [ -n "$raw" ] && echo "$raw" | awk '{printf "%d", $1*100/255}'
}

# ------------------------------------------------------------------- discovery

# metric|friendly name|unit|device_class|icon
SENSORS='
soc_temp|SoC 温度|°C|temperature|
bigcore_temp|大核温度|°C|temperature|
little_core_temp|小核温度|°C|temperature|
ddr_temp|DDR 温度|°C|temperature|
npu_temp|NPU 温度|°C|temperature|
gpu_temp|GPU 温度|°C|temperature|
input_voltage|输入电压|V|voltage|
fan_pwm|风扇 PWM|%||mdi:fan
'

discovery_payload() {
    metric="$1"; name="$2"; unit="$3"; device_class="$4"; icon="$5"
    printf '{"name":"%s","object_id":"%s_%s","unique_id":"%s_%s",' \
        "$name" "$NODE" "$metric" "$NODE" "$metric"
    printf '"state_topic":"%s/%s/%s",' "$STATE_PREFIX" "$NODE" "$metric"
    printf '"availability_topic":"%s/%s/availability",' "$STATE_PREFIX" "$NODE"
    printf '"payload_available":"online","payload_not_available":"offline",'
    printf '"device":{"identifiers":["ha-metrics-%s"],' "$NODE"
    printf '"name":"OpenWrt (NanoPi R76S)",'
    printf '"manufacturer":"FriendlyElec","model":"NanoPi R76S"}'
    [ -n "$unit" ] && printf ',"unit_of_measurement":"%s"' "$unit"
    [ -n "$device_class" ] && printf ',"device_class":"%s"' "$device_class"
    [ -n "$icon" ] && printf ',"icon":"%s"' "$icon"
    printf ',"state_class":"measurement"}'
}

publish_discovery() {
    echo "$SENSORS" | while IFS='|' read -r metric name unit device_class icon; do
        [ -z "$metric" ] && continue
        payload=$(discovery_payload "$metric" "$name" "$unit" "$device_class" "$icon")
        pub "$DISCOVERY_PREFIX/sensor/$NODE/$metric/config" "$payload" 1
    done
}

# ---------------------------------------------------------------- state publish

publish_state() {
    have_any=0

    soc=$(read_thermal soc_thermal)
    big=$(read_thermal bigcore_thermal)
    little=$(read_thermal little_core_thermal)
    ddr=$(read_thermal ddr_thermal)
    npu=$(read_thermal npu_thermal)
    gpu=$(read_thermal gpu_thermal)
    volt=$(read_voltage)
    pwm=$(read_pwm_percent)

    for pair in "soc_temp:$soc" "bigcore_temp:$big" "little_core_temp:$little" \
                "ddr_temp:$ddr" "npu_temp:$npu" "gpu_temp:$gpu" \
                "input_voltage:$volt" "fan_pwm:$pwm"; do
        metric=${pair%%:*}
        value=${pair#*:}
        pub "$STATE_PREFIX/$NODE/$metric" "$value"
        [ -n "$value" ] && have_any=1
    done

    if [ "$have_any" -eq 1 ]; then
        pub "$STATE_PREFIX/$NODE/availability" "online" 1
    else
        pub "$STATE_PREFIX/$NODE/availability" "offline" 1
    fi
}

# ------------------------------------------------------------------------ main

# Parse first so --config can select the file before it is read.
CLI_INTERVAL=""
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --once) ONCE=1 ;;
        --config) shift; CONFIG_FILE="$1" ;;
        --interval) shift; CLI_INTERVAL="$1" ;;
        *) log "unknown argument: $1"; exit 2 ;;
    esac
    shift
done

load_config
apply_env_overrides
[ -n "$CLI_INTERVAL" ] && INTERVAL="$CLI_INTERVAL"

if [ "$DRY_RUN" -eq 1 ] || [ "$ONCE" -eq 1 ]; then
    publish_discovery
    publish_state
    exit 0
fi

publish_discovery
while :; do
    publish_state || log "state publish failed"
    sleep "$INTERVAL"
done
