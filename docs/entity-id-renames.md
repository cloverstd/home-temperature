# HA 实体 ID 改名对照表

在 HA 网页里逐个操作：**设置 → 设备与服务 → 实体**，搜索旧 ID，点齿轮图标，修改「实体 ID」。

或者按 `Ctrl+K`（命令面板）输入 `entity` 快速跳转到实体列表。

## PVE (SER8) — 8 个

| 当前 entity_id | 改成 |
|---|---|
| `sensor.pve_ser8_cpu_tctl` | `sensor.pve_cpu_tctl` |
| `sensor.pve_ser8_nvme_fu_he_wen_du` | `sensor.pve_nvme_composite` |
| `sensor.pve_ser8_nvme_ke_li_1` | `sensor.pve_nvme_sensor1` |
| `sensor.pve_ser8_nvme_ke_li_2` | `sensor.pve_nvme_sensor2` |
| `sensor.pve_ser8_gpu_wen_du` | `sensor.pve_gpu_edge` |
| `sensor.pve_ser8_gpu_gong_hao` | `sensor.pve_gpu_ppt` |
| `sensor.pve_ser8_gpu_he_xin_dian_ya` | `sensor.pve_gpu_vddgfx` |
| `sensor.pve_ser8_gpu_bei_qiao_dian_ya` | `sensor.pve_gpu_vddnb` |

## QNAP (TS-X64) — 11 个

| 当前 entity_id | 改成 |
|---|---|
| `sensor.qnap_nas_ts_x64_cpu_wen_du` | `sensor.qnap_cpu_temp` |
| `sensor.qnap_nas_ts_x64_xi_tong_wen_du` | `sensor.qnap_system_temp` |
| `sensor.qnap_nas_ts_x64_ying_pan_1_wen_du` | `sensor.qnap_hdd1_temp` |
| `sensor.qnap_nas_ts_x64_ying_pan_2_wen_du` | `sensor.qnap_hdd2_temp` |
| `sensor.qnap_nas_ts_x64_ying_pan_3_wen_du` | `sensor.qnap_hdd3_temp` |
| `sensor.qnap_nas_ts_x64_ying_pan_4_wen_du` | `sensor.qnap_hdd4_temp` |
| `sensor.qnap_nas_ts_x64_ying_pan_5_wen_du` | `sensor.qnap_hdd5_temp` |
| `sensor.qnap_nas_ts_x64_ying_pan_6_wen_du` | `sensor.qnap_hdd6_temp` |
| `sensor.qnap_nas_ts_x64_xi_tong_feng_shan_zhuan_su` | `sensor.qnap_fan1_rpm` |
| `sensor.qnap_nas_ts_x64_cpu_shi_yong_lu` | `sensor.qnap_cpu_usage` |
| `sensor.qnap_nas_ts_x64_nei_cun_shi_yong_lu` | `sensor.qnap_memory_usage` |

## OpenWrt (NanoPi R76S) — 8 个

| 当前 entity_id | 改成 |
|---|---|
| `sensor.openwrt_nanopi_r76s_soc_wen_du` | `sensor.openwrt_soc_temp` |
| `sensor.openwrt_nanopi_r76s_da_he_wen_du` | `sensor.openwrt_bigcore_temp` |
| `sensor.openwrt_nanopi_r76s_xiao_he_wen_du` | `sensor.openwrt_little_core_temp` |
| `sensor.openwrt_nanopi_r76s_ddr_wen_du` | `sensor.openwrt_ddr_temp` |
| `sensor.openwrt_nanopi_r76s_npu_wen_du` | `sensor.openwrt_npu_temp` |
| `sensor.openwrt_nanopi_r76s_gpu_wen_du` | `sensor.openwrt_gpu_temp` |
| `sensor.openwrt_nanopi_r76s_shu_ru_dian_ya` | `sensor.openwrt_input_voltage` |
| `sensor.openwrt_nanopi_r76s_feng_shan_pwm` | `sensor.openwrt_fan_pwm` |

## 说明

新实体在发现配置里已经带了 `object_id`，所以从今以后新建的实体不会再有这个问题。
只有这 27 个已经创建过的需要手动改。改名不影响历史数据（statistics 按 entity_id 关联，
改名后需要在新 ID 下重新积累，或者用 `homeassistant.update_entity`/statistics 重命名工具迁移）。
