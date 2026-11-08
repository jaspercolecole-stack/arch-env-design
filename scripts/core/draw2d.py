#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""draw2d.py -- 同源三后端的公共「原语层」。

设计原则：**图纸内容只描述一次**。siteplan / plan / section / elevation 的
几何全部在这里生成成一条「图元流水」（Scene.items），svg_backend /
pdf_backend / png_backend 只是同一份流水图的三个渲染器，因此三份输出必然
一致，不存在“SVG 和 PDF 画得不一样”的问题。

坐标与单位
  * 全部使用 **毫米**，原点在图纸左上角，y 轴向下（与 SVG 一致）。
  * PDF 后端负责把 y 翻成 PDF 的 y-up；PNG 后端负责乘以 DPI/25.4 的缩放。
  * 设计坐标（design.json）是米、y 轴向上；用 ToPaper 做一次变换。

线宽层级（建筑制图惯例）
  outline   0.70 mm  可见轮廓 / 剖到的构件
  secondary 0.35 mm  次轮廓 / 家具级构件 / 图框
  detail    0.18 mm  细部 / 尺寸线 / 洞口
  hatch     0.13 mm  填充线 / 地形等高线 / 网格

字体
  PDF 无法依赖外部字体渲染器，故本模块自带一个最小 TrueType 解析器，
  把 simhei.ttf 的 glyf 轮廓读出来转成折线，供 pdf_backend 以路径填充的
  方式画字（不嵌字体，绝不会因 CMap 错误整页失败）。同一份度量也用来算
  文本宽度，保证 SVG / PDF / PNG 三者的对齐完全一致。
