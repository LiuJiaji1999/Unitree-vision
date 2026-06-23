#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import time
import argparse

SDK_ROOT = "/home/epai/Desktop/unitree_sdk2_python-master"
if SDK_ROOT not in sys.path:
    sys.path.insert(0, SDK_ROOT)

from unitree_sdk2py.core.channel import ChannelFactoryInitialize, ChannelSubscriber

try:
    from unitree_sdk2py.idl.sensor_msgs.msg.dds_ import PointCloud2_
except ImportError:
    from unitree_sdk2py.idl.sensor_msgs.msg.dds_._PointCloud2_ import PointCloud2_

count = 0
last_time = time.time()

def callback(msg):
    global count, last_time
    count += 1

    now = time.time()
    if now - last_time >= 1.0:
        last_time = now

        print("=" * 60)
        print(f"收到点云帧数: {count}")
        print(f"height      : {msg.height}")
        print(f"width       : {msg.width}")
        print(f"point_step  : {msg.point_step}")
        print(f"row_step    : {msg.row_step}")
        print(f"data bytes  : {len(msg.data)}")
        try:
            print(f"fields      : {[f.name for f in msg.fields]}")
        except Exception as e:
            print(f"fields 读取失败: {e}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("interface")
    parser.add_argument("--domain", type=int, default=0)
    parser.add_argument(
        "--topic",
        default="rt/utlidar/cloud_livox_mid360",
        help="默认 rt/utlidar/cloud_livox_mid360",
    )
    args = parser.parse_args()

    print(f"初始化 DDS: interface={args.interface}, domain={args.domain}")
    ChannelFactoryInitialize(args.domain, args.interface)

    print(f"订阅点云 topic: {args.topic}")
    sub = ChannelSubscriber(args.topic, PointCloud2_)
    sub.Init(callback, 10)

    print("等待点云数据，按 Ctrl+C 退出...")
    while True:
        time.sleep(1)
        print(f"cloud count: {count}")

if __name__ == "__main__":
    main()
