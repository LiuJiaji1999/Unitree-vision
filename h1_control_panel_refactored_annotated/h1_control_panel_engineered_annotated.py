#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
H1 机器人控制面板 - 工程化精简注释版。

本版本做了三件事：
1. 删除未启用的参数页、视频流占位、大段旧注释和未使用导入。
2. 按“配置 / 工具函数 / 数据解析 / 后台线程 / UI / 程序入口”组织代码。
3. 对每个可执行代码块添加中文注释，便于继续拆分成多文件工程。

运行：
    pip install PyQt5 matplotlib numpy
    python3 h1_control_panel_engineered_annotated.py
"""

from __future__ import annotations  # 允许类型注解引用尚未定义的类。

import hashlib  # 用于演示账号密码哈希。
import json  # 用于读写 users.json 和 h1_config.json。
import math  # 用于角度转换和点云抽样计算。
import random  # 用于 mock 模式生成模拟数据。
import re  # 用于清洗终端输出中的 ANSI 控制码。
import shlex  # 用于安全拼接 SSH 命令。
import sys  # 用于 Qt argv 和程序退出码。
import time  # 用于线程时间、超时判断和 mock 时间。
from dataclasses import asdict, dataclass  # 用于配置对象和 JSON 保存。
from datetime import datetime  # 用于日志时间戳。
from pathlib import Path  # 用于跨平台路径处理。
from typing import Any, Dict, List, Optional, Sequence, Tuple  # 用于类型提示。

import numpy as np  # 用于读取和处理 PCD 点云。
from matplotlib import font_manager, rcParams  # 用于设置 Matplotlib 中文字体。
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas  # Qt 内嵌 Matplotlib 画布。
from matplotlib.figure import Figure  # Matplotlib 图对象。
from matplotlib.ticker import MaxNLocator  # 用于控制坐标轴刻度数量。
from PyQt5.QtCore import QObject, QProcess, QThread, QTimer, Qt, pyqtSignal  # Qt 核心类。
from PyQt5.QtGui import QFont  # Qt 字体类。
from PyQt5.QtWidgets import (  # Qt 常用控件。
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QSplitter, QStatusBar, QTabWidget, QTableWidget, QTableWidgetItem,
    QTextEdit, QVBoxLayout, QWidget,
)

APP_DIR = Path(__file__).resolve().parent  # 程序所在目录。
USERS_FILE = APP_DIR / "users.json"  # 用户账号文件。
CONFIG_FILE = APP_DIR / "h1_config.json"  # 连接配置文件。
ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")  # 标准 ANSI 控制码。
BROKEN_COLOR_RE = re.compile(r"(?<!\w)(?:\[[0-9;]*m|[0-9;]*m)")  # 残缺颜色码。
KEY_RE = re.compile(r"Key\s+pressed\.", re.IGNORECASE)  # demo 输出中无用的按键提示。

UI_TEXT = {  # 字段到中文文案的映射。
    "update_time": "更新时间", "packet_count": "数据包计数", "topic": "主题",
    "idl_type": "数据类型", "index": "编号", "head": "帧头",
    "foot_force": "足端力", "tick": "计时器", "wireless_remote": "遥控器原始数据",
    "bit_flag": "组件状态", "adc_reel": "卷线器电流", "temperature_ntc1": "主板中心温度",
    "temperature_ntc2": "自动充电温度", "power_v": "电池电压", "power_a": "电池电流",
    "fan_frequency": "风扇转速", "crc": "校验位", "imu_state": "惯性测量单元状态",
    "bms_state": "电池管理系统状态", "bms_status_display": "电池状态",
    "bms_soc_display": "电池电量", "motor_state_count": "电机数量",
    "battery_voltage_display": "电池电压显示", "battery_current_display": "电池电流显示",
    "quaternion": "四元数", "rpy": "姿态角", "rpy_deg": "姿态角（度）",
    "gyroscope": "陀螺仪", "accelerometer": "加速度计", "temperature": "温度",
    "mode": "模式", "q": "关节位置", "dq": "关节速度", "ddq": "关节加速度",
    "tau_est": "估算力矩", "lost": "通信丢失", "error_flag": "错误标志",
    "comm_frequency": "通信频率", "stamp": "时间戳", "firmware_version": "固件版本",
    "software_version": "软件版本", "sdk_version": "SDK 版本", "sys_rotation_speed": "系统转速",
    "com_rotation_speed": "通信转速", "error_state": "错误状态",
    "error_state_text": "错误状态说明", "cloud_frequency": "点云频率",
    "cloud_packet_loss_rate": "点云丢包率", "cloud_size": "点云数量",
    "cloud_scan_num": "点云扫描帧数", "imu_frequency": "惯导频率",
    "imu_packet_loss_rate": "惯导丢包率", "imu_rpy": "惯导姿态角",
    "imu_rpy_deg": "惯导姿态角（度）", "serial_recv_stamp": "串口接收时间戳",
    "serial_buffer_size": "串口缓存大小", "serial_buffer_read": "串口已读大小",
}

BMS_STATUS = {  # BMS 状态码说明。
    0: "SAFE（未开启电池）", 1: "WAKE_UP（唤醒事件）", 6: "PRECHG（预充电）",
    7: "CHG（充电）", 8: "DCHG（放电）", 9: "SELF_DCHG（自放电）",
    11: "ALARM（警告）", 12: "RESET_ALARM（等待复位）", 13: "AUTO_RECOVERY（自动恢复）",
}

_DDS_INITIALIZED = False  # DDS 是否已初始化。
_DDS_IFACE = ""  # DDS 首次绑定的网卡。


@dataclass
class RobotConfig:
    """机器人运行配置。"""
    robot_ip: str = "192.168.123.162"  # 机器人 IP。
    port: int = 8080  # 预留端口。
    network_interface: str = "enx9c69d3565ef9"  # 本机 DDS 网卡。
    protocol: str = "sdk2"  # mock 或 sdk2。
    timeout_ms: int = 2000  # 预留超时。
    lowstate_topic: str = "rt/lowstate"  # LowState topic。
    lowstate_idl: str = "unitree_go"  # LowState IDL。
    lidar_state_topic: str = "rt/utlidar/lidar_state"  # LiDAR topic。
    pointcloud_file: str = str(APP_DIR / "GlobalMap.pcd")  # 默认 PCD 文件。


def now_text() -> str:
    """返回日志时间。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # 统一时间格式。


def tr(text: Any) -> str:
    """字段名翻译。"""
    return UI_TEXT.get(str(text), str(text))  # 映射不到则原样返回。


def setup_matplotlib_font() -> None:
    """设置 Matplotlib 中文字体。"""
    for p in ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"]:  # 常见字体路径。
        path = Path(p)  # 转成 Path。
        if path.exists():  # 如果字体存在。
            font_manager.fontManager.addfont(str(path))  # 注册字体。
            name = font_manager.FontProperties(fname=str(path)).get_name()  # 获取字体名。
            rcParams["font.sans-serif"] = [name, "DejaVu Sans"]  # 设置优先字体。
            rcParams["axes.unicode_minus"] = False  # 修复负号显示。
            return  # 设置成功后返回。
    rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "Microsoft YaHei", "SimHei", "DejaVu Sans"]  # 兜底字体名。
    rcParams["axes.unicode_minus"] = False  # 修复负号显示。


def clean_terminal(text: str) -> str:
    """清洗远程终端输出。"""
    text = str(text).replace("\r\n", "\n").replace("\r", "\n")  # 统一换行。
    text = ANSI_RE.sub("", text)  # 删除 ANSI 控制码。
    text = BROKEN_COLOR_RE.sub("", text)  # 删除残缺颜色码。
    lines = []  # 保存有效行。
    for line in text.splitlines():  # 按行处理。
        if not line.strip():  # 跳过空行。
            continue
        if KEY_RE.fullmatch(line.strip()):  # 跳过 Key pressed 行。
            continue
        lines.append(line)  # 保存有效行。
    return "\n".join(lines)  # 拼回文本。


def safe_get(obj: Any, name: str, default: Any = None) -> Any:
    """安全读取 SDK 对象字段。"""
    try:  # getattr 可能因为 SDK 生成类异常而失败。
        return getattr(obj, name, default)  # 读取字段。
    except Exception:  # 读取失败。
        return default  # 返回默认值。


def to_list(value: Any) -> List[Any]:
    """把 SDK 数组转换为 list。"""
    if value is None:  # None 转空列表。
        return []
    try:  # 尝试迭代。
        return list(value)  # 返回列表。
    except Exception:  # 不可迭代。
        return [value]  # 单值包装。


