#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
H1 Robot Control Panel - PyQt5

功能：
1. 美化登录界面
2. 主控制界面
3. H1 机器人连接配置
4. SDK2 LowState 实时状态读取：
   - IMU
   - 电池电压/电流/SOC
   - 电机状态
5. 深度相机/视频流显示：
   - USB 摄像头，例如 0
   - MJPEG/HTTP 流
   - RTSP 流
   - 本机 RealSense 深度相机，输入 realsense
6. 参数读取/写入接口预留
7. 指令接口预留：急停、站立、坐下
8. 日志输出

运行：
    pip install PyQt5 opencv-python
    python3 h1_pyqt_login_app.py

如果要用本机 RealSense 深度相机：
    pip install pyrealsense2

演示账号：
    admin / admin123
    operator / operator123

安全说明：
    本程序的 SDK2 部分默认只订阅 LowState，不发布 LowCmd，不控制电机。
"""

import hashlib
import json
import math
import random
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List

from PyQt5.QtCore import QObject, QTimer, pyqtSignal, Qt, QThread
from PyQt5.QtGui import QFont, QImage, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStatusBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


APP_DIR = Path(__file__).resolve().parent
USERS_FILE = APP_DIR / "users.json"
CONFIG_FILE = APP_DIR / "h1_config.json"


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def safe_get(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def safe_path(obj: Any, path: List[str], default: Any = None) -> Any:
    cur = obj
    for name in path:
        cur = safe_get(cur, name, None)
        if cur is None:
            return default
    return cur


def first_valid_value(obj: Any, paths: List[List[str]]) -> Any:
    for path in paths:
        value = safe_path(obj, path, None)
        if value is not None:
            return value
    return None


def to_list(value: Any) -> List[Any]:
    if value is None:
        return []
    try:
        return list(value)
    except Exception:
        return [value]


def fmt_float(value: Any, digits: int = 3) -> str:
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return str(value)


def vector_text(value: Any, digits: int = 3) -> str:
    arr = to_list(value)
    if not arr:
        return "N/A"

    result = []
    for item in arr:
        try:
            result.append(f"{float(item):.{digits}f}")
        except Exception:
            result.append(str(item))
    return "[" + ", ".join(result) + "]"


def rpy_deg_text(rpy_value: Any) -> str:
    rpy = to_list(rpy_value)
    if len(rpy) < 3:
        return "N/A"

    deg = []
    for item in rpy[:3]:
        try:
            deg.append(math.degrees(float(item)))
        except Exception:
            return "N/A"

    return vector_text(deg, 2)


def object_public_fields(obj: Any, limit: int = 40) -> str:
    if obj is None:
        return "N/A"
    try:
        names = [x for x in dir(obj) if not x.startswith("_")]
        return ", ".join(names[:limit])
    except Exception:
        return "N/A"


class UserStore:
    def __init__(self, path: Path = USERS_FILE):
        self.path = path
        self._ensure_default_users()

    @staticmethod
    def password_hash(password: str) -> str:
        return hashlib.sha256(("h1-demo-salt:" + password).encode("utf-8")).hexdigest()

    def _ensure_default_users(self) -> None:
        if self.path.exists():
            return

        default_users = {
            "admin": {
                "password_hash": self.password_hash("admin123"),
                "role": "admin",
                "display_name": "管理员",
            },
            "operator": {
                "password_hash": self.password_hash("operator123"),
                "role": "operator",
                "display_name": "操作员",
            },
        }

        self.path.write_text(
            json.dumps(default_users, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def authenticate(self, username: str, password: str) -> Optional[Dict[str, str]]:
        users = json.loads(self.path.read_text(encoding="utf-8"))
        user = users.get(username)

        if not user:
            return None

        if user.get("password_hash") != self.password_hash(password):
            return None

        return {
            "username": username,
            "role": user.get("role", "operator"),
            "display_name": user.get("display_name", username),
        }


@dataclass
class RobotConfig:
    robot_ip: str = "192.168.123.162"
    port: int = 8080

    # 注意：你原代码里写成了 eenx9c69d3565ef9，多了一个 e
    network_interface: str = "enx9c69d3565ef9"

    protocol: str = "mock"  # mock / sdk2 / ros2 / tcp
    timeout_ms: int = 2000

    # H1 常用：rt/lowState；有些示例或型号可能是 rt/lowstate
    lowstate_topic: str = "rt/lowState"

    # H1 常先试 unitree_go；H1-2 / G1 等可能要试 unitree_hg
    lowstate_idl: str = "unitree_go"

    # 深度相机/视频流：
    # 0 表示本机 USB 摄像头；
    # realsense 表示本机 RealSense 深度模式；
    # http://... 或 rtsp://... 表示网络视频流
    camera_source: str = "0"


def import_lowstate_class(idl_type: str):
    if idl_type == "unitree_go":
        from unitree_sdk2py.idl.unitree_go.msg.dds_ import LowState_
        return LowState_

    if idl_type == "unitree_hg":
        from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowState_
        return LowState_

    raise RuntimeError(f"未知 LowState IDL：{idl_type}")


def extract_low_state(msg: Any, topic: str, idl_type: str, packet_count: int) -> Dict[str, Any]:
    imu_state = safe_get(msg, "imu_state")
    bms_state = safe_get(msg, "bms_state")
    motor_state = safe_get(msg, "motor_state", [])

    rpy = first_valid_value(
        imu_state,
        [
            ["rpy"],
            ["rpy_"],
        ],
    )

    quaternion = first_valid_value(
        imu_state,
        [
            ["quaternion"],
            ["quat"],
            ["q"],
        ],
    )

    gyroscope = first_valid_value(
        imu_state,
        [
            ["gyroscope"],
            ["gyro"],
            ["angular_velocity"],
        ],
    )

    accelerometer = first_valid_value(
        imu_state,
        [
            ["accelerometer"],
            ["acc"],
            ["linear_acceleration"],
        ],
    )

    power_v = first_valid_value(
        msg,
        [
            ["power_v"],
            ["voltage"],
            ["battery_voltage"],
            ["bms_state", "voltage"],
            ["bms_state", "vol"],
            ["battery_state", "voltage"],
            ["battery_state", "power_v"],
        ],
    )

    power_a = first_valid_value(
        msg,
        [
            ["power_a"],
            ["current"],
            ["battery_current"],
            ["bms_state", "current"],
            ["bms_state", "curr"],
            ["battery_state", "current"],
            ["battery_state", "power_a"],
        ],
    )

    soc = first_valid_value(
        msg,
        [
            ["bms_state", "soc"],
            ["battery_state", "soc"],
            ["battery_soc"],
            ["soc"],
        ],
    )

    tick = first_valid_value(
        msg,
        [
            ["tick"],
            ["stamp"],
            ["time_stamp"],
        ],
    )

    mode_machine = first_valid_value(
        msg,
        [
            ["mode_machine"],
            ["mode"],
        ],
    )

    motors = to_list(motor_state)
    motor_preview_lines = []

    for index, motor in enumerate(motors[:8]):
        q = safe_get(motor, "q", None)
        dq = safe_get(motor, "dq", None)

        tau = safe_get(motor, "tau_est", None)
        if tau is None:
            tau = safe_get(motor, "tau", None)

        temperature = safe_get(motor, "temperature", None)
        if temperature is None:
            temperature = safe_get(motor, "temp", None)

        motor_preview_lines.append(
            f"{index}: q={fmt_float(q)}, dq={fmt_float(dq)}, "
            f"tau={fmt_float(tau)}, temp={temperature if temperature is not None else 'N/A'}"
        )

    if motor_preview_lines:
        motor_preview = "\n".join(motor_preview_lines)
    else:
        motor_preview = "N/A"

    if power_v is None:
        power_v_text = "N/A（未在当前 LowState 字段中找到 power_v/voltage）"
    else:
        power_v_text = fmt_float(power_v, 2)

    if power_a is None:
        power_a_text = "N/A（未在当前 LowState 字段中找到 power_a/current）"
    else:
        power_a_text = fmt_float(power_a, 2)

    if soc is None:
        soc_text = "N/A（当前字段未提供 SOC）"
    else:
        soc_text = f"{soc}%"

    return {
        "update_time": now_text(),
        "packet_count": packet_count,
        "topic": topic,
        "idl_type": idl_type,
        "tick": tick if tick is not None else "N/A",
        "mode_machine": mode_machine if mode_machine is not None else "N/A",
        "imu_rpy_rad": vector_text(rpy, 4),
        "imu_rpy_deg": rpy_deg_text(rpy),
        "imu_quaternion": vector_text(quaternion, 4),
        "imu_gyro": vector_text(gyroscope, 4),
        "imu_acc": vector_text(accelerometer, 4),
        "power_v": power_v_text,
        "power_a": power_a_text,
        "soc": soc_text,
        "motor_count": len(motors),
        "motor_preview": motor_preview,
        "lowstate_fields": object_public_fields(msg),
        "imu_fields": object_public_fields(imu_state),
        "bms_fields": object_public_fields(bms_state),
    }


class LowStateWorker(QThread):
    status_signal = pyqtSignal(dict)
    log_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)

    def __init__(self, config: RobotConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self._running = True
        self._subscriber = None
        self._packet_count = 0
        self._last_msg_time = 0.0
        self._last_emit_time = 0.0

    def stop(self) -> None:
        self._running = False

        subscriber = getattr(self, "_subscriber", None)
        if subscriber is not None:
            for method_name in ["Close", "close", "Stop", "stop", "Destroy", "destroy"]:
                method = getattr(subscriber, method_name, None)
                if callable(method):
                    try:
                        method()
                        break
                    except Exception:
                        pass

    def run(self) -> None:
        if self.config.protocol == "mock":
            self._run_mock()
            return

        if self.config.protocol == "sdk2":
            self._run_sdk2()
            return

        self.error_signal.emit(
            f"当前实时状态只实现 mock/sdk2。protocol={self.config.protocol}"
        )

    def _run_mock(self) -> None:
        self.log_signal.emit("mock 状态线程启动：生成模拟 IMU / 电池数据。")
        start = time.monotonic()

        while self._running:
            self._packet_count += 1
            t = time.monotonic() - start

            rpy = [
                0.02 * math.sin(t),
                0.04 * math.sin(t * 0.6),
                0.12 * math.sin(t * 0.2),
            ]

            status = {
                "update_time": now_text(),
                "packet_count": self._packet_count,
                "topic": "mock/lowState",
                "idl_type": "mock",
                "tick": int(t * 1000),
                "mode_machine": "mock",
                "imu_rpy_rad": vector_text(rpy, 4),
                "imu_rpy_deg": rpy_deg_text(rpy),
                "imu_quaternion": vector_text([1.0, 0.0, 0.0, 0.0], 4),
                "imu_gyro": vector_text(
                    [
                        random.uniform(-0.02, 0.02),
                        random.uniform(-0.02, 0.02),
                        random.uniform(-0.02, 0.02),
                    ],
                    4,
                ),
                "imu_acc": vector_text(
                    [
                        random.uniform(-0.05, 0.05),
                        random.uniform(-0.05, 0.05),
                        9.81 + random.uniform(-0.05, 0.05),
                    ],
                    4,
                ),
                "power_v": fmt_float(67.2 + random.uniform(-0.3, 0.3), 2),
                "power_a": fmt_float(1.5 + random.uniform(-0.2, 0.2), 2),
                "soc": f"{int(80 + random.uniform(-2, 2))}%",
                "motor_count": 20,
                "motor_preview": "mock motor data",
                "lowstate_fields": "mock",
                "imu_fields": "mock",
                "bms_fields": "mock",
            }

            self.status_signal.emit(status)
            self.msleep(100)

        self.log_signal.emit("mock 状态线程已停止。")

    def _run_sdk2(self) -> None:
        try:
            from unitree_sdk2py.core.channel import ChannelFactoryInitialize, ChannelSubscriber

            LowState_ = import_lowstate_class(self.config.lowstate_idl)

            iface = self.config.network_interface.strip()
            topic = self.config.lowstate_topic.strip()
            idl_type = self.config.lowstate_idl.strip()

            if iface:
                self.log_signal.emit(f"初始化 Unitree DDS，绑定网卡：{iface}")
                ChannelFactoryInitialize(0, iface)
            else:
                self.log_signal.emit("初始化 Unitree DDS：未指定网卡，使用 SDK 默认接口。")
                ChannelFactoryInitialize(0)

            self.log_signal.emit(f"订阅 LowState：topic={topic}, idl={idl_type}")

            self._subscriber = ChannelSubscriber(topic, LowState_)
            self._subscriber.Init(self._on_low_state, 10)

            self._last_msg_time = time.monotonic()
            warned_no_data = False

            self.log_signal.emit(
                "LowState 订阅已启动。若无数据，请切换 topic=rt/lowState/rt/lowstate，"
                "或切换 IDL=unitree_go/unitree_hg。"
            )

            while self._running:
                now = time.monotonic()

                if now - self._last_msg_time > 3.0 and not warned_no_data:
                    warned_no_data = True
                    self.log_signal.emit(
                        "超过 3 秒未收到 LowState。请检查：网卡、topic、IDL、防火墙、DDS 环境变量。"
                    )

                self.msleep(100)

            self.log_signal.emit("SDK2 状态线程已停止。")

        except ModuleNotFoundError as exc:
            self.error_signal.emit(
                "未找到 unitree_sdk2py。\n"
                "请安装 Unitree SDK2 Python：\n"
                "cd ~\n"
                "git clone https://github.com/unitreerobotics/unitree_sdk2_python.git\n"
                "cd unitree_sdk2_python\n"
                "python -m pip install -e .\n\n"
                f"原始错误：{exc}"
            )

        except Exception as exc:
            self.error_signal.emit(f"SDK2 LowState 读取失败：{exc}")

    def _on_low_state(self, msg: Any) -> None:
        if not self._running:
            return

        self._packet_count += 1
        now = time.monotonic()
        self._last_msg_time = now

        # UI 限制到约 10Hz，避免刷新过快
        if now - self._last_emit_time < 0.1:
            return

        self._last_emit_time = now

        try:
            status = extract_low_state(
                msg=msg,
                topic=self.config.lowstate_topic,
                idl_type=self.config.lowstate_idl,
                packet_count=self._packet_count,
            )

            if self._running:
                self.status_signal.emit(status)

        except Exception as exc:
            self.error_signal.emit(f"解析 LowState 失败：{exc}")


class CameraWorker(QThread):
    frame_signal = pyqtSignal(QImage)
    log_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)

    def __init__(self, source: str, parent=None):
        super().__init__(parent)
        self.source = source.strip()
        self._running = True

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        if self.source.lower() == "realsense":
            self._run_realsense()
        else:
            self._run_opencv()

    def _run_opencv(self) -> None:
        try:
            import cv2

            source_value: Any
            if self.source.isdigit():
                source_value = int(self.source)
            else:
                source_value = self.source

            self.log_signal.emit(f"正在打开视频流：{source_value}")
            cap = cv2.VideoCapture(source_value)

            if not cap.isOpened():
                self.error_signal.emit(
                    "无法打开视频流。请检查 camera_source，例如 0、rtsp://...、http://...。"
                )
                return

            while self._running:
                ok, frame = cap.read()

                if not ok or frame is None:
                    self.msleep(50)
                    continue

                frame = cv2.resize(frame, (640, 480))
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                h, w, ch = rgb.shape
                bytes_per_line = ch * w

                image = QImage(
                    rgb.data,
                    w,
                    h,
                    bytes_per_line,
                    QImage.Format_RGB888,
                ).copy()

                self.frame_signal.emit(image)
                self.msleep(30)

            cap.release()
            self.log_signal.emit("视频流已停止。")

        except ModuleNotFoundError:
            self.error_signal.emit("未安装 OpenCV，请执行：pip install opencv-python")
        except Exception as exc:
            self.error_signal.emit(f"视频流读取失败：{exc}")

    def _run_realsense(self) -> None:
        try:
            import cv2
            import numpy as np
            import pyrealsense2 as rs

            self.log_signal.emit("正在打开本机 RealSense 深度相机。")

            pipeline = rs.pipeline()
            config = rs.config()
            config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)

            pipeline.start(config)

            while self._running:
                frames = pipeline.wait_for_frames()
                depth_frame = frames.get_depth_frame()

                if not depth_frame:
                    continue

                depth_image = np.asanyarray(depth_frame.get_data())
                depth_colormap = cv2.applyColorMap(
                    cv2.convertScaleAbs(depth_image, alpha=0.03),
                    cv2.COLORMAP_JET,
                )

                rgb = cv2.cvtColor(depth_colormap, cv2.COLOR_BGR2RGB)
                h, w, ch = rgb.shape
                bytes_per_line = ch * w

                image = QImage(
                    rgb.data,
                    w,
                    h,
                    bytes_per_line,
                    QImage.Format_RGB888,
                ).copy()

                self.frame_signal.emit(image)
                self.msleep(30)

            pipeline.stop()
            self.log_signal.emit("RealSense 深度流已停止。")

        except ModuleNotFoundError:
            self.error_signal.emit(
                "未安装 pyrealsense2。若相机是本机 USB RealSense，请执行：pip install pyrealsense2"
            )
        except Exception as exc:
            self.error_signal.emit(f"RealSense 深度流读取失败：{exc}")


class H1RobotClient(QObject):
    log_signal = pyqtSignal(str)
    state_signal = pyqtSignal(bool)
    status_signal = pyqtSignal(dict)

    def __init__(self):
        super().__init__()

        self.connected = False
        self.config = RobotConfig()
        self.status_worker: Optional[LowStateWorker] = None

        self._mock_params: Dict[str, Any] = {
            "control.mode": "idle",
            "motion.max_speed": 0.5,
            "safety.torque_limit": 0.6,
            "network.timeout_ms": 2000,
            "sensor.lidar_enable": True,
            "sensor.depth_camera_enable": True,
            "body.height_offset": 0.0,
        }

    def connect_robot(self, config: RobotConfig) -> None:
        if self.connected:
            self.log_signal.emit("机器人已经处于连接状态。")
            return

        self.config = config

        self.log_signal.emit(
            f"正在连接 H1：protocol={config.protocol}, ip={config.robot_ip}, "
            f"iface={config.network_interface}, topic={config.lowstate_topic}, idl={config.lowstate_idl}"
        )

        if config.protocol in ("mock", "sdk2"):
            self.connected = True
            self.state_signal.emit(True)

            self.status_worker = LowStateWorker(config)
            self.status_worker.status_signal.connect(self.status_signal.emit)
            self.status_worker.log_signal.connect(self.log_signal.emit)
            self.status_worker.error_signal.connect(self.log_signal.emit)
            self.status_worker.start()

            self.log_signal.emit("状态读取线程已启动。")
            return

        self.connected = False
        self.state_signal.emit(False)
        self.log_signal.emit("当前程序只实现 mock/sdk2 的状态读取。ros2/tcp 仍为预留接口。")

    def disconnect_robot(self) -> None:
        if not self.connected:
            self.log_signal.emit("机器人当前未连接。")
            return

        if self.status_worker is not None:
            self.status_worker.stop()

            if not self.status_worker.wait(3000):
                self.log_signal.emit("状态线程 3 秒内未完全退出，已停止 UI 刷新。")

            self.status_worker = None

        self.connected = False
        self.state_signal.emit(False)
        self.log_signal.emit("已断开连接。")

    def read_params(self) -> Dict[str, Any]:
        if not self.connected:
            raise RuntimeError("机器人未连接，无法读取参数。")

        self.log_signal.emit("已读取参数。当前参数页仍为项目预留参数，不等同于 SDK2 LowState。")
        return dict(self._mock_params)

    def write_params(self, params: Dict[str, Any]) -> None:
        if not self.connected:
            raise RuntimeError("机器人未连接，无法写入参数。")

        self._mock_params.update(params)
        self.log_signal.emit(f"已写入参数：{json.dumps(params, ensure_ascii=False)}")

    def send_command(self, command: str, payload: Optional[Dict[str, Any]] = None) -> None:
        if not self.connected:
            raise RuntimeError("机器人未连接，无法发送指令。")

        payload = payload or {}

        self.log_signal.emit(
            f"指令接口仍为预留，不向真机发布 LowCmd：{command}, "
            f"payload={json.dumps(payload, ensure_ascii=False)}"
        )



class LoginDialog(QDialog):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("H1 控制台登录")

        # 不要用 setFixedSize，避免不同系统字体缩放后被裁切
        self.setMinimumSize(620, 660)
        self.resize(620, 660)

        self.user_profile: Optional[Dict[str, str]] = None
        self.store = UserStore()

        self._build_ui()
        self._apply_style()

    def _build_ui(self) -> None:
        root = QVBoxLayout()
        root.setContentsMargins(48, 38, 48, 34)
        root.setSpacing(18)

        logo = QLabel("H1")
        logo.setObjectName("LogoLabel")
        logo.setAlignment(Qt.AlignCenter)

        logo_row = QHBoxLayout()
        logo_row.addStretch()
        logo_row.addWidget(logo)
        logo_row.addStretch()

        title = QLabel("H1 Robot Control Panel")
        title.setObjectName("TitleLabel")
        title.setAlignment(Qt.AlignCenter)

        subtitle = QLabel("机器人上位机 · 状态监控 · 参数预留接口")
        subtitle.setObjectName("SubtitleLabel")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setWordWrap(True)

        card = QWidget()
        card.setObjectName("LoginCard")

        card_layout = QVBoxLayout()
        card_layout.setContentsMargins(34, 30, 34, 30)
        card_layout.setSpacing(14)

        user_label = QLabel("用户名")
        user_label.setObjectName("FieldLabel")

        self.username_edit = QLineEdit()
        self.username_edit.setPlaceholderText("请输入用户名，例如 admin")
        self.username_edit.setText("admin")
        self.username_edit.setMinimumHeight(48)

        pass_label = QLabel("密码")
        pass_label.setObjectName("FieldLabel")

        self.password_edit = QLineEdit()
        self.password_edit.setPlaceholderText("请输入密码，例如 admin123")
        self.password_edit.setText("admin123")
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.setMinimumHeight(48)

        self.show_password_box = QCheckBox("显示密码")
        self.show_password_box.setMinimumHeight(32)
        self.show_password_box.toggled.connect(self._toggle_password_visible)

        button_row = QHBoxLayout()
        button_row.setSpacing(14)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setObjectName("SecondaryButton")
        self.cancel_btn.setMinimumSize(120, 42)
        self.cancel_btn.clicked.connect(self.reject)

        self.login_btn = QPushButton("登录")
        self.login_btn.setObjectName("PrimaryButton")
        self.login_btn.setMinimumSize(120, 42)
        self.login_btn.setDefault(True)
        self.login_btn.clicked.connect(self._try_login)

        button_row.addStretch()
        button_row.addWidget(self.cancel_btn)
        button_row.addWidget(self.login_btn)

        tip = QLabel("演示账号：admin / admin123；operator / operator123")
        tip.setObjectName("TipLabel")
        tip.setWordWrap(True)

        card_layout.addWidget(user_label)
        card_layout.addWidget(self.username_edit)
        card_layout.addSpacing(8)
        card_layout.addWidget(pass_label)
        card_layout.addWidget(self.password_edit)
        card_layout.addSpacing(4)
        card_layout.addWidget(self.show_password_box)
        card_layout.addSpacing(16)
        card_layout.addLayout(button_row)
        card_layout.addSpacing(12)
        card_layout.addWidget(tip)

        card.setLayout(card_layout)

        footer = QLabel("真机调试前请先确认：ping 192.168.123.162 可达")
        footer.setObjectName("FooterLabel")
        footer.setAlignment(Qt.AlignCenter)
        footer.setWordWrap(True)

        root.addLayout(logo_row)
        root.addWidget(title)
        root.addWidget(subtitle)
        root.addSpacing(10)
        root.addWidget(card)
        root.addStretch()
        root.addWidget(footer)

        self.setLayout(root)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QDialog {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:1,
                    stop:0 #0f172a,
                    stop:1 #1e293b
                );
                color: #e5e7eb;
                font-family: "Noto Sans CJK SC", "Microsoft YaHei", Arial;
            }

            QLabel {
                color: #e5e7eb;
                font-size: 15px;
            }

            #LogoLabel {
                min-width: 88px;
                max-width: 88px;
                min-height: 88px;
                max-height: 88px;
                border-radius: 44px;
                background-color: #2563eb;
                color: white;
                font-size: 30px;
                font-weight: 900;
            }

            #TitleLabel {
                font-size: 26px;
                font-weight: 900;
                color: white;
            }

            #SubtitleLabel {
                font-size: 15px;
                color: #cbd5e1;
            }

            #LoginCard {
                background-color: rgba(255, 255, 255, 0.11);
                border: 1px solid rgba(255, 255, 255, 0.22);
                border-radius: 22px;
            }

            #FieldLabel {
                font-size: 15px;
                font-weight: 700;
                color: #e5e7eb;
            }

            QLineEdit {
                background-color: rgba(15, 23, 42, 0.96);
                border: 1px solid #475569;
                border-radius: 12px;
                padding-left: 14px;
                padding-right: 14px;
                color: white;
                font-size: 15px;
                selection-background-color: #2563eb;
                selection-color: white;
            }

            QLineEdit:focus {
                border: 1px solid #60a5fa;
                background-color: #0f172a;
            }

            QCheckBox {
                color: #cbd5e1;
                font-size: 15px;
                spacing: 10px;
            }

            QCheckBox::indicator {
                width: 18px;
                height: 18px;
            }

            QPushButton {
                border-radius: 10px;
                padding-left: 24px;
                padding-right: 24px;
                font-weight: 700;
                font-size: 15px;
            }

            #PrimaryButton {
                background-color: #2563eb;
                color: white;
                border: none;
            }

            #PrimaryButton:hover {
                background-color: #1d4ed8;
            }

            #SecondaryButton {
                background-color: transparent;
                color: #e5e7eb;
                border: 1px solid #64748b;
            }

            #SecondaryButton:hover {
                background-color: rgba(255, 255, 255, 0.10);
            }

            #TipLabel {
                color: #93c5fd;
                font-size: 13px;
            }

            #FooterLabel {
                color: #94a3b8;
                font-size: 13px;
            }
            """
        )

    def _toggle_password_visible(self, checked: bool) -> None:
        self.password_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)

    def _try_login(self) -> None:
        username = self.username_edit.text().strip()
        password = self.password_edit.text()

        profile = self.store.authenticate(username, password)

        if not profile:
            QMessageBox.warning(self, "登录失败", "用户名或密码错误。")
            return

        self.user_profile = profile
        self.accept()


