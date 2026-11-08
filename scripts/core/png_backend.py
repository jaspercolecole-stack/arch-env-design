# -*- coding: utf-8 -*-
"""png_backend.py -- Scene 图元流水 -> Pillow 位图（唯一能「看见图」的通道）。

与 svg_backend / pdf_backend 共用同一份 Scene，因此三者视觉一致：
fill → polygon，stroke → 逐段 line（closed 闭合、dash 按 on/off 切段），
text → simhei.ttf，锚点为 item 的 x、基线为 item 的 y（与 SVG 的 y 属性同义）。
"""

import math
import os
import sys

from PIL import Image, ImageDraw, ImageFont

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import draw2d

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

FONT_PATH = r"C:\Windows\Fonts\simhei.ttf"
FONT_CANDIDATES = [FONT_PATH] + [p for p in draw2d.FONT_CANDIDATES if p != FONT_PATH]

_WARNED = set()
_FONT_CACHE = {}


def _warn_once(key, msg):
    if key in _WARNED:
        return
    _WARNED.add(key)
    sys.stderr.write(msg)


# ==========================================================================
# 颜色 / 字体
# ==========================================================================
def _rgba(color, alpha=None):
    try:
        r, g, b = draw2d.hex_rgb(color)
    except Exception:
        r = g = b = 0.0
    a = 1.0 if alpha is None else max(0.0, min(1.0, float(alpha)))
    return (int(round(r * 255)), int(round(g * 255)), int(round(b * 255)),
            int(round(a * 255)))


def _is_page_bg(it, scene):
    """整页纸色填充视为页面背景（draw2d.build 画完图形后还会再补一次纸底），
    画布已经是该颜色，跳过它，否则会把已画的内容整片盖掉。"""
    if it.get("alpha") is not None:
        return False
    try:
        c = draw2d.hex_rgb(it.get("color"))
        p = draw2d.hex_rgb(draw2d.C["paper"])
    except Exception:
        return False
    if any(abs(c[i] - p[i]) > 1e-6 for i in range(3)):
        return False
    xs = [q[0] for q in it["pts"]]
    ys = [q[1] for q in it["pts"]]
    return (max(xs) - min(xs)) >= scene.w * 0.99 and (max(ys) - min(ys)) >= scene.h * 0.99


def _font(px):
    px = max(1, int(round(px)))
    if px in _FONT_CACHE:
        return _FONT_CACHE[px]
    f = None
    for p in FONT_CANDIDATES:
        if not os.path.exists(p):
            continue
        try:
            f = ImageFont.truetype(p, px)
            break
        except Exception:
            continue
    if f is None:
        _warn_once("nofont", "警告: 无法加载中文字体 %s，PNG 文字降级为内置位图字体\n"
                   % FONT_PATH)
    _FONT_CACHE[px] = f
    return f


# ==========================================================================
# 虚线切段
# ==========================================================================
def _lerp(p0, p1, t):
    return (p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t)


def _dash_runs(pts, on, off, closed):
    """把折线按 on/off 切成实线段，返回 [(p0, p1), ...]（与 pts 同单位）。"""
    if on <= 1e-9:
        return []
    seq = list(pts) + ([pts[0]] if closed else [])
    if off <= 1e-9:
        return [(seq[i], seq[i + 1]) for i in range(len(seq) - 1)]
    runs = []
    drawing = True
    left = on
    cur = seq[0]
    guard = 0
    for nxt in seq[1:]:
        seg = math.dist(cur, nxt)
        if seg < 1e-12:
            cur = nxt
            continue
        t = 0.0
        while seg - t > 1e-9 and guard < 200000:
            guard += 1
            step = min(left, seg - t)
            t2 = t + step
            if drawing:
                runs.append((_lerp(cur, nxt, t / seg), _lerp(cur, nxt, t2 / seg)))
            t = t2
            left -= step
            if left <= 1e-9:
                drawing = not drawing
                left = off if drawing else on
        cur = nxt
    return runs


# ==========================================================================
# 图元
# ==========================================================================
def _draw_stroke(d, it, scale):
    pts = [(x * scale, y * scale) for (x, y) in (it.get("pts") or [])]
    if len(pts) < 2:
        return
    col = _rgba(it.get("color"))
    wpx = max(1, int(round(float(it.get("w") or draw2d.LW["detail"]) * scale)))
    closed = bool(it.get("closed"))
    dash = it.get("dash")
    if dash:
        runs = _dash_runs(pts, max(float(dash[0]), 0.0) * scale,
                          max(float(dash[1]), 0.0) * scale, closed)
        for (p0, p1) in runs:
            d.line([p0, p1], fill=col, width=wpx)
        return
    seq = pts + ([pts[0]] if closed else [])
    d.line(seq, fill=col, width=wpx, joint="curve")
    if it.get("cap") == "round" and wpx > 2 and not closed:
        r = wpx / 2.0
        for (x, y) in (pts[0], pts[-1]):
            d.ellipse([x - r, y - r, x + r, y + r], fill=col)


def _draw_text(d, it, scale):
    s = it.get("s") or ""
    if not s:
        return
    size = float(it.get("size") or 0.0)
    x = float(it.get("x") or 0.0) * scale
    y = float(it.get("y") or 0.0) * scale
    col = _rgba(it.get("color"))
    f = _font(size * scale)
    if f is not None:
        try:
            d.text((x, y), s, font=f, fill=col, anchor="ls")
            return
        except Exception:
            pass
    d.text((x, y - max(1.0, size * scale * 0.8)), s, fill=col)


# ==========================================================================
# 出图
# ==========================================================================
def scene_to_png(scene, dpi=150):
    dpi = float(dpi)
    scale = dpi / 25.4
    w = max(1, int(round(scene.w * scale)))
    h = max(1, int(round(scene.h * scale)))
    img = Image.new("RGB", (w, h), draw2d.C["paper"])
    d = ImageDraw.Draw(img, "RGBA")
    for it in scene.items:
        t = it.get("t")
        if t == "fill":
            pts = it.get("pts") or []
            if len(pts) < 3 or _is_page_bg(it, scene):
                continue
            d.polygon([(x * scale, y * scale) for (x, y) in pts],
                      fill=_rgba(it.get("color"), it.get("alpha")))
        elif t == "stroke":
            _draw_stroke(d, it, scale)
        elif t == "text":
            _draw_text(d, it, scale)
    img.info["dpi"] = (dpi, dpi)
    return img


def write_png(scene, path, dpi=150):
    """写出 PNG，返回字节数。"""
    img = scene_to_png(scene, dpi)
    p = os.path.abspath(path)
    d = os.path.dirname(p)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    img.save(p, "PNG", dpi=(float(dpi), float(dpi)))
    return os.path.getsize(p)
