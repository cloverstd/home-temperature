#!/bin/sh
# Install the HA metrics collector on OpenWrt.
#
# Reads sysfs thermal/voltage/pwm and publishes to MQTT.
# Run as root on the router: sh install.sh

set -eu

SRC_DIR=$(cd "$(dirname "$0")" && pwd)

echo "==> checking dependencies"
if ! command -v mosquitto_pub >/dev/null 2>&1; then
    echo "==> installing mosquitto client"
    opkg update
    opkg install mosquitto-client-nossl
fi

echo "==> installing collector"
cp "$SRC_DIR/ha-metrics.sh" /usr/bin/ha-metrics.sh
chmod 0755 /usr/bin/ha-metrics.sh
cp "$SRC_DIR/ha-metrics.init" /etc/init.d/ha-metrics
chmod 0755 /etc/init.d/ha-metrics

echo "==> checking config"
if [ ! -f /etc/ha-metrics.conf ]; then
    cat >&2 <<'EOF'
/etc/ha-metrics.conf not found. Create it first, for example:

  cp ha-metrics.conf.example /etc/ha-metrics.conf
  chmod 600 /etc/ha-metrics.conf
  # then set MQTT_HOST to your broker

See ha-metrics.conf.example for the available keys.
EOF
    exit 1
fi
chmod 600 /etc/ha-metrics.conf

echo "==> enabling service"
/etc/init.d/ha-metrics enable
/etc/init.d/ha-metrics restart

sleep 2
echo "==> status"
/etc/init.d/ha-metrics status 2>/dev/null || true

echo
echo "Verify data flow with:"
echo "  mosquitto_sub -h <broker-from-config> -t 'home/openwrt/#' -v"
echo "Check logs with:"
echo "  logread -e ha-metrics"
