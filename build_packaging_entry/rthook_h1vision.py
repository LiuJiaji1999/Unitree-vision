# -*- coding: utf-8 -*-
import os
import sys
import tempfile

os.environ.setdefault("QT_API", "pyqt5")
os.environ.setdefault("OPENCV_LOG_LEVEL", "SILENT")
os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")

# Matplotlib 在只读安装目录下可能无法写缓存，统一放到用户临时目录。
os.environ.setdefault(
    "MPLCONFIGDIR",
    os.path.join(tempfile.gettempdir(), "h1vision_matplotlib")
)

# 关键修复：
# 强制 Qt 使用 PyQt5 自己的插件目录，
# 避免误加载 cv2/qt/plugins/platforms/libqxcb.so 导致 xcb 崩溃。
if getattr(sys, "frozen", False):
    app_dir = os.path.dirname(sys.executable)
else:
    app_dir = os.getcwd()

pyqt_plugins = os.path.join(app_dir, "PyQt5", "Qt5", "plugins")
pyqt_platforms = os.path.join(pyqt_plugins, "platforms")

if os.path.isdir(pyqt_plugins):
    os.environ["QT_PLUGIN_PATH"] = pyqt_plugins

if os.path.isdir(pyqt_platforms):
    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = pyqt_platforms

os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
