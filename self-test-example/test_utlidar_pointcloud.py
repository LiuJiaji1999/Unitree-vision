#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import os
import re
import struct
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional


# ROS sensor_msgs/PointField datatype constants
POINTFIELD_STRUCT = {
    1: ("b", 1),  # INT8
    2: ("B", 1),  # UINT8
    3: ("h", 2),  # INT16
    4: ("H", 2),  # UINT16
    5: ("i", 4),  # INT32
    6: ("I", 4),  # UINT32
    7: ("f", 4),  # FLOAT32
    8: ("d", 8),  # FLOAT64
}


def run_cmd(cmd: List[str]) -> str:
    try:
        return subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True).strip()
    except Exception:
        return ""


def ping(ip: str) -> bool:
    out = run_cmd(["ping", "-c", "1", "-W", "1", ip])
    return "1 received" in out or "1 packets received" in out or "0% packet loss" in out


def auto_iface_by_ip(local_ip: str) -> Optional[str]:
    out = run_cmd(["ip", "-o", "-4", "addr", "show"])
    for line in out.splitlines():
        # example: 2: enp3s0    inet 192.168.123.51/24 ...
        if local_ip in line:
            m = re.match(r"\d+:\s+([^ ]+)\s+inet\s+", line)
            if m:
                return m.group(1)
    return None


def set_cyclonedds_iface(iface: str) -> None:
    # 绑定 CycloneDDS 到指定网卡，避免多网卡时 DDS 走错接口
    os.environ["CYCLONEDDS_URI"] = (
        "<CycloneDDS>"
        "<Domain>"
        "<General>"
        "<Interfaces>"
        f'<NetworkInterface name="{iface}" priority="default" multicast="default" />'
        "</Interfaces>"
        "</General>"
        "</Domain>"
        "</CycloneDDS>"
    )


def norm_name(x: Any) -> str:
    if isinstance(x, str):
        return x
    if isinstance(x, bytes):
        return x.decode(errors="ignore").rstrip("\x00")
    if isinstance(x, (list, tuple)):
        try:
            return bytes(x).decode(errors="ignore").rstrip("\x00")
        except Exception:
            return str(x)
    return str(x)


def get_header_info(msg: Any) -> Dict[str, Any]:
    info = {"frame_id": None, "stamp": None}
    header = getattr(msg, "header", None)
    if header is None:
        return info

    frame_id = getattr(header, "frame_id", None)
    info["frame_id"] = norm_name(frame_id) if frame_id is not None else None

    stamp = getattr(header, "stamp", None)
    if stamp is not None:
        sec = getattr(stamp, "sec", None)
        nanosec = getattr(stamp, "nanosec", None)
        if sec is not None and nanosec is not None:
            info["stamp"] = f"{sec}.{nanosec:09d}"

    return info


def field_table(msg: Any) -> List[Dict[str, Any]]:
    rows = []
    for f in getattr(msg, "fields", []):
        rows.append({
            "name": norm_name(getattr(f, "name", "")),
            "offset": getattr(f, "offset", None),
            "datatype": getattr(f, "datatype", None),
            "count": getattr(f, "count", None),
        })
    return rows


def safe_bytes_slice(data: Any, n: int) -> bytes:
    try:
        return bytes(data[:n])
    except Exception:
        try:
            return bytes(list(data[:n]))
        except Exception:
            return b""


def extract_preview_points(msg: Any, max_points: int = 5) -> List[Dict[str, Any]]:
    point_step = int(getattr(msg, "point_step", 0) or 0)
    width = int(getattr(msg, "width", 0) or 0)
    height = int(getattr(msg, "height", 0) or 0)

    if point_step <= 0 or width * height <= 0:
        return []

    fields = {row["name"]: row for row in field_table(msg)}
    wanted = [name for name in ["x", "y", "z", "intensity", "ring", "time"] if name in fields]

    if not all(name in fields for name in ["x", "y", "z"]):
        return []

    n_points = min(max_points, width * height)
    raw = safe_bytes_slice(getattr(msg, "data", []), point_step * n_points)
    if len(raw) < point_step:
        return []

    endian = ">" if bool(getattr(msg, "is_bigendian", False)) else "<"

    points = []
    for i in range(n_points):
        base = i * point_step
        p = {}
        for name in wanted:
            row = fields[name]
            datatype = int(row["datatype"])
            offset = int(row["offset"])
            if datatype not in POINTFIELD_STRUCT:
                continue

            fmt, size = POINTFIELD_STRUCT[datatype]
            pos = base + offset
            if pos + size > len(raw):
                continue

            try:
                val = struct.unpack_from(endian + fmt, raw, pos)[0]
                p[name] = val
            except Exception:
                pass
        if p:
            points.append(p)

    return points


