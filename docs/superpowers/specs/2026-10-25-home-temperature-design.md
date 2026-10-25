2026-10-25-home-temperature-design

# 家庭设备温度采集进 Home Assistant — 设计文档

## 目标

把家中各设备的温度、风扇转速、功耗电压统一采集进 Home Assistant，用于长期监控和告警。

## 环境实测结论

采集前对全部设备做了实际探测，结果如下（2026-10-25 实测）。

### 可采集

| 设备 | 地址 | 采集通道 | 可用指标 |
|---|---|---|---|
| PVE（AZW SER8, AMD 8845HS, PVE 8.2.2） | `<pve-host>` | 本地 `sensors -j` / hwmon | CPU Tctl、NVMe 复合与双颗粒、GPU edge、GPU PPT 功耗、GPU 两路电压 |
| OpenWrt（FriendlyElec NanoPi R76S, 24.10.2） | `<router-host>` | 本地 `/sys/class/thermal` + hwmon | soc/bigcore/little_core/ddr/npu/gpu 六路温度、输入电压、风扇 PWM 占空比 |
| QNAP NAS（TS-X64） | `<nas-host>` | SNMP v3（noAuthNoPriv） | CPU 温度、系统温度、六块硬盘温度、系统风扇 RPM、CPU 与内存利用率 |
| 机柜风扇（ESP32） | `<fan-host>` | 已在 HA（MQTT） | 双风扇 RPM、PWM、转速故障 |

### 不可采集（硬件或固件限制，已实测确认）

| 设备 | 原因 |
|---|---|
| PVE 风扇转速 | SER8 使用 EC 散热，不通过标准接口暴露。`modprobe nct6775` 返回 No such device；ACPI 无 fan 节点；amdgpu 无 fan 属性；`/sys/class/thermal` 仅 16 个 Processor cooling 设备 |
| TP-Link XDR3050 | 家用固件仅提供 Web 登录页，无 API、无 SSH |
| 小米路由器 | LuCI API 返回 405，miIO UDP 54321 无响应，无 SSH |
| 电信光猫 | 仅 80 端口 Web 与 FTP，均需登录，无对外状态接口 |
| 铁威马硬盘盒 | 纯 USB 硬盘盒，无独立监控芯片；其硬盘温度已由 QNAP HDD5/HDD6 覆盖 |

## 架构

```
PVE (<pve-host>)                       OpenWrt (<router-host>)
  └─ ha-metrics.py  [systemd]            └─ ha-metrics.sh  [procd]
     ├─ 本地 hwmon (sensors -j)             ├─ /sys/class/thermal
     └─ SNMP → QNAP <nas-host>              ├─ /sys/class/hwmon
                                            └─ /sys/class/thermal/cooling_device
              │                                        │
              └────────────────┬───────────────────────┘
                               ▼
                  MQTT <broker>:1883（匿名）
                               ▼
                    HA MQTT Discovery 自动发现
```

QNAP 的 SSH 未开放（仅有 SNMP），因此 NAS 的 SNMP 轮询由 PVE 上的采集器承担。其余节点本地采集。

## 数据流

1. 采集器启动后，为每个指标发布一条 retained MQTT Discovery 配置到
   `homeassistant/sensor/{node}/{metric}/config`。
2. 每 60 秒读取一次数据源，把状态发布到 `home/{node}/{metric}`。
3. 同一轮发布 `online` 到 `home/{node}/availability`；进程退出时由 LWT 发布 `offline`。
4. HA 根据 discovery 配置自动创建实体，并把 availability 关联起来。

## 实体清单

### pve 节点

| entity_id | 名称 | 单位 | device_class |
|---|---|---|---|
| sensor.pve_cpu_tctl | PVE CPU Tctl | °C | temperature |
| sensor.pve_nvme_composite | PVE NVMe 复合温度 | °C | temperature |
| sensor.pve_nvme_sensor1 | PVE NVMe 颗粒 1 | °C | temperature |
| sensor.pve_nvme_sensor2 | PVE NVMe 颗粒 2 | °C | temperature |
| sensor.pve_gpu_edge | PVE GPU 温度 | °C | temperature |
| sensor.pve_gpu_ppt | PVE GPU 功耗 | W | power |
| sensor.pve_gpu_vddgfx | PVE GPU 核心电压 | V | voltage |
| sensor.pve_gpu_vddnb | PVE GPU 北桥电压 | V | voltage |

### openwrt 节点

