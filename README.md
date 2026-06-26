### 客户端封装
```shell
# 将其封装成deb/exe，无论linux系统/windows系统都可安装运行！
# 开始一直提示‘已中止（核心以转存）’改完package_unitree_vision.py后的重新打包流程 

1.在项目根目录执行：
    cd ~/Desktop/unitree_sdk2_python-master
    conda activate vision
    rm -rf build dist build_packaging_entry H1Vision.spec
    ./build_linux.sh --deb
2.然后测试：
    conda deactivate
    cd ~/Desktop/unitree_sdk2_python-master/dist/H1Vision
    H1_SHOW_NATIVE_CONSOLE=1 ./H1Vision

##### 
1. 安装客户端，部署到其他 Ubuntu 电脑
    dist/h1vision_1.0.0_amd64.deb # 把这个文件拷贝到目标电脑：
    cd ~/Desktop/unitree_sdk2_python-master
    sudo apt install ./dist/h1vision_1.0.0_amd64.deb
    sudo chown -R $USER:$USER /opt/h1vision
    /opt/h1vision/H1Vision 
    # 安装 .deb 后，桌面菜单里应该会出现：
    H1Vision #双击即可
2. 卸载客户端
    sudo apt remove h1vision
    sudo rm -rf /opt/h1vision
```

## 快速开始[https://support.unitree.com/home/zh/H1_developer/start]
```shell
系统环境:
    Ubuntu 20.04 系统下进行开发, 暂不支持在 Mac、Windows 系统下进行开发。
    PC 1 运行官方服务，不支持开发；PC 2 可以访问开发。
上位机：有线连接
    有线-IPv4-手动-
        地址：192.168.123.51
        子网掩码：255.255.255.0
        网关：空
    H1 网段：192.178.123.162
```

### 导航功能
```shell
ssh unitree@192.168.123.162 # 密码：Unitree0408
cd ws/unitree_slam/build
export LD_LIBRARY_PATH=$PWD/../unitree_robotics/lib/$(uname -m):$LD_LIBRARY_PATH
ifconfig #192.168.123.162 所在的网段名称，eth0
sudo ./demo_h1 eth0 #Unitree0408
进入导航页面中，开始建图导航；
```


### 前端开发
```shell
1. 通信方式：sdk2
2. 网卡/接口名：enx9c69d3565ef9 # ip addr
3. 运行命令

    # conda create -n vision python=3.9 
    # pip install cyclonedds==0.10.2, numpy, opencv-python, PyQt5
    conda activate vision
    git clone https://github.com/unitreerobotics/unitree_sdk2_python.git
    cd unitree_sdk2_python
    python -m pip install -e .
    cd /home/epai/Desktop/unitree_sdk2_python-master #当时没有网络。因此是解压的下载的压缩包，所以后缀有-master
    python h1-vision.py

    # python ./example/front_camera/camera_opencv.py enx9c69d3565ef9

------

4. 可调用接口说明[https://support.unitree.com/home/zh/H1_developer/Basic_Services_Interface]
# 底层服务接口
底层通信主要是获取电机，电池，遥控器，
    IMU数据并发布 rt/lowstate，
    订阅控制命令 rt/lowcmd 并控制电机、电池。
命名空间说明: H1的底层服务接口使用unitree_go命名空间。

### 底层数据接收
用户可通过发布 DDS 话题 “rt/lowState” 来获取电机、电池、IMU、遥控器数据，数据格式如 LowState_.idl 所示。
unitree_sdk2_python/unitree_sdk2py/idl/unitree_go/msg/dds_/_LowState_.py：
    _IMUState_.py:
        包含了三轴的加速度和角速度信息，四元数信息，欧拉角信息，温度信息:
    _MotorState_.py
        电机状态数据，共 19 个电机.电机反馈的实时信息，用于运动控制。
    _BmsState_.py
        包含了电池版本、状态信息、电池电量信息、充放电、循环次数、温度、单节电池电压。
    其他的已注释

### 底层控制指令
用户可通过订阅 DDS 话题 “rt/lowcmd” 来发送电机、电池、自动充电、电机电源开关的控制指令，数据格式如 LowCmd_.idl 所示。
    _MotorCmd_.py
        电机控制命令的实时信息，用于运动控制。H1 共有两类电机，髋关节、膝关节、腰部关节电机的 mode 需要设置为 0x0A，上肢、脚踝关节的 mode 需要设置为 0x01 。
    _BmsCmd_.py
        关闭机身电池指令。

上半身挥手的控制
```