def print_msg_summary(topic: str, msg: Any, index: int) -> None:
    width = int(getattr(msg, "width", 0) or 0)
    height = int(getattr(msg, "height", 0) or 0)
    point_step = int(getattr(msg, "point_step", 0) or 0)
    row_step = int(getattr(msg, "row_step", 0) or 0)
    data = getattr(msg, "data", [])
    data_len = len(data) if hasattr(data, "__len__") else -1

    header = get_header_info(msg)
    fields = field_table(msg)
    preview = extract_preview_points(msg, max_points=5)

    print("\n" + "=" * 80)
    print(f"[OK] 收到第 {index} 帧点云")
    print(f"topic       : {topic}")
    print(f"frame_id    : {header.get('frame_id')}")
    print(f"stamp       : {header.get('stamp')}")
    print(f"width/height: {width} x {height}")
    print(f"points      : {width * height}")
    print(f"point_step  : {point_step}")
    print(f"row_step    : {row_step}")
    print(f"data_len    : {data_len}")
    print(f"is_dense    : {getattr(msg, 'is_dense', None)}")
    print(f"is_bigendian: {getattr(msg, 'is_bigendian', None)}")
    print("fields      :")
    for f in fields:
        print(f"  - {f}")

    if preview:
        print("preview points:")
        for p in preview:
            print(f"  {p}")
    else:
        print("preview points: 未能解析 x/y/z，请检查 fields 中是否包含 x/y/z")


def main() -> int:
    parser = argparse.ArgumentParser(description="Test Unitree LiDAR PointCloud2 DDS subscription")
    parser.add_argument("--local-ip", default="192.168.123.51", help="上位机在机器人网段上的 IP")
    parser.add_argument("--h1-ip", default="192.168.123.162", help="H1 PC2 IP")
    parser.add_argument("--lidar-ip", default="192.168.124.162", help="雷达 IP")
    parser.add_argument("--iface", default=None, help="连接 H1 的网卡名，例如 enp3s0/eth0；不填则按 --local-ip 自动查找")
    parser.add_argument("--domain", type=int, default=0, help="DDS domain id，Unitree 通常是 0")
    parser.add_argument("--timeout", type=float, default=20.0, help="等待点云超时时间，秒")
    parser.add_argument("--num", type=int, default=3, help="收到多少帧后退出")
    parser.add_argument(
        "--topics",
        default="rt/utlidar/cloud,utlidar/cloud,/utlidar/cloud",
        help="逗号分隔的候选 topic；SDK 原始 DDS 通常用 rt/utlidar/cloud",
    )
    args = parser.parse_args()

    print("[INFO] 网络连通性检查：")
    print(f"  ping H1 PC2  {args.h1_ip}: {'OK' if ping(args.h1_ip) else 'FAIL'}")
    print(f"  ping LiDAR   {args.lidar_ip}: {'OK' if ping(args.lidar_ip) else 'FAIL'}")

    iface = args.iface or auto_iface_by_ip(args.local_ip)
    if not iface:
        print(f"[ERROR] 没找到 IP {args.local_ip} 对应的网卡。")
        print("请手动指定，例如：python3 test_utlidar_pointcloud.py --iface enp3s0")
        print("可用命令查看：ip -o -4 addr show")
        return 2

    print(f"[INFO] 使用网卡: {iface}")
    set_cyclonedds_iface(iface)

    try:
        from unitree_sdk2py.core.channel import ChannelFactoryInitialize, ChannelSubscriber
        from unitree_sdk2py.idl.sensor_msgs.msg.dds_ import PointCloud2_
    except Exception as e:
        print("[ERROR] 导入 unitree_sdk2py 失败：", repr(e))
        print("请确认已安装/配置 unitree_sdk2py，并且当前 Python 环境能 import。")
        return 3

    try:
        # Unitree SDK2 Python 通常支持：ChannelFactoryInitialize(domain_id, iface)
        ChannelFactoryInitialize(args.domain, iface)
    except TypeError:
        # 兼容某些版本只接收 domain 参数的情况
        ChannelFactoryInitialize(args.domain)

    topics = [t.strip() for t in args.topics.split(",") if t.strip()]
    done = threading.Event()
    count = {"n": 0}
    subscribers = []

    def make_handler(topic_name: str):
        def handler(msg: Any):
            count["n"] += 1
            print_msg_summary(topic_name, msg, count["n"])
            if count["n"] >= args.num:
                done.set()
        return handler

    print("[INFO] 开始订阅候选 topic：")
    for topic in topics:
        try:
            sub = ChannelSubscriber(topic, PointCloud2_)
            sub.Init(make_handler(topic), 10)
            subscribers.append(sub)
            print(f"  - {topic}")
        except Exception as e:
            print(f"  - {topic} 初始化失败: {repr(e)}")

    if not subscribers:
        print("[ERROR] 没有任何 topic 订阅成功。")
        return 4

    print(f"[INFO] 等待点云，timeout={args.timeout}s，收到 {args.num} 帧后退出...")
    t0 = time.time()
    while time.time() - t0 < args.timeout and not done.is_set():
        time.sleep(0.1)

    if count["n"] > 0:
        print(f"\n[SUCCESS] 已收到 {count['n']} 帧 PointCloud2 点云。")
        return 0

    print("\n[FAIL] 超时，未收到 PointCloud2 点云。")
    print("排查建议：")
    print("1. 确认网卡是否正确：ip -o -4 addr show | grep 192.168.123.51")
    print("2. 确认 DDS 走的是机器人网卡，而不是 Wi-Fi/lo。")
    print("3. 用 ROS2 交叉验证：ros2 topic list | grep utlidar")
    print("4. 若 ROS2 能看到 /utlidar/cloud，但本脚本收不到，把 --topics 改成实际 DDS topic。")
    print("5. ping 通只说明 IP 可达，不代表 DDS multicast/discovery 一定正常。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
