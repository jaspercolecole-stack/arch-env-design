# arch-env-design · 建筑与环境设计技能 交付说明

生成日期：2026-10-23（2026-11-08 修订至 0.1.1）　｜　版本 0.1.1　｜　单一真相源：`design.json`

---

## 1. 它是什么

一个**可自动执行**的建筑 / 环境（景观）设计技能。给一个用地范围与功能需求，它能：

1. 把设计意图写成机器可算的 `design.json`（几何 + 材料 + 约束 + 决策记录）；
2. **真算**指标——用地面积、建筑面积、建筑密度、容积率、绿地率、最大高度、层数、停车位；
3. 出三张 **A1 图纸**（总平面 / 平面 / 剖面）：SVG 矢量源 + PDF 正式图 + PNG 自检图，带图框、标题栏、指北针、比例尺、图例、设计说明、技术经济指标表；
4. 导出 **DXF R12**（8 个约定图层，给 AutoCAD）与 **glTF 2.0 / GLB**（带 PBR 材质，给 SketchUp / 3ds Max / 任意查看器）；
5. 按 **GB / CJJ** 或 **IBC / LEED** 双体系跑规范校核，输出 PASS / WARN / FAIL / UNVERIFIED 四态清单；
6. 从 **28 个真实案例库**里按手法标签检索先例（自然语言也行），只借手法不抄形式。

它**不**替你做设计决策：选址、功能配比、形式取向仍需你定；它负责把决策变成可算、可查、可交付的东西。

---

## 2. 装在哪

| 级别 | 路径 | 说明 |
|---|---|---|
| 工作区级 | `D:\自己\工作区\.dsh\skills\arch-env-design\` | 仅本工作区可用 |
| 用户级 | `C:\Users\Zhao5\.dsh\skills\arch-env-design\` | 所有会话可用 |

技能是 bundle 形式（`<name>/SKILL.md`，装在点开头的 `.dsh` 目录里，资源管理器默认不显示），DSH 热加载，无需重启。安装只需把仓库里的 `SKILL.md`、`references/`、`scripts/`、`assets/`、`examples/` 复制到 `<skills 根>/arch-env-design/`，或在本仓库根目录跑：

```powershell
.\install_skill.cmd -Scope User                                  # 用户级
.\install_skill.cmd -Scope Workspace -WorkspaceRoot "<工作区>"    # 工作区级
```

0.1.1 起工作区级、用户级与仓库三者逐文件 SHA256 一致。

**开工前先探测解释器**（`SKILL.md`「运行环境」一节）：本机裸 `python` 是 Microsoft Store 占位符，`Get-Command python` 照样找得到，但每次调用都以**退出码 9009** 结束、stderr 为空——看起来像「脚本没输出」，实际是解释器没启动。下文所有 `python` 都是占位符，替换成探测到的真解释器。

目录结构：

```
arch-env-design/
  SKILL.md                     # 唯一入口，只做路由（158 行）
  references/                  # 9 份方法文档，按需读
    workflow.md  cases.md  aesthetics.md  codes-cn.md  codes-intl.md
    drawing-standards.md  checklist.md  interoperability.md  report-deck.md
  scripts/
    core/    design_io.py  schema_lint.py  draw2d.py  svg_draw.py
             to_pdf.py  pdf_backend.py  png_backend.py  svg_backend.py
             dxf_export.py  gltf_export.py  design.schema.json
    check/   checklist.py
    cases/   find.py
    interop/ run_ext.py
  assets/     case-library.json（28 案例 / 112 标签）  palette.json（色板）
  examples/demo-campus/   design.json + expected/（三张 SVG、DXF、GLB）
```

---

## 3. 怎么用（五条命令，全部零外部依赖）

```bash
# 0 结构自检（改完 design.json 立刻跑；退出码 0/1/2）
python scripts/core/schema_lint.py <design.json>

# 1 真算指标
python scripts/core/design_io.py check <design.json> --json

# 2 出三张 A1 图（PDF + PNG 预览，PNG 用于视觉自检）
python scripts/core/to_pdf.py <design.json> --out <project>/out

# 3 导 CAD 与三维
python scripts/core/dxf_export.py  <design.json> --out <project>/out/design.dxf
python scripts/core/gltf_export.py <design.json> --out <project>/out/model.glb

# 4 规范校核（概念 8 条 / 方案 19 条 / 深化 23 条）
python scripts/check/checklist.py <design.json> --stage 方案 --json

