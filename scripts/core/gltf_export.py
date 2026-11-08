#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gltf_export.py -- design.json -> glTF 2.0 单文件 .glb。

产物
    一个 .glb（不是 .gltf + .bin）：12 字节头 (magic 'glTF' / version 2 / 总长度)
    + JSON chunk（4 字节对齐，空格补白）+ BIN chunk（4 字节对齐，0 补白）。
    几何全在 BIN 里，JSON 只放结构，所以单文件自洽、拖进任何查看器都能开。

坐标系（这一步最容易错，写清楚）
    design.json 的坐标是 (x 东, y 北, z 标高)，单位见 project.units（本 schema 恒为 m）。
    glTF 是右手坐标系、约定 +Y 朝上。于是取

        X_gltf =  x_design * k        (东 -> +X)
        Y_gltf =  z_design * k        (标高 -> +Y，即"上")
        Z_gltf = -y_design * k        (北 -> -Z，右手系里 +Z 指向南)

    这就是"y 轴翻转"：设计平面的北向落到 -Z，俯视时 +Z 朝下、+X 朝右、+Y 朝观察者，
    行列式保持 +1，法线不会整体反号；Z 轴在 glTF 里就是"上"，与设计的高程语义一致。
    k 由 project.units 决定（m -> 1.0，mm -> 0.001）。本 schema 校验 project.units
    必须为 "m"，所以 150m 的校园不会被打成 0.15m；/1000 只对确实以毫米书写的
    输入生效，缩放逻辑集中在 UNITS_TO_M。

层级
    scene -> "PROJECT …" -> 每栋建筑 -> 每层楼（mesh + 平移 Y=标高）。同一个 mass
    的所有楼层复用同一份长方体网格，楼板厚度不同的顶层再单独一份。

CLI
    python gltf_export.py <design.json> --out <path.glb>
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import design_io

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SKILL_DIR = os.path.dirname(os.path.dirname(HERE))       # skill/
DEFAULT_PALETTE = os.path.join(SKILL_DIR, "assets", "palette.json")
GROUND_MAT = "__site_ground__"
NEUTRAL = (0.60, 0.60, 0.60)
UNITS_TO_M = {"m": 1.0, "mm": 0.001, "cm": 0.01}
# 材质名 -> palette.json 语义角色。
# palette.json 是线稿色板，只有 building / plant / water / paving 四个语义色；
# brick、glass、metal_roof 这类设计稿材质在色板里没有对应语义，硬归到 building 会把
# 四栋楼压成同一个灰，所以只在名字真的对得上语义色时才复用 palette，其余用
# design.json 自己的 material.color。
ROLE = {
    "grass": "plant", "lawn": "plant", "green": "plant", "plant": "plant",
    "tree": "plant", "hedge": "plant",
    "water": "water", "pond": "water", "pool": "water", "river": "water",
    "stone": "paving", "paving": "paving", "plaza": "paving", "asphalt": "paving",
    "concrete": "building",
}


# ==========================================================================
# 颜色
# ==========================================================================
def srgb_to_linear(c):
    c = min(1.0, max(0.0, float(c)))
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_rgb(s):
    """'#RRGGBB' -> (r, g, b)，失败返回 None。"""
    if not isinstance(s, str):
        return None
    h = s.strip().lstrip("#")
    if len(h) != 6:
        return None
    try:
        return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0)
    except ValueError:
        return None


def linear_factor(rgb):
    return [round(srgb_to_linear(v), 6) for v in rgb] + [1.0]


def load_palette(path=None):
    p = path or DEFAULT_PALETTE
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}, None
    colors = (data.get("colors") or data) if isinstance(data, dict) else {}
    out = {}
    for name, v in colors.items():
        rgb = hex_rgb(v.get("hex") if isinstance(v, dict) else v)
        if rgb:
            out[name] = rgb
    if not out:
        return {}, None
    ap = os.path.abspath(p)
    try:
        ref = os.path.relpath(ap, SKILL_DIR).replace(os.sep, "/")
    except ValueError:
        ref = os.path.basename(ap)
    return out, (os.path.basename(ap) if ref.startswith("..") else ref)