###  RealSense D435I 启动与实时查看 RGB-D 指南
```bash
## 1. 硬件检查（每次使用前）

# 1) USB 层是否看到相机（VID 8086）
lsusb | grep 8086
# 预期: Bus 00x Device 0xx: ID 8086:0b3a Intel Corp. Intel(R) RealSense(TM) Depth Camera 435i

# 2) V4L2 节点
ls /dev/video*
# 预期: /dev/video0 ~ /dev/video5

# 3) librealsense 枚举
rs-enumerate-devices -s
# 预期:
#   Intel RealSense D435I   231522071694   05.15.01.55

如果 `lsusb` 看不到 `8086`，相机**硬件层**没被识别。先排除：
- 换一个 **蓝色/红色内芯的 USB 3 口**
- 必须用 RealSense 原装 **USB 3 数据线**（不是充电线）
- 拔插一次，等 3-5 秒枚举

## 2. 文件在以下目录中
# 原先的 /home/epai/Desktop/realsense_guide_1.md 中的 /tmp/rs+stream.py /tmp/start-realsense.py 的文件不存在！
ssh unitree@192.168.123.162
# 密码: Unitree0408
mkdir -p /home/unitree/realsense_web
vim /home/unitree/realsense_web/start_realsense.py #unitree_sdk2_python-master/test-example/start_realsense.py
chmod +x /home/unitree/realsense_web/start_realsense.py

## 3. 运行程序
pkill -f start_realsense.py 2>/dev/null
sleep 1
nohup /usr/bin/python3 /home/unitree/realsense_web/start_realsense.py > /home/unitree/realsense_web/start_realsense.log 2>&1 &
tail -100 /home/unitree/realsense_web/start_realsense.log
    nohup: ignoring input
    RealSense pipeline started
    HTTP server started on http://0.0.0.0:8080/
ss -lntp | grep 8080
    LISTEN 0      5              0.0.0.0:8080       0.0.0.0:*    users:(("python3",pid=21901,fd=14))
上位机浏览器：http://192.168.123.162:8080/

# 4. 暂停程序
pkill -f start_realsense.py
ps -ef | grep start_realsense.py
```

## 雷达
~/Downloads/LivoxViewer2 for Ubuntu v2.3.0$ 
./LivoxViewer2.sh



#### 可参考系统信息
```shell
cat /etc/os-release
    PRETTY_NAME="Ubuntu 24.04.4 LTS"
    NAME="Ubuntu"
    VERSION_ID="24.04"
    VERSION="24.04.4 LTS (Noble Numbat)"
    VERSION_CODENAME=noble
    ID=ubuntu
    ID_LIKE=debian
    HOME_URL="https://www.ubuntu.com/"
    SUPPORT_URL="https://help.ubuntu.com/"
    BUG_REPORT_URL="https://bugs.launchpad.net/ubuntu/"
    PRIVACY_POLICY_URL="https://www.ubuntu.com/legal/terms-and-policies/privacy-policy"
    UBUNTU_CODENAME=noble
    LOGO=ubuntu-logo
uname -a
    Linux epai-HYM-WXX 6.17.0-29-generic #29~24.04.1-Ubuntu SMP PREEMPT_DYNAMIC Mon May 11 10:30:58 UTC 2 x86_64 x86_64 x86_64 GNU/Linux
uname -m #表示 Linux 内核识别到的 CPU/机器架构，x86_64 就是 64 位 x86 架构。
    x86_64
dpkg --print-architecture #表示 Debian/Ubuntu 包管理器使用的软件包架构名。amd64 是 Debian 系里对 64 位 x86 的命名。
    amd64
```

### 其他解释和说明
```bash
unitree_sdk2 （Software Development Kit 软件开发工具包）是宇树在 DDS（Data Distribution Service 数据分发服务） 通信框架上封装出来的一套开发接口。
    它支持配置 DDS 通信的 QoS （Quality of Service 服务质量）参数，让应用开发更简单。同时，它还基于 DDS Topic（DDS 中的数据通道名称。发布者-机器人向某个 Topic 发数据，订阅者-上位机从这个 Topic 收数据。） 实现了类似 RPC （Remote Procedure Call 远程过程调用）的请求/响应通信机制。
这个 SDK 适合用于 H1 机器人内部不同进程之间的通信，也适合外部电脑和 H1 机器人内部进程之间通信。通信方式主要有两种：
    发布/订阅像广播：
        一个模块持续发布机器人状态，其他模块订阅后就能收到。
    请求/响应像问答：
        外部程序发一个请求，比如“获取电机状态”，机器人内部服务返回结果。
```

```shell
Remote 配置
cat ~/.ssh/config
vim ~/.ssh/config

#本机安装了两个版本的VScode,互不影响的，最红终端启动code185,
mkdir -p ~/apps/vscode-1.85.2
cd ~/apps/vscode-1.85.2
wget -O vscode-1.85.2.tar.gz "https://update.code.visualstudio.com/1.85.2/linux-x64/stable"
cd vscode-1.85.2
tar -xzf vscode-1.85.2.tar.gz --strip-components=1

~/apps/vscode-1.85.2/bin/code --version
<!-- 1.85.2
8b3775030ed1a69b13e4f4c628c612102e30a681
x64 -->

mkdir -p ~/.vscode-1.85-remote18-data/User
mkdir -p ~/.vscode-1.85-remote18-extensions

cat > ~/.vscode-1.85-remote18-data/User/settings.json <<'EOF'
{
  "update.mode": "none",
  "extensions.autoUpdate": false,
  "extensions.autoCheckUpdates": false,
  "remote.SSH.useLocalServer": true,
  "remote.SSH.useExecServer": false
}
EOF

mkdir -p ~/bin

cat > ~/bin/code185 <<'EOF'
#!/usr/bin/env bash
exec "$HOME/apps/vscode-1.85.2/bin/code" \
  --user-data-dir "$HOME/.vscode-1.85-remote18-data" \
  --extensions-dir "$HOME/.vscode-1.85-remote18-extensions" \
  "$@"
EOF

chmod +x ~/bin/code185
grep -q 'export PATH="$HOME/bin:$PATH"' ~/.bashrc || echo 'export PATH="$HOME/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc

code185 --version
code185 --install-extension ms-vscode-remote.remote-ssh@0.107.1 --force
code185 # 终端启动 1.85.2 VScode，来远程实验室的ubuntu18.04
```