#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""to_pdf.py -- design.json -> 图纸 PDF 与 PNG 预览。

用法:
    python to_pdf.py <design.json> --out <dir> [--sheets siteplan,plan,section]

sheet 的组装由 svg_draw.py 负责（其 build_sheets(design, out_dir) / SHEETS 接口）；
本文件只做「取 scene -> pdf_backend / png_backend 出图」。当 svg_draw.py 尚未就位
或导入失败时，回退到 draw2d.build(design, mode, paper) —— draw2d 本身就是
design.json 的场景组装器，同一份图元流水因此仍然能出图。
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import design_io
import draw2d
import pdf_backend
import png_backend

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DEFAULT_SHEETS = ("siteplan", "plan", "section")


def _from_svg_draw(design, out_dir):
    """尝试用 svg_draw 组装 sheet，返回 {sheet: Scene}；不可用时返回 {}。"""
    try:
        import svg_draw
    except Exception as exc:
        sys.stderr.write("提示: svg_draw.py 不可用（%s），改用 draw2d.build 组装。\n" % exc)
        return {}
    fn = getattr(svg_draw, "build_sheets", None)
    if not callable(fn):
        return {}
    try:
        got = fn(design, out_dir)
    except Exception as exc:
        sys.stderr.write("提示: svg_draw.build_sheets 调用失败（%s），改用 draw2d.build。\n" % exc)
        return {}
    out = {}
    if isinstance(got, dict):
        for k, v in got.items():
            if isinstance(v, draw2d.Scene):
                out[str(k)] = v
    elif isinstance(got, (list, tuple)):
        for row in got:
            if isinstance(row, (list, tuple)) and len(row) == 2 \
                    and isinstance(row[1], draw2d.Scene):
                out[str(row[0])] = row[1]
    return out


def _scene_for(design, sheet, paper):
    if sheet in ("siteplan", "plan", "section", "elevation"):
        sc, info = draw2d.build(design, mode=sheet, paper=paper)
        return sc, info
    raise ValueError("未知图幅 %r（可用: siteplan/plan/section/elevation）" % (sheet,))


def main(argv=None):
    ap = argparse.ArgumentParser(description="design.json -> PDF 图纸 + PNG 预览")
    ap.add_argument("design")
    ap.add_argument("--out", required=True)
    ap.add_argument("--sheets", default=",".join(DEFAULT_SHEETS))
    ap.add_argument("--paper", default="A1", choices=sorted(draw2d.PAPER))
    ap.add_argument("--dpi", type=int, default=150)
    a = ap.parse_args(argv)

    d = design_io.load_design(a.design)
    out_dir = os.path.abspath(a.out)
    prev_dir = os.path.join(out_dir, "preview")
    for p in (out_dir, prev_dir):
        if not os.path.isdir(p):
            os.makedirs(p)

    sheets = [s.strip() for s in a.sheets.split(",") if s.strip()]
    shared = _from_svg_draw(d, out_dir)
    rc = 0
    for sheet in sheets:
        try:
            if sheet in shared:
                sc, src = shared[sheet], "svg_draw"
            else:
                sc, info = _scene_for(d, sheet, a.paper)
                src = "draw2d"
            pdf_path = os.path.join(out_dir, sheet + ".pdf")
            png_path = os.path.join(prev_dir, sheet + ".png")
            n_pdf = pdf_backend.write_pdf(sc, pdf_path)
            n_png = png_backend.write_png(sc, png_path, dpi=a.dpi)
            print("%-10s %-8s %.0fx%.0f mm  items=%-5d %s" %
                  (sheet, src, sc.w, sc.h, len(sc.items), ",".join(sorted(
                      "%s=%.0fB" % (e, n) for e, n in
                      (("pdf", n_pdf), ("png", n_png))))))
        except Exception as exc:
            sys.stderr.write("错误: %s 出图失败: %s\n" % (sheet, exc))
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
