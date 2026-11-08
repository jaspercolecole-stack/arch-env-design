#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""design_io.py -- design.json 的读写 / 校验 / 指标计算 / 共享几何基元。

本模块同时承担「核心几何」的角色：其他脚本（dxf_export / gltf_export /
draw2d）都从这里 import 多边形与折线基元，避免同一套公式写三遍。

CLI
  python design_io.py check   <design.json> [--json]
  python design_io.py metrics <design.json> [--json]
  python design_io.py backfill <design.json>     # 把算出的 metrics 写回文件
  python design_io.py summarize <design.json>

指标定义（全部真算，不算的宁可留空并给 warnings）
  site_area        用地边界多边形面积（shoelace）
  footprint_area   各 mass 基底多边形面积之和
  building_area    Σ 基底面积 × 该 mass 层数（层数由 height/level_height 推，可被 masses[].storeys 覆盖）
  stories          面积加权平均层数 = building_area / footprint_area
  far              容积率 = building_area / site_area
  density          建筑密度 = footprint_area / site_area
  greenRatio       绿地率 = green_area / site_area
  green_area       site_area - footprint_area - 硬化地面(道路/广场) - 水面
  max_height       max(base + height + roof_rise)
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

# Windows 控制台默认 GBK，中文/² 之类字符会 UnicodeEncodeError。所有脚本统一前置。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCHEMA_NAME = "design.schema.json"
PROJECT_TYPES = ("住宅", "公建", "校园", "街区", "滨水", "公园")
STANDARDS = ("CN", "INTL")
ROOFS = ("flat", "gable", "shed")
ELEMENT_KINDS = ("wall", "slab", "column", "opening", "path", "tree",
                 "water", "plaza", "green", "contour", "stair")
DEFAULT_LEVEL_HEIGHT = 3.6
NOTE_KEY_SUFFIXES = ("_note", "_notice", "_remark")
GROUND_ELEMENTS_HINT = "地面要素请写进顶层 elements，kind=path/water/plaza/green"

EPS = 1e-9


# --------------------------------------------------------------------------
# 几何基元
# --------------------------------------------------------------------------
def as_pts(seq):
    """[[x,y],...] -> [(float,float),...]"""
    return [(float(p[0]), float(p[1])) for p in seq]


def shoelace(pts):
    """有符号面积，CCW 为正。"""
    n = len(pts)
    s = 0.0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s * 0.5


def poly_area(pts):
    return abs(shoelace(pts))


def poly_perimeter(pts):
    n = len(pts)
    return sum(math.dist(pts[i], pts[(i + 1) % n]) for i in range(n))


def polyline_length(pts):
    return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def bbox(points):
    """points 可以是 [[x,y],...] 或点列表的列表。"""
    xs, ys = [], []
    stack = list(points)
    while stack:
        item = stack.pop()
        if item is None:
            continue
        if isinstance(item, (list, tuple)) and len(item) == 2 and all(
                isinstance(v, (int, float)) for v in item):
            xs.append(float(item[0]))
            ys.append(float(item[1]))
        else:
            stack.extend(item)
    if not xs:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs), min(ys), max(xs), max(ys))


def centroid(pts):
    a = shoelace(pts)
    if abs(a) < EPS:
        n = len(pts)
        return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)
    cx = cy = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        cross = x1 * y2 - x2 * y1
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    return (cx / (6.0 * a), cy / (6.0 * a))


def normalize_ccw(pts):
    pts = as_pts(pts)
    return pts if shoelace(pts) >= 0 else list(reversed(pts))


def point_in_poly(pt, pts):
    """射线法，含边界判定为真。"""
    x, y = pt
    n = len(pts)
    inside = False
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        if _on_segment((x, y), (x1, y1), (x2, y2)):
            return True
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if xi > x:
                inside = not inside
    return inside


def _on_segment(p, a, b, tol=1e-9):
    cross = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
    if abs(cross) > tol:
        return False
    return (min(a[0], b[0]) - tol <= p[0] <= max(a[0], b[0]) + tol and
            min(a[1], b[1]) - tol <= p[1] <= max(a[1], b[1]) + tol)


