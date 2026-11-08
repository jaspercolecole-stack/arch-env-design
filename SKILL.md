---
name: arch-env-design
description: 建筑设计与环境（景观）设计全流程。做建筑方案、场地总图、景观/环境设计时用它：先立 design.json 单一真相源，再真算指标（容积率/密度/绿地率/退线/消防），再出 DXF / glTF / SVG / PDF 图纸与汇报册；接入 AutoCAD（accoreconsole 批处理）、SketchUp（Ruby）、3ds Max（MAXScript）与 DXF/glTF/OBJ 等中立格式；按中国 GB/CJJ 与国际 IFC/IBC/LEED 双体系校核；调用内置 28 个真实案例的手法标签做类比设计。
whenToUse: 用户提出建筑方案设计、体量生成、总平面/场地设计、景观或环境设计、竖向与雨水组织、日照与消防校核、图纸出图（总平面/平面/剖面/分析图）、把模型或图纸导入 CAD/SketchUp/3ds Max、做设计汇报册或方案评审时。
metadata:
  version: 0.1.1
  truth_source: design.json
  out_dir: <project>/out
---

# 建筑 + 环境设计

本文件只做路由。**不要在这里找方法细节**——按第 0 节的表跳到对应 reference。

## 运行环境（开工前 30 秒，必做）

脚本只要 Python 3.10+（numpy / Pillow / lxml 可选，缺了会退化），不需要任何外部转换器。

**下文所有 `python` 都是占位符**：先探测出一个真能跑的解释器，之后每条命令都用它替换。按顺序试，第一个 `--version` 退出码为 0 的胜出：

| 顺序 | 候选 | 备注 |
|---|---|---|
| 1 | `$env:DSH_PYTHON` | DSH 注入的解释器 |
| 2 | `$env:DSH_HOME\dsh-runtimes\*\dependencies\python\python.exe` | DSH 自带运行时，numpy / Pillow / lxml 齐备 |
| 3 | `py -3` / `python3` | 常规安装 |
| 4 | `python` | **只在 `python --version` 退出码为 0 时可用** |

Windows 头号坑：没装 Python 时 `python` 是 Microsoft Store 的占位符，`Get-Command python` 照样能找到它，但每次调用都以**退出码 9009** 结束、stderr 为空——看起来像「脚本没输出」，实际是解释器根本没启动。**永远先看退出码，再相信输出。**

```powershell
# 探测并打印可用解释器（PowerShell）
$cands = @($env:DSH_PYTHON) + @(Get-ChildItem "$env:DSH_HOME\dsh-runtimes\*\dependencies\python\python.exe" -ErrorAction SilentlyContinue | ForEach-Object FullName) + @('py','python3','python')
foreach ($c in $cands) { if (-not $c) { continue }; try { $v = & $c --version 2>&1; if ($LASTEXITCODE -eq 0 -and "$v" -match 'Python 3') { "PY=$c"; break } } catch {} }
```

拿到路径后，PowerShell 里写成 `& $PY scripts/core/design_io.py check design.json --json`，bash 里写成 `"$PY" scripts/...`。**不要因为一次 9009 就判定技能坏了。**

## 0. 先查这张表

