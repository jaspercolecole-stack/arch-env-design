# 自检清单契约（checklist）

本文件与 `scripts/check/checklist.py` 是**同一份规则的两种表示**：本文件写"为什么查、查什么字段"，脚本写"怎么算"。两处的 rule id 必须逐字一致；改规则时两边同时改。

下面的 `python` 是占位符，按 `SKILL.md`「运行环境」探测出的解释器替换（Windows 上裸 `python` 常是 Store 占位符，退出码 9009）。

```bash
python scripts/check/checklist.py <design.json> --stage 方案        # 人读报告
python scripts/check/checklist.py <design.json> --stage 深化 --json # 机器读
python scripts/check/checklist.py --list-rules                      # 规则总表
```

## 1. 四种状态与退出码

| 状态 | 含义 | 你要做什么 |
|---|---|---|
| PASS | 有实测值、有限值、判据成立 | 记录在案，进入下一步 |
| WARN | 判据勉强成立（余量不足或让量过大） | 允许推进，但必须写进决策记录并说明为什么接受 |
| FAIL | 判据不成立 | **停**：不许继续深化，先改设计或改口径，再重跑 |
| UNVERIFIED | 缺字段算不出来 | 不许当成通过；补字段重跑，或显式记入"未决" |

退出码：存在 FAIL 时为 1，否则 0。UNVERIFIED 不改变退出码——它是"没算"，不是"通过"。

**UNVERIFIED 的纪律**：一张自检表上 UNVERIFIED 超过一半，等于没做自检。此时不要宣称方案合规。

## 2. 指标字段契约（最容易对接错的地方）

实际值由 `scripts/core/design_io.py` **真算**，上限值由你在 design.json 里**填**（来自控规/规划条件）。两处必须分开，不许互相顶替。

| 规则用到的键 | 来源 | 说明 |
|---|---|---|
| `far` | core 实测 | 容积率 = 计容建筑面积 / 用地面积 |
| `far_limit` | design.json `limits.far` | 控规给定上限 |
| `density` | core 实测 | 建筑密度，百分数 |
| `density_limit` | design.json `limits.density` | 上限；填 0.40 或 40 都可，脚本会归一成 40 |
| `green` | core 实测 | 绿地率，百分数（core 里叫 `greenRatio`，别名映射见脚本 `GREEN_ALIASES`） |
| `green_min` | design.json `limits.green` | 下限 |
| `parking` | core 实测 | 机动车位数（core 返回 `{car,bike}`，取 `car`） |
| `parking_required` | design.json `limits.parking` | 配建要求 |

```json
"limits": { "far": 2.0, "density": 0.40, "green": 0.35, "parking": 48 }
```

只写 `limits` 不写 `metrics` 是正确用法：实测值必须由 core 算出来，你手写的 `metrics` 只作参考、会被 core 覆盖。

判据：FAR ≤ 上限且余量 ≥3%（占上限 >97% → WARN；<80% → WARN 提示确认是否主动让量）；密度同理；绿地率 ≥ 下限（不足下限 +3% → WARN）；车位 ≥ 要求值。

## 3. 23 条规则与字段契约

概念阶段跑前 8 条，方案阶段跑前 15 条 + 审美 4 条，深化阶段跑全部。