def _seg_cross(p1, p2, p3, p4):
    """真交叉（不含端点相接）。"""
    d1 = (p4[0] - p3[0]) * (p1[1] - p3[1]) - (p4[1] - p3[1]) * (p1[0] - p3[0])
    d2 = (p4[0] - p3[0]) * (p2[1] - p3[1]) - (p4[1] - p3[1]) * (p2[0] - p3[0])
    d3 = (p2[0] - p1[0]) * (p3[1] - p1[1]) - (p2[1] - p1[1]) * (p3[0] - p1[0])
    d4 = (p2[0] - p1[0]) * (p4[1] - p1[1]) - (p2[1] - p1[1]) * (p4[0] - p1[0])
    return ((d1 > EPS and d2 < -EPS) or (d1 < -EPS and d2 > EPS)) and \
           ((d3 > EPS and d4 < -EPS) or (d3 < -EPS and d4 > EPS))


def poly_self_intersects(pts):
    n = len(pts)
    if n < 4:
        return False
    for i in range(n):
        a1, a2 = pts[i], pts[(i + 1) % n]
        for j in range(i + 1, n):
            if j == i:
                continue
            if (j + 1) % n == i or (i + 1) % n == j:
                continue
            b1, b2 = pts[j], pts[(j + 1) % n]
            if _seg_cross(a1, a2, b1, b2):
                return True
    return False


def _line_line(p1, d1, p2, d2):
    """两条参数直线交点；平行返回 None。"""
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-12:
        return None
    t = ((p2[0] - p1[0]) * d2[1] - (p2[1] - p1[1]) * d2[0]) / den
    return (p1[0] + d1[0] * t, p1[1] + d1[1] * t)


def offset_polygon(pts, dist, miter_limit=6.0):
    """向内偏移（dist>0 表示收缩）。CCW 入参，非法则返回 []。

    用于平面图的墙体双线；凹角做尖角(miter)处理并限长，避免炸开。
    """
    p = normalize_ccw(pts)
    n = len(p)
    if n < 3 or dist <= 0:
        return p
    segs = []
    for i in range(n):
        a, b = p[i], p[(i + 1) % n]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        if L < 1e-9:
            continue
        nx, ny = -dy / L, dx / L          # CCW 的左法向 = 指向内部
        segs.append(((a[0] + nx * dist, a[1] + ny * dist), (dx, dy)))
    m = len(segs)
    if m < 3:
        return []
    out = []
    for i in range(m):
        pa, da = segs[i]
        pb, db = segs[(i + 1) % m]
        hit = _line_line(pa, da, pb, db)
        if hit is None:
            out.append(pa)
            continue
        src = p[(i + 1) % n]
        if math.dist(hit, src) > miter_limit * dist:
            k = miter_limit * dist / max(math.dist(hit, src), EPS)
            hit = (src[0] + (hit[0] - src[0]) * k, src[1] + (hit[1] - src[1]) * k)
        out.append(hit)
    if poly_area(out) <= EPS or poly_self_intersects(out):
        return []
    return out


def polyline_offset(pts, dist):
    """折线单侧偏移（dist>0 为左法向），用于道路轮廓。"""
    p = as_pts(pts)
    n = len(p)
    if n < 2:
        return p
    off = []
    for i in range(n - 1):
        a, b = p[i], p[i + 1]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        if L < 1e-9:
            continue
        off.append(((a[0] - dy / L * dist, a[1] + dx / L * dist), (dx, dy)))
    if not off:
        return p
    out = [off[0][0]]
    for i in range(len(off) - 1):
        pa, da = off[i]
        pb, db = off[i + 1]
        hit = _line_line(pa, da, pb, db)
        out.append(hit if hit else pa)
    last_a, last_d = off[-1]
    out.append((last_a[0] + last_d[0], last_a[1] + last_d[1]))
    return out


def triangulate(pts):
    """耳切法三角化（支持凹多边形，不支持自相交）。

    返回 index 三元组列表，索引对应 pts 的下标。
    """
    p = normalize_ccw(pts)
    n = len(p)
    if n < 3:
        return []
    idx = list(range(n))
    tris = []
    guard = 0
    while len(idx) > 3 and guard < 4 * n * n:
        guard += 1
        ear = False
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = p[i0], p[i1], p[i2]
            if _cross(a, b, c) <= 1e-12:
                continue
            bad = False
            for j in idx:
                if j in (i0, i1, i2):
                    continue
                if _in_tri(p[j], a, b, c):
                    bad = True
                    break
            if bad:
                continue
            tris.append((i0, i1, i2))
            idx.pop(k)
            ear = True
            break
        if not ear:
            break
    if len(idx) == 3:
        tris.append((idx[0], idx[1], idx[2]))
    return tris