def as_int(value: Any) -> Optional[int]:
    """安全转 int。"""
    try:  # 尝试转换。
        return int(value) if value is not None else None  # None 保持 None。
    except Exception:  # 转换失败。
        return None  # 返回 None。


def as_float(value: Any) -> Optional[float]:
    """安全转 float。"""
    try:  # 尝试转换。
        return float(value) if value is not None else None  # None 保持 None。
    except Exception:  # 转换失败。
        return None  # 返回 None。


def fmt(value: Any, digits: int = 3) -> str:
    """格式化浮点文本。"""
    n = as_float(value)  # 转成浮点。
    return "N/A" if n is None else f"{n:.{digits}f}"  # 无效值显示 N/A。


def display(value: Any, max_len: int = 1200) -> str:
    """把任意字段转换为表格文本。"""
    if value is None:  # None 统一显示。
        return "N/A"
    if isinstance(value, float):  # 浮点保留 6 位。
        return f"{value:.6f}"
    if isinstance(value, (int, bool, str)):  # 基础类型直接显示。
        return str(value)
    try:  # 尝试处理数组。
        text = "[" + ", ".join(display(v, 80) for v in list(value)) + "]"  # 递归格式化数组。
    except Exception:  # 不是数组时。
        text = object_summary(value)  # 使用对象摘要。
    return text[:max_len] + " ..." if len(text) > max_len else text  # 限制长度。


def vector_text(value: Any, digits: int = 3) -> str:
    """格式化向量。"""
    arr = to_list(value)  # 转列表。
    return "N/A" if not arr else "[" + ", ".join(fmt(v, digits) if as_float(v) is not None else str(v) for v in arr) + "]"  # 生成向量文本。


def rpy_deg_text(value: Any) -> str:
    """RPY 弧度转角度。"""
    rpy = to_list(value)  # 转列表。
    if len(rpy) < 3:  # 不足三轴。
        return "N/A"
    nums = [as_float(v) for v in rpy[:3]]  # 转浮点。
    if any(v is None for v in nums):  # 有无效值。
        return "N/A"
    return vector_text([math.degrees(v) for v in nums], 2)  # 返回角度文本。


def temp_text(value: Any) -> str:
    """显示 int8/uint8 温度。"""
    t = as_int(value)  # 转整数。
    if t is None:  # 无效值。
        return "N/A"
    if t > 127:  # uint8 转 int8。
        t -= 256
    return f"{t} ℃"  # 返回摄氏度。


def field_names(obj: Any, preferred: Sequence[str] = ()) -> List[str]:
    """获取对象字段名。"""
    if obj is None:  # 无对象。
        return []
    ann = getattr(obj.__class__, "__annotations__", {})  # 优先用注解。
    names = list(ann.keys()) if ann else [n for n in dir(obj) if not n.startswith("_") and not callable(safe_get(obj, n, None))]  # 取字段。
    ordered = [n for n in preferred if n in names]  # 常用字段优先。
    ordered += [n for n in names if n not in ordered]  # 补齐其他字段。
    return ordered  # 返回字段名。


def object_summary(obj: Any) -> str:
    """生成 SDK 对象摘要。"""
    if obj is None:  # 空对象。
        return "N/A"
    if isinstance(obj, (int, float, bool, str)):  # 基础类型。
        return display(obj)
    parts = []  # 保存字段摘要。
    for name in field_names(obj)[:12]:  # 最多展示 12 个字段。
        value = safe_get(obj, name, None)  # 读取字段。
        parts.append(f"{name}={display(value, 60)}")  # 添加摘要。
    return "{" + ", ".join(parts) + ("..." if len(parts) >= 12 else "") + "}"  # 返回摘要。


def object_table(obj: Any, preferred: Sequence[str] = ()) -> Dict[str, str]:
    """对象转键值表。"""
    data: Dict[str, str] = {}  # 初始化字典。
    for name in field_names(obj, preferred):  # 遍历字段。
        data[name] = display(safe_get(obj, name, None))  # 保存字段显示值。
    return data  # 返回表格数据。


def bms_status_text(value: Any) -> str:
    """BMS 状态码转文字。"""
    code = as_int(value)  # 转整数。
    return "N/A" if code is None else f"{code} {BMS_STATUS.get(code, '未知状态')}"  # 返回状态文本。


def cell_summary(cell_vol: Any) -> str:
    """电芯电压概览。"""
    vals = []  # 保存有效电芯。
    for i, raw in enumerate(to_list(cell_vol)[:15], 1):  # 遍历最多 15 节。
        mv = as_int(raw)  # 原始 mV。
        if mv and mv > 0:  # 有效电压。
            vals.append((i, mv / 1000.0))  # 转为 V。
    if not vals:  # 无有效数据。
        return "N/A"
    total = sum(v for _, v in vals)  # 总电压。
    mn = min(vals, key=lambda x: x[1])  # 最低单体。
    mx = max(vals, key=lambda x: x[1])  # 最高单体。
    return f"有效单体数={len(vals)}，估算总电压={total:.2f} V，最低=第{mn[0]:02d}节 {mn[1]:.3f} V，最高=第{mx[0]:02d}节 {mx[1]:.3f} V，压差={(mx[1]-mn[1])*1000:.0f} mV"  # 摘要文本。


def extract_motor_rows(motor_state: Any) -> Dict[str, Any]:
    """提取电机表格。"""
    columns = ["index", "mode", "q", "dq", "ddq", "tau_est", "temperature", "lost", "error_flag", "comm_frequency"]  # 表头。
    rows = []  # 行数据。
    for index, motor in enumerate(to_list(motor_state)):  # 遍历电机。
        reserve = to_list(safe_get(motor, "reserve", []))  # reserve 字段。
        rows.append({  # 添加一行。
            "index": str(index), "mode": display(safe_get(motor, "mode", None)),
            "q": display(safe_get(motor, "q", None)), "dq": display(safe_get(motor, "dq", None)),
            "ddq": display(safe_get(motor, "ddq", None)), "tau_est": display(safe_get(motor, "tau_est", None)),
            "temperature": display(safe_get(motor, "temperature", None)), "lost": display(safe_get(motor, "lost", None)),
            "error_flag": display(reserve[0] if len(reserve) > 0 else None),
            "comm_frequency": display(reserve[1] if len(reserve) > 1 else None),
        })
    return {"columns": columns, "rows": rows}  # 返回表头和行。


def extract_low_state(msg: Any, cfg: RobotConfig, count: int) -> Dict[str, Any]:
    """LowState 消息转 UI 数据。"""
    imu = safe_get(msg, "imu_state", None)  # IMU 子结构。
    bms = safe_get(msg, "bms_state", None)  # BMS 子结构。
    motors = safe_get(msg, "motor_state", [])  # 电机数组。
    main = {"update_time": now_text(), "packet_count": str(count), "topic": cfg.lowstate_topic, "idl_type": cfg.lowstate_idl}  # 主信息。
    for name in ["head", "foot_force", "tick", "wireless_remote", "bit_flag", "adc_reel", "temperature_ntc1", "temperature_ntc2", "power_v", "power_a", "fan_frequency", "crc"]:  # 主字段。
        value = safe_get(msg, name, None)  # 读取字段。
        main[name] = temp_text(value) if name.startswith("temperature_ntc") else display(value)  # 温度特殊处理。
    main["imu_state"] = object_summary(imu)  # IMU 摘要。
    main["bms_state"] = object_summary(bms)  # BMS 摘要。
    main["bms_status_display"] = bms_status_text(safe_get(bms, "status", None))  # BMS 状态。
    soc = as_int(safe_get(bms, "soc", None))  # SOC。
    main["bms_soc_display"] = f"{soc} %" if soc is not None else "N/A"  # SOC 显示。
    main["bms_cell_voltage_summary"] = cell_summary(safe_get(bms, "cell_vol", None))  # 电芯摘要。
    main["motor_state_count"] = str(len(to_list(motors)))  # 电机数量。
    pv = as_float(safe_get(msg, "power_v", None))  # 总电压。
    pa = as_float(safe_get(msg, "power_a", None))  # 总电流。
    main["battery_voltage_display"] = f"{pv:.2f} V" if pv is not None else "N/A"  # 电压显示。
    main["battery_current_display"] = f"{pa:.2f} A" if pa is not None else "N/A"  # 电流显示。
    imu_table = object_table(imu, ["quaternion", "rpy", "gyroscope", "accelerometer", "temperature"])  # IMU 表。
    imu_table["rpy_deg"] = rpy_deg_text(safe_get(imu, "rpy", None))  # 角度制 RPY。
    bms_table = {  # BMS 表。
        "LowState 总电压 power_v": main["battery_voltage_display"],
        "LowState 总电流 power_a": main["battery_current_display"],
        "BMS 状态": main["bms_status_display"],
        "SOC 电量": main["bms_soc_display"],
        "15 节电芯电压概览": main["bms_cell_voltage_summary"],
    }
    motor_data = extract_motor_rows(motors)  # 电机表。
    return {"lowstate_main": main, "imu_state": imu_table, "bms_state": bms_table, "motor_columns": motor_data["columns"], "motor_rows": motor_data["rows"]}  # 返回完整数据。