def unit_scale(d):
    u = str(((d.get("project") or {}).get("units")) or "m").strip().lower()
    return UNITS_TO_M.get(u, 1.0), u


# ==========================================================================
# BIN / accessor 组装
# ==========================================================================
class Bin:
    """一个 buffer + 若干 bufferView/accessor；写入前做 4 字节对齐。"""

    def __init__(self):
        self.data = bytearray()
        self.views = []
        self.accessors = []

    def _view(self, payload, target):
        while len(self.data) % 4:
            self.data.append(0)
        off = len(self.data)
        self.data += payload
        self.views.append({"buffer": 0, "byteOffset": off, "byteLength": len(payload),
                           "target": target})
        return len(self.views) - 1

    def vec3(self, vals):
        n = len(vals) // 3
        if n <= 0:
            raise ValueError("empty vec3 array")
        payload = struct.pack("<%df" % len(vals), *vals)
        view = self._view(payload, 34962)                   # ARRAY_BUFFER
        mins = [round(min(vals[i::3]), 6) for i in range(3)]
        maxs = [round(max(vals[i::3]), 6) for i in range(3)]
        self.accessors.append({"bufferView": view, "componentType": 5126, "count": n,
                               "type": "VEC3", "min": mins, "max": maxs})
        return len(self.accessors) - 1

    def indices(self, vals):
        if not vals:
            raise ValueError("empty index array")
        payload = struct.pack("<%dH" % len(vals), *vals)
        view = self._view(payload, 34963)                   # ELEMENT_ARRAY_BUFFER
        self.accessors.append({"bufferView": view, "componentType": 5123,
                               "count": len(vals), "type": "SCALAR"})
        return len(self.accessors) - 1


def box_arrays(x0, x1, y0, y1, z0, z1):
    """轴对齐长方体：每面 4 个独立顶点 -> 平面法线；12 个三角形。

    用 24 个顶点而不是 8 个：8 个共享顶点只能带平均法线，方盒子会被渲染成圆角疙瘩；
    24 顶点里每个面法线独立，棱角才是硬的。几何上仍然是"一个长方体"。
    """
    faces = (
        ((0.0, 1.0, 0.0), ((x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0))),
        ((0.0, -1.0, 0.0), ((x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1))),
        ((1.0, 0.0, 0.0), ((x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1))),
        ((-1.0, 0.0, 0.0), ((x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0))),
        ((0.0, 0.0, 1.0), ((x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1))),
        ((0.0, 0.0, -1.0), ((x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0))),
    )
    pos, nrm, idx = [], [], []
    for n, quad in faces:
        base = len(pos) // 3
        for p in quad:
            pos.extend(p)
            nrm.extend(n)
        idx.extend((base, base + 1, base + 2, base, base + 2, base + 3))
    return pos, nrm, idx


def plane_arrays(x0, x1, y, z0, z1):
    pos = [x0, y, z0, x0, y, z1, x1, y, z1, x1, y, z0]
    nrm = [0.0, 1.0, 0.0] * 4
    idx = [0, 1, 2, 0, 2, 3]
    return pos, nrm, idx


# ==========================================================================
# 材质
# ==========================================================================
def _clamp01(v, dflt):
    try:
        return min(1.0, max(0.0, float(v)))
    except (TypeError, ValueError):
        return dflt


