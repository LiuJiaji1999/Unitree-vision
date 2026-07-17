#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import time

import cv2
import numpy as np

from unitree_sdk2py.core.channel import ChannelFactoryInitialize
from unitree_sdk2py.go2.video.video_client import VideoClient


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 test_h1_video_debug.py <network_interface>")
        print("Example: python3 test_h1_video_debug.py enx9c69d3565ef9")
        return

    iface = sys.argv[1]

    print(f"[INFO] Init ChannelFactory on interface: {iface}")
    ChannelFactoryInitialize(0, iface)

    client = VideoClient()
    client.SetTimeout(3.0)
    client.Init()

    print("[INFO] VideoClient initialized. Start GetImageSample loop.")

    frame_count = 0
    empty_count = 0
    decode_fail_count = 0
    last_log_time = 0.0

    while True:
        code, data = client.GetImageSample()

        data_len = len(data) if data is not None else 0

        if code != 0:
            print(f"[RPC ERROR] code={code}, data_len={data_len}")
            time.sleep(0.2)
            continue

        if data_len == 0:
            empty_count += 1
            now = time.time()
            if now - last_log_time > 1.0:
                print(
                    f"[EMPTY] RPC OK but image data is empty. "
                    f"code={code}, data_len={data_len}, empty_count={empty_count}"
                )
                last_log_time = now
            time.sleep(1.0 / 15.0)
            continue

        raw = bytes(data)
        header = raw[:16].hex(" ")

        image_data = np.frombuffer(raw, dtype=np.uint8)
        image = cv2.imdecode(image_data, cv2.IMREAD_COLOR)

        if image is None:
            decode_fail_count += 1
            print(
                f"[DECODE FAIL] code={code}, data_len={data_len}, "
                f"header={header}, fail_count={decode_fail_count}"
            )

            # 保存一帧原始数据，后面可以判断到底是不是 JPEG
            with open("bad_unitree_frame.bin", "wb") as f:
                f.write(raw)

            time.sleep(0.1)
            continue

        frame_count += 1

        if frame_count == 1:
            print(f"[OK] First frame received: shape={image.shape}, data_len={data_len}, header={header}")

        cv2.imshow("H1 / Unitree VideoClient", image)

        # ESC 退出
        if cv2.waitKey(1) == 27:
            break

        time.sleep(1.0 / 15.0)

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