def lidar_error_text(value: Any) -> str:
    """LiDAR 错误码解释。"""
    code = as_int(value)  # 转整数。
    if code is None:  # 无效值。
        return "N/A"
    if code == 0:  # 正常。
        return "0 正常"
    mapping = [(0x01, "电机转速异常"), (0x02, "点云数据异常"), (0x04, "串口数据异常")]  # 错误位。
    names = [text for bit, text in mapping if code & bit]  # 解析位图。
    return f"{code} / " + "，".join(names) if names else f"{code} 未知错误码"  # 返回说明。


def extract_lidar_state(msg: Any, cfg: RobotConfig, count: int) -> Dict[str, str]:
    """LiDAR State 消息转 UI 数据。"""
    data = {"update_time": now_text(), "packet_count": str(count), "topic": cfg.lidar_state_topic, "idl_type": "unitree_go/LidarState_"}  # 基础字段。
    fields = ["stamp", "firmware_version", "software_version", "sdk_version", "sys_rotation_speed", "com_rotation_speed", "error_state", "cloud_frequency", "cloud_packet_loss_rate", "cloud_size", "cloud_scan_num", "imu_frequency", "imu_packet_loss_rate", "imu_rpy", "serial_recv_stamp", "serial_buffer_size", "serial_buffer_read"]  # 关注字段。
    for name in fields:  # 遍历字段。
        data[name] = display(safe_get(msg, name, None))  # 填充值。
    data["imu_rpy_deg"] = rpy_deg_text(safe_get(msg, "imu_rpy", None))  # RPY 角度。
    data["error_state_text"] = lidar_error_text(safe_get(msg, "error_state", None))  # 错误说明。
    return data  # 返回表格数据。


def import_lowstate_class(idl_type: str) -> Any:
    """延迟导入 LowState IDL。"""
    if idl_type == "unitree_go":  # go IDL。
        from unitree_sdk2py.idl.unitree_go.msg.dds_ import LowState_  # 真机环境才需要。
        return LowState_  # 返回类。
    if idl_type == "unitree_hg":  # hg IDL。
        from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowState_  # 真机环境才需要。
        return LowState_  # 返回类。
    raise RuntimeError(f"未知 LowState IDL：{idl_type}")  # 配置错误。


def ensure_dds_initialized(iface: str, log=None) -> None:
    """DDS 只初始化一次。"""
    global _DDS_INITIALIZED, _DDS_IFACE  # 修改全局状态。
    from unitree_sdk2py.core.channel import ChannelFactoryInitialize  # 延迟导入 SDK。
    iface = (iface or "").strip()  # 清理网卡名。
    if _DDS_INITIALIZED:  # 已初始化。
        if log and iface and iface != _DDS_IFACE:  # 新网卡不同。
            log(f"DDS 已初始化过，继续使用首次网卡：{_DDS_IFACE}，忽略新的网卡：{iface}")  # 提示。
        return  # 不重复初始化。
    if iface:  # 指定网卡。
        if log: log(f"初始化 Unitree DDS，绑定网卡：{iface}")  # 日志。
        ChannelFactoryInitialize(0, iface)  # 初始化 DDS。
        _DDS_IFACE = iface  # 记录网卡。
    else:  # 未指定网卡。
        if log: log("初始化 Unitree DDS：使用 SDK 默认接口。")  # 日志。
        ChannelFactoryInitialize(0)  # 默认初始化。
    _DDS_INITIALIZED = True  # 标记完成。


class UserStore:
    """演示用户认证。"""
    def __init__(self, path: Path = USERS_FILE) -> None:  # 初始化。
        self.path = path  # 保存路径。
        self.ensure_defaults()  # 确保默认账号存在。

    @staticmethod
    def password_hash(password: str) -> str:  # 密码哈希。
        return hashlib.sha256(("h1-demo-salt:" + password).encode("utf-8")).hexdigest()  # 加盐 SHA256。

    def ensure_defaults(self) -> None:  # 创建默认账号。
        if self.path.exists():  # 文件存在则不覆盖。
            return
        users = {  # 默认账号。
            "admin": {"password_hash": self.password_hash("admin123"), "role": "admin", "display_name": "管理员"},
            "operator": {"password_hash": self.password_hash("operator123"), "role": "operator", "display_name": "操作员"},
        }
        self.path.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")  # 写入文件。

    def authenticate(self, username: str, password: str) -> Optional[Dict[str, str]]:  # 登录校验。
        users = json.loads(self.path.read_text(encoding="utf-8"))  # 读取用户表。
        user = users.get(username)  # 查找用户。
        if not user or user.get("password_hash") != self.password_hash(password):  # 不存在或密码错误。
            return None
        return {"username": username, "role": user.get("role", "operator"), "display_name": user.get("display_name", username)}  # 返回资料。


