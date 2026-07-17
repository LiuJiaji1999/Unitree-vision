#!/usr/bin/env python3
import sys
sys.path.insert(0, "/usr/lib/python3/dist-packages/pyrealsense2")
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np
import pyrealsense2 as rs

HOST = "0.0.0.0"
PORT = 8080

lock = threading.Lock()
latest = {
    "color": None,
    "depth": None,
    "combined": None,
    "ok": False,
    "err": "",
}

running = True

def start_pipeline_with_fallbacks(pipeline):
    """
    按优先级尝试多组 RealSense 配置。
    D435i 上 depth 640x480 不一定在当前 USB/profile 下可用，
    所以不要固定写死 640x480@30。
    """

    candidates = [
        # color_w, color_h, color_fps, depth_w, depth_h, depth_fps
        (640, 480, 30, 640, 480, 30),
        (640, 480, 15, 640, 480, 15),

        # D435/D435i 常见深度分辨率，848x480 通常很稳
        (640, 480, 30, 848, 480, 30),
        (640, 480, 15, 848, 480, 15),

        # 降低带宽
        (640, 480, 30, 480, 270, 30),
        (640, 480, 15, 480, 270, 15),

        (640, 480, 30, 424, 240, 30),
        (640, 480, 15, 424, 240, 15),

        # 最低兜底
        (640, 480, 15, 320, 240, 15),
        (640, 480, 6, 320, 240, 6),
    ]

    last_error = None

    for color_w, color_h, color_fps, depth_w, depth_h, depth_fps in candidates:
        config = rs.config()

        try:
            config.enable_stream(
                rs.stream.color,
                color_w,
                color_h,
                rs.format.bgr8,
                color_fps,
            )

            config.enable_stream(
                rs.stream.depth,
                depth_w,
                depth_h,
                rs.format.z16,
                depth_fps,
            )

            profile = pipeline.start(config)

            print(
                "RealSense pipeline started with "
                f"color={color_w}x{color_h}@{color_fps}, "
                f"depth={depth_w}x{depth_h}@{depth_fps}",
                flush=True,
            )

            return profile

        except Exception as e:
            last_error = e

            print(
                "RealSense config failed: "
                f"color={color_w}x{color_h}@{color_fps}, "
                f"depth={depth_w}x{depth_h}@{depth_fps}, "
                f"err={repr(e)}",
                file=sys.stderr,
                flush=True,
            )

            try:
                pipeline.stop()
            except Exception:
                pass

            time.sleep(0.2)

    raise RuntimeError(f"All RealSense stream configs failed. Last error: {repr(last_error)}")

def capture_loop():
    global latest

    while running:
        pipeline = rs.pipeline()
        align = rs.align(rs.stream.color)
        colorizer = rs.colorizer()

        started = False
        timeout_count = 0

        try:
            start_pipeline_with_fallbacks(pipeline)
            started = True
            timeout_count = 0

            with lock:
                latest["ok"] = True
                latest["err"] = ""

            while running:
                try:
                    frames = pipeline.wait_for_frames(1000)

                except RuntimeError as e:
                    timeout_count += 1
                    err = repr(e)

                    print(
                        f"RealSense frame timeout count={timeout_count}: {err}",
                        file=sys.stderr,
                        flush=True,
                    )

                    with lock:
                        latest["ok"] = False
                        latest["err"] = f"frame timeout count={timeout_count}: {err}"

                    if timeout_count < 10:
                        continue

                    print(
                        "Too many RealSense frame timeouts, restarting pipeline...",
                        file=sys.stderr,
                        flush=True,
                    )
                    break

                timeout_count = 0

                frames = align.process(frames)

                color_frame = frames.get_color_frame()
                depth_frame = frames.get_depth_frame()

                if not color_frame or not depth_frame:
                    continue

                color_img = np.asanyarray(color_frame.get_data())
                depth_img = np.asanyarray(colorizer.colorize(depth_frame).get_data())

                # 对齐后一般尺寸一致；这里再保险处理一下
                if depth_img.shape[:2] != color_img.shape[:2]:
                    depth_img = cv2.resize(
                        depth_img,
                        (color_img.shape[1], color_img.shape[0]),
                        interpolation=cv2.INTER_NEAREST,
                    )

                combined = np.hstack((color_img, depth_img))

                with lock:
                    latest["color"] = color_img.copy()
                    latest["depth"] = depth_img.copy()
                    latest["combined"] = combined.copy()
                    latest["ok"] = True
                    latest["err"] = ""

        except Exception as e:
            err = repr(e)

            print("RealSense pipeline error:", err, file=sys.stderr, flush=True)

            with lock:
                latest["ok"] = False
                latest["err"] = err

        finally:
            if started:
                try:
                    pipeline.stop()
                except Exception as e:
                    print(
                        "RealSense pipeline stop error:",
                        repr(e),
                        file=sys.stderr,
                        flush=True,
                    )

        time.sleep(1)


def encode_jpeg(img):
    ok, buffer = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    if not ok:
        return None
    return buffer.tobytes()


def get_frame(kind):
    with lock:
        img = latest.get(kind)
        err = latest.get("err", "")

    if img is None:
        blank = np.zeros((480, 640, 3), dtype=np.uint8)
        text = "Waiting for RealSense..."
        if err:
            text = "Error: " + err[:80]
        cv2.putText(
            blank,
            text,
            (20, 240),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (255, 255, 255),
            2,
        )
        img = blank

    return encode_jpeg(img)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.address_string(), fmt % args), flush=True)

    def send_html(self, html):
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            self.send_html("""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>RealSense D435i Stream</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 20px; background: #111; color: #eee; }
    img { max-width: 100%; border: 1px solid #555; }
    a { color: #8ab4f8; margin-right: 16px; }
  </style>
</head>
<body>
  <h2>RealSense D435i Stream</h2>
  <p>
    <a href="/">combined</a>
    <a href="/color">color</a>
    <a href="/depth">depth</a>
    <a href="/status">status</a>
  </p>
  <img src="/stream/combined">
</body>
</html>
""")
            return

        if self.path == "/color":
            self.send_html('<html><body style="background:#111;color:#eee"><h2>Color</h2><img src="/stream/color"></body></html>')
            return

        if self.path == "/depth":
            self.send_html('<html><body style="background:#111;color:#eee"><h2>Depth</h2><img src="/stream/depth"></body></html>')
            return

        if self.path == "/status":
            with lock:
                data = (
                    "{"
                    f'"ok": {str(latest["ok"]).lower()}, '
                    f'"err": "{latest["err"]}", '
                    f'"has_color": {str(latest["color"] is not None).lower()}, '
                    f'"has_depth": {str(latest["depth"] is not None).lower()}, '
                    f'"has_combined": {str(latest["combined"] is not None).lower()}'
                    "}"
                ).encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if self.path.startswith("/stream/"):
            kind = self.path.split("/")[-1]
            if kind not in ("color", "depth", "combined"):
                self.send_error(404)
                return

            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            while True:
                jpg = get_frame(kind)
                if jpg is not None:
                    try:
                        self.wfile.write(b"--frame\r\n")
                        self.wfile.write(b"Content-Type: image/jpeg\r\n\r\n")
                        self.wfile.write(jpg)
                        self.wfile.write(b"\r\n")
                    except BrokenPipeError:
                        break
                    except ConnectionResetError:
                        break

                time.sleep(0.03)
            return

        self.send_error(404)


if __name__ == "__main__":
    t = threading.Thread(target=capture_loop, daemon=True)
    t.start()

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"HTTP server started on http://{HOST}:{PORT}/", flush=True)
    server.serve_forever()
