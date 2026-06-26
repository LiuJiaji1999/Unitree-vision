#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unitree H1 Vision PyInstaller packager.

用法：
  1) 先进入 Unitree-vision 项目根目录，也就是 H1-vision.py 所在目录。
  2) Linux:
       conda activate vision
       python package_unitree_vision.py --platform linux
  3) Windows:
       conda activate vision
       python package_unitree_vision.py --platform windows

说明：
  - PyInstaller 不是交叉编译器：Linux 上生成 Linux 可执行文件；Windows 上生成 Windows exe。
  - 默认使用 onedir 文件夹版，适合带 h1_config.json、users.json、GlobalMap.pcd、日志等可读写资源的 GUI 程序。
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path


APP_NAME = "H1Vision"
ENTRY_SOURCE = "H1-vision.py"
PATCHED_ENTRY_DIR = Path("build_packaging_entry")
PATCHED_ENTRY = PATCHED_ENTRY_DIR / "H1Vision_entry.py"
RUNTIME_HOOK = PATCHED_ENTRY_DIR / "rthook_h1vision.py"

DATA_FILES = [
    "h1_config.json",
    "users.json",
    "GlobalMap.pcd",
]

EXCLUDE_QT_BINDINGS = [
    "PyQt6",
    "PySide2",
    "PySide6",
]


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    print("\n$ " + " ".join(map(str, cmd)))
    return subprocess.run(cmd, check=check)


def ensure_project_root() -> Path:
    root = Path.cwd()
    if not (root / ENTRY_SOURCE).exists():
        raise SystemExit(
            f"当前目录不是项目根目录：未找到 {ENTRY_SOURCE}\n"
            f"请先 cd 到 Unitree-vision 项目根目录后再运行本脚本。"
        )
    return root


def pip_install(args: list[str]) -> None:
    run([sys.executable, "-m", "pip", "install", *args])


def module_exists(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def prepare_environment(skip_install: bool = False) -> None:
    if skip_install:
        return

    if not module_exists("PyInstaller"):
        pip_install(["-U", "pyinstaller", "pyinstaller-hooks-contrib"])

    # 项目自身包含 setup.py，可把 unitree_sdk2py 以 editable 方式安装进当前 conda 环境。
    # 如果已经安装过，重复执行通常也没关系。
    if Path("setup.py").exists():
        pip_install(["-e", "."])

    # 这些是 H1-vision.py 明确使用/README 提到的核心依赖。
    # 在离线环境中可先准备 wheelhouse，然后用 --skip-install 跳过。
    required = [
        ("PyQt5", "PyQt5"),
        ("cv2", "opencv-python-headless"),
        ("numpy", "numpy"),
        ("matplotlib", "matplotlib"),
        ("cyclonedds", "cyclonedds==0.10.2"),
    ]
    missing = [pip_name for import_name, pip_name in required if not module_exists(import_name)]
    if missing:
        pip_install(missing)



def create_runtime_hook() -> None:
    PATCHED_ENTRY_DIR.mkdir(parents=True, exist_ok=True)
    RUNTIME_HOOK.write_text(
        """# -*- coding: utf-8 -*-
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
""",
        encoding="utf-8",
    )


def create_patched_entry() -> Path:
    """
    把 H1-vision.py 复制成打包专用入口，并修正 APP_DIR。
    原始代码使用 Path(__file__).resolve().parent；冻结后资源目录可能不稳定。
    改为冻结状态下使用 sys.executable 所在目录，便于部署后编辑 h1_config.json/users.json。
    """
    PATCHED_ENTRY_DIR.mkdir(parents=True, exist_ok=True)
    source = Path(ENTRY_SOURCE).read_text(encoding="utf-8")

    old = "APP_DIR = Path(__file__).resolve().parent"
    new = (
        'if getattr(sys, "frozen", False):\n'
        "    APP_DIR = Path(sys.executable).resolve().parent\n"
        "else:\n"
        "    APP_DIR = Path(__file__).resolve().parent"
    )

    if old in source:
        source = source.replace(old, new, 1)
    else:
        print(
            f"警告：未找到 `{old}`，将不自动修正 APP_DIR。"
            "如运行后找不到 h1_config.json/users.json，请手动修改源码里的 APP_DIR。"
        )

    PATCHED_ENTRY.write_text(source, encoding="utf-8")
    return PATCHED_ENTRY


def add_data_arg(src: str, dest: str = ".") -> str:
    # PyInstaller CLI 在 Windows 用 ;，Linux/macOS 用 :；os.pathsep 正好匹配。
    return f"{src}{os.pathsep}{dest}"


def build_pyinstaller(debug_console: bool = False) -> None:
    create_runtime_hook()
    entry = create_patched_entry()

    os.environ.setdefault("QT_API", "pyqt5")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name", APP_NAME,
        "--contents-directory", ".",
        "--runtime-hook", str(RUNTIME_HOOK),
        "--paths", str(Path.cwd()),
        "--collect-all", "unitree_sdk2py",
        "--collect-all", "cyclonedds",
        "--collect-all", "PyQt5",
        "--collect-all", "matplotlib",
        "--collect-all", "cv2",
        "--hidden-import", "unitree_sdk2py.core.channel",
        "--hidden-import", "unitree_sdk2py.idl.unitree_go.msg.dds_",
        "--hidden-import", "unitree_sdk2py.idl.unitree_hg.msg.dds_",
        "--hidden-import", "matplotlib.backends.backend_qt5agg",
    ]

    for mod in EXCLUDE_QT_BINDINGS:
        cmd += ["--exclude-module", mod]

    # GUI 程序默认不弹命令行窗口；调试时用 --debug-console 保留控制台。
    if not debug_console:
        cmd.append("--windowed")

    for file_name in DATA_FILES:
        p = Path(file_name)
        if p.exists():
            cmd += ["--add-data", add_data_arg(str(p), ".")]
        else:
            print(f"提示：未找到 {file_name}，跳过打包该资源。")

    cmd.append(str(entry))
    run(cmd)