class LowStateWorker(QThread):
    """LowState 后台线程。"""
    status_signal = pyqtSignal(dict)  # 状态信号。
    log_signal = pyqtSignal(str)  # 日志信号。
    fatal_signal = pyqtSignal(str)  # 致命错误信号。
    ready_signal = pyqtSignal()  # 初始化成功信号。

    def __init__(self, cfg: RobotConfig, parent=None) -> None:  # 初始化。
        super().__init__(parent)  # 初始化线程。
        self.cfg = cfg  # 保存配置。
        self.running = True  # 运行标志。
        self.subscriber = None  # SDK 订阅器。
        self.count = 0  # 包计数。
        self.last_msg = 0.0  # 最近消息时间。
        self.last_emit = 0.0  # 最近 UI 刷新时间。

    def stop(self) -> None:  # 停止线程。
        self.running = False  # 请求停止。
        if self.subscriber:  # 如果订阅器存在。
            for name in ["Close", "close", "Stop", "stop", "Destroy", "destroy"]:  # 兼容关闭函数。
                fn = getattr(self.subscriber, name, None)  # 获取函数。
                if callable(fn):  # 可调用。
                    try: fn(); break  # 尝试关闭。
                    except Exception: pass  # 忽略关闭异常。

    def run(self) -> None:  # 线程入口。
        if self.cfg.protocol == "mock":  # mock 模式。
            self.run_mock(); return
        if self.cfg.protocol == "sdk2":  # SDK2 模式。
            self.run_sdk2(); return
        self.fatal_signal.emit(f"只支持 mock/sdk2，当前 protocol={self.cfg.protocol}")  # 不支持协议。

    def run_mock(self) -> None:  # mock 数据。
        self.log_signal.emit("mock LowState 线程启动。")  # 日志。
        self.ready_signal.emit()  # mock 立即就绪。
        start = time.monotonic()  # 起始时间。
        while self.running:  # 循环生成数据。
            self.count += 1  # 包计数。
            t = time.monotonic() - start  # 运行时间。
            rpy = [0.02 * math.sin(t), 0.04 * math.sin(t * 0.6), 0.12 * math.sin(t * 0.2)]  # 模拟姿态。
            status = self.mock_status(rpy)  # 构造状态。
            self.status_signal.emit(status)  # 发给 UI。
            self.msleep(100)  # 10Hz。
        self.log_signal.emit("mock LowState 线程已停止。")  # 日志。

    def mock_status(self, rpy: List[float]) -> Dict[str, Any]:  # 构造 mock 状态。
        motors = []  # 电机行。
        for i in range(20):  # 20 个电机。
            motors.append({"index": str(i), "mode": "0", "q": fmt(random.uniform(-0.1, 0.1), 6), "dq": fmt(random.uniform(-0.05, 0.05), 6), "ddq": "0.000000", "tau_est": fmt(random.uniform(-0.2, 0.2), 6), "temperature": str(35 + i % 4), "lost": "0", "error_flag": "0", "comm_frequency": "100"})  # 一行电机。
        return {  # 返回完整状态。
            "lowstate_main": {"update_time": now_text(), "packet_count": str(self.count), "topic": "mock/lowstate", "idl_type": "mock", "power_v": "67.200000", "power_a": "1.500000", "battery_voltage_display": "67.20 V", "battery_current_display": "1.50 A", "bms_status_display": "0 SAFE（未开启电池）", "bms_soc_display": "80 %", "motor_state_count": "20"},
            "imu_state": {"quaternion": "[1, 0, 0, 0]", "rpy": vector_text(rpy, 6), "rpy_deg": rpy_deg_text(rpy), "gyroscope": vector_text([random.uniform(-0.02, 0.02) for _ in range(3)], 6), "accelerometer": vector_text([0, 0, 9.81], 6), "temperature": "35 ℃"},
            "bms_state": {"LowState 总电压 power_v": "67.20 V", "LowState 总电流 power_a": "1.50 A", "BMS 状态": "0 SAFE（未开启电池）", "SOC 电量": "80 %", "15 节电芯电压概览": "mock"},
            "motor_columns": ["index", "mode", "q", "dq", "ddq", "tau_est", "temperature", "lost", "error_flag", "comm_frequency"],
            "motor_rows": motors,
        }

    def run_sdk2(self) -> None:  # SDK2 读取。
        try:  # 捕获 SDK 初始化错误。
            from unitree_sdk2py.core.channel import ChannelSubscriber  # 延迟导入订阅器。
            LowState = import_lowstate_class(self.cfg.lowstate_idl)  # 导入 IDL。
            ensure_dds_initialized(self.cfg.network_interface, self.log_signal.emit)  # 初始化 DDS。
            self.subscriber = ChannelSubscriber(self.cfg.lowstate_topic, LowState)  # 创建订阅器。
            self.subscriber.Init(self.on_message, 10)  # 初始化订阅。
            self.last_msg = time.monotonic()  # 初始化时间。
            self.ready_signal.emit()  # 通知就绪。
            warned = False  # 无数据提示标志。
            while self.running:  # 保持线程存活。
                if time.monotonic() - self.last_msg > 3 and not warned:  # 3 秒无数据。
                    warned = True  # 避免重复提示。
                    self.log_signal.emit("超过 3 秒未收到 LowState，请检查网卡、Topic、IDL、防火墙和 DDS。")  # 提示。
                self.msleep(100)  # 降低 CPU。
        except ModuleNotFoundError as exc:  # SDK 未安装。
            self.fatal_signal.emit(f"未找到 unitree_sdk2py，请先安装 Unitree SDK2 Python。原始错误：{exc}")  # 报错。
        except Exception as exc:  # 其他错误。
            self.fatal_signal.emit(f"SDK2 LowState 初始化失败：{exc}")  # 报错。

    def on_message(self, msg: Any) -> None:  # SDK 回调。
        if not self.running:  # 停止后忽略。
            return
        self.count += 1  # 包计数。
        self.last_msg = time.monotonic()  # 更新接收时间。
        if self.last_msg - self.last_emit < 0.1:  # UI 限速。
            return
        self.last_emit = self.last_msg  # 更新时间。
        try:  # 解析消息。
            self.status_signal.emit(extract_low_state(msg, self.cfg, self.count))  # 发状态。
        except Exception as exc:  # 解析失败。
            self.log_signal.emit(f"解析 LowState 失败：{exc}")  # 记日志。


class LidarStateWorker(QThread):
    """LiDAR 后台线程。"""
    status_signal = pyqtSignal(dict)  # 状态信号。
    log_signal = pyqtSignal(str)  # 日志信号。
    error_signal = pyqtSignal(str)  # 错误信号。

    def __init__(self, cfg: RobotConfig, parent=None) -> None:  # 初始化。
        super().__init__(parent)  # 初始化线程。
        self.cfg = cfg  # 保存配置。
        self.running = True  # 运行标志。
        self.subscriber = None  # 订阅器。
        self.count = 0  # 包计数。
        self.last_msg = 0.0  # 最近消息。
        self.last_emit = 0.0  # 最近刷新。

    def stop(self) -> None:  # 停止。
        self.running = False  # 请求停止。
        if self.subscriber:  # 有订阅器。
            for name in ["Close", "close", "Stop", "stop", "Destroy", "destroy"]:  # 兼容关闭方法。
                fn = getattr(self.subscriber, name, None)  # 获取方法。
                if callable(fn):  # 可调用。
                    try: fn(); break  # 关闭。
                    except Exception: pass  # 忽略。

    def run(self) -> None:  # 线程入口。
        if self.cfg.protocol == "mock": self.run_mock(); return  # mock。
        if self.cfg.protocol == "sdk2": self.run_sdk2(); return  # sdk2。
        self.error_signal.emit(f"只支持 mock/sdk2，当前 protocol={self.cfg.protocol}")  # 不支持。

    def run_mock(self) -> None:  # mock LiDAR。
        self.log_signal.emit("mock LiDAR 线程启动。")  # 日志。
        while self.running:  # 循环。
            self.count += 1  # 计数。
            rpy = [random.uniform(-0.02, 0.02), random.uniform(-0.02, 0.02), random.uniform(-0.1, 0.1)]  # 姿态。
            self.status_signal.emit({"update_time": now_text(), "packet_count": str(self.count), "topic": "mock/lidar", "idl_type": "mock", "error_state": "0", "error_state_text": "0 正常", "cloud_frequency": "10.0", "cloud_size": "20000", "imu_rpy": vector_text(rpy, 6), "imu_rpy_deg": rpy_deg_text(rpy)})  # 发送。
            self.msleep(200)  # 5Hz。
        self.log_signal.emit("mock LiDAR 线程已停止。")  # 日志。

    def run_sdk2(self) -> None:  # SDK2 LiDAR。
        try:  # 捕获异常。
            from unitree_sdk2py.core.channel import ChannelSubscriber  # 订阅器。
            from unitree_sdk2py.idl.unitree_go.msg.dds_ import LidarState_  # LiDAR IDL。
            ensure_dds_initialized(self.cfg.network_interface, self.log_signal.emit)  # 初始化 DDS。
            self.subscriber = ChannelSubscriber(self.cfg.lidar_state_topic, LidarState_)  # 创建订阅。
            self.subscriber.Init(self.on_message, 10)  # 初始化订阅。
            self.last_msg = time.monotonic()  # 时间。
            warned = False  # 无数据提示标志。
            while self.running:  # 保持线程。
                if time.monotonic() - self.last_msg > 3 and not warned:  # 3 秒无数据。
                    warned = True  # 避免重复。
                    self.log_signal.emit("超过 3 秒未收到 LiDAR State，请检查 Topic、网卡和 DDS。")  # 日志。
                self.msleep(100)  # 降 CPU。
        except Exception as exc:  # 错误。
            self.error_signal.emit(f"SDK2 LiDAR State 读取失败：{exc}")  # 报错。

    def on_message(self, msg: Any) -> None:  # 回调。
        if not self.running: return  # 停止后忽略。
        self.count += 1  # 计数。
        self.last_msg = time.monotonic()  # 更新时间。
        if self.last_msg - self.last_emit < 0.1: return  # 限速。
        self.last_emit = self.last_msg  # 更新时间。
        self.status_signal.emit(extract_lidar_state(msg, self.cfg, self.count))  # 发状态。


def load_ascii_pcd(path: Path, max_points: int = 80000) -> Tuple[np.ndarray, Optional[np.ndarray], int, int]:
    """读取 ASCII PCD 的 xyz 和 intensity。"""
    if not path.exists():  # 文件不存在。
        raise FileNotFoundError(f"PCD 文件不存在：{path}")
    fields, data_type, skiprows, total = [], "", 0, 0  # 头部信息。
    with path.open("r", encoding="utf-8", errors="ignore") as f:  # 打开文件。
        for i, line in enumerate(f):  # 读头部。
            text = line.strip()  # 清理空白。
            upper = text.upper()  # 转大写。
            if upper.startswith("FIELDS"): fields = text.split()[1:]  # 字段。
            elif upper.startswith("POINTS"): total = as_int(text.split()[1]) or 0  # 点数。
            elif upper.startswith("DATA"): data_type, skiprows = text.split()[1].lower(), i + 1; break  # 数据类型。
    if data_type != "ascii":  # 只支持 ASCII。
        raise RuntimeError(f"当前读取器只支持 ASCII PCD，当前 DATA={data_type}")
    for required in ["x", "y", "z"]:  # 必要字段。
        if required not in fields: raise RuntimeError(f"PCD 缺少字段：{required}")  # 缺字段。
    raw = np.loadtxt(str(path), skiprows=skiprows, dtype=np.float32)  # 读取数据。
    if raw.ndim == 1: raw = raw.reshape(1, -1)  # 单点转二维。
    original = raw.shape[0]  # 原始点数。
    if original > max_points: raw = raw[::max(1, math.ceil(original / max_points))]  # 抽样。
    xyz = raw[:, [fields.index("x"), fields.index("y"), fields.index("z")]]  # 坐标。
    intensity = raw[:, fields.index("intensity")] if "intensity" in fields else None  # 强度。
    return xyz, intensity, total or original, original  # 返回数据。