def _cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _in_tri(p, a, b, c):
    d1 = _cross(a, b, p)
    d2 = _cross(b, c, p)
    d3 = _cross(c, a, p)
    has_neg = (d1 < -EPS) or (d2 < -EPS) or (d3 < -EPS)
    has_pos = (d1 > EPS) or (d2 > EPS) or (d3 > EPS)
    return not (has_neg and has_pos)


def segment_line_intersections(p0, p1, poly):
    """线段 p0->p1 与多边形求交，返回按参数 t 排序的 t 值列表（t in [0,1]）。"""
    d = (p1[0] - p0[0], p1[1] - p0[1])
    ts = []
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        e = (b[0] - a[0], b[1] - a[1])
        den = d[0] * e[1] - d[1] * e[0]
        if abs(den) < 1e-12:
            continue
        wx = a[0] - p0[0]
        wy = a[1] - p0[1]
        t = (wx * e[1] - wy * e[0]) / den
        u = (wx * d[1] - wy * d[0]) / den
        if -EPS <= u <= 1 + EPS:
            ts.append(t)
    ts = sorted(ts)
    # 合并重复点（过顶点时会出现两个几乎相同的 t）
    merged = []
    for t in ts:
        if not merged or abs(t - merged[-1]) > 1e-6:
            merged.append(t)
    return merged


# --------------------------------------------------------------------------
# 读写
# --------------------------------------------------------------------------
def load_design(path):
    p = Path(path)
    if not p.exists():
        raise SystemExit("design.json 不存在: %s" % p)
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise SystemExit("design.json 顶层必须是对象: %s" % p)
    return data


def dump_design(design, path):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(design, f, ensure_ascii=False, indent=2)
        f.write("\n")


# --------------------------------------------------------------------------
# 派生几何
# --------------------------------------------------------------------------
def level_height(design):
    v = design.get("project", {}).get("level_height", DEFAULT_LEVEL_HEIGHT)
    return float(v) if v and float(v) > 0 else DEFAULT_LEVEL_HEIGHT


def mass_storeys(mass, lh):
    if mass.get("storeys"):
        return max(1, int(mass["storeys"]))
    return max(1, int(round(float(mass.get("height", 0)) / lh)))


def mass_top(mass):
    return float(mass.get("base", 0.0)) + float(mass.get("height", 0.0)) + \
        float(mass.get("roof_rise", 0.0) or 0.0)


def path_half_width(el):
    return float(el.get("width", 1.0)) * 0.5


def expand_openings(design):
    """把参数式 opening 展开成显式线段。

    返回 [{'host','p0','p1','sill','height','level','type','width'}]
    level 为 None 表示各层重复。
    """
    out = []
    masses = {m["id"]: m for m in design.get("masses", [])}
    for el in design.get("elements", []):
        if el.get("kind") != "opening":
            continue
        rec = {
            "host": el.get("host"),
            "sill": float(el.get("sill", 0.9) or 0.0),
            "height": float(el.get("height", 2.1) or 2.1),
            "level": el.get("level"),
            "type": el.get("type", "window"),
        }
        if el.get("points"):
            p = as_pts(el["points"])
            rec["p0"], rec["p1"] = p[0], p[1]
            rec["width"] = math.dist(p[0], p[1])
            out.append(rec)
            continue
        host = masses.get(el.get("host"))
        if host is None:
            continue
        fp = as_pts(host["footprint"])
        n = len(fp)
        e = int(el.get("edge", 0)) % n
        a, b = fp[e], fp[(e + 1) % n]
        L = math.dist(a, b)
        cnt = max(1, int(el.get("count", 1)))
        w = float(el.get("width", 1.5))
        margin = float(el.get("margin", 0.0))
        span = L - 2 * margin
        if span <= 0:
            continue
        step = span / cnt
        ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
        for i in range(cnt):
            c = margin + (i + 0.5) * step
            s0 = max(0.0, c - w / 2)
            s1 = min(L, c + w / 2)
            p0 = (a[0] + ux * s0, a[1] + uy * s0)
            p1 = (a[0] + ux * s1, a[1] + uy * s1)
            r = dict(rec)
            r.update({"p0": p0, "p1": p1, "width": s1 - s0})
            out.append(r)
    return out


