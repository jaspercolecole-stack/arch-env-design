# -*- coding: utf-8 -*-
"""pdf_backend.py -- Scene 图元流水 -> 纯 Python PDF（单页，文字以字形轮廓填充）。

只依赖标准库 + draw2d：不嵌字体、不用任何第三方 PDF 库。
内容流以 `1 0 0 -1 0 <h_pt> cm` 起头，把 PDF 的 y 轴翻回「原点左上、y 向下」，
因此 Scene 的毫米坐标可以按原样写进内容流（写之前统一乘 MM2PT 换成 pt）。
文字走 `scene.font.contours()` 的字形轮廓路径 + 非零环绕填充，不嵌字体。
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import draw2d

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

MM2PT = 72.0 / 25.4          # 2.8346456692913385

_WARNED = set()


def _warn_once(key, msg):
    if key in _WARNED:
        return
    _WARNED.add(key)
    sys.stderr.write(msg)


# ==========================================================================
# 数值与路径
# ==========================================================================
def _n(v, nd=4):
    """PDF 数字：定点小数，去掉尾零。"""
    s = ("%." + str(nd) + "f") % float(v)
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    if s in ("", "-", "-0"):
        return "0"
    return s


def _rgb(color):
    """#rrggbb -> "r g b"（0-1，4 位小数）。"""
    try:
        r, g, b = draw2d.hex_rgb(color)
    except Exception:
        r = g = b = 0.0
    return "%.4f %.4f %.4f" % (r, g, b)


def _xy(x, y):
    return "%s %s" % (_n(x * MM2PT), _n(y * MM2PT))


def _path_ops(pts, close=False):
    """毫米点列 -> m / l / h 操作符。"""
    ops = []
    if not pts:
        return ops
    ops.append(_xy(pts[0][0], pts[0][1]) + " m")
    for (x, y) in pts[1:]:
        ops.append(_xy(x, y) + " l")
    if close:
        ops.append("h")
    return ops


def _glyph_ops(font, gi, pen, base, s, ops):
    """字形轮廓（字体单位、y 向上）按 size/upem 缩放后追加到 ops。

    Scene 用 y 向下，故字形 y 取反：y = base - gy * s。
    """
    n0 = len(ops)
    for ctr in font.contours(gi):
        if len(ctr) < 3:
            continue
        ops.append(_xy(pen + ctr[0][0] * s, base - ctr[0][1] * s) + " m")
        for (gx, gy) in ctr[1:]:
            ops.append(_xy(pen + gx * s, base - gy * s) + " l")
        ops.append("h")
    return len(ops) > n0


def _text_ops(scene, it):
    """返回 (操作符列表, 绘制方式)，方式为 "fill" 或 "stroke"（降级方框）。"""
    txt = it.get("s") or ""
    size = float(it.get("size") or 0.0)
    x = float(it.get("x") or 0.0)
    y = float(it.get("y") or 0.0)
    f = scene.font
    if f is None:
        _warn_once("nofont", "警告: scene.font 为空，PDF 文字降级为方框占位\n")
        w = float(it.get("w") or max(len(txt), 1) * size * 0.6)
        box = [(x, y - size * 0.78), (x + w, y - size * 0.78),
               (x + w, y + size * 0.14), (x, y + size * 0.14)]
        return _path_ops(box, close=True), "stroke"
    if not txt or size <= 0:
        return [], "fill"
    s = size / float(f.upem)
    ops = []
    pen = x
    for ch in txt:
        gi = f.glyph_id(ch)
        if gi:
            _glyph_ops(f, gi, pen, y, s, ops)
            pen += f.advance(gi) * s
        else:
            _warn_once("missing:" + ch, "警告: 字体缺字形 U+%04X，已跳过\n" % ord(ch))
            pen += f.upem * 0.5 * s
    return ops, "fill"


# ==========================================================================
# 内容流
# ==========================================================================
def _alpha_map(scene):
    """收集需要透明度的取值，映射到 ExtGState 名。"""
    vals = set()
    for it in scene.items:
        if it.get("t") != "fill":
            continue
        al = it.get("alpha")
        if al is None:
            continue
        al = float(al)
        if 0.0 <= al < 1.0:
            vals.add(round(al, 3))
    return dict((v, "GS%d" % (i + 1)) for i, v in enumerate(sorted(vals)))


