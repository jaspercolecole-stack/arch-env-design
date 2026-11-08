# 设计汇报册 · Univer → PDF / PPTX 产出链路操作手册

面向「被技能调用、要把一份方案设计汇报册做成可交付文件」的 AI 执行者。
本手册出现的每个工具名、参数名、返回字段与数值都来自本仓库实测（原始记录见 §11）。
看到本手册没写的参数名，先用 univer_api 的 find / show 查证，禁止按印象拼参数。

## 0 铁律

1. **工具返回成功不等于做对了。** 每页必须过三道：univer_lint 无 findings、univer_screenshot 出图、read_image 亲自看图。
2. **不许用占位方块、假数据、编造指标凑版面。** 缺图就按 §9 生成；生成不了就在该页如实标注缺什么。
3. **页面尺寸、字号、路径一律用本手册数值**，不要自创第二套。
4. **只写 .univer 与其产物文件。** 不改仓库里其它文件、不动 scripts 目录。

## 1 产物路径约定

以项目根 <project> 为基准，产物只落这几处：

| 产物 | 路径 | 说明 |
| --- | --- | --- |
| Univer 主文件 | <project>/report.deck.univer | 唯一可编辑源，先建它 |
| 每页 SVG 源 | <project>/svg/page-01.svg … page-12.svg | 一页一个文件，便于逐页重编与 diff |
| 页面 PNG | <project>/png/<页面批次>/page-01.png … | 目视校验证据，见 §1.1 |
| 位图 PDF | <project>/out/report.deck.pdf | 投屏、预览（内容是位图，见 §3.3） |
| 文本 PDF | <project>/out/report.text.pdf | 打印、检索、可复制文字（走 §9 兜底后端） |
| PPTX | <project>/out/report.deck.pptx | 可编辑交付，文本真实 |
| 图纸图源 | <project>/fig/*.png | 供 image 元素引用 |

### 1.1 三条路径规则

- 调工具一律传**绝对路径**，不依赖进程当前目录。
- univer_screenshot 的 output 目录**按页面批次独立**：同一 output 目录下不同 .univer 的截图都叫 page-01.png，会互相覆盖（实测事故，见 §11.3）。汇报册用 <project>/png/deck/，抽检用 <project>/png/check/。
- image 元素的 href **相对 SVG 源文件所在目录**解析（实测通过）。SVG 放 <project>/svg/，图源放 <project>/fig/，就写 href="../fig/site-plan.png"。

## 2 实测工具链（照这个顺序，不要改）

| # | 工具 | 参数 | 必须校验的返回字段 |
| --- | --- | --- | --- |
| 1 | univer_new | file | created === true |
| 2 | univer_worktree | file, action:"create", name | worktreeId 非空，status === "draft" |
| 3 | univer_unit | file, worktreeId, action:"create", kind:"slide", name | unitId 非空，kind === "slide"，headRev === 1 |
| 4 | univer_status | file, worktreeId | selectedWorktree.units[] 里能查到该 unitId（后续所有调用都用它） |
| 5 | univer_compile_svg | file, worktreeId, unitId, source, page, mode:"replace" | warnings 与 lints 均空，execution.committed === true，revision 递增 |
| 6 | univer_inspect | file, worktreeId, unitId | slides.length === 页数；每页 elementCounts 合理；textPreview 含该页首个标题 |
| 7 | univer_lint | file, worktreeId, unitId, pages | findings.length === 0 |
| 8 | univer_screenshot | file, worktreeId, unitId, output, pages, scale | images[].path 真实存在，width/height = 960×scale / 540×scale |
| 9 | read_image | file_path（上一步的 png） | 亲自看图：中文非豆腐块、元素不出血、不重叠 |
| 10 | univer_print_pdf | file, worktreeId, unitId, output | pageCount === 页数 |
| 11 | univer_export | file, worktreeId, unitId, output | outputPath 非空，产物是 .pptx 且 PK 头（§8.3） |
| 12 | univer_worktree | file, action:"ready", worktreeId | ok === true，status === "ready" |

要点：
- **page 从 1 开始**（工具页码一基）；univer_inspect 返回的 slides[].index 是**零基**，page:1 对应 index:0。
- 新建的 Slide Unit 自带 1 张空页。第 1 页直接 replace，第 2 页起 compile 会自动追加页面。
- 改页面**永远用 mode:"replace"**。用 mode:"add" 会把新内容叠在旧内容上，旧元素留在页里，越改越脏。
- 每 compile 一次 revision 加一。用 revision 递增 + committed === true 判定「这次写入真的落盘了」。
- 全部页完成后才做 6–11；**不要编译一页就导出一次**，导出会打断 revision 追踪。

### 2.1 一次完整跑通的调用序列（字段名均为实测）

~~~
univer_new          { file: <project>/report.deck.univer }                            -> created: true
univer_worktree     { file, action:"create", name:"deck-v1" }                         -> worktreeId: wt-xxxx
univer_unit         { file, worktreeId, action:"create", kind:"slide", name:"汇报册" } -> unitId: u-xxxx
univer_status       { file, worktreeId }                                              -> units[0].unitId === unitId
univer_compile_svg  { file, worktreeId, unitId, source:".../svg/page-01.svg", page:1, mode:"replace" }
  ... 逐页，page:2 … page:12
univer_inspect      { file, worktreeId, unitId }                                      -> slides.length === 12
univer_lint         { file, worktreeId, unitId, pages:[1,...,12] }                    -> findings: []
univer_screenshot   { file, worktreeId, unitId, output:"<project>/png/deck", pages:[1,...,12], scale:2 }
read_image          { file_path:"<project>/png/deck/page-01.png" } ... 逐页看
univer_print_pdf    { file, worktreeId, unitId, output:"<project>/out/report.deck.pdf" }
univer_export       { file, worktreeId, unitId, output:"<project>/out/report.deck.pptx" }
univer_worktree     { file, action:"ready", worktreeId }                              -> status: "ready"
~~~

## 3 页面几何与字号换算

### 3.1 物理尺寸（实测）

| 载体 | 页尺寸 | 换算 |
| --- | --- | --- |
| Univer Slide 页 | 960 × 540 px，原点左上，y 向下 | 1 px = 1 SVG 用户单位 |
| univer_export → PPTX | 12192000 × 6858000 EMU = 13.333 × 7.5 in = 960 × 540 pt | **1 SVG px = 1 pt**（实测 font-size 53.3 → sz 5329 = 53.29 pt） |
| univer_print_pdf → PDF | 720 × 404.88 pt = 10 × 5.623 in = 254 × 142.9 mm | **1 SVG px = 0.75 pt** |

推论：同一份 SVG 在 PPTX 里字号数值不变，在 PDF 里缩小 25%。想按 mm 定字高时用 1 px = 0.2646 mm。
需要 A4 横版（842 × 595 pt）印刷件时，不要指望上面两条导出，按 §9 走文本 PDF 后端另排。

表格下的一行说明：上表三个载体对应同一份 SVG 源，改源文件即可，不要为不同载体维护两套坐标。

### 3.2 字号表（下限是硬约束）

| 用途 | SVG font-size (px) | PPTX pt | 位图 PDF pt | 限制 |
| --- | --- | --- | --- | --- |
| 封面主标题 | 52–60 | 同值 | ×0.75 | ≤ 2 行，每行 ≤ 10 字 |
| 页标题 | 34–38 | 同值 | ×0.75 | 1 行，≤ 14 字 |
| 页副标题 / 导语 | 19–22 | 同值 | ×0.75 | ≤ 1 行 |
| 正文 / 要点 | 15–17 | 同值 | ×0.75 | 行距 1.6 × 字号 |
| 表格正文 | 14–15 | 同值 | ×0.75 | 表格下限 14 |
| 图注 / 数据来源 | 12–13 | 同值 | ×0.75 | 1 行 |
| 页脚 / 页码 | 11–12 | 同值 | ×0.75 | **全书绝对下限 11，任何文字不得小于 11** |

字号 < 14 的文字只允许出现在图注、页脚、单位与法条引用上；正文与表格内容一律 ≥ 14。

## 4 版式模板（先定坐标，再填内容）

共用页边距：左右 56，标题基线 68，副标题基线 88，页脚基线 508。
版心 = x 56…904（宽 848）× y 96…492（高 396）。

| 模板 | 用途 | 图框 | 文字区 | 图占版心 |
| --- | --- | --- | --- | --- |
| T1 封面 | 第 1 页 | 右侧 x 620…904，y 150…390 | 左 x 56…580：主标题基线 250 / 300，副标题 348，横线 372，信息块 412…472 | 约 20% |
| T2 图主 | 需要一整幅图说话的页 | x 56…904，y 96…420（848×324） | 图注 y 440；结论句 y 474 | 82% |
| T3 图左文右 | 图 + 3 条要点 | x 56…616，y 96…411（560×315） | 右栏 x 648…904（宽 256）：栏标题基线 120，正文 y 从 150 起、行距 24 | 52% |
| T4 表主 | 指标表、造价表、议题清单 | 无图（表格直接画） | 表 x 56…904，表头行高 34，数据行高 32；表下结论 y = 表底 + 34 | — |

T2 三联变体（平立剖页）：三张图各 272 × 180，x 分别 56 / 344 / 632，y 96…276；图注 y 296；结论 y 340。

留白硬指标：图框与文字栏间距 ≥ 24；正文末行到页脚基线留 ≥ 24；除 T4 外每页至少留一处 ≥ 60px 的连续空白，禁止把版心塞满。

## 5 12 页固定页序

页数固定 12，顺序不得调换；确需加页只追加在末尾并在第 12 页之后。

| 页 | 页名 | 一句话信息（标题下的导语） | 图（引用文件） | 文字上限 | 模板 |
| --- | --- | --- | --- | --- | --- |
| 1 | 封面 | 项目名 + 阶段 + 汇报单位 + 日期 + 版本 | ../fig/cover-mass.png（场地体量示意） | 主标题 ≤ 2 行 × 10 字；信息 3 行 | T1 |
| 2 | 场地理解 | 边界、坡向、日照、风、周边关系得出的结论 | ../fig/site-analysis.png | 结论 1 句 ≤ 30 字；要点 3 条各 ≤ 18 字 | T3 |
| 3 | 案例参照 | 只借手法，不搬形式 | ../fig/case-diagram.png | ≤ 3 个案例，每个 ≤ 2 行 × 20 字 | T3 |
| 4 | 概念生成 | 从场地问题到形体策略的三步推演 | ../fig/concept-diagram.png | 3 步各 ≤ 10 字；结论 1 句 ≤ 28 字 | T2 |
| 5 | 总平面 | 总平 + 出入口、消防车道、停车与无障碍 | ../fig/site-plan.png | 图注 1 行；要点 3 条各 ≤ 16 字 | T2 |
| 6 | 平立剖 | 首层平面 / 南立面 / A-A 剖面 | ../fig/plan-1f.png、../fig/elevation-south.png、../fig/section-aa.png | 每图注 ≤ 12 字；结论 1 句 | T2 三联 |
| 7 | 指标校核表 | 指标值 + 是否合规 + 校核依据 | 无图，SVG 直接画表 | 表 ≤ 10 行 × 4 列；注 ≤ 2 行 | T4 |
| 8 | 景观结构 | 景观骨架、节点、视线通廊 | ../fig/landscape-structure.png | 要点 3 条各 ≤ 18 字 | T3 |
| 9 | 竖向与雨水 | 关键标高、汇水分区、海绵设施布置 | ../fig/grading-rain.png | 要点 3 条各 ≤ 18 字；数据 1 行 | T3 |
| 10 | 材料与色彩 | 材料样板 + 主色板 + 用量分配 | ../fig/material-palette.png | ≤ 6 项，每项 ≤ 8 字 | T3 |
| 11 | 分期与造价 | 分期甘特 + 单方造价区间 | ../fig/phasing-gantt.png + SVG 价表 | 表 ≤ 6 行 × 3 列 | T4 |
| 12 | 下一步与缺口 | 待决事项、责任人、影响范围 | 无图（纯版式） | ≤ 6 条，每条 ≤ 24 字 | T4 |

每页必有：页标题（第 1 页除外）、页码（右下，格式 页码/12）、数据来源行（有数据的页）。
每页限一个论点：导语只写一句，不写第二句。

## 6 逐页要点

1 **封面**：主标题写项目名，副标题写「方案设计 · 汇报册」；信息块三行是汇报单位 / 汇报日期 / 图册版本与阶段。右侧图框内放体量示意，不放照片。不写页码。
2 **场地理解**：图必须含指北针与比例；结论句写「因此我们把主入口放在……」，不要复述图面已有信息。
3 **案例参照**：每个案例三行——项目名 / 借的手法（一句）/ 我们不用它的什么。标题下导语必须出现「只借手法」字样，防止被读成抄袭。
4 **概念生成**：三步用三个等宽方块串起来，箭头在同一水平线上；每步一个动词开头短语。
5 **总平面**：图占满 T2 图框；出入口用箭头 + 文字标注，消防车道用虚线描出；结论句给用地面积与容积率两个数，数值必须与第 7 页表格一致。
6 **平立剖**：三图同比例、同底标高对齐；图注只写「首层平面 1:200」这类标签，不写描述句子。
7 **指标校核表**：四列固定为 指标名称 / 数值 / 限值或依据 / 判定；判定只允许「合规 / 不达标 / 待核」三种词；表下注必须给规范号。
8 **景观结构**：图上用三类线宽区分骨架 / 节点 / 视线；要点三条各对应一个设计动作。
9 **竖向与雨水**：至少一个整场地汇水分区；数据行给设计暴雨重现期与调蓄容积。
10 **材料与色彩**：色块必须给十六进制与材料名；每项后面标应用部位。
11 **分期与造价**：甘特用条形长度表达工期，不写日期文字；价表给区间不给单点，并写明「估算，未计价差」。
12 **下一步与缺口**：每条写「事项 / 谁定 / 影响哪一页」，禁止出现「持续优化」这类不可判定表述。

## 7 中文排版硬约束

### 7.1 字体

- 每处 text 都写 font-family="SimHei, sans-serif"。缺省会回落到无中文字形的字体，出现豆腐块。
- 只用 TTF 单字重字体。禁止引用 msyh.ttc / simsun.ttc（TTC 集合，后端解析不了）。
- 不写 font-weight（SimHei 只有一个字重，粗细靠字号与颜色拉开层次）。

### 7.2 行距与行宽

- 行距（相邻 tspan 的 y 差）= round(字号 × 1.6)（正文）、× 1.35（表格）、× 1.15（标题）。
- 中文全角字宽约等于字号。单行字数上限 = floor(栏宽 / (字号 × 1.02))，按栏宽 256、字号 15 算 = 16 字。
- 一行只放一个语义单元，宁可多一行，不许压字号。

### 7.3 断行（禁止拆词）

- 换行一律用多个 tspan，每个给同一个绝对 x 与各自的绝对 y；禁止 dx / dy 渐进布局，禁止靠空格对齐。
- 断点只允许在：标点之后、动宾之间、并列短语之间。**禁止把一个词拆到两行**——「场地理解」不能成「场地理 / 解」，「指标校核」不能成「指标校 / 核」。
- 避头尾：行首不得出现 ，。、；：）」』】% ；行尾不得出现 （「『【。
- 数字、英文、单位与中文之间不手动加空格；需要固定间隔时用 &#160;。

### 7.4 字符与数值

- 连续空格会被折叠成 1 个（实测 lint 原文见 §11.1）。需要视觉间隔时用 &#160;，不用空格串。
- 面积单位只写 m2 或 ㎡，**不写 m²**：SimHei 的 cmap 里 U+00B2 映射到 gid 0（实测），走兜底 PDF 后端会缺字。
- 小数统一两位；百分比写「17.4%」不写「百分之十七点四」。
- 所有文本文件 UTF-8 无 BOM。

### 7.5 禁用写法

未实测的写法一律不用：foreignObject 元素、style 内联 CSS、textLength、外部 URL 资源、动画标签。
只用这三类元素：矩形 / 线 / 折线 / 圆 / 路径 / 文本，以及 image 元素。

## 8 校验清单（每条都可判定）

| 检查 | 判据 | 不过怎么办 |
| --- | --- | --- |
| 页数 | univer_inspect 的 slides.length === 12 | 重编缺页；漏页不许多导一页凑数 |
| 版式 lint | univer_lint 的 findings.length === 0 | 按 findings 的 page + message 改 SVG，重编该页 |
| 溢出（人工） | 每页 PNG 上无文字压边、无元素出血 | lint 只查出血/越界/重叠三类，**查不出视觉平衡**（实测漏检：北字压虚线框、比例注压图框右边缘），必须人工看图 |
| 中文可读 | PNG 上中文笔画完整，无方块、无缺字 | 检查 font-family 是否写了 SimHei |
| 字号 | 全文最小 font-size ≥ 11，正文 ≥ 14 | 提字号或删字，不许压字号 |
| 图存在 | 每页引用的 fig 文件真实存在且宽高比与图框一致 | 缺图按 §9 生成，不许留空框 |
| PDF | pageCount === 12；文件头 %PDF-；页对象数 − 页树数 = 12 | 见 §8.1 |
| PPTX | 文件头 PK；zip 校验通过；slide 数与页数一致 | 见 §8.3 |

### 8.1 PDF 校验命令（PowerShell + python）

字节数与文件头：

~~~
$p = '<绝对路径>.pdf'
$b = [IO.File]::ReadAllBytes($p); $b.Length; ($b[0..4] -join ' ')
~~~

期望：字节数 > 0，头 5 字节是 25 50 44 46 2D（%PDF-）。
页对象计数必须**减掉页树**，否则永远多 1 个——'/Type /Page' 是 '/Type /Pages' 的前缀子串（实测：2 页数出 3）：

~~~
d = open(p, 'rb').read()
pages = d.count(b'/Type /Page') - d.count(b'/Type /Pages')
pages, d.rstrip().endswith(b'%%EOF')
~~~

期望：pages === 12，末尾有 %%EOF。

### 8.2 判定 print_pdf 产物的性质

导出后用上面的 python 再数一遍：/Font /BaseFont /FontFile2 /BT /Tj 的出现次数。
全为 0 说明这份 PDF 每页是一整张位图（实测就是如此，见 §11.4）：可以投屏，**不可检索、不可选中文字、不可印刷**。
需要真文字时走 §9，不要在这里反复重导。

### 8.3 PPTX 校验

~~~
import zipfile
z = zipfile.ZipFile(p)
z.testzip()                       # 期望 None
[n for n in z.namelist() if n.startswith('ppt/slides/slide')]
~~~

再读 ppt/presentation.xml 的 sldSz 确认 12192000 × 6858000，读 slide1.xml 确认有 a:ea typeface="SimHei" 与真实中文 run。
PPTX 是唯一「文字真实、客户可编辑」的交付格式。

## 9 失败降级路径

按失败面分档，**先判定档位再动手**，不许跳档。

### D1 只有渲染类工具不可用（截图失败，其余正常）

- 继续 compile / lint / print_pdf / export，链路照走。
- 视觉证据缺失时，交付说明里必须写「未做目视校验」，并把 PNG 缺失页列出来。
- 禁止在没看图的情况下宣布「页面正常」。

### D2 Univer 整体不可用（工具报错或 .univer 损坏）

改走 skill/scripts/core 的纯 Python PDF 后端手工排版：

1. 先确认后端在不在：Test-Path <skill>/scripts/core/to_pdf.py。
2. 存在：先跑 python <skill>/scripts/core/to_pdf.py --help 读出它真实的参数与输入格式，再按读到的参数调用。**禁止按猜测拼参数**，本手册不为该脚本背书任何参数名。
3. 现状核对（2026-10 更新）：`scripts/core/to_pdf.py` 已经存在，但它渲染的是 **A1 图纸**（`svg_draw` 组装的 Scene → 纯 Python PDF，文字走轮廓、文件里没有 /Font），**不是汇报册页面**。汇报册页面仍走两条路：`univer_print_pdf`（位图，不可搜文字）或下面的 D3 兜底后端（真文字）。不要把 A1 图纸的 to_pdf.py 当成汇报册后端来接。

### D3 手写最小 PDF 后端（已实测可用）

以 skill/examples/_probe_deck/pv_fallback_probe.py 为骨架（只留脚本与 SVG 源；探针产出的 PDF/PPTX/.univer 已从交付包删除，证据以本手册记录的数字为准），它已验证能产出：

- 5,051,271 字节的单页 PDF；头 %PDF-、尾 %%EOF；
- SimHei 以 Type0 / CIDFontType2 / Identity-H 嵌入，含 FontFile2 原始 TTF 与 ToUnicode；
- 内容流里是 BT/Tj 真文字（不是位图）；
- 10/10 条 xref 偏移都指向正确对象。

调用方式：

~~~
& '<python>' '<skill>\examples\_probe_deck\pv_fallback_probe.py' '<project>\out\report.text.pdf'
~~~

扩展成 12 页与图片时的硬要求（骨架目前只做单页纯文字，扩展后必须按 §8 校验）：

- 每页一个 /Type /Page 对象 + 一条独立的 Contents 流；/Pages 的 /Kids 与 /Count 同步改。
- 图片用 /XObject /Subtype /Image + /FlateDecode，页 /Resources 的 /XObject 里登记名字，正文用 /Name Do。
- xref 表必须按对象序号逐条写真实字节偏移，不许写 0 糊弄；写完回读校验：startxref 指向 xref，逐条偏移处的对象号与表项一致。
- 中文全部经 ToUnicode 映射，缺字先查 SimHei cmap（U+00B2 缺失），把 m² 换成 m2 或 ㎡。
- 页尺寸自定：A4 横版写 842 595，与汇报册同比例写 720 404.88。

### D4 想直接出图但没有渲染器

本沙箱没有 Edge headless / LibreOffice / Ghostscript / poppler（环境事实，已确认），所以 PDF 不能再转图。
需要位图证据时只能用 univer_screenshot；这条不可用时按 D1 处理，不要试图装外部程序。

## 10 常见错误与处置

| 现象 | 原因 | 处置 |
| --- | --- | --- |
| compile 返回 lints 里出现「N run(s) of consecutive spaces collapse」 | 文本里有连续空格 | 换成 &#160;，重编该页 |
| 中文显示成方框 | font-family 没写 SimHei，或写了 TTC 字体名 | 补 font-family="SimHei, sans-serif" |
| lint 全过但看着别扭 | lint 只查出血/越界/重叠 | 人工看图，挪坐标后重编 |
| 同一 output 目录两次截图后图不对 | 不同 .univer 的截图都叫 page-01.png | 每批次独立输出子目录 |
| 页数比预期多一页 | 新建 Unit 自带一张空页 | 从 page:1 开始 replace，不要先 add |
| 改完一页旧元素还在 | 用了 mode:"add" | 一律 mode:"replace" |
| PDF 里数出 13 个 /Type /Page | 把 /Type /Pages 也算进去了 | 相减 |
| PDF 里搜不到文字 | print_pdf 出的是位图 | 要文本走 §9 D2/D3 |
| 导出的 PPTX 打开报错 | 导出未完成就中断 | 重导，zip.testzip() 必须为 None |
| 引用的图不显示 | href 写成了相对仓库根或绝对路径 | href 相对 SVG 源文件目录写 |

## 11 实测基线（本仓库的原始数字）

### 11.1 探针文件

| 项 | 值 |
| --- | --- |
| .univer | 探针包已删除（原 probe.univer 782,336 B / probe-img.univer 630,784 B）；保留 page-01.svg / page-02.svg / page-img.svg 三个 SVG 源 |
| worktreeId | wt-mvl1ev89-xgkb48（name: probe-deck，最终 status ready，headRevision 6） |
| unitId | u-mvl1ewne-kshwx2（kind slide，type 3） |
| 页数 | 2（封面 + 总平面与指标校核） |

compile 首次返回的 lint 原文（逐字）：

> <text>: 2 run(s) of consecutive spaces collapse to a single space under SVG default whitespace handling (the browser preview collapses them too), so indentation or alignment built from spaces is lost. Use xml:space="preserve", non-breaking spaces (&#160;), or split into positioned elements.

改掉双空格后 warnings [] lints [] 通过。

### 11.2 lint / inspect

univer_lint 返回 kind "unit-layout-lint"，rules 三条：text-off-page、text-escapes-container、text-overlaps-text；两页 findings 均为 []。
univer_inspect 返回 size 960×540、masterSlideCount 1、layoutSlideCount 8；第 1 页 elementCounts.total 17、第 2 页 56。

### 11.3 截图

pages [1,2]、scale 2 → 1920×1080，分别 279,334 B 与 325,646 B。
坑：另建 probe-img.univer 做图片引用试验时也用同一个 png 目录，把 page-01.png 覆盖了一次——output 目录必须按批次隔离，这就是 §1.1 那条规则的来源。

### 11.4 PDF 与 PPTX 的真实性质

| 项 | probe.pdf | probe.pptx |
| --- | --- | --- |
| 字节数 | 78,168 | 18,923 |
| 文件头 | 25 50 44 46 2D（%PDF-） | 50 4B 03 04（PK） |
| 结构 | 25 个对象，页面 MediaBox 720 × 404.88 pt | zip 校验 None，slide1/slide2 |
| 文本 | /Font 0、/BaseFont 0、/FontFile2 0、BT 0、Tj 0；内容流只有白底 + /X8 Do | a:latin 与 a:ea 均为 SimHei，真实中文 run |
| 结论 | 每页被栅格化成 960×540 位图，约 96 dpi | 文本可编辑，字号 1:1 保留 |

PDF 的 Producer 是 Skia/PDF m155，Creator 带浏览器 UA —— 说明 print_pdf 走的是浏览器渲染管线，这也解释了为什么它没有文字对象。

### 11.5 兜底 PDF 基线

pv_fallback.pdf：5,051,271 B；/BaseFont /SimHei ×2、/FontFile2 ×1、/ToUnicode ×1、/CIDToGIDMap /Identity ×1；FontFile2 解压 9,745,792 B（sfnt 00010000，20 张表）；内容流 681 B，5 个 BT / 5 个 Tj。
SimHei cmap（format 4，unitsPerEm 256）实测覆盖：U+00B2 → gid 0（缺）、U+33A1 → 837、U+00B7 → 103、U+2014 → 258、U+FF0F → 955、U+3001 → 573、U+FF08 → 948、U+2033 → 269、U+00B0 → 101。

### 11.6 图片引用

页内写 image 元素，href="png/page-02.png"、x=56、y=96、width=560、height=315，放在 page-img.svg 同目录的 png/ 下即解析成功；compile 无警告，inspect 报 images: 1，截图确认渲染正确。
结论：href 相对 SVG 源文件目录；width / height 必须写。
