#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LowState_ 顶层字段有效性测试脚本。

用途：
    单独订阅 rt/lowstate，连续采样若干包 LowState_，
    统计每个顶层字段是否存在、是否一直为 0、是否出现过有效值。

运行：
    python lowstate_field_probe.py --interface enx9c69d3565ef9 --samples 200

输出：
    lowstate_field_probe_report.txt
    lowstate_field_probe_report.csv
"""

import argparse
import csv
import os
import signal
import sys
import time
from typing import Any, Dict, List


LOWSTATE_IDL_FIELDS = [
    "head",
    "level_flag",
    "frame_reserve",
    "sn",
    "version",
    "bandwidth",
    "imu_state",
    "motor_state",
    "bms_state",
    "foot_force",
    "foot_force_est",
    "tick",
    "wireless_remote",
    "bit_flag",
    "adc_reel",
    "temperature_ntc1",
    "temperature_ntc2",
    "power_v",
    "power_a",
    "fan_frequency",
    "reserve",
    "crc",
]


FIELD_DESC = {
    "head": "帧头，数据校验用，通常为 [0xFE, 0xEF]",
    "level_flag": "沿用字段，目前可能不用",
    "frame_reserve": "沿用字段，目前可能不用",
    "sn": "序列号，当前版本可能改为文件存储，可能不用",
    "version": "版本号，沿用字段，目前可能不用",
    "bandwidth": "带宽，沿用字段，目前可能不用",
    "imu_state": "IMU 数据信息",
    "motor_state": "电机总数据，数组长度通常为 20",
    "bms_state": "电池总数据",
    "foot_force": "足端力，数组：0-FR，1-FL，2-RR，3-RL",
    "foot_force_est": "估算足端力",
    "tick": "1ms 计时，按 1ms 递增",
    "wireless_remote": "遥控器原始数据",
    "bit_flag": "组件状态标志",
    "adc_reel": "卷线器电流，范围 0-3A",
    "temperature_ntc1": "主板中心温度",
    "temperature_ntc2": "自动充电温度",
    "power_v": "电池电压",
    "power_a": "电机电流",
    "fan_frequency": "风扇转速",
    "reserve": "保留位",
    "crc": "CRC 校验位",
}


def safe_get(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name)
    except Exception:
        return default


def short_value(value: Any, max_len: int = 180) -> str:
    if value is None:
        return "None"

    try:
        if not isinstance(value, (str, bytes, bytearray)) and not hasattr(value, "__dict__"):
            arr = list(value)
            text = repr(arr)
            return text if len(text) <= max_len else text[:max_len] + " ..."
    except Exception:
        pass

    if hasattr(value, "__dict__"):
        public_keys = [k for k in value.__dict__.keys() if not k.startswith("_")]
        if public_keys:
            text = f"{type(value).__name__}({', '.join(public_keys[:8])}"
            if len(public_keys) > 8:
                text += ", ..."
            text += ")"
            return text
        return type(value).__name__

    text = repr(value)
    return text if len(text) <= max_len else text[:max_len] + " ..."


def is_numeric_zero(value: Any) -> bool:
    if isinstance(value, bool):
        return False

    if isinstance(value, (int, float)):
        try:
            return float(value) == 0.0
        except Exception:
            return False

    return False


def value_has_effective_data(value: Any) -> bool:
    if value is None:
        return False

    if isinstance(value, bool):
        return bool(value)

    if isinstance(value, (int, float)):
        return not is_numeric_zero(value)

    if isinstance(value, str):
        return value.strip() != ""

    try:
        if not hasattr(value, "__dict__"):
            arr = list(value)
            if not arr:
                return False
            return any(value_has_effective_data(item) for item in arr)
    except Exception:
        pass

    if hasattr(value, "__dict__"):
        for key, item in value.__dict__.items():
            if key.startswith("_"):
                continue
            if value_has_effective_data(item):
                return True
        return False

    return True


def value_is_zero_or_empty(value: Any) -> bool:
    if value is None:
        return True

    if isinstance(value, bool):
        return not value

    if isinstance(value, (int, float)):
        return is_numeric_zero(value)

    if isinstance(value, str):
        return value.strip() == ""

    try:
        if not hasattr(value, "__dict__"):
            arr = list(value)
            if not arr:
                return True
            return all(value_is_zero_or_empty(item) for item in arr)
    except Exception:
        pass

    if hasattr(value, "__dict__"):
        public_items = [
            item for key, item in value.__dict__.items()
            if not key.startswith("_")
        ]
        if not public_items:
            return True
        return all(value_is_zero_or_empty(item) for item in public_items)

    return False


class LowStateFieldProbe:
    def __init__(self, sample_limit: int, report_prefix: str):
        self.sample_limit = sample_limit
        self.report_prefix = report_prefix
        self.packet_count = 0
        self.start_time = time.time()
        self.finished = False

        self.stats: Dict[str, Dict[str, Any]] = {}

        for name in LOWSTATE_IDL_FIELDS:
            self.stats[name] = {
                "exist_count": 0,
                "effective_count": 0,
                "zero_or_empty_count": 0,
                "missing_count": 0,
                "examples": [],
                "first_value": "",
                "last_value": "",
            }

    def feed(self, msg: Any) -> None:
        if self.finished:
            return

        self.packet_count += 1

        for name in LOWSTATE_IDL_FIELDS:
            value = safe_get(msg, name, None)
            stat = self.stats[name]

            if value is None:
                stat["missing_count"] += 1
                continue

            stat["exist_count"] += 1

            value_text = short_value(value)
            if not stat["first_value"]:
                stat["first_value"] = value_text
            stat["last_value"] = value_text

            if value_has_effective_data(value):
                stat["effective_count"] += 1
                if len(stat["examples"]) < 3:
                    stat["examples"].append(value_text)

            if value_is_zero_or_empty(value):
                stat["zero_or_empty_count"] += 1

        if self.packet_count >= self.sample_limit:
            self.finished = True

    def result_status(self, name: str) -> str:
        stat = self.stats[name]

        if stat["exist_count"] == 0:
            return "字段不存在，暂不展示"

        if stat["effective_count"] > 0:
            return "出现过有效值，建议前端展示"

        return "字段存在，但采样期间一直为 0 / 空，暂不建议展示"

    def print_progress(self) -> None:
        elapsed = max(time.time() - self.start_time, 0.001)
        hz = self.packet_count / elapsed
        print(
            f"\r采样中: {self.packet_count}/{self.sample_limit} 包, 约 {hz:.1f} Hz",
            end="",
            flush=True,
        )

    def build_text_report(self) -> str:
        elapsed = max(time.time() - self.start_time, 0.001)

        lines = []
        lines.append("LowState_ 顶层字段探测报告")
        lines.append("=" * 90)
        lines.append(f"采样包数: {self.packet_count}")
        lines.append(f"采样耗时: {elapsed:.2f} 秒")
        lines.append(f"平均频率: {self.packet_count / elapsed:.2f} Hz")
        lines.append("")
        lines.append("说明：")
        lines.append("  有效值：数字非 0、数组存在非 0 元素、结构体内部存在有效字段。")
        lines.append("  一直为 0 / 空：不一定绝对表示未开放，但可作为前端是否展示的参考。")
        lines.append("=" * 90)
        lines.append("")

        recommended = []
        not_recommended = []

        for name in LOWSTATE_IDL_FIELDS:
            status = self.result_status(name)
            if "建议前端展示" in status:
                recommended.append(name)
            else:
                not_recommended.append(name)

            stat = self.stats[name]

            lines.append(f"字段名: {name}")
            lines.append(f"中文说明: {FIELD_DESC.get(name, '')}")
            lines.append(f"存在次数: {stat['exist_count']}")
            lines.append(f"有效次数: {stat['effective_count']}")
            lines.append(f"零值/空值次数: {stat['zero_or_empty_count']}")
            lines.append(f"缺失次数: {stat['missing_count']}")
            lines.append(f"判断结果: {status}")

            if stat["first_value"]:
                lines.append(f"首个值: {stat['first_value']}")

            if stat["last_value"]:
                lines.append(f"最后值: {stat['last_value']}")

            if stat["examples"]:
                lines.append("有效示例:")
                for example in stat["examples"]:
                    lines.append(f"  - {example}")

            lines.append("-" * 90)

        lines.append("")
        lines.append("建议展示字段 Python 列表：")
        lines.append("LOWSTATE_DISPLAY_FIELDS = [")
        for name in recommended:
            lines.append(f'    "{name}",')
        lines.append("]")
        lines.append("")
        lines.append("暂不建议展示字段：")
        lines.append(", ".join(not_recommended) if not_recommended else "无")

        return "\n".join(lines)

    def save_reports(self) -> None:
        txt_path = f"{self.report_prefix}.txt"
        csv_path = f"{self.report_prefix}.csv"

        text = self.build_text_report()

        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(text)

        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow([
                "field",
                "description",
                "exist_count",
                "effective_count",
                "zero_or_empty_count",
                "missing_count",
                "status",
                "first_value",
                "last_value",
                "examples",
            ])

            for name in LOWSTATE_IDL_FIELDS:
                stat = self.stats[name]
                writer.writerow([
                    name,
                    FIELD_DESC.get(name, ""),
                    stat["exist_count"],
                    stat["effective_count"],
                    stat["zero_or_empty_count"],
                    stat["missing_count"],
                    self.result_status(name),
                    stat["first_value"],
                    stat["last_value"],
                    " | ".join(stat["examples"]),
                ])

        print("")
        print(text)
        print("")
        print("报告已保存:")
        print(f"  TXT: {os.path.abspath(txt_path)}")
        print(f"  CSV: {os.path.abspath(csv_path)}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="单独测试 Unitree LowState_ 顶层字段是否有有效数据"
    )

    parser.add_argument(
        "--interface",
        required=True,
        help="DDS 网卡名，例如 enx9c69d3565ef9 / eth0",
    )

    parser.add_argument(
        "--topic",
        default="rt/lowstate",
        help="LowState 话题名，默认 rt/lowstate",
    )

    parser.add_argument(
        "--samples",
        type=int,
        default=200,
        help="采样包数，默认 200",
    )

    parser.add_argument(
        "--report-prefix",
        default="lowstate_field_probe_report",
        help="报告文件名前缀，默认 lowstate_field_probe_report",
    )

    parser.add_argument(
        "--domain",
        type=int,
        default=0,
        help="DDS domain id，默认 0",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        from unitree_sdk2py.core.channel import ChannelFactoryInitialize
        from unitree_sdk2py.core.channel import ChannelSubscriber
        from unitree_sdk2py.idl.unitree_go.msg.dds_ import LowState_
    except Exception as exc:
        print("导入 Unitree SDK2 Python 依赖失败。")
        print("请确认你在 unitree_sdk2_python-master 环境中运行，并且已激活对应 Python 环境。")
        print(f"错误信息: {exc}")
        return 1

    probe = LowStateFieldProbe(
        sample_limit=args.samples,
        report_prefix=args.report_prefix,
    )

    should_stop = {"value": False}

    def handle_signal(signum, frame):
        should_stop["value"] = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    def lowstate_callback(msg: Any) -> None:
        probe.feed(msg)
        probe.print_progress()

    print("正在初始化 DDS ...")
    print(f"  interface: {args.interface}")
    print(f"  domain: {args.domain}")
    print(f"  topic: {args.topic}")
    print(f"  samples: {args.samples}")

    ChannelFactoryInitialize(args.domain, args.interface)

    subscriber = ChannelSubscriber(args.topic, LowState_)
    subscriber.Init(lowstate_callback, 10)

    print("开始采样 LowState_，按 Ctrl+C 可提前结束。")

    while not probe.finished and not should_stop["value"]:
        time.sleep(0.05)

    if probe.packet_count == 0:
        print("")
        print("没有收到任何 LowState_ 数据。")
        print("请检查：")
        print("  1. 机器人是否开机并发布 rt/lowstate")
        print("  2. --interface 是否是正确网卡")
        print("  3. 本机是否能通过 SDK2 DDS 收到数据")
        return 2

    probe.finished = True
    probe.save_reports()

    return 0


if __name__ == "__main__":
    sys.exit(main())
