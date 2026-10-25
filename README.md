# home-temperature

把家里各设备的温度、风扇转速、功耗电压采集进 Home Assistant。

## 当前覆盖

| 设备 | 采集内容 |
|---|---|
| **PVE**（迷你主机） | CPU Tctl、NVMe 复合与双颗粒温度、GPU 温度、GPU 功耗（W）、两路 GPU 电压 |
| **OpenWrt**（NanoPi R76S） | SoC / 大核 / 小核 / DDR / NPU / GPU 六路温度、输入电压、风扇 PWM |
| **QNAP NAS**（TS-X64） | CPU 温度、系统温度、6 块硬盘温度、系统风扇转速、CPU 与内存使用率 |
| **机柜风扇**（ESP32） | 双风扇转速 —— 原本已通过 MQTT 接入，本仓库不涉及 |

## 无法采集的设备

这些是硬件或固件层面的限制，不是配置问题：

| 设备 | 原因 |
|---|---|
| **PVE 风扇转速** | 迷你主机使用 EC 散热，不通过标准接口暴露。`modprobe nct6775` 报 No such device，ACPI 无 fan 节点，amdgpu 无 fan 属性 |
| **TP-Link XDR3050** | 家用固件只有 Web 登录页，无 API、无 SSH |
| **小米路由器** | LuCI API 返回 405，miIO 无响应，无 SSH |
| **电信光猫** | 仅 Web 与 FTP，均需登录，无状态接口 |
| **USB 硬盘盒** | 纯硬盘盒，无监控芯片；其硬盘温度已包含在 NAS 的 HDD 列表中 |

## 架构

```
PVE 主机                               OpenWrt 路由器
  └─ ha-metrics.py  [systemd]           └─ ha-metrics.sh  [procd]
     ├─ 本地 hwmon (sensors -j)            ├─ /sys/class/hwmon
     └─ SNMP → NAS                         └─ /sys/class/thermal
              │                                      │
              └──────────┬───────────────────────────┘
                         ▼
                   MQTT broker
                         ▼
                  HA MQTT Discovery 自动发现
```

NAS 的 SSH 未开放，因此 NAS 的 SNMP 轮询放在 PVE 上执行。其余节点各自采集。

## 配置

> **本仓库不含任何内网地址、域名或用户名。** 这些值全部放在被 `.gitignore` 忽略的本地配置文件中。

### PVE

```sh
cp pve/config.example.json /etc/ha-metrics.json
chmod 600 /etc/ha-metrics.json
$EDITOR /etc/ha-metrics.json
```

```json
{
  "mqtt_host": "mqtt.example.lan",
  "mqtt_port": 1883,
  "interval": 60,
  "snmp_host": "nas.example.lan",
  "snmp_user": "snmpuser",
  "snmp_community": ""
}
```

`snmp_community` 留空时走 SNMP v3（`noAuthNoPriv`）；填入字符串则改用 v2c。

### OpenWrt

```sh
cp openwrt/ha-metrics.conf.example /etc/ha-metrics.conf
chmod 600 /etc/ha-metrics.conf
vi /etc/ha-metrics.conf
```

```ini
MQTT_HOST=mqtt.example.lan
MQTT_PORT=1883
INTERVAL=60
```

两边的环境变量都会覆盖配置文件，便于临时调试。

## 部署

### PVE

```sh
scp -r pve root@<pve-host>:/tmp/
ssh root@<pve-host> 'cd /tmp/pve && sh install.sh'
```

依赖（`lm-sensors`、`snmp`、`python3`）缺失时安装脚本会提示对应命令。
配置未就绪时脚本会拒绝安装并打印示例。

### OpenWrt

```sh
scp -r openwrt root@<router-host>:/tmp/
ssh root@<router-host> 'cd /tmp/openwrt && sh install.sh'
```

安装脚本会自动 `opkg install mosquitto-client-nossl`。

## 验证

```sh
# 看数据流
mosquitto_sub -h <broker> -t 'home/#' -v

# 单次采集，打印 payload 不发送
/usr/local/bin/ha-metrics.py --dry-run
/usr/bin/ha-metrics.sh --dry-run

# 服务状态
systemctl status ha-metrics        # PVE
/etc/init.d/ha-metrics status      # OpenWrt
logread -e ha-metrics              # OpenWrt 日志
```

在 HA 中会看到三个设备：`PVE`、`QNAP NAS`、`OpenWrt`。

## 配置项

| 键 | 默认值 | 说明 |
|---|---|---|
| `mqtt_host` / `MQTT_HOST` | `localhost` | MQTT broker 地址 |
| `mqtt_port` / `MQTT_PORT` | `1883` | broker 端口 |
| `interval` / `INTERVAL` | `60` | 采集间隔（秒） |
| `snmp_host` | `localhost` | NAS 地址（仅 PVE 侧） |
| `snmp_user` | `public` | SNMP v3 用户名（仅 PVE 侧） |
| `snmp_community` | 空 | 设置后改用 v2c 认证（仅 PVE 侧） |

## MQTT 主题

- 状态：`home/{node}/{metric}`
- 发现：`homeassistant/sensor/{node}/{metric}/config`（retained）
- 可用性：`home/{node}/availability`（retained，LWT 为 `offline`）

`node` 取值为 `pve`、`qnap`、`openwrt`。

## 新增指标

PVE 侧：在 `SENSORS` 字典里加一条，并在 `read_pve()` / `read_qnap()` 返回值中提供同名 key。
OpenWrt 侧：在 `ha-metrics.sh` 的 `SENSORS` 表里加一行，并在 `publish_state()` 中补上读取逻辑。

发现配置发布后需要清掉旧的 retained 消息才能改名：

```sh
mosquitto_pub -h <broker> -r -t 'homeassistant/sensor/pve/old_metric/config' -m ''
```

## 设计文档

见 `docs/superpowers/specs/2026-10-25-home-temperature-design.md`。