def roof_top_at(mass, x, y):
    """给定平面点，返回该处屋面顶标高（含坡屋顶）。"""
    base = float(mass.get("base", 0.0))
    top = base + float(mass.get("height", 0.0))
    rise = float(mass.get("roof_rise", 0.0) or 0.0)
    roof = mass.get("roof", "flat")
    if roof == "flat" or rise <= 0:
        return top
    fp = as_pts(mass["footprint"])
    x0, y0, x1, y1 = bbox(fp)
    w, h = max(x1 - x0, EPS), max(y1 - y0, EPS)
    if roof == "gable":
        if w >= h:
            d = abs(y - (y0 + h / 2)) / (h / 2)
        else:
            d = abs(x - (x0 + w / 2)) / (w / 2)
    else:  # shed：沿短轴单向起坡
        if w >= h:
            d = (y1 - y) / h if h > EPS else 0.0
        else:
            d = (x1 - x) / w if w > EPS else 0.0
    d = min(1.0, max(0.0, d))
    return top + rise * (1.0 - d) if roof == "gable" else top + rise * d


def site_hardscape_areas(design):
    """道路（按中心线长 × 宽）与广场面积。"""
    road = 0.0
    plaza = 0.0
    for el in design.get("elements", []):
        k = el.get("kind")
        if k == "path":
            road += polyline_length(as_pts(el["points"])) * float(el.get("width", 0))
        elif k == "plaza":
            plaza += poly_area(as_pts(el["polygon"]))
    return road, plaza


def water_area(design):
    return sum(poly_area(as_pts(el["polygon"]))
               for el in design.get("elements", []) if el.get("kind") == "water")


def green_area_explicit(design):
    return sum(poly_area(as_pts(el["polygon"]))
               for el in design.get("elements", []) if el.get("kind") == "green")


# --------------------------------------------------------------------------
# 未知键 / 越界检查（静默失败防线）
#   背景：site.roads、site.green 这类直觉写法既不报错也不会被任何代码读取，
#   图纸上什么都没画、绿地率还被算高。这两项检查只追加 warnings，
#   不进 errors，也不改变 ok 判定。
# --------------------------------------------------------------------------
def _schema_file():
    """design.schema.json 与本文件同级（运行时定位，不写死绝对路径）。"""
    return Path(__file__).resolve().parent / SCHEMA_NAME


def schema_whitelist():
    """从 schema 的 properties 取白名单。

    返回 {"top","project","site","masses","elements"} 五个键名集合；
    schema 读不到或结构不符（白名单缺失）时返回 None，调用方退化成跳过检查。
    """
    try:
        with open(_schema_file(), "r", encoding="utf-8") as f:
            props = json.load(f).get("properties")
    except Exception:
        return None
    if not isinstance(props, dict) or not props:
        return None
    out = {"top": set(props)}
    for name in ("project", "site"):
        node = props.get(name)
        sub = node.get("properties") if isinstance(node, dict) else None
        if not isinstance(sub, dict) or not sub:
            return None
        out[name] = set(sub)
    for name in ("masses", "elements"):
        node = props.get(name)
        items = node.get("items") if isinstance(node, dict) else None
        if not isinstance(items, dict):
            return None
        keys = set(items.get("properties") or {})
        for branch in items.get("oneOf") or []:       # elements 按 kind 分成 11 个分支
            if isinstance(branch, dict) and isinstance(branch.get("properties"), dict):
                keys |= set(branch["properties"])
        if not keys:
            return None
        out[name] = keys
    return out


def module_read_keys():
    """scripts/ 下其它模块以字符串字面量引用的键名：确实有代码在读，保持静默。

    不维护第二份手写白名单——随代码库自动推导；本文件自身跳过。
    """
    keys = set()
    here = Path(__file__).resolve()
    for py in sorted(here.parent.parent.rglob("*.py")):
        try:
            if py.resolve() == here:
                continue
            text = py.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        keys.update(re.findall(r"""["']([A-Za-z_][A-Za-z0-9_]*)["']""", text))
    return keys


def _quiet_key(name, allowed, read_keys):
    """不需要告警：schema 已定义 / 其它模块在读 / *_note 之类的说明性备注。"""
    if name in allowed:
        return True
    if not isinstance(name, str):
        return False
    low = name.lower()
    if low in ("note", "notice") or low.endswith(NOTE_KEY_SUFFIXES):
        return True
    return name in read_keys


