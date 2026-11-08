# 互操作层（CAD / SketchUp / 3ds Max）

三条通道，按可靠性排序。**永远先走中立格式，需要专有能力时才升到命令行自动化，最后才让用户手动点。**

| 通道 | 可靠性 | 能否无人值守 | 适用 |
|---|---|---|---|
| 中立格式（DXF/glTF/OBJ/SVG/PDF） | 高（纯文件读写） | 是 | 交付、校核、二次编辑的默认路径 |
| 命令行自动化（accoreconsole / 3dsmaxbatch） | 中（厂商工具，会崩、编码古怪） | 是，但要防崩 | 批量改图、批量渲染、批量清图 |
| 软件内脚本（SketchUp Ruby） | 中（需要软件真的开着） | 否 | 建模、体量推敲、贴材质 |

## 1. 本机实测事实（不要凭印象，凭这些）

| 软件 | 版本/路径 | 无头入口 | 实测结论 |
|---|---|---|---|
| AutoCAD | 2026，`D:\CAD\AutoCAD 2026\` | `accoreconsole.exe /i <input.dwg> /s <script.scr>` | `/s` 必填；`/?` 回显是 **UTF-16LE 宽字符**；曾出现"回显被重定向到临时文件 + 退出码 0xC06D007E 崩溃" |
| SketchUp | 2025，`D:\SU1\2025\SketchUp\SketchUp.exe` | **没有真正的无头模式** | `-RubyStartup <boot.rb>` 会开窗口；Ruby 3.2.2 内嵌，API 依附 `Sketchup.active_model` |
| 3ds Max | 2022，`D:\3d\3ds Max 2022\3ds Max 2022\` | `3dsmaxbatch.exe -mxs "<MAXScript>" -listenerLog <log>` | `3dsmaxcmd.exe` 只是 command line renderer，不是脚本宿主 |

已知的"读不了"清单（别再试）：

- `.dwg` 二进制：本机无 ODA File Converter、无 Teigha → **只有 DXF R12 文本能读**。
- `.skp`：全站没有可用的开源读写库（曾存在的 skp2tri 于 2013 停更）→ **只能由 SketchUp 自己导出**。
- `.max`：只能由 3ds Max 自己打开。
- Python 侧 `ezdxf / trimesh / ifcopenshell / reportlab / cairosvg` **都不在本机**，且不许 pip 安装 → 导出器全部手写。

## 2. 中立格式契约

| 格式 | 由谁产出 | 单位 | 去向 | 注意 |
|---|---|---|---|---|
| DXF R12（AC1009，纯 ASCII） | `scripts/core/dxf_export.py` | 毫米 | AutoCAD / 天正 / Rhino / Illustrator | R12 是最保守的版本，任何工具都能开；R12 无 unicode，中文必须写拼音或英文 |
| glTF 2.0 `.glb`（单文件） | `scripts/core/gltf_export.py` | 米 | SketchUp / 3ds Max / Blender / three.js / 网页 | y-up；材质用 PBR metallic-roughness；单文件避免 .gltf+.bin 走散 |
| OBJ | 同上（可选） | 米 | 通用兜底 | 无材质打包，纹理要单独给 |
| SVG | `scripts/core/svg_backend.py` | 毫米 | Illustrator / 浏览器 / 排版 | 文字用系统字体，跨机器会掉字 → 正式交付要 PDF |
| PDF | `scripts/core/pdf_backend.py` | 毫米→pt | 打印、送审、汇报 | 文字走轮廓路径，不依赖字体，任何机器打开都一样 |

图层名是硬契约，导入后不要改名，否则下游的 `A-*` / `L-*` 分类会断：
`A-SITE`（用地/场地）、`A-WALL`（墙）、`A-GLAZ`（门窗幕墙）、`A-COLS`（柱/结构）、`L-PLNT`（种植）、`L-WATER`（水景）、`A-ANNO`（文字符号）、`A-DIMS`（尺寸标注）。

## 3. 命令行自动化的调用纪律

所有外部进程调用都走 `scripts/interop/run_ext.py`，不要自己写 `subprocess`（下面的 `python` 按 `SKILL.md`「运行环境」探测出的解释器替换）：

```bash
python scripts/interop/run_ext.py --timeout 180 --json --name acad_batch -- \
    "D:\CAD\AutoCAD 2026\accoreconsole.exe" /i "site.dwg" /s "plot.scr"
