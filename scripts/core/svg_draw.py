#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""svg_draw.py -- design.json -> 总平面 / 平面 / 剖面 三张 A1 图纸。

职责单一：把 design.json 组装成 draw2d.Scene 图元流水，再经 svg_backend 落盘。
几何全部来自 design.json（design_io 读取与派生），图元全部经 draw2d.Scene 生成，
因此 to_pdf.py / png_backend.py 可以复用同一份 Scene，三份输出必然一致。

对外接口
    build_sheet(name, design, out_dir) -> draw2d.Scene
    build_all(design, out_dir, sheets=SHEETS) -> {name: Scene}

CLI
    python svg_draw.py <design.json> --out <dir> --sheets siteplan,plan,section
"""
from __future__ import annotations

import argparse
import math
import os
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import design_io                      # noqa: E402
import draw2d                         # noqa: E402
import svg_backend                    # noqa: E402

C = draw2d.C
LW = draw2d.LW

# --------------------------------------------------------------------------
# 图幅与版面常量：A1 横 841x594，图框内边距 左 25 / 其余 10
# --------------------------------------------------------------------------
PW, PH = draw2d.PAPER["A1"]
IN_X0, IN_Y0 = 25.0, 10.0
IN_X1, IN_Y1 = PW - 10.0, PH - 10.0

TB_W, TB_H = 180.0, 60.0                       # 标题栏（右下角）
TB_X0, TB_Y0 = IN_X1 - TB_W, IN_Y1 - TB_H

F_BODY, F_NAME, F_TITLE = 2.5, 5.0, 7.0        # 正文 / 图名 / 标题（mm）

COLL_X, COLL_W = 44.0, 180.0                   # 左栏（说明/表）
COLR_X, COLR_W = 620.0, 191.0                  # 右栏
REGION = (44.0, 50.0, 806.0, 452.0)            # 图形可用区
REG_X0, REG_Y0, REG_X1, REG_Y1 = REGION
REG_CX, REG_CY = (REG_X0 + REG_X1) / 2.0, (REG_Y0 + REG_Y1) / 2.0
CTR_X = REG_CX                                 # 主图水平中心
BAND_Y = 462.0                                 # 图例 / 说明 / 表 横带顶
STRIP_Y = 512.0                                # 兼容旧引用
LADDER = (50, 100, 150, 200, 250, 300, 400, 500, 750, 1000)
NORTH_SLOT = {"section": (668.0, 140.0)}         # 剖面图上方空白处的指北针位置

SHEETS = ("siteplan", "plan", "section")
SCALES = {"siteplan": 500, "plan": 200, "section": 200}     # design.json 未声明时的兜底
SCALE_KEY = {"siteplan": "siteplan_scale", "plan": "plan_scale",
             "section": "section_scale"}
SHEET_NO = {"siteplan": "01", "plan": "02", "section": "03"}

MAT_FILL = {"concrete": "concrete", "brick": "brick", "glass": "glass",
            "metal_roof": "roof", "stone": "plaza", "asphalt": "road",
            "grass": "green_l", "water": "water"}

CORR_W = 2.4            # 内走廊净宽（m）
PART_T = 0.12           # 内隔墙厚（m）
COL_S = 0.5             # 结构柱（m）
BAY = 7.5               # 柱网模数（m）
SLAB_T = 0.12           # 楼板厚（m）
BEAM_D = 0.60           # 梁高（m）
PARAPET = 0.90          # 女儿墙（m）

SEC_SLAB = 0.15         # 剖面：楼板厚（上下两条 0.35 线）
SEC_BEAM = 0.50         # 剖面：板下边梁高（一条 0.18 线，弱化）
SEC_PARAPET = 0.30      # 剖面：女儿墙高（m）


# ==========================================================================
# 版面小工具
# ==========================================================================
def _scene():
    return draw2d.Scene(PW, PH, draw2d.load_font())


def _scale(d, name):
    """design.json 请求的比例（drawings.<name>_scale），未声明时用标准兜底值。"""
    v = (d.get("drawings", {}) or {}).get(SCALE_KEY[name])
    if isinstance(v, (int, float)) and float(v) > 0:
        return int(v)
    return SCALES[name]


def _fit_scale(d, name, w_m, h_m, region=REGION):
    """按图形可用区优选比例：填得进且取最大档；请求比例仅在其面积填充率 >=50% 时采用。
    返回 (采用比例, 请求比例或 None)。"""
    aw, ah = region[2] - region[0], region[3] - region[1]
    req = _scale(d, name)
    pw, ph = w_m * 1000.0 / req, h_m * 1000.0 / req
    if pw <= aw * 0.92 and ph <= ah * 0.92 and pw * ph >= 0.50 * aw * ah:
        return req, None
    for s in LADDER:
        if w_m * 1000.0 / s <= aw * 0.92 and h_m * 1000.0 / s <= ah * 0.92:
            return s, req
    return LADDER[-1], req


def _wrap(sc, s, size, wmax):
    """按版面宽度断行（CJK 无空格，逐字断）。"""
    out, cur = [], ""
    for ch in s:
        trial = cur + ch
        if cur and sc.text_width(trial, size) > wmax:
            out.append(cur)
            cur = ch
        else:
            cur = trial
    if cur:
        out.append(cur)
    return out or [""]


def _plate(sc, x, y, s, size=F_BODY, color=None, anchor="cm"):
    """带白底的压线文字。"""
    w = sc.text_width(s, size)
    if anchor[0] == "c":
        x0 = x - w / 2.0
    elif anchor[0] == "r":
        x0 = x - w
    else:
        x0 = x
    sc.rect(x0 - 0.6, y - size * 0.70, x0 + w + 0.6, y + size * 0.58,
            fill=C["paper"], stroke=None)
    sc.text(x, y, s, size, color or C["text"], anchor)


def _leader(sc, tip, elbow, tail, s, color=None):
    """折线引注：tip -> elbow -> tail，文字接在 tail。"""
    col = color or C["text_l"]
    sc.line(tip, elbow, col, LW["detail"])
    sc.line(elbow, tail, col, LW["detail"])
    anchor = "lm" if tail[0] >= elbow[0] else "rm"
    sc.text(tail[0] + (1.5 if anchor == "lm" else -1.5), tail[1], s, F_BODY,
            C["text"], anchor)


def _axis_bubble(sc, x, y, s, r=2.4):
    sc.circle(x, y, r, fill=C["paper"], stroke=C["frame"], w=LW["detail"], seg=28)
    sc.text(x, y, s, F_BODY, C["text"], "cm")


def _level_symbol(sc, x, y, text):
    """标高符号 ▽：水平线 + 倒三角 + 数值（数值写在线上方）。"""
    sc.line((x, y), (x + 11.0, y), C["text_l"], LW["detail"])
    sc.poly([(x - 1.7, y), (x + 1.7, y), (x, y + 2.6)], C["paper"], C["text"],
            LW["detail"])
    sc.text(x - 2.6, y - 0.6, text, F_BODY, C["text"], "rt")


def _overall_dim_h(sc, xa, xb, edge_y, y, text, size=F_BODY, over=2.0, gap=2.0):
    """横向总尺寸：尺寸线离外轮廓 gap+offset，界线只从轮廓外侧引出并伸出尺寸线 over，45° 角标长 2mm。"""
    col = C["dash"]
    s = 1.0 if y >= edge_y else -1.0
    for x in (xa, xb):
        sc.line((x, edge_y + s * gap), (x, y + s * over), col, LW["hatch"])
    sc.line((xa, y), (xb, y), col, LW["detail"])
    for x in (xa, xb):
        sc.tick((x, y), col, size=2.0)
    sc.text((xa + xb) / 2.0, y - s * 0.6, text, size, col, "cb")


def _overall_dim_v(sc, ya, yb, edge_x, x, text, size=F_BODY, over=2.0, gap=2.0):
    """竖向总尺寸：同 _overall_dim_h；数字水平压线（后端不支持旋转文字）。"""
    col = C["dash"]
    s = 1.0 if x >= edge_x else -1.0
    for y in (ya, yb):
        sc.line((edge_x + s * gap, y), (x + s * over, y), col, LW["hatch"])
    sc.line((x, ya), (x, yb), col, LW["detail"])
    for y in (ya, yb):
        sc.tick((x, y), col, size=2.0)
    _plate(sc, x, (ya + yb) / 2.0, text, size, col, "cm")


def _human(sc, x, ybase, k, h=1.70):
    """1.7m 人体比例参照（x 为轴线，ybase 为站立面）。"""
    hh = h * k
    top = ybase - hh
    hd = hh * 0.135
    col = C["text_l"]
    sc.circle(x, top + hd / 2.0, hd / 2.0, fill=C["paper"], stroke=col,
              w=LW["detail"], seg=18)
    sh = top + hd
    hip = ybase - hh * 0.44
    sc.line((x, sh), (x, hip), col, LW["detail"])
    sc.line((x, sh + hh * 0.05), (x - hh * 0.115, sh + hh * 0.30), col, LW["detail"])
    sc.line((x, sh + hh * 0.05), (x + hh * 0.115, sh + hh * 0.30), col, LW["detail"])
    sc.line((x, hip), (x - hh * 0.085, ybase), col, LW["detail"])
    sc.line((x, hip), (x + hh * 0.085, ybase), col, LW["detail"])


# ==========================================================================
# 图框 / 标题栏 / 指北针 / 比例尺 / 图例 / 说明
# ==========================================================================
def draw_border(sc):
    draw2d.paper_underlay(sc)
    sc.rect(4.0, 4.0, sc.w - 4.0, sc.h - 4.0, fill=None, stroke=C["frame"],
            w=LW["secondary"])
    sc.rect(IN_X0, IN_Y0, IN_X1, IN_Y1, fill=None, stroke=C["frame"],
            w=LW["outline"])
    step = 50.0
    i = 1
    while IN_X0 + i * step < IN_X1 - 1.0:
        x = IN_X0 + i * step
        sc.line((x, IN_Y0), (x, IN_Y0 + 2.5), C["frame"], LW["hatch"])
        sc.line((x, IN_Y1 - 2.5), (x, IN_Y1), C["frame"], LW["hatch"])
        i += 1
    i = 1
    while IN_Y0 + i * step < IN_Y1 - 1.0:
        y = IN_Y0 + i * step
        sc.line((IN_X0, y), (IN_X0 + 2.5, y), C["frame"], LW["hatch"])
        sc.line((IN_X1 - 2.5, y), (IN_X1, y), C["frame"], LW["hatch"])
        i += 1


def draw_title_block(sc, d, sheet):
    pr = d.get("project", {})
    x0, y0, x1, y1 = TB_X0, TB_Y0, IN_X1, IN_Y1
    r1, r2 = y0 + 24.0, y0 + 42.0
    c1, c2, c3 = x0 + 120.0, x0 + 70.0, x0 + 132.0

    sc.rect(x0, y0, x1, y1, fill=C["paper"], stroke=C["frame"], w=LW["outline"])
    for yy in (r1, r2):
        sc.line((x0, yy), (x1, yy), C["frame"], LW["secondary"])
    sc.line((c1, r1), (c1, r2), C["frame"], LW["secondary"])
    for cx in (c2, c3):
        sc.line((cx, r2), (cx, y1), C["frame"], LW["secondary"])

    sc.text(x0 + 4.0, y0 + 3.2, pr.get("name", ""), F_NAME, C["text"], "lt")
    sc.text(x1 - 4.0, y0 + 3.8, "arch-env-design", F_BODY, C["text"], "rt")
    sc.text(x0 + 4.0, y0 + 15.2, "%s / %s / %s 标准 / 单位 m"
            % (pr.get("type", ""), pr.get("location", ""), pr.get("standards", "")),
            F_BODY, C["text_l"], "lt")
    sc.text(x1 - 4.0, y0 + 15.2, pr.get("drawing_number", ""), F_BODY,
            C["text_l"], "rt")

    sc.text(x0 + 4.0, r1 + 2.6, "图名", F_BODY, C["text_l"], "lt")
    sc.text(x0 + 4.0, r1 + 7.2, sheet["title"], F_NAME, C["text"], "lt")
    sc.text(c1 + 4.0, r1 + 2.6, "比例", F_BODY, C["text_l"], "lt")
    sc.text(c1 + 4.0, r1 + 7.2, "1:%d" % sheet["scale"], F_NAME, C["text"], "lt")

    sc.text(x0 + 4.0, r2 + 2.6, "图号", F_BODY, C["text_l"], "lt")
    sc.text(x0 + 4.0, r2 + 10.2, sheet["no"], F_BODY, C["text"], "lt")
    sc.text(c2 + 4.0, r2 + 2.6, "日期", F_BODY, C["text_l"], "lt")
    sc.text(c2 + 4.0, r2 + 10.2, pr.get("date", ""), F_BODY, C["text"], "lt")
    sc.text(c3 + 4.0, r2 + 2.6, "专业", F_BODY, C["text_l"], "lt")
    sc.text(c3 + 4.0, r2 + 10.2, sheet["disc"], F_BODY, C["text"], "lt")


def draw_north(sc, cx, cy, r, deg, label="指北针"):
    sc.circle(cx, cy, r, fill=C["paper"], stroke=C["frame"], w=LW["secondary"],
              seg=64)
    sc.circle(cx, cy, r * 0.70, fill=None, stroke=C["dash"], w=LW["hatch"], seg=48)
    a = math.radians(90.0 + deg)
    tip = (cx + r * 0.86 * math.cos(a), cy - r * 0.86 * math.sin(a))
    left = (cx + r * 0.34 * math.cos(a + 2.1), cy - r * 0.34 * math.sin(a + 2.1))
    right = (cx + r * 0.34 * math.cos(a - 2.1), cy - r * 0.34 * math.sin(a - 2.1))
    sc.poly([tip, left, (cx, cy)], C["cut"], C["cut"], LW["detail"])
    sc.poly([tip, right, (cx, cy)], C["paper"], C["cut"], LW["detail"])
    sc.text(cx + (r + 5.4) * math.cos(a), cy - (r + 5.4) * math.sin(a), "N",
            F_NAME, C["text"], "cm")
    sc.text(cx, cy + r + 4.2, label, F_BODY, C["text_l"], "ct")


def draw_scale(sc, x, y, scale, total_m, segs=4):
    """黑白相间比例尺条 + 数值；长度按 1:scale 实算。"""
    k = 1000.0 / float(scale)
    seg_m = total_m / float(segs)
    w = seg_m * k
    for i in range(segs):
        sc.rect(x + i * w, y, x + (i + 1) * w, y + 3.0,
                fill=C["frame"] if i % 2 == 0 else C["paper"],
                stroke=C["frame"], w=LW["detail"])
    for i in range(segs + 1):
        sc.text(x + i * w, y + 4.4, "%g" % (i * seg_m), F_BODY, C["text"], "ct")
    sc.text(x, y - 4.6, "比例尺 1:%d（单位：米）" % scale, F_BODY, C["text_l"], "lt")


def draw_legend(sc, x, y, items, cols=2, colw=150.0, rowh=6.8, title="图例"):
    sc.text(x, y, title, F_BODY, C["text"], "lt")
    sc.line((x, y + 3.6), (x + colw * cols - 20.0, y + 3.6), C["frame"],
            LW["secondary"])
    for i, (label, kind, color) in enumerate(items):
        cx = x + (i % cols) * colw
        cy = y + 8.0 + (i // cols) * rowh
        if kind == "fill":
            sc.rect(cx, cy, cx + 7.5, cy + 4.2, fill=color, stroke=C["text_l"],
                    w=LW["detail"])
        elif kind == "line":
            sc.line((cx, cy + 2.1), (cx + 7.5, cy + 2.1), color, LW["outline"])
        elif kind == "dash":
            sc.stroke([(cx, cy + 2.1), (cx + 7.5, cy + 2.1)], color,
                      LW["secondary"], dash=(2.2, 1.2))
        elif kind == "tree":
            sc.circle(cx + 3.7, cy + 2.1, 2.1, fill=color, stroke=C["tree_e"],
                      w=LW["detail"], seg=24)
            sc.line((cx + 1.6, cy + 2.1), (cx + 5.8, cy + 2.1), C["tree_e"],
                    LW["hatch"])
            sc.line((cx + 3.7, cy - 0.0), (cx + 3.7, cy + 4.2), C["tree_e"],
                    LW["hatch"])
        sc.text(cx + 10.0, cy + 2.1, label, F_BODY, C["text_l"], "lm")


def draw_notes(sc, x, y, lines, w=COLR_W, title="设计说明", rowh=5.4):
    sc.text(x, y, title, F_BODY, C["text"], "lt")
    sc.line((x, y + 3.6), (x + w, y + 3.6), C["frame"], LW["secondary"])
    yy = y + 9.2
    for s in lines:
        for chunk in _wrap(sc, s, F_BODY, w):
            sc.text(x, yy, chunk, F_BODY, C["text_l"], "lt")
            yy += rowh
    return yy


def draw_table(sc, x, y, cols, rows, colw, rowh=6.4, title=""):
    """格网表（表头 + 数据行），返回表格底边 y。"""
    top = y
    if title:
        sc.text(x, top, title, F_BODY, C["text"], "lt")
        top += 4.6
    nrow = (1 if cols else 0) + len(rows)
    if nrow <= 0:
        return top
    w = sum(colw)
    h = nrow * rowh
    sc.rect(x, top, x + w, top + h, fill=C["paper"], stroke=C["frame"],
            w=LW["secondary"])
    for i in range(1, nrow):
        sc.line((x, top + i * rowh), (x + w, top + i * rowh), C["frame"],
                LW["hatch"])
    cx = x
    for cw in colw[:-1]:
        cx += cw
        sc.line((cx, top), (cx, top + h), C["frame"], LW["hatch"])
    cy = top + rowh / 2.0
    if cols:
        ccx = x
        for i, s in enumerate(cols):
            sc.text(ccx + colw[i] / 2.0, cy, s, F_BODY, C["text"], "cm")
            ccx += colw[i]
        cy += rowh
    for row in rows:
        ccx = x
        for i, s in enumerate(row[:len(colw)]):
            sc.text(ccx + colw[i] / 2.0, cy, s, F_BODY, C["text_l"], "cm")
            ccx += colw[i]
        cy += rowh
    return top + h


def draw_heading(sc, title, sub):
    sc.text(COLL_X, 24.0, title, F_TITLE, C["text"], "lt")
    sc.line((COLL_X, 36.6), (COLL_X + COLL_W, 36.6), C["frame"], LW["secondary"])
    sc.text(COLL_X, 39.6, sub, F_BODY, C["text_l"], "lt")


# ==========================================================================
# 一、总平面图
# ==========================================================================
NEUTRAL = ("plaza", "concrete")


def _mass_fills(d):
    """建筑统一取中性建筑色，按体量高度分级明度（越高越深）；材料色只用于立面/材料分析。"""
    ms = sorted(d["masses"], key=lambda m: design_io.mass_top(m))
    n = len(ms)
    out = {}
    for i, m in enumerate(ms):
        k = 0 if n <= 1 else int(round(i * (len(NEUTRAL) - 1) / float(n - 1)))
        out[m["id"]] = C[NEUTRAL[k]]
    return out


def _trees(d):
    """乔木符号：优先用 design.json 的 tree 图元，否则在绿地范围内示意布点。"""
    out = []
    for el in d.get("elements", []):
        if el.get("kind") == "tree" and el.get("center"):
            out.append((tuple(el["center"]), float(el.get("radius", 3.0) or 3.0)))
    if out:
        return out
    for el in d.get("elements", []):
        if el.get("kind") != "green":
            continue
        poly = design_io.as_pts(el["polygon"])
        bx0, by0, bx1, by1 = design_io.bbox(poly)
        step = 6.0
        nx = max(1, int(round((bx1 - bx0) / step)))
        ny = max(1, int(round((by1 - by0) / step)))
        r = max(1.2, min(bx1 - bx0, by1 - by0) * 0.18)
        for i in range(nx):
            for j in range(ny):
                px = bx0 + (bx1 - bx0) * (i + 0.5) / nx
                py = by0 + (by1 - by0) * (j + 0.5) / ny
                if design_io.point_in_poly((px, py), poly):
                    out.append(((px, py), r))
    return out


def draw_siteplan(sc, d, met, scale, region=REGION):
    """总平面：返回 {"bbox": 图面范围}，图例/表/说明由 _band 统一排。"""
    bd = design_io.as_pts(d["site"]["boundary"])
    bx0, by0, bx1, by1 = design_io.bbox(bd)
    cx = (region[0] + region[2]) / 2.0 + 10.0
    cy = (region[1] + region[3]) / 2.0 - 6.0
    T = draw2d.ToPaper(scale, cx, cy, (bx0 + bx1) / 2.0, (by0 + by1) / 2.0)
    fills = _mass_fills(d)
    els = d.get("elements", [])

    sc.poly([T(p) for p in bd], C["green"], None)

    sbp = draw2d.setback_polygon(bd, d["site"].get("setbacks", {}) or {})
    if sbp:
        sc.stroke([T(p) for p in sbp], C["dash"], LW["hatch"], closed=True,
                  dash=(3.0, 1.6))

    for el in els:
        if el.get("kind") == "green":
            sc.poly([T(p) for p in el["polygon"]], C["green_l"], C["tree_e"],
                    LW["hatch"])

    for el in els:
        if el.get("kind") == "contour":
            pts = [T(p) for p in el["points"]]
            sc.stroke(pts, C["hatch"], LW["hatch"])
            mid = pts[len(pts) // 2]
            _plate(sc, mid[0], mid[1], "%+.1f" % float(el["elevation"]), F_BODY,
                   C["text_l"], "cm")

    for el in els:
        if el.get("kind") == "water":
            sc.poly([T(p) for p in el["polygon"]], C["water"], C["water_e"],
                    LW["secondary"])
            c = T(design_io.centroid(design_io.as_pts(el["polygon"])))
            if el.get("name"):
                _plate(sc, c[0], c[1], el["name"], F_BODY, C["water_e"], "cm")

    for el in els:
        if el.get("kind") != "plaza":
            continue
        pp = [T(p) for p in el["polygon"]]
        sc.poly(pp, C["plaza"], C["road_e"], LW["detail"])
        sc.hatch(pp, spacing=2.0, angle=0.0, color=C["road_e"], w=LW["hatch"])
        sc.hatch(pp, spacing=2.0, angle=90.0, color=C["road_e"], w=LW["hatch"])
        c = T(design_io.centroid(design_io.as_pts(el["polygon"])))
        if el.get("name"):
            _plate(sc, c[0], c[1], el["name"], F_BODY, C["text_l"], "cm")

    for el in els:
        if el.get("kind") != "path":
            continue
        pts = design_io.as_pts(el["points"])
        w = float(el.get("width", 6.0))
        paper = [T(p) for p in pts]
        sc.stroke(paper, C["road"], T.m(w), cap="butt", join="round")
        for sgn in (1.0, -1.0):
            edge = design_io.polyline_offset(pts, sgn * w / 2.0)
            if len(edge) >= 2:
                sc.stroke([T(p) for p in edge], C["road_e"], LW["detail"],
                          cap="butt")
        sc.stroke(paper, C["paper"], LW["hatch"], dash=(5.0, 3.0))

    for (c, r) in _trees(d):
        pc = T(c)
        rr = max(1.3, T.m(float(r)))
        sc.circle(pc[0], pc[1], rr, fill=C["tree"], stroke=C["tree_e"],
                  w=LW["detail"], seg=24)
        sc.line((pc[0] - rr * 0.72, pc[1]), (pc[0] + rr * 0.72, pc[1]),
                C["tree_e"], LW["hatch"])
        sc.line((pc[0], pc[1] - rr * 0.72), (pc[0], pc[1] + rr * 0.72),
                C["tree_e"], LW["hatch"])

    for m in d["masses"]:
        fp = design_io.as_pts(m["footprint"])
        sc.poly([T(p) for p in fp], fills[m["id"]], C["cut"], LW["outline"])
        oh = float(m.get("overhang", 0) or 0)
        if oh > 0:
            g = draw2d.offset_poly_signed(fp, -oh)
            if g:
                sc.stroke([T(p) for p in g], C["dash"], LW["hatch"], closed=True,
                          dash=(3.0, 1.4))
        if m.get("roof") in ("gable", "shed") and float(m.get("roof_rise", 0) or 0) > 0:
            gx0, gy0, gx1, gy1 = design_io.bbox(fp)
            if (gx1 - gx0) >= (gy1 - gy0):
                ym = (gy0 + gy1) / 2.0
                sc.line(T.pt(gx0, ym), T.pt(gx1, ym), C["cut"], LW["secondary"])
            else:
                xm = (gx0 + gx1) / 2.0
                sc.line(T.pt(xm, gy0), T.pt(xm, gy1), C["cut"], LW["secondary"])

    sc.stroke([T(p) for p in bd], C["site"], LW["secondary"], closed=True,
              dash=(6.0, 2.2))

    lh = design_io.level_height(d)
    for m in d["masses"]:
        c = T(design_io.centroid(design_io.as_pts(m["footprint"])))
        st = design_io.mass_storeys(m, lh)
        _plate(sc, c[0], c[1] - 1.8, m["id"], F_NAME, C["text"], "cm")
        _plate(sc, c[0], c[1] + 4.6, "%dF H=%.1fm" % (st, design_io.mass_top(m)),
               F_BODY, C["text_l"], "cm")

    p0, p1 = T.pt(bx0, by0), T.pt(bx1, by1)
    xa, xb = min(p0[0], p1[0]), max(p0[0], p1[0])
    ya, yb = min(p0[1], p1[1]), max(p0[1], p1[1])
    _overall_dim_h(sc, xa, xb, yb, yb + 10.0, "%.2f" % (bx1 - bx0))
    _overall_dim_v(sc, ya, yb, xa, xa - 10.0, "%.2f" % (by1 - by0))
    return {"bbox": (xa - 21.0, ya, xb, yb + 21.0)}


# ==========================================================================
# 二、平面图（代表性楼层）
# ==========================================================================
def _wall_thickness(d):
    for src, key in ((d.get("project", {}) or {}, "wall_thickness"),
                     (d.get("project", {}) or {}, "wall"),
                     (d.get("drawings", {}) or {}, "wall_thickness")):
        v = src.get(key)
        if isinstance(v, (int, float)) and 0.05 <= float(v) <= 0.6:
            return float(v)
    return 0.24


def _openings_of(d, mid):
    return [o for o in design_io.expand_openings(d) if o.get("host") == mid]


def _op_rows(d, m, ent=None):
    """门窗表：按类型与洞口尺寸归并计数，避免逐洞口罗列。"""
    groups = {}
    for o in _openings_of(d, m["id"]):
        key = ("W" if o.get("type") == "window" else "M",
               int(round(float(o["width"]) * 1000)),
               int(round(float(o["height"]) * 1000)))
        groups[key] = groups.get(key, 0) + int(o.get("count", 1))
    rows, no = [], {"W": 0, "M": 0}
    for (t, w, h) in sorted(groups, key=lambda q: (q[0] != "W", -q[1])):
        no[t] += 1
        rows.append(["%s%d" % (t, no[t]), "%d x %d" % (w, h), "%d" % groups[(t, w, h)]])
    if ent and ent[2] - ent[1] > 0.5:
        wd = int(round(min(2.4, (ent[2] - ent[1]) * 0.6) * 1000))
        rows.append(["M主", "%d x 2700" % wd, "1"])
    return rows


def _plan_target(d):
    levels = d["site"].get("levels") or [{"name": "一层", "elevation": 0.0}]
    idx = int((d.get("drawings", {}) or {}).get("plan_level", 1))
    idx = max(0, min(idx, len(levels) - 1))
    lh = design_io.level_height(d)
    z = float(levels[idx]["elevation"])
    cands = []
    for m in d["masses"]:
        st = design_io.mass_storeys(m, lh)
        base = float(m.get("base", 0.0))
        if base - 1e-6 <= z < base + st * lh - 1e-6:
            cands.append(m)
    if not cands:
        cands = list(d["masses"])
    m = max(cands, key=lambda x: (len(_openings_of(d, x["id"])),
                                  design_io.poly_area(design_io.as_pts(x["footprint"]))))
    st = design_io.mass_storeys(m, lh)
    idx = min(idx, st - 1)
    return m, idx, levels[idx]


def _layout(fp, wall):
    x0, y0, x1, y1 = design_io.bbox(fp)
    along_x = (x1 - x0) >= (y1 - y0)
    L = (x1 - x0) if along_x else (y1 - y0)
    D = (y1 - y0) if along_x else (x1 - x0)
    nb = max(2, int(round(L / BAY)))
    axes = [L * i / float(nb) for i in range(nb + 1)]
    c0 = D / 2.0 - CORR_W / 2.0
    c1 = c0 + CORR_W
    if c0 < wall + PART_T * 3.0:
        c0 = wall + PART_T * 2.0
        c1 = c0 + CORR_W
    if c1 > D - wall - PART_T * 3.0:
        c1 = D - wall - PART_T * 2.0
        c0 = c1 - CORR_W

    def P(a, c):
        return (x0 + a, y0 + c) if along_x else (x0 + c, y0 + a)

    def loc(p):
        return ((p[0] - x0, p[1] - y0) if along_x else (p[1] - y0, p[0] - x0))

    return {"fp": fp, "bbox": (x0, y0, x1, y1), "along_x": along_x, "L": L,
            "D": D, "nb": nb, "axes": axes, "c0": c0, "c1": c1, "wall": wall,
            "P": P, "loc": loc}


def _band(lay, a0, a1, c0, c1):
    P = lay["P"]
    return [P(a0, c0), P(a1, c0), P(a1, c1), P(a0, c1)]


def _entrance(d, lay):
    """由贴在 footprint 上的 slab（雨篷 / 台阶）推断主入口：返回 (side, a0, a1)。"""
    L, D, wall = lay["L"], lay["D"], lay["wall"]
    best = None
    for el in d.get("elements", []):
        if el.get("kind") != "slab":
            continue
        lc = [lay["loc"](p) for p in design_io.as_pts(el["polygon"])]
        a0 = min(q[0] for q in lc)
        a1 = max(q[0] for q in lc)
        c0 = min(q[1] for q in lc)
        c1 = max(q[1] for q in lc)
        if a1 <= wall or a0 >= L - wall:
            continue
        side = None
        if c1 <= wall + 0.8:
            side = 0
        elif c0 >= D - wall - 0.8:
            side = 1
        if side is None:
            continue
        a0 = max(wall + 0.6, a0)
        a1 = min(L - wall - 0.6, a1)
        if a1 - a0 < 2.0:
            continue
        if best is None or (a1 - a0) > (best[2] - best[1]):
            best = (side, a0, a1)
    return best


def draw_plan(sc, d, m, li, lv, met, scale, region=REGION):
    """本层平面：返回 {"bbox": 图面范围, "cells": 房间格子, "ops": 门窗表行}。"""
    k = 1000.0 / scale
    fp = design_io.as_pts(m["footprint"])
    wall = _wall_thickness(d)
    lay = _layout(fp, wall)
    x0, y0, x1, y1 = lay["bbox"]
    T = draw2d.ToPaper(scale, (region[0] + region[2]) / 2.0,
                       (region[1] + region[3]) / 2.0 - 12.0,
                       (x0 + x1) / 2.0, (y0 + y1) / 2.0)
    P = lay["P"]

    ops = [o for o in _openings_of(d, m["id"])
           if o.get("level") in (None, li, lv.get("name"))]
    ent = _entrance(d, lay)
    if ent:
        side, a0, a1 = ent
        # 入口由贴在立面上的 slab 推断，slab 沿立面往往很长（台阶 / 平台），
        # 洞口宽按 _op_rows 的同一口径收敛到门宽，否则整段外墙被当作门洞切除。
        dw = min(2.4, (a1 - a0) * 0.6)
        am = (a0 + a1) / 2.0
        target = 0.0 if side == 0 else lay["D"]
        p0, p1 = P(am - dw / 2.0, target), P(am + dw / 2.0, target)
        ops.append({"host": m["id"], "type": "door", "sill": 0.0, "height": 2.7,
                    "level": None, "p0": p0, "p1": p1,
                    "width": dw})

    inner = design_io.offset_polygon(fp, wall)
    inner = draw2d.align_poly(inner, fp) if inner else None

    # ---- 外墙（双线）+ 洞口 ----
    n = len(fp)
    for i in range(n):
        a, b = fp[i], fp[(i + 1) % n]
        ia, ib = (inner[i], inner[(i + 1) % n]) if inner else (a, b)
        L = math.dist(a, b)
        if L < 1e-6:
            continue
        ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
        cuts = []
        for o in ops:
            p, q = o["p0"], o["p1"]
            if abs((p[0] - a[0]) * uy - (p[1] - a[1]) * ux) > 0.12:
                continue
            if abs((q[0] - a[0]) * uy - (q[1] - a[1]) * ux) > 0.12:
                continue
            t0 = (p[0] - a[0]) * ux + (p[1] - a[1]) * uy
            t1 = (q[0] - a[0]) * ux + (q[1] - a[1]) * uy
            t0, t1 = min(t0, t1), max(t0, t1)
            if t1 < -0.01 or t0 > L + 0.01:
                continue
            cuts.append((max(0.0, t0), min(L, t1), o))
        mo = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
        mi = ((ia[0] + ib[0]) / 2.0, (ia[1] + ib[1]) / 2.0)
        dlen = math.hypot(mi[0] - mo[0], mi[1] - mo[1]) if inner else 0.0
        nx, ny = ((mi[0] - mo[0]) / dlen, (mi[1] - mo[1]) / dlen) \
            if dlen > 1e-9 else (0.0, 0.0)

        def out_pt(t):
            return (a[0] + ux * t, a[1] + uy * t)

        def in_pt(t):
            p = out_pt(t)
            return (p[0] + nx * dlen, p[1] + ny * dlen)

        spans = draw2d.subtract_spans((0.0, L), [(c[0], c[1]) for c in cuts])
        for (s0, s1) in spans:
            sc.line(T(out_pt(s0)), T(out_pt(s1)), C["cut"], LW["outline"])
            if inner:
                sc.line(T(in_pt(s0)), T(in_pt(s1)), C["cut"], LW["outline"])
        for (c0, c1, o) in cuts:
            p0, p1 = out_pt(c0), out_pt(c1)
            q0, q1 = in_pt(c0), in_pt(c1)
            if o.get("type") == "door":
                sc.line(T(p0), T(q0), C["cut"], LW["outline"])
                sc.line(T(p1), T(q1), C["cut"], LW["outline"])
            elif inner:
                # 窗：墙线已在洞口处断开，洞内按真实墙厚围合四条边——外墙皮 p0-p1、
                # 内墙皮 q0-q1、两端洞口侧壁 p0-q0 / p1-q1；玻璃单线居中，线宽 LW detail。
                sc.line(T(p0), T(p1), C["cut"], LW["outline"])
                sc.line(T(q0), T(q1), C["cut"], LW["outline"])
                sc.line(T(p0), T(q0), C["cut"], LW["outline"])
                sc.line(T(p1), T(q1), C["cut"], LW["outline"])
                sc.line(T(draw2d.lerp(p0, q0, 0.5)), T(draw2d.lerp(p1, q1, 0.5)),
                        C["water_e"], LW["detail"])

    # ---- 内隔墙 / 走廊 / 分间 ----
    L, D = lay["L"], lay["D"]
    c0c, c1c = lay["c0"], lay["c1"]
    a_lo, a_hi = wall, L - wall
    c_axes = [0.0, c0c - PART_T / 2.0, c1c + PART_T / 2.0, D]

    cells = []
    for i in range(lay["nb"]):
        a0 = lay["axes"][i] + (PART_T / 2.0 if i > 0 else wall)
        a1 = lay["axes"][i + 1] - (PART_T / 2.0 if i < lay["nb"] - 1 else wall)
        for side, (cc0, cc1) in ((0, (wall, c0c - PART_T)), (1, (c1c + PART_T, D - wall))):
            if a1 - a0 < 1.0 or cc1 - cc0 < 1.0:
                continue
            corners = [P(a0, cc0), P(a1, cc0), P(a1, cc1), P(a0, cc1)]
            if not all(design_io.point_in_poly(c, fp) for c in corners):
                continue
            cells.append({"a0": a0, "a1": a1, "c0": cc0, "c1": cc1,
                          "side": side, "bay": i,
                          "area": (a1 - a0) * (cc1 - cc0)})

    stairs = []
    for el in d.get("elements", []):
        if el.get("kind") != "stair":
            continue
        st_poly = design_io.as_pts(el["polygon"])
        if not all(design_io.point_in_poly(p, fp) for p in st_poly):
            continue
        stairs.append((el, st_poly))

    for cell in cells:
        cell["name"] = "普通教室"
        cen = P((cell["a0"] + cell["a1"]) / 2.0, (cell["c0"] + cell["c1"]) / 2.0)
        for (el, st_poly) in stairs:
            b = design_io.bbox(st_poly)
            lc = [lay["loc"](p) for p in ((b[0], b[1]), (b[2], b[3]))]
            sa0, sa1 = min(q[0] for q in lc), max(q[0] for q in lc)
            sc0, sc1 = min(q[1] for q in lc), max(q[1] for q in lc)
            if sa1 > cell["a0"] and sa0 < cell["a1"] and sc1 > cell["c0"] and sc0 < cell["c1"]:
                cell["name"] = "楼梯间"
        if ent and cell["side"] == ent[0]:
            mid = (ent[1] + ent[2]) / 2.0
            if cell["a0"] <= mid <= cell["a1"]:
                cell["name"] = "门厅"

    wall_bands = [(c0c - PART_T, c0c, 0), (c1c, c1c + PART_T, 1)]
    for (b0, b1, side) in wall_bands:
        gaps = []
        for cell in cells:
            if cell["side"] != side:
                continue
            mid = (cell["a0"] + cell["a1"]) / 2.0
            dw = min(2.4, (cell["a1"] - cell["a0"]) * 0.6) if (cell["name"] == "门厅") else 1.0
            gaps.append((mid - dw / 2.0, mid + dw / 2.0))
        for (s0, s1) in draw2d.subtract_spans((a_lo, a_hi), gaps):
            sc.poly([T(p) for p in _band(lay, s0, s1, b0, b1)], C["cut"],
                    C["cut"], LW["detail"])

    for i in range(1, lay["nb"]):
        a = lay["axes"][i]
        for (b0, b1) in ((wall, c0c - PART_T), (c1c + PART_T, D - wall)):
            if b1 - b0 < 0.6:
                continue
            bp = [P(a - PART_T / 2.0, b0), P(a + PART_T / 2.0, b0),
                  P(a + PART_T / 2.0, b1), P(a - PART_T / 2.0, b1)]
            if not all(design_io.point_in_poly(p, fp) for p in bp):
                continue
            hit = False
            for (el, st_poly) in stairs:
                b = design_io.bbox(st_poly)
                lc = [lay["loc"](p) for p in ((b[0], b[1]), (b[2], b[3]))]
                sa0, sa1 = min(q[0] for q in lc), max(q[0] for q in lc)
                sc0, sc1 = min(q[1] for q in lc), max(q[1] for q in lc)
                if sa0 - 0.2 <= a <= sa1 + 0.2 and sc1 > b0 and sc0 < b1:
                    hit = True
            if hit:
                continue
            sc.poly([T(p) for p in bp], C["cut"], C["cut"], LW["detail"])

    # ---- 门扇 ----
    for cell in cells:
        if cell["name"] == "楼梯间":
            continue
        mid = (cell["a0"] + cell["a1"]) / 2.0
        dw = min(2.4, (cell["a1"] - cell["a0"]) * 0.6) if (cell["name"] == "门厅") else 1.0
        sgn = -1.0 if cell["side"] == 0 else 1.0
        cface = (c0c - PART_T) if cell["side"] == 0 else (c1c + PART_T)
        hinge = P(mid - dw / 2.0, cface)
        tip = P(mid - dw / 2.0, cface + sgn * dw)
        sc.line(T(hinge), T(tip), C["cut"], LW["detail"])
        arc = []
        for t in range(9):
            ang = math.pi / 2.0 * t / 8.0
            arc.append(T(P(mid - dw / 2.0 + dw * math.sin(ang),
                           cface + sgn * dw * (1.0 - math.cos(ang)))))
        sc.stroke(arc, C["cut"], LW["hatch"])

    # ---- 柱网 ----
    cs = COL_S
    cols = []
    for a in lay["axes"]:
        a_ = min(max(a, wall / 2.0 + 0.05), L - wall / 2.0 - 0.05)
        cols.append(P(a_, cs / 2.0))
        cols.append(P(a_, D - cs / 2.0))
        cols.append(P(a, c0c - PART_T / 2.0))
        cols.append(P(a, c1c + PART_T / 2.0))
    for p in cols:
        if not design_io.point_in_poly(p, fp):
            continue
        pc = T(p)
        s = T.m(cs)
        sc.rect(pc[0] - s / 2.0, pc[1] - s / 2.0, pc[0] + s / 2.0, pc[1] + s / 2.0,
                fill=C["cut"], stroke=None, w=LW["hatch"])

    # ---- 楼梯 ----
    for (el, st_poly) in stairs:
        st_paper = [T(p) for p in st_poly]
        sc.poly(st_paper, C["paper"], C["cut"], LW["secondary"])
        b = design_io.bbox(st_poly)
        bx0, by0, bx1, by1 = b
        along_stair_x = (bx1 - bx0) >= (by1 - by0)
        span = (bx1 - bx0) if along_stair_x else (by1 - by0)
        nstep = max(4, int(round(span / 0.30)))
        for j in range(1, nstep):
            t = j / float(nstep)
            if along_stair_x:
                xx = bx0 + (bx1 - bx0) * t
                sc.line(T.pt(xx, by0), T.pt(xx, by1), C["cut"], LW["hatch"])
            else:
                yy = by0 + (by1 - by0) * t
                sc.line(T.pt(bx0, yy), T.pt(bx1, yy), C["cut"], LW["hatch"])
        if along_stair_x:
            yc = (by0 + by1) / 2.0
            sc.arrow(T.pt(bx0 + (bx1 - bx0) * 0.22, yc),
                     T.pt(bx0 + (bx1 - bx0) * 0.78, yc), C["cut"], LW["detail"], 1.4)
        else:
            xc = (bx0 + bx1) / 2.0
            sc.arrow(T.pt(xc, by0 + (by1 - by0) * 0.22),
                     T.pt(xc, by0 + (by1 - by0) * 0.78), C["cut"], LW["detail"], 1.4)
        c = T(design_io.centroid(st_poly))
        _plate(sc, c[0], c[1] - 1.8, "上", F_BODY, C["text"], "cm")
        _plate(sc, c[0], c[1] + 3.4, "楼梯间", F_BODY, C["text_l"], "cm")

    # ---- 房间名与面积 ----
    for cell in cells:
        c = T(P((cell["a0"] + cell["a1"]) / 2.0, (cell["c0"] + cell["c1"]) / 2.0))
        if cell["name"] == "楼梯间":
            continue
        _plate(sc, c[0], c[1] - 1.6, cell["name"], F_BODY, C["text"], "cm")
        _plate(sc, c[0], c[1] + 2.4, "%.1f m2" % cell["area"], F_BODY,
               C["text_l"], "cm")
    if a_hi - a_lo > 3.0:
        cc = T(P((a_lo + a_hi) / 2.0, (c0c + c1c) / 2.0))
        _plate(sc, cc[0], cc[1], "内走廊 净宽 %d" % int(round(CORR_W * 1000)),
               F_BODY, C["text_l"], "cm")

    # ---- 轴网 / 尺寸 ----
    pt_a = [T.pt(*(P(a, 0.0))) for a in lay["axes"]]
    pt_c = [T.pt(*(P(0.0, c))) for c in c_axes]
    xs_axes = sorted(pt_a if lay["along_x"] else pt_c, key=lambda p: p[0])
    ys_axes = sorted(pt_c if lay["along_x"] else pt_a, key=lambda p: -p[1])

    px = [p[0] for p in (T.pt(x0, y0), T.pt(x1, y1))]
    py = [p[1] for p in (T.pt(x0, y0), T.pt(x1, y1))]
    bx_lo, bx_hi = min(px), max(px)
    by_lo, by_hi = min(py), max(py)

    if lay["along_x"]:
        hpts = sorted([(T.pt(*(P(a, 0.0)))[0], a) for a in lay["axes"]])
        vpts = sorted([(T.pt(*(P(0.0, c)))[1], c) for c in c_axes])
    else:
        hpts = sorted([(T.pt(*(P(0.0, c)))[0], c) for c in c_axes])
        vpts = sorted([(T.pt(*(P(a, 0.0)))[1], a) for a in lay["axes"]])

    y_dim1, y_dim2, y_bub = by_hi + 10.0, by_hi + 20.0, by_hi + 29.0
    for i in range(len(hpts) - 1):
        draw2d.dim_h(sc, hpts[i][0], hpts[i + 1][0], y_dim1,
                     "%.0f" % ((hpts[i + 1][1] - hpts[i][1]) * 1000.0),
                     ext=(by_hi, by_hi), color=C["dash"], size=F_BODY)
    draw2d.dim_h(sc, hpts[0][0], hpts[-1][0], y_dim2,
                 "%.0f" % ((hpts[-1][1] - hpts[0][1]) * 1000.0),
                 ext=(by_hi, by_hi), color=C["dash"], size=F_BODY)

    x_dim1, x_dim2, x_bub = bx_lo - 10.0, bx_lo - 20.0, bx_lo - 29.0
    for i in range(len(vpts) - 1):
        draw2d.dim_v(sc, vpts[i][0], vpts[i + 1][0], x_dim1,
                     "%.0f" % ((vpts[i + 1][1] - vpts[i][1]) * 1000.0),
                     ext=(bx_lo, bx_lo), color=C["dash"], size=F_BODY)
    draw2d.dim_v(sc, vpts[0][0], vpts[-1][0], x_dim2,
                 "%.0f" % ((vpts[-1][1] - vpts[0][1]) * 1000.0),
                 ext=(bx_lo, bx_lo), color=C["dash"], size=F_BODY)

    for i, p in enumerate(xs_axes):
        sc.line((p[0], by_hi), (p[0], y_bub), C["dash"], LW["hatch"], dash=(2.0, 1.2))
        _axis_bubble(sc, p[0], y_bub + 4.0, str(i + 1))
    for i, p in enumerate(ys_axes):
        sc.line((bx_lo, p[1]), (x_bub, p[1]), C["dash"], LW["hatch"], dash=(2.0, 1.2))
        _axis_bubble(sc, x_bub - 4.0, p[1], "ABCD"[i] if i < 4 else "?")

    agg = {}
    for cell in cells:
        a = agg.setdefault(cell["name"], [0, 0.0])
        a[0] += 1
        a[1] += cell["area"]
    agg_rows = [[k2, "%d" % v2[0], "%.1f" % v2[1]] for k2, v2 in agg.items()]
    agg_rows.append(["合计", "%d" % sum(v2[0] for v2 in agg.values()),
                     "%.1f" % sum(v2[1] for v2 in agg.values())])
    return {"bbox": (bx_lo - 31.0, by_lo, bx_hi, by_hi + 34.0),
            "agg": agg_rows, "ops": _op_rows(d, m, ent), "cells": cells,
            "wall": wall}


# ==========================================================================
# 三、剖面图
# ==========================================================================
def _cut_line(d):
    dw = d.get("drawings", {}) or {}
    pf = dw.get("section_from") or [0.0, 0.0]
    pt = dw.get("section_to") or [1.0, 0.0]
    return ((float(pf[0]), float(pf[1])), (float(pt[0]), float(pt[1])))


def _section_frame(d, m):
    """按 design.json 剖切线定方向，取穿洞口最近的一条横向剖切面。"""
    pf, pt = _cut_line(d)
    L = math.dist(pf, pt) or 1.0
    u = ((pt[0] - pf[0]) / L, (pt[1] - pf[1]) / L)
    nv = (-u[1], u[0])
    fp = design_io.as_pts(m["footprint"])
    cen = design_io.centroid(fp)
    uv = [(p[0] - cen[0]) * u[0] + (p[1] - cen[1]) * u[1] for p in fp]
    umin, umax = min(uv), max(uv)
    cu, best = 0.0, None
    for o in _openings_of(d, m["id"]):
        if o.get("type") != "window":
            continue
        p, q = o["p0"], o["p1"]
        if abs((q[0] - p[0]) * nv[0] + (q[1] - p[1]) * nv[1]) > 0.12:
            continue
        px, py = (p[0] + q[0]) / 2.0 - cen[0], (p[1] + q[1]) / 2.0 - cen[1]
        uu = px * u[0] + py * u[1]
        if not (umin + 0.5 < uu < umax - 0.5):
            continue
        if best is None or abs(uu - cu) < best:
            best = abs(uu - cu)
            cu = uu
    cut = (cen[0] + u[0] * cu, cen[1] + u[1] * cu)
    return cut, u, nv


def _section_mass(d):
    pf, pt = _cut_line(d)
    best = None
    for m in d["masses"]:
        fp = design_io.as_pts(m["footprint"])
        ts = design_io.segment_line_intersections(pf, pt, fp)
        ts = [t for t in ts if -0.001 <= t <= 1.001]
        if len(ts) < 2:
            continue
        span = max(ts[i + 1] - ts[i] for i in range(len(ts) - 1))
        if best is None or span > best[0]:
            best = (span, m)
    return best[1] if best else d["masses"][0]


def _ground_z(d):
    g = -0.15
    for lv in d["site"].get("levels", []):
        nm = str(lv.get("name", ""))
        if nm in ("室外地面", "室外地坪", "ground") or "室外" in nm:
            g = float(lv["elevation"])
    return g


def draw_section(sc, d, m, scale, region=REGION):
    """1-1 剖面：返回 {"bbox": 图面范围}。"""
    k = 1000.0 / scale
    fp = design_io.as_pts(m["footprint"])
    wall = _wall_thickness(d)
    lay = _layout(fp, wall)
    cut, u, nv = _section_frame(d, m)
    lh = design_io.level_height(d)
    st = max(1, design_io.mass_storeys(m, lh))
    base = float(m.get("base", 0.0))
    top = design_io.mass_top(m)
    g = _ground_z(d)

    sv = [(p[0] - cut[0]) * u[0] + (p[1] - cut[1]) * u[1] for p in fp]
    smin, smax = min(sv), max(sv)
    sc0 = (lay["P"](0.0, lay["c0"])[0] - cut[0]) * u[0] + \
          (lay["P"](0.0, lay["c0"])[1] - cut[1]) * u[1]
    sc1 = sc0 + CORR_W

    sill0, hgt0, win_w0 = 0.9, 2.4, 2.2
    for o in _openings_of(d, m["id"]):
        if o.get("type") == "window":
            sill0 = float(o.get("sill", 0.9))
            hgt0 = float(o.get("height", 2.4))
            win_w0 = float(o.get("width", 2.2))
            break

    vtop = top + PARAPET
    GY = ((region[1] + region[3]) / 2.0
          + ((vtop - g) - 3.2) * k / 2.0)
    cx0 = (region[0] + region[2]) / 2.0 - 25.0

    def X(s):
        return cx0 + (s - (smin + smax) / 2.0) * k

    def Y(z):
        return GY - (z - g) * k

    # ---- 地坪线与土体（斜填带按图面 13 mm 收窄，与比例无关） ----
    ex0, ex1 = X(smin - 6.0), X(smax + 6.0)
    gh = 13.0 / k
    sc.hatch([(ex0, Y(g)), (ex1, Y(g)), (ex1, Y(g - gh)), (ex0, Y(g - gh))],
             spacing=1.8, angle=45.0, color=C["hatch"], w=LW["hatch"])
    sc.line((ex0, Y(g)), (ex1, Y(g)), C["cut"], LW["outline"])
    sc.line((ex0, Y(g - gh)), (ex1, Y(g - gh)), C["hatch"], LW["detail"])

    # ---- 室内地坪（室内外高差） ----
    ix0, ix1 = X(smin), X(smax)
    sc.poly([(ix0, Y(g)), (ix1, Y(g)), (ix1, Y(0.0)), (ix0, Y(0.0))],
            C["concrete"], None)
    sc.line((ix0, Y(0.0)), (ix1, Y(0.0)), C["cut"], LW["secondary"])

    # ---- 楼板（150 厚，上下两条 0.35 线）+ 板下边梁（500 高，0.18 线弱化） ----
    levels_z = [base + i * lh for i in range(1, st)]
    levels_z.append(top)
    slabs = []
    for z in levels_z:
        slabs.append(z)
        zb, zc = z - SEC_SLAB, z - SEC_SLAB - SEC_BEAM
        sc.poly([(ix0, Y(zb)), (ix1, Y(zb)), (ix1, Y(z)), (ix0, Y(z))],
                C["concrete"], None)
        sc.line((ix0, Y(z)), (ix1, Y(z)), C["cut"], LW["secondary"])
        sc.line((ix0, Y(zb)), (ix1, Y(zb)), C["cut"], LW["secondary"])
        sc.line((X(smin), Y(zc)), (X(smax), Y(zc)), C["cut"], LW["detail"])
        for s in (smin, smax):
            sc.line((X(s), Y(zc)), (X(s), Y(zb)), C["cut"], LW["detail"])

    # ---- 剖切面以外可见：远侧柱列 + 按开间开窗（窗间留墙垛） ----
    def PS(a):
        p = lay["P"](a, lay["D"])
        return (p[0] - cut[0]) * u[0] + (p[1] - cut[1]) * u[1]

    fw0, fw1 = smin + wall, smax - wall
    bay_w = lay["L"] / float(lay["nb"])
    win_w = min(win_w0, bay_w - 2.0)
    for a in lay["axes"][1:-1]:
        s = PS(a)
        sc.poly([(X(s - COL_S / 2.0), Y(base)), (X(s + COL_S / 2.0), Y(base)),
                 (X(s + COL_S / 2.0), Y(top)), (X(s - COL_S / 2.0), Y(top))],
                C["cut"], None)
    for i in range(st):
        z0 = base + i * lh + sill0
        z1 = min(z0 + hgt0, base + i * lh + lh - SEC_SLAB)
        if z1 <= z0 + 0.05:
            continue
        for b in range(lay["nb"]):
            a0 = lay["axes"][b] + (bay_w - win_w) / 2.0
            s0, s1 = sorted((PS(a0), PS(a0 + win_w)))
            s0, s1 = max(s0, fw0), min(s1, fw1)
            if s1 - s0 < 0.4:
                continue
            sc.poly([(X(s0), Y(z0)), (X(s1), Y(z0)),
                     (X(s1), Y(z1)), (X(s0), Y(z1))],
                    C["glass"], C["water_e"], LW["detail"])
            xm = X((s0 + s1) / 2.0)
            sc.line((xm, Y(z0)), (xm, Y(z1)), C["water_e"], LW["detail"])

    # ---- 墙（剖切） ----
    wall_bands = [(smin, smin + wall), (smax - wall, smax), (sc0 - PART_T,
                                                                 sc0), (sc1, sc1 + PART_T)]
    for (s0, s1) in wall_bands:
        sc.poly([(X(s0), Y(0.0)), (X(s1), Y(0.0)), (X(s1), Y(top)),
                 (X(s0), Y(top))], C["cut"], None)

    # ---- 窗（剖切） ----
    sill, hgt = sill0, hgt0
    for (s0, s1) in wall_bands[:2]:
        for i in range(st):
            z0 = base + i * lh + sill
            z1 = z0 + hgt
            sc.poly([(X(s0), Y(z0)), (X(s1), Y(z0)), (X(s1), Y(z1)),
                     (X(s0), Y(z1))], C["glass"], C["cut"], LW["detail"])
            xm = X((s0 + s1) / 2.0)
            sc.line((xm, Y(z0)), (xm, Y(z1)), C["water_e"], LW["detail"])

    # ---- 女儿墙（屋面楼板已含在楼板循环中，此处只加 300 高女儿墙） ----
    for (s0, s1) in wall_bands[:2]:
        sc.poly([(X(s0), Y(top)), (X(s1), Y(top)),
                 (X(s1), Y(top + SEC_PARAPET)),
                 (X(s0), Y(top + SEC_PARAPET))], C["cut"], None)

    # ---- 柱 ----
    cw = COL_S
    for s in (sc0, sc1):
        sc.poly([(X(s - cw / 2.0), Y(0.0)), (X(s + cw / 2.0), Y(0.0)),
                 (X(s + cw / 2.0), Y(top - SEC_SLAB)),
                 (X(s - cw / 2.0), Y(top - SEC_SLAB))], C["cut"], None)

    # ---- 标高符号 ----
    lv_lines = [g, 0.0] + levels_z
    for z in lv_lines:
        if abs(z - g) < 1e-9:
            continue
        _level_symbol(sc, X(smin) - 15.0, Y(z), "%+.3f" % z)
        sc.line((X(smin) - 4.0, Y(z)), (X(smin), Y(z)), C["text_l"], LW["hatch"])
    xg, yg = X(smin) - 15.0, Y(g)                       # 室外地坪：数值左移避让 ±0.000
    sc.line((xg, yg), (xg + 11.0, yg), C["text_l"], LW["detail"])
    sc.poly([(xg - 1.7, yg), (xg + 1.7, yg), (xg, yg + 2.6)], C["paper"],
            C["text"], LW["detail"])
    sc.text(xg - 13.6, yg - 0.6, "%+.3f" % g, F_BODY, C["text"], "rt")
    sc.line((X(smin) - 4.0, Y(g)), (X(smin), Y(g)), C["text_l"], LW["hatch"])

    # ---- 层高 / 总高：短引出线 + 数值排在尺寸线外侧、逐段错开 ----
    seg_txt = "%.0f" % (lh * 1000.0)
    tot_txt = "%.0f" % ((top - 0.0) * 1000.0)
    XS = X(smax)
    c1 = XS + 10.0
    lab1 = c1 + 4.8
    c2 = lab1 + sc.text_width(seg_txt, F_BODY) + 2.6
    lab2 = c2 + 4.8
    for z in [0.0] + levels_z:
        sc.line((XS + 2.0, Y(z)), (c2 + 2.0, Y(z)), C["dash"], LW["hatch"])
    sc.line((c1, Y(top)), (c1, Y(0.0)), C["dash"], LW["detail"])
    sc.line((c2, Y(top)), (c2, Y(0.0)), C["dash"], LW["detail"])
    for z in [0.0] + levels_z:
        sc.tick((c1, Y(z)), C["dash"])
    for z in (0.0, top):
        sc.tick((c2, Y(z)), C["dash"])
    zprev = 0.0
    for z in levels_z:
        ya, yb = Y(z), Y(zprev)
        ym = (ya + yb) / 2.0
        sc.line((c1, ym), (lab1 - 1.0, ym), C["dash"], LW["detail"])
        sc.text(lab1, ym, "%.0f" % ((z - zprev) * 1000.0), F_BODY, C["dash"], "lm")
        zprev = z
    ym = (Y(0.0) + Y(top)) / 2.0
    sc.line((c2, ym), (lab2 - 1.0, ym), C["dash"], LW["detail"])
    sc.text(lab2, ym, tot_txt, F_BODY, C["dash"], "lm")
    draw2d.dim_h(sc, X(smin), X(smax), Y(g - 1.6) + 14.0,
                 "%.0f" % ((smax - smin) * 1000.0), ext=(Y(g), Y(g)),
                 color=C["dash"], size=F_BODY)

    # ---- 人体比例参照 1.7m ----
    hx = X((sc0 + sc1) / 2.0)
    _human(sc, hx, Y(0.0), k)
    draw2d.dim_v(sc, Y(0.0) - 1.7 * k, Y(0.0), hx + 9.0, "1700",
                 color=C["dash"], size=F_BODY)

    # ---- 引注 ----
    tips = []
    tips.append(("屋面 40 厚细石混凝土保护层", (X(smax - 1.4), Y(top + 0.1))))
    tips.append(("女儿墙 %d（含压顶）" % round(SEC_PARAPET * 1000),
                 (X(smax - wall / 2.0), Y(top + SEC_PARAPET - 0.02))))
    if len(slabs) > 1:
        tips.append(("现浇楼板 %d mm（上下双线）" % round(SEC_SLAB * 1000),
                     (X(smax - 3.0), Y(top - SEC_SLAB / 2.0))))
        tips.append(("板下边梁 %d 高（细线弱化）" % round(SEC_BEAM * 1000),
                     (X(smax - 3.0), Y(top - SEC_SLAB - SEC_BEAM / 2.0))))
    tips.append(("框架柱 500x500", (X(PS(lay["axes"][-2])), Y(base + lh * 2.8))))
    tips.append(("窗 C %.1f x %.1f（窗台 %.1f）" % (win_w, hgt0, sill0),
                 (X(PS(lay["axes"][-2] + bay_w / 2.0)),
                  Y(base + 2 * lh + sill0 + hgt0 / 2.0))))
    tips.append(("室内地坪 ±0.000", (X(smax - 2.0), Y(0.0))))
    tips.append(("室外地面 %.3f（室内外高差 %.3f）" % (g, -g), (X(smax - 1.0), Y(g))))

    elbow_x = lab2 + sc.text_width(tot_txt, F_BODY) + 5.0
    split = Y(g) - 2.5
    y_lab = Y(top + PARAPET) - 4.0
    for (txt, tip) in sorted([t for t in tips if t[1][1] < split],
                             key=lambda t: t[1][1]):
        y = max(y_lab, tip[1] + 2.0)
        _leader(sc, tip, (elbow_x, y), (elbow_x + 12.0, y), txt)
        y_lab = y + 7.4
    y_gnd = Y(g) - 4.6
    for (txt, tip) in reversed(sorted([t for t in tips if t[1][1] >= split],
                                     key=lambda t: t[1][1])):
        _leader(sc, tip, (elbow_x, y_gnd), (elbow_x + 12.0, y_gnd), txt)
        y_gnd -= 7.4

    # ---- 剖切位置示意 1:1000 ----
    _cut_diagram(sc, d, m, cut, u, nv)

    return {"bbox": (X(smin) - 22.0, Y(vtop) - 6.0, X(smax), Y(g) + 8.0),
            "mass": m, "level_height": lh, "storeys": st,
            "win": (win_w, hgt0, sill0)}


def _cut_diagram(sc, d, m, cut, u, nv, cx=738.0, cy=104.0, scale=1500, w=126.0):
    """剖切位置示意：用地边界 + 各体量 + 1-1 剖切线与视向。"""
    bd = design_io.as_pts(d["site"]["boundary"])
    bx0, by0, bx1, by1 = design_io.bbox(bd)
    T = draw2d.ToPaper(scale, cx, cy, (bx0 + bx1) / 2.0, (by0 + by1) / 2.0)
    sc.text(cx - w / 2.0, 54.0, "剖切位置示意 1:%d" % scale, F_BODY, C["text"], "lt")
    sc.line((cx - w / 2.0, 57.6), (cx + w / 2.0, 57.6), C["frame"], LW["secondary"])
    sc.poly([T(p) for p in bd], C["paper"], C["site"], LW["detail"])
    for mm in d["masses"]:
        pts = [T(p) for p in design_io.as_pts(mm["footprint"])]
        if mm["id"] == m["id"]:
            sc.poly(pts, C["concrete"], C["cut"], LW["secondary"])
        else:
            sc.poly(pts, C["paper"], C["dash"], LW["hatch"])
    sc.text(cx, cy, m["id"], F_NAME, C["text"], "cm")
    a0 = T((cut[0] - u[0] * 40.0, cut[1] - u[1] * 40.0))
    a1 = T((cut[0] + u[0] * 40.0, cut[1] + u[1] * 40.0))
    for aa in (a0, a1):
        sc.line((aa[0] - 3.0, aa[1]), (aa[0] + 3.0, aa[1]), C["cut"], LW["outline"])
        sc.line((aa[0], aa[1] - 3.0), (aa[0], aa[1] + 3.0), C["cut"], LW["outline"])
    sc.line(a0, a1, C["cut"], LW["outline"], dash=(6.0, 2.0))
    for t, sgn in ((0.0, -1.0), (1.0, 1.0)):
        p = (a0[0] + (a1[0] - a0[0]) * t, a0[1] + (a1[1] - a0[1]) * t)
        sc.arrow((p[0] - nv[0] * 6.0 * sgn, p[1] - nv[1] * 6.0 * sgn),
                 (p[0] + nv[0] * 6.0 * sgn, p[1] + nv[1] * 6.0 * sgn),
                 C["cut"], LW["detail"], 2.0)
    sc.text(a0[0] - 4.0, a0[1], "1", F_NAME, C["text"], "rm")


def _furniture(sc, name, d, met, info):
    """图例 / 设计说明 / 表：同一横带、同一起止线、同一行距，左对齐图框内边距。"""
    y = BAND_Y
    if name == "siteplan":
        draw_legend(sc, COLL_X, y, [
            ("用地红线", "dash", C["site"]),
            ("建筑退线", "dash", C["dash"]),
            ("新建建筑", "fill", C["concrete"]),
            ("道路 / 场地", "fill", C["road"]),
            ("硬质广场", "fill", C["plaza"]),
            ("绿地", "fill", C["green_l"]),
            ("水体", "fill", C["water"]),
            ("乔木", "tree", C["tree"]),
            ("等高线", "line", C["hatch"]),
            ("悬挑投影", "dash", C["dash"]),
        ], cols=2, colw=132.0)
        rows = [
            ["用地面积", "%.2f m2" % met["site_area"]],
            ["建筑基底", "%.2f m2" % met["footprint_area"]],
            ["总建筑面积", "%.2f m2" % met["building_area"]],
            ["容积率", "%.4f" % (met["far"] or 0.0)],
            ["建筑密度", "%.2f %%" % ((met["density"] or 0.0) * 100)],
            ["绿地率", "%.2f %%" % ((met["greenRatio"] or 0.0) * 100)],
            ["绿地面积", "%.2f m2" % met["green_area"]],
            ["道路 / 广场", "%.1f / %.1f m2" % (met["road_area"], met["plaza_area"])],
            ["水面", "%.2f m2" % met["water_area"]],
            ["限高 / 层数", "%.2f m / %dF" % (met["max_height"], met["max_stories"])],
        ]
        draw_table(sc, 320.0, y, ["项目", "指标"], rows, [104.0, 72.0], rowh=5.2,
                   title="技术经济指标（design_io.compute_metrics 实算）")
        draw_notes(sc, 510.0, y, [
            "1. 图中尺寸单位米，标高单位米；粗虚线为用地红线，细虚线为建筑退线控制线。",
            "2. 用地面积 / 容积率 / 绿地率取自 design_io.compute_metrics，未另行估算。",
            "3. 建筑统一取中性色并按体量高度分级明度；材料色仅用于立面与材料分析。",
            "4. 建筑外圈细虚线为悬挑投影，坡屋顶以屋脊线加投影线表示。",
            "5. 乔木符号优先取 design.json 的 tree 图元，未声明时按绿地范围示意布点。",
        ], w=290.0)
        sb = d["site"].get("setbacks", {}) or {}
        sc.text(510.0, y + 44.0, "退线：前 %.1f / 后 %.1f / 左 %.1f / 右 %.1f m"
                % (sb.get("front", 0), sb.get("back", 0), sb.get("left", 0), sb.get("right", 0)),
                F_BODY, C["text_l"], "lt")
        sc.text(510.0, y + 50.2, d["site"].get("datum_note", ""), F_BODY, C["text_l"], "lt")
        sc.text(510.0, y + 56.4, "用地面积 %.2f m2   容积率 %.3f   绿地率 %.2f%%"
                % (met["site_area"], met["far"] or 0.0, (met["greenRatio"] or 0.0) * 100),
                F_BODY, C["text"], "lt")
    elif name == "plan":
        draw_legend(sc, COLL_X, y, [
            ("剖到的外墙 / 隔墙", "fill", C["cut"]),
            ("玻璃（A-GLAZ）", "fill", C["glass"]),
            ("窗玻璃线（洞口内）", "line", C["water_e"]),
            ("结构柱 500x500", "fill", C["cut"]),
            ("楼梯 / 踏步", "line", C["cut"]),
            ("轴线 / 轴号", "dash", C["dash"]),
        ], cols=2, colw=132.0)
        draw_notes(sc, 320.0, y, [
            "1. 尺寸单位毫米，标高单位米；外墙厚按 design.json 声明绘制。",
            "2. 窗洞按 design.json opening 展开：按真实墙厚围合洞口，玻璃单线居中（LW detail）。",
            "3. 内隔墙、柱网与轴号由 footprint 与 %g m 模数派生，柱 500x500 待结构专业确认。" % BAY,
            "4. 门洞净宽按房间开间确定，门扇按 90 度开启弧示意。",
            "5. 房间面积为净面积，建筑面积以 design_io 实算为准。",
        ], w=204.0)
        draw_table(sc, 532.0, y, ["编号", "洞口宽 x 高", "数量"], info["ops"],
                   [40.0, 66.0, 34.0], rowh=5.4, title="门窗表（design.json opening）")
        draw_table(sc, 686.0, y, ["房间", "间数", "面积 m2"], info["agg"],
                   [44.0, 24.0, 42.0], rowh=5.4, title="本层房间面积")
    else:
        draw_legend(sc, COLL_X, y, [
            ("剖到的墙 / 板 / 柱", "fill", C["cut"]),
            ("楼板（150 厚双线）", "line", C["cut"]),
            ("板下边梁（500 高细线）", "line", C["text_l"]),
            ("窗（剖切面）", "fill", C["glass"]),
            ("地坪线（加粗）", "line", C["cut"]),
            ("标高符号 ▽", "line", C["text_l"]),
            ("人体比例参照 1.7 m", "line", C["text_l"]),
        ], cols=2, colw=132.0)
        draw_notes(sc, 320.0, y, [
            "1. 剖切位置见本图上方 1:1500 示意，剖切面穿过外墙洞口。",
            "2. 楼板 %d、板下边梁 %d 高、柱 500x500 为示意结构断面，梁只画细线轮廓；"
            "窗按 opening（%.1f 宽 x %.1f 高、窗台 %.1f）逐开间居中开洞。"
            % ((round(SEC_SLAB * 1000), round(SEC_BEAM * 1000))
               + tuple(info.get("win") or (2.2, 2.4, 0.9))),
            "3. 标高单位米；▽ 为标高符号，加粗线为室外地坪线。",
            "4. 层高取 design.json project.level_height；标高表只列本图所剖体量，不含其他栋。",
            "5. 人体比例参照 1.7 m 用于校核门窗与层高的视觉尺度。",
        ], w=254.0)
        m = info.get("mass") or _section_mass(d)
        lh = design_io.level_height(d)
        st = max(1, design_io.mass_storeys(m, lh))
        base = float(m.get("base", 0.0))
        top = design_io.mass_top(m)
        g = _ground_z(d)
        lrows = []
        if g < base - 1e-9:
            lrows.append(["室外地坪", "%+.3f" % g])
        for i in range(st):
            lrows.append(["%dF" % (i + 1), "%+.3f" % (base + i * lh)])
        lrows.append(["屋面", "%+.3f" % top])
        draw_table(sc, 590.0, y, ["设计标高", "标高 m"], lrows, [96.0, 94.0],
                   rowh=5.4, title="%s 栋竖向标高（%dF，层高 %.3f m）"
                   % (m["id"], st, lh))


# ==========================================================================
# 组装
# ==========================================================================
def _sheet_no(d, name):
    base = (d.get("project") or {}).get("drawing_number") or "AE"
    return "%s-%s" % (base, SHEET_NO[name])


def build_sheet(name, design, out_dir=None):
    """组装一张图纸：返回 Scene，并按 out_dir 落盘 SVG。"""
    if name not in SHEETS:
        raise ValueError("未知图纸 %r，可选 %s" % (name, "/".join(SHEETS)))
    d = design
    sc = _scene()
    met = design_io.compute_metrics(d)
    lh = design_io.level_height(d)
    draw_border(sc)

    if name == "siteplan":
        sbd = design_io.bbox(design_io.as_pts(d["site"]["boundary"]))
        scale, req = _fit_scale(d, name, sbd[2] - sbd[0], sbd[3] - sbd[1])
        title, disc = "总平面图", "总图"
        sub = "用地红线 · 建筑落位 · 道路广场与绿地 · 1:%d" % scale
        info = draw_siteplan(sc, d, met, scale, REGION)
    elif name == "plan":
        m, li, lv = _plan_target(d)
        fbd = design_io.bbox(design_io.as_pts(m["footprint"]))
        scale, req = _fit_scale(d, name, fbd[2] - fbd[0], fbd[3] - fbd[1])
        title = "%s %s平面图" % (m.get("name", m["id"]), lv.get("name", ""))
        sub = "本层标高 %+.3f m · 层高 %.3f m · 1:%d" % (float(lv["elevation"]), lh, scale)
        disc = "建筑"
        info = draw_plan(sc, d, m, li, lv, met, scale, REGION)
    else:
        m = _section_mass(d)
        fbd = design_io.bbox(design_io.as_pts(m["footprint"]))
        scale, req = _fit_scale(d, name, fbd[2] - fbd[0],
                                design_io.mass_top(m) + PARAPET - _ground_z(d) + 3.2)
        title, disc = "1-1 剖面图", "建筑"
        sub = "%s · 层高 %.3f m · 檐口 %.2f m · 1:%d" % (
            m.get("name", m["id"]), lh, design_io.mass_top(m), scale)
        info = draw_section(sc, d, m, scale, REGION)

    bbox = info["bbox"]
    draw_heading(sc, title, sub)
    if name in NORTH_SLOT:
        ncx, ncy = NORTH_SLOT[name]
    else:
        ncx = min(bbox[2] + 11.0, IN_X1 - 13.0)
        ncy = max(bbox[1] + 11.0, REG_Y0 + 12.0)
    draw_north(sc, ncx, ncy, 8.5,
               float((d.get("site") or {}).get("north_deg", 0)))
    draw_scale(sc, max(COLL_X, bbox[0] - 74.0), BAND_Y - 15.0, scale, 40.0, 4)
    if req and req != scale:
        sc.text(IN_X1, BAND_Y - 4.0,
                "比例已按图幅优选（原请求 1:%d），本图 1:%d" % (req, scale),
                F_BODY, C["text_l"], "rt")
    _furniture(sc, name, d, met, info)
    draw_title_block(sc, d, {"title": title, "scale": scale,
                             "no": _sheet_no(d, name), "disc": disc})
    sc.meta = {"sheet": name, "paper": "A1", "scale": scale,
               "unit": "mm", "project": d["project"].get("name", ""),
               "title": title}

    if out_dir:
        if not os.path.isdir(out_dir):
            os.makedirs(out_dir)
        path = os.path.join(out_dir, name + ".svg")
        svg_backend.write_svg(sc, path, "%s %s" % (title, d["project"].get("name", "")))
    return sc


def build_all(design, out_dir=None, sheets=SHEETS):
    return {name: build_sheet(name, design, out_dir) for name in sheets}


def build_sheets(design, out_dir=None, sheets=SHEETS):
    """返回 {sheet_name: Scene}，供 to_pdf.py 直接复用同一套组装（同一 Scene 出 SVG/PDF/PNG）。"""
    return {name: build_sheet(name, design, out_dir) for name in sheets}


def main(argv=None):
    ap = argparse.ArgumentParser(description="design.json -> A1 图纸 SVG（总平面/平面/剖面）")
    ap.add_argument("design")
    ap.add_argument("--out", default="out", help="SVG 输出目录")
    ap.add_argument("--sheets", default=",".join(SHEETS))
    a = ap.parse_args(argv)

    d = design_io.load_design(a.design)
    errs, warns = design_io.validate(d)
    for w in warns:
        print("警告: %s" % w, file=sys.stderr)
    if errs:
        for e in errs:
            print("错误: %s" % e, file=sys.stderr)
        return 2

    names = [s.strip() for s in a.sheets.split(",") if s.strip()]
    for name in names:
        sc = build_sheet(name, d, a.out)
        polys = sum(1 for it in sc.items if it["t"] in ("fill", "stroke", "text"))
        print("%-9s %5d 图元 (%d 描边/填充 + %d 文字)  %s"
              % (name, len(sc.items), polys - sum(1 for it in sc.items if it["t"] == "text"),
                 sum(1 for it in sc.items if it["t"] == "text"),
                 os.path.join(a.out, name + ".svg")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