# 5 检索先例
python scripts/cases/find.py --problem 滨水修复 --limit 5
```

数据契约上最容易踩的一个坑：体量的层数键是 **`storeys`**，写成 `stories` 会被静默忽略（现在 `check` 会给 warning）；地面要素必须写进顶层 `elements` 并用 `kind` 区分，写进 `site.roads` 不会报错但**不会被读取**（也会 warning）。完整的必填清单在 `SKILL.md` §3。

依赖只有本机已有的 Python 3.12 + numpy + Pillow + lxml。**不调用任何外部程序做格式转换**（本机无 LibreOffice / Ghostscript / poppler / ImageMagick，Edge headless 在沙箱内不可用），PDF 由纯 Python 手写结构、文字走字体轮廓填充。

---

## 4. 真的跑过（不是自述）

### 4.1 示例工程 demo-campus（虚构但自洽：4 栋教学楼 27,120 m²）

| 项 | 结果 |
|---|---|
| 指标 | 用地 14,772 m²、建筑 27,120 m²、密度 0.3861、容积率 1.8359、绿地率 0.4651、限高 21.6 m（`limits` 上限 2.0 / 0.40 / 0.35 / 24.0 m，全部在限内） |
| 图纸 | 三张 A1，637 / 634 / 736 个图元，PDF 1,691,349 / 1,643,527 / 1,535,609 B，PNG 273,766 / 232,206 / 256,336 B |
| 出图来源 | `source = svg_draw` ×3（非回退路径） |
| DXF | 14,613 B，纯 ASCII，`$ACADVER AC1009`，8 图层齐全，无 LWPOLYLINE |
| GLB | 10,188 B，5 mesh / 24 node / 9 material / 50 三角形，楼层实例化（3.6 m 一层逐层累加到 14.4 / 18.0 / 21.6 / 10.8 m）；文件里不写任何绝对路径，换目录、换工作目录重导 SHA256 完全一致 |
| 规范 | 深化阶段 PASS 22 · FAIL 0 · UNVERIFIED 1（DET-NODE-01：本技能不出 1:20 节点详图，属诚实缺口） |
| 案例检索 | 10 条自然语言查询全部非空，top1 命中合理（滨水修复→C15 碧山宏茂桥；旧厂房改造→C04 PSA） |

### 4.2 第二个工程（换用地形状 / 换功能，验证泛化）

L 形用地 78×96 m 带 30×42 m 凹口，3 栋体量（文化馆 / 图书室 / 连廊）。在同一套脚本上闭环跑通：用地 5,868 m²（与手算一致）、容积率 0.8003、密度 0.2863、绿地率 0.5894；三张图、DXF 6,781 B（8 图层表齐全；本工程无玻璃洞口故 A-GLAZ 层为空）、GLB 7,008 B（4 mesh / 12 node / 4 material / 38 三角形）全部生成。**说明脚本不绑定示例工程。**

### 4.3 验收器

`verify_skill.py`（父级工具，不属于技能本体）18 项自动检查，全绿：文件骨架、指标偏差、案例库标签封闭性、10 条检索、三阶段校核、SVG/PDF/PNG 出图、出图来源、DXF 图层、GLB 结构、绘制顺序不变量、schema 覆盖、文档内引用的脚本与文件是否真实存在。

### 4.4 每张图都亲眼看

出图后渲染 PNG 逐张审：图框双线、标题栏、指北针、比例尺、图例、说明、尺寸链、轴号圆圈、门窗表、房间面积表是否在位；中文是否出豆腐块；线宽层级是否可辨。据此修掉的真实问题（都是看图才暴露的）：

- 总平面**整页纸色矩形被追加在 items 末尾**，把前面画的内容刷白（首跑 PNG 非背景像素只 0.79%）→ 改为永远插在 `items[0]`；
- 比例硬跟 design.json 请求值，150×110 m 的用地在 1:500 下只占 A1 的三分之一 → 改为按可用绘图区自动优选比例并在图面注明；
- 玻璃材质的楼栋被填成水面蓝，与图例撞色 → 总平面建筑统一中性色，材料色只用于立面与材料分析图；
- 剖面的楼层标高表列了**别的楼**的 +18.000 / +21.600 → 改为由被剖切那栋自己推层数；
- 竖向总尺寸被画成悬空长条盒子（尺寸线离图 11 mm、界线贯全幅）→ 界线只从图形边界引出、45° 起止符 2 mm、偏移 10 mm；
- 平面窗只画成贴墙细蓝带 → 按 240 mm 真实墙厚画四条边围合的洞口，玻璃居中。

---

## 5. 能接哪些软件（本机实测，不是照抄文档）

优先级：**中立格式 > 命令行自动化 > 手工点击**。

| 软件 | 自动路径 | 实测结论 |
|---|---|---|
| AutoCAD 2026 | `accoreconsole.exe /i <input.dwg> /s <script.scr>` | `/s` 必填；`/?` 回显是 UTF-16LE（脚本侧必须处理编码）。**本机在此沙箱内它以 0xC06D007E 崩溃**，已如实记录，不作为主路径 |
| SketchUp 2025 | 无真无头模式（`-RubyStartup` 会开窗口，Ruby 3.2.2 依附 `Sketchup.active_model`） | 走 glTF/OBJ 交换 + 人工确认 |
| 3ds Max 2022 | `3dsmaxbatch.exe -mxs "<expr>" -listenerLog <log>` | `3dsmaxcmd.exe` 只做渲染，不是自动化入口 |
| 任意 CAD / 查看器 | DXF R12（AC1009，纯 ASCII，毫米）+ glTF 2.0 GLB（米，y-up） | 本技能自己生成，不依赖任何转换器 |

`.dwg` 与 `.skp` 没有开源读写库，本机也没有 ODA 转换器——**这是外部限制，不是偷懒**：技能一律以 DXF / GLB 作为交换层，需要原生格式时由你在软件里另存。

---

## 6. 已知限制（诚实清单）

1. **不出节点详图**：图纸集只到 1:100 / 1:200，1:20 / 1:10 放大节点不在能力内（所以 DET-NODE-01 恒为 UNVERIFIED）。
2. **绿地率按面积相减**算（用地 − 建筑基底），不认 `design.json` 里声明的绿地多边形；要按多边形口径需另行对齐。
3. **规范校核到「指标 + 限值 + 名」级别**，不做到条文逐字判定；引用只写规范名与指标名，编号不确定标 `[待核]`，绝不编造条号。
4. **结构校验不是完整 JSON Schema validator**：`oneOf / patternProperties / minProperties` 等未实现，会以 warning 明示而**不静默通过**。
5. **SketchUp 无法无头批处理**；3ds Max 的 `3dsmaxbatch` 开关组合仍有未核实项（`-secure` / `-safescene` 语义）。
6. **无外部转换器**：不做 DOCX→PDF、SVG→PNG（除自带 PNG 后端）等格式转换；汇报册链路需按 `references/report-deck.md` 的现状走，不是一键出册。
7. 剖面图纸的窗按「每开间居中」等分排布，与 `design.json` 里逐洞口 x 中心不是一一对应（按实际 x 会出现 8.2 m 空端垛）；右侧尺寸数字已挪到尺寸线外侧，但屋面区四条引注与轮廓仍有短距贴近。
8. 62 m 长的建筑在 1:100 下只占 A1 绘图区约三分之一高度，剖面图上半部留白是比例与图幅的客观结果（放大到 1:75 会超出图幅）。

---

## 7. 对照你的原始要求

| 你的要求 | 落地情况 |
|---|---|
| 接入 CAD / SketchUp / 3ds Max 等多种主流软件 | 三通道（中立格式 / 命令行 / 软件内脚本）+ 三个软件的实测语法与本机可用性，写进 `references/interoperability.md`；DXF 与 GLB 由技能自己产出 |
| 自主结合知名案例做设计 | 28 个真实案例（苏州博物馆、宁波博物馆、龙美术馆、后滩公园、碧山宏茂桥、高线、菊儿胡同、达士岭……）结构化入库，112 个受控手法标签；自然语言检索 + 「借哪一层手法」的四段引用格式 + 案例卡模板 |
| 搜索 GitHub 找成熟项目与开源方案 | 调研落 `research/01-interop-survey.md`：ezdxf 1491★、IfcOpenShell 2884★、trimesh 3708★、osmnx 5883★、WeasyPrint 9727★、resvg 4154★ 等；本机禁 pip 且这些库都不在，因此**只取思想不取依赖**（DXF 手写、PDF 手写、glTF 手写），换来零安装可跑 |
| 高效率 | 一个 `design.json` 驱动全部产物；三张 A1 图 + DXF + GLB 全量重出约 40 秒；案例检索 0.2 秒 |
| 有审美 | 4 级线宽 2:1 层级、10 色语义色板（含 Okabe-Ito 色盲安全）、A1 图面网格与图例说明分区、比例自动优选——并且**每张图都人工看过**才交付 |
| 按建筑与环境设计要求制作 | 六步主流程（立项→立数→校核→成图→建模→交付）+ 五条铁律 + 出手前的 6 道门；建筑与环境并重（场地、竖向、雨水、植被、覆土、退线、日照、消防都在校核清单里） |

---

## 8. 交付边界

- 本技能**不覆盖用户已有文件**，同名先备份 `*.bak`；产物统一落 `<project>/out/`。
- 图纸、指标、模型三者同源于 `design.json`，改数据即改全部；**不要在图上直接改**。
- 出图后必须看 PNG（铁律②）。没看过图不算出完。

---

## 9. 发布

- 仓库：https://github.com/jaspercolecole-stack/arch-env-design （MIT，公开）
- 分支 `main` 即交付版本；`verify_skill.py` 在仓库根可直接对自己跑：`<python> verify_skill.py --skill . --work _verify`
- 安装：`.\install_skill.cmd -Scope User`（或 `-Scope Workspace -WorkspaceRoot "<工作区>"`）
- 远程复验：新克隆的仓库跑 18/18 PASS，从克隆安装出的技能目录亦 18/18 PASS。

