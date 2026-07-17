#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import time

from unitree_sdk2py.core.channel import ChannelFactoryInitialize
from unitree_sdk2py.go2.video.video_client import VideoClient


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 test_h1_videohub_service.py <network_interface>")
        return

    iface = sys.argv[1]

    print(f"[INFO] interface = {iface}")
    ChannelFactoryInitialize(0, iface)

    client = VideoClient()
    client.SetTimeout(3.0)
    client.Init()

    print("[INFO] SDK video client api version:", client.GetApiVersion())

    print("[INFO] Try GetServerApiVersion() ...")
    code, server_version = client.GetServerApiVersion()
    print(f"[RESULT] GetServerApiVersion code={code}, server_version={server_version}")

    print("[INFO] Try GetImageSample() ...")
    for i in range(10):
        code, data = client.GetImageSample()
        data_len = len(data) if data is not None else 0
        print(f"[{i}] GetImageSample code={code}, data_len={data_len}")
        time.sleep(0.2)


if __name__ == "__main__":
    main()
