# H1 控制面板工程化重构说明

## 删除的冗余内容

- 删除未真正挂载到界面的参数页大段代码。
- 删除视频 / 深度相机说明中对应的未实现逻辑，避免误导维护者。
- 删除重复 import、`QToolButton` 等未使用依赖。
- 删除大块被注释掉的旧代码。
- 合并重复的安全读取、格式化、对象摘要工具函数。
- 将 LowState / LiDAR / PCD / SSH / UI 的职责分开。

## 重构后的结构

- `RobotConfig`：集中管理 IP、网卡、Topic、IDL、PCD 文件路径。
- 工具函数区：时间、翻译、终端清洗、类型转换、字段格式化。
- 解析函数区：LowState、BMS、MotorState、LiDAR State。
- `LowStateWorker`：只负责 LowState 后台读取。
- `LidarStateWorker`：只负责 LiDAR State 后台读取。
- `PointCloudCanvas`：只负责 PCD 点云显示。
- `H1RobotClient`：连接状态协调，不直接写 UI。
- `LoginDialog / NavigationDialog / MainWindow`：分别负责登录、导航 SSH、主界面。

## 注释说明

源码中已经对每个可执行代码块添加中文注释。为了避免代码变得不可维护，以下内容没有逐属性展开：
- Qt 样式表中的每一个 CSS 属性。
- 同一行中多个短 Qt 设置语句的每个子表达式。

## 运行

```bash
pip install PyQt5 matplotlib numpy
python3 h1_control_panel_engineered_annotated.py
```

真机 SDK2 读取需要额外安装 `unitree_sdk2py`。

导航 SSH 需要：

```bash
sudo apt install sshpass
```

## 仍保留的限制

- 指令按钮仍为安全占位，不发布 LowCmd。
- PCD 读取器只支持 ASCII PCD。
- ROS2 / TCP 预留项已移除到最小，后续建议单独实现通信适配层。
- 导航窗口默认密码仍是演示值，正式项目建议改为运行时输入或安全密钥管理。