| 用户说 | 先做这一件事 | 读哪份 |
|---|---|---|
| 「做个方案」「这块地能盖多少」 | 立 `design.json`，算指标，再谈形式 | `references/workflow.md` §概念 |
| 「找参考」「学谁的手法」 | 按手法标签检索案例，取 2–3 个先例 | `references/cases.md` + `scripts/cases/find.py` |
| 「算容积率/密度/绿地率/退线/消防/日照」 | 真算，不许估 | `references/codes-cn.md` + `scripts/core/design_io.py` |
| 「出总平面/平面/剖面/分析图」 | 先定比例与线宽层级，再落图元 | `references/drawing-standards.md` |
| 「导 DXF 给 CAD」 | 先确认图层名与导出器一致 | `references/interoperability.md` |
| 「在 SketchUp 里推敲体量」 | 走 glTF/OBJ 中性交换 | `references/interoperability.md` |
| 「3ds Max 出效果图/动画」 | 走 glTF/OBJ + MAXScript 批处理 | `references/interoperability.md` |
| 「做汇报册 / PPT / 评审」 | 先有指标表和图纸，再排版 | `references/report-deck.md` |
| 「这方案好不好看 / 帮我评一下」 | 用否决式信号逐条过 | `references/aesthetics.md` |
| 「国际项目 / LEED / IBC」 | 先判断该走哪套体系 | `references/codes-intl.md` |
| 「交付前自查」 | 跑清单脚本 | `references/checklist.md` + `scripts/check/checklist.py` |

## 1. 五条铁律

1. **先 design.json，后出图。** 没有 `design.json` 就不许生成任何图纸或模型——否则图和数必然对不上。
2. **每次出图必须视觉自检。** 除了写 SVG/PDF/DXF，必须再渲染一张 PNG，**亲自打开看图**：元素不重叠、图框标题栏在位、中文不出豆腐块、线宽层级可辨。没看过图不算出完。
3. **指标真算。** 容积率、密度、绿地率、建筑高度、层数由几何算出来。算不出来就报「缺口」，不许填一个看起来合理的数字。
4. **案例只借手法，不复制形式。** 引用案例必须同时说明借的是哪一层（组织逻辑/空间操作/材料策略/细部），不得把某案例的平面或立面照搬。
5. **不编造条文号。** 只写「规范名 + 指标名 + 限值」；具体条号不确定就写「以现行有效版本核对」。不确定的一律标 `[待核]`。

## 2. 主流程（六步，逐段验收）

| 步 | 动作 | 通过判据 | 产物 |
|---|---|---|---|
| 1 立项 | 问清阶段（概念/方案/深化）、用地边界、功能、指标约束来源 | 能写出 1 页任务书 | 对话 |
| 2 立数 | 写 `design.json`（project/site/masses/elements/materials/metrics） | schema 校验通过 | `design.json` |
| 3 校核 | 跑指标与规范检查，列出缺口 | 无 UNVERIFIED 才能进下一步 | `out/metrics.json`、检查输出 |
| 4 成图 | 总平面 / 平面 / 剖面 / 分析图 | 每张都看过 PNG | `out/*.svg` `out/*.pdf` |
| 5 建模 | DXF + glTF 导出，可回软件继续做 | 能被 CAD/查看器打开 | `out/design.dxf` `out/model.glb` |
| 6 交付 | 汇报册 + 设计说明书 + 缺口清单 | 12 页册子能讲完整故事 | `out/report.pdf`（可选） |

阶段与决策点细节见 `references/workflow.md`。**不许跳过第 2、3 步直接出效果图。**

## 3. 单一真相源 design.json

所有导出器只读它，不各自维护几何副本。

最小可运行骨架（这份能直接通过 schema_lint 与 design_io check，长度单位米）：

```json
{
  "project": {"name": "示例项目", "type": "公建", "standards": "CN", "units": "m"},
  "site": {"boundary": [[0,0],[60,0],[60,40],[0,40]], "north_deg": 0,
           "setbacks": {"front": 6, "back": 6, "left": 6, "right": 6},
           "levels": [{"name": "室外地坪", "elevation": -0.15}]},
  "masses": [{"id": "A", "name": "教学楼 A", "footprint": [[8,8],[32,8],[32,28],[8,28]], "base": 0,
              "height": 12.6, "storeys": 3, "roof": "flat", "material": "concrete"}],
  "materials": {"concrete": {"color": "#D8D3C9", "roughness": 0.8, "metalness": 0.0}},
  "elements": [{"kind": "path", "name": "主路", "points": [[6,34],[54,34]], "width": 6, "surface": "asphalt"}]
}
```

