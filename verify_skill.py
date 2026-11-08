# -*- coding: utf-8 -*-
"""arch-env-design 交付验收器（父代理用，不属于技能本体）。

跑法：python verify_skill.py [--skill <skill 目录>] [--work <临时输出目录>]
逐项 PASS/FAIL，最后打印汇总与退出码（0 = 全通过）。
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile

PY = sys.executable
HERE = os.path.dirname(os.path.abspath(__file__))


def run(args, cwd=None, timeout=300):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([PY] + args, cwd=cwd, capture_output=True, timeout=timeout, env=env)
    out = p.stdout.decode("utf-8", "replace")
    err = p.stderr.decode("utf-8", "replace")
    return p.returncode, out, err


def load_json(path):
    raw = open(path, "rb").read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return json.loads(raw.decode("utf-16"))
    return json.loads(raw.decode("utf-8-sig"))


class Report:
    def __init__(self):
        self.rows = []

    def add(self, name, ok, detail=""):
        self.rows.append((name, bool(ok), detail))
        print(("%-28s %s  %s" % (name, "PASS" if ok else "FAIL", detail)).rstrip())

    def summary(self):
        fails = [r for r in self.rows if not r[1]]
        print("\n%d/%d PASS" % (len(self.rows) - len(fails), len(self.rows)))
        for name, _, detail in fails:
            print("  FAIL %s  %s" % (name, detail))
        return 1 if fails else 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--skill", default=os.path.join(HERE, "skill"))
    ap.add_argument("--work", default="")
    a = ap.parse_args(argv)
    skill = os.path.abspath(a.skill)
    core = os.path.join(skill, "scripts", "core")
    demo = os.path.join(skill, "examples", "demo-campus", "design.json")
    work = os.path.abspath(a.work or os.path.join(HERE, "_verify_out"))
    os.makedirs(work, exist_ok=True)
    R = Report()

    # 1 文件骨架
    need = [
        "SKILL.md", "references/workflow.md", "references/cases.md", "references/codes-cn.md",
        "references/codes-intl.md", "references/drawing-standards.md", "references/aesthetics.md",
        "references/checklist.md", "references/interoperability.md", "references/report-deck.md",
        "scripts/core/design.schema.json", "scripts/core/design_io.py", "scripts/core/draw2d.py",
        "scripts/core/svg_draw.py", "scripts/core/svg_backend.py", "scripts/core/pdf_backend.py",
        "scripts/core/png_backend.py", "scripts/core/to_pdf.py", "scripts/core/dxf_export.py",
        "scripts/core/gltf_export.py", "scripts/core/schema_lint.py", "scripts/cases/find.py", "scripts/check/checklist.py",
        "scripts/interop/run_ext.py", "assets/case-library.json", "assets/palette.json", "examples/demo-campus/design.json",
    ]
    missing = [p for p in need if not os.path.isfile(os.path.join(skill, p))]
    R.add("文件骨架", not missing, "缺 %s" % missing if missing else "%d 个必需文件齐" % len(need))
    if missing:
        return R.summary()

    # 2 指标真算
    rc, out, err = run([os.path.join(core, "design_io.py"), "check", demo, "--json"])
    try:
        io = json.loads(out)
    except ValueError:
        R.add("design_io check", False, "输出不是 JSON: " + (err or out)[:160])
        return R.summary()
    met = io.get("metrics") or {}
    want = {"site_area": 14772.0, "building_area": 27120.0, "footprint_area": 5704.0,
            "far": 1.8359, "density": 0.3861, "greenRatio": 0.4651, "max_height": 21.6}
    bad = {k: (met.get(k), v) for k, v in want.items() if not isinstance(met.get(k), (int, float)) or abs(met[k] - v) > 5e-4}
    R.add("design_io check", rc == 0 and io.get("ok") and not bad, "ok=%s 偏差=%s" % (io.get("ok"), bad or "无"))

    # 3 案例库
    lib = load_json(os.path.join(skill, "assets", "case-library.json"))
    cases = lib.get("cases") or []
    vocab = lib.get("tag_vocabulary") or {}
    allowed = set()
    for v in vocab.values():
        allowed.update(v if isinstance(v, list) else [])
    out_of_vocab = [c.get("id") for c in cases if any(t not in allowed for t in (c.get("tags") or []))]
    R.add("案例库封闭性", len(cases) >= 28 and not out_of_vocab,
          "%d 条 / 词表 %d / 越界 %s" % (len(cases), len(allowed), out_of_vocab or "无"))

    # 4 检索召回
    queries = ["滨水修复", "旧厂房改造", "校园规划", "山地建筑", "高密度住宅",
               "儿童活动场地", "屋顶花园", "雨水花园", "日照间距", "无障碍入口"]
    empty = []
    for q in queries:
        rc2, out2, err2 = run([os.path.join(skill, "scripts", "cases", "find.py"), "--problem", q, "--json", "--limit", "3"])
        try:
            hits = json.loads(out2)
        except ValueError:
            empty.append(q + "(非JSON)")
            continue
        items = hits.get("results") if isinstance(hits, dict) else hits
        if not items:
            empty.append(q)
    R.add("案例检索非空", not empty, "空结果 %s" % (empty or "无（10/10 有结果）"))

    # 5 三阶段清单
    for stage in ("概念", "方案", "深化"):
        rc3, out3, err3 = run([os.path.join(skill, "scripts", "check", "checklist.py"), demo, "--stage", stage, "--json"])
        try:
            d3 = json.loads(out3)
        except ValueError:
            R.add("checklist " + stage, False, (err3 or out3)[:160])
            continue
        st = collections.Counter(r.get("status") for r in d3.get("results") or [])
        unver = st.get("UNVERIFIED", 0)
        total = sum(st.values())
        R.add("checklist " + stage, rc3 == 0 and st.get("FAIL", 0) == 0 and unver <= 2,
              "PASS %d / FAIL %d / WARN %d / UNVERIFIED %d / 共 %d" % (
                  st.get("PASS", 0), st.get("FAIL", 0), st.get("WARN", 0), unver, total))

    # 5b schema 校验器
    rc_l, out_l, err_l = run([os.path.join(core, "schema_lint.py"), demo, "--json"])
    ok_l = rc_l == 0
    det_l = "exit=%d" % rc_l
    try:
        jl = json.loads(out_l)
        ok_l = ok_l and bool(jl.get("ok")) and not jl.get("errors")
        det_l = "ok=%s errors=%d warnings=%d" % (jl.get("ok"), len(jl.get("errors") or []), len(jl.get("warnings") or []))
    except ValueError:
        ok_l = False
        det_l += " 输出不是 JSON: " + (err_l or out_l)[:120]
    # 反例必须被拒：抽一条必填缺失
    broken = json.loads(open(demo, "rb").read().decode("utf-8-sig"))
    broken.pop("site", None)  # 根 required 里有 site，删掉必须被拒
    bp = os.path.join(work, "broken.json")
    open(bp, "w", encoding="utf-8").write(json.dumps(broken, ensure_ascii=False))
    rc_b, out_b, err_b = run([os.path.join(core, "schema_lint.py"), bp, "--json"])
    rej = rc_b == 1
    R.add("schema 校验器", ok_l and rej, det_l + " / 反例 exit=%d" % rc_b)

    # 6 SVG
    svgdir = os.path.join(work, "svg")
    rc4, out4, err4 = run([os.path.join(core, "svg_draw.py"), demo, "--out", svgdir, "--sheets", "siteplan,plan,section"])
    ok_svg = rc4 == 0
    detail = []
    try:
        from lxml import etree
        for name in ("siteplan", "plan", "section"):
            p = os.path.join(svgdir, name + ".svg")
            if not os.path.isfile(p):
                ok_svg = False
                detail.append(name + ":缺")
                continue
            root = etree.parse(p).getroot()
            ns = "{http://www.w3.org/2000/svg}"
            n = sum(len(list(root.iter(ns + k))) for k in ("polygon", "polyline", "text", "path"))
            w = root.get("width")
            detail.append("%s:%d图元/%s" % (name, n, w))
            if n < 300 or w != "841mm":
                ok_svg = False
    except ImportError:
        detail.append("无 lxml")
        ok_svg = False
    R.add("SVG 出图", ok_svg, " ".join(detail))

    # 7 PDF + PNG
    rc5, out5, err5 = run([os.path.join(core, "to_pdf.py"), demo, "--out", work])
    srcs = collections.Counter(l.split()[1] for l in out5.splitlines() if len(l.split()) > 2 and l.split()[1] in ("svg_draw", "draw2d"))
    pdfs, pngs = [], []
    for name in ("siteplan", "plan", "section"):
        pdfs.append(os.path.join(work, name + ".pdf"))
        pngs.append(os.path.join(work, "preview", name + ".png"))
    pdf_ok = True
    for p in pdfs:
        if not os.path.isfile(p):
            pdf_ok = False
            continue
        raw = open(p, "rb").read()
        if not (raw.startswith(b"%PDF-") and raw.rstrip().endswith(b"%%EOF") and b"/Type /Page" in raw):
            pdf_ok = False
    png_ok = all(os.path.isfile(p) and os.path.getsize(p) > 20000 for p in pngs)
    R.add("PDF 出图", rc5 == 0 and pdf_ok, "source=%s sizes=%s" % (dict(srcs), [os.path.getsize(p) for p in pdfs if os.path.isfile(p)]))
    R.add("PNG 预览", png_ok, "sizes=%s" % [os.path.getsize(p) for p in pngs if os.path.isfile(p)])
    R.add("图面组装来源", srcs.get("svg_draw", 0) >= 3, "svg_draw=%d draw2d=%d（应为 3/0）" % (srcs.get("svg_draw", 0), srcs.get("draw2d", 0)))

    # 8 DXF
    dxf = os.path.join(work, "design.dxf")
    rc6, out6, err6 = run([os.path.join(core, "dxf_export.py"), demo, "--out", dxf])
    ok_dxf = rc6 == 0 and os.path.isfile(dxf)
    d = b""
    if ok_dxf:
        d = open(dxf, "rb").read()
        txt = d.decode("ascii", "replace")
        ok_dxf = (all(b < 128 for b in d) and txt.rstrip().endswith("EOF") and "$ACADVER" in txt and "AC1009" in txt
                  and "LWPOLYLINE" not in txt)
        for lay in ("A-SITE", "A-WALL", "A-GLAZ", "A-COLS", "L-PLNT", "L-WATER", "A-ANNO", "A-DIMS"):
            ok_dxf = ok_dxf and (lay in txt)
    R.add("DXF 导出", ok_dxf, "%d B" % len(d) if d else "未产出")

    # 9 GLB
    glb = os.path.join(work, "model.glb")
    rc7, out7, err7 = run([os.path.join(core, "gltf_export.py"), demo, "--out", glb])
    ok_glb = rc7 == 0 and os.path.isfile(glb)
    detail = ""
    if ok_glb:
        raw = open(glb, "rb").read()
        magic, ver, length = struct.unpack("<III", raw[:12])
        ok_glb = magic == 0x46546C67 and ver == 2 and length == len(raw)
        if ok_glb:
            cl, ct = struct.unpack("<II", raw[12:20])
            j = json.loads(raw[20:20 + cl].decode("utf-8"))
            mats = j.get("materials") or []
            tris = sum((j["accessors"][p["indices"]]["count"] // 3) for m in j["meshes"] for p in m["primitives"] if "indices" in p)
            detail = "mesh %d / mat %d / tri %d" % (len(j["meshes"]), len(mats), tris)
            ok_glb = len(j["meshes"]) >= 5 and len(mats) >= 5 and bool(mats[0].get("pbrMetallicRoughness"))
    R.add("GLB 导出", ok_glb, detail or "未产出")

    # 10 draw2d 顺序不变量
    sys.path.insert(0, core)
    ok_order = True
    try:
        import draw2d, svg_draw  # noqa: F401
        sc = svg_draw.build_sheet("siteplan", load_json(demo))
        first = sc.items[0]
        bad = [i for i, it in enumerate(sc.items) if i > 0 and it.get("t") == "fill"
               and abs(max(p[0] for p in it["pts"]) - sc.w) < 1e-6 and abs(max(p[1] for p in it["pts"]) - sc.h) < 1e-6]
        ok_order = first.get("t") == "fill" and not bad
        detail = "items=%d items[0]=%s 后续整页填充=%s" % (len(sc.items), first.get("t"), bad or "无")
    except Exception as exc:  # noqa: BLE001
        ok_order = False
        detail = "%s: %s" % (type(exc).__name__, exc)
    R.add("绘制顺序不变量", ok_order, detail)

    # 11 schema 覆盖 demo 顶层键
    try:
        schema = load_json(os.path.join(core, "design.schema.json"))
        props = set((schema.get("properties") or {}).keys())
        demo = load_json(demo)
        extra = sorted(set(demo.keys()) - props)
        R.add("schema 覆盖顶层键", not extra, "schema %d 键 / demo %d 键 / 未描述 %s" % (len(props), len(demo), extra or "无"))
    except Exception as exc:  # noqa: BLE001
        R.add("schema 覆盖顶层键", False, "%s: %s" % (type(exc).__name__, exc))

    # 12 SKILL.md 里提到的脚本都真实存在
    import re
    text = open(os.path.join(skill, "SKILL.md"), encoding="utf-8").read()
    refs = sorted(set(re.findall(r"scripts/[\w./-]+\.py", text)))
    gone = [r for r in refs if not os.path.isfile(os.path.join(skill, r.replace("/", os.sep)))]
    R.add("SKILL.md 命令可落地", not gone, "引用 %d 个脚本 / 缺 %s" % (len(refs), gone or "无"))

    # 13 SKILL.md 引用的 references 都真实存在
    refs2 = sorted(set(re.findall(r"references/[\w./-]+\.md", text)))
    gone2 = [r for r in refs2 if not os.path.isfile(os.path.join(skill, r.replace("/", os.sep)))]
    R.add("SKILL.md 引用完整", not gone2, "引用 %d 份 / 缺 %s" % (len(refs2), gone2 or "无"))

    print("\n输出目录：%s" % work)
    return R.summary()


if __name__ == "__main__":
    sys.exit(main())
