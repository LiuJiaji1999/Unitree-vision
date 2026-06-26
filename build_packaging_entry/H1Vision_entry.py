#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
H1 机器人视觉监控平台 - 领导驾驶舱 v3 修正版

演示账号：
    admin / admin123
    operator / operator123

安全说明：
    本程序的 SDK2 部分默认只订阅 LowState，不发布 LowCmd，不控制电机。
"""
import re
import signal
import os

# 领导演示版：尽量压制 OpenCV/FFmpeg 的底层控制台噪声。
# 例如未正常结束的 MP4 可能触发 "moov atom not found"，后面会在业务层跳过这类文件。
os.environ.setdefault("OPENCV_LOG_LEVEL", "SILENT")
os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")

import cv2


import hashlib
import html
import json
import math
import random
import shlex
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List
import numpy as np
from PyQt5.QtCore import QObject, QTimer, pyqtSignal, Qt, QThread, QProcess, QUrl

from PyQt5.QtGui import QFont, QImage, QPixmap, QDesktopServices, QTextDocument
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
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
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QFileDialog,
    QToolButton,
    QInputDialog,
    QProgressBar,
    QSlider,

)
from PyQt5.QtPrintSupport import QPrinter
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator
from matplotlib import font_manager, rcParams



if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent
USERS_FILE = APP_DIR / "users.json"
CONFIG_FILE = APP_DIR / "h1_config.json"
NATIVE_RUNTIME_LOG = APP_DIR / "h1_native_runtime.log"


def redirect_native_console_output() -> None:
    """
    将 C/C++ 底层库直接写到 stdout/stderr 的信息转存到文件。

    Unitree SDK2 / DDS、OpenCV / FFmpeg 有些输出不经过 Python logging，
    例如 [Reader] take sample error、moov atom not found。
    领导演示版默认不让这些底层信息刷屏；如需调试，可用：
        H1_SHOW_NATIVE_CONSOLE=1 python H1_robot_vision_leader_cockpit_v3_fixed.py
    """
    if os.environ.get("H1_SHOW_NATIVE_CONSOLE") == "1":
        return

    try:
        fd = os.open(str(NATIVE_RUNTIME_LOG), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        banner = f"\n===== H1 native console redirected at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} =====\n"
        os.write(fd, banner.encode("utf-8", errors="ignore"))

        # stdout/stderr 都转存。QProcess 捕获的远程命令输出不受影响，仍能进入 UI 事件/状态处理。
        os.dup2(fd, 1)
        os.dup2(fd, 2)
    except Exception:
        pass


# 开发板 PCD 固定位置
BOARD_PCD_HOST = "192.168.123.162"
BOARD_PCD_USER = "unitree"
BOARD_PCD_PASSWORD = "Unitree0408"
BOARD_PCD_REMOTE_PATH = "/home/unitree/graph_pid_ws/config_files/QT_Server_config/GlobalMap.pcd"

# 下载到本机后的缓存路径，后续 matplotlib 从这里读取
LOCAL_PCD_PATH = APP_DIR / "GlobalMap.pcd"

# RealSense Web 视频流固定位置
BOARD_REALSENSE_REMOTE_DIR = "/home/unitree/realsense_web"
BOARD_REALSENSE_REMOTE_PATH = "/home/unitree/realsense_web/start_realsense.py"
BOARD_REALSENSE_REMOTE_LOG = "/home/unitree/realsense_web/start_realsense.log"
BOARD_REALSENSE_PORT = 8080



def setup_matplotlib_chinese_font() -> None:
    font_candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ]

    for font_path in font_candidates:
        path = Path(font_path)
        if path.exists():
            font_manager.fontManager.addfont(str(path))
            font_name = font_manager.FontProperties(fname=str(path)).get_name()

            rcParams["font.family"] = "sans-serif"
            rcParams["font.sans-serif"] = [font_name, "DejaVu Sans"]
            rcParams["axes.unicode_minus"] = False
            return

    # 找不到字体时的兜底，不会报错，但中文可能还是 warning
    rcParams["font.sans-serif"] = [
        "Noto Sans CJK SC",
        "WenQuanYi Micro Hei",
        "Microsoft YaHei",
        "SimHei",
        "DejaVu Sans",
    ]
    rcParams["axes.unicode_minus"] = False


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

ANSI_ESCAPE_RE = re.compile(
    r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])"
)

KEY_PRESSED_RE = re.compile(
    r"(?:\x1b\[[0-9;]*m|\[[0-9;]*m|[0-9;]*m)?\s*Key\s+pressed\.\s*(?:\x1b\[[0-9;]*m|\[[0-9;]*m|[0-9;]*m)?",
    re.IGNORECASE,
)

BROKEN_COLOR_RE = re.compile(
    r"(?<!\w)(?:\[[0-9;]*m|[0-9;]*m)"
)
FUZZY_KEY_PRESSED_RE = re.compile(
    r"k\s*e\s*y\s*p\s*r\s*e\s*s\s*s\s*e\s*d\s*\.",
    re.IGNORECASE,
)

ANSI_TAIL_RE = re.compile(
    r"\x1B(?:\[[0-?;]*[ -/]*)?$"
)

CONTROL_FRAGMENT_RE = re.compile(
    r"^\s*(?:\x1b|\[[0-9;]*|[0-9;]*m?)\s*$"
)



def clean_terminal_output(text: str) -> str:
    text = str(text)

    # 兼容 \r 刷新输出
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 先删带颜色的 Key pressed
    text = KEY_PRESSED_RE.sub("", text)

    # 删除标准 ANSI 控制符
    text = ANSI_ESCAPE_RE.sub("", text)

    # 删除 ESC 被吞掉后残留的 32m / 0m / [1;32m
    text = BROKEN_COLOR_RE.sub("", text)

    # 再删一次纯文本 Key pressed
    text = KEY_PRESSED_RE.sub("", text)

    lines = []

    for line in text.splitlines():
        clean_line = line.strip()

        # 兜底：整行是 Key pressed 的直接丢掉
        if re.fullmatch(r"Key\s+pressed\.", clean_line, re.IGNORECASE):
            continue

        # 空行不要刷
        if not clean_line:
            continue

        lines.append(line)

    return "\n".join(lines)


def clean_terminal_stream(buffer: str, flush: bool = False):
    buffer = str(buffer).replace("\r\n", "\n").replace("\r", "\n")

    ansi_tail = ""
    if not flush:
        match = ANSI_TAIL_RE.search(buffer)
        if match:
            ansi_tail = match.group(0)
            buffer = buffer[:match.start()]

    text = ANSI_ESCAPE_RE.sub("", buffer)
    text = BROKEN_COLOR_RE.sub("", text)
    text = FUZZY_KEY_PRESSED_RE.sub("", text)

    lines = text.split("\n")

    tail = ""
    if not flush and lines and not text.endswith("\n"):
        tail = lines.pop()

    output_lines = []

    for line in lines:
        stripped = line.strip()

        if not stripped:
            continue

        compact = re.sub(r"\s+", "", stripped).lower()

        if "keypressed.".startswith(compact):
            continue

        if "\x1b" in line:
            continue

        if "[" in line and CONTROL_FRAGMENT_RE.fullmatch(line):
            continue

        output_lines.append(line)

    return "\n".join(output_lines), tail + ansi_tail


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


def rpy_deg_values(rpy_value: Any):
    """
    将 IMU rpy 从弧度转换为角度，返回 Roll / Pitch / Yaw。
    Roll：机身左右侧倾；Pitch：机身前后俯仰；Yaw：机器人水平朝向。
    """
    rpy = to_list(rpy_value)
    if len(rpy) < 3:
        return None

    values = []
    for item in rpy[:3]:
        try:
            values.append(math.degrees(float(item)))
        except Exception:
            return None

    return values[0], values[1], values[2]

def lowstate_temp_text(value: Any) -> str:
    temp = as_int_or_none(value)
    if temp is None:
        return "N/A"
    if temp > 127:
        temp -= 256
    return f"{temp} ℃"


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

    # enx9c69d3565ef9
    network_interface: str = "enx9c69d3565ef9"

    protocol: str = "sdk2"  # mock / sdk2
    timeout_ms: int = 2000

    # H1 rt/lowstate；
    lowstate_topic: str = "rt/lowstate"

    # H1 常先试 unitree_go；H1-2 / G1 等可能要试 unitree_hg
    lowstate_idl: str = "unitree_go"

     # LiDAR 状态 DDS topic
    lidar_state_topic: str = "rt/lidarstate"

    # 已导出的 PCD 点云地图文件，建议把 GlobalMap.pcd 放到程序同目录
    pointcloud_file: str = str(LOCAL_PCD_PATH)


def import_lowstate_class(idl_type: str):
    if idl_type == "unitree_go":
        from unitree_sdk2py.idl.unitree_go.msg.dds_ import LowState_
        return LowState_

    if idl_type == "unitree_hg":
        from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowState_
        return LowState_

    raise RuntimeError(f"未知 LowState IDL：{idl_type}")

_DDS_FACTORY_INITIALIZED = False
_DDS_FACTORY_IFACE = ""


def ensure_dds_initialized(iface: str, log_func=None) -> None:
    """
    避免 LowStateWorker 和 LidarStateWorker 重复初始化 ChannelFactory。
    """
    global _DDS_FACTORY_INITIALIZED, _DDS_FACTORY_IFACE

    from unitree_sdk2py.core.channel import ChannelFactoryInitialize

    iface = (iface or "").strip()

    if _DDS_FACTORY_INITIALIZED:
        if log_func and iface and iface != _DDS_FACTORY_IFACE:
            log_func(
                f"DDS 已初始化过，当前继续使用首次网卡：{_DDS_FACTORY_IFACE}，"
                f"忽略新的网卡：{iface}"
            )
        return

    if iface:
        if log_func:
            log_func(f"初始化 Unitree DDS，绑定网卡：{iface}")
        ChannelFactoryInitialize(0, iface)
        _DDS_FACTORY_IFACE = iface
    else:
        if log_func:
            log_func("初始化 Unitree DDS：未指定网卡，使用 SDK 默认接口。")
        ChannelFactoryInitialize(0)
        _DDS_FACTORY_IFACE = ""

    _DDS_FACTORY_INITIALIZED = True


def get_object_field_names(obj: Any, preferred: Optional[List[str]] = None) -> List[str]:
    if obj is None:
        return []

    annotations = getattr(obj.__class__, "__annotations__", {})
    if annotations:
        names = list(annotations.keys())
    else:
        names = []
        for name in dir(obj):
            if name.startswith("_"):
                continue
            value = safe_get(obj, name, None)
            if callable(value):
                continue
            names.append(name)

    result = []

    if preferred:
        for name in preferred:
            if name in names and name not in result:
                result.append(name)

    for name in names:
        if name not in result:
            result.append(name)

    return result


def display_value(value: Any, max_len: int = 1200) -> str:
    if value is None:
        return "N/A"

    if isinstance(value, float):
        return f"{value:.6f}"

    if isinstance(value, (int, bool)):
        return str(value)

    if isinstance(value, str):
        return value

    if isinstance(value, (bytes, bytearray)):
        text = value.hex(" ")
        if len(text) > max_len:
            return text[:max_len] + " ..."
        return text

    try:
        arr = list(value)
        out = []

        for item in arr:
            if isinstance(item, float):
                out.append(f"{item:.6f}")
            elif isinstance(item, (int, bool, str)):
                out.append(str(item))
            else:
                out.append(object_brief_text(item))

        text = "[" + ", ".join(out) + "]"
        if len(text) > max_len:
            return text[:max_len] + " ..."
        return text

    except Exception:
        pass

    text = object_brief_text(value)
    if len(text) > max_len:
        return text[:max_len] + " ..."
    return text

def is_zero_or_all_zero(value: Any) -> bool:
    """
    判断字段是否为 0。

    规则：
    1. 单个 int / float 为 0，认为未开放。
    2. 数组 / list / SDK array 全部为 0，认为未开放。
    3. None 不认为是 0，仍显示 N/A。
    4. 对象结构体不在这里判断，例如 imu_state / bms_state / motor_state。
    """

    if value is None:
        return False

    if isinstance(value, bool):
        return False

    if isinstance(value, (int, float)):
        return float(value) == 0.0

    try:
        arr = list(value)
    except Exception:
        return False

    if not arr:
        return False

    for item in arr:
        if isinstance(item, bool):
            return False

        try:
            if float(item) != 0.0:
                return False
        except Exception:
            return False

    return True


def display_lowstate_field_value(name: str, value: Any) -> str:
    """
    LowState 主字段专用显示逻辑。

    数值为 0 或数组全 0：
        显示“该字段并未开放”

    温度字段：
        按 int8 / uint8 温度显示

    其他字段：
        使用原来的 display_value()
    """

    if is_zero_or_all_zero(value):
        return "该字段并未开放"

    if name in ("temperature_ntc1", "temperature_ntc2"):
        return lowstate_temp_text(value)

    return display_value(value)


UI_TEXT_MAP = {
    # 通用
    "update_time": "更新时间",
    "packet_count": "数据包计数",
    "topic": "主题",
    "idl_type": "数据类型",
    "index": "编号",

    # LowState 主字段
    "head": "帧头",
    "foot_force": "足端力",
    "foot_force_est": "估算足端力",
    "tick": "计时器",
    "wireless_remote": "遥控器原始数据",
    "bit_flag": "组件状态",
    "adc_reel": "卷线器电流",
    "temperature_ntc1": "主板中心温度",
    "temperature_ntc2": "自动充电温度",
    "power_v": "电池电压（底层未开放，已隐藏）",
    "power_a": "电池电流（底层未开放，已隐藏）",
    "fan_frequency": "风扇转速",
    "crc": "校验位",
    "imu_state": "惯性测量单元状态",
    "motor_state": "电机状态",
    "bms_state": "电池管理系统状态",
    
    "bms_status_display": "电池状态",
    "bms_soc_display": "电池电量",
    "bms_cell_voltage_summary": "电芯电压概览",
    "motor_state_count": "电机数量",
    "battery_voltage_display": "电池电压显示（底层未开放，已隐藏）",
    "battery_current_display": "电池电流显示（底层未开放，已隐藏）",

    # IMU
    "quaternion": "四元数",
    "rpy": "姿态角原始值（弧度）",
    "rpy_deg": "姿态角（Roll/Pitch/Yaw，度）",
    "leader_attitude_note": "展示说明",
    "roll_deg": "Roll 左右侧倾",
    "pitch_deg": "Pitch 前后俯仰",
    "yaw_deg": "Yaw 水平朝向",
    "gyroscope": "陀螺仪",
    "accelerometer": "加速度计",
    "temperature": "温度",

    # BMS
    "version_high": "主版本号",
    "version_low": "次版本号",
    "status": "状态",
    "soc": "剩余电量",
    "current": "电流",
    "cycle": "充电循环次数",
    "bq_ntc": "电池内部温度",
    "mcu_ntc": "电池板温度",
    "cell_vol": "电芯电压",

    # MotorState
    "mode": "模式",
    "q": "关节位置",
    "dq": "关节速度",
    "ddq": "关节加速度",
    "tau_est": "估算力矩",
    "lost": "通信丢失",
    "error_flag": "错误标志",
    "comm_frequency": "通信频率",

    # LiDAR
    "stamp": "时间戳",
    "firmware_version": "固件版本",
    "software_version": "软件版本",
    "sdk_version": "SDK 版本",
    "sys_rotation_speed": "系统转速",
    "com_rotation_speed": "通信转速",
    "error_state": "错误状态",
    "error_state_text": "错误状态说明",
    "cloud_frequency": "点云频率",
    "cloud_packet_loss_rate": "点云丢包率",
    "cloud_size": "点云数量",
    "cloud_scan_num": "点云扫描帧数",
    "imu_frequency": "惯导频率",
    "imu_packet_loss_rate": "惯导丢包率",
    "imu_rpy": "惯导姿态角",
    "imu_rpy_deg": "惯导姿态角（度）",
    "serial_recv_stamp": "串口接收时间戳",
    "serial_buffer_size": "串口缓存大小",
    "serial_buffer_read": "串口已读大小",

    # 参数值
    "idle": "空闲",
    "manual": "手动",
    "auto": "自动",
}


def tr_ui_text(text: Any) -> str:
    return UI_TEXT_MAP.get(str(text), str(text))


def object_brief_text(obj: Any) -> str:
    if obj is None:
        return "N/A"

    if isinstance(obj, float):
        return f"{obj:.6f}"

    if isinstance(obj, (int, bool, str)):
        return str(obj)

    fields = get_object_field_names(obj)

    if not fields:
        return str(obj)

    parts = []
    for name in fields[:12]:
        value = safe_get(obj, name, None)
        if isinstance(value, float):
            value_text = f"{value:.6f}"
        elif isinstance(value, (int, bool, str)):
            value_text = str(value)
        else:
            value_text = type(value).__name__
        parts.append(f"{name}={value_text}")

    if len(fields) > 12:
        parts.append("...")

    return "{" + ", ".join(parts) + "}"


def object_to_table_dict(obj: Any, preferred: Optional[List[str]] = None) -> Dict[str, str]:
    data: Dict[str, str] = {}

    if obj is None:
        return data

    for name in get_object_field_names(obj, preferred):
        value = safe_get(obj, name, None)
        data[name] = display_value(value)

    return data


def extract_motor_rows(motor_state: Any) -> Dict[str, Any]:
    """
    MotorState 前端展示字段。

    不展示：
        q_raw / dq_raw / ddq_raw：沿用字段，目前不用。

    reserve 不直接展示，拆成：
        reserve[0] -> error_flag
        reserve[1] -> comm_frequency
    """

    motors = to_list(motor_state)

    columns = [
        "mode",
        "q",
        "dq",
        "ddq",
        "tau_est",
        "temperature",
        "lost",
        "error_flag",
        "comm_frequency",
    ]

    rows = []


    for index, motor in enumerate(motors):
        reserve = to_list(safe_get(motor, "reserve", []))

        error_flag = reserve[0] if len(reserve) > 0 else None
        comm_frequency = reserve[1] if len(reserve) > 1 else None

        row = {
            "index": str(index),
            "mode": display_value(safe_get(motor, "mode", None)),
            "q": display_value(safe_get(motor, "q", None)),
            "dq": display_value(safe_get(motor, "dq", None)),
            "ddq": display_value(safe_get(motor, "ddq", None)),
            "tau_est": display_value(safe_get(motor, "tau_est", None)),
            "temperature": display_value(safe_get(motor, "temperature", None)),
            "lost": display_value(safe_get(motor, "lost", None)),
            "error_flag": display_value(error_flag),
            "comm_frequency": display_value(comm_frequency),
        }

        rows.append(row)

    return {
        "columns": columns,
        "rows": rows,
    }


def as_float_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None

    try:
        return float(value)
    except Exception:
        return None


def as_int_or_none(value: Any) -> Optional[int]:
    if value is None:
        return None

    try:
        return int(value)
    except Exception:
        return None


def all_zero_list(value: Any) -> bool:
    arr = to_list(value)

    if not arr:
        return True

    for item in arr:
        try:
            if float(item) != 0:
                return False
        except Exception:
            return False

    return True

BMS_STATUS_MAP = {
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


def bms_status_text(value: Any) -> str:
    code = as_int_or_none(value)

    if code is None:
        return "N/A"

    return f"{code} {BMS_STATUS_MAP.get(code, '未知状态')}"


def bms_version_text(high: Any, low: Any) -> str:
    high_i = as_int_or_none(high)
    low_i = as_int_or_none(low)

    if high_i is None and low_i is None:
        return "N/A"

    return f"{high_i if high_i is not None else 'N/A'}.{low_i if low_i is not None else 'N/A'}"


def bms_current_text(value: Any) -> str:
    current = as_int_or_none(value)

    if current is None:
        return "N/A"

    if current > 0:
        direction = "充电"
    elif current < 0:
        direction = "放电"
    else:
        direction = "无充放电"

    return f"{current}（{direction}）"


def bms_temp_value_text(value: Any) -> str:
    """
    BMS 的 NTC 在 IDL 里常见是 uint8，但注释里又写 int8_t。
    这里做一个兼容：
    - 0~150 按正常温度显示
    - 151~255 按有符号 int8 推测显示，例如 246 -> -10
    """

    temp = as_int_or_none(value)

    if temp is None:
        return "N/A"

    if temp > 150:
        temp = temp - 256

    return f"{temp} ℃"


def bms_temp_array_text(value: Any, names: Optional[List[str]] = None) -> str:
    arr = to_list(value)

    if not arr:
        return "N/A"

    result = []

    for index, item in enumerate(arr):
        label = names[index] if names and index < len(names) else f"NTC{index}"
        result.append(f"{label}: {bms_temp_value_text(item)}")

    return "；".join(result)


def bms_cell_voltage_values(cell_vol: Any) -> List[Optional[float]]:
    """
    BmsState_.cell_vol 是 15 节电芯电压，通常 raw 单位是 mV。
    例如 4100 表示 4.100 V。
    """

    result: List[Optional[float]] = []

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
    values = bms_cell_voltage_values(cell_vol)

    indexed_cells = [
        (index + 1, value)
        for index, value in enumerate(values)
        if value is not None and value > 0
    ]

    if not indexed_cells:
        return "N/A"

    total_v = sum(value for _, value in indexed_cells)
    avg_v = total_v / len(indexed_cells)

    min_index, min_v = min(indexed_cells, key=lambda item: item[1])
    max_index, max_v = max(indexed_cells, key=lambda item: item[1])

    diff_mv = (max_v - min_v) * 1000.0

    return (
        f"有效单体数={len(indexed_cells)}，"
        f"估算总电压={total_v:.2f} V，"
        f"平均={avg_v:.3f} V，"
        f"最低=第{min_index:02d}节 {min_v:.3f} V，"
        f"最高=第{max_index:02d}节 {max_v:.3f} V，"
        f"压差={diff_mv:.0f} mV"
    )


def extract_low_state(msg: Any, topic: str, idl_type: str, packet_count: int) -> Dict[str, Any]:
    imu_state = safe_get(msg, "imu_state")
    # bms_state = safe_get(msg, "bms_state")
    motor_state = safe_get(msg, "motor_state", [])


    lowstate_fields = [
        "update_time",
        "packet_count",
        "head",
        "imu_state",
        "motor_state",
        "tick",
        "bit_flag",
        "crc",


        "foot_force",
        # "foot_force_est",
        "wireless_remote",
        "adc_reel",
        "temperature_ntc1",
        "temperature_ntc2",
        # 电池电压/电流底层未开放，领导展示版不在状态主字段和驾驶舱展示。
        # "power_v",
        # "power_a",
        "fan_frequency",

    ]


    imu_preferred = [
        "quaternion",
        "rpy",
        "gyroscope",
        "accelerometer",
        "temperature",
    ]

  

    # 低频状态主字段页面
    lowstate_main: Dict[str, str] = {}

    for name in lowstate_fields:

        if name == "update_time":
            lowstate_main[name] = now_text()
            continue

        if name == "packet_count":
            lowstate_main[name] = str(packet_count)
            continue

        value = safe_get(msg, name, None)

        if name == "imu_state":
            lowstate_main[name] = object_brief_text(imu_state)

        # elif name == "bms_state":
        #     lowstate_main[name] = object_brief_text(bms_state)

        elif name == "motor_state":
            motor_count = len(to_list(motor_state))

            if motor_count == 0:
                lowstate_main[name] = "该字段并未开放"
            else:
                lowstate_main[name] = f"电机状态数组，数量：{motor_count}，详情见“电机状态”页面"

        else:
            lowstate_main[name] = display_lowstate_field_value(name, value)

    # IMU 详细页面
    imu_table = object_to_table_dict(imu_state, imu_preferred)

    if "rpy" in imu_table:
        rpy_value = safe_get(imu_state, "rpy", None)
        imu_table["rpy_deg"] = rpy_deg_text(rpy_value)

        rpy_leader_values = rpy_deg_values(rpy_value)
        if rpy_leader_values is not None:
            roll_deg, pitch_deg, yaw_deg = rpy_leader_values
            imu_table["leader_attitude_note"] = (
                "Roll 表示机身左右侧倾；Pitch 表示机身前后俯仰；"
                "Yaw 表示机器人在水平面上的朝向变化。单位均为度，数值越接近 0 代表姿态越平稳。"
            )
            imu_table["roll_deg"] = f"{roll_deg:.2f}°"
            imu_table["pitch_deg"] = f"{pitch_deg:.2f}°"
            imu_table["yaw_deg"] = f"{yaw_deg:.2f}°"

    # # BMS 详细页面
    # bms_table = build_bms_display_table(msg, bms_state)

    # 电机详细页面
    motor_data = extract_motor_rows(motor_state)

    return {
        "lowstate_main": lowstate_main,
        "imu_state": imu_table,
        # "bms_state": bms_table,
        "motor_columns": motor_data["columns"],
        "motor_rows": motor_data["rows"],
    }



class LowStateWorker(QThread):
    status_signal = pyqtSignal(dict)
    log_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)

    # 真正初始化成功后才发这个信号
    ready_signal = pyqtSignal()

    # SDK2 初始化失败这类致命错误用这个信号
    fatal_signal = pyqtSignal(str)

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

        self.fatal_signal.emit(
            f"当前实时状态只实现 mock/sdk2。protocol={self.config.protocol}"
        )

    def _run_sdk2(self) -> None:
        try:
            from unitree_sdk2py.core.channel import ChannelSubscriber

            LowState_ = import_lowstate_class(self.config.lowstate_idl)

            iface = self.config.network_interface.strip()
            topic = self.config.lowstate_topic.strip()
            idl_type = self.config.lowstate_idl.strip()

            ensure_dds_initialized(iface, self.log_signal.emit)


            self.log_signal.emit(f"订阅 LowState：topic={topic}, idl={idl_type}")

            self._subscriber = ChannelSubscriber(topic, LowState_)
            self._subscriber.Init(self._on_low_state, 10)

            self._last_msg_time = time.monotonic()
            warned_no_data = False

            self.log_signal.emit(
                "lowstate 订阅已启动。若无数据，请切换 topic=rt/lowState、/rt/lowstate，"
                "或切换 IDL=unitree_go/unitree_hg。"
            )

            # 只有走到这里，才说明 SDK2 ChannelFactory 和 Subscriber 初始化成功
            self.ready_signal.emit()

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
            self.fatal_signal.emit(
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
        
        
def import_lidarstate_class():
    from unitree_sdk2py.idl.unitree_go.msg.dds_ import LidarState_
    return LidarState_


def lidar_error_text(value: Any) -> str:
    try:
        code = int(value)
    except Exception:
        return "N/A"

    if code == 0:
        return "0 正常"

    mapping = [
        (0x01, "电机转速异常"),
        (0x02, "点云数据异常"),
        (0x04, "串口数据异常"),
    ]

    names = [text for bit, text in mapping if code & bit]

    if not names:
        return f"{code} 未知错误码"

    return f"{code} / " + "，".join(names)


def extract_lidar_state(msg: Any, topic: str, packet_count: int) -> Dict[str, Any]:
    fields = [
        "stamp",
        "firmware_version",
        "software_version",
        "sdk_version",
        "sys_rotation_speed",
        "com_rotation_speed",
        "error_state",
        "cloud_frequency",
        "cloud_packet_loss_rate",
        "cloud_size",
        "cloud_scan_num",
        "imu_frequency",
        "imu_packet_loss_rate",
        "imu_rpy",
        "serial_recv_stamp",
        "serial_buffer_size",
        "serial_buffer_read",
    ]

    data = {
        "update_time": now_text(),
        "packet_count": str(packet_count),
        "topic": topic,
        "idl_type": "unitree_go/LidarState_",
    }

    for name in fields:
        data[name] = display_value(safe_get(msg, name, None))

    data["imu_rpy_deg"] = rpy_deg_text(safe_get(msg, "imu_rpy", None))
    data["error_state_text"] = lidar_error_text(safe_get(msg, "error_state", None))

    return data

# 新增 LiDAR 状态解析和线程
class LidarStateWorker(QThread):
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
            f"当前 LiDAR 状态只实现 mock/sdk2。protocol={self.config.protocol}"
        )

    def _run_mock(self) -> None:
        self.log_signal.emit("mock LiDAR 状态线程启动。")

        while self._running:
            self._packet_count += 1

            status = {
                "update_time": now_text(),
                "packet_count": str(self._packet_count),
                "topic": "mock/lidar_state",
                "idl_type": "mock",
                "stamp": fmt_float(time.time(), 6),
                "firmware_version": "mock-fw-1.0",
                "software_version": "mock-sw-1.0",
                "sdk_version": "mock-sdk",
                "sys_rotation_speed": fmt_float(600 + random.uniform(-10, 10), 3),
                "com_rotation_speed": fmt_float(600 + random.uniform(-10, 10), 3),
                "error_state": "0",
                "error_state_text": "0 正常",
                "cloud_frequency": fmt_float(10.0 + random.uniform(-0.2, 0.2), 3),
                "cloud_packet_loss_rate": fmt_float(random.uniform(0, 0.5), 3),
                "cloud_size": str(random.randint(18000, 22000)),
                "cloud_scan_num": str(random.randint(1, 100000)),
                "imu_frequency": fmt_float(200.0 + random.uniform(-2, 2), 3),
                "imu_packet_loss_rate": fmt_float(random.uniform(0, 0.2), 3),
                "imu_rpy": vector_text(
                    [
                        random.uniform(-0.02, 0.02),
                        random.uniform(-0.02, 0.02),
                        random.uniform(-0.1, 0.1),
                    ],
                    6,
                ),
                "imu_rpy_deg": "mock",
                "serial_recv_stamp": fmt_float(time.time(), 6),
                "serial_buffer_size": "0",
                "serial_buffer_read": "0",
            }

            self.status_signal.emit(status)
            self.msleep(200)

        self.log_signal.emit("mock LiDAR 状态线程已停止。")

    def _run_sdk2(self) -> None:
        try:
            from unitree_sdk2py.core.channel import ChannelSubscriber

            LidarState_ = import_lidarstate_class()

            iface = self.config.network_interface.strip()
            topic = self.config.lidar_state_topic.strip()

            ensure_dds_initialized(iface, self.log_signal.emit)

            self.log_signal.emit(f"订阅 LiDAR State：topic={topic}, idl=unitree_go/LidarState_")

            self._subscriber = ChannelSubscriber(topic, LidarState_)
            self._subscriber.Init(self._on_lidar_state, 10)

            self._last_msg_time = time.monotonic()
            warned_no_data = False

            while self._running:
                now = time.monotonic()

                if now - self._last_msg_time > 3.0 and not warned_no_data:
                    warned_no_data = True
                    self.log_signal.emit(
                        "超过 3 秒未收到 LiDAR State。请检查：topic、网卡、DDS、LiDAR 是否开启。"
                    )

                self.msleep(100)

            self.log_signal.emit("SDK2 LiDAR 状态线程已停止。")

        except ModuleNotFoundError as exc:
            self.error_signal.emit(
                "未找到 unitree_sdk2py。\n"
                "请确认已经安装 unitree_sdk2_python：\n"
                "cd ~/unitree_sdk2_python\n"
                "python -m pip install -e .\n\n"
                f"原始错误：{exc}"
            )

        except Exception as exc:
            self.error_signal.emit(f"SDK2 LiDAR State 读取失败：{exc}")

    def _on_lidar_state(self, msg: Any) -> None:
        if not self._running:
            return

        self._packet_count += 1
        now = time.monotonic()
        self._last_msg_time = now

        if now - self._last_emit_time < 0.1:
            return

        self._last_emit_time = now

        try:
            status = extract_lidar_state(
                msg=msg,
                topic=self.config.lidar_state_topic,
                packet_count=self._packet_count,
            )

            if self._running:
                self.status_signal.emit(status)

        except Exception as exc:
            self.error_signal.emit(f"解析 LiDAR State 失败：{exc}")


class MjpegStreamWorker(QThread):
    frame_signal = pyqtSignal(QImage)
    log_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self.url = url
        self._running = True
        self._response = None

    def stop(self) -> None:
        self._running = False

        response = getattr(self, "_response", None)
        if response is not None:
            try:
                response.close()
            except Exception:
                pass

    def run(self) -> None:
        """
        直接解析 MJPEG HTTP 流，不使用 cv2.VideoCapture。

        原因：
        1. cv2.VideoCapture 读取 HTTP/MJPEG 时容易缓存，表现为画面不实时或卡在首帧。
        2. opencv-python 还可能和 PyQt5 的 Qt 插件冲突。
        3. 这里直接从 HTTP 字节流中提取 JPEG 帧，实时性更稳定。
        """
        import urllib.request
        import socket

        buffer = b""

        try:
            self.log_signal.emit(f"正在打开 MJPEG 视频流：{self.url}")

            request = urllib.request.Request(
                self.url,
                headers={
                    "User-Agent": "H1-Vision-PyQt",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                    "Connection": "close",
                },
            )

            self._response = urllib.request.urlopen(request, timeout=8)

            self.log_signal.emit("MJPEG 视频流已连接。")

            while self._running:
                try:
                    chunk = self._response.read(4096)
                except socket.timeout:
                    continue
                except Exception as exc:
                    if self._running:
                        self.error_signal.emit(f"视频流读取中断：{exc}")
                    break

                if not self._running:
                    break

                if not chunk:
                    if self._running:
                        self.error_signal.emit("视频流连接已断开。")
                    break

                buffer += chunk

                start = buffer.find(b"\xff\xd8")
                end = buffer.find(b"\xff\xd9")

                if start != -1 and end != -1 and end > start:
                    jpg = buffer[start:end + 2]
                    buffer = buffer[end + 2:]

                    image = QImage.fromData(jpg, "JPG")

                    if not image.isNull() and self._running:
                        self.frame_signal.emit(image)

                if len(buffer) > 2 * 1024 * 1024:
                    buffer = buffer[-512 * 1024:]

        except Exception as exc:
            if self._running:
                self.error_signal.emit(f"视频流读取失败：{exc}")

        finally:
            try:
                if self._response is not None:
                    self._response.close()
            except Exception:
                pass

            self._response = None
            # self.log_signal.emit("视频流线程已停止。")

class MjpegRecorderWorker(QThread):
    log_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)
    done_signal = pyqtSignal(str, int)

    def __init__(self, url: str, save_path: str, fps: float = 20.0, parent=None):
        super().__init__(parent)
        self.url = url
        self.save_path = str(save_path)
        self.fps = float(fps)
        self._running = True
        self._response = None
        self._writer = None
        self._record_size = None
        self._frames_written = 0

    def stop(self) -> None:
        self._running = False

        response = getattr(self, "_response", None)
        if response is not None:
            try:
                response.close()
            except Exception:
                pass

    def run(self) -> None:
        import urllib.request
        import socket

        buffer = b""

        try:
            self.log_signal.emit(f"开始录制视频流：{self.url}")

            request = urllib.request.Request(
                self.url,
                headers={
                    "User-Agent": "H1-Vision-Recorder",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                    "Connection": "close",
                },
            )

            self._response = urllib.request.urlopen(request, timeout=8)

            while self._running:
                try:
                    chunk = self._response.read(4096)
                except socket.timeout:
                    continue
                except Exception as exc:
                    if self._running:
                        self.error_signal.emit(f"录制视频流读取中断：{exc}")
                    break

                if not self._running:
                    break

                if not chunk:
                    if self._running:
                        self.error_signal.emit("录制视频流连接已断开。")
                    break

                buffer += chunk

                start = buffer.find(b"\xff\xd8")
                end = buffer.find(b"\xff\xd9")

                if start != -1 and end != -1 and end > start:
                    jpg = buffer[start:end + 2]
                    buffer = buffer[end + 2:]

                    jpg_array = np.frombuffer(jpg, dtype=np.uint8)
                    frame_bgr = cv2.imdecode(jpg_array, cv2.IMREAD_COLOR)

                    if frame_bgr is None:
                        continue

                    self._write_frame(frame_bgr)

                if len(buffer) > 2 * 1024 * 1024:
                    buffer = buffer[-512 * 1024:]

        except Exception as exc:
            if self._running:
                self.error_signal.emit(f"录制视频流失败：{exc}")

        finally:
            try:
                if self._response is not None:
                    self._response.close()
            except Exception:
                pass

            self._response = None

            if self._writer is not None:
                self._writer.release()
                self._writer = None

            self.done_signal.emit(self.save_path, self._frames_written)

    def _write_frame(self, frame_bgr) -> None:
        h, w = frame_bgr.shape[:2]

        if self._writer is None:
            self._record_size = (w, h)

            save_path = Path(self.save_path)
            save_path.parent.mkdir(parents=True, exist_ok=True)

            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            self._writer = cv2.VideoWriter(
                str(save_path),
                fourcc,
                self.fps,
                self._record_size,
            )

            if not self._writer.isOpened():
                self._writer = None
                self._running = False
                self.error_signal.emit(f"无法创建视频文件：{save_path}")
                return

        if self._record_size != (w, h):
            frame_bgr = cv2.resize(frame_bgr, self._record_size)

        self._writer.write(frame_bgr)
        self._frames_written += 1



def is_finalized_mp4(path: Path) -> bool:
    """
    判断 MP4 是否基本完整，避免 OpenCV 打开未写完的文件并触发：
        moov atom not found

    正常停止录制后，MP4 文件会写入 moov 元数据；如果程序/相机/电源中断，
    文件可能存在但不可回放，应在列表里跳过。
    """
    try:
        path = Path(path)
        if path.suffix.lower() != ".mp4" or not path.exists() or not path.is_file():
            return False

        size = path.stat().st_size
        if size < 8 * 1024:
            return False

        with path.open("rb") as f:
            head = f.read(min(size, 2 * 1024 * 1024))
            if size > 2 * 1024 * 1024:
                f.seek(max(0, size - 2 * 1024 * 1024))
                tail = f.read(2 * 1024 * 1024)
            else:
                tail = b""

        sample = head + tail
        return b"ftyp" in sample[:4096] and b"moov" in sample

    except Exception:
        return False


class VideoPlaybackDialog(QDialog):
    """应用内 MP4 回放窗口，支持播放、暂停和拖动进度。"""

    def __init__(self, video_path: str, parent=None):
        super().__init__(parent)
        self.video_path = str(video_path)

        if not is_finalized_mp4(Path(self.video_path)):
            self.capture = None
            self.fps = 25.0
            self.frame_count = 0
        else:
            self.capture = cv2.VideoCapture(self.video_path)
            self.fps = self.capture.get(cv2.CAP_PROP_FPS) or 25.0
            self.frame_count = int(self.capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        self.current_frame = 0
        self._seeking = False

        self.setWindowTitle(f"录像回放 - {Path(self.video_path).name}")
        self.resize(960, 650)
        self.setMinimumSize(720, 480)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(9)

        title = QLabel(Path(self.video_path).name)
        title.setStyleSheet("font-size:18px; font-weight:900; color:#0f172a;")
        root.addWidget(title)

        self.video_label = QLabel("正在加载录像…")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setMinimumSize(640, 360)
        self.video_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.video_label.setStyleSheet(
            "background:#020617; color:#dbeafe; border:1px solid #294568; "
            "border-radius:12px; font-size:16px; font-weight:800;"
        )
        root.addWidget(self.video_label, stretch=1)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.play_btn = QPushButton("暂停")
        self.play_btn.setObjectName("PrimaryButton")
        self.play_btn.clicked.connect(self.toggle_playback)

        self.position_slider = QSlider(Qt.Horizontal)
        self.position_slider.setRange(0, max(0, self.frame_count - 1))
        self.position_slider.sliderPressed.connect(self._on_slider_pressed)
        self.position_slider.sliderReleased.connect(self._on_slider_released)
        self.position_slider.sliderMoved.connect(self.seek_frame)

        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setMinimumWidth(120)
        self.time_label.setAlignment(Qt.AlignCenter)

        controls.addWidget(self.play_btn)
        controls.addWidget(self.position_slider, stretch=1)
        controls.addWidget(self.time_label)
        root.addLayout(controls)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._read_next_frame)
        self.timer.start(max(15, int(1000.0 / max(1.0, self.fps))))

        if self.capture is None or not self.capture.isOpened():
            self.timer.stop()
            self.play_btn.setEnabled(False)
            self.video_label.setText("无法打开该录像文件")
        else:
            self._read_next_frame()

    @staticmethod
    def _format_seconds(seconds: float) -> str:
        seconds = max(0, int(seconds))
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    def toggle_playback(self) -> None:
        if self.timer.isActive():
            self.timer.stop()
            self.play_btn.setText("播放")
        else:
            if self.current_frame >= max(0, self.frame_count - 1):
                self.capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                self.current_frame = 0
            self.timer.start(max(15, int(1000.0 / max(1.0, self.fps))))
            self.play_btn.setText("暂停")

    def _read_next_frame(self) -> None:
        if self.capture is None or not self.capture.isOpened():
            return

        ok, frame = self.capture.read()
        if not ok:
            self.timer.stop()
            self.play_btn.setText("重新播放")
            return

        self.current_frame = int(self.capture.get(cv2.CAP_PROP_POS_FRAMES))
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, channels = frame_rgb.shape
        image = QImage(frame_rgb.data, w, h, channels * w, QImage.Format_RGB888).copy()
        pixmap = QPixmap.fromImage(image).scaled(
            self.video_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.video_label.setPixmap(pixmap)

        if not self._seeking:
            self.position_slider.setValue(min(self.current_frame, self.position_slider.maximum()))

        current_seconds = self.current_frame / max(1.0, self.fps)
        total_seconds = self.frame_count / max(1.0, self.fps)
        self.time_label.setText(
            f"{self._format_seconds(current_seconds)} / {self._format_seconds(total_seconds)}"
        )

    def _on_slider_pressed(self) -> None:
        self._seeking = True

    def _on_slider_released(self) -> None:
        self.seek_frame(self.position_slider.value())
        self._seeking = False

    def seek_frame(self, frame_number: int) -> None:
        if self.capture is None or not self.capture.isOpened():
            return
        frame_number = max(0, min(int(frame_number), max(0, self.frame_count - 1)))
        self.capture.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
        self.current_frame = frame_number
        if not self.timer.isActive():
            self._read_next_frame()

    def closeEvent(self, event) -> None:
        self.timer.stop()
        if self.capture is not None:
            self.capture.release()
        event.accept()


#新增 PCD 点云显示控件
def load_ascii_pcd_xyz_intensity(path: Path, max_points: int = 80000):
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"PCD 文件不存在：{path}")

    fields = []
    data_type = ""
    skiprows = 0
    total_points = 0

    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for index, line in enumerate(f):
            text = line.strip()

            if text.upper().startswith("FIELDS"):
                fields = text.split()[1:]

            elif text.upper().startswith("POINTS"):
                try:
                    total_points = int(text.split()[1])
                except Exception:
                    total_points = 0

            elif text.upper().startswith("DATA"):
                parts = text.split()
                data_type = parts[1].lower() if len(parts) > 1 else ""
                skiprows = index + 1
                break

    if data_type != "ascii":
        raise RuntimeError(
            f"当前内置读取器只支持 ASCII PCD。当前 DATA={data_type}。"
            "如果你的 PCD 是 binary，请先用 pcl_pcd2pcd 转成 ascii，或改用 open3d 读取。"
        )

    if not fields:
        raise RuntimeError("PCD 头部没有找到 FIELDS。")

    for required in ["x", "y", "z"]:
        if required not in fields:
            raise RuntimeError(f"PCD 缺少字段：{required}")

    raw = np.loadtxt(str(path), skiprows=skiprows, dtype=np.float32)

    if raw.ndim == 1:
        raw = raw.reshape(1, -1)

    original_count = raw.shape[0]

    if original_count > max_points:
        step = max(1, math.ceil(original_count / max_points))
        raw = raw[::step]

    x_index = fields.index("x")
    y_index = fields.index("y")
    z_index = fields.index("z")

    xyz = raw[:, [x_index, y_index, z_index]]

    intensity = None
    if "intensity" in fields:
        intensity = raw[:, fields.index("intensity")]

    return xyz, intensity, fields, total_points or original_count, original_count


# 这张图是机器人激光雷达构建出来的三维点云地图。
# 图中的每一个点代表激光雷达扫描到的一个空间位置，
#   X、Y 表示平面位置，Z 表示高度。
# 颜色表示激光回波强度，也就是雷达打到物体表面后返回信号的强弱。
#   颜色变化通常和物体材质、距离、入射角、反射能力有关，不代表物体类别，也不直接代表点云密度。

class PointCloudCanvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.ax = None
        self._center = None
        self._zoom = 1.0

        self._base_x_range = 1.0
        self._base_y_range = 1.0
        self._base_z_range = 1.0

        self._elev = 60
        self._azim = -90

        self._dragging = False
        self._press_x = 0
        self._press_y = 0
        self._press_elev = self._elev
        self._press_azim = self._azim

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        self.figure = Figure(figsize=(6, 4))
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        layout.addWidget(self.canvas)
        self.setLayout(layout)

        self.canvas.mpl_connect("button_press_event", self._on_mouse_press)
        self.canvas.mpl_connect("motion_notify_event", self._on_mouse_move)
        self.canvas.mpl_connect("button_release_event", self._on_mouse_release)

        self.show_message("尚未加载点云地图")

    def show_message(self, text: str) -> None:
        self.figure.clear()
        self.ax = self.figure.add_subplot(111)
        self.ax.axis("off")
        self.ax.text(
            0.5,
            0.5,
            text,
            ha="center",
            va="center",
            fontsize=13,
            color="#334155",
            wrap=True,
        )
        self.canvas.draw_idle()

    def plot_pcd(self, path: str):
        xyz, intensity, fields, total_points, original_count = load_ascii_pcd_xyz_intensity(
            Path(path)
        )

        x = xyz[:, 0]
        y = xyz[:, 1]
        z = xyz[:, 2]

        self.figure.clear()
        self.ax = self.figure.add_subplot(111, projection="3d")

        # 必须保留 intensity 图例
        if intensity is not None:
            color_value = intensity
            color_label = "intensity"
        else:
            color_value = z
            color_label = "Z "

        scatter = self.ax.scatter(
            x,
            y,
            z,
            c=color_value,
            s=1,
            cmap="viridis",
            depthshade=False,
        )


    
        # 关键：加 colorbar 图例，并让它离 3D 图远一点
        cbar = self.figure.colorbar(
            scatter,
            ax=self.ax,
            shrink=0.68,
            pad=0.13,        # 越大，图例离 3D 图越远
            fraction=0.035,  # 图例宽度，越小越细
            aspect=25,
        )

        cbar.set_label(color_label, labelpad=10)

        self.ax.set_title("3D Point Cloud Map", pad=8)
        
        self.ax.set_xlabel("X", labelpad=12)
        self.ax.set_ylabel("Y", labelpad=12)
        # Z 轴标签单独加大间距
        self.ax.set_zlabel("Z", labelpad=22)
        self.ax.zaxis.labelpad = 22

        # # 可选：让 Z 标签不跟着轴旋转，显示更稳定
        # self.ax.zaxis.set_rotate_label(False)
        # self.ax.zaxis.label.set_rotation(0)

        x_min, x_max = float(np.min(x)), float(np.max(x))
        y_min, y_max = float(np.min(y)), float(np.max(y))
        z_min, z_max = float(np.min(z)), float(np.max(z))

        x_mid = (x_min + x_max) / 2.0
        y_mid = (y_min + y_max) / 2.0
        z_mid = (z_min + z_max) / 2.0

        x_range = max(x_max - x_min, 1e-6)
        y_range = max(y_max - y_min, 1e-6)
        z_range = max(z_max - z_min, 1e-6)

        # 保存原始中心点和三个方向的基础范围
        self._center = (x_mid, y_mid, z_mid)

        padding = 1.10 # 变大就是坐标范围变大，点云看起来更小，反之则更大
        self._base_x_range = x_range * padding
        self._base_y_range = y_range * padding
        self._base_z_range = z_range * padding

        # Z 太扁时给一点最小显示范围，否则高度轴会挤在一起
        xy_max = max(self._base_x_range, self._base_y_range)
        self._base_z_range = max(self._base_z_range, xy_max * 0.08)
        
        # 初始就使用和鼠标拖动/缩放一样的显示逻辑
        self._zoom = 0.85

        self._elev = 60
        self._azim = -90

        self._apply_view()

        self.figure.subplots_adjust(
            left=0.02, 
            right=0.78, 
            bottom=0.02, 
            top=0.92)
        
        self.canvas.draw_idle()

        log_info = (
            f"已加载：{Path(path).name} | "
            f"原始点数：{original_count} | "
            f"显示点数：{len(xyz)} | "
            # f"字段：{', '.join(fields)} | "
            f"图例：{color_label}"
        )

        return log_info

    def zoom_in(self) -> None:
        if self.ax is None or self._center is None:
            return

        self._zoom *= 0.8
        self._zoom = max(0.03, self._zoom)

        self._apply_view()
        self.canvas.draw_idle()

    def zoom_out(self) -> None:
        if self.ax is None or self._center is None:
            return

        self._zoom *= 1.25
        self._zoom = min(30.0, self._zoom)

        self._apply_view()
        self.canvas.draw_idle()

    def save_current_view(self) -> str:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "保存当前点云视角",
            str(APP_DIR / "pointcloud_view.png"),
            "PNG Image (*.png);;JPEG Image (*.jpg);;PDF File (*.pdf);;All Files (*)",
        )

        if not path:
            return ""

        self.figure.savefig(path, dpi=200, bbox_inches="tight")
        return path

    def _apply_view(self) -> None:
        if self.ax is None or self._center is None:
            return

        x_mid, y_mid, z_mid = self._center

        x_view_range = self._base_x_range * self._zoom
        y_view_range = self._base_y_range * self._zoom
        z_view_range = self._base_z_range * self._zoom

        # 关键：这里不是改点大小，而是改坐标轴范围
        self.ax.set_xlim(
            x_mid - x_view_range / 2.0,
            x_mid + x_view_range / 2.0,
        )
        self.ax.set_ylim(
            y_mid - y_view_range / 2.0,
            y_mid + y_view_range / 2.0,
        )
        self.ax.set_zlim(
            z_mid - z_view_range / 2.0,
            z_mid + z_view_range / 2.0,
        )

        # 缩放后刻度也重新计算
        self.ax.xaxis.set_major_locator(MaxNLocator(nbins=5))
        self.ax.yaxis.set_major_locator(MaxNLocator(nbins=5))
        self.ax.zaxis.set_major_locator(MaxNLocator(nbins=4))

        self.ax.tick_params(axis="both", labelsize=8, pad=2)

        # 3D 坐标盒比例也跟着当前 X/Y/Z 范围更新
        if hasattr(self.ax, "set_box_aspect"):
            self.ax.set_box_aspect([
                x_view_range,
                y_view_range,
                z_view_range,
            ])

        self.ax.view_init(elev=self._elev, azim=self._azim)

    def _on_mouse_press(self, event) -> None:
        if self.ax is None:
            return

        if event.button != 1:
            return

        self._dragging = True
        self._press_x = event.x
        self._press_y = event.y
        self._press_elev = self._elev
        self._press_azim = self._azim

    def _on_mouse_move(self, event) -> None:
        if not self._dragging or self.ax is None:
            return

        dx = event.x - self._press_x
        dy = event.y - self._press_y

        self._azim = self._press_azim - dx * 0.4
        self._elev = self._press_elev - dy * 0.4

        self._elev = max(-89, min(89, self._elev))

        self._apply_view()
        self.canvas.draw_idle()

    def _on_mouse_release(self, event) -> None:
        self._dragging = False


class H1RobotClient(QObject):
    log_signal = pyqtSignal(str)
    state_signal = pyqtSignal(bool)
    status_signal = pyqtSignal(dict)

    def __init__(self):
        super().__init__()

        self.connected = False
        self.connecting = False
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

        if self.connecting:
            self.log_signal.emit("机器人正在连接中，请稍候。")
            return

        self.config = config

        self.log_signal.emit(
            f"正在连接 H1：protocol={config.protocol}, ip={config.robot_ip}, "
            f"iface={config.network_interface}, topic={config.lowstate_topic}, idl={config.lowstate_idl}"
        )

        if config.protocol in ("mock", "sdk2"):
            self.connected = False
            self.connecting = True
            self.state_signal.emit(False)

            self.status_worker = LowStateWorker(config)

            self.status_worker.ready_signal.connect(self._on_status_worker_ready)
            self.status_worker.fatal_signal.connect(self._on_status_worker_fatal)

            self.status_worker.status_signal.connect(self.status_signal.emit)
            self.status_worker.log_signal.connect(self.log_signal.emit)

            # 普通错误只写日志，例如解析 LowState 失败
            self.status_worker.error_signal.connect(self.log_signal.emit)

            self.status_worker.finished.connect(self._on_status_worker_finished)
            self.status_worker.start()

            self.log_signal.emit("状态读取线程已启动，等待 SDK2 初始化成功。")
            return

        self.connected = False
        self.connecting = False
        self.state_signal.emit(False)
        self.log_signal.emit("当前程序仅支持 mock/sdk2 状态读取，请在“系统连接”页面选择可用方式。")
    
    def disconnect_robot(self) -> None:
        if not self.connected and not self.connecting and self.status_worker is None:
            self.log_signal.emit("机器人当前未连接。")
            return

        self.connecting = False

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

    def _on_status_worker_ready(self) -> None:
        self.connecting = False
        self.connected = True
        self.state_signal.emit(True)
        self.log_signal.emit("SDK2 / 状态读取初始化成功，连接状态已确认。")


    def _on_status_worker_fatal(self, text: str) -> None:
        self.log_signal.emit(text)

        self.connecting = False
        self.connected = False
        self.state_signal.emit(False)

        if self.status_worker is not None:
            self.status_worker.stop()


    def _on_status_worker_finished(self) -> None:
        if self.connecting:
            self.connecting = False
            self.connected = False
            self.state_signal.emit(False)

        if not self.connected:
            self.status_worker = None



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

        title = QLabel("H1 机器人控制面板")
        title.setObjectName("TitleLabel")
        title.setAlignment(Qt.AlignCenter)

        subtitle = QLabel("机器人视觉感知 · 实时画面 · 状态监测")
        subtitle.setObjectName("SubtitleLabel")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setWordWrap(True)

        card = QWidget()
        card.setObjectName("LoginCard")

        card_layout = QVBoxLayout()
        card_layout.setContentsMargins(38, 34, 38, 34)
        # 不完全依赖全局 spacing，后面手动控制每组间距
        card_layout.setSpacing(6)


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
        # card_layout.addSpacing(8)
        card_layout.addWidget(self.username_edit)

        card_layout.addSpacing(25)

        card_layout.addWidget(pass_label)
        # card_layout.addSpacing(8)
        card_layout.addWidget(self.password_edit)

        card_layout.addSpacing(22)
        card_layout.addWidget(self.show_password_box)

        card_layout.addSpacing(22)
        card_layout.addLayout(button_row)

        card_layout.addSpacing(16)
        card_layout.addWidget(tip)


        card.setLayout(card_layout)

        footer = QLabel("H1 机器人视觉监控平台 · 请确认机器人网络连接正常")
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
                spacing: 12px;
                padding-top: 2px;
                padding-bottom: 2px;
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


class InteractiveTerminalEdit(QTextEdit):
    input_signal = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setReadOnly(False)
        self.setUndoRedoEnabled(False)
        self.setAcceptRichText(False)
        self.setFocusPolicy(Qt.StrongFocus)

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()
        seq = ""

        if modifiers & Qt.ControlModifier and key == Qt.Key_C:
            seq = "\x03"
        elif modifiers & Qt.ControlModifier and key == Qt.Key_D:
            seq = "\x04"
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            seq = "\n"
        elif key == Qt.Key_Backspace:
            seq = "\x7f"
        elif key == Qt.Key_Tab:
            seq = "\t"
        elif key == Qt.Key_Escape:
            seq = "\x1b"
        elif key == Qt.Key_Up:
            seq = "\x1b[A"
        elif key == Qt.Key_Down:
            seq = "\x1b[B"
        elif key == Qt.Key_Right:
            seq = "\x1b[C"
        elif key == Qt.Key_Left:
            seq = "\x1b[D"
        else:
            seq = event.text()

        if seq:
            self.input_signal.emit(seq)
            event.accept()
            return

        super().keyPressEvent(event)

    def append_remote_text(self, text: str) -> None:
        self.moveCursor(self.textCursor().End)
        self.insertPlainText(text)
        self.moveCursor(self.textCursor().End)


class NavigationWindow(QDialog):
    log_signal = pyqtSignal(str)

    def __init__(
        self,
        robot_ip: str = "192.168.123.162",
        robot_user: str = "unitree",
        robot_password: str = "Unitree0408",
        remote_dir: str = "ws/unitree_slam/build",
        remote_iface: str = "eth0",
        parent=None,
    ):
        super().__init__(parent)

        self.process: Optional[QProcess] = None
        self.terminal_buffer = ""


        self.setWindowTitle("H1 导航建图窗口")
        self.setMinimumSize(820, 560)
        self.resize(920, 620)
        self.setWindowModality(Qt.NonModal)

        self.robot_ip = robot_ip
        self.robot_user = robot_user
        self.robot_password = robot_password
        self.remote_dir = remote_dir
        self.remote_iface = remote_iface

        self._build_ui()
        self._apply_style()

    def _build_ui(self) -> None:
        root = QVBoxLayout()
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        config_group = QGroupBox("导航建图 SSH 配置")
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)

        self.nav_ip_edit = QLineEdit(self.robot_ip)
        self.nav_user_edit = QLineEdit(self.robot_user)

        self.nav_password_edit = QLineEdit(self.robot_password)
        self.nav_password_edit.setEchoMode(QLineEdit.Password)

        self.nav_remote_dir_edit = QLineEdit(self.remote_dir)
        self.nav_iface_edit = QLineEdit(self.remote_iface)

        form.addRow("机器人 IP：", self.nav_ip_edit)
        form.addRow("SSH 用户：", self.nav_user_edit)
        form.addRow("SSH 密码 / sudo 密码：", self.nav_password_edit)
        form.addRow("远程目录：", self.nav_remote_dir_edit)
        form.addRow("导航网卡：", self.nav_iface_edit)

        config_group.setLayout(form)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.start_nav_btn = QPushButton("启动导航建图")
        self.stop_nav_btn = QPushButton("停止导航程序")
        self.ifconfig_btn = QPushButton("查看远程 ifconfig")
        self.clear_nav_log_btn = QPushButton("清空输出")

        self.start_nav_btn.clicked.connect(self.start_navigation)
        self.stop_nav_btn.clicked.connect(self.stop_navigation)
        self.ifconfig_btn.clicked.connect(self.run_ifconfig)
        self.clear_nav_log_btn.clicked.connect(self.clear_output)

        btn_row.addWidget(self.start_nav_btn)
        btn_row.addWidget(self.stop_nav_btn)
        btn_row.addWidget(self.ifconfig_btn)
        btn_row.addStretch()
        btn_row.addWidget(self.clear_nav_log_btn)

  
        self.nav_output = InteractiveTerminalEdit()
        self.nav_output.input_signal.connect(self.send_stdin)
        self.nav_output.setMinimumHeight(280)

        root.addWidget(config_group)
        root.addLayout(btn_row)
        root.addWidget(self.nav_output, stretch=1)

        self.setLayout(root)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QDialog {
                background-color: #eef2f7;
                font-family: "Microsoft YaHei", "Noto Sans CJK SC", Arial;
            }

            QGroupBox {
                font-weight: 800;
                border: 1px solid #cbd5e1;
                border-radius: 12px;
                margin-top: 10px;
                padding: 12px;
                background-color: white;
            }

            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: #1e293b;
            }

            QLineEdit {
                min-height: 32px;
                border-radius: 8px;
                border: 1px solid #cbd5e1;
                padding-left: 10px;
                padding-right: 10px;
                background-color: white;
                color: #0f172a;
            }

            QPushButton {
                min-height: 32px;
                border-radius: 8px;
                padding: 5px 14px;
                background-color: #f1f5f9;
                border: 1px solid #cbd5e1;
                color: #0f172a;
                font-weight: 700;
            }

            QPushButton:hover {
                background-color: #dbeafe;
                border: 1px solid #93c5fd;
            }

            QTextEdit {
                border: 1px solid #1e293b;
                border-radius: 10px;
                background-color: #020617;
                color: #dbeafe;
                font-family: Consolas, "Courier New";
                font-size: 13px;
                padding: 6px;
            }
            """
        )

    def append_output(self, text: str) -> None:
        # text = clean_terminal_output(text)

        # if not text.strip():
        #     return
        self.nav_output.append(f"[{now_text()}] {text}")

    def send_stdin(self, text: str) -> None:
        if self.process is None:
            return

        if self.process.state() == QProcess.NotRunning:
            return

        self.process.write(text.encode("utf-8", errors="ignore"))
        self.process.waitForBytesWritten(50)


    def clear_output(self) -> None:
        self.nav_output.clear()

    def _ssh_prefix(self) -> str:
        password = self.nav_password_edit.text()
        user = self.nav_user_edit.text().strip()
        ip = self.nav_ip_edit.text().strip()

        return (
            "sshpass -p "
            + shlex.quote(password)
            + " ssh -tt "
            + "-o StrictHostKeyChecking=no "
            + "-o UserKnownHostsFile=/dev/null "
            + f"{shlex.quote(user)}@{shlex.quote(ip)}"
        )

    def _run_bash_command(self, command: str, title: str) -> None:
        if self.process is not None and self.process.state() != QProcess.NotRunning:
            QMessageBox.information(self, "提示", "已有导航/SSH 命令正在运行，请先停止。")
            return

        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.MergedChannels)

        self.process.readyReadStandardOutput.connect(self._on_process_output)
        self.process.readyReadStandardError.connect(self._on_process_output)
        self.process.finished.connect(self._on_process_finished)
        self.process.errorOccurred.connect(self._on_process_error)

        self.append_output(f"开始执行：{title}")
        self.append_output(command)
        self.log_signal.emit(f"导航窗口执行：{title}")

        self.process.start("bash", ["-lc", command])
        self.nav_output.setFocus()


    def start_navigation(self) -> None:
        ip = self.nav_ip_edit.text().strip()
        user = self.nav_user_edit.text().strip()
        password = self.nav_password_edit.text()
        remote_dir = self.nav_remote_dir_edit.text().strip()
        iface = self.nav_iface_edit.text().strip()

        if not ip or not user or not password or not remote_dir or not iface:
            QMessageBox.warning(self, "配置错误", "IP、用户、密码、远程目录、导航网卡都不能为空。")
            return

        remote_cmd = (
            "set -e; "
            "echo '[remote] user='$(whoami); "
            "echo '[remote] pwd='$(pwd); "
            f"echo '[remote] target_dir={remote_dir}'; "
            f"cd {shlex.quote(remote_dir)} || "
            "{ echo '远程目录不存在，请检查远程目录配置'; exit 1; }; "
            "export TERM=xterm; "
            "export NO_COLOR=1; "
            "export LD_LIBRARY_PATH=$PWD/../unitree_robotics/lib/$(uname -m):$LD_LIBRARY_PATH; "

            # 关键：先单独验证 sudo 密码，只让 sudo -v 消耗密码管道
            f"printf '%s\\n' {shlex.quote(password)} | sudo -S -p '' -v; "

            # 关键：真正运行 demo_h1 时，不能再接密码管道
            # 这样 demo_h1 的 stdin 才会连接到 ssh -tt 分配的伪终端
            f"exec sudo -n ./demo_h1 {shlex.quote(iface)}"
        )


        command = (
            "command -v sshpass >/dev/null 2>&1 || "
            "{ echo '本机缺少 sshpass，请先执行：sudo apt install sshpass'; exit 127; }; "
            + self._ssh_prefix()
            + " "
            + shlex.quote(remote_cmd)
        )

        self._run_bash_command(command, "启动 H1 导航建图 demo_h1")

    def run_ifconfig(self) -> None:
        remote_cmd = "ifconfig"
        command = (
            "command -v sshpass >/dev/null 2>&1 || "
            "{ echo '本机缺少 sshpass，请先执行：sudo apt install sshpass'; exit 127; }; "
            + self._ssh_prefix()
            + " "
            + shlex.quote(remote_cmd)
        )

        self._run_bash_command(command, "查看远程 ifconfig")

    def stop_navigation(self) -> None:
        password = self.nav_password_edit.text()
        iface = self.nav_iface_edit.text().strip()

        if self.process is not None and self.process.state() != QProcess.NotRunning:
            self.append_output("正在向 SSH 进程发送 Ctrl+C ...")
            try:
                self.process.write(b"\x03")
                self.process.waitForBytesWritten(500)
            except Exception:
                pass

            self.process.terminate()

            if not self.process.waitForFinished(1500):
                self.process.kill()

      
        remote_cmd = (
            f"printf '%s\\n' {shlex.quote(password)} | "
            "sudo -S -p '' pkill -9 -f '[d]emo_h1'"
        )


        stop_command = (
            "command -v sshpass >/dev/null 2>&1 || exit 0; "
            + self._ssh_prefix()
            + " "
            + shlex.quote(remote_cmd)
        )

        QProcess.startDetached("bash", ["-lc", stop_command])

        self.append_output("已发送停止 demo_h1 的命令。")
        self.log_signal.emit("已停止导航建图 demo_h1。")
    


    def _on_process_output(self) -> None:
        if self.process is None:
            return

        data = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="ignore")
        data += bytes(self.process.readAllStandardError()).decode("utf-8", errors="ignore")

        self.terminal_buffer += data

        data, self.terminal_buffer = clean_terminal_stream(
            self.terminal_buffer,
            flush=False,
        )

        if not data.strip():
            return


        if not data.endswith("\n"):
            data += "\n"

        self.nav_output.append_remote_text(data)

    def _on_process_finished(self, exit_code: int, exit_status) -> None:
        data = ""
        if self.terminal_buffer:
            data, self.terminal_buffer = clean_terminal_stream(
                self.terminal_buffer,
                flush=True,
            )

        if data.strip():
            # self.nav_output.moveCursor(self.nav_output.textCursor().End)
            if not data.endswith("\n"):
                data += "\n"

            # self.nav_output.insertPlainText(data)
            # self.nav_output.moveCursor(self.nav_output.textCursor().End)
            self.nav_output.append_remote_text(data)

        self.append_output(f"进程已结束，exit_code={exit_code}")
        self.log_signal.emit(f"导航建图进程已结束，exit_code={exit_code}")

    def _on_process_error(self, error) -> None:
        self.append_output(f"进程错误：{error}")
        self.log_signal.emit(f"导航建图进程错误：{error}")

    def closeEvent(self, event) -> None:
        if self.process is not None and self.process.state() != QProcess.NotRunning:
            self.stop_navigation()

        super().closeEvent(event)