必填项（缺一个 check 就报错）：`project` 的 `name / type / standards / units`（type ∈ 住宅·公建·校园·街区·滨水·公园，standards ∈ CN·INTL）；`site` 的 `boundary / north_deg / setbacks{front,back,left,right} / levels[{name,elevation}]`；每个 `masses[i]` 的 `id / footprint / base / height / roof / material`（roof ∈ flat·gable·shed；层数键是 **`storeys`**，不是 `stories`；可选的 `name / label_offset / overhang / roof_rise`）；`materials` 是**对象**（名字 → `{color, roughness, metalness}` 三个都要有）。

**地面要素写顶层 `elements`，不是 `site.roads`。** `kind` 只能取 `wall / slab / column / opening / path / tree / water / plaza / green / contour / stair`：道路 `{kind:"path", points, width, surface}`；水面／广场／绿地 `{kind:"water|plaza|green", polygon}`；乔木 `{kind:"tree", center, radius}`。写错位置不会被读取，绿地率与道路面积会因此失真。

`project.units` 只接受 `"m"`——坐标与尺寸全部是米（毫米另用带 `_mm` 后缀的键，如 `dims_mm`、`module_mm`），坐标原点取用地西南角；绘制原语层 `draw2d.py` 内部固定用毫米，换算在导出时做。校验与指标：`python scripts/core/design_io.py check <design.json> --json`（输出 `metrics{site_area,building_area,footprint_area,far,density,greenRatio,max_height,stories}`）。`check` 的 `warnings` 会指出**写错位置的键**（如 `site.roads`）与**越出红线的图元**（含越界面积），看到就改，不要当噪声忽略。结构自检：`python scripts/core/schema_lint.py <design.json>`（纯标准库；退出码 0 通过 / 1 不合规并给字段路径 / 2 读不到文件；本校验器未实现的关键字——oneOf、patternProperties、minProperties 等——会以 warning 列出，**不静默通过**）。字段全表见 `scripts/core/design.schema.json`。

## 4. 出图与建模

出图只有一个原语层：`scripts/core/draw2d.py`（毫米单位），向下有三条同源后端——SVG、PDF、PNG。**不要绕过它自己拼 SVG。**

```bash
python scripts/core/svg_draw.py <design.json> --out <project>/out --sheets siteplan,plan,section
python scripts/core/to_pdf.py   <design.json> --out <project>/out
python scripts/core/dxf_export.py <design.json> --out <project>/out/design.dxf
python scripts/core/gltf_export.py <design.json> --out <project>/out/model.glb
```

线宽按 `references/drawing-standards.md` §2 取：主层级 `0.70 / 0.35 / 0.18 / 0.09 mm`（相邻 2:1），`0.13 / 0.05 mm` 只是辅助值；代码里现成常量是 `draw2d.LW = {outline 0.70, secondary 0.35, detail 0.18, hatch 0.13}`，要 0.09/0.05 直接写数值并说明用途；DXF 图层名固定为 `A-SITE / A-WALL / A-GLAZ / A-COLS / L-PLNT / L-WATER / A-ANNO / A-DIMS`，改名前先读 `references/drawing-standards.md`（改了 CAD 侧就接不上）。

## 5. 规范校核

```bash
python scripts/check/checklist.py <design.json> --stage 方案 --json
```

输出 PASS / FAIL / WARN / **UNVERIFIED** 四类。**UNVERIFIED 必须逐条处理**：要么补数据，要么写进交付说明的「缺口清单」，不许静默略过。规范体系、指标体系、处置顺序见 `references/codes-cn.md`；国际项目见 `references/codes-intl.md`。

## 6. 软件接入

优先级：**中立格式（DXF/glTF/OBJ/IFC）> 命令行自动化 > 手工点击**。GUI 点击不可复现，不要作为主路径。