def _extgstate(amap):
    if not amap:
        return ""
    parts = []
    for v, name in sorted(amap.items()):
        parts.append("/%s << /Type /ExtGState /ca %s /CA %s >>"
                     % (name, _n(v, 3), _n(v, 3)))
    return "/ExtGState << %s >>" % " ".join(parts)


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


def _content_stream(scene):
    w_pt = scene.w * MM2PT
    h_pt = scene.h * MM2PT
    amap = _alpha_map(scene)
    out = []
    a = out.append

    a("q")
    a("1 0 0 -1 0 %s cm" % _n(h_pt, 4))
    a("%s rg" % _rgb(draw2d.C["paper"]))
    a("0 0 %s %s re" % (_n(w_pt, 4), _n(h_pt, 4)))
    a("f")

    for it in scene.items:
        t = it.get("t")
        if t == "fill":
            pts = it.get("pts") or []
            if len(pts) < 3 or _is_page_bg(it, scene):
                continue
            a("q")
            al = it.get("alpha")
            if al is not None:
                name = amap.get(round(float(al), 3))
                if name:
                    a("/%s gs" % name)
            a("%s rg" % _rgb(it.get("color")))
            out.extend(_path_ops(pts, close=True))
            a("f")
            a("Q")
        elif t == "stroke":
            pts = it.get("pts") or []
            if len(pts) < 2:
                continue
            a("q")
            a("%s RG" % _rgb(it.get("color")))
            a("%s w" % _n(float(it.get("w") or draw2d.LW["detail"]) * MM2PT))
            a("%d J" % (1 if it.get("cap") == "round" else 0))
            a("%d j" % (1 if it.get("join") in (None, "round") else 0))
            dash = it.get("dash")
            if dash:
                a("[%s %s] 0 d" % (_n(float(dash[0]) * MM2PT), _n(float(dash[1]) * MM2PT)))
            else:
                a("[] 0 d")
            out.extend(_path_ops(pts, close=bool(it.get("closed"))))
            a("S")
            a("Q")
        elif t == "text":
            ops, kind = _text_ops(scene, it)
            if not ops:
                continue
            a("q")
            if kind == "fill":
                a("%s rg" % _rgb(it.get("color")))
                out.extend(ops)
                a("f")
            else:
                a("%s RG" % _rgb(it.get("color")))
                a("%s w" % _n(draw2d.LW["hatch"] * MM2PT))
                out.extend(ops)
                a("S")
            a("Q")

    a("Q")
    return out


# ==========================================================================
# 文件结构
# ==========================================================================
def _compose(objs):
    """objs 为第 1..N 号对象的字节内容，按顺序拼出带 xref 的 PDF。"""
    out = bytearray()
    out += b"%PDF-1.4\n"
    out += b"%\xe2\xe3\xcf\xd3\n"
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i
        out += body
        out += b"\nendobj\n"
    xref_off = len(out)
    n = len(objs) + 1
    out += b"xref\n0 %d\n" % n
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\n" % n
    out += b"startxref\n%d\n" % xref_off
    out += b"%%EOF\n"
    return bytes(out)


def scene_to_pdf_bytes(scene):
    """Scene -> 一页 PDF 的完整字节串。"""
    content = "\n".join(_content_stream(scene)) + "\n"
    body = content.encode("ascii", "replace")

    res = "<</ProcSet [/PDF]%s>>" % _extgstate(_alpha_map(scene))
    page = ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %s %s] "
            "/Resources %s /Contents 4 0 R >>"
            % (_n(scene.w * MM2PT, 4), _n(scene.h * MM2PT, 4), res)).encode("ascii")

    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        page,
        b"<< /Length %d >>\nstream\n" % len(body) + body + b"endstream",
    ]
    return _compose(objs)


def write_pdf(scene, path):
    """写出 PDF，返回字节数。"""
    data = scene_to_pdf_bytes(scene)
    p = os.path.abspath(path)
    d = os.path.dirname(p)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with open(p, "wb") as f:
        f.write(data)
    return len(data)
