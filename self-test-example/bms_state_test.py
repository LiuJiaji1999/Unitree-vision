#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import time
from typing import Any, List, Optional


def to_list(value: Any) -> List[Any]:
    if value is None:
        return []
    try:
        return list(value)
    except Exception:
        return [value]


def as_int_or_none(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def as_float_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def safe_get(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def bms_temp_value(raw_value: Any) -> Optional[int]:
    """
    BMSState 里的 bq_ntc / mcu_ntc 在生成的 IDL 里是 uint8，
    但注释里写的是 int8_t。
    所以这里做兼容：
    - 0~150 直接按温度显示
    - 151~255 按补码推测为负温度，例如 246 -> -10
    """
    value = as_int_or_none(raw_value)
    if value is None:
        return None

    if value > 150:
        value -= 256

    return value


def bms_temp_array_text(value: Any, names: List[str]) -> str:
    arr = to_list(value)
    if not arr:
        return "N/A"

    parts = []
    for i, item in enumerate(arr):
        name = names[i] if i < len(names) else f"NTC{i}"
        temp = bms_temp_value(item)
        if temp is None:
            parts.append(f"{name}: raw={item}, temp=N/A")
        else:
            parts.append(f"{name}: raw={int(item)}, temp={temp} ℃")

    return "；".join(parts)


def cell_voltage_values(cell_vol: Any) -> List[Optional[float]]:
    """
    BMS cell_vol 通常是 mV。
    例如 4100 -> 4.100 V。
    """
    result = []

    for item in to_list(cell_vol)[:15]:
        raw = as_int_or_none(item)
        if raw is None or raw <= 0:
            result.append(None)
        else:
            result.append(raw / 1000.0)

    while len(result) < 15:
        result.append(None)

    return result


def cell_voltage_summary(cell_vol: Any) -> str:
    raw_cells = to_list(cell_vol)
    values = cell_voltage_values(cell_vol)

    valid = [
        (i + 1, v)
        for i, v in enumerate(values)
        if v is not None and v > 0
    ]

    if not valid:
        return "没有有效电芯电压，cell_vol 可能全为 0"

    total_v = sum(v for _, v in valid)
    avg_v = total_v / len(valid)

    min_index, min_v = min(valid, key=lambda item: item[1])
    max_index, max_v = max(valid, key=lambda item: item[1])
    diff_mv = (max_v - min_v) * 1000.0

    raw_text = ", ".join(str(as_int_or_none(x)) for x in raw_cells[:15])

    return (
        f"raw cell_vol = [{raw_text}]\n"
        f"有效电芯数 = {len(valid)}\n"
        f"估算总电压 = {total_v:.2f} V\n"
        f"平均单体电压 = {avg_v:.3f} V\n"
        f"最低单体 = 第 {min_index:02d} 节 {min_v:.3f} V\n"
        f"最高单体 = 第 {max_index:02d} 节 {max_v:.3f} V\n"
        f"压差 = {diff_mv:.0f} mV"
    )


def bms_status_text(status: Any) -> str:
    mapping = {
        0: "SAFE（未开启电池）",
        1: "WAKE_UP（唤醒事件）",
        6: "PRECHG（电池预充电中）",
        7: "CHG（电池正常充电中）",
        8: "DCHG（电池正常放电中）",
        9: "SELF_DCHG（电池自放电中）",
        11: "ALARM（电池存在警告）",
        12: "RESET_ALARM（等待按键复位警告中）",
        13: "AUTO_RECOVERY（复位中）",
    }

    code = as_int_or_none(status)
    if code is None:
        return "N/A"

    return f"{code} {mapping.get(code, '未知状态')}"


def import_lowstate_class(idl_type: str):
    if idl_type == "unitree_go":
        from unitree_sdk2py.idl.unitree_go.msg.dds_ import LowState_
        return LowState_

    if idl_type == "unitree_hg":
        from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowState_
        return LowState_

    raise RuntimeError(f"未知 IDL 类型：{idl_type}")


class BmsTester:
    def __init__(self, print_interval: float):
        self.packet_count = 0
        self.last_print_time = 0.0
        self.print_interval = print_interval

    def callback(self, msg: Any) -> None:
        self.packet_count += 1

        now = time.monotonic()
        if now - self.last_print_time < self.print_interval:
            return

        self.last_print_time = now

        bms = safe_get(msg, "bms_state", None)

        print("\n" + "=" * 80)
        print(f"收到 LowState 包数: {self.packet_count}")
        print(f"当前时间戳 tick: {safe_get(msg, 'tick', 'N/A')}")

        imu = safe_get(msg, "imu_state", None)
        motor_state = to_list(safe_get(msg, "motor_state", []))
        motor0 = motor_state[0] if motor_state else None

        print("\n[IMU / Motor 简单校验]")
        print("imu.rpy =", to_list(safe_get(imu, "rpy", [])))
        print("imu.quaternion =", to_list(safe_get(imu, "quaternion", [])))

        if motor0 is not None:
            print("motor[0].q =", safe_get(motor0, "q", "N/A"))
            print("motor[0].dq =", safe_get(motor0, "dq", "N/A"))
            print("motor[0].tau_est =", safe_get(motor0, "tau_est", "N/A"))
        else:
            print("motor_state 为空")


        power_v = as_float_or_none(safe_get(msg, "power_v", None))
        power_a = as_float_or_none(safe_get(msg, "power_a", None))

        print("\n[LowState 顶层电源字段]")
        print(f"power_v = {power_v}")
        print(f"power_a = {power_a}")
        print(f"temperature_ntc1 = {safe_get(msg, 'temperature_ntc1', 'N/A')}")
        print(f"temperature_ntc2 = {safe_get(msg, 'temperature_ntc2', 'N/A')}")

        print("\n[BMSState 原始字段]")
        if bms is None:
            print("没有拿到 msg.bms_state")
            return

        version_high = safe_get(bms, "version_high", None)
        version_low = safe_get(bms, "version_low", None)
        status = safe_get(bms, "status", None)
        soc = safe_get(bms, "soc", None)
        current = safe_get(bms, "current", None)
        cycle = safe_get(bms, "cycle", None)
        bq_ntc = safe_get(bms, "bq_ntc", None)
        mcu_ntc = safe_get(bms, "mcu_ntc", None)
        cell_vol = safe_get(bms, "cell_vol", None)

        print(f"version_high = {version_high}")
        print(f"version_low  = {version_low}")
        print(f"version      = {version_high}.{version_low}")
        print(f"status       = {bms_status_text(status)}")
        print(f"soc          = {soc} %")
        print(f"current raw  = {current}")

        cur_i = as_int_or_none(current)
        if cur_i is not None:
            direction = "充电" if cur_i > 0 else "放电" if cur_i < 0 else "无充放电"
            print(f"current dir  = {direction}")
            print(f"current /1000 = {cur_i / 1000.0:.3f} A  # 如果你的固件单位是 mA，可参考这个值")

        print(f"cycle        = {cycle}")

        print("\n[BMS 温度]")
        print("bq_ntc  =", bms_temp_array_text(bq_ntc, ["BAT1", "BAT2"]))
        print("mcu_ntc =", bms_temp_array_text(mcu_ntc, ["RES", "MOS"]))

        print("\n[BMS 电芯电压]")
        print(cell_voltage_summary(cell_vol))

        # 简单诊断
        soc_i = as_int_or_none(soc)
        raw_cells = [as_int_or_none(x) for x in to_list(cell_vol)]
        valid_cells = [x for x in raw_cells if x is not None and x > 0]

        print("\n[诊断]")
        if soc_i in (None, 0) and not valid_cells:
            print("BMS 字段存在，但 soc/cell_vol 看起来没有有效数据，可能底层未填充 BMS。")
        elif valid_cells:
            print("已读取到有效 BMS 电芯电压，说明 bms_state 获取正常。")
        elif soc_i not in (None, 0):
            print("已读取到有效 SOC，说明 bms_state 至少部分字段正常。")
        else:
            print("BMS 状态不明确，需要继续观察多包数据。")


def main():
    parser = argparse.ArgumentParser(description="Unitree LowState BMSState 测试脚本")

    parser.add_argument(
        "--iface",
        default="enx9c69d3565ef9",
        help="DDS 绑定网卡，例如 enx9c69d3565ef9 / eth0",
    )

    parser.add_argument(
        "--topic",
        default="rt/lowstate",
        help="LowState topic，例如 rt/lowstate、/rt/lowstate、rt/lowState",
    )

    parser.add_argument(
        "--idl",
        default="unitree_go",
        choices=["unitree_go", "unitree_hg"],
        help="LowState IDL 类型",
    )

    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="打印间隔，单位秒",
    )

    args = parser.parse_args()

    from unitree_sdk2py.core.channel import ChannelFactoryInitialize, ChannelSubscriber

    LowState_ = import_lowstate_class(args.idl)

    print("初始化 DDS...")
    print(f"iface = {args.iface}")
    print(f"topic = {args.topic}")
    print(f"idl   = {args.idl}")

    if args.iface:
        ChannelFactoryInitialize(0, args.iface)
    else:
        ChannelFactoryInitialize(0)

    tester = BmsTester(print_interval=args.interval)

    subscriber = ChannelSubscriber(args.topic, LowState_)
    subscriber.Init(tester.callback, 10)

    print("\n订阅已启动，等待 LowState 数据...")
    print("按 Ctrl+C 退出。\n")

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，退出测试。")


if __name__ == "__main__":
    main()

# python bms_state_test.py --iface enx9c69d3565ef9 --topic rt/lowstate --idl unitree_go

# python bms_state_test.py --iface enx9c69d3565ef9 --topic rt/lowstate --idl unitree_hg
# timeout 5 python bms_state_test.py --iface enx9c69d3565ef9 --topic rt/lowstate --idl unitree_go
# timeout 5 python bms_state_test.py --iface enx9c69d3565ef9 --topic /rt/lowstate --idl unitree_hg
# timeout 5 python bms_state_test.py --iface enx9c69d3565ef9 --topic rt/lowState --idl unitree_go
# timeout 5 python bms_state_test.py --iface enx9c69d3565ef9 --topic /rt/lowState --idl unitree_hg
