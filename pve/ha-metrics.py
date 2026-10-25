#!/usr/bin/env python3
"""Collect PVE host and QNAP NAS metrics, publish to Home Assistant over MQTT.

Runs on the Proxmox host. Reads local hwmon via `sensors -j` and polls the
QNAP NAS over SNMP v3, then publishes HA MQTT Discovery configs plus state.

Usage:
    ha-metrics.py                 # run forever, publish every interval
    ha-metrics.py --once          # single collection, then exit
    ha-metrics.py --dry-run       # print payloads instead of publishing
    ha-metrics.py --config PATH   # use a specific config file

Configuration comes from a JSON file (default /etc/ha-metrics.json), with
environment variables taking precedence. See config.example.json.
"""

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time

DEFAULT_CONFIG_PATH = "/etc/ha-metrics.json"

DEFAULTS = {
    "mqtt_host": "localhost",
    "mqtt_port": 1883,
    "interval": 60,
    "snmp_host": "localhost",
    "snmp_user": "public",
    "snmp_community": "",
}

ENV_OVERRIDES = {
    "mqtt_host": "MQTT_HOST",
    "mqtt_port": "MQTT_PORT",
    "interval": "INTERVAL",
    "snmp_host": "SNMP_HOST",
    "snmp_user": "SNMP_USER",
    "snmp_community": "SNMP_COMMUNITY",
}

CONFIG = dict(DEFAULTS)


def load_config(path):
    """Merge defaults, the JSON config file, and environment variables."""
    merged = dict(DEFAULTS)
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                merged.update(json.load(handle))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"config {path} unreadable ({exc}); using defaults", file=sys.stderr)

    for key, env_name in ENV_OVERRIDES.items():
        if env_name in os.environ:
            merged[key] = os.environ[env_name]

    merged["mqtt_port"] = int(merged["mqtt_port"])
    merged["interval"] = int(merged["interval"])
    return merged

DISCOVERY_PREFIX = "homeassistant"
STATE_PREFIX = "home"


# ---------------------------------------------------------------- MQTT client