def copy_runtime_editable_files() -> Path:
    dist_dir = Path("dist") / APP_NAME
    if not dist_dir.exists():
        raise SystemExit(f"打包失败：未找到 {dist_dir}")
    
 # 关键修复：
    # 删除 OpenCV/cv2 自带或残留的 Qt 插件目录。
    # 否则 Qt 可能优先加载 cv2/qt/plugins/platforms/libqxcb.so，
    # 导致 “Could not load the Qt platform plugin xcb”。
    bad_cv2_qt = dist_dir / "cv2" / "qt"
    if bad_cv2_qt.exists():
        shutil.rmtree(bad_cv2_qt)
        print(f"已删除冲突目录：{bad_cv2_qt}")

    # 把配置/账号/地图文件再复制一份到 exe 同级，确保 APP_DIR 指向它们。
    for file_name in DATA_FILES:
        src = Path(file_name)
        if src.exists():
            shutil.copy2(src, dist_dir / src.name)

    readme = dist_dir / "README_RUN.txt"
    readme.write_text(
        f"""H1Vision 运行说明


Linux:
  chmod +x ./{APP_NAME}
  ./{APP_NAME}

Windows:
  双击 {APP_NAME}.exe

部署注意：
  1. 不要只拷贝单个 exe；请拷贝整个 {APP_NAME} 文件夹。
  2. h1_config.json、users.json、GlobalMap.pcd 应与可执行文件放在同一目录。
  3. 连接真机前，确认目标电脑 IP/网卡名与 h1_config.json 中配置一致。
  4. Linux 如遇 Qt xcb 报错，安装常见图形库：
     sudo apt install -y libxcb-cursor0 libxcb-xinerama0 libxkbcommon-x11-0 libgl1 libegl1
""",
        encoding="utf-8",
    )
    return dist_dir