def check_unknown_keys(design, warn):
    """以 schema 的 properties 为白名单，报出「写了但没人读」的键。"""
    wl = schema_whitelist()
    if wl is None:
        warn("design.schema.json 读不到或结构不符，已跳过未知键检查（写错位置的字段不会被发现）")
        return
    read_keys = module_read_keys()

    for k in design:
        if not _quiet_key(k, wl["top"], read_keys):
            warn("未知顶层键 %r（不在 design.schema.json 中，本脚本不会读取它）" % k)

    targets = [("project", design.get("project"), "project", "project", ""),
               ("site", design.get("site"), "site", "site", "；" + GROUND_ELEMENTS_HINT)]
    for i, m in enumerate(design.get("masses") or []):
        targets.append(("masses[%d]" % i, m, "masses", "mass", ""))
    for i, el in enumerate(design.get("elements") or []):
        targets.append(("elements[%d]" % i, el, "elements", "element", ""))
    for where, obj, scope, label, tail in targets:
        if not isinstance(obj, dict):
            continue
        for k in obj:
            if _quiet_key(k, wl[scope], read_keys):
                continue
            if scope in ("masses", "elements"):
                tail = "；可用子键：" + "/".join(sorted(wl[scope]))
            warn("%s 出现未知键 %r（不在 design.schema.json 的 %s 中，本脚本不会读取它）%s"
                 % (where, k, label, tail))


def _element_outline(el):
    """图元的平面轮廓：polygon 直接用；path 用 points+width 偏移成闭合路幅。"""
    try:
        pol = el.get("polygon")
        if isinstance(pol, list) and len(pol) >= 3:
            return as_pts(pol)
        if el.get("kind") == "path":
            pts, w = el.get("points"), el.get("width")
            if isinstance(pts, list) and len(pts) >= 2 and isinstance(w, (int, float)):
                half = float(w) * 0.5
                ring = list(polyline_offset(pts, half)) + \
                    list(reversed(polyline_offset(pts, -half)))
                if len(ring) >= 3:
                    return ring
    except Exception:                    # 几何写坏了：交给 validate 的 errors，不在这里崩
        return None
    return None


def _edge_hit(p, q, a, b):
    return _line_line(p, (q[0] - p[0], q[1] - p[1]), a, (b[0] - a[0], b[1] - a[1]))


def _clip_convex(subject, clip):
    """Sutherland–Hodgman：用凸多边形 clip 裁剪 subject。"""
    out = list(subject)
    n = len(clip)
    for i in range(n):
        a, b = clip[i], clip[(i + 1) % n]
        inp, out = out, []
        if not inp:
            break
        for j in range(len(inp)):
            cur, prv = inp[j], inp[j - 1]
            sc, sp = _cross(a, b, cur), _cross(a, b, prv)
            if sc >= 0:
                if sp < 0:
                    hit = _edge_hit(prv, cur, a, b)
                    if hit is not None:
                        out.append(hit)
                out.append(cur)
            elif sp >= 0:
                hit = _edge_hit(prv, cur, a, b)
                if hit is not None:
                    out.append(hit)
    return out


def _poly_intersection_area(subject, clip):
    """subject ∩ clip 的面积。

    用真多边形裁剪（不是「顶点在外比例×面积」那种近似）：clip 允许凹多边形，
    先 triangulate() 耳切成三角形，再逐三角形做 Sutherland–Hodgman 凸裁剪累加。
    三角化不完整（面积极不闭合）时返回 None，调用方跳过该图元而不是乱报。
    """
    cc = normalize_ccw(clip)
    tris = triangulate(cc)
    if not tris:
        return None
    tri_area = sum(poly_area([cc[i0], cc[i1], cc[i2]]) for (i0, i1, i2) in tris)
    trim_area = poly_area(cc)
    if abs(tri_area - trim_area) > max(1e-6, trim_area * 1e-6):
        return None
    total = 0.0
    for (i0, i1, i2) in tris:
        piece = _clip_convex(subject, [cc[i0], cc[i1], cc[i2]])
        if len(piece) >= 3:
            total += poly_area(piece)
    return total


def check_element_bounds(design, warn):
    """elements 里带 polygon（water/plaza/green…）或 points+width（path）的图元
    是否落在 site.boundary 内。越界面积用真裁剪切出。"""
    site = design.get("site")
    if not isinstance(site, dict):
        return
    bd = site.get("boundary")
    if not isinstance(bd, list) or len(bd) < 3:
        return
    try:
        boundary = as_pts(bd)
    except Exception:
        return
    boundary_area = poly_area(boundary)
    if boundary_area <= 1e-6 or poly_self_intersects(boundary):
        return                            # 红线本身不合法：由 validate 的 errors 报
    for i, el in enumerate(design.get("elements") or []):
        if not isinstance(el, dict):
            continue
        outline = _element_outline(el)
        if outline is None:
            continue
        area = poly_area(outline)
        if area <= 1e-6:
            continue
        inside = _poly_intersection_area(outline, boundary)
        if inside is None:
            continue
        name = el.get("name") or el.get("kind") or "-"
        if not point_in_poly(centroid(outline), boundary):
            warn("elements[%d]（%s）几何中心在用地红线外" % (i, name))
        elif area - inside > max(1e-6, area * 1e-4):
            warn("elements[%d]（%s）部分越出用地红线（越界面积约 %.1f m2）"
                 % (i, name, area - inside))