def material_list(d, pal):
    mats, index, roles = [], {}, {}

    def add(name, rgb, rough, metal, src):
        index[name] = len(mats)
        mats.append({
            "name": name,
            "pbrMetallicRoughness": {
                "baseColorFactor": linear_factor(rgb),
                "metallicFactor": round(metal, 4),
                "roughnessFactor": round(rough, 4),
            },
            "doubleSided": False,
            "extras": {"colorHex": "#%02X%02X%02X" % tuple(int(round(c * 255)) for c in rgb),
                       "colorSource": src},
        })

    for name, m in (d.get("materials") or {}).items():
        m = m or {}
        role = ROLE.get(str(name).strip().lower())
        rgb, src = None, "neutral"
        if role and role in pal:
            rgb, src = pal[role], "palette:" + role
        if rgb is None:
            rgb = hex_rgb(m.get("color"))
            src = "design.json" if rgb else "neutral"
        if rgb is None:
            rgb = NEUTRAL
        add(name, rgb, _clamp01(m.get("roughness"), 0.8), _clamp01(m.get("metalness"), 0.0),
            src)
        if role and role in pal:
            roles[name] = role

    grgb, gsrc = (pal.get("paving"), "palette:paving") if "paving" in pal \
        else (NEUTRAL, "neutral")
    add(GROUND_MAT, grgb, 0.95, 0.0, gsrc)
    return mats, index, roles