"""
from __future__ import annotations

import json
import math
import os
import struct
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import design_io

MM2PT = 72.0 / 25.4          # 2.8346456692913385
PAPER = {"A1": (841.0, 594.0), "A2": (594.0, 420.0), "A3": (420.0, 297.0)}
LW = {"outline": 0.70, "secondary": 0.35, "detail": 0.18, "hatch": 0.13}

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\SimHei.ttf",
    r"C:\Windows\Fonts\msyh.ttf",
    r"C:\Windows\Fonts\simsun.ttf",
]
FONT_FAMILY = '"Microsoft YaHei", "SimHei", sans-serif'

# 图纸配色（打印友好，低饱和）
C = {
    "paper":   "#FFFFFF",
    "frame":   "#1A1A1A",
    "site":    "#2B2B2B",
    "green":   "#DCE6CE",
    "green_l": "#C8D8B4",
    "road":    "#B9B9B4",
    "road_e":  "#8A8A85",
    "plaza":   "#E2DDD2",
    "water":   "#BFD6E2",
    "water_e": "#6E97AC",
    "tree":    "#7E9B62",
    "tree_e":  "#5C7546",
    "concrete":"#D3CFC4",
    "brick":   "#C89A80",
    "glass":   "#CFE1EA",
    "roof":    "#B4BABE",
    "cut":     "#4A4A4A",
    "hatch":   "#9A9A94",
    "text":    "#1A1A1A",
    "text_l":  "#5A5A5A",
    "dash":    "#6B6B6B",
}


def hex_rgb(h):
    h = h.lstrip("#")
    return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0)


# ==========================================================================
# 最小 TrueType 解析（只读 glyf 轮廓与 hmtx 步进）
# ==========================================================================
class TTFont:
    """只做三件事：cmap 查字形、hmtx 取字宽、glyf 取轮廓。

    轮廓返回 [(x, y), ...] 的列表（字体单位、y 轴向上、二次曲线已离散化）。
    """

    def __init__(self, path):
        with open(path, "rb") as f:
            self.data = f.read()
        self.path = path
        self._parse_dir()
        self.upem = self._u16("head", 18)
        self.index_to_loc = self._i16("head", 50)
        self.num_glyphs = self._u16("maxp", 4)
        self.num_hm = self._u16("hhea", 34)
        self._parse_loca()
        self._parse_cmap()
        self._width_cache = {}
        self._glyph_cache = {}

    # -- 基础读取 --
    def _parse_dir(self):
        d = self.data
        if len(d) < 12:
            raise ValueError("不是 TTF: %s" % self.path)
        tag = d[0:4]
        if tag == b"ttcf":
            raise ValueError("是 TTC 集合，本引擎不支持，请给单字体 ttf: %s" % self.path)
        n = struct.unpack(">H", d[4:6])[0]
        self.tables = {}
        for i in range(n):
            off = 12 + i * 16
            tg = d[off:off + 4].decode("latin-1")
            toff, tlen = struct.unpack(">II", d[off + 8:off + 16])
            self.tables[tg] = (toff, tlen)
        if "glyf" not in self.tables or "loca" not in self.tables:
            raise ValueError("字体缺少 glyf/loca，不是 TrueType 轮廓: %s" % self.path)

    def _u16(self, tag, off):
        base = self.tables[tag][0]
        return struct.unpack(">H", self.data[base + off:base + off + 2])[0]

    def _i16(self, tag, off):
        base = self.tables[tag][0]
        return struct.unpack(">h", self.data[base + off:base + off + 2])[0]

    def _parse_loca(self):
        base = self.tables["loca"][0]
        n = self.num_glyphs + 1
        if self.index_to_loc == 0:
            self.loca = [v * 2 for v in struct.unpack(">%dH" % n, self.data[base:base + 2 * n])]
        else:
            self.loca = list(struct.unpack(">%dI" % n, self.data[base:base + 4 * n]))

    def _parse_cmap(self):
        base = self.tables["cmap"][0]
        n = struct.unpack(">H", self.data[base + 2:base + 4])[0]
        best = None
        for i in range(n):
            pid, eid, off = struct.unpack(">HHI", self.data[base + 4 + i * 8:base + 12 + i * 8])
            sub = base + off
            fmt = struct.unpack(">H", self.data[sub:sub + 2])[0]
            score = {(3, 10): 5, (3, 1): 4, (0, 4): 3, (0, 3): 2}.get((pid, eid), 1)
            if fmt in (4, 12) and (best is None or score > best[0]):
                best = (score, fmt, sub)
        if best is None:
            raise ValueError("cmap 中没有可用的 format 4/12 子表: %s" % self.path)
        _, self.cmap_fmt, self.cmap_off = best

    def glyph_id(self, ch):
        c = ord(ch)
        if self.cmap_fmt == 4:
            return self._cmap4(c)
        return self._cmap12(c)

    def _cmap4(self, c):
        b = self.cmap_off
        seg2 = struct.unpack(">H", self.data[b + 6:b + 8])[0]
        seg = seg2 // 2
        end = b + 14
        start = end + seg2 + 2
        delta = start + seg2
        rng = delta + seg2
        for i in range(seg):
            ec = struct.unpack(">H", self.data[end + i * 2:end + i * 2 + 2])[0]
            if ec < c:
                continue
            sc = struct.unpack(">H", self.data[start + i * 2:start + i * 2 + 2])[0]
            if sc > c:
                return 0
            d = struct.unpack(">h", self.data[delta + i * 2:delta + i * 2 + 2])[0]
            ro_off = rng + i * 2
            ro = struct.unpack(">H", self.data[ro_off:ro_off + 2])[0]
            if ro == 0:
                return (c + d) & 0xFFFF
            gi = ro_off + ro + (c - sc) * 2
            if gi + 2 > len(self.data):
                return 0
            g = struct.unpack(">H", self.data[gi:gi + 2])[0]
            return (g + d) & 0xFFFF if g else 0
        return 0

    def _cmap12(self, c):
        b = self.cmap_off
        ngroups = struct.unpack(">I", self.data[b + 12:b + 16])[0]
        lo, hi = 0, ngroups - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            g = b + 16 + mid * 12
            s, e, sg = struct.unpack(">III", self.data[g:g + 12])
            if c < s:
                hi = mid - 1
            elif c > e:
                lo = mid + 1
            else:
                return sg + (c - s)
        return 0

    def advance(self, gi):
        if gi in self._width_cache:
            return self._width_cache[gi]
        base = self.tables["hmtx"][0]
        idx = min(gi, self.num_hm - 1)
        w = struct.unpack(">H", self.data[base + idx * 4:base + idx * 4 + 2])[0]
        self._width_cache[gi] = w
        return w

    def _glyph_bytes(self, gi):
        if gi + 1 >= len(self.loca):
            return b""
        go = self.tables["glyf"][0]
        a, b = self.loca[gi], self.loca[gi + 1]
        return self.data[go + a:go + b]

    def contours(self, gi, depth=0):
        if gi in self._glyph_cache:
            return self._glyph_cache[gi]
        raw = self._glyph_bytes(gi)
        if len(raw) < 10:
            self._glyph_cache[gi] = []
            return []
        nc = struct.unpack(">h", raw[0:2])[0]
        if nc >= 0:
            out = self._simple_glyph(raw, nc)
        elif depth < 4:
            out = self._composite_glyph(raw, depth)
        else:
            out = []
        self._glyph_cache[gi] = out
        return out

    def _simple_glyph(self, raw, nc):
        if nc == 0:
            return []
        ends = list(struct.unpack(">%dH" % nc, raw[10:10 + nc * 2]))
        np_ = ends[-1] + 1
        p = 10 + nc * 2
        ilen = struct.unpack(">H", raw[p:p + 2])[0]
        p += 2 + ilen
        flags = []
        while len(flags) < np_:
            f = raw[p]
            p += 1
            flags.append(f)
            if f & 0x08:
                rep = raw[p]
                p += 1
                flags.extend([f] * rep)
        flags = flags[:np_]
        xs, ys = [], []
        x = 0
        for f in flags:
            if f & 0x02:
                dx = raw[p]
                p += 1
                x += dx if f & 0x10 else -dx
            elif not (f & 0x10):
                x += struct.unpack(">h", raw[p:p + 2])[0]
                p += 2
            xs.append(x)
        y = 0
        for f in flags:
            if f & 0x04:
                dy = raw[p]
                p += 1
                y += dy if f & 0x20 else -dy
            elif not (f & 0x20):
                y += struct.unpack(">h", raw[p:p + 2])[0]
                p += 2
            ys.append(y)
        out = []
        start = 0
        for e in ends:
            pts = [(xs[i], ys[i], bool(flags[i] & 0x01)) for i in range(start, e + 1)]
            start = e + 1
            c = _quad_contour(pts)
            if len(c) >= 3:
                out.append(c)
        return out

    def _composite_glyph(self, raw, depth):
        out = []
        p = 10
        while True:
            flags, gi = struct.unpack(">HH", raw[p:p + 4])
            p += 4
            if flags & 0x0001:
                if flags & 0x0002:
                    a1, a2 = struct.unpack(">hh", raw[p:p + 4])
                else:
                    a1, a2 = struct.unpack(">HH", raw[p:p + 4])
                p += 4
            else:
                if flags & 0x0002:
                    a1 = struct.unpack(">b", raw[p:p + 1])[0]
                    a2 = struct.unpack(">b", raw[p + 1:p + 2])[0]
                else:
                    a1, a2 = struct.unpack(">BB", raw[p:p + 2])
                p += 2
            dx, dy = (a1, a2) if flags & 0x0002 else (0, 0)
            a = d = 1.0
            b = c = 0.0
            if flags & 0x0008:
                s = struct.unpack(">h", raw[p:p + 2])[0] / 16384.0
                p += 2
                a = d = s
            elif flags & 0x0040:
                sx, sy = struct.unpack(">hh", raw[p:p + 4])
                p += 4
                a, d = sx / 16384.0, sy / 16384.0
            elif flags & 0x0080:
                sa, sb, sc, sd = struct.unpack(">hhhh", raw[p:p + 8])
                p += 8
                a, b, c, d = sa / 16384.0, sb / 16384.0, sc / 16384.0, sd / 16384.0
            for ctr in self.contours(gi, depth + 1):
                out.append([(a * x + c * y + dx, b * x + d * y + dy) for (x, y) in ctr])
            if not (flags & 0x0020):
                break
        return out


def _quad_contour(pts):
    """TrueType 的 on/off 控制点序列 -> 折线。"""
    n = len(pts)
    if n < 2:
        return [p[:2] for p in pts]
    # 找一个 on-curve 起点
    start = None
    for i in range(n):
        if pts[i][2]:
            start = i
            break
    if start is None:
        mid = ((pts[0][0] + pts[1][0]) / 2.0, (pts[0][1] + pts[1][1]) / 2.0)
        seq = [(mid[0], mid[1], True)] + [pts[i] for i in range(1, n)] + [pts[0]]
    else:
        seq = [pts[(start + i) % n] for i in range(n)] + [pts[start]]
    out = [(seq[0][0], seq[0][1])]
    cur = (seq[0][0], seq[0][1])
    i = 1
    while i < len(seq):
        x, y, on = seq[i]
        if on:
            out.append((x, y))
            cur = (x, y)
            i += 1
        else:
            nxt = seq[i + 1] if i + 1 < len(seq) else seq[0]
            ex, ey = (nxt[0], nxt[1]) if nxt[2] else \
                ((x + nxt[0]) / 2.0, (y + nxt[1]) / 2.0)
            steps = 6
            for s in range(1, steps + 1):
                t = s / float(steps)
                mt = 1.0 - t
                out.append((mt * mt * cur[0] + 2 * mt * t * x + t * t * ex,
                            mt * mt * cur[1] + 2 * mt * t * y + t * t * ey))
            cur = (ex, ey)
            i += 2 if nxt[2] else 3
    if len(out) > 1 and math.dist(out[0], out[-1]) < 1e-6:
        out.pop()
    return out


_FONT_CACHE = {}


def load_font(path=None):
    if path is None:
        for c in FONT_CANDIDATES:
            if os.path.exists(c):
                path = c
                break
    if path is None or not os.path.exists(path):
        return None
    if path not in _FONT_CACHE:
        try:
            _FONT_CACHE[path] = TTFont(path)
        except Exception as exc:                     # 字体坏了也不能让出图瘫掉
            sys.stderr.write("警告: 字体解析失败 %s: %s\n" % (path, exc))
            _FONT_CACHE[path] = None
    return _FONT_CACHE[path]


# ==========================================================================
# 图元流水
# ==========================================================================
class Scene:
    """一张图的图元流水。所有坐标单位 mm，原点左上，y 向下。"""

    def __init__(self, w, h, font=None, meta=None):
        self.w = float(w)
        self.h = float(h)
        self.items = []
        self.font = font
        self.meta = meta or {}
        self._wcache = {}

    # ---- 文本度量 ----
    def text_width(self, s, size):
        key = (s, round(size, 4))
        if key in self._wcache:
            return self._wcache[key]
        f = self.font
        w = 0.0
        if f is not None:
            for ch in s:
                gi = f.glyph_id(ch)
                adv = f.advance(gi) if gi else f.upem * 0.5
                w += adv
            w = w * size / float(f.upem)
        else:
            for ch in s:
                w += size * (1.0 if _is_wide(ch) else 0.52)
        self._wcache[key] = w
        return w

    def _text_anchor(self, s, size, anchor):
        w = self.text_width(s, size)
        if anchor in ("ct", "cm", "cb", "c"):
            return -w / 2.0, w
        if anchor in ("rt", "rm", "rb", "r"):
            return -w, w
        return 0.0, w

    # ---- 基础图元 ----
    def fill(self, pts, color, alpha=None):
        if len(pts) < 3:
            return
        self.items.append({"t": "fill", "pts": [tuple(p) for p in pts],
                           "color": color, "alpha": alpha})

    def stroke(self, pts, color, w, closed=False, dash=None, cap="butt", join="miter"):
        if len(pts) < 2:
            return
        self.items.append({"t": "stroke", "pts": [tuple(p) for p in pts], "color": color,
                           "w": w, "closed": closed, "dash": dash, "cap": cap, "join": join})

    def poly(self, pts, fill=None, stroke=None, w=LW["secondary"], dash=None,
             cap="butt", join="miter", alpha=None):
        if fill:
            self.fill(pts, fill, alpha)
        if stroke and w > 0:
            self.stroke(pts, stroke, w, closed=True, dash=dash, cap=cap, join=join)

    def line(self, p0, p1, color, w=LW["detail"], dash=None, cap="butt"):
        self.stroke([p0, p1], color, w, dash=dash, cap=cap)

    def rect(self, x0, y0, x1, y1, fill=None, stroke=None, w=LW["secondary"], dash=None):
        self.poly([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], fill, stroke, w, dash)

    def circle(self, cx, cy, r, fill=None, stroke=None, w=LW["detail"], seg=48):
        pts = [(cx + r * math.cos(2 * math.pi * i / seg),
                cy + r * math.sin(2 * math.pi * i / seg)) for i in range(seg)]
        self.poly(pts, fill, stroke, w)

    def polyline(self, pts, color, w=LW["secondary"], dash=None, cap="butt", join="miter"):
        self.stroke(pts, color, w, dash=dash, cap=cap, join=join)

    def text(self, x, y, s, size=2.5, color=None, anchor="lt", rot=0.0, spacing=0.0):
        if not s:
            return
        dx, wdt = self._text_anchor(s, size, anchor)
        vy = anchor[1] if len(anchor) > 1 else "t"
        if vy == "m" or vy == "c":
            ny = y + size * 0.36
        elif vy == "b":
            ny = y - size * 0.20
        else:
            ny = y + size * 0.86
        self.items.append({"t": "text", "x": x + dx, "y": ny, "s": s, "size": size,
                           "color": color or C["text"], "rot": rot, "w": wdt})

    def hatch(self, pts, spacing=1.6, angle=45.0, color=None, w=LW["hatch"]):
        for seg in hatch_segments(pts, spacing, angle):
            self.line(seg[0], seg[1], color or C["hatch"], w)

    def arrow(self, p0, p1, color, w=LW["detail"], head=1.6, dash=None):
        """带箭头的线（尺寸线）。"""
        ang = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
        self.line(p0, p1, color, w, dash=dash)
        for sgn, base in ((0, p0), (math.pi, p1)):
            a = ang + sgn
            for da in (0.38, -0.38):
                self.line(base, (base[0] - head * math.cos(a + da),
                                 base[1] - head * math.sin(a + da)), color, w)

    def tick(self, p, color, size=1.2, w=LW["detail"], ang=45.0):
        """45° 短斜线（尺寸起止符）。"""
        a = math.radians(ang)
        self.line((p[0] - size / 2 * math.cos(a), p[1] - size / 2 * math.sin(a)),
                  (p[0] + size / 2 * math.cos(a), p[1] + size / 2 * math.sin(a)), color, w)


def _is_wide(ch):
    o = ord(ch)
    return (0x2E80 <= o <= 0x9FFF) or (0xF900 <= o <= 0xFAFF) or \
           (0xFF00 <= o <= 0xFF60) or (0x3000 <= o <= 0x303F)


def hatch_segments(poly, spacing, angle):
    """把多边形裁成一组平行线段（even-odd），返回 [((x0,y0),(x1,y1)), ...]。"""
    if spacing <= 1e-6 or len(poly) < 3:
        return []
    a = math.radians(angle)
    ca, sa = math.cos(-a), math.sin(-a)
    rp = [(x * ca - y * sa, x * sa + y * ca) for (x, y) in poly]
    ymin = min(p[1] for p in rp)
    ymax = max(p[1] for p in rp)
    n = len(rp)
    out = []
    y = math.ceil(ymin / spacing) * spacing
    guard = 0
    while y < ymax and guard < 20000:
        guard += 1
        xs = []
        for i in range(n):
            x1, y1 = rp[i]
            x2, y2 = rp[(i + 1) % n]
            if (y1 <= y < y2) or (y2 <= y < y1):
                xs.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
        xs.sort()
        for i in range(0, len(xs) - 1, 2):
            X0, X1 = xs[i], xs[i + 1]
            if X1 - X0 > 1e-6:
                out.append(_rot_back(X0, y, a, X1, y))
        y += spacing
    return out


def _rot_back(x0, y0, a, x1, y1):
    ca, sa = math.cos(a), math.sin(a)
    return ((x0 * ca - y0 * sa, x0 * sa + y0 * ca),
            (x1 * ca - y1 * sa, x1 * sa + y1 * ca))


# ==========================================================================
# 设计坐标（米, y 向上） -> 图纸坐标（毫米, y 向下）
# ==========================================================================
class ToPaper:
    def __init__(self, scale, cx, cy, site_cx, site_cy):
        self.k = 1000.0 / float(scale)      # 1 米 = k 毫米
        self.cx, self.cy, self.sx, self.sy = cx, cy, site_cx, site_cy

    def __call__(self, p):
        return (self.cx + (p[0] - self.sx) * self.k,
                self.cy - (p[1] - self.sy) * self.k)

    def pt(self, x, y):
        return (self.cx + (x - self.sx) * self.k, self.cy - (y - self.sy) * self.k)

    def m(self, d):
        """长度（米）-> 毫米。"""
        return d * self.k


# ==========================================================================
# 图面家具：图框 / 标题栏 / 指北针 / 比例尺 / 图例 / 说明
# ==========================================================================
FRAME = {"left": 25.0, "right": 10.0, "top": 10.0, "bottom": 10.0}
TITLE_W = 180.0
TITLE_H = 46.0
BAND_H = 52.0            # 图框底部为标题栏预留的高度


def inner_frame(w, h):
    return (FRAME["left"], FRAME["top"], w - FRAME["right"], h - FRAME["bottom"])


def drawing_area(w, h):
    x0, y0, x1, y1 = inner_frame(w, h)
    return (x0, y0, x1, y1 - BAND_H)


def scale_ladder(fit_mm, want):
    """在标准比例梯子里挑一个不小于 want、且能放下的比例。"""
    ladder = [50, 100, 150, 200, 250, 300, 400, 500, 750, 1000, 1500, 2000]
    cand = [s for s in ladder if s >= want] or [ladder[-1]]
    for s in cand:
        if fit_mm * 1000.0 / s <= 1.0:
            return s
    return cand[-1]


def paper_underlay(sc):
    """整页纸色垫底：永远插在 items[0]，保证顺序渲染器不会刷掉已画内容。"""
    w, h = sc.w, sc.h
    if sc.items and sc.items[0].get("t") == "fill":
        it = sc.items[0]
        pts = it.get("pts") or ()
        if (it.get("color") == C["paper"] and len(pts) == 4
                and abs(pts[2][0] - w) < 1e-6 and abs(pts[2][1] - h) < 1e-6):
            return
    sc.items.insert(0, {"t": "fill",
                        "pts": [(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)],
                        "color": C["paper"], "alpha": None})


def draw_frame(sc, title, scale_txt, sheet_no, project_name, date, extra_note=""):
    w, h = sc.w, sc.h
    x0, y0, x1, y1 = inner_frame(w, h)
    paper_underlay(sc)
    sc.rect(x0, y0, x1, y1, fill=None, stroke=C["frame"], w=LW["outline"])
    sc.rect(x0 + 2.0, y0 + 2.0, x1 - 2.0, y1 - 2.0, fill=None, stroke=C["frame"],
            w=LW["hatch"])

    step = 50.0
    n = int((x1 - x0) / step)
    for i in range(1, n):
        x = x0 + i * step
        sc.line((x, y0), (x, y0 + 3.0), C["frame"], LW["hatch"])
        sc.line((x, y1 - 3.0), (x, y1), C["frame"], LW["hatch"])
    m = int((y1 - y0) / step)
    for i in range(1, m):
        y = y0 + i * step
        sc.line((x0, y), (x0 + 3.0, y), C["frame"], LW["hatch"])
        sc.line((x1 - 3.0, y), (x1, y), C["frame"], LW["hatch"])

    tx1, ty1 = x1, y1
    tx0, ty0 = x1 - TITLE_W, y1 - TITLE_H
    r1 = ty0 + 17.0
    r2 = ty0 + 32.0
    c1 = tx0 + 66.0
    c2 = tx0 + 122.0
    sc.rect(tx0, ty0, tx1, ty1, fill="#FFFFFF", stroke=C["frame"], w=LW["outline"])
    for yy in (r1, r2):
        sc.line((tx0, yy), (tx1, yy), C["frame"], LW["secondary"])
    sc.line((c1, r1), (c1, r2), C["frame"], LW["secondary"])
    sc.line((c2, r2), (c2, ty1), C["frame"], LW["secondary"])

    sc.text(tx0 + 4, ty0 + 6.4, project_name, 5.2, C["text"], "lt")
    sc.text(tx1 - 4, ty0 + 4.6, "建筑与环境设计 · 图纸", 2.5, C["text_l"], "rt")
    sc.text(tx0 + 4, r2 - 1.4, title, 6.0, C["text"], "lb")
    sc.text(tx1 - 4, r2 - 1.4, "比例 " + scale_txt, 4.0, C["text"], "rb")
    sc.text(tx0 + 3, r1 + 3.2, "图号", 2.3, C["text_l"], "lt")
    sc.text(tx0 + 3, ty1 - 3.0, sheet_no, 3.2, C["text"], "lb")
    sc.text(c2 + 3, r2 + 3.2, "日期", 2.3, C["text_l"], "lt")
    sc.text(c2 + 3, ty1 - 3.0, date, 3.0, C["text"], "lb")
    if extra_note:
        sc.text(c1 + 3, r1 + 3.2, "专业", 2.3, C["text_l"], "lt")
        sc.text(c1 + 3, ty1 - 3.0, extra_note, 3.0, C["text"], "lb")

    sc.line((x0, y1 - BAND_H), (tx0, y1 - BAND_H), C["frame"], LW["secondary"])


def draw_north(sc, cx, cy, r, north_deg, label="指北针"):
    sc.circle(cx, cy, r, fill="#FFFFFF", stroke=C["frame"], w=LW["secondary"], seg=64)
    a = math.radians(90.0 + north_deg)
    tip = (cx + r * 0.82 * math.cos(a), cy - r * 0.82 * math.sin(a))
    left = (cx + r * 0.36 * math.cos(a + 2.2), cy - r * 0.36 * math.sin(a + 2.2))
    right = (cx + r * 0.36 * math.cos(a - 2.2), cy - r * 0.36 * math.sin(a - 2.2))
    sc.poly([tip, left, (cx, cy)], fill=C["frame"], stroke=C["frame"], w=LW["detail"])
    sc.poly([tip, right, (cx, cy)], fill="#FFFFFF", stroke=C["frame"], w=LW["detail"])
    lx = cx + (r + 4.4) * math.cos(a)
    ly = cy - (r + 4.4) * math.sin(a)
    sc.text(lx, ly, "N", 5.0, C["text"], "cm")
    sc.text(cx, cy + r + 5.6, label, 2.3, C["text_l"], "ct")


def draw_scale_bar(sc, x, y, scale, total_m=40.0, seg=4):
    k = 1000.0 / scale
    seg_m = total_m / seg
    seg_mm = seg_m * k
    for i in range(seg):
        fill = C["frame"] if i % 2 == 0 else "#FFFFFF"
        sc.rect(x + i * seg_mm, y, x + (i + 1) * seg_mm, y + 2.6, fill=fill,
                stroke=C["frame"], w=LW["detail"])
    for i in range(seg + 1):
        sc.text(x + i * seg_mm, y + 4.6, "%g" % (i * seg_m), 2.4, C["text"], "ct")
    sc.text(x + seg_mm * seg / 2.0, y + 9.4, "单位: 米   (1:%d)" % scale, 2.4,
            C["text_l"], "ct")


def draw_legend(sc, x, y, items, cols=2, rowh=7.0, colw=46.0):
    for i, (label, kind, color) in enumerate(items):
        cx = x + (i % cols) * colw
        cy = y + (i // cols) * rowh
        if kind == "fill":
            sc.rect(cx, cy, cx + 7.0, cy + 4.0, fill=color, stroke=C["text_l"], w=LW["detail"])
        elif kind == "line":
            sc.line((cx, cy + 2.0), (cx + 7.0, cy + 2.0), color, LW["outline"])
        elif kind == "dash":
            sc.line((cx, cy + 2.0), (cx + 7.0, cy + 2.0), color, LW["secondary"], dash=(1.6, 1.0))
        elif kind == "circle":
            sc.circle(cx + 3.5, cy + 2.0, 2.0, fill=color, stroke=C["tree_e"], w=LW["detail"])
        sc.text(cx + 9.5, cy + 3.4, label, 2.8, C["text"], "lb")


def draw_notes(sc, x, y, lines, size=2.6, rowh=4.4, title="设计说明"):
    sc.text(x, y, title, 3.2, C["text"], "lt")
    for i, s in enumerate(lines):
        sc.text(x, y + 6.2 + i * rowh, s, size, C["text_l"], "lt")


# ==========================================================================
# 几何小工具
# ==========================================================================
def subtract_spans(span, cuts):
    out = [span]
    for c0, c1 in sorted(cuts):
        nxt = []
        for a, b in out:
            if c1 <= a + 1e-9 or c0 >= b - 1e-9:
                nxt.append((a, b))
                continue
            if c0 > a + 1e-9:
                nxt.append((a, c0))
            if c1 < b - 1e-9:
                nxt.append((c1, b))
        out = nxt
    return [(a, b) for a, b in out if b - a > 1e-6]


def lerp(p, q, t):
    return (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)


def setback_polygon(boundary, sb, limit=8.0):
    """按各边朝向套 front/back/left/right 退线距离，做逐边内偏移。"""
    pts = design_io.normalize_ccw(boundary)
    n = len(pts)
    segs = []
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        if L < 1e-9:
            continue
        nx, ny = -dy / L, dx / L
        ox, oy = -nx, -ny
        if abs(oy) >= abs(ox):
            d = sb.get("front", 0.0) if oy < 0 else sb.get("back", 0.0)
        else:
            d = sb.get("left", 0.0) if ox < 0 else sb.get("right", 0.0)
        d = float(d or 0.0)
        segs.append(((a[0] + nx * d, a[1] + ny * d), (dx, dy), d))
    m = len(segs)
    if m < 3:
        return []
    out = []
    for i in range(m):
        pa, da, d = segs[i]
        pb, db, _ = segs[(i + 1) % m]
        hit = design_io._line_line(pa, da, pb, db)
        src = pts[(i + 1) % n]
        if hit is None:
            out.append(pa)
            continue
        lim = limit * max(d, 1e-3)
        if math.dist(hit, src) > lim:
            out.append(lerp(src, hit, lim / math.dist(hit, src)))
        else:
            out.append(hit)
    if design_io.poly_area(out) <= 1e-6 or design_io.poly_self_intersects(out):
        return []
    return out


def mass_is_above(mass, z):
    base = float(mass.get("base", 0.0))
    return base - 1e-6 <= z < base + float(mass.get("height", 0.0)) - 1e-6


def site_bbox(design, pad=0.0):
    x0, y0, x1, y1 = design_io.bbox(design["site"]["boundary"])
    return (x0 - pad, y0 - pad, x1 + pad, y1 + pad)


def masses_bbox(design, pad=0.0):
    xs, ys = [], []
    for m in design["masses"]:
        b = design_io.bbox(m["footprint"])
        xs += [b[0], b[2]]
        ys += [b[1], b[3]]
    if not xs:
        return site_bbox(design, pad)
    return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)


def _storeys(m, lh):
    st = int(m.get("storeys", 0) or 0)
    if st > 0:
        return st
    h = float(m.get("height", 0.0) or 0.0)
    return max(1, int(round(h / lh))) if lh > 0 else 1

GLZ = "#5E7F90"


def offset_poly_signed(pts, dist):
    """dist>0 向内、dist<0 向外（米）。用于悬挑投影轮廓。"""
    ccw = design_io.normalize_ccw(pts)
    n = len(ccw)
    if n < 3:
        return []
    segs = []
    for i in range(n):
        a, b = ccw[i], ccw[(i + 1) % n]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        if L < 1e-9:
            continue
        nx, ny = -dy / L, dx / L
        segs.append(((a[0] + nx * dist, a[1] + ny * dist), (dx, dy)))
    m = len(segs)
    if m < 3:
        return []
    out = []
    for i in range(m):
        pa, da = segs[i]
        pb, db = segs[(i + 1) % m]
        hit = design_io._line_line(pa, da, pb, db)
        out.append(hit if hit else pa)
    if design_io.poly_area(out) <= 1e-6 or design_io.poly_self_intersects(out):
        return []
    return out


def align_poly(inner, outer):
    """偏移后顶点会整体错位一格，按最近距离旋转对齐。"""
    if not inner or len(inner) != len(outer):
        return None
    n = len(inner)
    best, bestd = 0, None
    for s in range(n):
        dd = sum(math.dist(inner[(i + s) % n], outer[i]) for i in range(n))
        if bestd is None or dd < bestd:
            best, bestd = s, dd
    return [inner[(i + best) % n] for i in range(n)]


def plate(sc, x, y, s, size=2.8, color=None, pad=0.8):
    """带白色底衬的文字，避免压在图线上看不清。"""
    w = sc.text_width(s, size)
    x0 = x - w / 2.0
    sc.rect(x0 - pad, y - size * 0.80, x0 + w + pad, y + size * 0.12, fill="#FFFFFF",
            stroke=None)
    sc.text(x0, y, s, size, color or C["text"], "lb")


def dim_h(sc, xa, xb, y, text, ext=None, color=None, size=2.6):
    col = color or C["dash"]
    if ext:
        ya, yb = ext
        sc.line((xa, ya), (xa, y), col, LW["hatch"])
        sc.line((xb, yb), (xb, y), col, LW["hatch"])
    sc.line((xa, y), (xb, y), col, LW["detail"])
    sc.tick((xa, y), col)
    sc.tick((xb, y), col)
    sc.text((xa + xb) / 2.0, y - 1.2, text, size, col, "cb")


def dim_v(sc, ya, yb, x, text, ext=None, color=None, size=2.6):
    col = color or C["dash"]
    if ext:
        xa, xb = ext
        sc.line((xa, ya), (x, ya), col, LW["hatch"])
        sc.line((xb, yb), (x, yb), col, LW["hatch"])
    sc.line((x, ya), (x, yb), col, LW["detail"])
    sc.tick((x, ya), col)
    sc.tick((x, yb), col)
    sc.text(x - 1.2, (ya + yb) / 2.0, text, size, col, "rm")


def mat_color(design, name, fallback):
    mat = (design.get("materials") or {}).get(name or "")
    if mat and isinstance(mat.get("color"), str) and len(mat["color"]) == 7:
        try:
            r, g, b = hex_rgb(mat["color"])
            k = 0.52
            return "#%02X%02X%02X" % (int(255 * (r + (1 - r) * k)),
                                      int(255 * (g + (1 - g) * k)),
                                      int(255 * (b + (1 - b) * k)))
        except Exception:
            return fallback
    return fallback


# ==========================================================================
# 一、总平面图
# ==========================================================================
def build_siteplan(sc, d, T):
    lh = design_io.level_height(d)
    els = d.get("elements", [])
    bd = design_io.as_pts(d["site"]["boundary"])
    sc.fill([T(p) for p in bd], C["green"])

    sbp = setback_polygon(bd, d["site"].get("setbacks", {}))
    if sbp:
        sc.stroke([T(p) for p in sbp], C["dash"], LW["detail"], closed=True, dash=(5, 2))

    for el in els:
        if el.get("kind") == "green":
            sc.poly([T(p) for p in el["polygon"]], C["green_l"], C["tree_e"], LW["hatch"])

    for el in els:
        if el.get("kind") == "contour":
            pts = [T(p) for p in el["points"]]
            sc.stroke(pts, "#9C9C90", LW["hatch"])
            mid = pts[len(pts) // 2]
            plate(sc, mid[0], mid[1], "%+.1f" % float(el["elevation"]), 2.2, "#7A7A70")

    for el in els:
        if el.get("kind") == "water":
            sc.poly([T(p) for p in el["polygon"]], C["water"], C["water_e"], LW["secondary"])

    for el in els:
        if el.get("kind") == "plaza":
            sc.poly([T(p) for p in el["polygon"]], C["plaza"], C["road_e"], LW["secondary"])

    for el in els:
        if el.get("kind") != "path":
            continue
        pts = [T(p) for p in el["points"]]
        wmm = T.m(float(el["width"]))
        sc.stroke(pts, C["road"], wmm, cap="butt", join="round")
        left = design_io.polyline_offset(el["points"], float(el["width"]) / 2.0)
        right = design_io.polyline_offset(el["points"], -float(el["width"]) / 2.0)
        sc.stroke([T(p) for p in left], C["road_e"], LW["detail"], cap="butt")
        sc.stroke([T(p) for p in right], C["road_e"], LW["detail"], cap="butt")
        sc.stroke(pts, "#FFFFFF", LW["detail"], dash=(4, 3))

    for m in d["masses"]:
        fp = design_io.as_pts(m["footprint"])
        sc.poly([T(p) for p in fp], mat_color(d, m.get("material"), C["concrete"]),
                C["cut"], LW["outline"])
        oh = float(m.get("overhang", 0) or 0)
        if oh > 0:
            g = offset_poly_signed(fp, -oh)
            if g:
                sc.stroke([T(p) for p in g], C["dash"], LW["detail"], closed=True,
                          dash=(4, 1.6))
        if m.get("roof") == "gable" and float(m.get("roof_rise", 0) or 0) > 0:
            x0, y0, x1, y1 = design_io.bbox(fp)
            if (x1 - x0) >= (y1 - y0):
                ym = (y0 + y1) / 2.0
                sc.line(T.pt(x0, ym), T.pt(x1, ym), C["cut"], LW["secondary"])
                for kk in (0.25, 0.75):
                    yy = y0 + (y1 - y0) * kk
                    sc.line(T.pt(x0, yy), T.pt(x1, yy), C["hatch"], LW["hatch"], dash=(3, 1.4))
            else:
                xm = (x0 + x1) / 2.0
                sc.line(T.pt(xm, y0), T.pt(xm, y1), C["cut"], LW["secondary"])

    for el in els:
        if el.get("kind") == "tree":
            c = T(el["center"])
            r = max(1.1, T.m(float(el.get("radius", 3.0))))
            sc.circle(c[0], c[1], r, fill=C["tree"], stroke=C["tree_e"],
                      w=LW["detail"], seg=20)
            sc.circle(c[0], c[1], max(0.5, r * 0.15), fill=C["tree_e"], stroke=None, seg=10)

    for el in els:
        if el.get("kind") == "column":
            c = T(el["center"])
            s = max(0.8, T.m(float(el.get("size", 0.6))))
            sc.rect(c[0] - s / 2, c[1] - s / 2, c[0] + s / 2, c[1] + s / 2,
                    fill=C["cut"], stroke=None)

    for m in d["masses"]:
        c = T(design_io.centroid(design_io.as_pts(m["footprint"])))
        st = _storeys(m, lh)
        top = design_io.mass_top(m)
        plate(sc, c[0], c[1] - 1.4, str(m["id"]), 3.6)
        plate(sc, c[0], c[1] + 2.8, "%dF  H=%.1fm" % (st, top), 2.4, C["text_l"])

    x0, y0, x1, y1 = design_io.bbox(bd)
    px0, py0 = T.pt(x0, y0)
    px1, py1 = T.pt(x1, y1)
    dim_h(sc, px0, px1, py1 + 9.0, "%.2f m" % (x1 - x0), ext=(py0, py1))
    dim_v(sc, py1, py0, px0 - 9.0, "%.2f m" % (y1 - y0), ext=(px0, px1))
    sc.text(px0, py1 + 13.5, "用地面积 %.1f m2" % design_io.poly_area(bd), 2.6,
            C["text_l"], "lt")


# ==========================================================================
# 二、标准层平面图
# ==========================================================================
def build_plan(sc, d, T, level_index=1):
    lh = design_io.level_height(d)
    levels = d["site"]["levels"]
    li = max(0, min(int(level_index), len(levels) - 1))
    z = float(levels[li]["elevation"])
    level_name = levels[li]["name"]
    els = d.get("elements", [])
    openings = design_io.expand_openings(d)
    wall = 0.24

    bd = design_io.as_pts(d["site"]["boundary"])
    sc.stroke([T(p) for p in bd], "#C8C8C8", LW["detail"], closed=True, dash=(6, 2.4))
    sbp = setback_polygon(bd, d["site"].get("setbacks", {}))
    if sbp:
        sc.stroke([T(p) for p in sbp], "#D8D8D8", LW["hatch"], closed=True, dash=(2, 2))

    for m in d["masses"]:
        if not mass_is_above(m, z):
            continue
        fp = design_io.as_pts(m["footprint"])
        n = len(fp)
        inner = design_io.offset_polygon(fp, wall)
        inner = align_poly(inner, fp) if inner else None
        sc.poly([T(p) for p in fp], "#F4F2EC", None)
        ops = [o for o in openings if o.get("host") == m["id"]]
        for i in range(n):
            a, b = fp[i], fp[(i + 1) % n]
            ia, ib = (inner[i], inner[(i + 1) % n]) if inner else (a, b)
            L = math.dist(a, b)
            if L < 1e-6:
                continue
            ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
            cuts = []
            for o in ops:
                if o.get("level") not in (None, li, level_name):
                    continue
                p, q = o["p0"], o["p1"]
                if abs((p[0] - a[0]) * uy - (p[1] - a[1]) * ux) > 0.12:
                    continue
                t0 = (p[0] - a[0]) * ux + (p[1] - a[1]) * uy
                t1 = (q[0] - a[0]) * ux + (q[1] - a[1]) * uy
                t0, t1 = min(t0, t1), max(t0, t1)
                if t1 < -0.01 or t0 > L + 0.01:
                    continue
                cuts.append((max(0.0, t0), min(L, t1)))
            cuts.sort()
            for (s0, s1) in subtract_spans((0.0, L), cuts):
                sc.line(T(lerp(a, b, s0 / L)), T(lerp(a, b, s1 / L)), C["cut"], LW["outline"])
                if inner:
                    sc.line(T(lerp(ia, ib, s0 / L)), T(lerp(ia, ib, s1 / L)),
                            C["cut"], LW["outline"])
            for (c0, c1) in cuts:
                p0, p1 = lerp(a, b, c0 / L), lerp(a, b, c1 / L)
                q0, q1 = lerp(ia, ib, c0 / L), lerp(ia, ib, c1 / L)
                sc.line(T(p0), T(q0), C["cut"], LW["detail"])
                sc.line(T(p1), T(q1), C["cut"], LW["detail"])
                g0 = lerp(ia, ib, (c0 + (c1 - c0) * 0.16) / L)
                g1 = lerp(ia, ib, (c1 - (c1 - c0) * 0.16) / L)
                sc.line(T(g0), T(g1), GLZ, LW["detail"])
        for i in range(n):
            c = fp[i]
            s = 0.5
            p0 = T.pt(c[0] - s / 2, c[1] - s / 2)
            p1 = T.pt(c[0] + s / 2, c[1] + s / 2)
            sc.rect(min(p0[0], p1[0]), min(p0[1], p1[1]), max(p0[0], p1[0]),
                    max(p0[1], p1[1]), fill=C["cut"], stroke=None)
        c = T(design_io.centroid(fp))
        plate(sc, c[0], c[1] - 1.2, str(m["id"]), 3.4)
        plate(sc, c[0], c[1] + 2.6, "%s  %dF" % (m.get("name", ""), _storeys(m, lh)), 2.3,
              C["text_l"])

    for el in els:
        if el.get("kind") == "stair":
            poly = [T(p) for p in el["polygon"]]
            sc.poly(poly, "#FFFFFF", C["cut"], LW["secondary"])
            x0, y0, x1, y1 = design_io.bbox(el["polygon"])
            nstep = max(3, int(round(max(x1 - x0, y1 - y0) / 0.28)))
            for kk in range(1, nstep):
                t = kk / float(nstep)
                if (x1 - x0) >= (y1 - y0):
                    xx = x0 + (x1 - x0) * t
                    sc.line(T.pt(xx, y0), T.pt(xx, y1), C["cut"], LW["hatch"])
                else:
                    yy = y0 + (y1 - y0) * t
                    sc.line(T.pt(x0, yy), T.pt(x1, yy), C["cut"], LW["hatch"])
            sc.arrow(T.pt(x0 + (x1 - x0) * 0.25, (y0 + y1) / 2.0),
                     T.pt(x0 + (x1 - x0) * 0.75, (y0 + y1) / 2.0), C["cut"], LW["detail"], 1.3)

    for el in els:
        if el.get("kind") == "slab" and float(el.get("base", 0) or 0) > z - 1e-6:
            sc.stroke([T(p) for p in el["polygon"]], C["dash"], LW["detail"], closed=True,
                      dash=(4, 1.6))

    for m in d["masses"]:
        if not mass_is_above(m, z):
            continue
        x0, y0, x1, y1 = design_io.bbox(m["footprint"])
        px0, py0 = T.pt(x0, y0)
        px1, py1 = T.pt(x1, y1)
        ylo, yhi = min(py0, py1), max(py0, py1)
        dim_h(sc, px0, px1, yhi + 6.5, "%.1f" % (x1 - x0), ext=(ylo, yhi))
        dim_v(sc, yhi, ylo, px0 - 6.5, "%.1f" % (y1 - y0), ext=(px0, px1))
    return z, level_name


# ==========================================================================
# 三、剖面 / 立面
# ==========================================================================
def build_section(sc, d, T, p_from, p_to):
    return _section_like(sc, d, T, p_from, p_to, cut=True)


def build_elevation(sc, d, T, p_from, p_to):
    return _section_like(sc, d, T, p_from, p_to, cut=False)


def _section_like(sc, d, T, p_from, p_to, cut=True):
    lh = design_io.level_height(d)
    p0 = (float(p_from[0]), float(p_from[1]))
    p1 = (float(p_to[0]), float(p_to[1]))
    L = math.dist(p0, p1)
    u = ((p1[0] - p0[0]) / L, (p1[1] - p0[1]) / L)

    def proj(q):
        return (q[0] - p0[0]) * u[0] + (q[1] - p0[1]) * u[1]

    def at(s):
        return (p0[0] + u[0] * s, p0[1] + u[1] * s)

    ground = -0.15
    for lv in d["site"].get("levels", []):
        if lv.get("name") in ("室外地面", "室外地坪", "ground", "室外地坪标高"):
            ground = float(lv["elevation"])

    items = []
    for m in d["masses"]:
        fp = design_io.as_pts(m["footprint"])
        ts = [t for t in design_io.segment_line_intersections(p0, p1, fp) if -0.02 <= t <= 1.02]
        ivs = []
        i = 0
        while i + 1 < len(ts):
            s0, s1 = ts[i] * L, ts[i + 1] * L
            if s1 - s0 > 0.05 and design_io.point_in_poly(at((s0 + s1) / 2), fp):
                ivs.append((s0, s1))
            i += 2
        pr = [proj(q) for q in fp]
        items.append((m, ivs, (min(pr), max(pr))))

    for m, ivs, span in items:
        top = design_io.mass_top(m)
        if cut and ivs:
            for (s0, s1) in ivs:
                steps = 28
                prof = []
                for kk in range(steps + 1):
                    s = s1 - (s1 - s0) * kk / float(steps)
                    pt = at(s)
                    prof.append((s, design_io.roof_top_at(m, pt[0], pt[1])))
                poly = [(s0, ground)] + prof + [(s1, ground)]
                pp = [T((s, zz)) for (s, zz) in poly]
                sc.poly(pp, "#EDEBE4", C["cut"], LW["outline"])
                sc.hatch(pp, spacing=1.5, angle=45.0, color="#B9B6AC", w=LW["hatch"])
                st = _storeys(m, lh)
                base = float(m.get("base", 0.0))
                for kk in range(1, st):
                    zz = base + kk * lh
                    sc.line(T((s0, zz)), T((s1, zz)), C["cut"], LW["secondary"])
        else:
            s0, s1 = span
            poly = [(s0, ground), (s0, design_io.roof_top_at(m, *at(s0))),
                    (s1, design_io.roof_top_at(m, *at(s1))), (s1, ground)]
            sc.poly([T((s, zz)) for (s, zz) in poly], "#F7F6F2", "#9C9C94", LW["detail"])
        c = (span[0] + span[1]) / 2.0
        pc = T((c, top))
        plate(sc, pc[0], pc[1] - 3.4, str(m["id"]), 3.2, C["text"])

    sc.line(T((-6.0, ground)), T((L + 6.0, ground)), C["cut"], LW["outline"])
    band = [(-6.0, ground), (L + 6.0, ground), (L + 6.0, ground - 3.4), (-6.0, ground - 3.4)]
    sc.hatch([T(p) for p in band], spacing=2.0, angle=45.0, color="#C9C5B8", w=LW["hatch"])

    if cut:
        xa = T((0.0, 0.0))[0]
        for lv in d["site"].get("levels", []):
            zz = float(lv["elevation"])
            if zz < ground - 0.01:
                continue
            y = T((0.0, zz))[1]
            sc.line((xa - 13.0, y), (xa - 2.0, y), C["dash"], LW["hatch"])
            sc.poly([(xa - 9.0, y), (xa - 7.4, y - 2.2), (xa - 10.6, y - 2.2)], "#FFFFFF",
                    C["dash"], LW["hatch"])
            sc.text(xa - 12.0, y + 1.0, "%+.3f" % zz, 2.4, C["text"], "rm")

    top_z = max([design_io.mass_top(m) for m in d["masses"]] or [10.0])
    dim_v(sc, T((0.0, ground))[1], T((0.0, top_z))[1], T((L, 0.0))[0] + 8.0,
          "%.2f m" % (top_z - ground))


# ==========================================================================
# 组装
# ==========================================================================
SCALE_LADDER = [50, 100, 150, 200, 250, 300, 400, 500, 750, 1000, 1500, 2000]


def _pick_scale(bw, bh, aw, ah, want):
    for s in [x for x in SCALE_LADDER if x >= want] or [SCALE_LADDER[-1]]:
        if bw * 1000.0 / s <= aw and bh * 1000.0 / s <= ah:
            return s
    return SCALE_LADDER[-1]


def build(d, mode="siteplan", paper="A1", scale=None, level=None):
    """组装一张图的完整场景，返回 (scene, info)。"""
    w, h = PAPER[paper]
    sc = Scene(w, h, load_font())
    ax0, ay0, ax1, ay1 = drawing_area(w, h)
    dw = d.get("drawings", {}) or {}
    notes = []

    if scale is None:
        scale = {"siteplan": dw.get("siteplan_scale", 500),
                 "plan": dw.get("plan_scale", 200),
                 "section": dw.get("section_scale", 200),
                 "elevation": dw.get("section_scale", 200)}.get(mode, 200)

    pf = dw.get("section_from", [0.0, 0.0])
    pt = dw.get("section_to", [1.0, 0.0])
    L = math.dist(pf, pt)
    top = max([design_io.mass_top(m) for m in d["masses"]] or [10.0])

    if mode == "siteplan":
        box = site_bbox(d, pad=6.0)
    elif mode == "plan":
        box = masses_bbox(d, pad=9.0)
    else:
        box = (0.0, -3.4, L, top + 3.0)

    bw, bh = box[2] - box[0], box[3] - box[1]
    got = _pick_scale(bw, bh, ax1 - ax0, ay1 - ay0, scale)
    if got != scale:
        notes.append("请求 1:%d 超出幅面，按标准比例梯降为 1:%d" % (scale, got))
        scale = got

    T = ToPaper(scale, (ax0 + ax1) / 2.0, (ay0 + ay1) / 2.0,
                (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
    info = {"scale": scale, "mode": mode, "paper": paper, "notes": notes}
    met = design_io.compute_metrics(d)
    dn = dw.get("drawing_number", "A-00")
    pname = d["project"]["name"]
    date = d["project"].get("date", "")
    lh = design_io.level_height(d)

    if mode == "siteplan":
        build_siteplan(sc, d, T)
        draw_frame(sc, "总平面图", "1:%d" % scale, dn + "-01", pname, date, "总图")
        draw_north(sc, ax1 - 30.0, ay0 + 26.0, 10.0, float(d["site"].get("north_deg", 0)))
        draw_scale_bar(sc, ax0 + 5.0, ay1 - 28.0, scale, 40.0, 4)
        draw_legend(sc, ax0 + 128.0, ay1 - 44.0, [
            ("建筑（新建）", "fill", C["concrete"]), ("绿地 / 草坪", "fill", C["green_l"]),
            ("道路 / 场地", "fill", C["road"]), ("广场 / 铺装", "fill", C["plaza"]),
            ("水面", "fill", C["water"]), ("乔木", "circle", C["tree"]),
            ("用地红线", "line", C["site"]), ("退线控制线", "dash", C["dash"]),
            ("悬挑投影", "dash", C["dash"]), ("等高线", "line", "#9C9C90"),
        ], cols=2, rowh=7.0, colw=54.0)
        sb = d["site"].get("setbacks", {}) or {}
        draw_notes(sc, ax0 + 250.0, ay1 - 44.0, [
            "1. 比例 1:%d，尺寸单位米，标高单位米。" % scale,
            "2. 用地面积 %.1f m2，建筑密度 %.1f%%，容积率 %.3f，限高 %.1f m。"
            % (met["site_area"], met["density"] * 100, met["far"], met["max_height"]),
            "3. 退线：前 %.1f / 后 %.1f / 左 %.1f / 右 %.1f m。"
            % (sb.get("front", 0), sb.get("back", 0), sb.get("left", 0), sb.get("right", 0)),
            "4. 绿地率 %.1f%%（扣除法：用地减基底、硬化与水面）。" % (met["greenRatio"] * 100),
            "5. 虚线为地上悬挑投影，细单点划线为退线控制线。",
        ])
    elif mode == "plan":
        zi = int(level if level is not None else dw.get("plan_level", 1))
        z, lname = build_plan(sc, d, T, zi)
        draw_frame(sc, "%s 平面图" % lname, "1:%d" % scale, dn + "-02", pname, date, "建筑")
        draw_north(sc, ax1 - 30.0, ay0 + 26.0, 10.0, float(d["site"].get("north_deg", 0)))
        draw_scale_bar(sc, ax0 + 5.0, ay1 - 28.0, scale, 20.0, 4)
        draw_legend(sc, ax0 + 128.0, ay1 - 40.0, [
            ("剖到的墙体", "fill", C["cut"]), ("门窗洞口", "line", GLZ),
            ("楼梯", "line", C["cut"]), ("上方悬挑投影", "dash", C["dash"]),
            ("用地边界", "dash", "#C8C8C8"), ("结构柱示意", "fill", C["cut"]),
        ], cols=2, rowh=7.0, colw=54.0)
        draw_notes(sc, ax0 + 250.0, ay1 - 44.0, [
            "1. 本层标高 %+.3f m，层高 %.2f m。" % (z, lh),
            "2. 墙厚 240 mm；洞口由 design.json 的参数式 opening 展开。",
            "3. 角部方块为 500x500 结构柱示意，柱网待结构专业确认。",
            "4. 虚线表示本层上方悬挑或上层轮廓投影。",
        ])
        info["level"] = zi
    else:
        if mode == "section":
            build_section(sc, d, T, pf, pt)
            draw_frame(sc, "1-1 剖面图", "1:%d" % scale, dn + "-03", pname, date, "建筑")
        else:
            build_elevation(sc, d, T, pf, pt)
            draw_frame(sc, "南立面图", "1:%d" % scale, dn + "-04", pname, date, "建筑")
        draw_scale_bar(sc, ax0 + 5.0, ay1 - 28.0, scale, 20.0, 4)
        draw_notes(sc, ax0 + 128.0, ay1 - 44.0, [
            "1. 剖切位置 %s -> %s（design.json drawings.section_*）。" % (pf, pt),
            "2. 剖到的构件填充 45 度细线；未剖到的构件以细线投影。",
            "3. 层高按 project.level_height = %.2f m 推算。" % lh,
            "4. 标高单位为米，室内 +/-0.000 对应 site.levels 中的“一层”。",
        ])
        info["cut"] = [pf, pt]

    info["items"] = len(sc.items)
    info["notes"] = notes
    return sc, info


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="draw2d 图元层自检（不输出文件）")
    ap.add_argument("design")
    ap.add_argument("--mode", default="siteplan",
                    choices=["siteplan", "plan", "section", "elevation"])
    ap.add_argument("--paper", default="A1", choices=["A1", "A2", "A3"])
    ap.add_argument("--scale", type=int, default=None)
    ap.add_argument("--level", type=int, default=None)
    a = ap.parse_args(argv)
    d = design_io.load_design(a.design)
    sc, info = build(d, a.mode, a.paper, a.scale, a.level)
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