| rule id | 阶段 | 判据 | design.json 必需字段 |
|---|---|---|---|
| PLAN-METRIC-01 | 概念/方案/深化 | FAR/DENSITY/GREEN/PARK 四项都有值 | 见上表 |
| PLAN-FAR-01 | 概念/方案/深化 | 容积率不超上限且留余量 | `limits.far` |
| PLAN-DENSITY-01 | 概念/方案/深化 | 建筑密度不超上限 | `limits.density` |
| PLAN-GREEN-01 | 概念/方案/深化 | 绿地率不低于下限 | `limits.green` |
| PLAN-PARK-01 | 概念/方案/深化 | 停车配建满足 | `limits.parking` |
| PLAN-SITE-01 | 概念/方案/深化 | 场地限制条件逐条有处置措施 | `site.constraints[]`：`{id,type,handled,response/measure}` |
| PLAN-PROG-01 | 概念/方案/深化 | 决策三分（已定/未定/假设）且未定项有负责人 | `decisions.{confirmed,pending,assumed}[]`，每条 `{id,question}` |
| PLAN-CASE-01 | 概念/方案/深化 | 先例 ≤3 个，每个写明借哪一层、改写了什么 | `references[]`：`{case_id,layer/borrowed_layer,rewritten/adaptation}` |
| PLAN-XLAY-01 | 方案/深化 | 有 1:500 建筑-景观接口图，含叠图要素 | `interface_map.{scale,overlays[]}` 或 `sheets` 里有 1:500 接口图 |
| PLAN-DRAIN-01 | 方案/深化 | 每条雨水路径闭合到出口且无反坡 | `drainage[]`：`{id,slopes_pct,outfall,kind,reverse_slope}` |
| PLAN-SECT-01 | 方案/深化 | 主要公共空间被剖面切到 | `spaces[]{id,name} + sections[]{id,cuts[]}` |
| PLAN-FIRE-01 | 方案/深化 | 消防车道闭合、有回车场、登高面净空、车道宽与坡度合规 | `fire{loop_closed,turnaround,high_side_clear,lane_width_m,slope_pct}` |
| PLAN-ACCESS-01 | 方案/深化 | 每个出入口有连续无障碍路径 | `entrances[]{id,accessible,step_mm,ramp_slope}` |
| PLAN-BASE-01 | 方案/深化 | 地下室轮廓不越红线与退线 | `basement{outline_inside_redline,min_offset_mm,required_offset_mm}` |
| PLAN-TREE-01 | 方案/深化 | 乔木分级与覆土厚度匹配（屋顶植树更严） | `trees[]{id,class,size,soil_depth_mm/soil_min_mm,on_roof/roof}` |
| DET-WIN-01 | 深化 | 同一立面窗宽是基准模数的整数倍 | `facade.module_mm`（缺省 300）、`facade.openings[]{width_mm}` |
| DET-WIN-02 | 深化 | 立面窗洞与平面窗洞、梁位一致 | `facade.openings[]{width_mm,beam_aligned}` |
| DET-NODE-01 | 深化 | 每种外露材料都有节点做法与缝宽 | `node_materials[]{name,exposed,node_ref,joint_mm}`（也接受 `materials` 里带同样字段的条目，list 或 dict 均可） |
| DET-CLEAR-01 | 深化 | 关键空间净高 ≥ 限值且有余量 | `clearances[]{space,clear_mm} + clearance_limits{space:min_mm}` |
| AEST-MOD-01 | 方案/深化 | 抽 10 个主要尺寸，多数能被基准模数整除 | `dims_mm[]`（主要开间/进深/层高/分格） |
| AEST-MAT-01 | 方案/深化 | 立面主材 ≤3 种且前三占比够 | `facade_materials[]{name,share}` |
| AEST-LIGHT-01 | 方案/深化 | 3 日期 × 3 时刻共 9 组光影校核齐备 | `light_checks[]{date,time,sun_altitude_deg}` |
| AEST-COLOR-01 | 方案/深化 | 色相数、主色面积占比、明度跨度受控 | `palette{hues,hue_count,main_share,high_sat_share,lightness_range}` |

### DET-NODE-01 读的是 node_materials，不是材料色

`materials` 在 schema 里是 **对象**（名称 → `{color,roughness,metalness}`），供出图取色与 GLB 材质；它不含节点字段，所以拿它判节点必然判不出来。节点信息请另起一个数组：

```json
"node_materials": [
  {"name": "石材干挂", "node_ref": "N-01", "joint_mm": 8, "exposed": true},
  {"name": "清水混凝土", "node_ref": "N-02", "joint_mm": 15}
]
```

判定：任一条目（`exposed` 不为 false）缺 `node_ref` → FAIL；`joint_mm < 8` → WARN；全库无任何节点字段 → UNVERIFIED，`actual` 会直说「materials 只声明颜色/粗糙度」。`node_ref` 必须指向真实存在的节点详图编号——本技能图纸集目前只到 1:100/1:200 的平面、总平、剖面，**不出 1:20/1:10 节点详图**，所以示例工程在这里恒为 UNVERIFIED 是诚实结果，不是漏做；要让它 PASS，得先真的画出节点图。

阈值细节以 `scripts/check/checklist.py` 的实现为准；本表只保证字段名与判据方向。字段名写错会让规则回落成 UNVERIFIED，而 UNVERIFIED 看起来很像"没问题"——这是这套工具最危险的失败模式。

## 4. 出现 FAIL 时的处置顺序

1. **先确认口径**：是不是面积口径、绿地计入规则、车位折算系数用错了。口径错导致的 FAIL，改口径不算造假，但必须在决策记录里写明依据。
2. **再改设计**：砍计容面积 / 缩小基底 / 补绿地 / 加车位。改哪一项取决于哪条指标卡住，也取决于形态代价——先算一次代价再动手。
3. **最后改条件**：确实做不到时，把"用地条件不足"作为结论写出来（红线、限高、退线、容积率之间的冲突），附计算过程，交给甲方或规划部门，而不是硬凑一个数。

三档的先后不能颠倒：先怀疑自己的算法，再怀疑自己的设计，最后才怀疑条件。

## 5. 与其它文件的关系

- 指标怎么算：`scripts/core/design_io.py`（唯一算指标的入口，不要另写一套）
- 规范条文依据：`references/codes-cn.md`（中国）、`references/codes-intl.md`（国际）
- 图面是否合规：`references/drawing-standards.md`
- 审美判据的理论背景：`references/aesthetics.md`
- 流程节点与交付物：`references/workflow.md`

## 6. 自检的诚实边界

本清单检查的是**可计算、可列举**的部分：指标、字段齐备性、几何关系、模数整除、材料占比。它不检查：

- 方案是否成立（功能组织、流线、空间质量）
- 形态是否好看（那是 `references/aesthetics.md` 的评审表，判据是人的判断）
- 规范条文是否适用（只到"名称 + 指标 + 限值"层，条文号的适用性由注册建筑师确认）

所以：全部 PASS 不等于方案对。自检表的作用是**把低级错误挡在汇报之前**，不是替你背书。