| entity_id | 名称 | 单位 |
|---|---|---|
| sensor.openwrt_soc_temp | SoC 温度 | °C |
| sensor.openwrt_bigcore_temp | 大核温度 | °C |
| sensor.openwrt_little_core_temp | 小核温度 | °C |
| sensor.openwrt_ddr_temp | DDR 温度 | °C |
| sensor.openwrt_npu_temp | NPU 温度 | °C |
| sensor.openwrt_gpu_temp | GPU 温度 | °C |
| sensor.openwrt_input_voltage | 输入电压 | V |
| sensor.openwrt_fan_pwm | 风扇 PWM | % |

### qnap 节点

| entity_id | 名称 | 单位 |
|---|---|---|
| sensor.qnap_cpu_temp | CPU 温度 | °C |
| sensor.qnap_system_temp | 系统温度 | °C |
| sensor.qnap_hdd1_temp … hdd6_temp | 硬盘 1..6 温度 | °C |
| sensor.qnap_fan1_rpm | 系统风扇转速 | RPM |
| sensor.qnap_cpu_usage | CPU 使用率 | % |
| sensor.qnap_memory_usage | 内存使用率 | % |

### 已存在

机柜风扇 `cabinet-fan-c88368` 的双风扇 RPM 已通过 MQTT 接入 HA，不做改动。

## 关键实现细节

### MQTT 主题规范

- 状态：`home/{node}/{metric}`
- 发现：`homeassistant/sensor/{node}/{metric}/config`（retained）
- 可用性：`home/{node}/availability`（retained，LWT 消息为 `offline`）
- 发现配置统一包含 `unique_id`、`state_topic`、`availability_topic`、`device` 块（按 node 聚合为一个 HA 设备）。

### 数据解析要点

QNAP SNMP 返回带单位的字符串，必须清洗：

| OID | 原始值 | 提取规则 |
|---|---|---|
| `1.3.6.1.4.1.24681.1.2.5.0` | `"71 C/159 F"` | 取首个整数 |
| `1.3.6.1.4.1.24681.1.2.6.0` | `"48 C/118 F"` | 取首个整数 |
| `1.3.6.1.4.1.24681.1.2.11.1.3.N` | `"48 C/118 F"` | 取首个整数，N=1..6 |
| `1.3.6.1.4.1.24681.1.2.15.1.3.1` | `"1516 RPM"` | 取首个整数 |
| `1.3.6.1.4.1.24681.1.2.1.0` | `"54.2 %"` | 取浮点 |
| `1.3.6.1.4.1.24681.1.2.2.0` | `"7735.8 MB"` | 取浮点 |

OpenWrt 的 `/sys/class/thermal/thermal_zoneN/temp` 单位为毫摄氏度，需除以 1000。
`pwm1` 取值 0–255，换算百分比为 `pwm1 / 255 * 100`。

PVE 使用 `sensors -j` 输出，按芯片名与属性名定位：
`k10temp-pci-00c3.Tctl.temp1_input`、`nvme-pci-0400.Composite.temp1_input`、
`amdgpu-pci-6500.edge.temp1_input`、`amdgpu-pci-6500.PPT.power1_input` 等。

### 容错

- 单个数据源失败时，向对应实体发布空 payload，HA 显示 `unavailable`，进程不退出。
- SNMP 超时 5 秒、重试 1 次；单条 OID 失败不影响其它 OID。
- 任何未捕获异常记录到 stderr 后继续下一轮。
- 发布失败时保留在下一轮重试，不丢失进程。

### 部署

- PVE：systemd unit，`Restart=always`，`RestartSec=10`。
- OpenWrt：procd init 脚本，`respawn`。
- 两个采集器都支持 `--dry-run`（打印 payload 不发送）与 `--once`（单次采集后退出）。

## 仓库结构

```
home-temperature/
├── README.md
├── pve/
│   ├── ha-metrics.py
│   ├── ha-metrics.service
│   └── install.sh
├── openwrt/
│   ├── ha-metrics.sh
│   ├── ha-metrics.init
│   └── install.sh
└── docs/superpowers/specs/2026-10-25-home-temperature-design.md
```

## 测试策略

1. `--dry-run --once`：确认 payload 结构正确、数值单位合理。
2. 部署后 `mosquitto_sub -t 'home/#' -v` 观察数据流与 60 秒周期。
3. 在 HA 中确认实体出现、数值正确、设备分组正确。
4. 断开数据源（如停掉 SNMP 可达性）验证 `unavailable` 行为，再恢复。

## 明确不做

- PVE 风扇转速：硬件不暴露，需外挂物理传感器，本次不涉及。
- TP-Link / 小米路由器 / 光猫：固件无可用 API，不接入。
- 铁威马硬盘盒单独监控：无监控硬件，温度由 QNAP 覆盖。
- 机柜风扇改造：已接入，保持现状。
