# pyOCD GUI

基于 PySide6 的 pyOCD 固件烧录图形界面工具。

## 功能

- 自动检测已连接的调试器（CMSIS-DAP / J-Link 等）
- 选择目标芯片型号
- 选择固件文件（.bin / .hex / .elf）
- 一键烧录，实时显示进度与日志
- 支持擦除、烧录、校验操作
- DFP 包管理：按芯片查询、安装 CMSIS-Pack、更新索引和查看已安装包
- BIN 自定义地址、多镜像一次提交烧录（可混合 BIN/HEX/ELF）
- 独立 Mass/Chip/地址范围擦除、独立复位
- 芯片信息侧栏：核心、探针 UID、Flash/RAM 内存映射
- 独立文件/Flash 逐字节比对，显示差异地址与两侧字节，支持导出 CSV

## 烧录与设备操作

- **Add Images** 添加镜像，选中行后可用删除按钮移除。BIN 的 **BIN Address** 接受
    `0x08004000` 形式的十六进制地址或十进制地址；留空使用目标启动内存地址。
    HEX/ELF 使用内嵌地址，不接受 BIN 地址覆盖。
- 多镜像在同一次连接内缓冲后统一提交，不会为每份镜像重复整片擦除。
    文件、地址及重叠检查在提交前完成。Sector/Smart 模式保留受影响扇区内未覆盖的数据；
    Chip erase 仍会擦除整个 Flash，不适合保留其他分区的场景。
- **Standalone Erase** 选择 Mass、Chip 或 Address range，再点击 **Erase Flash**。
    范围使用 `[Start, End)`，结束地址不包含在内；两端必须严格对齐目标扇区边界，
    不会自动向外扩展。所有擦除操作均需二次确认。范围擦除不启用自动解锁。
- **Standalone Reset** 选择默认、System、Hardware pin 或 Emulated，再点击 **Reset Target**。
    支持程度取决于目标和探针；此操作不烧录固件。
- **Chip Info** 通过 attach 连接且不自动解锁，显示核心、探针 UID 和内存区域。
    显示的容量来自所选目标的 pyOCD 内存映射，不代表额外读取了芯片容量寄存器。
    各设备操作互斥，执行期间不可关闭主窗口。
- 读取不会自动解锁芯片。未勾选操作后复位时，读写和擦除均不在断开连接时主动恢复运行。
    如果读取中发现目标重新运行或复位，会报错且不保存快照；带看门狗的固件可能需要
    通过 pyOCD 用户脚本设置调试冻结，不能仅依赖暂停 CPU。

## Flash 比对

1. 在主窗口添加参考镜像，并为 BIN 设置地址（留空使用目标启动地址）。
2. 点击 **Compare Flash** 打开独立窗口，确认文件、目标芯片和探针。
3. 点击 **Compare**，完成后显示 MATCH 或 DIFFERENT、实际比对字节数、
    不同字节总数及首个差异地址。表格并排显示地址、文件字节和 Flash 字节。
4. **Export Differences** 导出当前显示的差异为 CSV；**Cancel** 可在读取块之间取消。

比对直接回读，不烧录、不擦除、不自动解锁，也不执行复位。目标会短暂暂停，
结束后恢复原来的运行状态；原本暂停的目标保持暂停。
支持多镜像及 BIN/HEX/ELF，仅检查文件实际覆盖的 Flash 区间：HEX 空洞、ELF 的
未初始化数据，以及镜像范围之外的内容不参与比较。映射到 RAM 的装载段不支持此操作。

所有不同字节均计入总数，但界面和 CSV 最多保留前 256 个明细，避免大量差异卡住窗口。
探针错误、短读取、目标中途复位或取消操作会显示未完成，不生成有效比对结果。

STM32F1 目标可显式勾选 **Freeze STM32F1 watchdogs during comparison**，
在暂停期间临时冻结 IWDG/WWDG。该选项默认关闭，结束或异常退出时尝试恢复原
`DBGMCU_CR`，不修改固件；其他芯片仍需使用适合其型号的调试配置。

## 测试

```bash
uv run python -m unittest discover -s tests -v
```

多镜像测试自动生成一份 BIN、按边界拆成两份，使用真实 pyOCD 文件解析器验证
地址、逐字节重组及单次提交；日常测试不连接或写入硬件。

显式硬件验收脚本默认只读取备份并构造拆分镜像：

```bash
uv run python tests/hardware_phase_one.py --firmware B04_1_0.bin --target stm32f103rb --probe PROBE_UID
```

加 `--write` 才会实际擦写。脚本在临时目录保存完整 Flash 备份、两份 BIN 和 JSON 报告，
然后一次提交两份镜像、比较整个 Flash（包括镜像之外的数据）并执行独立 System Reset。
该脚本仅适用于已确认型号、单 Flash 区域且允许被测试固件覆盖的设备。

范围擦除与读取验收增加 `--erase-read-test --write`：先比较 GUI BIN/HEX 导出与独立备份，
再在末尾非启动扇区写入图样并擦除，检查擦除区全为 `0xFF` 且范围外不变，最后恢复完整备份。
中途失败也会尝试恢复；恢复未校验通过时不会主动复位运行。

本次 STM32F103RB 固件启用了看门狗，调试暂停仍会自行复位。复测可显式添加：

```bash
uv run python tests/hardware_phase_one.py --firmware B04_1_0.bin --target stm32f103rb --probe PROBE_UID --write --erase-read-test --freeze-stm32f1-watchdogs
```

该冻结选项仅用于 STM32F1 验收：通过公开 `did_connect` 钩子设置 `DBGMCU_CR` 的
IWDG/WWDG 调试冻结位，所有探针读写仍走真实 worker；成功结束后恢复寄存器并复位。
GUI 没有默认开启此芯片专用设置。BIN/HEX 导出保持精确长度，不额外追加结束字节。

## DFP 包管理

点击 Target 旁的 **DFP Packs** 打开管理器，默认显示已安装包。

1. 首次使用或查询不到新芯片时，点击 **Update Index** 更新在线索引。
2. 输入芯片型号，例如 `stm32g0b1retx`，点击 **Find DFP** 查询对应包和安装状态。
3. 点击 **Install** 并确认，下载包含匹配芯片的 DFP 包。宽泛型号可能匹配多个包，建议先查询。
4. 安装完成后，主窗口自动刷新 Target 列表和输入补全，保留当前输入。

管理器与当前 Python 环境下的 `pyocd pack` 共用缓存，不需要单独配置包目录。
索引更新与安装需要联网，查询和已安装列表使用本地缓存。命令在独立后台进程中执行，
输出实时显示；执行期间不能关闭管理窗口。探测或读写芯片期间管理入口不可用。

当前不提供单包卸载：pyOCD 的 `pack clean` 会删除全部包和索引，不适合作为单包删除操作。

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