# --------------------------------------------------------------------------
# 校验
# --------------------------------------------------------------------------
def validate(design):
    errors, warnings = [], []

    def err(m):
        errors.append(m)

    def warn(m):
        warnings.append(m)

    # 静默失败防线（只加 warnings，不动 errors / ok）：先跑，避免后面的 early return 吞掉。
    check_unknown_keys(design, warn)
    check_element_bounds(design, warn)

    for key in ("project", "site", "masses", "materials"):
        if key not in design:
            err("缺少顶层字段 %r" % key)
    if errors:
        return errors, warnings

    pr = design["project"]
    for key in ("name", "type", "standards", "units"):
        if key not in pr:
            err("project 缺少字段 %r" % key)
    if not str(pr.get("name", "")).strip():
        err("project.name 为空")
    if pr.get("type") not in PROJECT_TYPES:
        err("project.type 必须是 %s 之一，实际 %r" % ("/".join(PROJECT_TYPES), pr.get("type")))
    if pr.get("standards") not in STANDARDS:
        err("project.standards 必须是 CN 或 INTL，实际 %r" % pr.get("standards"))
    if pr.get("units") != "m":
        err("project.units 必须为 'm'，实际 %r" % pr.get("units"))

    st = design["site"]
    for key in ("boundary", "north_deg", "setbacks", "levels"):
        if key not in st:
            err("site 缺少字段 %r" % key)
    if "boundary" in st:
        bd = st["boundary"]
        if not isinstance(bd, list) or len(bd) < 3:
            err("site.boundary 至少需要 3 个点")
        else:
            bpts = as_pts(bd)
            if poly_area(bpts) <= 1e-6:
                err("site.boundary 面积为 0")
            if poly_self_intersects(bpts):
                err("site.boundary 自相交")
    if "setbacks" in st:
        sb = st["setbacks"]
        for k in ("front", "back", "left", "right"):
            if k not in sb:
                err("site.setbacks 缺少 %r" % k)
            elif not isinstance(sb[k], (int, float)) or sb[k] < 0:
                err("site.setbacks.%s 必须是非负数" % k)
    if "levels" in st:
        lv = st["levels"]
        if not isinstance(lv, list) or not lv:
            err("site.levels 不能为空")
        else:
            last = None
            for i, l in enumerate(lv):
                if "name" not in l or "elevation" not in l:
                    err("site.levels[%d] 需要 name 与 elevation" % i)
                    continue
                if last is not None and l["elevation"] < last:
                    warn("site.levels 标高未按升序排列（index %d）" % i)
                last = l["elevation"]

    ids = set()
    for i, m in enumerate(design["masses"]):
        tag = "masses[%d]" % i
        for key in ("id", "footprint", "base", "height", "roof", "material"):
            if key not in m:
                err("%s 缺少字段 %r" % (tag, key))
        if not isinstance(m.get("id"), str) or not m.get("id"):
            err("%s.id 必须是非空字符串" % tag)
        elif m["id"] in ids:
            err("mass id 重复: %r" % m["id"])
        else:
            ids.add(m["id"])
            tag = "mass %r" % m["id"]
        if "footprint" in m:
            fp = as_pts(m["footprint"])
            if len(fp) < 3:
                err("%s.footprint 至少 3 点" % tag)
            elif poly_area(fp) <= 1e-6:
                err("%s.footprint 面积为 0" % tag)
            elif poly_self_intersects(fp):
                err("%s.footprint 自相交" % tag)
        if m.get("roof") not in ROOFS:
            err("%s.roof 必须是 %s" % (tag, "/".join(ROOFS)))
        if isinstance(m.get("height"), (int, float)) and m["height"] <= 0:
            err("%s.height 必须 > 0" % tag)
        if m.get("material") and m["material"] not in design["materials"]:
            err("%s.material %r 未在 materials 中定义" % (tag, m["material"]))
        if m.get("roof") in ("gable", "shed") and not m.get("roof_rise"):
            warn("%s 是坡屋顶但未给 roof_rise，按平屋顶绘制" % tag)

    for name, mat in design["materials"].items():
        for key in ("color", "roughness", "metalness"):
            if key not in mat:
                err("materials[%r] 缺少 %r" % (name, key))
        c = str(mat.get("color", ""))
        if not (len(c) == 7 and c[0] == "#"):
            err("materials[%r].color 必须是 #RRGGBB，实际 %r" % (name, c))
        for key in ("roughness", "metalness"):
            v = mat.get(key)
            if not isinstance(v, (int, float)) or not (0 <= v <= 1):
                err("materials[%r].%s 必须在 0..1" % (name, key))

    for i, el in enumerate(design.get("elements", [])):
        tag = "elements[%d]" % i
        k = el.get("kind")
        if k not in ELEMENT_KINDS:
            err("%s.kind %r 不在支持列表 %s" % (tag, k, "/".join(ELEMENT_KINDS)))
            continue
        if k in ("wall", "path", "contour"):
            pts = el.get("points")
            if not isinstance(pts, list) or len(pts) < 2:
                err("%s 需要至少 2 个 points" % tag)
        if k in ("slab", "water", "plaza", "green", "stair"):
            pol = el.get("polygon")
            if not isinstance(pol, list) or len(pol) < 3:
                err("%s 需要至少 3 点的 polygon" % tag)
            elif poly_area(as_pts(pol)) <= 1e-6:
                err("%s.polygon 面积为 0" % tag)
        if k == "path" and not isinstance(el.get("width"), (int, float)):
            err("%s.path 需要 width" % tag)
        if k == "tree":
            if not el.get("center"):
                err("%s.tree 需要 center" % tag)
        if k == "column" and not el.get("center"):
            err("%s.column 需要 center" % tag)
        if k == "contour" and "elevation" not in el:
            err("%s.contour 需要 elevation" % tag)
        if k == "opening":
            if el.get("points"):
                continue
            host = el.get("host")
            if host not in ids:
                err("%s.opening.host %r 未匹配任何 mass" % (tag, host))
                continue
            m = next(x for x in design["masses"] if x["id"] == host)
            n = len(m["footprint"])
            e = el.get("edge")
            if not isinstance(e, int) or not (0 <= e < n):
                err("%s.opening.edge 必须是 0..%d，实际 %r" % (tag, n - 1, e))
            if float(el.get("width", 0)) <= 0:
                err("%s.opening.width 必须 > 0" % tag)
            if int(el.get("count", 0)) < 1:
                err("%s.opening.count 必须 >= 1" % tag)
            L = math.dist(as_pts(m["footprint"])[e % n],
                          as_pts(m["footprint"])[(e % n + 1) % n]) if isinstance(e, int) else 0
            need = int(el.get("count", 1)) * float(el.get("width", 0)) + 2 * float(el.get("margin", 0))
            if L and need > L + 1e-6:
                warn("%s 洞口总宽 %.2fm 超过所在边 %.2fm" % (tag, need, L))

    met = design.get("metrics")
    if met is not None and not isinstance(met, dict):
        err("metrics 必须是对象或 null")

    return errors, warnings