def archive_dist(dist_dir: Path) -> Path:
    system = platform.system().lower()
    if system == "windows":
        archive = Path("dist") / f"{APP_NAME}-windows-x64.zip"
        if archive.exists():
            archive.unlink()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for p in dist_dir.rglob("*"):
                zf.write(p, p.relative_to(dist_dir.parent))
        print(f"\n已生成 Windows 部署包：{archive}")
        return archive

    archive = Path("dist") / f"{APP_NAME}-linux-x64.tar.gz"
    if archive.exists():
        archive.unlink()
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(dist_dir, arcname=dist_dir.name)
    print(f"\n已生成 Linux 部署包：{archive}")
    return archive


def make_deb(dist_dir: Path) -> Path:
    if platform.system().lower() != "linux":
        raise SystemExit("--deb 只能在 Linux 上执行")

    version = "1.0.0"
    pkg_root = Path("dist") / "deb_root"
    if pkg_root.exists():
        shutil.rmtree(pkg_root)

    app_opt = pkg_root / "opt" / "h1vision"
    app_opt.mkdir(parents=True, exist_ok=True)
    shutil.copytree(dist_dir, app_opt, dirs_exist_ok=True)

    desktop_dir = pkg_root / "usr" / "share" / "applications"
    desktop_dir.mkdir(parents=True, exist_ok=True)
    (desktop_dir / "h1vision.desktop").write_text(
        """[Desktop Entry]
Type=Application
Name=H1Vision
Comment=Unitree H1 robot vision client
Exec=/opt/h1vision/H1Vision
Terminal=false
Categories=Utility;Robotics;
""",
        encoding="utf-8",
    )

    control_dir = pkg_root / "DEBIAN"
    control_dir.mkdir(parents=True, exist_ok=True)
    (control_dir / "control").write_text(
        f"""Package: h1vision
Version: {version}
Section: utils
Priority: optional
Architecture: amd64
Maintainer: Unitree Vision User <user@example.com>
Depends: libxcb-cursor0, libxcb-xinerama0, libxkbcommon-x11-0, libgl1, libegl1, openssh-client
Description: Unitree H1 robot vision PyQt client
 A packaged PyQt5 visualization client for Unitree H1 robot vision monitoring.
""",
        encoding="utf-8",
    )

    deb_path = Path("dist") / f"h1vision_{version}_amd64.deb"
    if deb_path.exists():
        deb_path.unlink()
    run(["dpkg-deb", "--build", str(pkg_root), str(deb_path)])
    print(f"\n已生成 Ubuntu/Debian 安装包：{deb_path}")
    return deb_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build H1Vision executable with PyInstaller.")
    parser.add_argument("--platform", choices=["linux", "windows"], default=None,
                        help="仅用于提示；实际产物由当前操作系统决定。")
    parser.add_argument("--skip-install", action="store_true",
                        help="跳过 pip install，适合离线/已配好环境。")
    parser.add_argument("--debug-console", action="store_true",
                        help="保留控制台窗口，便于查看报错。")
    parser.add_argument("--deb", action="store_true",
                        help="Linux 下额外生成 .deb 包。")
    args = parser.parse_args()

    root = ensure_project_root()
    current = platform.system().lower()
    if args.platform:
        wanted = "windows" if args.platform == "windows" else "linux"
        if wanted == "windows" and current != "windows":
            print("警告：当前不是 Windows。PyInstaller 不能在 Linux 上直接生成 Windows exe。")
        if wanted == "linux" and current != "linux":
            print("警告：当前不是 Linux。PyInstaller 不能在 Windows 上直接生成 Linux 可执行文件。")

    print(f"项目根目录：{root}")
    print(f"当前 Python：{sys.executable}")
    print(f"当前系统：{platform.platform()}")

    prepare_environment(skip_install=args.skip_install)
    build_pyinstaller(debug_console=args.debug_console)
    dist_dir = copy_runtime_editable_files()
    archive_dist(dist_dir)

    if args.deb:
        make_deb(dist_dir)

    print("\n完成。部署时请分发 dist 目录下的压缩包，或整个 dist/H1Vision 文件夹。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