```

它替你处理了这些踩过坑的地方：

- **编码**：厂商工具的输出经常是 UTF-16LE 或混杂字节，脚本按 NUL 比例与坏字节率判定编码再解码，不会抛 UnicodeDecodeError。
- **超时与杀进程树**：`--timeout` 到点后杀掉整棵子进程树（AutoCAD/Max 会派生一堆），返回结构化的超时结论而不是挂死。
- **日志**：默认写 `<cwd>/out/logs/<时间戳>-<name>.log`，同时记录命令行、退出码、前后各若干行输出。崩溃后你还能查到它说了什么——厂商工具崩溃前常常先把话说进临时文件。
- **自测**：`--selftest` 跑内置五项自测（编码、超时、退出码透传等），换机器后先跑它。

三条死规则：

1. **不在用户的工作目录里乱写**：脚本文件落 `out/scripts/`，日志落 `out/logs/`。
2. **不覆盖输入文件**：任何批处理都输出到 `out/`，绝不原地 `open -save`。
3. **崩溃即降级，不反复重试**：同一个命令崩溃两次，就改走中立格式路径，并在交付说明里写明"该步需要你在软件里手动完成"。

## 4. 各软件的可用剧本

### AutoCAD（accoreconsole）

```
plot.scr:
  (setvar "FILEDIA" 0)
  (command "_.AUDIT" "Y")
  (command "_.SAVEAS" "" "out\cleaned.dwg")
```

能做的：清理、批量改图层/颜色、批量打印出 PDF、统计图元数量。
做不到的：需要交互对话框的命令；需要授权弹窗第一次运行时的确认。
**第一次运行必须在有人看着的情况下跑一次**，把授权与信任对话框过掉，之后才可无人值守。

### SketchUp（Ruby，会开窗口）

```bash
"D:\SU1\2025\SketchUp\SketchUp.exe" -RubyStartup "out\scripts\su_body.rb"
```

`su_body.rb` 只做三件事：清空场景 → 按 `design.json` 的 `masses` 拉体块 → 导出 `out/model.skp` 与 `out/model.obj`。
它一定会开窗口，所以：跑之前告诉用户"会弹窗，别关"；脚本里 `Sketchup.active_model` 为空就直接 abort 并写日志，不要空转。

### 3ds Max（3dsmaxbatch）

```bash
"D:\3d\3ds Max 2022\3ds Max 2022\3dsmaxbatch.exe" -mxs "fileIn @\"out\\scripts\\render.ms\"" -listenerLog "out\logs\max.txt"
```

能做的：导入 glTF/OBJ、铺材质、批量渲染出图。
渲染预设统一到：1900×1080、Corona/V-Ray 有无自动降级为 Scanline、输出 `out/render/*.png`。
**MAXScript 一律写成 `.ms` 文件再 `fileIn`**，不要把长脚本塞进 `-mxs` 字符串（引号地狱 + 命令行长度限制）。

## 5. 保底路径（当自动化不可用时）

自动化失败不要硬扛，明确降级成人能执行的三步，并打印给用户：

1. 打开 `out/design.dxf`（或 `out/model.glb`）——这是唯一必须成功的产物。
2. 在该软件里做那件机器做不了的事（贴图、细化节点、调镜头）。
3. 导出回 `out/`，然后继续跑 `scripts/check/checklist.py` 与出图脚本。

判断标准很简单：**任何一步卡住，都必须留下一个用户能接手的中立格式文件。**没有这个文件，等于这一步白做。

## 6. 校核结论怎么写进交付

互操作层的结论只有三种，写交付说明时照抄其一，不要含糊：

- **已自动完成**：附命令、退出码、产物路径、字节数。
- **需人工**：说明哪一步、为什么机器做不了（引用本文件第 1 节的事实）、给出接手指令。
- **不可行**：直接说做不到（例如"本机无法读取 .skp"），并给出替代路径（让 SketchUp 导出 obj/dae）。