class MainWindow(QMainWindow):

    PARAM_DEFS = [
    {
        "key": "control.mode",
        "name": "控制模式",
        "default": "idle",
        "desc": "控制模式：空闲 / 手动 / 自动",
        "writable": True,
    },
    {
        "key": "motion.max_speed",
        "name": "最大运动速度",
        "default": 0.5,
        "desc": "最大运动速度，建议先小范围调试",
        "writable": True,
    },
    {
        "key": "safety.torque_limit",
        "name": "力矩限制",
        "default": 0.6,
        "desc": "力矩限制，建议仅管理员修改",
        "writable": True,
    },
    {
        "key": "network.timeout_ms",
        "name": "网络超时时间",
        "default": 2000,
        "desc": "网络通信超时时间，单位 ms",
        "writable": True,
    },
    {
        "key": "sensor.lidar_enable",
        "name": "启用激光雷达",
        "default": True,
        "desc": "是否启用 3D 激光雷达",
        "writable": True,
    },
    {
        "key": "sensor.depth_camera_enable",
        "name": "启用深度相机",
        "default": True,
        "desc": "是否启用深度相机",
        "writable": True,
    },
    {
        "key": "body.height_offset",
        "name": "机身高度偏移",
        "default": 0.0,
        "desc": "机身高度偏移，单位 m",
        "writable": True,
    },
]


    def __init__(self, user_profile: Dict[str, str]):
        super().__init__()

        self.user_profile = user_profile
        self.client = H1RobotClient()
        self.lidar_worker: Optional[LidarStateWorker] = None
     
        self.camera_worker: Optional[MjpegStreamWorker] = None
        self.realsense_process: Optional[QProcess] = None
        self.realsense_stop_process: Optional[QProcess] = None
        self.realsense_output = ""
        self.realsense_stop_output = ""
        self._start_camera_after_remote_start = False
        self._closing_app = False

        # 领导驾驶舱、事件时间轴和演示报告所需的运行态数据。
        self.latest_robot_status: Dict[str, Any] = {}
        self.event_history: List[Dict[str, str]] = []
        self.current_task = {
            "name": "系统演示准备",
            "stage": "准备中",
            "progress": 0,
            "updated_at": now_text(),
        }
        self._last_robot_packet_time: Optional[float] = None
        self._last_camera_image: Optional[QImage] = None
        self._fps_frame_count = 0
        self._fps_last_tick = time.monotonic()
        self._current_video_fps = 0.0
        self._network_latency_ms: Optional[float] = None
        self._ping_process: Optional[QProcess] = None
        self._ping_output = ""
        self.last_report_path: Optional[Path] = None


        self.client.log_signal.connect(self.append_log)
        self.client.state_signal.connect(self.on_connection_state_changed)
        self.client.status_signal.connect(self.update_robot_status)

        self.setWindowTitle("H1 机器人视觉监控平台")

        self._camera_auto_start = False

        self._build_ui()
        self._apply_main_style()
        self._set_initial_window_size()
        self._load_config()

        self._header_clock_timer = QTimer(self)
        self._header_clock_timer.timeout.connect(self._update_header_clock)
        self._header_clock_timer.start(1000)
        self._update_header_clock()

        self._dashboard_timer = QTimer(self)
        self._dashboard_timer.timeout.connect(self._refresh_dashboard_metrics)
        self._dashboard_timer.start(1000)

        self._ping_timer = QTimer(self)
        self._ping_timer.timeout.connect(self._start_network_ping)
        self._ping_timer.start(3000)
        QTimer.singleShot(1200, self._start_network_ping)

        self.append_log(
            f"用户 {user_profile['display_name']} 已登录，角色：{user_profile['role']}"
        )

        # 领导展示模式：登录后直接进入实时画面，并自动尝试启动相机。
        QTimer.singleShot(800, self._auto_start_presentation)

    def _fast_close_cleanup(self) -> None:
        """
        关闭窗口专用清理：
        不做长时间 wait，不弹窗，不更新 UI，避免 GNOME 判断程序无响应。
        """

        self._closing_app = True

        # 1. 停本地视频线程
        worker = getattr(self, "camera_worker", None)
        self.camera_worker = None

        if worker is not None:
            try:
                worker.frame_signal.disconnect()
            except Exception:
                pass

            try:
                worker.log_signal.disconnect()
            except Exception:
                pass

            try:
                worker.error_signal.disconnect()
            except Exception:
                pass

            try:
                worker.stop()
            except Exception:
                pass

            # 关闭窗口时最多等 800ms，不要等 5 秒
            try:
                if not worker.wait(800):
                    worker.terminate()
                    worker.wait(300)
            except Exception:
                pass

        # 2. 停启动/停止 RealSense 的 QProcess
        for process_name in ("realsense_process", "realsense_stop_process", "_ping_process"):
            process = getattr(self, process_name, None)

            if process is not None:
                try:
                    if process.state() != QProcess.NotRunning:
                        process.kill()
                except Exception:
                    pass

                try:
                    process.deleteLater()
                except Exception:
                    pass

                setattr(self, process_name, None)

        # 3. 静默停止机器人端 RealSense 服务
        try:
            self.stop_remote_realsense_service(detached=True)
        except Exception:
            pass

        # 4. 停 LiDAR 线程，最多等 500ms
        lidar_worker = getattr(self, "lidar_worker", None)

        if lidar_worker is not None:
            try:
                lidar_worker.stop()
            except Exception:
                pass

            try:
                lidar_worker.wait(500)
            except Exception:
                pass

            self.lidar_worker = None

        # 5. 停 LowState 线程，最多等 500ms
        try:
            status_worker = getattr(self.client, "status_worker", None)

            if status_worker is not None:
                try:
                    status_worker.stop()
                except Exception:
                    pass

                try:
                    status_worker.wait(500)
                except Exception:
                    pass

                self.client.status_worker = None

            self.client.connected = False
            self.client.connecting = False

        except Exception:
            pass


    def _set_initial_window_size(self) -> None:
        screen = QApplication.primaryScreen()

        if screen:
            rect = screen.availableGeometry()

            width = min(1180, int(rect.width() * 0.88))
            height = min(820, int(rect.height() * 0.86))

            width = max(860, width)
            height = max(560, height)

            self.resize(width, height)
            self.move(
                rect.x() + (rect.width() - width) // 2,
                rect.y() + (rect.height() - height) // 2,
            )
        else:
            self.resize(980, 660)

        self.setMinimumSize(760, 500)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def _apply_main_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow {
                background-color: #edf2f7;
                font-family: "Microsoft YaHei", "Noto Sans CJK SC", Arial;
            }

            QWidget {
                font-size: 14px;
                color: #172033;
            }

            #PresentationHeader {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #0b1736, stop:0.58 #102a56, stop:1 #0c4a6e
                );
                border: 1px solid #173b69;
                border-radius: 16px;
            }

            #HeaderTitle {
                color: #ffffff;
                font-size: 23px;
                font-weight: 900;
            }

            #HeaderSubtitle {
                color: #bfd7f7;
                font-size: 13px;
            }

            #HeaderClock {
                color: #e0f2fe;
                font-size: 14px;
                font-weight: 700;
                padding: 7px 10px;
            }

            #LiveTitle {
                color: #0f172a;
                font-size: 21px;
                font-weight: 900;
            }

            #LiveSubtitle {
                color: #64748b;
                font-size: 13px;
            }

            #CameraStatusPanel {
                background-color: #f8fafc;
                border: 1px solid #d9e2ec;
                border-radius: 10px;
            }

            #CockpitPanel {
                background-color: #f8fafc;
                border: 1px solid #d7e0ea;
                border-radius: 14px;
            }

            #CockpitTitle {
                color: #0f172a;
                font-size: 18px;
                font-weight: 900;
            }

            #CockpitSubtitle {
                color: #64748b;
                font-size: 12px;
            }

            #MetricCard {
                background-color: white;
                border: 1px solid #dbe4ee;
                border-radius: 12px;
            }

            #MetricLabel {
                color: #64748b;
                font-size: 12px;
                font-weight: 700;
            }

            #MetricValue {
                color: #0f2a4a;
                font-size: 23px;
                font-weight: 900;
            }

            #MetricHint {
                color: #94a3b8;
                font-size: 11px;
            }

            QProgressBar {
                min-height: 20px;
                border: 1px solid #cbd5e1;
                border-radius: 9px;
                background-color: #eef2f7;
                text-align: center;
                color: #0f172a;
                font-weight: 800;
            }

            QProgressBar::chunk {
                border-radius: 8px;
                background-color: #1769e0;
            }

            QTabWidget::pane {
                border: none;
                background-color: transparent;
                top: -1px;
            }

            QTabBar::tab {
                min-width: 112px;
                min-height: 34px;
                padding: 6px 15px;
                margin: 2px 4px 5px 0;
                border-radius: 10px;
                background-color: #dfe7f1;
                color: #475569;
                font-weight: 800;
            }

            QTabBar::tab:selected {
                background-color: #1769e0;
                color: white;
            }

            QTabBar::tab:hover:!selected {
                background-color: #cfe1f8;
                color: #174ea6;
            }

            QGroupBox {
                font-weight: 900;
                border: 1px solid #d7e0ea;
                border-radius: 14px;
                margin-top: 11px;
                padding: 13px;
                background-color: white;
            }

            QGroupBox::title {
                subcontrol-origin: margin;
                left: 14px;
                padding: 0 7px;
                color: #20324d;
                background-color: transparent;
            }

            QPushButton {
                min-height: 32px;
                border-radius: 9px;
                padding: 5px 15px;
                background-color: #f1f5f9;
                border: 1px solid #cbd5e1;
                color: #172033;
                font-weight: 800;
            }

            QPushButton:hover {
                background-color: #e0edff;
                border-color: #8db9f4;
            }

            QPushButton:pressed {
                background-color: #cfe2ff;
            }

            QPushButton:disabled {
                background-color: #edf1f5;
                color: #9aa7b5;
                border-color: #dbe2e9;
            }

            #PrimaryButton {
                background-color: #1769e0;
                border-color: #1769e0;
                color: white;
            }

            #PrimaryButton:hover {
                background-color: #0f5bcc;
                border-color: #0f5bcc;
            }

            #DangerButton {
                background-color: #e5484d;
                border-color: #d63c42;
                color: white;
            }

            #DangerButton:hover {
                background-color: #cf353b;
            }

            #HeaderButton {
                background-color: rgba(255, 255, 255, 0.12);
                border: 1px solid rgba(255, 255, 255, 0.28);
                color: white;
            }

            #HeaderButton:hover {
                background-color: rgba(255, 255, 255, 0.22);
            }

            QLineEdit, QSpinBox, QComboBox {
                min-height: 34px;
                border-radius: 9px;
                border: 1px solid #cbd5e1;
                padding-left: 10px;
                padding-right: 10px;
                background-color: white;
                color: #172033;
                selection-background-color: #1769e0;
                selection-color: white;
            }

            QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
                border: 1px solid #1769e0;
            }

            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 28px;
                border-left: 1px solid #cbd5e1;
                border-top-right-radius: 8px;
                border-bottom-right-radius: 8px;
                background-color: #f8fafc;
            }

            QComboBox QAbstractItemView {
                background-color: white;
                color: #172033;
                border: 1px solid #94a3b8;
                selection-background-color: #dbeafe;
                selection-color: #172033;
                outline: none;
                padding: 4px;
            }

            QTextEdit {
                border: 1px solid #253858;
                border-radius: 10px;
                background-color: #071224;
                color: #dbeafe;
                font-family: Consolas, "Courier New";
                font-size: 12px;
                padding: 6px;
            }

            QTableWidget {
                background-color: white;
                border: 1px solid #d7e0ea;
                border-radius: 11px;
                color: #172033;
                gridline-color: #e6edf4;
                selection-background-color: #dbeafe;
                selection-color: #172033;
            }

            QTableWidget::item {
                padding: 5px;
            }

            QHeaderView::section {
                background-color: #eef3f8;
                padding: 7px;
                border: none;
                border-right: 1px solid #e2e8f0;
                border-bottom: 1px solid #d7e0ea;
                font-weight: 900;
                color: #20324d;
            }

            QSplitter::handle {
                background-color: transparent;
            }

            QSplitter::handle:vertical {
                height: 5px;
            }

            QScrollArea {
                border: none;
                background-color: transparent;
            }

            QStatusBar {
                background-color: #f8fafc;
                border-top: 1px solid #d7e0ea;
                color: #475569;
            }
            """
        )


    def _build_ui(self) -> None:
        central = QWidget()
        root_layout = QVBoxLayout()
        root_layout.setContentsMargins(10, 10, 10, 6)
        root_layout.setSpacing(8)

        # 先创建连接页，确保相机页可读取 IP 配置；展示顺序仍以实时画面为首屏。
        connection_page = self._make_scroll_page(self._build_connection_tab())
        camera_page = self._build_camera_tab()
        status_page = self._build_status_tab()
        replay_page = self._build_replay_report_tab()
        navigation_page = self._make_scroll_page(self._build_navigation_tab())
        lidar_page = self._build_lidar_tab()

        root_layout.addWidget(self._build_presentation_header())

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(camera_page, "实时画面")
        self.tabs.addTab(status_page, "运行状态")
        self.tabs.addTab(replay_page, "回放与报告")
        self.tabs.addTab(lidar_page, "点云地图")
        self.tabs.addTab(navigation_page, "建图导航")
        self.tabs.addTab(connection_page, "系统连接")
        self.tabs.setCurrentIndex(0)

        # 领导展示版彻底移除底部“系统运行信息/设备日志”终端面板。
        # 技术日志不再占用页面空间；重要演示节点进入“回放与报告”的事件时间轴。
        self.tabs.currentChanged.connect(self._on_main_tab_changed)

        root_layout.addWidget(self.tabs)

        central.setLayout(root_layout)
        self.setCentralWidget(central)

        self.status = QStatusBar()
        self.status.setSizeGripEnabled(True)
        self.setStatusBar(self.status)

        self.connection_label = QLabel("未连接")
        self.user_label = QLabel(
            f"当前用户：{self.user_profile['display_name']} | 角色：{self.user_profile['role']}"
        )

        self.status.addWidget(self.user_label)
        self.status.addPermanentWidget(self.connection_label)

        self._set_robot_badge(False)
        self._set_camera_badge("待启动", "idle")

        # 首屏为“实时画面”，默认隐藏设备日志并立即释放底部空间。
        self._on_main_tab_changed(self.tabs.currentIndex())

    def _on_main_tab_changed(self, index: int) -> None:
        """
        切换页面时只做业务刷新，不再显示底部终端日志。
        """
        if not hasattr(self, "tabs"):
            return

        page_name = self.tabs.tabText(index)

        if page_name == "回放与报告":
            self.refresh_recordings_table()
            self._refresh_event_table()


    def _build_presentation_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("PresentationHeader")
        header.setMinimumHeight(86)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(20, 13, 16, 13)
        layout.setSpacing(12)

        brand_layout = QVBoxLayout()
        brand_layout.setSpacing(2)

        title = QLabel("H1 机器人视觉监控平台")
        title.setObjectName("HeaderTitle")

        subtitle = QLabel("实时视觉感知 · 设备状态监测 · 三维环境展示")
        subtitle.setObjectName("HeaderSubtitle")

        brand_layout.addWidget(title)
        brand_layout.addWidget(subtitle)

        self.robot_badge = QLabel("● 机器人未连接")
        self.camera_badge = QLabel("● 画面待启动")
        self.header_clock_label = QLabel()
        self.header_clock_label.setObjectName("HeaderClock")

        self.fullscreen_btn = QPushButton("全屏展示")
        self.fullscreen_btn.setObjectName("HeaderButton")
        self.fullscreen_btn.setMinimumWidth(94)
        self.fullscreen_btn.clicked.connect(self.toggle_fullscreen)

        layout.addLayout(brand_layout)
        layout.addStretch()
        layout.addWidget(self.robot_badge)
        layout.addWidget(self.camera_badge)
        layout.addWidget(self.header_clock_label)
        layout.addWidget(self.fullscreen_btn)

        return header

    def _update_header_clock(self) -> None:
        if hasattr(self, "header_clock_label"):
            self.header_clock_label.setText(datetime.now().strftime("%Y-%m-%d  %H:%M:%S"))

    def _set_robot_badge(self, connected: bool) -> None:
        if not hasattr(self, "robot_badge"):
            return

        if connected:
            self.robot_badge.setText("● 机器人在线")
            self.robot_badge.setStyleSheet(
                "color:#dcfce7; background:#166534; border:1px solid #22c55e; "
                "border-radius:14px; padding:6px 11px; font-weight:800;"
            )
        else:
            self.robot_badge.setText("● 机器人未连接")
            self.robot_badge.setStyleSheet(
                "color:#fee2e2; background:#7f1d1d; border:1px solid #ef4444; "
                "border-radius:14px; padding:6px 11px; font-weight:800;"
            )

    def _set_camera_badge(self, text: str, state: str = "idle") -> None:
        if not hasattr(self, "camera_badge"):
            return

        styles = {
            "live": ("#dcfce7", "#166534", "#22c55e"),
            "working": ("#e0f2fe", "#075985", "#38bdf8"),
            "error": ("#fee2e2", "#7f1d1d", "#ef4444"),
            "idle": ("#e2e8f0", "#334155", "#64748b"),
        }
        color, background, border = styles.get(state, styles["idle"])
        self.camera_badge.setText(f"● 画面{text}")
        self.camera_badge.setStyleSheet(
            f"color:{color}; background:{background}; border:1px solid {border}; "
            "border-radius:14px; padding:6px 11px; font-weight:800;"
        )

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
            self.fullscreen_btn.setText("全屏展示")
        else:
            self.showFullScreen()
            self.fullscreen_btn.setText("退出全屏")

    def _auto_start_presentation(self) -> None:
        if getattr(self, "_closing_app", False):
            return

        self.tabs.setCurrentIndex(0)
        self._camera_auto_start = True
        self._set_camera_badge("启动中", "working")
        self.append_log("展示模式已启动，正在自动连接机器人并打开实时画面。")

        if not self.client.connected and not self.client.connecting:
            self.connect_robot()

        self.start_camera()

    def _make_scroll_page(self, widget: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidget(widget)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        return scroll


    def _build_bottom_tip_panel(self) -> QGroupBox:
        group = QGroupBox("系统运行信息")
        layout = QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        title = QLabel("设备日志")
        title.setStyleSheet("font-weight: 800; color: #334155;")

        clear_btn = QPushButton("清空")
        clear_btn.setMaximumWidth(76)
        clear_btn.clicked.connect(self._clear_tip_log)

        top_row.addWidget(title)
        top_row.addStretch()
        top_row.addWidget(clear_btn)

        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMinimumHeight(105)
        self.log_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        layout.addLayout(top_row)
        layout.addWidget(self.log_edit)

        group.setLayout(layout)
        return group


    def _clear_tip_log(self) -> None:
        if hasattr(self, "log_edit"):
            self.log_edit.clear()

    def _build_connection_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("ConnectionPage")

        outer = QHBoxLayout(page)
        outer.setContentsMargins(16, 12, 16, 16)
        outer.addStretch(1)

        content = QWidget()
        content.setMaximumWidth(860)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)

        heading = QLabel("设备连接设置")
        heading.setObjectName("LiveTitle")
        intro = QLabel("连接配置已从展示主界面后置，仅在部署、换网卡或排查通信时使用。")
        intro.setObjectName("LiveSubtitle")
        intro.setWordWrap(True)

        content_layout.addWidget(heading)
        content_layout.addWidget(intro)

        conn_group = QGroupBox("H1 通信配置")
        conn_layout = QVBoxLayout()
        conn_layout.setContentsMargins(14, 18, 14, 14)
        conn_layout.setSpacing(12)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setFormAlignment(Qt.AlignTop)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)

        self.ip_edit = QLineEdit("192.168.123.162")

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(8080)

        self.iface_edit = QLineEdit("enx9c69d3565ef9")

        self.protocol_combo = QComboBox()
        self.protocol_combo.addItem("Unitree SDK2", "sdk2")
        self.protocol_combo.addItem("演示模拟模式", "mock")
        self.protocol_combo.setCurrentIndex(0)

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
            combo.setMinimumHeight(34)
            combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)

        form.addRow("机器人 IP：", self.ip_edit)
        form.addRow("视频服务端口：", self.port_spin)
        form.addRow("本机网卡/接口：", self.iface_edit)
        form.addRow("通信方式：", self.protocol_combo)
        form.addRow("状态 Topic：", self.lowstate_topic_combo)
        form.addRow("状态数据类型：", self.lowstate_idl_combo)

        button_row = QHBoxLayout()
        button_row.setSpacing(9)

        self.connect_btn = QPushButton("连接并读取状态")
        self.connect_btn.setObjectName("PrimaryButton")
        self.disconnect_btn = QPushButton("断开连接")
        self.save_config_btn = QPushButton("保存配置")

        self.connect_btn.clicked.connect(self.connect_robot)
        self.disconnect_btn.clicked.connect(self.disconnect_robot)
        self.save_config_btn.clicked.connect(self.save_config)

        button_row.addWidget(self.connect_btn)
        button_row.addWidget(self.disconnect_btn)
        button_row.addWidget(self.save_config_btn)
        button_row.addStretch()

        conn_layout.addLayout(form)
        conn_layout.addLayout(button_row)
        conn_group.setLayout(conn_layout)

        info_group = QGroupBox("连接说明")
        info_layout = QVBoxLayout(info_group)
        info = QLabel(
            "系统当前只读取机器人状态，不发布运动控制指令。没有数据时，请依次检查机器人 IP、"
            "网卡名称、DDS 环境、Topic 和 IDL 类型。"
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#526277; line-height:150%; padding:3px;")
        info_layout.addWidget(info)

        content_layout.addWidget(conn_group)
        content_layout.addWidget(info_group)
        content_layout.addStretch()

        outer.addWidget(content, 6)
        outer.addStretch(1)
        return page


    def _build_status_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self.status_data_tabs = QTabWidget()

        self.lowstate_table = self._create_kv_table()
        self.imu_table = self._create_kv_table()
        # self.bms_table = self._create_kv_table()

        self.motor_table = QTableWidget()
        self.motor_table.setAlternatingRowColors(True)
        self.motor_table.verticalHeader().setVisible(False)
        self.motor_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.motor_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.motor_table.setHorizontalScrollMode(QTableWidget.ScrollPerPixel)
        self.motor_table.setVerticalScrollMode(QTableWidget.ScrollPerPixel)

        motor_header = self.motor_table.horizontalHeader()
        motor_header.setSectionResizeMode(QHeaderView.Interactive)
        motor_header.setDefaultSectionSize(92)
        motor_header.setMinimumSectionSize(52)

        self.status_data_tabs.addTab(self.lowstate_table, "低频状态主字段")
        self.status_data_tabs.addTab(self.imu_table, "惯性测量单元")
        # self.status_data_tabs.addTab(self.bms_table, "电池管理系统")
        self.status_data_tabs.addTab(self.motor_table, "电机状态")


        layout.addWidget(self.status_data_tabs)
        page.setLayout(layout)

        return page


    def _build_camera_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("LivePage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(9)

        camera_group = QGroupBox("实时视觉画面")
        camera_layout = QVBoxLayout(camera_group)
        camera_layout.setContentsMargins(12, 15, 12, 12)
        camera_layout.setSpacing(10)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title = QLabel("H1 第一视角")
        title.setObjectName("LiveTitle")
        subtitle = QLabel("RealSense D435i · 彩色与深度实时感知")
        subtitle.setObjectName("LiveSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)

        self.camera_kind_combo = QComboBox()
        self.camera_kind_combo.addItem("彩色 + 深度", "combined")
        self.camera_kind_combo.addItem("仅彩色", "color")
        self.camera_kind_combo.addItem("仅深度", "depth")
        self.camera_kind_combo.setMinimumWidth(126)
        self.camera_kind_combo.currentIndexChanged.connect(self._on_camera_kind_changed)

        default_host = self.ip_edit.text().strip() if hasattr(self, "ip_edit") else BOARD_PCD_HOST
        default_url = f"http://{default_host or BOARD_PCD_HOST}:{BOARD_REALSENSE_PORT}/stream/combined"
        self.camera_url_edit = QLineEdit(default_url)
        self.camera_url_edit.setReadOnly(True)
        self.camera_url_edit.setVisible(False)

        self.start_camera_btn = QPushButton("启动实时画面")
        self.start_camera_btn.setObjectName("PrimaryButton")
        self.stop_camera_btn = QPushButton("暂停显示")
        self.snapshot_camera_btn = QPushButton("保存截图")
        self.snapshot_camera_btn.setEnabled(False)
        self.record_camera_btn = QPushButton("开始录制")
        self.record_camera_btn.setObjectName("DangerButton")
        self.record_camera_btn.setEnabled(False)
        self.stop_remote_camera_btn = QPushButton("关闭相机服务")

        self.camera_recording = False
        self.camera_record_fps = 20.0
        self.camera_service_started = False
        self.camera_stream_has_frame = False
        self.color_record_worker = None
        self.depth_record_worker = None
        self.color_record_path = None
        self.depth_record_path = None

        self.start_camera_btn.clicked.connect(self.start_camera)
        self.stop_camera_btn.clicked.connect(self.stop_camera)
        self.snapshot_camera_btn.clicked.connect(self.save_camera_snapshot)
        self.record_camera_btn.clicked.connect(self.toggle_camera_recording)
        self.stop_remote_camera_btn.clicked.connect(self.stop_remote_realsense_service)

        top_row.addLayout(title_box)
        top_row.addStretch()
        top_row.addWidget(QLabel("画面模式："))
        top_row.addWidget(self.camera_kind_combo)
        top_row.addWidget(self.start_camera_btn)
        top_row.addWidget(self.stop_camera_btn)
        top_row.addWidget(self.snapshot_camera_btn)
        top_row.addWidget(self.record_camera_btn)
        top_row.addWidget(self.stop_remote_camera_btn)

        self.camera_status_label = QLabel(
            "系统将在登录后自动尝试连接相机；也可使用右上角按钮手动启动。"
        )
        self.camera_status_label.setObjectName("CameraStatusPanel")
        self.camera_status_label.setWordWrap(True)
        self.camera_status_label.setMinimumHeight(40)
        self.camera_status_label.setStyleSheet(
            "color:#475569; padding:8px 11px; font-weight:700;"
        )

        self.camera_label = QLabel("正在准备实时画面…")
        self.camera_label.setObjectName("CameraViewport")
        self.camera_label.setAlignment(Qt.AlignCenter)
        self.camera_label.setMinimumSize(560, 315)
        self.camera_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.camera_label.setStyleSheet(
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #020617,stop:1 #101d35); "
            "color:#cfe8ff; border:1px solid #294568; border-radius:13px; "
            "font-size:18px; font-weight:900;"
        )

        live_splitter = QSplitter(Qt.Horizontal)
        live_splitter.setChildrenCollapsible(False)
        live_splitter.addWidget(self.camera_label)
        live_splitter.addWidget(self._build_leader_cockpit())
        live_splitter.setStretchFactor(0, 7)
        live_splitter.setStretchFactor(1, 3)
        live_splitter.setSizes([830, 330])

        camera_layout.addLayout(top_row)
        camera_layout.addWidget(self.camera_status_label)
        camera_layout.addWidget(live_splitter, stretch=1)
        layout.addWidget(camera_group)
        return page

    def _make_metric_card(self, title: str, initial: str, hint: str = ""):
        card = QWidget()
        card.setObjectName("MetricCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 8, 10, 8)
        card_layout.setSpacing(1)

        label = QLabel(title)
        label.setObjectName("MetricLabel")
        value = QLabel(initial)
        value.setObjectName("MetricValue")
        value.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hint_label = QLabel(hint)
        hint_label.setObjectName("MetricHint")
        hint_label.setWordWrap(True)

        card_layout.addWidget(label)
        card_layout.addWidget(value)
        card_layout.addWidget(hint_label)
        return card, value, hint_label

    def _build_leader_cockpit(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(305)
        scroll.setMaximumWidth(410)

        panel = QWidget()
        panel.setObjectName("CockpitPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(9)

        title = QLabel("观看舱")
        title.setObjectName("CockpitTitle")
        subtitle = QLabel("核心指标实时汇总")
        subtitle.setObjectName("CockpitSubtitle")

        layout.addWidget(title)
        layout.addWidget(subtitle)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)

        cards = []
        # card, self.cockpit_robot_value, self.cockpit_robot_hint = self._make_metric_card(
        #     "机器人", "离线", "等待 LowState"
        # )
        # cards.append(card)
        card, self.cockpit_latency_value, self.cockpit_latency_hint = self._make_metric_card(
            "网络延迟", "--", "Ping 往返耗时"
        )
        cards.append(card)
        card, self.cockpit_fps_value, self.cockpit_fps_hint = self._make_metric_card(
            "视频帧率", "0.0", "FPS"
        )
        cards.append(card)
        card, self.cockpit_packet_value, self.cockpit_packet_hint = self._make_metric_card(
            "状态数据包", "0", "实时累计"
        )
        # cards.append(card)
        cards.append(card)

        card, self.cockpit_roll_value, self.cockpit_roll_hint = self._make_metric_card(
            "Roll 左右侧倾", "--", "° · 越接近 0 越平稳"
        )
        cards.append(card)
        card, self.cockpit_pitch_value, self.cockpit_pitch_hint = self._make_metric_card(
            "Pitch 前后俯仰", "--", "° · 前倾/后仰"
        )
        cards.append(card)
        card, self.cockpit_yaw_value, self.cockpit_yaw_hint = self._make_metric_card(
            "Yaw 水平朝向", "--", "° · 转向角"
        )
        cards.append(card)

        for index, card in enumerate(cards):
            grid.addWidget(card, index // 2, index % 2)

        layout.addLayout(grid)

        task_box = QGroupBox("当前任务")
        task_layout = QVBoxLayout(task_box)
        task_layout.setContentsMargins(10, 13, 10, 10)
        task_layout.setSpacing(6)

        self.cockpit_task_label = QLabel("系统演示准备 · 准备中")
        self.cockpit_task_label.setWordWrap(True)
        self.cockpit_task_label.setStyleSheet("font-weight:900; color:#20324d;")
        self.cockpit_task_progress = QProgressBar()
        self.cockpit_task_progress.setRange(0, 100)
        self.cockpit_task_progress.setValue(0)
        self.cockpit_task_progress.setFormat("%p%")

        report_btn = QPushButton("一键生成演示报告")
        report_btn.setObjectName("PrimaryButton")
        report_btn.clicked.connect(self.generate_demo_report)

        task_layout.addWidget(self.cockpit_task_label)
        task_layout.addWidget(self.cockpit_task_progress)
        task_layout.addWidget(report_btn)
        layout.addWidget(task_box)
        layout.addStretch()

        scroll.setWidget(panel)
        return scroll

    @staticmethod
    def _parse_vector_numbers(value: Any) -> List[float]:
        numbers = re.findall(r"[-+]?\d+(?:\.\d+)?", str(value))
        result = []
        for item in numbers:
            try:
                result.append(float(item))
            except Exception:
                pass
        return result

    @staticmethod
    def _metric_text(value: Any, suffix: str = "", digits: int = 1) -> str:
        if value is None:
            return "--"
        text = str(value).strip()
        if not text or text in ("N/A", "该字段并未开放"):
            return "--"
        try:
            number = float(text)
            return f"{number:.{digits}f}{suffix}"
        except Exception:
            return text

    def _refresh_dashboard_metrics(self) -> None:
        now = time.monotonic()
        elapsed = max(0.2, now - self._fps_last_tick)
        self._current_video_fps = self._fps_frame_count / elapsed
        self._fps_frame_count = 0
        self._fps_last_tick = now

        if hasattr(self, "cockpit_fps_value"):
            fps = self._current_video_fps if self.camera_stream_has_frame else 0.0
            self.cockpit_fps_value.setText(f"{fps:.1f}")

        connected = bool(self.client.connected)
        if hasattr(self, "cockpit_robot_value"):
            self.cockpit_robot_value.setText("在线" if connected else "离线")
            if self._last_robot_packet_time is None:
                hint = "等待 LowState 数据"
            else:
                age = max(0.0, now - self._last_robot_packet_time)
                hint = f"数据更新于 {age:.1f} 秒前"
            self.cockpit_robot_hint.setText(hint)

        if hasattr(self, "cockpit_task_label"):
            self._sync_task_display()

    def _start_network_ping(self) -> None:
        if getattr(self, "_closing_app", False):
            return

        process = getattr(self, "_ping_process", None)
        if process is not None and process.state() != QProcess.NotRunning:
            return

        host = self.ip_edit.text().strip() if hasattr(self, "ip_edit") else BOARD_PCD_HOST
        host = host or BOARD_PCD_HOST

        self._ping_output = ""
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.MergedChannels)
        process.readyReadStandardOutput.connect(self._on_network_ping_output)
        process.finished.connect(self._on_network_ping_finished)
        process.errorOccurred.connect(self._on_network_ping_error)
        self._ping_process = process
        process.start("ping", ["-c", "1", "-W", "1", host])

    def _on_network_ping_output(self) -> None:
        process = getattr(self, "_ping_process", None)
        if process is None:
            return
        data = bytes(process.readAllStandardOutput()).decode("utf-8", errors="ignore")
        self._ping_output += data

    def _on_network_ping_finished(self, exit_code: int, exit_status) -> None:
        process = getattr(self, "_ping_process", None)
        output = self._ping_output
        if process is not None:
            output += bytes(process.readAllStandardOutput()).decode("utf-8", errors="ignore")

        match = re.search(r"time[=<]\s*([0-9.]+)\s*ms", output, re.IGNORECASE)
        if exit_code == 0 and match:
            self._network_latency_ms = float(match.group(1))
            if hasattr(self, "cockpit_latency_value"):
                self.cockpit_latency_value.setText(f"{self._network_latency_ms:.1f}")
                self.cockpit_latency_hint.setText("ms · Ping 往返耗时")
        else:
            self._network_latency_ms = None
            if hasattr(self, "cockpit_latency_value"):
                self.cockpit_latency_value.setText("超时")
                self.cockpit_latency_hint.setText("网络不可达或无响应")

        if process is not None:
            process.deleteLater()
        self._ping_process = None

    def _on_network_ping_error(self, error) -> None:
        self._network_latency_ms = None
        if hasattr(self, "cockpit_latency_value"):
            self.cockpit_latency_value.setText("--")
            self.cockpit_latency_hint.setText("无法启动 ping")

        process = getattr(self, "_ping_process", None)
        if process is not None:
            process.deleteLater()
        self._ping_process = None

    def _build_replay_report_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(9)

        heading_row = QHBoxLayout()
        heading_box = QVBoxLayout()
        heading = QLabel("录像回放、事件时间轴与演示报告")
        heading.setObjectName("LiveTitle")
        intro = QLabel("集中管理录像、演示事件、任务进度和汇报材料。")
        intro.setObjectName("LiveSubtitle")
        heading_box.addWidget(heading)
        heading_box.addWidget(intro)
        heading_row.addLayout(heading_box)
        heading_row.addStretch()

        report_now_btn = QPushButton("一键生成演示报告")
        report_now_btn.setObjectName("PrimaryButton")
        report_now_btn.clicked.connect(self.generate_demo_report)
        heading_row.addWidget(report_now_btn)
        layout.addLayout(heading_row)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        recordings_group = QGroupBox("录像回放")
        recordings_layout = QVBoxLayout(recordings_group)
        recordings_layout.setContentsMargins(10, 14, 10, 10)
        recordings_layout.setSpacing(8)

        recording_buttons = QHBoxLayout()
        refresh_btn = QPushButton("刷新录像")
        play_btn = QPushButton("播放选中")
        play_btn.setObjectName("PrimaryButton")
        open_record_dir_btn = QPushButton("打开录像目录")
        refresh_btn.clicked.connect(self.refresh_recordings_table)
        play_btn.clicked.connect(self.play_selected_recording)
        open_record_dir_btn.clicked.connect(self.open_recordings_directory)
        recording_buttons.addWidget(refresh_btn)
        recording_buttons.addWidget(play_btn)
        recording_buttons.addWidget(open_record_dir_btn)
        recording_buttons.addStretch()

        self.recordings_table = QTableWidget()
        self.recordings_table.setColumnCount(4)
        self.recordings_table.setHorizontalHeaderLabels(["录制时间", "类型", "文件", "大小"])
        self.recordings_table.verticalHeader().setVisible(False)
        self.recordings_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.recordings_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.recordings_table.setSelectionMode(QTableWidget.SingleSelection)
        header = self.recordings_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.recordings_table.doubleClicked.connect(self.play_selected_recording)

        recordings_layout.addLayout(recording_buttons)
        recordings_layout.addWidget(self.recordings_table)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(9)

        task_group = QGroupBox("任务进度展示")
        task_layout = QGridLayout(task_group)
        task_layout.setContentsMargins(10, 15, 10, 10)
        task_layout.setHorizontalSpacing(8)
        task_layout.setVerticalSpacing(8)

        self.task_name_edit = QLineEdit(self.current_task["name"])
        self.task_stage_combo = QComboBox()
        self.task_stage_combo.addItems(["准备中", "执行中", "已完成", "暂停"])
        self.task_progress_spin = QSpinBox()
        self.task_progress_spin.setRange(0, 100)
        self.task_progress_spin.setSuffix(" %")
        self.task_progress_spin.setValue(int(self.current_task["progress"]))
        apply_task_btn = QPushButton("更新任务")
        apply_task_btn.setObjectName("PrimaryButton")
        apply_task_btn.clicked.connect(self.apply_task_progress)

        self.task_progress_bar = QProgressBar()
        self.task_progress_bar.setRange(0, 100)
        self.task_progress_bar.setValue(int(self.current_task["progress"]))
        self.task_progress_bar.setFormat("当前进度 %p%")

        task_layout.addWidget(QLabel("任务名称"), 0, 0)
        task_layout.addWidget(self.task_name_edit, 0, 1, 1, 3)
        task_layout.addWidget(QLabel("阶段"), 1, 0)
        task_layout.addWidget(self.task_stage_combo, 1, 1)
        task_layout.addWidget(QLabel("进度"), 1, 2)
        task_layout.addWidget(self.task_progress_spin, 1, 3)
        task_layout.addWidget(self.task_progress_bar, 2, 0, 1, 3)
        task_layout.addWidget(apply_task_btn, 2, 3)

        timeline_group = QGroupBox("事件时间轴")
        timeline_layout = QVBoxLayout(timeline_group)
        timeline_layout.setContentsMargins(10, 14, 10, 10)
        timeline_layout.setSpacing(8)

        timeline_buttons = QHBoxLayout()
        mark_btn = QPushButton("添加演示事件")
        clear_timeline_btn = QPushButton("清空时间轴")
        mark_btn.clicked.connect(self.add_manual_event)
        clear_timeline_btn.clicked.connect(self.clear_event_timeline)
        timeline_buttons.addWidget(mark_btn)
        timeline_buttons.addWidget(clear_timeline_btn)
        timeline_buttons.addStretch()

        self.event_table = QTableWidget()
        self.event_table.setColumnCount(3)
        self.event_table.setHorizontalHeaderLabels(["时间", "级别", "事件"])
        self.event_table.verticalHeader().setVisible(False)
        self.event_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.event_table.setSelectionBehavior(QTableWidget.SelectRows)
        event_header = self.event_table.horizontalHeader()
        event_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        event_header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        event_header.setSectionResizeMode(2, QHeaderView.Stretch)

        timeline_layout.addLayout(timeline_buttons)
        timeline_layout.addWidget(self.event_table)

        report_group = QGroupBox("演示报告")
        report_layout = QHBoxLayout(report_group)
        report_layout.setContentsMargins(10, 14, 10, 10)
        generate_btn = QPushButton("生成 PDF 演示报告")
        generate_btn.setObjectName("PrimaryButton")
        open_report_btn = QPushButton("打开最近报告")
        open_report_dir_btn = QPushButton("打开报告目录")
        generate_btn.clicked.connect(self.generate_demo_report)
        open_report_btn.clicked.connect(self.open_last_report)
        open_report_dir_btn.clicked.connect(self.open_reports_directory)
        report_layout.addWidget(generate_btn)
        report_layout.addWidget(open_report_btn)
        report_layout.addWidget(open_report_dir_btn)
        report_layout.addStretch()

        right_layout.addWidget(task_group)
        right_layout.addWidget(timeline_group, stretch=1)
        right_layout.addWidget(report_group)

        splitter.addWidget(recordings_group)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([560, 580])

        layout.addWidget(splitter, stretch=1)
        QTimer.singleShot(0, self.refresh_recordings_table)
        return page

    def refresh_recordings_table(self) -> None:
        if not hasattr(self, "recordings_table"):
            return

        directory = APP_DIR / "camera_recordings"
        directory.mkdir(parents=True, exist_ok=True)
        all_files = sorted(directory.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        files = [path for path in all_files if is_finalized_mp4(path)]
        skipped = len(all_files) - len(files)

        if skipped and hasattr(self, "status"):
            self.status.showMessage(
                f"已隐藏 {skipped} 个未正常结束或损坏的 MP4，避免回放报错。", 6000
            )

        self.recordings_table.setRowCount(len(files))
        for row, path in enumerate(files):
            stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            kind = "彩色" if path.stem.endswith("_color") else "深度" if path.stem.endswith("_depth") else "录像"
            size_mb = path.stat().st_size / (1024 * 1024)

            time_item = QTableWidgetItem(stamp)
            time_item.setData(Qt.UserRole, str(path))
            kind_item = QTableWidgetItem(kind)
            name_item = QTableWidgetItem(path.name)
            name_item.setToolTip(str(path))
            size_item = QTableWidgetItem(f"{size_mb:.1f} MB")

            self.recordings_table.setItem(row, 0, time_item)
            self.recordings_table.setItem(row, 1, kind_item)
            self.recordings_table.setItem(row, 2, name_item)
            self.recordings_table.setItem(row, 3, size_item)

        if files:
            self.recordings_table.selectRow(0)

    def _selected_recording_path(self) -> Optional[Path]:
        if not hasattr(self, "recordings_table"):
            return None
        row = self.recordings_table.currentRow()
        if row < 0:
            return None
        item = self.recordings_table.item(row, 0)
        if item is None:
            return None
        path = item.data(Qt.UserRole)
        return Path(path) if path else None

    def play_selected_recording(self, *args) -> None:
        path = self._selected_recording_path()
        if path is None or not path.exists():
            QMessageBox.information(self, "请选择录像", "请先在列表中选择一个有效录像。")
            return

        if not is_finalized_mp4(path):
            QMessageBox.warning(
                self,
                "录像不可回放",
                "该 MP4 文件未正常结束或已损坏，缺少 moov 元数据。\n"
                "请重新录制，或只回放正常停止录制后生成的文件。"
            )
            self.refresh_recordings_table()
            return

        self.append_log(f"回放录像：{path.name}")
        dialog = VideoPlaybackDialog(str(path), self)
        dialog.exec_()

    def open_recordings_directory(self) -> None:
        directory = APP_DIR / "camera_recordings"
        directory.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory.resolve())))

    def _event_level(self, text: str) -> str:
        lower = text.lower()
        if any(word in lower for word in ("失败", "错误", "异常", "超时", "崩溃", "error", "failed")):
            return "异常"
        if any(word in lower for word in ("警告", "断开", "未收到", "未找到", "无效", "warning")):
            return "提醒"
        return "信息"

    def _presentation_event_text(self, text: str) -> Optional[str]:
        """
        事件时间轴只保留领导能看懂的演示节点，不再混入终端设备日志。
        """
        clean = " ".join(str(text).split())
        if not clean:
            return None

        technical_markers = (
            "topic=", "idl=", "iface=", "ChannelFactory", "LowState", "LidarState",
            "DDS", "QProcess", "sshpass", "StrictHostKeyChecking", "ss output",
            "tail -", "[remote]", "start_realsense.py pid", "rt/lowstate",
            "rt/lidarstate", "Key pressed", "Reader", "take sample", "moov atom",
        )

        leader_keywords = (
            "用户", "展示模式", "实时画面", "画面", "相机", "截图", "录制", "回放",
            "演示事件", "任务进度", "报告", "机器人端 RealSense 服务已启动",
            "机器人端 RealSense 服务已停止", "连接失败", "启动失败", "导航", "建图",
            "点云地图已更新", "点云地图更新失败", "已断开连接",
        )

        if any(keyword in clean for keyword in leader_keywords):
            return clean[:240]

        if any(marker in clean for marker in technical_markers):
            return None

        # 只保留短的异常/提醒，长段终端输出一律不进时间轴。
        level = self._event_level(clean)
        if level in ("异常", "提醒") and len(clean) <= 180:
            return clean

        return None

    def _record_event(self, text: str) -> None:
        clean = self._presentation_event_text(text)
        if not clean:
            return

        event = {
            "time": now_text(),
            "level": self._event_level(clean),
            "text": clean[:240],
        }
        self.event_history.append(event)
        if len(self.event_history) > 500:
            self.event_history = self.event_history[-500:]

        if hasattr(self, "event_table"):
            self.event_table.insertRow(0)
            self.event_table.setItem(0, 0, QTableWidgetItem(event["time"]))
            self.event_table.setItem(0, 1, QTableWidgetItem(event["level"]))
            item = QTableWidgetItem(event["text"])
            item.setToolTip(event["text"])
            self.event_table.setItem(0, 2, item)
            while self.event_table.rowCount() > 200:
                self.event_table.removeRow(self.event_table.rowCount() - 1)

    def _refresh_event_table(self) -> None:
        if not hasattr(self, "event_table"):
            return
        events = list(reversed(self.event_history[-200:]))
        self.event_table.setRowCount(len(events))
        for row, event in enumerate(events):
            self.event_table.setItem(row, 0, QTableWidgetItem(event["time"]))
            self.event_table.setItem(row, 1, QTableWidgetItem(event["level"]))
            item = QTableWidgetItem(event["text"])
            item.setToolTip(event["text"])
            self.event_table.setItem(row, 2, item)

    def add_manual_event(self) -> None:
        text, ok = QInputDialog.getText(self, "添加演示事件", "事件说明：")
        if ok and text.strip():
            self.append_log(f"演示事件：{text.strip()}")

    def clear_event_timeline(self) -> None:
        self.event_history.clear()
        if hasattr(self, "event_table"):
            self.event_table.setRowCount(0)

    def apply_task_progress(self) -> None:
        name = self.task_name_edit.text().strip() or "未命名任务"
        stage = self.task_stage_combo.currentText()
        progress = self.task_progress_spin.value()

        self.current_task = {
            "name": name,
            "stage": stage,
            "progress": progress,
            "updated_at": now_text(),
        }
        self._sync_task_display()
        self.append_log(f"任务进度更新：{name}，阶段={stage}，进度={progress}%")

    def _sync_task_display(self) -> None:
        task = self.current_task
        if hasattr(self, "cockpit_task_label"):
            self.cockpit_task_label.setText(f"{task['name']} · {task['stage']}")
            self.cockpit_task_progress.setValue(int(task["progress"]))
        if hasattr(self, "task_progress_bar"):
            self.task_progress_bar.setValue(int(task["progress"]))

    def _report_metric_rows(self) -> List[tuple]:
        lowstate = self.latest_robot_status.get("lowstate_main", {})
        imu = self.latest_robot_status.get("imu_state", {})
        rpy = self._parse_vector_numbers(imu.get("rpy_deg", ""))
        roll = f"{rpy[0]:.2f}°" if len(rpy) > 0 else "--"
        pitch = f"{rpy[1]:.2f}°" if len(rpy) > 1 else "--"
        yaw = f"{rpy[2]:.2f}°" if len(rpy) > 2 else "--"
        latency = f"{self._network_latency_ms:.1f} ms" if self._network_latency_ms is not None else "超时/未知"

        return [
            ("机器人连接", "在线" if self.client.connected else "离线"),
            ("实时画面", "运行中" if self.camera_stream_has_frame else "未运行"),
            ("网络延迟", latency),
            ("视频帧率", f"{self._current_video_fps:.1f} FPS"),
            ("状态数据包", str(lowstate.get("packet_count", "0"))),
            ("Roll 左右侧倾", roll),
            ("Pitch 前后俯仰", pitch),
            ("Yaw 水平朝向", yaw),
        ]

    def generate_demo_report(self) -> None:
        report_dir = APP_DIR / "demo_reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        pdf_path = report_dir / f"H1_demo_report_{stamp}.pdf"
        html_path = report_dir / f"H1_demo_report_{stamp}.html"
        dashboard_image = report_dir / f"H1_dashboard_{stamp}.png"
        camera_image = report_dir / f"H1_camera_{stamp}.png"

        old_index = self.tabs.currentIndex() if hasattr(self, "tabs") else 0
        try:
            if hasattr(self, "tabs"):
                self.tabs.setCurrentIndex(0)
                QApplication.processEvents()
            self.centralWidget().grab().save(str(dashboard_image), "PNG")
        finally:
            if hasattr(self, "tabs") and old_index != 0:
                self.tabs.setCurrentIndex(old_index)

        camera_saved = False
        if self._last_camera_image is not None and not self._last_camera_image.isNull():
            camera_saved = self._last_camera_image.save(str(camera_image), "PNG")
        elif hasattr(self, "camera_label"):
            pixmap = self.camera_label.pixmap()
            if pixmap is not None and not pixmap.isNull():
                camera_saved = pixmap.save(str(camera_image), "PNG")

        metric_rows = self._report_metric_rows()
        metrics_html = "".join(
            f"<tr><th>{html.escape(name)}</th><td>{html.escape(value)}</td></tr>"
            for name, value in metric_rows
        )

        lowstate = self.latest_robot_status.get("lowstate_main", {})
        detail_keys = [
            "update_time", "temperature_ntc1",
            "temperature_ntc2", "fan_frequency", "foot_force", "bit_flag",
        ]
        details = []
        for key in detail_keys:
            if key in lowstate:
                details.append((tr_ui_text(key), str(lowstate.get(key, ""))))
        detail_html = "".join(
            f"<tr><th>{html.escape(name)}</th><td>{html.escape(value)}</td></tr>"
            for name, value in details
        ) or "<tr><td colspan='2'>当前尚未收到机器人状态数据。</td></tr>"

        exceptions = [e for e in self.event_history if e["level"] in ("异常", "提醒")][-20:]
        exception_html = "".join(
            f"<tr><td>{html.escape(e['time'])}</td><td>{html.escape(e['level'])}</td>"
            f"<td>{html.escape(e['text'])}</td></tr>" for e in reversed(exceptions)
        ) or "<tr><td colspan='3'>本次运行暂未记录异常或提醒。</td></tr>"

        recent_events = self.event_history[-30:]
        event_html = "".join(
            f"<tr><td>{html.escape(e['time'])}</td><td>{html.escape(e['level'])}</td>"
            f"<td>{html.escape(e['text'])}</td></tr>" for e in reversed(recent_events)
        ) or "<tr><td colspan='3'>暂无事件记录。</td></tr>"

        task = self.current_task
        camera_section = (
            f"<h2>实时画面截图</h2><img src='{html.escape(camera_image.name)}' class='shot'>"
            if camera_saved else
            "<h2>实时画面截图</h2><p class='muted'>生成报告时尚未取得有效相机画面。</p>"
        )

        report_html = f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>H1 机器人演示报告</title>
<style>
body {{ font-family: 'Noto Sans CJK SC', 'Microsoft YaHei', sans-serif; color:#172033; margin:28px; }}
h1 {{ color:#0f2a4a; margin-bottom:4px; }}
h2 {{ color:#174ea6; border-bottom:1px solid #dbe4ee; padding-bottom:6px; margin-top:24px; }}
.meta, .muted {{ color:#64748b; }}
table {{ width:100%; border-collapse:collapse; margin-top:10px; }}
th, td {{ border:1px solid #d7e0ea; padding:8px; text-align:left; vertical-align:top; }}
th {{ background:#eef4fb; width:28%; }}
.summary td {{ width:25%; font-size:15px; }}
.task {{ padding:12px; background:#eef6ff; border:1px solid #bfd8f8; border-radius:8px; }}
.shot {{ max-width:100%; border:1px solid #cbd5e1; border-radius:8px; margin-top:8px; }}
.footer {{ margin-top:28px; color:#64748b; font-size:12px; text-align:center; }}
</style>
</head>
<body>
<h1>H1 机器人视觉监控平台演示报告</h1>
<p class="meta">生成时间：{html.escape(now_text())}　|　操作人员：{html.escape(self.user_profile.get('display_name', ''))}</p>

<h2>任务进度</h2>
<div class="task"><strong>{html.escape(task['name'])}</strong>　·　{html.escape(task['stage'])}　·　进度 {int(task['progress'])}%<br>
<span class="muted">更新时间：{html.escape(task['updated_at'])}</span></div>

<h2>核心运行指标</h2>
<table class="summary">{metrics_html}</table>

<h2>领导驾驶舱截图</h2>
<img src="{html.escape(dashboard_image.name)}" class="shot">

{camera_section}

<h2>运行状态摘要</h2>
<table>{detail_html}</table>

<h2>异常与提醒记录</h2>
<table><tr><th>时间</th><th>级别</th><th>内容</th></tr>{exception_html}</table>

<h2>事件时间轴（最近 30 条）</h2>
<table><tr><th>时间</th><th>级别</th><th>事件</th></tr>{event_html}</table>

<p class="footer">本报告由 H1 机器人视觉监控平台自动生成。</p>
</body>
</html>
"""

        html_path.write_text(report_html, encoding="utf-8")

        try:
            document = QTextDocument()
            document.setBaseUrl(QUrl.fromLocalFile(str(report_dir.resolve()) + "/"))
            document.setHtml(report_html)

            printer = QPrinter(QPrinter.HighResolution)
            printer.setOutputFormat(QPrinter.PdfFormat)
            printer.setOutputFileName(str(pdf_path))
            printer.setPageMargins(12, 12, 12, 12, QPrinter.Millimeter)
            document.print_(printer)
        except Exception as exc:
            self.append_log(f"PDF 报告生成失败，已保留 HTML 报告：{exc}")
            QMessageBox.warning(
                self,
                "PDF 生成失败",
                f"PDF 生成失败，但 HTML 报告已经保存：\n{html_path}\n\n错误：{exc}",
            )
            self.last_report_path = html_path
            return

        self.last_report_path = pdf_path
        self.append_log(f"演示报告已生成：{pdf_path}")
        QMessageBox.information(
            self,
            "演示报告已生成",
            f"PDF：\n{pdf_path}\n\nHTML：\n{html_path}",
        )

    def open_last_report(self) -> None:
        path = self.last_report_path
        if path is None or not Path(path).exists():
            QMessageBox.information(self, "暂无报告", "请先生成一份演示报告。")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve())))

    def open_reports_directory(self) -> None:
        directory = APP_DIR / "demo_reports"
        directory.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory.resolve())))

    def save_camera_snapshot(self) -> None:
        pixmap = self.camera_label.pixmap() if hasattr(self, "camera_label") else None

        if pixmap is None or pixmap.isNull():
            QMessageBox.information(self, "暂无画面", "收到实时画面后才能保存截图。")
            return

        save_dir = APP_DIR / "camera_snapshots"
        save_dir.mkdir(parents=True, exist_ok=True)
        date_name = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")[:-3]
        path = save_dir / f"{date_name}_snapshot.png"

        if pixmap.save(str(path), "PNG"):
            self.camera_status_label.setText(f"截图已保存：{path}")
            self.append_log(f"实时画面截图已保存：{path}")
        else:
            QMessageBox.warning(self, "保存失败", f"无法保存截图：{path}")

    def toggle_camera_recording(self):
        if getattr(self, "camera_recording", False):
            self.stop_camera_recording()
        else:
            self.start_camera_recording()

    def start_camera_recording(self):
        if getattr(self, "camera_recording", False):
            return

        if not getattr(self, "camera_service_started", False):
            self.camera_status_label.setText("请先启动机器人端 RealSense 相机服务，再开始录制。")
            return

        if getattr(self, "camera_worker", None) is None:
            self.camera_status_label.setText("当前没有打开实时画面，请先点击“启动实时画面”。")
            return

        if not getattr(self, "camera_stream_has_frame", False):
            self.camera_status_label.setText("当前还没有收到实时画面，收到第一帧后才能录制。")
            return

        host = self.ip_edit.text().strip() if hasattr(self, "ip_edit") else BOARD_PCD_HOST
        host = host or BOARD_PCD_HOST
        port = BOARD_REALSENSE_PORT

        color_url = f"http://{host}:{port}/stream/color"
        depth_url = f"http://{host}:{port}/stream/depth"

        save_dir = APP_DIR / "camera_recordings"
        save_dir.mkdir(parents=True, exist_ok=True)

        date_name = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")[:-3]


        self.color_record_path = save_dir / f"{date_name}_color.mp4"
        self.depth_record_path = save_dir / f"{date_name}_depth.mp4"

        # 如果同一天重复录制，按“当前日期”命名会覆盖旧文件。
        # 这里主动删除旧文件，避免 VideoWriter 追加/占用异常。
        for path in [self.color_record_path, self.depth_record_path]:
            try:
                if path.exists():
                    path.unlink()
            except Exception as exc:
                self.camera_status_label.setText(f"无法覆盖旧视频文件：{path}，错误：{exc}")
                return

        self.color_record_worker = MjpegRecorderWorker(
            color_url,
            str(self.color_record_path),
            self.camera_record_fps,
            self,
        )

        self.depth_record_worker = MjpegRecorderWorker(
            depth_url,
            str(self.depth_record_path),
            self.camera_record_fps,
            self,
        )

        for worker in [self.color_record_worker, self.depth_record_worker]:
            worker.log_signal.connect(self.append_log)
            worker.error_signal.connect(self._on_camera_record_error)
            worker.done_signal.connect(self._on_camera_record_done)

        self.camera_recording = True

        self.record_camera_btn.setText("停止录制")
        self.record_camera_btn.setStyleSheet(
            "background-color: #dc2626; color: white; font-weight: 800;"
        )

        self.color_record_worker.start()
        self.depth_record_worker.start()

        self.camera_status_label.setText(
            "正在录制 color 和 depth 两路视频：\n"
            f"color：{self.color_record_path}\n"
            f"depth：{self.depth_record_path}"
        )

        self.append_log(f"开始录制 color 视频：{self.color_record_path}")
        self.append_log(f"开始录制 depth 视频：{self.depth_record_path}")

    def stop_camera_recording(self):
        color_worker = getattr(self, "color_record_worker", None)
        depth_worker = getattr(self, "depth_record_worker", None)

        if not getattr(self, "camera_recording", False) and color_worker is None and depth_worker is None:
            return

        self.camera_recording = False

        for attr_name in ["color_record_worker", "depth_record_worker"]:
            worker = getattr(self, attr_name, None)

            if worker is None:
                continue

            try:
                worker.stop()

                if not worker.wait(5000):
                    self.append_log(f"{attr_name} 录制线程 5 秒内未退出。")
                else:
                    self.append_log(f"{attr_name} 录制线程已停止。")

                worker.deleteLater()

            except Exception as exc:
                self.append_log(f"停止 {attr_name} 失败：{exc}")

            setattr(self, attr_name, None)

        self.record_camera_btn.setText("开始录制")
        self.record_camera_btn.setStyleSheet("")

        if getattr(self, "camera_service_started", False) and getattr(self, "camera_stream_has_frame", False):
            self.record_camera_btn.setEnabled(True)
        else:
            self.record_camera_btn.setEnabled(False)

        self.camera_status_label.setText(
            "录制已停止，视频已保存到本地：\n"
            f"color：{self.color_record_path}\n"
            f"depth：{self.depth_record_path}"
        )

    def _on_camera_record_error(self, text: str) -> None:
        self.append_log(text)

        if getattr(self, "camera_recording", False):
            self.camera_status_label.setText(f"录制发生错误，已停止录制：{text}")
            self.stop_camera_recording()
        else:
            self.camera_status_label.setText(f"录制发生错误：{text}")


    def _on_camera_record_done(self, path: str, frames: int) -> None:
        if frames > 0:
            self.append_log(f"录制完成：{path}，写入帧数：{frames}")
        else:
            self.append_log(f"录制结束但未写入有效帧：{path}")
        self.refresh_recordings_table()




    def _build_navigation_tab(self) -> QWidget:
        page = QWidget()

        layout = QVBoxLayout()
        layout.setContentsMargins(10,10,10,10)
        layout.setSpacing(10)

        group = QGroupBox("导航功能")
        group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        group_layout = QVBoxLayout()
        group_layout.setContentsMargins(14, 18, 14, 14)
        group_layout.setSpacing(10)

        title = QLabel("H1 导航建图")
        title.setStyleSheet(
            """
            QLabel {
                font-size: 18px;
                font-weight: 900;
                color: #0f172a;
            }
            """
        )

        summary = QLabel("点击按钮后，会单独打开导航建图窗口，并在远程机器人上启动 demo_h1。")
        summary.setWordWrap(True)
        summary.setStyleSheet(
            """
            QLabel {
                color: #334155;
                font-size: 14px;
                padding-bottom: 2px;
            }
            """
        )

        step_box = QGroupBox("执行流程")
        step_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        step_layout = QVBoxLayout()
        step_layout.setContentsMargins(12, 14, 12, 12)
        step_layout.setSpacing(6)

        step_1 = QLabel("1. 通过 SSH 登录机器人")
        step_2 = QLabel("2. 进入 unitree_slam/build 目录")
        step_3 = QLabel("3. 设置运行库路径")
        step_4 = QLabel("4. 使用 sudo 启动 demo_h1")

        for label in (step_1, step_2, step_3, step_4):
            label.setStyleSheet("color: #475569; font-size: 14px;")
            step_layout.addWidget(label)

        step_box.setLayout(step_layout)

        self.open_navigation_btn = QPushButton("打开导航建图窗口")
        self.open_navigation_btn.setMinimumHeight(42)
        self.open_navigation_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #2563eb;
                color: white;
                border: none;
                border-radius: 9px;
                font-size: 15px;
                font-weight: 800;
            }

            QPushButton:hover {
                background-color: #1d4ed8;
            }

            QPushButton:pressed {
                background-color: #1e40af;
            }
            """
        )
        self.open_navigation_btn.clicked.connect(self.open_navigation_window)
        tip = QLabel(
            "注意：本页面只负责打开导航建图窗口，并启动 / 停止远程 demo_h1。"
            "如果 demo_h1 自己打开图形页面或 Web 页面，显示方式仍由 demo_h1 决定。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet(
            """
            QLabel {
                color: #92400e;
                background-color: #fffbeb;
                border: 1px solid #fde68a;
                border-radius: 9px;
                padding: 9px 10px;
                font-size: 13px;
                font-weight: 700;
            }
            """
        )
        group_layout.addWidget(title)
        group_layout.addWidget(summary)
        group_layout.addWidget(step_box)
 
        group_layout.addWidget(self.open_navigation_btn)
        group_layout.addWidget(tip)

        group.setLayout(group_layout)

        layout.addWidget(group, 0, Qt.AlignTop)
        layout.addStretch(1)

        page.setLayout(layout)
        return page

        
    def open_navigation_window(self) -> None:
        if not hasattr(self, "navigation_window"):
            self.navigation_window = None

        if self.navigation_window is None:
            self.navigation_window = NavigationWindow(
                robot_ip=self.ip_edit.text().strip() or "192.168.123.162",
                robot_user="unitree",
                robot_password="Unitree0408",
                remote_dir="ws/unitree_slam/build",
                remote_iface="eth0",
                parent=self,
            )
            self.navigation_window.log_signal.connect(self.append_log)

        self.navigation_window.show()
        self.navigation_window.raise_()
        self.navigation_window.activateWindow()

        self.append_log("已打开导航建图窗口。")


    def _create_kv_table(self) -> QTableWidget:
        table = QTableWidget()
        table.setColumnCount(2)
        table.setHorizontalHeaderLabels(["字段", "值"])
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        return table

    def _fill_kv_table(self, table: QTableWidget, data: Dict[str, Any]) -> None:
        table.setRowCount(len(data))

        for row, key in enumerate(data.keys()):
            display_key = tr_ui_text(key)

            key_item = QTableWidgetItem(display_key)
            value_item = QTableWidgetItem(str(data.get(key, "")))

            key_item.setToolTip(str(key))
            value_item.setToolTip(str(data.get(key, "")))

            key_item.setFlags(key_item.flags() & ~Qt.ItemIsEditable)
            value_item.setFlags(value_item.flags() & ~Qt.ItemIsEditable)

            key_item.setTextAlignment(Qt.AlignCenter)

            table.setItem(row, 0, key_item)
            table.setItem(row, 1, value_item)

    def _fill_motor_table(self, columns: List[str], rows: List[Dict[str, Any]]) -> None:
        if not columns:
            self.motor_table.setColumnCount(1)
            self.motor_table.setRowCount(1)
            self.motor_table.setHorizontalHeaderLabels(["状态"])
            item = QTableWidgetItem("暂无电机数据")
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.motor_table.setItem(0, 0, item)
            return

        self.motor_table.setColumnCount(len(columns))
        self.motor_table.setHorizontalHeaderLabels([tr_ui_text(col) for col in columns])
        self.motor_table.setRowCount(len(rows))

        for row_index, row_data in enumerate(rows):
            for col_index, col_name in enumerate(columns):
                value = str(row_data.get(col_name, ""))
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                item.setToolTip(value)

                if col_name == "index":
                    item.setTextAlignment(Qt.AlignCenter)

                self.motor_table.setItem(row_index, col_index, item)

        self.motor_table.resizeColumnsToContents()


    def _build_lidar_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        # # ========== 左侧：LiDAR State ==========
        # left_panel = QWidget()
        # left_layout = QVBoxLayout()
        # left_layout.setContentsMargins(0, 0, 6, 0)
        # left_layout.setSpacing(8)

        # lidar_group = QGroupBox("激光雷达状态")
        # lidar_layout = QVBoxLayout()
        # lidar_layout.setContentsMargins(8, 10, 8, 8)
        # lidar_layout.setSpacing(8)

        # topic_row = QHBoxLayout()
        # topic_label = QLabel("状态 Topic：")

        # self.lidar_state_topic_edit = QLineEdit("rt/utlidar/lidar_state")
        # self.lidar_state_topic_edit.setPlaceholderText(
        #     "rt/utlidar/lidar_state"
        # )

        # topic_row.addWidget(topic_label)
        # topic_row.addWidget(self.lidar_state_topic_edit, stretch=1)

        # btn_row = QHBoxLayout()
        # self.start_lidar_btn = QPushButton("开始读取 LiDAR")
        # self.stop_lidar_btn = QPushButton("停止读取")

        # self.start_lidar_btn.clicked.connect(self.start_lidar_state)
        # self.stop_lidar_btn.clicked.connect(self.stop_lidar_state)

        # btn_row.addWidget(self.start_lidar_btn)
        # btn_row.addWidget(self.stop_lidar_btn)
        # btn_row.addStretch()

        # self.lidar_state_table = self._create_kv_table()
        # self.lidar_state_table.setMinimumWidth(360)

        # lidar_layout.addLayout(topic_row)
        # lidar_layout.addLayout(btn_row)
        # lidar_layout.addWidget(self.lidar_state_table, stretch=1)

        # lidar_group.setLayout(lidar_layout)

        # left_layout.addWidget(lidar_group)
        # left_panel.setLayout(left_layout)

        # ========== 右侧：PCD 点云地图 ==========
        right_panel = QWidget()
        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(6, 0, 0, 0)
        right_layout.setSpacing(8)

        pcd_group = QGroupBox("已导出 PCD 点云地图")
        pcd_layout = QVBoxLayout()
        pcd_layout.setContentsMargins(8, 10, 8, 8)
        pcd_layout.setSpacing(8)

        pcd_top_row = QHBoxLayout()

        # pcd_label = QLabel("PCD 文件：")
        # self.pcd_path_edit = QLineEdit(str(APP_DIR / "GlobalMap.pcd"))
        # self.pcd_path_edit.setPlaceholderText("请选择 GlobalMap.pcd")

        # pcd_label = QLabel("PCD 文件：")
        # self.pcd_path_edit = QLineEdit(str(LOCAL_PCD_PATH))
        # self.pcd_path_edit.setPlaceholderText("先从开发板获取 GlobalMap.pcd，再加载")
        # self.pcd_path_edit.setToolTip(
        #     f"远程文件：{BOARD_PCD_USER}@{BOARD_PCD_HOST}:{BOARD_PCD_REMOTE_PATH}\n"
        #     f"本地缓存：{LOCAL_PCD_PATH}"
        # )
        pcd_label = QLabel("PCD 文件：")
        self.pcd_path_edit = QLineEdit(str(LOCAL_PCD_PATH))
        self.pcd_path_edit.setPlaceholderText("点击“更新点云地图”后自动加载")
        self.pcd_path_edit.setReadOnly(True)


       
        self.fetch_pcd_btn = QPushButton("更新点云地图")
        self.browse_pcd_btn = QPushButton("本地选择")
        self.reload_pcd_btn = QPushButton("加载")


        self.zoom_in_pcd_btn = QPushButton("+")


        self.zoom_out_pcd_btn = QPushButton("-")
        self.save_pcd_btn = QPushButton("保存")
                
        self.pointcloud_view = PointCloudCanvas()
        self.pointcloud_view.setMinimumSize(420, 320)
        self.pointcloud_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # self.browse_pcd_btn.clicked.connect(self.browse_pcd_file)
        # self.reload_pcd_btn.clicked.connect(self.load_pcd_map)

        self.fetch_pcd_btn.clicked.connect(self.fetch_pcd_from_board)
        self.browse_pcd_btn.clicked.connect(self.browse_pcd_file)
        self.reload_pcd_btn.clicked.connect(self.load_pcd_map)

        
        self.zoom_in_pcd_btn.clicked.connect(self.pointcloud_view.zoom_in)
        self.zoom_out_pcd_btn.clicked.connect(self.pointcloud_view.zoom_out)
        self.save_pcd_btn.clicked.connect(self.save_pcd_view)

        # pcd_top_row.addWidget(pcd_label)
        # pcd_top_row.addWidget(self.pcd_path_edit, stretch=1)
        # pcd_top_row.addWidget(self.browse_pcd_btn)
        # pcd_top_row.addWidget(self.reload_pcd_btn)
        
        pcd_top_row.addWidget(pcd_label)
        pcd_top_row.addWidget(self.pcd_path_edit, stretch=1)
        pcd_top_row.addWidget(self.fetch_pcd_btn)
        pcd_top_row.addWidget(self.browse_pcd_btn)
        pcd_top_row.addWidget(self.reload_pcd_btn)

        pcd_top_row.addWidget(self.zoom_in_pcd_btn)
        pcd_top_row.addWidget(self.zoom_out_pcd_btn)
        pcd_top_row.addWidget(self.save_pcd_btn)


        self.pcd_info_label = QLabel()
        self.pcd_info_label.setVisible(False)

        pcd_layout.addLayout(pcd_top_row)
        pcd_layout.addWidget(self.pointcloud_view, stretch=1)

        pcd_group.setLayout(pcd_layout)

        right_layout.addWidget(pcd_group)
        right_panel.setLayout(right_layout)

        # splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([420, 760])

        layout.addWidget(splitter)
        page.setLayout(layout)

        QTimer.singleShot(300, self.load_pcd_map_if_exists)

        return page

    def save_pcd_view(self) -> None:
        path = self.pointcloud_view.save_current_view()

        if path:
            self.append_log(f"当前点云视角已保存：{path}")


    def _build_params_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self.param_table = QTableWidget()
        self.param_table.setColumnCount(5)
        self.param_table.setHorizontalHeaderLabels(
            ["参数名", "当前值", "待写入值", "说明", "可写"]
        )

        header = self.param_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)

        self.param_table.setAlternatingRowColors(True)
        self.param_table.verticalHeader().setVisible(False)
        self.param_table.setRowCount(len(self.PARAM_DEFS))

        for row, param in enumerate(self.PARAM_DEFS):
            self._set_param_row(row, param, param["default"], "")

        button_row = QHBoxLayout()
        button_row.setSpacing(8)

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

        layout.addWidget(self.param_table, stretch=1)
        layout.addLayout(button_row)

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
            QTableWidgetItem(str(param.get("name", param["key"]))),
            QTableWidgetItem(tr_ui_text(current_value)),
            QTableWidgetItem(str(pending_value)),
            QTableWidgetItem(str(param["desc"])),
            QTableWidgetItem("是" if param["writable"] else "否"),
        ]

        items[0].setToolTip(str(param["key"]))


        for col, item in enumerate(items):
            if col == 2 and param["writable"]:
                item.setFlags(item.flags() | Qt.ItemIsEditable)
            else:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)

            if col in (0, 1, 4):
                item.setTextAlignment(Qt.AlignCenter)

            self.param_table.setItem(row, col, item)
    
    def _get_config_from_ui(self) -> RobotConfig:

        lidar_state_topic = "rt/utlidar/lidar_state"
        if hasattr(self, "lidar_state_topic_edit"):
            lidar_state_topic = self.lidar_state_topic_edit.text().strip()

        pointcloud_file = str(APP_DIR / "GlobalMap.pcd")
        if hasattr(self, "pcd_path_edit"):
            pointcloud_file = self.pcd_path_edit.text().strip()

        return RobotConfig(
            robot_ip=self.ip_edit.text().strip(),
            port=int(self.port_spin.value()),
            network_interface=self.iface_edit.text().strip(),
            protocol=self.protocol_combo.currentData(),
            lowstate_topic=self.lowstate_topic_combo.currentText().strip(),
            lowstate_idl=self.lowstate_idl_combo.currentText().strip(),
            lidar_state_topic=lidar_state_topic,
            pointcloud_file=pointcloud_file,
        )

    def _set_combo_by_data(self, combo: QComboBox, value: str) -> None:
        for index in range(combo.count()):
            if combo.itemData(index) == value:
                combo.setCurrentIndex(index)
                return


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
            self._set_combo_by_data(self.protocol_combo, cfg.protocol)
            self.lowstate_topic_combo.setCurrentText(cfg.lowstate_topic)
            self.lowstate_idl_combo.setCurrentText(cfg.lowstate_idl)

            if hasattr(self, "lidar_state_topic_edit"):
                self.lidar_state_topic_edit.setText(cfg.lidar_state_topic)

            if hasattr(self, "pcd_path_edit"):
                self.pcd_path_edit.setText(cfg.pointcloud_file)


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
        self._set_robot_badge(connected)
        self.connection_label.setStyleSheet(
            "color: green; font-weight: 700;" if connected else "color: red; font-weight: 700;"
        )

        if hasattr(self, "cockpit_robot_value"):
            self.cockpit_robot_value.setText("在线" if connected else "离线")
            self.cockpit_robot_hint.setText("LowState 已连接" if connected else "等待设备连接")

    def update_robot_status(self, status: Dict[str, Any]) -> None:
        self.latest_robot_status = status
        self._last_robot_packet_time = time.monotonic()

        lowstate_main = status.get("lowstate_main", {})
        imu_state = status.get("imu_state", {})
        # bms_state = status.get("bms_state", {})
        motor_columns = status.get("motor_columns", [])
        motor_rows = status.get("motor_rows", [])

        self._fill_kv_table(self.lowstate_table, lowstate_main)
        self._fill_kv_table(self.imu_table, imu_state)
        # self._fill_kv_table(self.bms_table, bms_state)
        self._fill_motor_table(motor_columns, motor_rows)

        if hasattr(self, "cockpit_packet_value"):
            self.cockpit_packet_value.setText(str(lowstate_main.get("packet_count", "0")))

        if hasattr(self, "cockpit_voltage_value"):
            voltage = lowstate_main.get("power_v")
            voltage_text = self._metric_text(voltage, " V", 1)
            self.cockpit_voltage_value.setText(voltage_text)

        rpy = self._parse_vector_numbers(imu_state.get("rpy_deg", ""))
        values = [
            self.cockpit_roll_value if hasattr(self, "cockpit_roll_value") else None,
            self.cockpit_pitch_value if hasattr(self, "cockpit_pitch_value") else None,
            self.cockpit_yaw_value if hasattr(self, "cockpit_yaw_value") else None,
        ]
        for index, label in enumerate(values):
            if label is not None:
                label.setText(f"{rpy[index]:.1f}" if len(rpy) > index else "--")


    def _camera_stream_url(self) -> str:
        host = self.ip_edit.text().strip() or BOARD_PCD_HOST
        port = BOARD_REALSENSE_PORT

        kind = "combined"

        if hasattr(self, "camera_kind_combo"):
            kind = self.camera_kind_combo.currentData() or "combined"

        return f"http://{host}:{port}/stream/{kind}"


    def _on_camera_kind_changed(self) -> None:
        """
        切换“彩色+深度 / 仅彩色 / 仅深度”时：
        1. 立即更新地址栏；
        2. 如果当前正在显示视频，则重启本地视频流线程，切到新的 /stream/xxx。
        """
        if not hasattr(self, "camera_url_edit"):
            return

        url = self._camera_stream_url()
        self.camera_url_edit.setText(url)

        worker = getattr(self, "camera_worker", None)

        if worker is not None:
            self.append_log(f"切换 RealSense 视频流：{url}")
            self._start_camera_stream()



    def start_camera(self) -> None:
        start_process = getattr(self, "realsense_process", None)
        stop_process = getattr(self, "realsense_stop_process", None)

        if start_process is not None and start_process.state() != QProcess.NotRunning:
            QMessageBox.information(self, "提示", "RealSense 服务正在启动，请稍候。")
            return

        if stop_process is not None and stop_process.state() != QProcess.NotRunning:
            QMessageBox.information(self, "提示", "RealSense 服务正在停止，请稍候。")
            return

        # 启动前也先清理本地显示线程，避免旧线程残留
        if not self.stop_camera(clear_label=False):
            QMessageBox.warning(
                self,
                "视频线程未退出",
                "旧的视频显示线程还没有完全退出，暂不能重新启动。",
            )
            return

        # self._start_camera_after_remote_start = True
        # self.start_remote_realsense_service()
        # self.record_camera_btn.setEnabled(True)

        self.camera_service_started = False
        self.camera_stream_has_frame = False
        self.record_camera_btn.setEnabled(False)

        self._start_camera_after_remote_start = True
        self.start_remote_realsense_service()


 
    def start_remote_realsense_service(self) -> None:
        host = self.ip_edit.text().strip() or BOARD_PCD_HOST
        username = BOARD_PCD_USER
        password = BOARD_PCD_PASSWORD

        # 重要：
        # 这里不要再用 pkill -f start_realsense.py。
        # 否则可能会把正在执行 SSH 远程命令的 shell 一起杀掉，导致 exit_code=255。
        #
        # 新逻辑：
        # 1. 如果 8080 已经监听，直接认为服务已启动。
        # 2. 如果没有监听，再启动 start_realsense.py。
        # 3. 启动后把 PID 写入 pid 文件，后续停止时只 kill 这个 PID。
        remote_cmd = (
            "set -u; "
            "echo '[remote] user='$(whoami)' host='$(hostname); "

            f"mkdir -p {shlex.quote(BOARD_REALSENSE_REMOTE_DIR)}; "

            f"if [ ! -f {shlex.quote(BOARD_REALSENSE_REMOTE_PATH)} ]; then "
            f"  echo '[remote] missing script: {BOARD_REALSENSE_REMOTE_PATH}'; "
            "  exit 10; "
            "fi; "

            f"chmod +x {shlex.quote(BOARD_REALSENSE_REMOTE_PATH)}; "

            # 如果 8080 已经起来了，不要杀，直接复用。
            f"if ss -lntp 2>/dev/null | grep -q ':{BOARD_REALSENSE_PORT}'; then "
            "  echo '[remote] RealSense Web service already listening'; "
            f"  ss -lntp 2>/dev/null | grep ':{BOARD_REALSENSE_PORT}' || true; "
            "  echo '[remote] last log:'; "
            f"  tail -40 {shlex.quote(BOARD_REALSENSE_REMOTE_LOG)} 2>/dev/null || true; "
            "  exit 0; "
            "fi; "

            # 走到这里说明 8080 没起来，再启动。
            f"nohup /usr/bin/python3 {shlex.quote(BOARD_REALSENSE_REMOTE_PATH)} "
            f"> {shlex.quote(BOARD_REALSENSE_REMOTE_LOG)} 2>&1 < /dev/null & "
            "pid=$!; "
            f"echo $pid > {shlex.quote(BOARD_REALSENSE_REMOTE_DIR + '/start_realsense.pid')}; "
            "echo '[remote] start_realsense.py pid='$pid; "

            # 等待 8080 开始监听
            "i=0; "
            "while [ $i -lt 20 ]; do "
            f"  if ss -lntp 2>/dev/null | grep -q ':{BOARD_REALSENSE_PORT}'; then "
            "    echo '[remote] RealSense Web service started'; "
            f"    ss -lntp 2>/dev/null | grep ':{BOARD_REALSENSE_PORT}' || true; "
            "    echo '[remote] last log:'; "
            f"    tail -40 {shlex.quote(BOARD_REALSENSE_REMOTE_LOG)} 2>/dev/null || true; "
            "    exit 0; "
            "  fi; "

            # 如果进程提前退出，打印日志
            "  if ! kill -0 $pid 2>/dev/null; then "
            "    echo '[remote] RealSense process exited early'; "
            "    echo '[remote] last log:'; "
            f"    tail -160 {shlex.quote(BOARD_REALSENSE_REMOTE_LOG)} 2>/dev/null || true; "
            "    exit 4; "
            "  fi; "

            "  i=$((i+1)); "
            "  sleep 0.5; "
            "done; "

            f"echo '[remote] RealSense Web service did not listen on port {BOARD_REALSENSE_PORT}'; "
            "echo '[remote] ss output:'; "
            "ss -lntp 2>/dev/null || true; "
            "echo '[remote] last log:'; "
            f"tail -160 {shlex.quote(BOARD_REALSENSE_REMOTE_LOG)} 2>/dev/null || true; "
            "exit 3"
        )

        command = (
            "command -v sshpass >/dev/null 2>&1 || "
            "{ echo '本机缺少 sshpass，请先执行：sudo apt install sshpass'; exit 127; }; "
            f"sshpass -p {shlex.quote(password)} "
            "ssh -T "
            "-o StrictHostKeyChecking=no "
            "-o UserKnownHostsFile=/dev/null "
            "-o LogLevel=ERROR "
            "-o ConnectTimeout=8 "
            "-o NumberOfPasswordPrompts=1 "
            f"{shlex.quote(username + '@' + host)} {shlex.quote(remote_cmd)}"
        )

        self.realsense_output = ""
        self.start_camera_btn.setEnabled(False)
        self.start_camera_btn.setText("启动中...")
        self._set_camera_badge("启动中", "working")
        self.camera_status_label.setText("正在连接机器人端 RealSense 服务，请稍候…")
        self.append_log("正在启动机器人端 RealSense 服务。")

        process = QProcess(self)
        process.setProcessChannelMode(QProcess.MergedChannels)

        self.realsense_process = process

        process.readyReadStandardOutput.connect(self._on_realsense_process_output)
        process.readyReadStandardError.connect(self._on_realsense_process_output)
        process.finished.connect(self._on_realsense_start_finished)
        process.errorOccurred.connect(self._on_realsense_process_error)

        process.start("bash", ["-lc", command])


    def _on_realsense_process_output(self) -> None:
        process = getattr(self, "realsense_process", None)
        if process is None:
            return
        data = bytes(process.readAllStandardOutput()).decode("utf-8", errors="ignore")
        data += bytes(process.readAllStandardError()).decode("utf-8", errors="ignore")
        if not data:
            return
        self.realsense_output += data
        clean_text = clean_terminal_output(data)
        if clean_text.strip():
            self.append_log(clean_text.strip())

    def _on_realsense_start_finished(self, exit_code: int, exit_status) -> None:
        process = getattr(self, "realsense_process", None)
        if process is not None:
            process.deleteLater()
        self.realsense_process = None

        self.start_camera_btn.setEnabled(True)
        self.start_camera_btn.setText("启动实时画面")
        if exit_code != 0:
            output = clean_terminal_output(getattr(self, "realsense_output", "")).strip() or "没有返回详细错误信息。"
            self.camera_status_label.setText("实时画面启动失败，请检查机器人网络与相机服务。")
            self._set_camera_badge("连接失败", "error")
            self.camera_label.clear()
            self.camera_label.setText("暂未获取到实时画面\n请检查设备网络和相机服务")

            # 自动展示模式失败时不弹出模态窗口，避免遮挡领导演示界面。
            if not getattr(self, "_camera_auto_start", False):
                QMessageBox.warning(
                    self,
                    "RealSense 启动失败",
                    "无法启动机器人端 RealSense 服务，请检查：\n"
                    "1. 机器人 IP 是否正确\n"
                    "2. 上位机是否安装 sshpass\n"
                    "3. 机器人端 RealSense 是否正常枚举\n"
                    "4. 8080 端口是否被占用\n\n"
                    f"详细信息：\n{output}",
                )
            self._camera_auto_start = False
            self.append_log(f"RealSense 服务启动失败，exit_code={exit_code}")
            return
        
        self.camera_service_started = True
        self.camera_stream_has_frame = False
        self.record_camera_btn.setEnabled(False)

        self.camera_status_label.setText("相机服务已就绪，正在加载实时画面…")
        self._set_camera_badge("加载中", "working")
        self._camera_auto_start = False
        self.append_log("机器人端 RealSense 服务已启动。")

        if self._start_camera_after_remote_start:
            self._start_camera_stream()
        self._start_camera_after_remote_start = False

    def _on_realsense_process_error(self, error) -> None:
        self.start_camera_btn.setEnabled(True)
        self.start_camera_btn.setText("启动实时画面")
        self.camera_status_label.setText(f"相机服务启动错误：{error}")
        self._set_camera_badge("启动错误", "error")
        self._camera_auto_start = False
        self.append_log(f"RealSense QProcess 错误：{error}")


  
    # def _start_camera_stream(self) -> None:
    # # 启动新视频流前，必须先确认旧线程已经完全退出
    #     if not self.stop_camera(clear_label=False):
    #         QMessageBox.warning(
    #             self,
    #             "视频线程未退出",
    #             "旧的视频显示线程还没有完全退出。\n"
    #             "请稍等几秒后再重新启动，避免程序崩溃。",
    #         )
    #         return

    #     url = self._camera_stream_url()

    #     if hasattr(self, "camera_url_edit"):
    #         self.camera_url_edit.setText(url)

    #     self.camera_worker = MjpegStreamWorker(url, self)
    #     self.camera_worker.frame_signal.connect(self._on_camera_frame)
    #     self.camera_worker.log_signal.connect(self.append_log)
    #     self.camera_worker.error_signal.connect(self._on_camera_error)
    #     self.camera_worker.start()

    #     self.camera_status_label.setText(f"正在显示视频流：{url}")
    #     self.camera_label.clear()
    #     self.camera_label.setText("正在连接 RealSense 视频流...")
    
    def _start_camera_stream(self) -> None:
        if not self.stop_camera(clear_label=False):
            QMessageBox.warning(
                self,
                "视频线程未退出",
                "旧的视频显示线程还没有完全退出。\n"
                "请稍等几秒后再重新启动，避免程序崩溃。",
            )
            return

        self.camera_stream_has_frame = False

        if not getattr(self, "camera_recording", False):
            self.record_camera_btn.setEnabled(False)

        url = self._camera_stream_url()

        if hasattr(self, "camera_url_edit"):
            self.camera_url_edit.setText(url)

        self.camera_worker = MjpegStreamWorker(url, self)
        self.camera_worker.frame_signal.connect(self._on_camera_frame)
        self.camera_worker.log_signal.connect(self.append_log)
        self.camera_worker.error_signal.connect(self._on_camera_error)
        self.camera_worker.start()

        self.camera_status_label.setText("相机服务已连接，正在接收第一帧画面…")
        self._set_camera_badge("加载中", "working")
        self.camera_label.clear()
        self.camera_label.setText("正在连接 RealSense 实时画面…")


    
    def _on_camera_frame(self, image: QImage) -> None:
        if not hasattr(self, "camera_label"):
            return

        self.camera_stream_has_frame = True
        self._last_camera_image = image.copy()
        self._fps_frame_count += 1
        self._set_camera_badge("实时", "live")
        self.snapshot_camera_btn.setEnabled(True)

        if getattr(self, "camera_service_started", False) and not getattr(self, "camera_recording", False):
            self.record_camera_btn.setEnabled(True)

        pixmap = QPixmap.fromImage(image)

        scaled = pixmap.scaled(
            self.camera_label.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )

        self.camera_label.setPixmap(scaled)

        if getattr(self, "camera_recording", False):
            self.camera_status_label.setText(
                "正在实时显示并录制 color/depth 两路视频：\n"
                f"color：{self.color_record_path}\n"
                f"depth：{self.depth_record_path}"
            )
        else:
            self.camera_status_label.setText("实时画面运行中 · 可切换彩色/深度模式、截图或录制")


    def _on_camera_error(self, text: str) -> None:
        if getattr(self, "_closing_app", False):
            return

        self.camera_status_label.setText(text)
        self.camera_label.clear()
        self.camera_label.setText("视频流读取失败")
        self.snapshot_camera_btn.setEnabled(False)
        self._set_camera_badge("异常", "error")
        self.append_log(text)



    def stop_camera(self, clear_label: bool = True) -> bool:
        self.stop_camera_recording()
        self.record_camera_btn.setEnabled(False)
        if hasattr(self, "snapshot_camera_btn"):
            self.snapshot_camera_btn.setEnabled(False)
        self.camera_stream_has_frame = False

        worker = getattr(self, "camera_worker", None)

        if worker is not None:
            self.append_log("正在停止本地 RealSense 视频显示线程。")

            # 先从 self 上摘掉，避免停止过程中又被二次 stop 或重新连接
            self.camera_worker = None

            try:
                worker.frame_signal.disconnect(self._on_camera_frame)
            except Exception:
                pass

            try:
                worker.log_signal.disconnect(self.append_log)
            except Exception:
                pass

            try:
                worker.error_signal.disconnect(self._on_camera_error)
            except Exception:
                pass

            worker.stop()

            if not worker.wait(5000):
                self.append_log("视频流线程 5 秒内未退出。为避免 Qt 崩溃，本次操作中止。")
                self.camera_worker = worker
                return False

            worker.deleteLater()
            self.append_log("本地 RealSense 视频显示线程已停止。")

        if clear_label and hasattr(self, "camera_label"):
            self.camera_label.clear()
            self.camera_label.setText("RealSense 画面已停止")

        if hasattr(self, "camera_status_label"):
            self.camera_status_label.setText(
                "实时画面已暂停；相机服务仍可保持运行，点击“启动实时画面”可恢复。"
            )
        self._set_camera_badge("已暂停", "idle")

        return True

    

    def stop_remote_realsense_service(self, detached: bool = False) -> None:
        self.stop_camera_recording()
        self.record_camera_btn.setEnabled(False)
        self.camera_service_started = False
        self.camera_stream_has_frame = False

        # 关键：必须先停本地视频线程，再停机器人端 8080
        if not self.stop_camera(clear_label=True):
            QMessageBox.warning(
                self,
                "停止失败",
                "本地视频线程还没有完全退出。\n"
                "为避免程序崩溃，暂不停止机器人端相机服务。",
            )
            return

        host = self.ip_edit.text().strip() if hasattr(self, "ip_edit") else BOARD_PCD_HOST
        host = host or BOARD_PCD_HOST

        username = BOARD_PCD_USER
        password = BOARD_PCD_PASSWORD

        pid_file = BOARD_REALSENSE_REMOTE_DIR + "/start_realsense.pid"

        remote_cmd = (
            "echo '[remote] stopping RealSense service'; "

            # 先按 pid 文件停
            f"if [ -f {shlex.quote(pid_file)} ]; then "
            f"  pid=$(cat {shlex.quote(pid_file)} 2>/dev/null || true); "
            "  if [ -n \"$pid\" ] && kill -0 \"$pid\" 2>/dev/null; then "
            "    echo '[remote] kill pid from pidfile: '$pid; "
            "    kill \"$pid\" 2>/dev/null || true; "
            "    sleep 1; "
            "  fi; "
            f"  rm -f {shlex.quote(pid_file)}; "
            "fi; "

            # 如果 8080 还在，用端口反查 PID 停
            f"if ss -lntp 2>/dev/null | grep -q ':{BOARD_REALSENSE_PORT}'; then "
            f"  pid=$(ss -lntp 2>/dev/null | grep ':{BOARD_REALSENSE_PORT}' "
            "| sed -n 's/.*pid=\\([0-9][0-9]*\\).*/\\1/p' | head -n 1); "
            "  if [ -n \"$pid\" ] && kill -0 \"$pid\" 2>/dev/null; then "
            "    echo '[remote] kill pid from port listener: '$pid; "
            "    kill \"$pid\" 2>/dev/null || true; "
            "    sleep 1; "
            "  fi; "
            "fi; "

            # 等待端口释放
            "i=0; "
            "while [ $i -lt 10 ]; do "
            f"  if ! ss -lntp 2>/dev/null | grep -q ':{BOARD_REALSENSE_PORT}'; then "
            "    echo '[remote] RealSense service stopped'; "
            "    exit 0; "
            "  fi; "
            "  i=$((i+1)); "
            "  sleep 0.3; "
            "done; "

            "echo '[remote] RealSense service may still be listening'; "
            f"ss -lntp 2>/dev/null | grep ':{BOARD_REALSENSE_PORT}' || true; "
            "exit 2"
        )

        command = (
            "command -v sshpass >/dev/null 2>&1 || "
            "{ echo '本机缺少 sshpass，请先执行：sudo apt install sshpass'; exit 127; }; "
            f"sshpass -p {shlex.quote(password)} "
            "ssh -T "
            "-o StrictHostKeyChecking=no "
            "-o UserKnownHostsFile=/dev/null "
            "-o LogLevel=ERROR "
            "-o ConnectTimeout=5 "
            "-o NumberOfPasswordPrompts=1 "
            f"{shlex.quote(username + '@' + host)} {shlex.quote(remote_cmd)}"
        )

        if detached:
            # 关闭窗口时静默停止机器人端服务。
            # 关键：startDetached 默认会继承当前终端 stdout/stderr，
            # 所以必须在 shell 里重定向，否则终端会打印 [remote] stopping...
            silent_command = f"({command}) >/dev/null 2>&1 < /dev/null"
            QProcess.startDetached("bash", ["-lc", silent_command])
            return

        self.realsense_stop_output = ""

        self.start_camera_btn.setEnabled(False)
        self.stop_camera_btn.setEnabled(False)
        self.stop_remote_camera_btn.setEnabled(False)

        self.camera_status_label.setText("正在停止机器人端 RealSense 服务。")
        self.append_log("正在停止机器人端 RealSense 服务。")

        process = QProcess(self)
        process.setProcessChannelMode(QProcess.MergedChannels)
        self.realsense_stop_process = process

        process.readyReadStandardOutput.connect(self._on_realsense_stop_output)
        process.readyReadStandardError.connect(self._on_realsense_stop_output)
        process.finished.connect(self._on_realsense_stop_finished)
        process.errorOccurred.connect(self._on_realsense_stop_error)

        process.start("bash", ["-lc", command])

    def _on_realsense_stop_output(self) -> None:
        process = getattr(self, "realsense_stop_process", None)

        if process is None:
            return

        data = bytes(process.readAllStandardOutput()).decode("utf-8", errors="ignore")
        data += bytes(process.readAllStandardError()).decode("utf-8", errors="ignore")

        if not data:
            return

        self.realsense_stop_output += data

        clean_text = clean_terminal_output(data)

        if clean_text.strip():
            self.append_log(clean_text.strip())


    def _on_realsense_stop_finished(self, exit_code: int, exit_status) -> None:
        self.start_camera_btn.setEnabled(True)
        self.stop_camera_btn.setEnabled(True)
        self.stop_remote_camera_btn.setEnabled(True)

        output = clean_terminal_output(getattr(self, "realsense_stop_output", "")).strip()

        process = getattr(self, "realsense_stop_process", None)
        if process is not None:
            process.deleteLater()

        self.realsense_stop_process = None

        if exit_code == 0:
            self.camera_status_label.setText("机器人端 RealSense 服务已停止。")
            self.append_log("机器人端 RealSense 服务已停止。")
            return

        self.camera_status_label.setText("机器人端 RealSense 服务停止可能未完全成功。")
        self.append_log(f"停止机器人端 RealSense 服务返回 exit_code={exit_code}")

        if output:
            QMessageBox.warning(
                self,
                "RealSense 停止提示",
                f"停止机器人端 RealSense 服务可能未完全成功。\n\n详细信息：\n{output}",
            )


    def _on_realsense_stop_error(self, error) -> None:
        self.start_camera_btn.setEnabled(True)
        self.stop_camera_btn.setEnabled(True)
        self.stop_remote_camera_btn.setEnabled(True)

        self.camera_status_label.setText(f"停止机器人端 RealSense 服务时 QProcess 错误：{error}")
        self.append_log(f"停止机器人端 RealSense 服务 QProcess 错误：{error}")



    def start_lidar_state(self) -> None:
        if self.lidar_worker is not None and self.lidar_worker.isRunning():
            QMessageBox.information(self, "提示", "LiDAR 状态读取已经在运行。")
            return

        topic = self.lidar_state_topic_edit.text().strip()

        if not topic:
            QMessageBox.warning(self, "提示", "请填写 LiDAR State Topic。")
            return

        cfg = self._get_config_from_ui()
        cfg.lidar_state_topic = topic

        self.lidar_worker = LidarStateWorker(cfg)
        self.lidar_worker.status_signal.connect(self.update_lidar_state_table)
        self.lidar_worker.log_signal.connect(self.append_log)
        self.lidar_worker.error_signal.connect(self.on_lidar_error)
        self.lidar_worker.start()

        self.append_log(f"正在读取 LiDAR State：{topic}")


    def stop_lidar_state(self) -> None:
        if self.lidar_worker is not None:
            self.lidar_worker.stop()

            if not self.lidar_worker.wait(3000):
                self.append_log("LiDAR 状态线程 3 秒内未完全退出。")

            self.lidar_worker = None

        self.append_log("已停止 LiDAR 状态读取。")


    def update_lidar_state_table(self, data: Dict[str, Any]) -> None:
        self._fill_kv_table(self.lidar_state_table, data)


    def on_lidar_error(self, text: str) -> None:
        self.append_log(text)
        QMessageBox.warning(self, "LiDAR 状态错误", text)

    def fetch_pcd_from_board(self) -> None:
        """
        从开发板后台获取 GlobalMap.pcd。
        用户点击按钮后，不弹用户名/密码窗口。
        下载成功后自动加载本地点云文件。
        """

        if getattr(self, "pcd_download_process", None) is not None:
            process = self.pcd_download_process
            if process.state() != QProcess.NotRunning:
                QMessageBox.information(self, "提示", "点云地图正在更新，请稍候。")
                return

        host = self.ip_edit.text().strip() or BOARD_PCD_HOST
        username = BOARD_PCD_USER
        password = BOARD_PCD_PASSWORD
        remote_path = BOARD_PCD_REMOTE_PATH
        local_path = LOCAL_PCD_PATH

        try:
            local_path.parent.mkdir(parents=True, exist_ok=True)

            remote_spec = f"{username}@{host}:{remote_path}"

            command = (
                "command -v sshpass >/dev/null 2>&1 || "
                "{ echo '本机缺少 sshpass，请先执行：sudo apt install sshpass'; exit 127; }; "
                f"sshpass -p {shlex.quote(password)} "
                "scp -q "
                "-o StrictHostKeyChecking=no "
                "-o UserKnownHostsFile=/dev/null "
                "-o ConnectTimeout=8 "
                f"{shlex.quote(remote_spec)} "
                f"{shlex.quote(str(local_path))}"
            )

            self.fetch_pcd_btn.setEnabled(False)
            self.fetch_pcd_btn.setText("更新中...")
            self.pcd_download_output = ""

            self.append_log("正在获取点云地图，请稍候。")

            process = QProcess(self)
            process.setProcessChannelMode(QProcess.MergedChannels)

            self.pcd_download_process = process

            process.readyReadStandardOutput.connect(self._on_pcd_download_output)
            process.readyReadStandardError.connect(self._on_pcd_download_output)
            process.finished.connect(self._on_pcd_download_finished)
            process.errorOccurred.connect(self._on_pcd_download_error)

            process.start("bash", ["-lc", command])

        except Exception as exc:
            self.fetch_pcd_btn.setEnabled(True)
            self.fetch_pcd_btn.setText("更新点云地图")

            QMessageBox.warning(
                self,
                "点云地图更新失败",
                f"无法获取点云地图：\n{exc}",
            )
            self.append_log(f"点云地图更新失败：{exc}")


    def _on_pcd_download_output(self) -> None:
        process = getattr(self, "pcd_download_process", None)

        if process is None:
            return

        data = bytes(process.readAllStandardOutput()).decode("utf-8", errors="ignore")
        data += bytes(process.readAllStandardError()).decode("utf-8", errors="ignore")

        if data:
            self.pcd_download_output = getattr(self, "pcd_download_output", "") + data


    def _on_pcd_download_finished(self, exit_code: int, exit_status) -> None:
        self.fetch_pcd_btn.setEnabled(True)
        self.fetch_pcd_btn.setText("更新点云地图")

        if exit_code != 0:
            output = clean_terminal_output(
                getattr(self, "pcd_download_output", "")
            ).strip()

            if not output:
                output = "没有返回详细错误信息。"

            QMessageBox.warning(
                self,
                "点云地图更新失败",
                "点云地图获取失败，请检查：\n"
                "1. 开发板是否在线\n"
                "2. IP 是否正确\n"
                "3. 远程 GlobalMap.pcd 是否存在\n"
                "4. 用户名或密码是否正确\n\n"
                f"详细信息：\n{output}",
            )

            self.append_log(f"点云地图更新失败，exit_code={exit_code}")
            return

        if not LOCAL_PCD_PATH.exists() or LOCAL_PCD_PATH.stat().st_size <= 0:
            QMessageBox.warning(
                self,
                "点云地图更新失败",
                f"文件下载完成，但本地文件无效：\n{LOCAL_PCD_PATH}",
            )
            self.append_log("点云地图文件无效。")
            return

        self.pcd_path_edit.setText(str(LOCAL_PCD_PATH))
        self.append_log(f"点云地图已更新：{LOCAL_PCD_PATH}")

        self.load_pcd_map()


    def _on_pcd_download_error(self, error) -> None:
        self.fetch_pcd_btn.setEnabled(True)
        self.fetch_pcd_btn.setText("更新点云地图")

        QMessageBox.warning(
            self,
            "点云地图更新错误",
            f"点云地图更新进程启动失败：{error}",
        )

        self.append_log(f"点云地图更新进程错误：{error}")


    def _on_pcd_download_output(self) -> None:
        process = getattr(self, "pcd_download_process", None)

        if process is None:
            return

        data = bytes(process.readAllStandardOutput()).decode("utf-8", errors="ignore")
        data += bytes(process.readAllStandardError()).decode("utf-8", errors="ignore")

        if not data:
            return

        self.pcd_download_output = getattr(self, "pcd_download_output", "") + data

        clean_text = clean_terminal_output(data)

        if clean_text.strip():
            self.append_log(clean_text.strip())


    def _on_pcd_download_finished(self, exit_code: int, exit_status) -> None:
        if exit_code != 0:
            output = getattr(self, "pcd_download_output", "").strip()

            text = (
                "从开发板下载 GlobalMap.pcd 失败。\n\n"
                f"远程路径：{BOARD_PCD_REMOTE_PATH}\n"
                f"退出码：{exit_code}\n\n"
                f"{output}"
            )

            QMessageBox.warning(self, "PCD 下载失败", text)
            self.append_log(f"PCD 下载失败，exit_code={exit_code}")
            return

        self.pcd_path_edit.setText(str(LOCAL_PCD_PATH))
        self.pcd_path_edit.setToolTip(
            f"远程文件：{BOARD_PCD_USER}@{BOARD_PCD_HOST}:{BOARD_PCD_REMOTE_PATH}\n"
            f"本地缓存：{LOCAL_PCD_PATH}"
        )

        self.append_log(f"已从开发板下载 PCD 到本机：{LOCAL_PCD_PATH}")

        QMessageBox.information(
            self,
            "PCD 下载成功",
            f"已从开发板下载：\n{BOARD_PCD_REMOTE_PATH}\n\n"
            f"保存到本机：\n{LOCAL_PCD_PATH}",
        )

        self.load_pcd_map()


    def _on_pcd_download_error(self, error) -> None:
        self.append_log(f"PCD 下载进程错误：{error}")
        QMessageBox.warning(
            self,
            "PCD 下载错误",
            f"启动 SCP 下载进程失败：{error}",
        )


    def browse_pcd_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 PCD 点云文件",
            str(APP_DIR),
            "Point Cloud (*.pcd);;All Files (*)",
        )

        if not path:
            return

        self.pcd_path_edit.setText(path)
        self.load_pcd_map()


    def load_pcd_map_if_exists(self) -> None:
        if not hasattr(self, "pcd_path_edit"):
            return

        path = self.pcd_path_edit.text().strip()

        if path and Path(path).exists():
            self.load_pcd_map()
        else:
            self.append_log("本机未找到点云地图，请点击“更新点云地图”。")


    def load_pcd_map(self) -> None:
        path = self.pcd_path_edit.text().strip()

        if not path:
            QMessageBox.warning(self, "提示", "请先更新或选择 PCD 文件。")
            return

        if not Path(path).exists():
            QMessageBox.warning(
                self,
                "PCD 文件不存在",
                f"本机没有找到点云文件：\n{path}\n\n"
                "请点击“更新点云地图”。"
            )
            return

        try:
            info = self.pointcloud_view.plot_pcd(path)
            self.append_log(info)

        except Exception as exc:
            text = f"加载 PCD 点云失败：{exc}"
            self.pointcloud_view.show_message(text)
            self.append_log(text)
            QMessageBox.warning(self, "PCD 加载失败", text)


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

    def append_log(self, text: str) -> None:
        clean = str(text).strip()
        if not clean:
            return

        # 不再显示“系统运行信息/设备日志”终端面板。
        # 简短状态给状态栏，演示相关节点进入事件时间轴。
        if hasattr(self, "status") and self.status is not None:
            brief = " ".join(clean.split())
            self.status.showMessage(brief[:160], 5000)

        self._record_event(clean)


   
    def closeEvent(self, event):
        self._fast_close_cleanup()
        event.accept()




def main() -> int:
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    redirect_native_console_output()
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    setup_matplotlib_chinese_font()
    app = QApplication(sys.argv)
    app.setApplicationName("H1 Robot Vision")

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