class PointCloudCanvas(QWidget):
    """PCD 点云显示控件。"""
    def __init__(self, parent=None) -> None:  # 初始化。
        super().__init__(parent)  # 父类初始化。
        self.ax = None  # 坐标轴。
        self.center = None  # 中心点。
        self.zoom = 1.0  # 缩放。
        self.base_range = (1.0, 1.0, 1.0)  # 基础范围。
        self.elev, self.azim = 60, -90  # 视角。
        self.dragging = False  # 拖拽状态。
        self.press = (0, 0, self.elev, self.azim)  # 按下状态。
        layout = QVBoxLayout()  # 布局。
        layout.setContentsMargins(0, 0, 0, 0)  # 无边距。
        self.figure = Figure(figsize=(6, 4))  # Figure。
        self.canvas = FigureCanvas(self.figure)  # 画布。
        layout.addWidget(self.canvas)  # 添加画布。
        self.setLayout(layout)  # 设置布局。
        self.canvas.mpl_connect("button_press_event", self.on_press)  # 鼠标按下。
        self.canvas.mpl_connect("motion_notify_event", self.on_move)  # 鼠标移动。
        self.canvas.mpl_connect("button_release_event", self.on_release)  # 鼠标释放。
        self.show_message("尚未加载点云地图")  # 初始提示。

    def show_message(self, text: str) -> None:  # 显示提示。
        self.figure.clear()  # 清图。
        ax = self.figure.add_subplot(111)  # 普通轴。
        ax.axis("off")  # 隐藏轴。
        ax.text(0.5, 0.5, text, ha="center", va="center", wrap=True)  # 文本。
        self.ax = ax  # 保存轴。
        self.canvas.draw_idle()  # 刷新。

    def plot_pcd(self, path: str) -> str:  # 绘制 PCD。
        xyz, intensity, total, original = load_ascii_pcd(Path(path))  # 读取 PCD。
        x, y, z = xyz[:, 0], xyz[:, 1], xyz[:, 2]  # 拆坐标。
        self.figure.clear()  # 清图。
        self.ax = self.figure.add_subplot(111, projection="3d")  # 3D 轴。
        color, label = (intensity, "intensity") if intensity is not None else (z, "Z")  # 上色字段。
        scatter = self.ax.scatter(x, y, z, c=color, s=1, cmap="viridis", depthshade=False)  # 绘点。
        cbar = self.figure.colorbar(scatter, ax=self.ax, shrink=0.68, pad=0.13, fraction=0.035, aspect=25)  # 色条。
        cbar.set_label(label, labelpad=10)  # 色条标签。
        self.ax.set_title("3D Point Cloud Map")  # 标题。
        self.ax.set_xlabel("X"); self.ax.set_ylabel("Y"); self.ax.set_zlabel("Z")  # 坐标标签。
        mins, maxs = xyz.min(axis=0), xyz.max(axis=0)  # 坐标范围。
        self.center = tuple((mins + maxs) / 2.0)  # 中心点。
        ranges = np.maximum(maxs - mins, 1e-6) * 1.10  # 基础范围。
        ranges[2] = max(ranges[2], max(ranges[0], ranges[1]) * 0.08)  # Z 最小范围。
        self.base_range = tuple(float(v) for v in ranges)  # 保存范围。
        self.zoom, self.elev, self.azim = 0.85, 60, -90  # 重置视角。
        self.apply_view()  # 应用视图。
        self.figure.subplots_adjust(left=0.02, right=0.78, bottom=0.02, top=0.92)  # 布局。
        self.canvas.draw_idle()  # 刷新。
        return f"已加载：{Path(path).name} | 原始点数：{original} | 显示点数：{len(xyz)} | 图例：{label}"  # 日志。

    def apply_view(self) -> None:  # 应用视图。
        if self.ax is None or self.center is None: return  # 无图不处理。
        cx, cy, cz = self.center  # 中心。
        rx, ry, rz = [v * self.zoom for v in self.base_range]  # 当前范围。
        self.ax.set_xlim(cx - rx / 2, cx + rx / 2); self.ax.set_ylim(cy - ry / 2, cy + ry / 2); self.ax.set_zlim(cz - rz / 2, cz + rz / 2)  # 坐标范围。
        self.ax.xaxis.set_major_locator(MaxNLocator(nbins=5)); self.ax.yaxis.set_major_locator(MaxNLocator(nbins=5)); self.ax.zaxis.set_major_locator(MaxNLocator(nbins=4))  # 刻度。
        if hasattr(self.ax, "set_box_aspect"): self.ax.set_box_aspect([rx, ry, rz])  # 坐标盒比例。
        self.ax.view_init(elev=self.elev, azim=self.azim)  # 视角。

    def zoom_in(self) -> None: self.zoom_action(0.8)  # 放大。
    def zoom_out(self) -> None: self.zoom_action(1.25)  # 缩小。

    def zoom_action(self, factor: float) -> None:  # 缩放动作。
        if self.center is None: return  # 无点云。
        self.zoom = max(0.03, min(30.0, self.zoom * factor))  # 限制缩放。
        self.apply_view(); self.canvas.draw_idle()  # 应用并刷新。

    def save_current_view(self) -> str:  # 保存视图。
        path, _ = QFileDialog.getSaveFileName(self, "保存当前点云视角", str(APP_DIR / "pointcloud_view.png"), "PNG Image (*.png);;JPEG Image (*.jpg);;PDF File (*.pdf)")  # 保存对话框。
        if not path: return ""  # 取消。
        self.figure.savefig(path, dpi=200, bbox_inches="tight")  # 保存。
        return path  # 返回路径。

    def on_press(self, event) -> None:  # 鼠标按下。
        if event.button != 1: return  # 只处理左键。
        self.dragging = True; self.press = (event.x, event.y, self.elev, self.azim)  # 记录状态。

    def on_move(self, event) -> None:  # 鼠标移动。
        if not self.dragging or self.center is None: return  # 未拖拽。
        px, py, pe, pa = self.press  # 按下状态。
        self.azim = pa - (event.x - px) * 0.4  # 方位角。
        self.elev = max(-89, min(89, pe - (event.y - py) * 0.4))  # 仰角。
        self.apply_view(); self.canvas.draw_idle()  # 刷新。

    def on_release(self, event) -> None:  # 鼠标释放。
        self.dragging = False  # 停止拖拽。


class H1RobotClient(QObject):
    """连接协调器。"""
    log_signal = pyqtSignal(str)  # 日志。
    state_signal = pyqtSignal(bool)  # 连接状态。
    status_signal = pyqtSignal(dict)  # 状态数据。

    def __init__(self) -> None:  # 初始化。
        super().__init__()  # 父类。
        self.connected = False  # 是否连接。
        self.connecting = False  # 是否连接中。
        self.worker: Optional[LowStateWorker] = None  # LowState 线程。

    def connect_robot(self, cfg: RobotConfig) -> None:  # 连接。
        if self.connected or self.connecting: self.log_signal.emit("机器人已连接或正在连接。"); return  # 防重复。
        self.connecting = True; self.connected = False; self.state_signal.emit(False)  # 状态。
        self.worker = LowStateWorker(cfg)  # 创建线程。
        self.worker.ready_signal.connect(self.on_ready)  # 就绪。
        self.worker.fatal_signal.connect(self.on_fatal)  # 错误。
        self.worker.log_signal.connect(self.log_signal.emit)  # 日志。
        self.worker.status_signal.connect(self.status_signal.emit)  # 状态。
        self.worker.finished.connect(self.on_finished)  # 结束。
        self.worker.start()  # 启动。
        self.log_signal.emit("状态读取线程已启动。")  # 日志。

    def disconnect_robot(self) -> None:  # 断开。
        self.connecting = False  # 清连接中。
        if self.worker:  # 有线程。
            self.worker.stop(); self.worker.wait(3000); self.worker = None  # 停止线程。
        self.connected = False; self.state_signal.emit(False); self.log_signal.emit("已断开连接。")  # 状态。

    def send_command(self, command: str) -> None:  # 预留指令。
        if not self.connected: raise RuntimeError("机器人未连接，无法发送指令。")  # 未连接。
        self.log_signal.emit(f"指令接口为安全预留，不发布 LowCmd：{command}")  # 日志。

    def on_ready(self) -> None:  # 就绪。
        self.connecting = False; self.connected = True; self.state_signal.emit(True); self.log_signal.emit("状态读取初始化成功。")  # 状态。

    def on_fatal(self, text: str) -> None:  # 致命错误。
        self.log_signal.emit(text); self.connecting = False; self.connected = False; self.state_signal.emit(False)  # 状态。
        if self.worker: self.worker.stop()  # 停止。

    def on_finished(self) -> None:  # 线程结束。
        if not self.connected: self.worker = None  # 清引用。


