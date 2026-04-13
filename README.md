# pyOCD GUI

基于 PySide6 的 pyOCD 固件烧录图形界面工具。

## 功能

- 自动检测已连接的调试器（CMSIS-DAP / J-Link 等）
- 选择目标芯片型号
- 选择固件文件（.bin / .hex / .elf）
- 一键烧录，实时显示进度与日志
- 支持擦除、烧录、校验操作

## 安装

```bash
pip install -e ".[dev]"
```

## 运行

```bash
pyocd-gui
# 或
python -m pyocd_gui.main
```

## 项目结构

```
src/pyocd_gui/
├── main.py          # 程序入口
├── core/
│   ├── flash_worker.py   # 烧录后台线程（QThread）
│   └── probe_manager.py  # 调试器探头管理
├── ui/
│   ├── main_window.py    # 主窗口
│   └── widgets/
│       ├── probe_selector.py  # 探头选择组件
│       ├── target_selector.py # 目标芯片选择组件
│       ├── file_picker.py     # 固件文件选择组件
│       └── log_viewer.py      # 日志输出组件
└── utils/
    └── config.py         # 应用配置持久化
```
