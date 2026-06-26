#!/usr/bin/env bash
set -euo pipefail

# 在 Unitree-vision 项目根目录执行：
#   chmod +x build_linux.sh
#   ./build_linux.sh
#
# 默认使用 conda 环境 vision；如你的环境名不同：
#   CONDA_ENV=myenv ./build_linux.sh

CONDA_ENV="${CONDA_ENV:-vision}"

if command -v conda >/dev/null 2>&1; then
  # shellcheck disable=SC1091
  source "$(conda info --base)/etc/profile.d/conda.sh"
  conda activate "$CONDA_ENV"
else
  echo "未找到 conda，将使用当前 Python 环境。"
fi

python package_unitree_vision.py --platform linux "$@"

echo
echo "Linux 产物："
echo "  dist/H1Vision/H1Vision"
echo "  dist/H1Vision-linux-x64.tar.gz"
echo
echo "运行测试："
echo "  cd dist/H1Vision && ./H1Vision"
