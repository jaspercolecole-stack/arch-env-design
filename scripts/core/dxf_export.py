#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dxf_export.py -- design.json -> DXF R12（AC1009，纯 ASCII，单位毫米）。

链路与 svg_backend 完全同构：

    design.json --(draw2d.Scene, 1:1, mm, 原点左上、y 向下)--> DXF ENTITIES

Scene 仍是那一份图元流水，这里只是再换一个渲染器（svg / dxf），所以 DXF 的几何
必然与 SVG/PDF 一致，不存在"导出 DXF 时又画一遍"导致的图面漂移。

坐标系
    Scene 是纸面坐标（mm，y 向下），DXF 模型空间是 y 向上，故写出时统一翻折一次
    Y_dxf = scene.h - y_scene。最终落地的模型空间坐标是：

        X_dxf = (x_design - x0) * 1000 + PAD
        Y_dxf = (y_design - y0) * 1000 + PAD

    PAD 是给尺寸线与图名留的页边距。$INSUNITS = 4（毫米），1:1 模型空间。

为什么是 R12（AC1009）
    它是所有 CAD 软件与 DXF 读写库的最小公分母：纯文本、无对象句柄、无字典、
    无 ACIS，个别实体写得不规范也不会让整张图打不开。代价是 R12 没有这些东西，
    所以本模块对 Scene 图元做了有损映射：

      * LWPOLYLINE -> POLYLINE + VERTEX + SEQEND，闭合靠 70 标志位 bit0；
      * HATCH      -> 不导出面积填充，只导出它的闭合轮廓（对应的 stroke 已经画了）；
                      唯一例外是三点填充转 SOLID，用来画尺寸箭头；
      * 线宽       -> R12 实体没有线宽属性，线宽信息靠图层 + 颜色 + 线型承载；
      * Unicode    -> R12 没有，中文一律转拼音，转不出的字符丢弃并在 stderr 警告。

CLI
    python dxf_export.py <design.json> --out <path.dxf>