class MqttClient:
    """Minimal MQTT 3.1.1 client: CONNECT with LWT, QoS0 PUBLISH, PINGREQ."""

    def __init__(self, host, port, client_id, will_topic, will_payload="offline"):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.will_topic = will_topic
        self.will_payload = will_payload
        self.sock = None
        self._buf = b""

    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=10)
        self.sock.settimeout(10)
        payload = self._str(self.client_id)
        payload += self._str(self.will_topic) + self._str(self.will_payload)
        # clean session | will flag | will retain
        flags = 0x02 | 0x04 | 0x20
        var_header = self._str("MQTT") + bytes([4, flags]) + (60).to_bytes(2, "big")
        self._send(0x10, var_header + payload)
        ack = self._recv_packet()
        if ack is None or ack[0] != 0x20:
            raise RuntimeError("MQTT CONNECT failed: no CONNACK")
        code = ack[1][1] if len(ack[1]) > 1 else 255
        if code != 0:
            raise RuntimeError(f"MQTT CONNECT rejected with code {code}")

    def publish(self, topic, payload, retain=False):
        body = self._str(topic) + payload.encode("utf-8")
        header = 0x31 if retain else 0x30
        self._send(header, body)

    def ping(self):
        self._send(0xC0, b"")

    def close(self):
        if self.sock:
            try:
                self._send(0xE0, b"")
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    # -- wire helpers

    @staticmethod
    def _str(value):
        raw = value.encode("utf-8")
        return len(raw).to_bytes(2, "big") + raw

    @staticmethod
    def _varint(length):
        out = bytearray()
        while True:
            byte = length % 128
            length //= 128
            if length:
                out.append(byte | 0x80)
            else:
                out.append(byte)
                return bytes(out)

    def _send(self, header, body):
        if self.sock is None:
            raise RuntimeError("not connected")
        self.sock.sendall(bytes([header]) + self._varint(len(body)) + body)

    def _read_exact(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("MQTT connection closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _recv_packet(self):
        header = self._read_exact(1)[0]
        multiplier, remaining = 1, 0
        while True:
            byte = self._read_exact(1)[0]
            remaining += (byte & 127) * multiplier
            if not byte & 0x80:
                break
            multiplier *= 128
            if multiplier > 128 ** 3:
                raise RuntimeError("malformed MQTT remaining length")
        return header, self._read_exact(remaining) if remaining else b""


# ------------------------------------------------------------------- readers


def read_sensors():
    """Return the parsed `sensors -j` tree, or None when unavailable."""
    try:
        out = subprocess.run(
            ["sensors", "-j"], capture_output=True, text=True, timeout=15
        )
        if out.returncode != 0:
            return None
        return json.loads(out.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None


def find_chip(tree, prefix):
    """Locate a chip by prefix; PCI suffixes in chip names can shift."""
    if not tree:
        return None
    for name, chip in tree.items():
        if name.startswith(prefix) and isinstance(chip, dict):
            return chip
    return None


def sensor_value(chip, label, key):
    if not chip:
        return None
    group = chip.get(label)
    if not isinstance(group, dict):
        return None
    value = group.get(key)
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    return None


def snmp_get(oids):
    """Fetch several OIDs in one snmpget call. Returns {oid: raw_value}.

    The reply is parsed by OID rather than by line position: snmpget prints a
    line for OIDs it could not resolve, and any positional mapping would then
    shift every later value onto the wrong metric.
    """
    if not oids:
        return {}
    if CONFIG["snmp_community"]:
        auth = ["-v2c", "-c", CONFIG["snmp_community"]]
    else:
        auth = ["-v3", "-l", "noAuthNoPriv", "-u", CONFIG["snmp_user"]]
    cmd = ["snmpget"] + auth + ["-t", "5", "-r", "1", "-On",
                                CONFIG["snmp_host"]] + oids
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return {}

    values = {}
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line or "No Such" in line or "No Response" in line:
            continue
        oid, sep, value = line.partition(" = ")
        if not sep:
            continue
        oid = oid.lstrip(".")  # -On prefixes the OID with a dot
        if oid not in oids:
            continue
        if value.startswith("STRING: "):
            value = value[len("STRING: "):]
        values[oid] = value.strip().strip('"')
    return values


NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def first_number(raw):
    if raw is None:
        return None
    match = NUMBER.search(raw)
    return round(float(match.group()), 2) if match else None


QNAP = "1.3.6.1.4.1.24681.1.2"


def read_qnap():
    """Poll QNAP SNMP. QNAP returns unit-suffixed strings like '48 C/118 F'."""
    oids = {
        "cpu_temp": f"{QNAP}.5.0",
        "system_temp": f"{QNAP}.6.0",
        "fan1_rpm": f"{QNAP}.15.1.3.1",
        "cpu_usage": f"{QNAP}.1.0",
        "mem_total": f"{QNAP}.2.0",
        "mem_free": f"{QNAP}.3.0",
    }
    for slot in range(1, 7):
        oids[f"hdd{slot}_temp"] = f"{QNAP}.11.1.3.{slot}"

    raw = snmp_get(list(oids.values()))
    if not raw:
        return {}

    by_name = {name: raw.get(oid) for name, oid in oids.items()}

    values = {}
    for name in ("cpu_temp", "system_temp", "fan1_rpm", "cpu_usage"):
        parsed = first_number(by_name.get(name))
        if parsed is not None:
            values[name] = parsed

    for slot in range(1, 7):
        parsed = first_number(by_name.get(f"hdd{slot}_temp"))
        if parsed is not None:
            values[f"hdd{slot}_temp"] = parsed

    total = first_number(by_name.get("mem_total"))
    free = first_number(by_name.get("mem_free"))
    if total and free is not None and total > 0:
        values["memory_usage"] = round((total - free) / total * 100, 1)

    return values


def read_pve():
    tree = read_sensors()
    k10 = find_chip(tree, "k10temp")
    nvme = find_chip(tree, "nvme")
    gpu = find_chip(tree, "amdgpu")

    values = {
        "cpu_tctl": sensor_value(k10, "Tctl", "temp1_input"),
        "nvme_composite": sensor_value(nvme, "Composite", "temp1_input"),
        "nvme_sensor1": sensor_value(nvme, "Sensor 1", "temp2_input"),
        "nvme_sensor2": sensor_value(nvme, "Sensor 2", "temp3_input"),
        "gpu_edge": sensor_value(gpu, "edge", "temp1_input"),
        "gpu_ppt": sensor_value(gpu, "PPT", "power1_input"),
        "gpu_vddgfx": sensor_value(gpu, "vddgfx", "in0_input"),
        "gpu_vddnb": sensor_value(gpu, "vddnb", "in1_input"),
    }
    return {k: v for k, v in values.items() if v is not None}


# ------------------------------------------------------------------ discovery

DEVICES = {
    "pve": {
        "name": "PVE (SER8)",
        "manufacturer": "AZW",
        "model": "SER8",
        "identifiers": ["ha-metrics-pve"],
    },
    "qnap": {
        "name": "QNAP NAS (TS-X64)",
        "manufacturer": "QNAP",
        "model": "TS-X64",
        "identifiers": ["ha-metrics-qnap"],
    },
}

# name -> (friendly name, unit, device_class, icon, state_class)
SENSORS = {
    "pve": {
        "cpu_tctl": ("CPU Tctl", "°C", "temperature", None, "measurement"),
        "nvme_composite": ("NVMe 复合温度", "°C", "temperature", None, "measurement"),
        "nvme_sensor1": ("NVMe 颗粒 1", "°C", "temperature", None, "measurement"),
        "nvme_sensor2": ("NVMe 颗粒 2", "°C", "temperature", None, "measurement"),
        "gpu_edge": ("GPU 温度", "°C", "temperature", None, "measurement"),
        "gpu_ppt": ("GPU 功耗", "W", "power", None, "measurement"),
        "gpu_vddgfx": ("GPU 核心电压", "V", "voltage", None, "measurement"),
        "gpu_vddnb": ("GPU 北桥电压", "V", "voltage", None, "measurement"),
    },
    "qnap": {
        "cpu_temp": ("CPU 温度", "°C", "temperature", None, "measurement"),
        "system_temp": ("系统温度", "°C", "temperature", None, "measurement"),
        "hdd1_temp": ("硬盘 1 温度", "°C", "temperature", "mdi:harddisk", "measurement"),
        "hdd2_temp": ("硬盘 2 温度", "°C", "temperature", "mdi:harddisk", "measurement"),
        "hdd3_temp": ("硬盘 3 温度", "°C", "temperature", "mdi:harddisk", "measurement"),
        "hdd4_temp": ("硬盘 4 温度", "°C", "temperature", "mdi:harddisk", "measurement"),
        "hdd5_temp": ("硬盘 5 温度", "°C", "temperature", "mdi:harddisk", "measurement"),
        "hdd6_temp": ("硬盘 6 温度", "°C", "temperature", "mdi:harddisk", "measurement"),
        "fan1_rpm": ("系统风扇转速", "RPM", None, "mdi:fan", "measurement"),
        "cpu_usage": ("CPU 使用率", "%", None, "mdi:cpu-64-bit", "measurement"),
        "memory_usage": ("内存使用率", "%", None, "mdi:memory", "measurement"),
    },
}


def discovery_payload(node, metric, spec):
    name, unit, device_class, icon, state_class = spec
    device = DEVICES[node]
    payload = {
        "name": name,
        "object_id": f"{node}_{metric}",
        "unique_id": f"{node}_{metric}",
        "state_topic": f"{STATE_PREFIX}/{node}/{metric}",
        "availability_topic": f"{STATE_PREFIX}/{node}/availability",
        "payload_available": "online",
        "payload_not_available": "offline",
        "device": {
            "identifiers": device["identifiers"],
            "name": device["name"],
            "manufacturer": device["manufacturer"],
            "model": device["model"],
        },
    }
    if unit:
        payload["unit_of_measurement"] = unit
    if device_class:
        payload["device_class"] = device_class
    if icon:
        payload["icon"] = icon
    if state_class:
        payload["state_class"] = state_class
    return json.dumps(payload, ensure_ascii=False)


def collect():
    """Return {node: {metric: value}}, never raising."""
    results = {}
    for node, reader in (("pve", read_pve), ("qnap", read_qnap)):
        try:
            results[node] = reader()
        except Exception as exc:  # noqa: BLE001 - one bad source must not stop others
            print(f"collect {node} failed: {exc}", file=sys.stderr)
            results[node] = {}
    return results


def publish_discovery(client, dry_run):
    for node, metrics in SENSORS.items():
        for metric, spec in metrics.items():
            topic = f"{DISCOVERY_PREFIX}/sensor/{node}/{metric}/config"
            payload = discovery_payload(node, metric, spec)
            if dry_run:
                print(f"[discovery] {topic}\n  {payload}")
            else:
                client.publish(topic, payload, retain=True)


def publish_state(client, results, dry_run):
    for node, metrics in SENSORS.items():
        values = results.get(node, {})
        for metric in metrics:
            value = values.get(metric)
            topic = f"{STATE_PREFIX}/{node}/{metric}"
            payload = "" if value is None else str(value)
            if dry_run:
                print(f"[state] {topic} = {payload!r}")
            else:
                client.publish(topic, payload)
        availability = "online" if values else "offline"
        topic = f"{STATE_PREFIX}/{node}/availability"
        if dry_run:
            print(f"[availability] {topic} = {availability}")
        else:
            client.publish(topic, availability, retain=True)


def run_once(dry_run):
    client = None
    if not dry_run:
        client = MqttClient(
            CONFIG["mqtt_host"], CONFIG["mqtt_port"], "ha-metrics-pve",
            f"{STATE_PREFIX}/pve/availability",
        )
        client.connect()
    try:
        publish_discovery(client, dry_run)
        publish_state(client, collect(), dry_run)
    finally:
        if client:
            client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="collect once and exit")
    parser.add_argument("--dry-run", action="store_true", help="print, do not publish")
    parser.add_argument("--interval", type=int, default=None, help="seconds between runs")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="config file path")
    args = parser.parse_args()

    CONFIG.update(load_config(args.config))
    interval = args.interval or CONFIG["interval"]

    if args.once or args.dry_run:
        run_once(args.dry_run)
        return 0

    client = None
    while True:
        try:
            if client is None:
                client = MqttClient(
                    CONFIG["mqtt_host"], CONFIG["mqtt_port"], "ha-metrics-pve",
                    f"{STATE_PREFIX}/pve/availability",
                )
                client.connect()
                publish_discovery(client, dry_run=False)
            publish_state(client, collect(), dry_run=False)
        except Exception as exc:  # noqa: BLE001 - keep the collector alive
            print(f"cycle failed: {exc}", file=sys.stderr)
            if client:
                client.close()
                client = None
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