| 软件 | 自动路径（已实测语法） | 手工路径 |
|---|---|---|
| AutoCAD 2026 | `accoreconsole.exe /i <input.dwg> /s <script.scr>`（`/s` 必填；可加 `/isolate` `/readonly`） | 打开 `out/design.dxf` |
| SketchUp 2025 | 无真无头模式：`SketchUp.exe -RubyStartup <boot.rb>` 会开窗口，只能进程内建模 | 导入 `out/model.glb` 人工确认 |
| 3ds Max 2022 | `3dsmaxbatch.exe -mxs "<expr>" -listenerLog <log>`（**不是** `3dsmaxcmd.exe`，那个只做渲染） | 导入 OBJ |

三条实测结论，别写反：

- `accoreconsole` 的输入是**图形文件**不是脚本，脚本走 `/s`。零 Python 依赖，是这台机器上最可靠的一环。
- **没有 ODA 转换器就读不了 DWG**：DWG 是本机交换层的外部限制，一律用 DXF R12 文本（`AC1009`）当交换格式。`scripts/core/dxf_export.py` 就是为此只用 ASCII 手写。
- SketchUp 的 Ruby API 依附 `Sketchup.active_model`，二进制里没有任何批处理开关——不要承诺「SketchUp 无头批处理」。

命令模板、脚本落点与坑（编码、单位、坐标系）见 `references/interoperability.md`。软件不在本机时**不要假装成功**：产出中立格式，并告诉用户在哪个软件里打开。

## 7. 撰写机的环境事实（示例，不是你的机器）

以下是本技能最初被写出来时的机器实测，**换台机器请重新探测**（见开头「运行环境」），别把它们当成通用前提：

- 该机上 Python 只有 DSH 自带运行时可用（3.12；有 numpy/Pillow/lxml；**不许 pip 安装**）；裸 `python` 是 Store 占位符。
- 已装：AutoCAD 2026、SketchUp 2025、3ds Max 2022、Photoshop 2021。**没有** Blender/Revit/Rhino/FreeCAD。
- **没有** LibreOffice / Ghostscript / poppler / ImageMagick；Edge headless 在本沙箱不可用（命名管道被拒）。**因此禁止调用任何外部程序做格式转换**，PDF 一律由纯 Python 生成。
- 中文字体只用 `C:\Windows\Fonts\simhei.ttf`（普通 TTF）；`msyh.ttc`、`simsun.ttc` 是 TTC，不要直接嵌。
- 文本文件 UTF-8 无 BOM。

## 8. 交付目录

```
<project>/
  design.json
  out/
    metrics.json           # python scripts/core/design_io.py metrics design.json --json > out/metrics.json
    design.dxf  model.glb
    siteplan.svg plan.svg section.svg
    siteplan.pdf plan.pdf section.pdf
    preview/*.png          # 视觉自检产物，必须存在
    report.pdf             # 汇报册（可选；链路见 references/report-deck.md，本技能不保证一键出册）
    README.md              # 交付说明：做了什么、缺口是什么
```

**不覆盖用户已有文件**；同名文件先备份成 `*.bak`。

## 9. 出手前的门

交付前逐条确认，任何一条不成立就先修再交：

- [ ] `design.json` 存在且 schema 校验通过
- [ ] `checklist.py` 没有未处理的 FAIL；UNVERIFIED 已进缺口清单
- [ ] 每张图都有对应的 PNG，且**你看过**
- [ ] DXF 能被打开，图层名与约定一致
- [ ] 案例引用写明了借的是哪一层手法
- [ ] 交付说明里写清了「哪些是算出来的、哪些是假设的、哪些还没做」

## 10. 不要做的事

- 不要在指标不成立时用渲染图打动用户（那是意向不是方案）
- 不要把「参考了某案例」写成「照某案例做的」
- 不要给不确定的规范编号编一个年份
- 不要在没看图的情况下宣布出图完成
- 不要为了跑通而静默跳过失败的步骤