# --------------------------------------------------------------------------
# 指标
# --------------------------------------------------------------------------
def compute_metrics(design, warnings=None):
    warn = warnings if warnings is not None else []
    lh = level_height(design)
    boundary = as_pts(design["site"]["boundary"])
    site_area = poly_area(boundary)

    footprint_area = 0.0
    building_area = 0.0
    max_height = 0.0
    max_st = 1
    for m in design["masses"]:
        a = poly_area(as_pts(m["footprint"]))
        s = mass_storeys(m, lh)
        footprint_area += a
        building_area += a * s
        max_height = max(max_height, mass_top(m))
        max_st = max(max_st, s)

    road_area, plaza_area = site_hardscape_areas(design)
    water = water_area(design)
    green_explicit = green_area_explicit(design)

    green_area = site_area - footprint_area - road_area - plaza_area - water
    basis = "site_area_minus_built_and_hardscape"
    if green_explicit > 0:
        if green_explicit > green_area + 1e-6:
            warn.append(
                "显式 green 图元面积 %.1f m² 大于扣除法绿地 %.1f m²，说明绿地与建筑/道路/水面重叠"
                % (green_explicit, green_area))
        basis = "site_area_minus_built_and_hardscape(explicit green=%.1f m²)" % green_explicit

    if footprint_area + road_area + plaza_area + water > site_area + 1e-6:
        warn.append(
            "建筑基底 %.1f + 道路 %.1f + 广场 %.1f + 水面 %.1f = %.1f m² 超过用地 %.1f m²，"
            "说明图元互相重叠，绿地率按 0 计" % (
                footprint_area, road_area, plaza_area, water,
                footprint_area + road_area + plaza_area + water, site_area))
    green_area = max(0.0, green_area)

    if site_area <= 0:
        warn.append("用地面积为 0，无法计算指标")

    metrics = {
        "site_area": round(site_area, 2),
        "building_area": round(building_area, 2),
        "footprint_area": round(footprint_area, 2),
        "far": round(building_area / site_area, 4) if site_area > 0 else None,
        "density": round(footprint_area / site_area, 4) if site_area > 0 else None,
        "greenRatio": round(green_area / site_area, 4) if site_area > 0 else None,
        "max_height": round(max_height, 2),
        "stories": round(building_area / footprint_area, 2) if footprint_area > 0 else None,
        "max_stories": max_st,
        "green_area": round(green_area, 2),
        "green_basis": basis,
        "road_area": round(road_area, 2),
        "plaza_area": round(plaza_area, 2),
        "water_area": round(water, 2),
        "north_deg": design["site"].get("north_deg", 0),
    }

    parking = (design.get("metrics") or {}).get("parking")
    metrics["parking"] = parking
    if parking in (None, {}, 0):
        warn.append("metrics.parking 未声明，本脚本不估算停车配建（需按地方配建指标人工核定）")

    return metrics