class MainWindow(QMainWindow):
    PARAM_DEFS = [
        {
            "key": "control.mode",
            "default": "idle",
            "desc": "控制模式：idle / manual / auto",
            "writable": True,
        },
        {
            "key": "motion.max_speed",
            "default": 0.5,
            "desc": "最大运动速度，建议先小范围调试",
            "writable": True,
        },
        {
            "key": "safety.torque_limit",
            "default": 0.6,
            "desc": "力矩限制，建议仅管理员修改",
            "writable": True,
        },
        {
            "key": "network.timeout_ms",
            "default": 2000,
            "desc": "网络通信超时时间，单位 ms",
            "writable": True,
        },
        {
            "key": "sensor.lidar_enable",
            "default": True,
            "desc": "是否启用 3D LiDAR",
            "writable": True,
        },
        {
            "key": "sensor.depth_camera_enable",
            "default": True,
            "desc": "是否启用深度相机",
            "writable": True,
        },
        {
            "key": "body.height_offset",
            "default": 0.0,
            "desc": "机身高度偏移，单位 m",
            "writable": True,
        },
    ]

    def __init__(self, user_profile: Dict[str, str]):
        super().__init__()

        self.user_profile = user_profile
        self.client = H1RobotClient()
        self.camera_worker: Optional[CameraWorker] = None

        self.client.log_signal.connect(self.append_log)
        self.client.state_signal.connect(self.on_connection_state_changed)
        self.client.status_signal.connect(self.update_robot_status)

        self.setWindowTitle("H1 机器人控制台")
        self.resize(1080, 760)

        self._build_ui()
        self._apply_main_style()
        self._load_config()

        self.append_log(
            f"用户 {user_profile['display_name']} 已登录，角色：{user_profile['role']}"
        )

    def _apply_main_style(self) -> None:
        self.setStyleSheet(
        """
        QMainWindow {
            background-color: #f8fafc;
            font-family: "Microsoft YaHei", "Noto Sans CJK SC", Arial;
        }

        QGroupBox {
            font-weight: 700;
            border: 1px solid #cbd5e1;
            border-radius: 10px;
            margin-top: 12px;
            padding: 12px;
            background-color: white;
        }

        QGroupBox::title {
            subcontrol-origin: margin;
            left: 12px;
            padding: 0 4px;
            color: #0f172a;
        }

        QPushButton {
            min-height: 32px;
            border-radius: 7px;
            padding: 5px 14px;
            background-color: #e2e8f0;
            border: 1px solid #cbd5e1;
            color: #0f172a;
        }

        QPushButton:hover {
            background-color: #cbd5e1;
        }

        QLineEdit, QSpinBox {
            min-height: 34px;
            border-radius: 7px;
            border: 1px solid #cbd5e1;
            padding-left: 10px;
            padding-right: 10px;
            background-color: white;
            color: #0f172a;
            selection-background-color: #2563eb;
            selection-color: white;
        }

        QComboBox {
            min-height: 34px;
            border-radius: 7px;
            border: 1px solid #cbd5e1;
            padding-left: 10px;
            padding-right: 34px;
            background-color: white;
            color: #0f172a;
        }

        QComboBox:focus {
            border: 1px solid #2563eb;
        }

        QComboBox::drop-down {
            subcontrol-origin: padding;
            subcontrol-position: top right;
            width: 30px;
            border-left: 1px solid #cbd5e1;
            border-top-right-radius: 7px;
            border-bottom-right-radius: 7px;
            background-color: #f1f5f9;
        }

        QComboBox::down-arrow {
            width: 0px;
            height: 0px;
        }

        QComboBox::drop-down:after {
            color: #0f172a;
        }

        QComboBox QAbstractItemView {
            background-color: white;
            color: #0f172a;
            border: 1px solid #94a3b8;
            selection-background-color: #dbeafe;
            selection-color: #0f172a;
            outline: none;
            padding: 4px;
        }

        QComboBox QAbstractItemView::item {
            min-height: 30px;
            padding: 6px;
        }

        QTextEdit {
            border: 1px solid #cbd5e1;
            border-radius: 8px;
            background-color: #0f172a;
            color: #dbeafe;
            font-family: Consolas, "Courier New";
        }

        QTableWidget {
            background-color: white;
            border: 1px solid #cbd5e1;
            border-radius: 8px;
            color: #0f172a;
        }

        QHeaderView::section {
            background-color: #e2e8f0;
            padding: 6px;
            border: none;
            font-weight: 700;
            color: #0f172a;
        }

        QLabel {
            color: #0f172a;
        }
        """
    )

    def _build_ui(self) -> None:
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_connection_tab(), "连接")
        self.tabs.addTab(self._build_status_tab(), "实时状态")
        self.tabs.addTab(self._build_camera_tab(), "深度相机/视频流")
        self.tabs.addTab(self._build_params_tab(), "参数")
        self.tabs.addTab(self._build_log_tab(), "日志")
        self.setCentralWidget(self.tabs)

        self.status = QStatusBar()
        self.setStatusBar(self.status)

        self.connection_label = QLabel("未连接")
        self.user_label = QLabel(
            f"当前用户：{self.user_profile['display_name']} | 角色：{self.user_profile['role']}"
        )

        self.status.addWidget(self.user_label)
        self.status.addPermanentWidget(self.connection_label)

    def _build_connection_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout()

        conn_group = QGroupBox("H1 连接配置")
        form = QFormLayout()

        self.ip_edit = QLineEdit("192.168.123.162")

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(8080)

        self.iface_edit = QLineEdit("enx9c69d3565ef9")

        self.protocol_combo = QComboBox()
        self.protocol_combo.addItems(["mock", "sdk2", "ros2", "tcp"])
        self.protocol_combo.setCurrentText("sdk2")

        self.lowstate_topic_combo = QComboBox()
        self.lowstate_topic_combo.addItems(["rt/lowstate", "rt/lowState"])
        self.lowstate_topic_combo.setCurrentText("rt/lowstate")

        self.lowstate_idl_combo = QComboBox()
        self.lowstate_idl_combo.addItems(["unitree_go", "unitree_hg"])
        self.lowstate_idl_combo.setCurrentText("unitree_go")

        for combo in [
            self.protocol_combo,
            self.lowstate_topic_combo,
            self.lowstate_idl_combo,
        ]:
            combo.setMinimumWidth(220)
            combo.setMinimumHeight(36)
            combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)


        form.addRow("机器人 IP：", self.ip_edit)
        form.addRow("端口：", self.port_spin)
        form.addRow("网卡/接口名：", self.iface_edit)
        form.addRow("通信方式：", self.protocol_combo)
        form.addRow("LowState Topic：", self.lowstate_topic_combo)
        form.addRow("LowState IDL：", self.lowstate_idl_combo)

        conn_group.setLayout(form)

        button_row = QHBoxLayout()

        self.connect_btn = QPushButton("连接并读取状态")
        self.disconnect_btn = QPushButton("断开/停止读取")
        self.save_config_btn = QPushButton("保存配置")

        self.connect_btn.clicked.connect(self.connect_robot)
        self.disconnect_btn.clicked.connect(self.disconnect_robot)
        self.save_config_btn.clicked.connect(self.save_config)

        button_row.addWidget(self.connect_btn)
        button_row.addWidget(self.disconnect_btn)
        button_row.addWidget(self.save_config_btn)
        button_row.addStretch()

        cmd_group = QGroupBox("常用指令接口预留")
        cmd_row = QHBoxLayout()

        self.estop_btn = QPushButton("急停")
        self.stand_btn = QPushButton("站立")
        self.sit_btn = QPushButton("坐下")
        self.enable_btn = QPushButton("使能电机")
        self.disable_btn = QPushButton("失能电机")

        self.estop_btn.setStyleSheet("font-weight: bold; color: red;")

        self.estop_btn.clicked.connect(lambda: self.send_command("emergency_stop"))
        self.stand_btn.clicked.connect(lambda: self.send_command("stand_up"))
        self.sit_btn.clicked.connect(lambda: self.send_command("sit_down"))
        self.enable_btn.clicked.connect(lambda: self.send_command("enable_motors"))
        self.disable_btn.clicked.connect(lambda: self.send_command("disable_motors"))

        for btn in [
            self.estop_btn,
            self.stand_btn,
            self.sit_btn,
            self.enable_btn,
            self.disable_btn,
        ]:
            cmd_row.addWidget(btn)

        cmd_row.addStretch()
        cmd_group.setLayout(cmd_row)

        hint = QLabel(
            "提示：sdk2 模式当前只订阅 LowState，不发布 LowCmd；"
            "如果电池电压为空，优先尝试切换 LowState Topic 和 IDL。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #475569;")

        layout.addWidget(conn_group)
        layout.addLayout(button_row)
        layout.addWidget(cmd_group)
        layout.addWidget(hint)
        layout.addStretch()

        page.setLayout(layout)
        return page

    def _build_status_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout()

        status_group = QGroupBox("H1 实时状态")
        grid = QGridLayout()

        self.status_labels: Dict[str, QLabel] = {}

        rows = [
            ("update_time", "更新时间"),
            ("packet_count", "接收包计数"),
            ("topic", "Topic"),
            ("idl_type", "IDL"),
            ("tick", "Tick"),
            ("mode_machine", "Mode Machine"),
            ("imu_rpy_rad", "IMU RPY / rad"),
            ("imu_rpy_deg", "IMU RPY / deg"),
            ("imu_quaternion", "IMU Quaternion"),
            ("imu_gyro", "IMU Gyro"),
            ("imu_acc", "IMU Acc"),
            ("power_v", "电池电压 / V"),
            ("power_a", "电池电流 / A"),
            ("soc", "电量 SOC"),
            ("motor_count", "电机数量"),
            ("motor_preview", "前 8 个电机状态"),
            ("lowstate_fields", "LowState 字段调试"),
            ("imu_fields", "IMU 字段调试"),
            ("bms_fields", "BMS 字段调试"),
        ]

        for row, (key, title) in enumerate(rows):
            name_label = QLabel(title + "：")
            name_label.setStyleSheet("font-weight: 700; color: #334155;")
            value_label = QLabel("N/A")
            value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            value_label.setWordWrap(True)

            self.status_labels[key] = value_label

            grid.addWidget(name_label, row, 0)
            grid.addWidget(value_label, row, 1)

        status_group.setLayout(grid)

        hint = QLabel(
            "说明：如果 power_v/power_a/SOC 显示 N/A，但 IMU 正常，说明当前 LowState IDL 中可能没有对应字段，"
            "或机器人固件未发布该字段。请查看下方字段调试项。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #64748b;")

        layout.addWidget(status_group)
        layout.addWidget(hint)
        page.setLayout(layout)

        return page

    def _build_camera_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout()

        camera_group = QGroupBox("深度相机 / 视频流")
        form = QFormLayout()

        self.camera_source_edit = QLineEdit("0")
        self.camera_source_edit.setPlaceholderText(
            "0 / realsense / rtsp://... / http://.../?action=stream"
        )

        form.addRow("视频源：", self.camera_source_edit)
        camera_group.setLayout(form)

        button_row = QHBoxLayout()
        self.start_camera_btn = QPushButton("打开视频流")
        self.stop_camera_btn = QPushButton("停止视频流")

        self.start_camera_btn.clicked.connect(self.start_camera)
        self.stop_camera_btn.clicked.connect(self.stop_camera)

        button_row.addWidget(self.start_camera_btn)
        button_row.addWidget(self.stop_camera_btn)
        button_row.addStretch()

        self.camera_label = QLabel("视频画面")
        self.camera_label.setAlignment(Qt.AlignCenter)
        self.camera_label.setMinimumSize(640, 480)
        self.camera_label.setStyleSheet(
            "background-color: #020617; color: #cbd5e1; border-radius: 10px;"
        )

        hint = QLabel(
            "说明：如果你是在外部上位机运行，通常不能直接访问 H1 机头 USB 深度相机，"
            "需要机器人端提供 RTSP/MJPEG/ROS 图像转发；如果程序运行在 H1 开发板上，"
            "可以尝试输入 realsense 读取本机 D435 深度图。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #64748b;")

        layout.addWidget(camera_group)
        layout.addLayout(button_row)
        layout.addWidget(self.camera_label, stretch=1)
        layout.addWidget(hint)

        page.setLayout(layout)
        return page

    def _build_params_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout()

        self.param_table = QTableWidget()
        self.param_table.setColumnCount(5)
        self.param_table.setHorizontalHeaderLabels(
            ["参数名", "当前值", "待写入值", "说明", "可写"]
        )
        self.param_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.param_table.setRowCount(len(self.PARAM_DEFS))

        for row, param in enumerate(self.PARAM_DEFS):
            self._set_param_row(row, param, param["default"], "")

        button_row = QHBoxLayout()

        self.read_param_btn = QPushButton("读取参数")
        self.write_param_btn = QPushButton("写入待写入值")
        self.reset_pending_btn = QPushButton("清空待写入值")

        self.read_param_btn.clicked.connect(self.read_params)
        self.write_param_btn.clicked.connect(self.write_params)
        self.reset_pending_btn.clicked.connect(self.reset_pending_values)

        button_row.addWidget(self.read_param_btn)
        button_row.addWidget(self.write_param_btn)
        button_row.addWidget(self.reset_pending_btn)
        button_row.addStretch()

        role_hint = QLabel(
            "权限规则：operator 可以读取参数和发送常用指令；admin 可以写入全部参数。"
        )
        role_hint.setStyleSheet("color: #64748b;")

        layout.addWidget(self.param_table)
        layout.addLayout(button_row)
        layout.addWidget(role_hint)

        page.setLayout(layout)
        return page

    def _build_log_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout()

        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)

        clear_btn = QPushButton("清空日志")
        clear_btn.clicked.connect(self.log_edit.clear)

        layout.addWidget(self.log_edit)
        layout.addWidget(clear_btn)

        page.setLayout(layout)
        return page

    def _set_param_row(
        self,
        row: int,
        param: Dict[str, Any],
        current_value: Any,
        pending_value: Any,
    ) -> None:
        items = [
            QTableWidgetItem(str(param["key"])),
            QTableWidgetItem(str(current_value)),
            QTableWidgetItem(str(pending_value)),
            QTableWidgetItem(str(param["desc"])),
            QTableWidgetItem("是" if param["writable"] else "否"),
        ]

        for col, item in enumerate(items):
            if col == 2 and param["writable"]:
                item.setFlags(item.flags() | Qt.ItemIsEditable)
            else:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)

            if col in (0, 1, 4):
                item.setTextAlignment(Qt.AlignCenter)

            self.param_table.setItem(row, col, item)

    def _get_config_from_ui(self) -> RobotConfig:
        return RobotConfig(
            robot_ip=self.ip_edit.text().strip(),
            port=int(self.port_spin.value()),
            network_interface=self.iface_edit.text().strip(),
            protocol=self.protocol_combo.currentText(),
            lowstate_topic=self.lowstate_topic_combo.currentText().strip(),
            lowstate_idl=self.lowstate_idl_combo.currentText().strip(),
            camera_source=self.camera_source_edit.text().strip(),
        )

    def _load_config(self) -> None:
        if not CONFIG_FILE.exists():
            return

        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))

            # 兼容老配置文件缺少新字段
            cfg = RobotConfig(**{**asdict(RobotConfig()), **data})

            self.ip_edit.setText(cfg.robot_ip)
            self.port_spin.setValue(cfg.port)
            self.iface_edit.setText(cfg.network_interface)
            self.protocol_combo.setCurrentText(cfg.protocol)
            self.timeout_spin.setValue(cfg.timeout_ms)
            self.lowstate_topic_combo.setCurrentText(cfg.lowstate_topic)
            self.lowstate_idl_combo.setCurrentText(cfg.lowstate_idl)
            self.camera_source_edit.setText(cfg.camera_source)

            self.append_log("已加载本地连接配置。")

        except Exception as exc:
            self.append_log(f"加载配置失败：{exc}")

    def save_config(self) -> None:
        cfg = self._get_config_from_ui()

        CONFIG_FILE.write_text(
            json.dumps(asdict(cfg), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        self.append_log("连接配置已保存。")
        QMessageBox.information(self, "已保存", f"配置已保存到：{CONFIG_FILE}")

    def connect_robot(self) -> None:
        cfg = self._get_config_from_ui()

        if not cfg.robot_ip:
            QMessageBox.warning(self, "配置错误", "机器人 IP 不能为空。")
            return

        self.client.connect_robot(cfg)

    def disconnect_robot(self) -> None:
        self.client.disconnect_robot()

    def on_connection_state_changed(self, connected: bool) -> None:
        self.connection_label.setText("已连接" if connected else "未连接")
        self.connection_label.setStyleSheet(
            "color: green; font-weight: 700;" if connected else "color: red; font-weight: 700;"
        )

    def update_robot_status(self, status: Dict[str, Any]) -> None:
        for key, label in self.status_labels.items():
            label.setText(str(status.get(key, "N/A")))

    def start_camera(self) -> None:
        if self.camera_worker is not None and self.camera_worker.isRunning():
            QMessageBox.information(self, "提示", "视频流已经在运行。")
            return

        source = self.camera_source_edit.text().strip()

        if not source:
            QMessageBox.warning(self, "提示", "请填写视频源，例如 0、realsense、rtsp://...。")
            return

        self.camera_worker = CameraWorker(source)
        self.camera_worker.frame_signal.connect(self.update_camera_frame)
        self.camera_worker.log_signal.connect(self.append_log)
        self.camera_worker.error_signal.connect(self.on_camera_error)
        self.camera_worker.start()

        self.append_log(f"正在打开视频源：{source}")

    def stop_camera(self) -> None:
        if self.camera_worker is not None:
            self.camera_worker.stop()

            if not self.camera_worker.wait(3000):
                self.append_log("视频线程 3 秒内未完全退出。")

            self.camera_worker = None

        self.camera_label.setText("视频画面")
        self.append_log("已停止视频流。")

    def update_camera_frame(self, image: QImage) -> None:
        pixmap = QPixmap.fromImage(image)
        scaled = pixmap.scaled(
            self.camera_label.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.camera_label.setPixmap(scaled)

    def on_camera_error(self, text: str) -> None:
        self.append_log(text)
        QMessageBox.warning(self, "视频流错误", text)

    def read_params(self) -> None:
        try:
            params = self.client.read_params()
        except Exception as exc:
            QMessageBox.warning(self, "读取失败", str(exc))
            self.append_log(f"读取参数失败：{exc}")
            return

        for row, param in enumerate(self.PARAM_DEFS):
            key = param["key"]
            current_value = params.get(key, "")

            pending_item = self.param_table.item(row, 2)
            pending_value = pending_item.text() if pending_item else ""

            self._set_param_row(row, param, current_value, pending_value)

    def _parse_value(self, text: str) -> Any:
        text = text.strip()
        lower = text.lower()

        if lower in ("true", "yes", "1", "on", "启用", "是"):
            return True

        if lower in ("false", "no", "0", "off", "禁用", "否"):
            return False

        try:
            if "." in text:
                return float(text)
            return int(text)
        except ValueError:
            return text

    def write_params(self) -> None:
        if self.user_profile.get("role") != "admin":
            QMessageBox.warning(self, "权限不足", "只有 admin 角色可以写入参数。")
            return

        params_to_write: Dict[str, Any] = {}

        for row, param in enumerate(self.PARAM_DEFS):
            if not param["writable"]:
                continue

            key = param["key"]
            pending_item = self.param_table.item(row, 2)

            if not pending_item:
                continue

            pending_text = pending_item.text().strip()

            if pending_text == "":
                continue

            params_to_write[key] = self._parse_value(pending_text)

        if not params_to_write:
            QMessageBox.information(self, "无需写入", "没有填写待写入值。")
            return

        try:
            self.client.write_params(params_to_write)
            self.read_params()
            self.reset_pending_values()

        except Exception as exc:
            QMessageBox.warning(self, "写入失败", str(exc))
            self.append_log(f"写入参数失败：{exc}")

    def reset_pending_values(self) -> None:
        for row in range(self.param_table.rowCount()):
            item = self.param_table.item(row, 2)
            if item:
                item.setText("")

        self.append_log("已清空待写入值。")

    def send_command(self, command: str) -> None:
        if command == "emergency_stop":
            ok = QMessageBox.question(
                self,
                "确认急停",
                "确认发送急停指令？当前代码不会发布 LowCmd，但真机接入后该动作可能立即停止机器人运动。",
                QMessageBox.Yes | QMessageBox.No,
            )

            if ok != QMessageBox.Yes:
                return

        try:
            self.client.send_command(command)

        except Exception as exc:
            QMessageBox.warning(self, "指令失败", str(exc))
            self.append_log(f"发送指令失败：{exc}")

    def append_log(self, text: str) -> None:
        self.log_edit.append(f"[{now_text()}] {text}")

    def closeEvent(self, event) -> None:
        self.stop_camera()
        self.client.disconnect_robot()
        super().closeEvent(event)


def main() -> int:
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName("H1 Robot Control Panel")

    font = QFont("Noto Sans CJK SC", 10)
    app.setFont(font)

    login = LoginDialog()

    if login.exec_() != QDialog.Accepted or not login.user_profile:
        return 0

    window = MainWindow(login.user_profile)
    window.show()

    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