# ==========================================================================
# 构建
# ==========================================================================
def build(d, palette_path=None):
    pal, pal_file = load_palette(palette_path)
    k, unit_name = unit_scale(d)
    lh = max(0.0, float(design_io.level_height(d) or 0.0))
    mats, mat_index, roles = material_list(d, pal)
    B = Bin()
    meshes, nodes = [], []
    tri_total = 0
    vertex_total = 0

    def add_mesh(name, arrays, material):
        nonlocal tri_total, vertex_total
        pos, nrm, idx = arrays
        a_pos = B.vec3(pos)
        a_nrm = B.vec3(nrm)
        a_idx = B.indices(idx)
        tri_total += len(idx) // 3
        vertex_total += len(pos) // 3
        meshes.append({
            "name": name,
            "primitives": [{"attributes": {"POSITION": a_pos, "NORMAL": a_nrm},
                            "indices": a_idx,
                            "material": mat_index.get(material, mat_index[GROUND_MAT]),
                            "mode": 4}],
        })
        return len(meshes) - 1

    def box_for(fp, z0, z1):
        bx0, by0, bx1, by1 = design_io.bbox(fp)
        return box_arrays(bx0 * k, bx1 * k, z0 * k, z1 * k, -by1 * k, -by0 * k)

    building_nodes = []
    for m in d["masses"]:
        fp = design_io.as_pts(m["footprint"])
        base = float(m.get("base", 0.0) or 0.0)
        height = max(0.0, float(m.get("height", 0.0) or 0.0))
        if height <= 1e-9:
            continue
        mat = m.get("material") or GROUND_MAT
        storeys = max(1, int(design_io.mass_storeys(m, lh) or 1)) if lh > 1e-9 else 1
        slab = lh if lh > 1e-9 else height
        floors = []
        for i in range(storeys):
            z0 = base + i * slab
            z1 = min(base + (i + 1) * slab, base + height)
            if z1 - z0 <= 1e-9:
                continue
            floors.append((i, z0, z1))

        groups = {}
        for i, z0, z1 in floors:
            groups.setdefault(round(z1 - z0, 6), []).append((i, z0, z1))
        children = []                       # 必须是 nodes 数组的下标，不能内联节点
        for thick in sorted(groups, reverse=True):
            mesh = add_mesh("%s slab %.3fm" % (m["id"], thick),
                            box_for(fp, 0.0, thick), mat)
            for i, z0, _z1 in groups[thick]:
                nodes.append({"name": "%s L%d" % (m["id"], i + 1), "mesh": mesh,
                              "translation": [0.0, round(z0 * k, 6), 0.0]})
                children.append(len(nodes) - 1)
        nodes.append({
            "name": "%s %s" % (m["id"], m.get("name") or ""),
            "children": children,
            "extras": {"base": base, "height": height,
                       "roofRise": float(m.get("roof_rise", 0) or 0),
                       "roof": m.get("roof", "flat"), "material": mat,
                       "storeys": storeys, "footprintM": [[round(p[0], 3), round(p[1], 3)]
                                                          for p in fp]},
        })
        building_nodes.append(len(nodes) - 1)

    # 地面
    bd = design_io.as_pts(d["site"]["boundary"])
    gx0, gy0, gx1, gy1 = design_io.bbox(bd)
    gz = min([0.0] + [float(lv.get("elevation", 0.0) or 0.0)
                      for lv in (d.get("site", {}).get("levels") or [])])
    gpos, gnrm, gidx = plane_arrays(gx0 * k, gx1 * k, round(gz * k, 6), -gy1 * k, -gy0 * k)
    ground_mesh = add_mesh("site ground", (gpos, gnrm, gidx), GROUND_MAT)
    nodes.append({"name": "GROUND", "mesh": ground_mesh,
                  "extras": {"elevation": gz, "boundaryM": [[round(p[0], 3), round(p[1], 3)]
                                                             for p in bd]}})

    name = str(((d.get("project") or {}).get("name")) or "PROJECT").strip()
    root = {"name": "PROJECT " + name,
            "children": [i for i in building_nodes] + [len(nodes) - 1]}
    nodes.append(root)

    gltf = {
        "asset": {
            "version": "2.0",
            "generator": "arch-env-design-build/gltf_export.py",
            "extras": {"source": "design.json", "designUnits": unit_name,
                       "unitScaleToMetre": k, "up": "+Y", "axisMap":
                       "X=+x(east) Y=+z(elevation) Z=-y(north)",
                       "palette": pal_file, "paletteRoles": roles},
        },
        "scene": 0,
        "scenes": [{"name": "PROJECT " + name, "nodes": [len(nodes) - 1]}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": mats,
        "accessors": B.accessors,
        "bufferViews": B.views,
        "buffers": [{"byteLength": 0}],
    }
    return gltf, B, {
        "meshes": len(meshes), "nodes": len(nodes), "materials": len(mats),
        "triangles": tri_total, "vertices": vertex_total,
        "buildings": len(building_nodes), "unit": unit_name, "k": k,
        "palette": pal_file, "roles": roles,
    }


def write_glb(gltf, bin_bytes, path):
    body = bytes(bin_bytes)
    gltf["buffers"][0]["byteLength"] = len(body)

    js = json.dumps(gltf, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    js += b" " * ((4 - len(js) % 4) % 4)                 # JSON chunk 用空格补齐
    bn = body + b"\x00" * ((4 - len(body) % 4) % 4)     # BIN chunk 用 0 补齐
    total = 12 + 8 + len(js) + 8 + len(bn)

    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with open(path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, total))   # 'glTF', version, length
        f.write(struct.pack("<II", len(js), 0x4E4F534A))     # 'JSON'
        f.write(js)
        f.write(struct.pack("<II", len(bn), 0x004E4942))     # 'BIN\0'
        f.write(bn)
    return {"path": os.path.abspath(path), "bytes": total, "jsonBytes": len(js),
            "binBytes": len(bn)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="design.json -> glTF 2.0 单文件 .glb")
    ap.add_argument("design")
    ap.add_argument("--out", required=True, help="输出 .glb 路径")
    ap.add_argument("--palette", default=None, help="palette.json 路径（默认 skill/assets）")
    a = ap.parse_args(argv)

    d = design_io.load_design(a.design)
    gltf, B, st = build(d, a.palette)
    out = write_glb(gltf, B.data, a.out)

    print("GLB: %s" % out["path"])
    print("  bytes %d = header 12 + json %d + bin %d"
          % (out["bytes"], out["jsonBytes"], out["binBytes"]))
    print("  units %r -> x%g to metre; axis X=east Y=up(elevation) Z=-north"
          % (st["unit"], st["k"]))
    print("  meshes %d / nodes %d / materials %d / triangles %d / vertices %d"
          % (st["meshes"], st["nodes"], st["materials"], st["triangles"], st["vertices"]))
    print("  buildings %d; palette %s; palette roles %s"
          % (st["buildings"], st["palette"] or "NOT FOUND (neutral)",
             st["roles"] or "-"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