"""

from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import design_io
import draw2d

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ==========================================================================
# 图层表：正好这 8 个，名字一个字符都不能差
# ==========================================================================
LAYERS = (
    ("A-SITE", 1, "CONTINUOUS", "Site: red line, setback, roads, plaza, contours"),
    ("A-WALL", 7, "CONTINUOUS", "Walls: building outline, overhang, roof ridge"),
    ("A-GLAZ", 4, "CONTINUOUS", "Glazing: window and door openings"),
    ("A-COLS", 2, "CONTINUOUS", "Columns"),
    ("L-PLNT", 3, "CONTINUOUS", "Planting: trees and lawns"),
    ("L-WATER", 5, "CONTINUOUS", "Water"),
    ("A-ANNO", 6, "CONTINUOUS", "Annotation text"),
    ("A-DIMS", 6, "CONTINUOUS", "Dimensions"),
)

# R12 的 LTYPE：图案长度单位是图纸单位，靠 header 的 $LTSCALE 放大到 1:1 模型空间
LTYPES = (
    ("CONTINUOUS", "Solid line", ()),
    ("DASHED", "Dashed __ __ __ __", (0.5, -0.25)),
)


# ==========================================================================
# 中文字符 -> ASCII（R12 没有 Unicode）
# ==========================================================================
_PINYIN = {
    "青": "Qing", "溪": "Xi", "实": "Shi", "验": "Yan", "学": "Xue", "校": "Xiao",
    "新": "Xin", "区": "Qu", "教": "Jiao", "楼": "Lou", "综": "Zong", "合": "He",
    "体": "Ti", "育": "Yu", "馆": "Guan", "用": "Yong", "地": "Di", "面": "Mian",
    "积": "Ji", "建": "Jian", "筑": "Zhu", "密": "Mi", "度": "Du", "容": "Rong",
    "率": "Lv", "绿": "Lv", "化": "Hua", "水": "Shui", "层": "Ceng", "高": "Gao",
    "标": "Biao", "米": "Mi", "图": "Tu", "总": "Zong", "平": "Ping", "一": "Yi",
    "二": "Er", "三": "San", "四": "Si", "五": "Wu", "六": "Liu", "七": "Qi",
    "八": "Ba", "九": "Jiu", "十": "Shi", "号": "Hao", "南": "Nan", "北": "Bei",
    "东": "Dong", "西": "Xi", "门": "Men", "窗": "Chuang", "墙": "Qiang",
    "柱": "Zhu", "主": "Zhu", "环": "Huan", "路": "Lu", "侧": "Ce", "草": "Cao",
    "坪": "Ping", "林": "Lin", "荫": "Yin", "广": "Guang", "场": "Chang",
    "入": "Ru", "口": "Kou", "映": "Ying", "月": "Yue", "池": "Chi", "坡": "Po",
    "公": "Gong", "园": "Yuan", "间": "Jian", "住": "Zhu", "宅": "Zhai",
    "商": "Shang", "业": "Ye", "街": "Jie", "滨": "Bin", "河": "He", "段": "Duan",
    "期": "Qi", "项": "Xiang", "目": "Mu", "方": "Fang", "案": "An", "设": "She",
    "计": "Ji", "说": "Shuo", "明": "Ming", "连": "Lian", "廊": "Lang",
    "雨": "Yu", "篷": "Peng", "车": "Che", "库": "Ku", "停": "Ting",
}

_DROPPED = set()


def to_ascii(s):
    """非 ASCII 字符转拼音；转不出的丢弃并在 stderr 警告（每个字符只报一次）。"""
    out = []
    for ch in str(s):
        o = ord(ch)
        if 32 <= o < 127:
            out.append(ch)
        elif ch in _PINYIN:
            out.append(_PINYIN[ch])
        else:
            if ch not in _DROPPED:
                _DROPPED.add(ch)
                sys.stderr.write("警告: DXF R12 无 Unicode，字符 %r (U+%04X) 无法转 ASCII，"
                                 "已在文字中丢弃\n" % (ch, o))
            out.append("?")
    return "".join(out).strip()


# ==========================================================================
# Scene（+图层标签）
# ==========================================================================
class CadScene(draw2d.Scene):
    """在 Scene 的三类图元上加一个 layer 键；图元结构本身不改。"""

    DEFAULT_LAYER = "A-SITE"

    def _tag(self, mark, layer):
        if len(self.items) > mark:
            self.items[-1]["layer"] = layer or self.DEFAULT_LAYER

    def fill(self, pts, color, alpha=None, layer=None):
        mark = len(self.items)
        super().fill(pts, color, alpha)
        self._tag(mark, layer)

    def stroke(self, pts, color, w, closed=False, dash=None, cap="butt", join="miter",
               layer=None):
        mark = len(self.items)
        super().stroke(pts, color, w, closed=closed, dash=dash, cap=cap, join=join)
        self._tag(mark, layer)

    def poly(self, pts, fill=None, stroke=None, w=draw2d.LW["secondary"], dash=None,
             cap="butt", join="miter", alpha=None, layer=None):
        mark = len(self.items)
        super().poly(pts, fill=fill, stroke=stroke, w=w, dash=dash, cap=cap, join=join,
                     alpha=alpha)
        self._tag(mark, layer)

    def rect(self, x0, y0, x1, y1, fill=None, stroke=None, w=draw2d.LW["secondary"],
             dash=None, layer=None):
        mark = len(self.items)
        super().rect(x0, y0, x1, y1, fill=fill, stroke=stroke, w=w, dash=dash)
        self._tag(mark, layer)

    def circle(self, cx, cy, r, fill=None, stroke=None, w=draw2d.LW["detail"], seg=48,
               layer=None):
        mark = len(self.items)
        super().circle(cx, cy, r, fill=fill, stroke=stroke, w=w, seg=seg)
        self._tag(mark, layer)

    def line(self, p0, p1, color, w=draw2d.LW["detail"], dash=None, cap="butt",
             layer=None):
        mark = len(self.items)
        super().line(p0, p1, color, w=w, dash=dash, cap=cap)
        self._tag(mark, layer)

    def polyline(self, pts, color, w=draw2d.LW["secondary"], dash=None, cap="butt",
                 join="miter", layer=None):
        mark = len(self.items)
        super().polyline(pts, color, w=w, dash=dash, cap=cap, join=join)
        self._tag(mark, layer)

    def circle_entity(self, cx, cy, r, color, w, layer):
        """真正画一个圆（DXF CIRCLE）。

        Scene 的 circle() 是折线近似的圆，落到 DXF 里会变成闭合 POLYLINE。想在 R12 里
        得到 CIRCLE 实体，就在那条描边上多挂一个 circle=[cx,cy,r] 提示键，让渲染器
        按圆处理。图元的三个几何字段（t/pts 等）一个都没改，只是多一个渲染提示。
        """
        self.circle(cx, cy, r, stroke=color, w=w, seg=36, layer=layer)
        if self.items:
            self.items[-1]["circle"] = [float(cx), float(cy), float(r)]

    def text(self, x, y, s, size=2.5, color=None, anchor="lt", rot=0.0, spacing=0.0,
             layer="A-ANNO"):
        mark = len(self.items)
        super().text(x, y, s, size=size, color=color, anchor=anchor, rot=rot,
                     spacing=spacing)
        self._tag(mark, layer)


# ==========================================================================
# DXF 写出器（手写 R12 组码）
# ==========================================================================
def _f(v):
    s = "%.4f" % float(v)
    if "." in s:
        s = s.rstrip("0")
        if s.endswith("."):
            s += "0"
    if s in ("-0.0", "-0"):
        s = "0.0"
    return s


def _v(value):
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    return _f(value)


class DxfWriter:
    """把图元逐条写进 ENTITIES 的组码流；写完才知道范围，故 header 后置拼装。"""

    def __init__(self, scene_height, ltscale=500.0):
        self.h = float(scene_height)
        self.ltscale = float(ltscale)
        self.body = []
        self.counts = {}
        self.layers = {}
        self.ext = None                     # [xmin, ymin, xmax, ymax]

    # ---- 组码 ----
    def _codes(self, pairs):
        for code, value in pairs:
            self.body.append(str(int(code)))
            self.body.append(_v(value))

    def _head(self, etype, layer, lt=None):
        self.body.extend(("0", etype, "8", layer))
        if lt:
            self.body.extend(("6", lt))
        self.body.extend(("62", "256"))     # BYLAYER
        self.counts[etype] = self.counts.get(etype, 0) + 1
        self.layers[layer] = self.layers.get(layer, 0) + 1

    def tf(self, p):
        """Scene 纸面坐标（y 向下）-> DXF 模型空间（y 向上）。"""
        x, y = float(p[0]), self.h - float(p[1])
        if self.ext is None:
            self.ext = [x, y, x, y]
        else:
            e = self.ext
            e[0], e[1] = min(e[0], x), min(e[1], y)
            e[2], e[3] = max(e[2], x), max(e[3], y)
        return x, y

    # ---- 实体 ----
    def line(self, layer, a, b, lt=None):
        x1, y1 = self.tf(a)
        x2, y2 = self.tf(b)
        self._head("LINE", layer, lt)
        self._codes(((10, x1), (20, y1), (30, 0.0), (11, x2), (21, y2), (31, 0.0)))

    def polyline(self, layer, pts, closed=False, lt=None):
        pts = list(pts)
        if len(pts) < 2:
            return
        self._head("POLYLINE", layer, lt)
        self._codes(((66, 1), (10, 0.0), (20, 0.0), (30, 0.0),
                     (70, 1 if (closed and len(pts) > 2) else 0)))
        for p in pts:
            x, y = self.tf(p)
            self.body.extend(("0", "VERTEX", "8", layer))
            self._codes(((10, x), (20, y), (30, 0.0)))
        self.body.extend(("0", "SEQEND", "8", layer))

    def circle(self, layer, c, r, lt=None):
        x, y = self.tf(c)
        self._head("CIRCLE", layer, lt)
        self._codes(((10, x), (20, y), (30, 0.0), (40, float(r))))

    def text(self, layer, p, height, s, rot=0.0, lt=None):
        x, y = self.tf(p)
        self._head("TEXT", layer, lt)
        self._codes(((10, x), (20, y), (30, 0.0), (40, float(height)), (1, s),
                     (50, -float(rot)), (7, "STANDARD")))

    def solid(self, layer, tri, lt=None):
        if len(tri) != 3:
            return
        p = [self.tf(q) for q in tri]
        p.append(p[2])                      # 第 3、4 点重合 => 三角形
        self._head("SOLID", layer, lt)
        pairs = []
        for i, (x, y) in enumerate(p):
            pairs.append((10 + i, x))
            pairs.append((20 + i, y))
            pairs.append((30 + i, 0.0))
        self._codes(pairs)

    # ---- 拼装 ----
    def header(self):
        x0, y0, x1, y1 = self.ext or [0.0, 0.0, 0.0, 0.0]
        out = ["0", "SECTION", "2", "HEADER"]
        for name, code, value in (
            ("$ACADVER", 1, "AC1009"),
            ("$DWGCODEPAGE", 3, "ANSI_1252"),
            ("$INSUNITS", 70, 4),           # 4 = 毫米
            ("$MEASUREMENT", 70, 1),
            ("$LTSCALE", 40, self.ltscale),
            ("$TEXTSTYLE", 7, "STANDARD"),
            ("$CLAYER", 8, "A-SITE"),
        ):
            out.extend(("9", name, str(code), _v(value)))
        for name, values in (("$EXTMIN", (x0, y0, 0.0)), ("$EXTMAX", (x1, y1, 0.0))):
            out.extend(("9", name, "10", _f(values[0]), "20", _f(values[1]),
                        "30", _f(values[2])))
        out.extend(("0", "ENDSEC"))
        return out

    def tables(self):
        out = ["0", "SECTION", "2", "TABLES"]

        out.extend(("0", "TABLE", "2", "LTYPE", "70", str(len(LTYPES))))
        for name, desc, pattern in LTYPES:
            out.extend(("0", "LTYPE", "2", name, "70", "0", "3", desc,
                        "72", "65", "73", str(len(pattern))))
            out.append("40")
            out.append(_f(sum(abs(x) for x in pattern)))
            for seg in pattern:
                out.append("49")
                out.append(_f(seg))
        out.extend(("0", "ENDTAB"))

        out.extend(("0", "TABLE", "2", "LAYER", "70", str(len(LAYERS))))
        for name, aci, lt, _desc in LAYERS:
            out.extend(("0", "LAYER", "2", name, "70", "0", "62", str(aci), "6", lt))
        out.extend(("0", "ENDTAB"))

        out.extend(("0", "TABLE", "2", "STYLE", "70", "1", "0", "STYLE", "2", "STANDARD",
                    "70", "0", "40", "0.0", "41", "1.0", "50", "0.0", "71", "0",
                    "42", "2.5", "3", "txt", "4", ""))
        out.extend(("0", "ENDTAB"))

        out.extend(("0", "ENDSEC"))
        return out

    def render(self):
        lines = []
        lines.extend(self.header())
        lines.extend(self.tables())
        lines.extend(("0", "SECTION", "2", "ENTITIES"))
        lines.extend(self.body)
        lines.extend(("0", "ENDSEC", "0", "EOF"))
        return "\r\n".join(lines) + "\r\n"


# ==========================================================================
# Scene 图元 -> 实体
# ==========================================================================
def scene_to_writer(sc, ltscale=500.0):
    w = DxfWriter(sc.h, ltscale)
    for it in sc.items:
        t = it.get("t")
        layer = it.get("layer") or CadScene.DEFAULT_LAYER
        lt = "DASHED" if it.get("dash") else None
        if t == "stroke":
            circ = it.get("circle")
            if circ:
                w.circle(layer, (circ[0], circ[1]), circ[2], lt=lt)
                continue
            pts = list(it["pts"])
            if it.get("closed") and len(pts) >= 3:
                w.polyline(layer, pts, closed=True, lt=lt)
            elif len(pts) == 2:
                w.line(layer, pts[0], pts[1], lt=lt)
            else:
                w.polyline(layer, pts, closed=False, lt=lt)
        elif t == "fill":
            pts = list(it["pts"])
            if len(pts) == 3:
                w.solid(layer, pts, lt=lt)   # 三点填充 = 箭头
            # 面积填充：R12 无 HATCH，轮廓由配对的 stroke 承载
        elif t == "text":
            s = to_ascii(it.get("s", ""))
            if s:
                w.text(layer, (it["x"], it["y"]), it["size"], s, it.get("rot") or 0.0)
    return w


# ==========================================================================
# 尺寸线（LINE + SOLID 箭头，全部落在 A-DIMS）
# ==========================================================================
def _arrow(sc, tip, direction, eh, color):
    """三点填充 -> DXF SOLID；tip 是箭头尖，direction 是箭头指向。"""
    L, Wd = 0.8 * eh, 0.22 * eh
    dx, dy = direction
    px, py = -dy, dx
    tail = (tip[0] - dx * L, tip[1] - dy * L)
    sc.fill([tip,
             (tail[0] + px * Wd, tail[1] + py * Wd),
             (tail[0] - px * Wd, tail[1] - py * Wd)], color, layer="A-DIMS")


def _dim_h(sc, xa, xb, y, text, eh, ext=None, color=None):
    col = color or draw2d.C["dash"]
    if ext:
        ya, yb = ext
        sc.line((xa, ya), (xa, y), col, draw2d.LW["hatch"], layer="A-DIMS")
        sc.line((xb, yb), (xb, y), col, draw2d.LW["hatch"], layer="A-DIMS")
    sc.line((xa, y), (xb, y), col, draw2d.LW["detail"], layer="A-DIMS")
    _arrow(sc, (xa, y), (-1.0, 0.0), eh, col)
    _arrow(sc, (xb, y), (1.0, 0.0), eh, col)
    sc.text((xa + xb) / 2.0, y - 0.25 * eh, text, 0.72 * eh, col, "cb", layer="A-DIMS")


def _dim_v(sc, ya, yb, x, text, eh, ext=None, color=None):
    col = color or draw2d.C["dash"]
    if ext:
        xa, xb = ext
        sc.line((xa, ya), (x, ya), col, draw2d.LW["hatch"], layer="A-DIMS")
        sc.line((xb, yb), (x, yb), col, draw2d.LW["hatch"], layer="A-DIMS")
    sc.line((x, ya), (x, yb), col, draw2d.LW["detail"], layer="A-DIMS")
    _arrow(sc, (x, ya), (0.0, -1.0), eh, col)
    _arrow(sc, (x, yb), (0.0, 1.0), eh, col)
    sc.text(x - 0.25 * eh, (ya + yb) / 2.0, text, 0.72 * eh, col, "rb", layer="A-DIMS")


# ==========================================================================
# design.json -> Scene（1:1 mm，总平面）
# ==========================================================================
def build_cad_scene(d):
    C, LW = draw2d.C, draw2d.LW
    lh = design_io.level_height(d)
    dw = d.get("drawings") or {}
    scale = float(dw.get("siteplan_scale", 500) or 500)
    eh = 2.5 * scale                        # 模型空间字高（mm），按 1:scale 出图后是 2.5mm

    bd = design_io.as_pts(d["site"]["boundary"])
    x0, y0, x1, y1 = design_io.bbox(bd)
    span_w, span_h = (x1 - x0) * 1000.0, (y1 - y0) * 1000.0
    pad = max(4000.0, 2.0 * eh, 0.05 * max(span_w, span_h))
    sc = CadScene(span_w + 2.0 * pad, span_h + 2.0 * pad, draw2d.load_font(),
                  {"mode": "siteplan", "scale": scale, "unit": "mm",
                   "entity_height_mm": eh, "pad_mm": pad})
    T = draw2d.ToPaper(1, pad - x0 * 1000.0, pad, x0, y1)
    els = d.get("elements", [])

    # 1 用地红线 / 退线控制线
    sc.stroke([T(p) for p in bd], C["site"], LW["outline"], closed=True, layer="A-SITE")
    sbp = draw2d.setback_polygon(bd, d["site"].get("setbacks") or {})
    if sbp:
        sc.stroke([T(p) for p in sbp], C["dash"], LW["detail"], closed=True,
                  dash=(5, 2), layer="A-SITE")

    # 2 绿地 / 等高线
    for el in els:
        if el.get("kind") == "green":
            sc.poly([T(p) for p in el["polygon"]], C["green_l"], C["tree_e"],
                    LW["hatch"], layer="L-PLNT")
    for el in els:
        if el.get("kind") == "contour":
            sc.stroke([T(p) for p in el["points"]], "#9C9C90", LW["hatch"],
                      dash=(3, 1.4), layer="A-SITE")

    # 3 水体
    for el in els:
        if el.get("kind") == "water":
            sc.poly([T(p) for p in el["polygon"]], C["water"], C["water_e"],
                    LW["secondary"], layer="L-WATER")

    # 4 广场
    for el in els:
        if el.get("kind") == "plaza":
            sc.poly([T(p) for p in el["polygon"]], C["plaza"], C["road_e"],
                    LW["secondary"], layer="A-SITE")

    # 5 道路：两条边线 + 中心虚线
    for el in els:
        if el.get("kind") != "path":
            continue
        half = float(el["width"]) / 2.0
        for dist in (half, -half):
            off = design_io.polyline_offset(el["points"], dist)
            if len(off) >= 2:
                sc.stroke([T(p) for p in off], C["road_e"], LW["detail"], layer="A-SITE")
        sc.stroke([T(p) for p in el["points"]], C["dash"], LW["detail"], dash=(6, 3),
                  layer="A-SITE")

    # 6 建筑：轮廓 + 悬挑投影 + 坡屋脊
    for m in d["masses"]:
        fp = design_io.as_pts(m["footprint"])
        sc.poly([T(p) for p in fp], draw2d.mat_color(d, m.get("material"), C["concrete"]),
                C["cut"], LW["outline"], layer="A-WALL")
        oh = float(m.get("overhang", 0) or 0)
        if oh > 0:
            ring = design_io.offset_polygon(fp, -oh)
            if ring:
                sc.stroke([T(p) for p in ring], C["dash"], LW["detail"], closed=True,
                          dash=(4, 1.6), layer="A-WALL")
        if m.get("roof") == "gable" and float(m.get("roof_rise", 0) or 0) > 0:
            gx0, gy0, gx1, gy1 = design_io.bbox(fp)
            if (gx1 - gx0) >= (gy1 - gy0):
                ym = (gy0 + gy1) / 2.0
                sc.line(T.pt(gx0, ym), T.pt(gx1, ym), C["cut"], LW["secondary"],
                        layer="A-WALL")
            else:
                xm = (gx0 + gx1) / 2.0
                sc.line(T.pt(xm, gy0), T.pt(xm, gy1), C["cut"], LW["secondary"],
                        layer="A-WALL")

    # 7 乔木 / 柱
    for el in els:
        if el.get("kind") == "tree":
            c = T(el["center"])
            r = max(1.1, T.m(float(el.get("radius", 3.0))))
            sc.circle_entity(c[0], c[1], r, C["tree_e"], LW["detail"], "L-PLNT")
            sc.circle_entity(c[0], c[1], max(0.5, r * 0.15), C["tree_e"], LW["detail"],
                             "L-PLNT")
    for el in els:
        if el.get("kind") == "column":
            c = T(el["center"])
            r = float(el.get("radius", el.get("size", 0.3)) or 0.3)
            s = max(0.8, T.m(2.0 * r))
            sc.rect(c[0] - s / 2.0, c[1] - s / 2.0, c[0] + s / 2.0, c[1] + s / 2.0,
                    stroke=C["cut"], w=LW["secondary"], layer="A-COLS")

    # 8 门窗洞口（design.json 的参数式 opening 展开成显式线段）
    for op in design_io.expand_openings(d):
        sc.stroke([T(op["p0"]), T(op["p1"])], draw2d.GLZ, LW["secondary"], layer="A-GLAZ")

    # 9 标注
    px0, pyt = T.pt(x0, y1)
    px1, pyb = T.pt(x1, y0)
    title = to_ascii((d.get("project") or {}).get("name", "")) or "PROJECT"
    sc.text(px0, pyt - 0.4 * eh, title, 1.1 * eh, C["text"], "lb", layer="A-ANNO")
    for m in d["masses"]:
        c = design_io.centroid(design_io.as_pts(m["footprint"]))
        pc = T(c)
        sc.text(pc[0], pc[1] - 0.35 * eh,
                "%s  %dF  H=%.2fm" % (m["id"], design_io.mass_storeys(m, lh),
                                      design_io.mass_top(m)),
                0.9 * eh, C["text"], "cb", layer="A-ANNO")

    # 10 尺寸线：用地总宽 / 总深
    dy = pyb + 0.55 * eh
    _dim_h(sc, px0, px1, dy, "%.2f m" % (x1 - x0), eh, ext=(pyb, pyb))
    dx = px0 - 0.55 * eh
    _dim_v(sc, pyb, pyt, dx, "%.2f m" % (y1 - y0), eh, ext=(px0, px0))
    sc.text(px0, dy + 1.5 * eh, "SITE AREA %.1f m2" % design_io.poly_area(bd),
            0.8 * eh, C["text_l"], "lb", layer="A-ANNO")
    sc.text(dx, dy + 1.5 * eh, "DXF R12 / UNIT mm / MODEL SPACE 1:1",
            0.8 * eh, C["text_l"], "lb", layer="A-ANNO")
    return sc


# ==========================================================================
# 写出
# ==========================================================================
def write_dxf(sc, path, ltscale=500.0):
    w = scene_to_writer(sc, ltscale)
    text = w.render()
    raw = text.encode("ascii")               # 出错就该出错：DXF 必须是纯 ASCII
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with open(path, "wb") as f:
        f.write(raw)
    return {
        "path": os.path.abspath(path),
        "bytes": len(raw),
        "lines": text.count("\r\n"),
        "groups": text.count("\r\n") // 2,
        "entities": dict(sorted(w.counts.items())),
        "by_layer": dict(sorted(w.layers.items())),
        "items": len(sc.items),
        "extents_mm": [round(v, 2) for v in (w.ext or [0, 0, 0, 0])],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="design.json -> DXF R12（AC1009，ASCII，毫米）")
    ap.add_argument("design")
    ap.add_argument("--out", required=True, help="输出 .dxf 路径")
    ap.add_argument("--ltscale", type=float, default=None,
                    help="线型比例，默认取 drawings.siteplan_scale")
    a = ap.parse_args(argv)

    d = design_io.load_design(a.design)
    sc = build_cad_scene(d)
    lt = a.ltscale if a.ltscale else float(sc.meta.get("scale") or 500)
    st = write_dxf(sc, a.out, lt)

    print("DXF: %s" % st["path"])
    print("  bytes %d / lines %d / group pairs %d" % (st["bytes"], st["lines"], st["groups"]))
    print("  header AC1009 / INSUNITS 4 (mm) / LTSCALE %g / entities at 1:1 model space"
          % lt)
    print("  entities: " + "  ".join("%s %d" % (k, v) for k, v in st["entities"].items()))
    print("  by layer: " + "  ".join("%s %d" % (k, v) for k, v in st["by_layer"].items()))
    print("  extents mm: %s" % (st["extents_mm"],))
    return 0


if __name__ == "__main__":
    sys.exit(main())