def _num(v):
    if isinstance(v, (int, float)):
        return float(v)
    return None


def compare_declared(design, metrics, warnings):
    declared = design.get("metrics") or {}
    pairs = (("far", 0.02), ("density", 0.02), ("greenRatio", 0.02))
    for key, tol in pairs:
        d = _num(declared.get(key))
        c = _num(metrics.get(key))
        if d is None or c is None:
            continue
        if abs(d - c) > max(tol, abs(c) * 0.05):
            warnings.append(
                "design.json 声明 metrics.%s=%s 与实算 %s 不一致（差异 %.1f%%）"
                % (key, d, c, abs(d - c) / max(abs(c), EPS) * 100))


def check(design):
    errors, warnings = validate(design)
    metrics = {}
    if not errors:
        metrics = compute_metrics(design, warnings)
        compare_declared(design, metrics, warnings)
    return {"ok": not errors, "errors": errors, "warnings": warnings, "metrics": metrics}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _print_human(res):
    print("ok: %s" % res["ok"])
    if res["errors"]:
        print("errors (%d):" % len(res["errors"]))
        for e in res["errors"]:
            print("  - %s" % e)
    if res["warnings"]:
        print("warnings (%d):" % len(res["warnings"]))
        for w in res["warnings"]:
            print("  - %s" % w)
    m = res["metrics"]
    if m:
        print("metrics:")
        for k, v in m.items():
            print("  %-16s %s" % (k, v))
        fa = m.get("footprint_area") or 0
        st = m.get("stories") or 0
        ba = m.get("building_area") or 0
        print("  自洽检查 footprint_area(%s) x stories(%s) = %.2f  vs building_area(%s) 差 %.4f%%"
              % (fa, st, fa * st, ba, abs(fa * st - ba) / max(ba, EPS) * 100))


def main(argv=None):
    ap = argparse.ArgumentParser(description="design.json 校验与指标计算")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "metrics", "backfill", "summarize"):
        sp = sub.add_parser(name)
        sp.add_argument("design")
        sp.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    design = load_design(args.design)
    res = check(design)

    if args.cmd == "backfill":
        if res["ok"]:
            met = dict(design.get("metrics") or {})
            met.update({k: v for k, v in res["metrics"].items()
                        if k in ("far", "density", "greenRatio")})
            design["metrics"] = met
            dump_design(design, args.design)
            print("已回填 metrics 到 %s" % args.design)
        else:
            print("校验失败，未回填", file=sys.stderr)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            _print_human(res)
        return 0 if res["ok"] else 1

    if args.cmd == "metrics":
        out = {"metrics": res["metrics"], "warnings": res["warnings"]}
    elif args.cmd == "summarize":
        m = res["metrics"]
        print("%s / %s" % (design["project"]["name"], design["project"]["type"]))
        print("用地 %.1f m² | 建筑 %.1f m² | 基底 %.1f m² | FAR %.3f | 密度 %.1f%% | 绿地率 %.1f%% | 限高 %.1f m"
              % (m.get("site_area", 0), m.get("building_area", 0), m.get("footprint_area", 0),
                 m.get("far") or 0, (m.get("density") or 0) * 100,
                 (m.get("greenRatio") or 0) * 100, m.get("max_height", 0)))
        return 0
    else:
        out = res

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        _print_human(res)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
