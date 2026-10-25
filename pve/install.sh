#!/bin/sh
# Install the HA metrics collector on the Proxmox host.
#
# Reads local hwmon and polls the QNAP NAS over SNMP, then publishes to MQTT.
# Run as root on the PVE host: sh install.sh

set -eu

PREFIX=/usr/local/bin
UNIT_DIR=/etc/systemd/system
SRC_DIR=$(cd "$(dirname "$0")" && pwd)

if [ "$(id -u)" -ne 0 ]; then
    echo "must run as root" >&2
    exit 1
fi

echo "==> checking dependencies"
for bin in sensors snmpget python3; do
    if ! command -v "$bin" >/dev/null 2>&1; then
        echo "missing: $bin" >&2
        case "$bin" in
            sensors) echo "  install with: apt-get install -y lm-sensors" >&2 ;;
            snmpget) echo "  install with: apt-get install -y snmp" >&2 ;;
            python3) echo "  install with: apt-get install -y python3" >&2 ;;
        esac
        exit 1
    fi
done

echo "==> installing collector"
install -m 0755 "$SRC_DIR/ha-metrics.py" "$PREFIX/ha-metrics.py"

echo "==> checking config"
if [ ! -f /etc/ha-metrics.json ]; then
    cat >&2 <<'EOF'
/etc/ha-metrics.json not found. Create it first, for example:

  cp pve/config.example.json /etc/ha-metrics.json
  chmod 600 /etc/ha-metrics.json
  # then fill in mqtt_host, snmp_host and snmp_user

See pve/config.example.json for the available keys.
EOF
    exit 1
fi
chmod 600 /etc/ha-metrics.json

echo "==> installing systemd unit"
install -m 0644 "$SRC_DIR/ha-metrics.service" "$UNIT_DIR/ha-metrics.service"
systemctl daemon-reload
systemctl enable --now ha-metrics.service
systemctl restart ha-metrics.service

echo "==> status"
sleep 3
systemctl --no-pager --lines=5 status ha-metrics.service || true

echo
echo "Verify data flow with:"
echo "  mosquitto_sub -h <broker-from-config> -t 'home/#' -v"
