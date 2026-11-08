
# 纯 Python PDF 兜底探针：SimHei 嵌入 (Type0 / CIDFontType2 / Identity-H + ToUnicode)
import struct, sys, os, zlib

FONT = r"C:\Windows\Fonts\simhei.ttf"
out = sys.argv[1]

data = open(FONT, "rb").read()
num = struct.unpack(">H", data[4:6])[0]
tables = {}
for i in range(num):
    off = 12 + i * 16
    tag = data[off:off+4].decode("latin1")
    o, l = struct.unpack(">II", data[off+8:off+16])
    tables[tag] = (o, l)

head = tables["head"][0]
unitsPerEm = struct.unpack(">H", data[head+18:head+20])[0]
xMin, yMin, xMax, yMax = struct.unpack(">hhhh", data[head+36:head+44])
hhea = tables["hhea"][0]
asc, desc, gap = struct.unpack(">hhh", data[hhea+4:hhea+10])
numH = struct.unpack(">H", data[hhea+34:hhea+36])[0]
hmtx = tables["hmtx"][0]
adv = [struct.unpack(">H", data[hmtx+i*4:hmtx+i*4+2])[0] for i in range(numH)]

def gid_for(cp):
    c = tables["cmap"][0]
    n = struct.unpack(">H", data[c+2:c+4])[0]
    sub = None
    for i in range(n):
        p, e, so = struct.unpack(">HHI", data[c+4+i*8:c+12+i*8])
        fmt = struct.unpack(">H", data[c+so:c+so+2])[0]
        if (p, e) in ((3, 1), (3, 10)) and fmt in (4, 12):
            sub = (fmt, c + so)
            if p == 3 and e == 1:
                break
    fmt, s = sub
    if fmt == 4:
        segX2 = struct.unpack(">H", data[s+6:s+8])[0]
        seg = segX2 // 2
        endO = s + 14
        startO = endO + segX2 + 2
        deltaO = startO + segX2
        rangeO = deltaO + segX2
        for i in range(seg):
            end = struct.unpack(">H", data[endO+i*2:endO+i*2+2])[0]
            if cp <= end:
                start = struct.unpack(">H", data[startO+i*2:startO+i*2+2])[0]
                if cp < start:
                    return 0
                delta = struct.unpack(">h", data[deltaO+i*2:deltaO+i*2+2])[0]
                ro = struct.unpack(">H", data[rangeO+i*2:rangeO+i*2+2])[0]
                if ro == 0:
                    return (cp + delta) % 65536
                addr = rangeO + i*2 + ro + (cp - start) * 2
                g = struct.unpack(">H", data[addr:addr+2])[0]
                return (g + delta) % 65536 if g else 0
    return 0

TITLE = "溪畔社区中心 · 方案设计汇报册"
LINES = [("指标校核表（方案设计阶段）", 13), ("用地面积 17,962 m²   建筑密度 17.4%", 11),
         ("容积率 0.68   绿地率 36.2%", 11), ("缺口：消防车道转弯半径尚未复核", 10)]
allchars = sorted({ord(ch) for ch in TITLE + "".join(t for t, _ in LINES) if ch != " "})
gids = {cp: gid_for(cp) for cp in allchars}
missing = [hex(cp) for cp, g in gids.items() if g == 0]
if missing:
    print("WARN unmapped codepoints:", missing)

def hexstr(text):
    return "".join("%04X" % gids[ord(ch)] for ch in text if ch != " ")

W, H = 720.0, 404.88
content = ["q 0.98 0.976 0.964 rg 0 0 %.2f %.2f re f Q" % (W, H)]
content.append("BT /F1 24 Tf 0.106 0.165 0.2 rg 48 %.2f Td <%s> Tj ET" % (H - 84, hexstr(TITLE)))
content.append("q 0.76 0.36 0.23 RG 2 w 48 %.2f m 420 %.2f l S Q" % (H - 104, H - 104))
y = H - 150
for text, pt in LINES:
    content.append("BT /F1 %d Tf 0.106 0.165 0.2 rg 48 %.2f Td <%s> Tj ET" % (pt, y, hexstr(text)))
    y -= pt * 2.4
content = "\n".join(content).encode("latin1")

ttf = zlib.compress(data, 9)
cmap_to_uni = "".join("<%04X> <%04X>\n" % (gids[cp], cp) for cp in allchars if gids[cp])
tounicode = ("/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
  "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
  "/CMapName /Adobe-Identity-UCS def /CMapType 2 def\n"
  "1 begincodespacerange <0000> <FFFF> endcodespacerange\n"
  "%d beginbfchar\n%sendbfchar\nendcmap CMapName currentdict /CMap defineresource pop end end" %
  (len(allchars), cmap_to_uni)).encode("latin1")

widths = " ".join("%d [%d]" % (gids[cp], round(adv[gids[cp]-1] * 1000 / unitsPerEm) if 0 < gids[cp] <= numH else 1000)
                  for cp in allchars if gids[cp])
cid = ("/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
  "/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> def\n"
  "/CMapName /Identity-H def /CMapType 0 def\n"
  "1 begincodespacerange <0000> <FFFF> endcodespacerange\nendcmap CMapName currentdict /CMap defineresource pop end end").encode("latin1")

objs = {}
objs[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
objs[2] = b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"
objs[3] = ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] /Resources << /Font << /F1 4 0 R >> >> /Contents 8 0 R >>" % (W, H)).encode("latin1")
objs[4] = b"<< /Type /Font /Subtype /Type0 /BaseFont /SimHei /Encoding 9 0 R /DescendantFonts [5 0 R] /ToUnicode 10 0 R >>"
objs[5] = ("<< /Type /Font /Subtype /CIDFontType2 /BaseFont /SimHei /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /FontDescriptor 6 0 R /CIDToGIDMap /Identity /DW 1000 /W [%s] >>" % widths).encode("latin1")
objs[6] = ("<< /Type /FontDescriptor /FontName /SimHei /Flags 4 /FontBBox [%d %d %d %d] /ItalicAngle 0 /Ascent %d /Descent %d /CapHeight %d /StemV 80 /FontFile2 7 0 R >>" %
           (xMin, yMin, xMax, yMax, asc, desc, asc)).encode("latin1")
objs[7] = b"<< /Length %d /Length1 %d /Filter /FlateDecode >>\nstream\n" % (len(ttf), len(data)) + ttf + b"\nendstream"
objs[8] = b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream"
objs[9] = b"<< /Length %d >>\nstream\n" % len(cid) + cid + b"\nendstream"
objs[10] = b"<< /Length %d >>\nstream\n" % len(tounicode) + tounicode + b"\nendstream"

buf = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
offsets = {}
for n in sorted(objs):
    offsets[n] = len(buf)
    buf += b"%d 0 obj\n" % n + objs[n] + b"\nendobj\n"
xref_at = len(buf)
buf += b"xref\n0 %d\n" % (len(objs) + 1)
buf += b"0000000000 65535 f \n"
for n in sorted(objs):
    buf += b"%010d 00000 n \n" % offsets[n]
buf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref_at)

open(out, "wb").write(buf)
print("wrote", out, len(buf), "bytes | unitsPerEm", unitsPerEm, "| mapped", len(allchars), "chars | missing", len(missing))