class LoginDialog(QDialog):
    """登录窗口。"""
    def __init__(self) -> None:  # 初始化。
        super().__init__()  # 父类。
        self.user_profile = None  # 登录结果。
        self.store = UserStore()  # 用户仓库。
        self.setWindowTitle("H1 控制台登录")  # 标题。
        self.setMinimumSize(560, 520)  # 尺寸。
        layout = QVBoxLayout(self)  # 根布局。
        layout.setContentsMargins(44, 34, 44, 34)  # 边距。
        title = QLabel("H1 机器人控制面板")  # 标题。
        title.setAlignment(Qt.AlignCenter)  # 居中。
        title.setStyleSheet("font-size:26px;font-weight:900;color:white;")  # 样式。
        self.username_edit = QLineEdit("admin")  # 用户名。
        self.password_edit = QLineEdit("admin123")  # 密码。
        self.password_edit.setEchoMode(QLineEdit.Password)  # 密码模式。
        show_box = QCheckBox("显示密码")  # 显示密码。
        show_box.toggled.connect(lambda checked: self.password_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password))  # 切换显示。
        login_btn = QPushButton("登录")  # 登录按钮。
        login_btn.clicked.connect(self.try_login)  # 登录。
        layout.addWidget(title); layout.addWidget(QLabel("用户名")); layout.addWidget(self.username_edit); layout.addWidget(QLabel("密码")); layout.addWidget(self.password_edit); layout.addWidget(show_box); layout.addWidget(login_btn)  # 添加控件。
        self.setStyleSheet("QDialog{background:#0f172a;color:white;font-family:'Microsoft YaHei';} QLineEdit{min-height:40px;border-radius:10px;padding:0 12px;} QPushButton{min-height:40px;border-radius:10px;background:#2563eb;color:white;font-weight:800;} QLabel{color:white;}")  # 样式。

    def try_login(self) -> None:  # 登录。
        profile = self.store.authenticate(self.username_edit.text().strip(), self.password_edit.text())  # 校验。
        if not profile: QMessageBox.warning(self, "登录失败", "用户名或密码错误。"); return  # 失败。
        self.user_profile = profile; self.accept()  # 成功。


class NavigationDialog(QDialog):
    """SSH 导航窗口。"""
    log_signal = pyqtSignal(str)  # 日志。

    def __init__(self, robot_ip: str, parent=None) -> None:  # 初始化。
        super().__init__(parent)  # 父类。
        self.process: Optional[QProcess] = None  # 当前进程。
        self.setWindowTitle("H1 导航建图窗口")  # 标题。
        self.resize(900, 620)  # 尺寸。
        layout = QVBoxLayout(self)  # 根布局。
        form = QFormLayout()  # 表单。
        self.ip_edit = QLineEdit(robot_ip or "192.168.123.162")  # IP。
        self.user_edit = QLineEdit("unitree")  # 用户。
        self.password_edit = QLineEdit("Unitree0408")  # 密码。
        self.password_edit.setEchoMode(QLineEdit.Password)  # 密码模式。
        self.dir_edit = QLineEdit("ws/unitree_slam/build")  # 目录。
        self.iface_edit = QLineEdit("eth0")  # 网卡。
        for label, widget in [("机器人 IP：", self.ip_edit), ("SSH 用户：", self.user_edit), ("SSH 密码 / sudo 密码：", self.password_edit), ("远程目录：", self.dir_edit), ("导航网卡：", self.iface_edit)]: form.addRow(label, widget)  # 添加表单项。
        btns = QHBoxLayout()  # 按钮行。
        start_btn, stop_btn, ifconfig_btn, clear_btn = QPushButton("启动导航建图"), QPushButton("停止导航程序"), QPushButton("查看远程 ifconfig"), QPushButton("清空输出")  # 按钮。
        start_btn.clicked.connect(self.start_navigation); stop_btn.clicked.connect(self.stop_navigation); ifconfig_btn.clicked.connect(self.run_ifconfig); clear_btn.clicked.connect(lambda: self.output.clear())  # 绑定。
        for b in [start_btn, stop_btn, ifconfig_btn, clear_btn]: btns.addWidget(b)  # 添加按钮。
        self.output = QTextEdit(); self.output.setReadOnly(True)  # 输出框。
        layout.addLayout(form); layout.addLayout(btns); layout.addWidget(self.output)  # 添加布局。
        self.setStyleSheet("QDialog{background:#eef2f7;} QTextEdit{background:#020617;color:#dbeafe;font-family:Consolas;}")  # 样式。

    def append_output(self, text: str) -> None:  # 输出。
        self.output.append(f"[{now_text()}] {text}")  # 带时间戳。

    def ssh_prefix(self) -> str:  # SSH 前缀。
        return "sshpass -p " + shlex.quote(self.password_edit.text()) + " ssh -T -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null " + f"{shlex.quote(self.user_edit.text().strip())}@{shlex.quote(self.ip_edit.text().strip())}"  # 命令。

    def run_command(self, command: str, title: str) -> None:  # 执行命令。
        if self.process and self.process.state() != QProcess.NotRunning: QMessageBox.information(self, "提示", "已有命令正在运行，请先停止。"); return  # 防重复。
        self.process = QProcess(self); self.process.setProcessChannelMode(QProcess.MergedChannels)  # 进程。
        self.process.readyReadStandardOutput.connect(self.on_output); self.process.readyReadStandardError.connect(self.on_output)  # 输出。
        self.process.finished.connect(lambda code, status: self.append_output(f"进程结束，exit_code={code}"))  # 结束。
        self.append_output(f"开始执行：{title}"); self.append_output(command); self.log_signal.emit(title)  # 日志。
        self.process.start("bash", ["-lc", command])  # 启动。

    def start_navigation(self) -> None:  # 启动导航。
        password, remote_dir, iface = self.password_edit.text(), self.dir_edit.text().strip(), self.iface_edit.text().strip()  # 参数。
        remote = f"set -e; cd {shlex.quote(remote_dir)}; export TERM=dumb NO_COLOR=1; export LD_LIBRARY_PATH=$PWD/../unitree_robotics/lib/$(uname -m):$LD_LIBRARY_PATH; printf '%s\\n' {shlex.quote(password)} | sudo -S ./demo_h1 {shlex.quote(iface)}"  # 远程命令。
        command = "command -v sshpass >/dev/null 2>&1 || { echo '缺少 sshpass，请 sudo apt install sshpass'; exit 127; }; " + self.ssh_prefix() + " " + shlex.quote(remote)  # 本机命令。
        self.run_command(command, "启动 demo_h1")  # 执行。

    def run_ifconfig(self) -> None:  # 查看 ifconfig。
        self.run_command(self.ssh_prefix() + " " + shlex.quote("ifconfig"), "查看远程 ifconfig")  # 执行。

    def stop_navigation(self) -> None:  # 停止。
        if self.process and self.process.state() != QProcess.NotRunning: self.process.terminate(); self.process.waitForFinished(1500); self.process.kill()  # 停进程。
        password, iface = self.password_edit.text(), self.iface_edit.text().strip()  # 参数。
        remote = f"printf '%s\\n' {shlex.quote(password)} | sudo -S pkill -f {shlex.quote('demo_h1 ' + iface)}"  # 远程停止。
        QProcess.startDetached("bash", ["-lc", self.ssh_prefix() + " " + shlex.quote(remote)])  # 后台停止。
        self.append_output("已发送停止 demo_h1 的命令。")  # 日志。

    def on_output(self) -> None:  # 输出回调。
        if not self.process: return  # 无进程。
        data = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="ignore") + bytes(self.process.readAllStandardError()).decode("utf-8", errors="ignore")  # 读取。
        data = clean_terminal(data)  # 清洗。
        if data.strip(): self.output.insertPlainText(data + ("\n" if not data.endswith("\n") else ""))  # 显示。


class MainWindow(QMainWindow):
    """主窗口。"""
    def __init__(self, profile: Dict[str, str]) -> None:  # 初始化。
        super().__init__()  # 父类。
        self.profile = profile  # 用户。
        self.client = H1RobotClient()  # 客户端。
        self.lidar_worker: Optional[LidarStateWorker] = None  # LiDAR 线程。
        self.nav_window: Optional[NavigationDialog] = None  # 导航窗口。
        self.client.log_signal.connect(self.append_log); self.client.state_signal.connect(self.set_connected); self.client.status_signal.connect(self.update_status)  # 信号。
        self.setWindowTitle("H1 机器人控制台")  # 标题。
        self.build_ui(); self.load_config(); self.resize(1100, 760)  # UI、配置、尺寸。
        self.append_log(f"用户 {profile['display_name']} 已登录，角色：{profile['role']}")  # 登录日志。

    def build_ui(self) -> None:  # 构建 UI。
        root = QWidget(); root_layout = QVBoxLayout(root)  # 中央控件。
        self.tabs = QTabWidget()  # 标签页。
        self.tabs.addTab(self.scroll(self.connection_tab()), "连接")  # 连接页。
        self.tabs.addTab(self.status_tab(), "实时状态")  # 状态页。
        self.tabs.addTab(self.scroll(self.navigation_tab()), "建图导航")  # 导航页。
        self.tabs.addTab(self.lidar_tab(), "激光雷达 / 点云地图")  # LiDAR 页。
        self.log_edit = QTextEdit(); self.log_edit.setReadOnly(True); self.log_edit.setMaximumHeight(150)  # 日志框。
        root_layout.addWidget(self.tabs); root_layout.addWidget(self.log_edit)  # 添加。
        self.setCentralWidget(root)  # 设置中央。
        self.status_bar = QStatusBar(); self.setStatusBar(self.status_bar)  # 状态栏。
        self.conn_label = QLabel("未连接"); self.status_bar.addPermanentWidget(self.conn_label)  # 连接状态。
        self.set_connected(False)  # 初始状态。
        self.setStyleSheet("QMainWindow{background:#eef2f7;} QGroupBox{font-weight:800;border:1px solid #cbd5e1;border-radius:10px;margin-top:10px;padding:10px;background:white;} QPushButton{min-height:30px;border-radius:8px;padding:5px 12px;} QTextEdit{background:#020617;color:#dbeafe;}")  # 样式。

    def scroll(self, widget: QWidget) -> QScrollArea:  # 包装滚动。
        area = QScrollArea(); area.setWidget(widget); area.setWidgetResizable(True); area.setFrameShape(QScrollArea.NoFrame); return area  # 返回滚动区。

    def connection_tab(self) -> QWidget:  # 连接页。
        page = QWidget(); layout = QGridLayout(page)  # 页面。
        group = QGroupBox("H1 连接配置"); form = QFormLayout(group)  # 配置组。
        self.ip_edit = QLineEdit("192.168.123.162"); self.port_spin = QSpinBox(); self.port_spin.setRange(1, 65535); self.port_spin.setValue(8080)  # IP 端口。
        self.iface_edit = QLineEdit("enx9c69d3565ef9"); self.protocol_combo = QComboBox(); self.protocol_combo.addItem("模拟模式", "mock"); self.protocol_combo.addItem("Unitree SDK2", "sdk2")  # 网卡协议。
        self.protocol_combo.setCurrentIndex(1); self.lowstate_topic_combo = QComboBox(); self.lowstate_topic_combo.addItems(["rt/lowstate", "rt/lowState"]); self.lowstate_idl_combo = QComboBox(); self.lowstate_idl_combo.addItems(["unitree_go", "unitree_hg"])  # Topic/IDL。
        for label, widget in [("机器人 IP：", self.ip_edit), ("端口：", self.port_spin), ("网卡/接口名：", self.iface_edit), ("通信方式：", self.protocol_combo), ("低频状态 Topic：", self.lowstate_topic_combo), ("低频状态 IDL：", self.lowstate_idl_combo)]: form.addRow(label, widget)  # 添加表单。
        btns = QHBoxLayout(); connect_btn, disconnect_btn, save_btn = QPushButton("连接并读取状态"), QPushButton("断开/停止读取"), QPushButton("保存配置")  # 按钮。
        connect_btn.clicked.connect(self.connect_robot); disconnect_btn.clicked.connect(self.disconnect_robot); save_btn.clicked.connect(self.save_config)  # 绑定。
        for b in [connect_btn, disconnect_btn, save_btn]: btns.addWidget(b)  # 添加按钮。
        form.addRow(btns)  # 添加按钮行。
        cmd = QGroupBox("常用指令接口预留"); cmd_layout = QGridLayout(cmd)  # 指令组。
        for text, command, r, c in [("急停", "emergency_stop", 0, 0), ("站立", "stand_up", 1, 0), ("坐下", "sit_down", 1, 1), ("使能电机", "enable_motors", 2, 0), ("失能电机", "disable_motors", 2, 1)]:  # 指令。
            btn = QPushButton(text); btn.clicked.connect(lambda checked=False, x=command: self.send_command(x)); cmd_layout.addWidget(btn, r, c)  # 添加按钮。
        layout.addWidget(group, 0, 0); layout.addWidget(cmd, 0, 1); layout.setColumnStretch(0, 3); layout.setColumnStretch(1, 2)  # 布局。
        return page  # 返回。

    def status_tab(self) -> QWidget:  # 状态页。
        page = QWidget(); layout = QVBoxLayout(page); tabs = QTabWidget()  # 页面。
        self.lowstate_table = self.kv_table(); self.imu_table = self.kv_table(); self.bms_table = self.kv_table(); self.motor_table = QTableWidget()  # 表格。
        self.motor_table.verticalHeader().setVisible(False); self.motor_table.setEditTriggers(QTableWidget.NoEditTriggers)  # 电机表设置。
        for widget, title in [(self.lowstate_table, "低频状态主字段"), (self.imu_table, "惯性测量单元"), (self.bms_table, "电池管理系统"), (self.motor_table, "电机状态")]: tabs.addTab(widget, title)  # 添加页。
        layout.addWidget(tabs); return page  # 返回。

    def navigation_tab(self) -> QWidget:  # 导航页。
        page = QWidget(); layout = QVBoxLayout(page); btn = QPushButton("打开导航建图窗口")  # 页面按钮。
        btn.clicked.connect(self.open_navigation); layout.addWidget(QLabel("点击按钮后会打开独立窗口，通过 SSH 远程启动 demo_h1。")); layout.addWidget(btn); layout.addStretch()  # 布局。
        return page  # 返回。

    def lidar_tab(self) -> QWidget:  # LiDAR 页。
        page = QWidget(); layout = QHBoxLayout(page)  # 页面。
        left = QGroupBox("激光雷达状态"); left_layout = QVBoxLayout(left)  # 左侧。
        self.lidar_topic_edit = QLineEdit("rt/utlidar/lidar_state"); start_btn, stop_btn = QPushButton("开始读取 LiDAR"), QPushButton("停止读取")  # 控件。
        start_btn.clicked.connect(self.start_lidar); stop_btn.clicked.connect(self.stop_lidar)  # 绑定。
        self.lidar_table = self.kv_table()  # 表格。
        left_layout.addWidget(QLabel("状态 Topic：")); left_layout.addWidget(self.lidar_topic_edit); left_layout.addWidget(start_btn); left_layout.addWidget(stop_btn); left_layout.addWidget(self.lidar_table)  # 添加。
        right = QGroupBox("PCD 点云地图"); right_layout = QVBoxLayout(right)  # 右侧。
        row = QHBoxLayout(); self.pcd_edit = QLineEdit(str(APP_DIR / "GlobalMap.pcd"))  # 路径。
        for text, slot in [("选择", self.browse_pcd), ("加载", self.load_pcd), ("+", lambda: self.pointcloud.zoom_in()), ("-", lambda: self.pointcloud.zoom_out()), ("保存", self.save_pcd)]:  # 按钮定义。
            btn = QPushButton(text); btn.clicked.connect(slot); row.addWidget(btn)  # 添加按钮。
        row.insertWidget(0, self.pcd_edit, stretch=1)  # 插入路径。
        self.pointcloud = PointCloudCanvas()  # 点云控件。
        right_layout.addLayout(row); right_layout.addWidget(self.pointcloud)  # 添加。
        layout.addWidget(left, 1); layout.addWidget(right, 2)  # 添加左右。
        QTimer.singleShot(300, self.load_pcd_if_exists)  # 自动加载。
        return page  # 返回。

    def kv_table(self) -> QTableWidget:  # 键值表。
        table = QTableWidget(); table.setColumnCount(2); table.setHorizontalHeaderLabels(["字段", "值"]); table.verticalHeader().setVisible(False); table.setEditTriggers(QTableWidget.NoEditTriggers)  # 初始化。
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents); table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)  # 列宽。
        return table  # 返回。

    def fill_kv(self, table: QTableWidget, data: Dict[str, Any]) -> None:  # 填充键值表。
        table.setRowCount(len(data))  # 行数。
        for r, (k, v) in enumerate(data.items()):  # 遍历。
            table.setItem(r, 0, QTableWidgetItem(tr(k))); table.setItem(r, 1, QTableWidgetItem(str(v)))  # 设置单元格。

    def fill_motor(self, columns: List[str], rows: List[Dict[str, Any]]) -> None:  # 填充电机表。
        self.motor_table.setColumnCount(len(columns)); self.motor_table.setHorizontalHeaderLabels([tr(c) for c in columns]); self.motor_table.setRowCount(len(rows))  # 表头行数。
        for r, row in enumerate(rows):  # 行。
            for c, name in enumerate(columns): self.motor_table.setItem(r, c, QTableWidgetItem(str(row.get(name, ""))))  # 单元格。
        self.motor_table.resizeColumnsToContents()  # 自适应。

    def get_config(self) -> RobotConfig:  # 从 UI 获取配置。
        return RobotConfig(robot_ip=self.ip_edit.text().strip(), port=int(self.port_spin.value()), network_interface=self.iface_edit.text().strip(), protocol=self.protocol_combo.currentData(), lowstate_topic=self.lowstate_topic_combo.currentText().strip(), lowstate_idl=self.lowstate_idl_combo.currentText().strip(), lidar_state_topic=self.lidar_topic_edit.text().strip() if hasattr(self, "lidar_topic_edit") else "rt/utlidar/lidar_state", pointcloud_file=self.pcd_edit.text().strip() if hasattr(self, "pcd_edit") else str(APP_DIR / "GlobalMap.pcd"))  # 返回配置。

    def save_config(self) -> None:  # 保存配置。
        CONFIG_FILE.write_text(json.dumps(asdict(self.get_config()), ensure_ascii=False, indent=2), encoding="utf-8"); self.append_log("配置已保存。")  # 写文件。

    def load_config(self) -> None:  # 加载配置。
        if not CONFIG_FILE.exists(): return  # 无文件。
        try:  # 捕获错误。
            cfg = RobotConfig(**{**asdict(RobotConfig()), **json.loads(CONFIG_FILE.read_text(encoding="utf-8"))})  # 合并默认值。
            self.ip_edit.setText(cfg.robot_ip); self.port_spin.setValue(cfg.port); self.iface_edit.setText(cfg.network_interface); self.lowstate_topic_combo.setCurrentText(cfg.lowstate_topic); self.lowstate_idl_combo.setCurrentText(cfg.lowstate_idl); self.lidar_topic_edit.setText(cfg.lidar_state_topic); self.pcd_edit.setText(cfg.pointcloud_file)  # 回填。
            for i in range(self.protocol_combo.count()):  # 找协议。
                if self.protocol_combo.itemData(i) == cfg.protocol: self.protocol_combo.setCurrentIndex(i)  # 设置。
            self.append_log("已加载本地配置。")  # 日志。
        except Exception as exc: self.append_log(f"加载配置失败：{exc}")  # 错误。

    def connect_robot(self) -> None: self.client.connect_robot(self.get_config())  # 连接。
    def disconnect_robot(self) -> None: self.client.disconnect_robot()  # 断开。

    def set_connected(self, connected: bool) -> None:  # 更新连接状态。
        self.conn_label.setText("已连接" if connected else "未连接"); self.conn_label.setStyleSheet("color:green;font-weight:800;" if connected else "color:red;font-weight:800;")  # 样式。

    def update_status(self, status: Dict[str, Any]) -> None:  # 刷新状态。
        self.fill_kv(self.lowstate_table, status.get("lowstate_main", {})); self.fill_kv(self.imu_table, status.get("imu_state", {})); self.fill_kv(self.bms_table, status.get("bms_state", {})); self.fill_motor(status.get("motor_columns", []), status.get("motor_rows", []))  # 填表。

    def open_navigation(self) -> None:  # 打开导航窗口。
        if self.nav_window is None: self.nav_window = NavigationDialog(self.ip_edit.text().strip(), self); self.nav_window.log_signal.connect(self.append_log)  # 创建。
        self.nav_window.show(); self.nav_window.raise_(); self.nav_window.activateWindow()  # 显示。

    def start_lidar(self) -> None:  # 启动 LiDAR。
        if self.lidar_worker and self.lidar_worker.isRunning(): QMessageBox.information(self, "提示", "LiDAR 已在运行。"); return  # 防重复。
        cfg = self.get_config(); self.lidar_worker = LidarStateWorker(cfg); self.lidar_worker.status_signal.connect(lambda data: self.fill_kv(self.lidar_table, data)); self.lidar_worker.log_signal.connect(self.append_log); self.lidar_worker.error_signal.connect(lambda text: (self.append_log(text), QMessageBox.warning(self, "LiDAR 错误", text)))  # 创建线程。
        self.lidar_worker.start(); self.append_log(f"正在读取 LiDAR State：{cfg.lidar_state_topic}")  # 启动。

    def stop_lidar(self) -> None:  # 停止 LiDAR。
        if self.lidar_worker: self.lidar_worker.stop(); self.lidar_worker.wait(3000); self.lidar_worker = None  # 停止。
        self.append_log("已停止 LiDAR 状态读取。")  # 日志。

    def browse_pcd(self) -> None:  # 选择 PCD。
        path, _ = QFileDialog.getOpenFileName(self, "选择 PCD 点云文件", str(APP_DIR), "Point Cloud (*.pcd);;All Files (*)")  # 对话框。
        if path: self.pcd_edit.setText(path); self.load_pcd()  # 加载。

    def load_pcd_if_exists(self) -> None:  # 自动加载。
        if Path(self.pcd_edit.text().strip()).exists(): self.load_pcd()  # 存在就加载。

    def load_pcd(self) -> None:  # 加载点云。
        try: self.append_log(self.pointcloud.plot_pcd(self.pcd_edit.text().strip()))  # 绘制。
        except Exception as exc: self.pointcloud.show_message(str(exc)); self.append_log(f"加载 PCD 失败：{exc}")  # 错误。

    def save_pcd(self) -> None:  # 保存点云视图。
        path = self.pointcloud.save_current_view()  # 保存。
        if path: self.append_log(f"当前点云视角已保存：{path}")  # 日志。

    def send_command(self, command: str) -> None:  # 发送指令。
        if command == "emergency_stop" and QMessageBox.question(self, "确认急停", "确认发送急停指令？当前代码不会发布 LowCmd。", QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes: return  # 急停确认。
        try: self.client.send_command(command)  # 发送。
        except Exception as exc: QMessageBox.warning(self, "指令失败", str(exc)); self.append_log(f"发送指令失败：{exc}")  # 错误。

    def append_log(self, text: str) -> None:  # 追加日志。
        self.log_edit.append(f"[{now_text()}] {text}")  # 时间戳。

    def closeEvent(self, event) -> None:  # 关闭窗口。
        if self.nav_window: self.nav_window.close()  # 关导航。
        self.stop_lidar(); self.client.disconnect_robot(); super().closeEvent(event)  # 停线程。


def main() -> int:
    """程序入口。"""
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)  # 高 DPI。
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)  # 高清图片。
    setup_matplotlib_font()  # 字体。
    app = QApplication(sys.argv)  # 应用。
    app.setApplicationName("H1 机器人控制面板")  # 应用名。
    app.setFont(QFont("Noto Sans CJK SC", 10))  # 默认字体。
    login = LoginDialog()  # 登录框。
    if login.exec_() != QDialog.Accepted or not login.user_profile: return 0  # 取消。
    window = MainWindow(login.user_profile)  # 主窗口。
    window.show()  # 显示。
    return app.exec_()  # 事件循环。


if __name__ == "__main__":
    sys.exit(main())  # 启动程序。
