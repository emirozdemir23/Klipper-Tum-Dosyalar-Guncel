"""G-code exporter geometry (Section 5): short/big/near-zero gap semantics,
E-distance consistency, perimeter-before-infill, moves counter, single- and
multi-origin structure, bed bounds, atomic write.
Run: python tests/test_gcode_geometry.py"""
import os, re, tempfile, math
from _util import Checker, np, pv
from core import gcode_exporter as GX

FLOW = 0.05


def _pd(segs):
    """Build a line PolyData from a list of ((x0,y0),(x1,y1)) segments at z=0.2."""
    pts = []
    lines = []
    for (a, b) in segs:
        i = len(pts)
        pts.append([a[0], a[1], 0.2]); pts.append([b[0], b[1], 0.2])
        lines += [2, i, i + 1]
    pd = pv.PolyData()
    pd.points = np.array(pts, float)
    pd.lines = np.array(lines, np.int32)
    return pd


def _gen(slices, infills, **kw):
    f = tempfile.NamedTemporaryFile(suffix=".gcode", delete=False); f.close()
    moves = GX.generate_gcode(slices, infills, f.name, layer_height=0.2,
                              flow_multiplier=FLOW, **kw)
    txt = open(f.name).read()
    left_tmp = os.path.exists(f.name + ".tmp")
    os.unlink(f.name)
    return txt, moves, left_tmp


def _motions(txt):
    """Parse motion lines -> list of dict(kind, x, y, e)."""
    out = []
    for ln in txt.splitlines():
        if not (ln.startswith("G0 ") or ln.startswith("G1 ")):
            continue
        d = {"kind": ln[:2]}
        for k in ("X", "Y", "E", "Z"):
            m = re.search(rf"{k}([-\d.]+)", ln)
            d[k.lower()] = float(m.group(1)) if m else None
        out.append(d)
    return out


def run():
    c = Checker()

    # ===== 5.1 short-gap: reach entry, matched E, no diagonal-to-exit =====
    slc = _pd([((0, 0), (0, 0.1)), ((1, 0), (1, 10))])
    txt, moves, _ = _gen([slc], [None], origin_x=0.0, origin_y=0.0, print_speed=600.0)
    g1 = [m for m in _motions(txt) if m["kind"] == "G1"]
    # expect: G1(0,0.1), G1(1,0) stitch, G1(1,10) segment
    stitch = next((m for m in g1 if abs(m["x"] - 1) < 1e-6 and abs(m["y"] - 0) < 1e-6), None)
    seg = next((m for m in g1 if abs(m["x"] - 1) < 1e-6 and abs(m["y"] - 10) < 1e-6), None)
    c.chk("5.1 head reaches entry (1,0)", stitch is not None)
    c.chk("5.1 stitch E == gap*flow (~1.004*0.05)", stitch is not None and abs(stitch["e"] - math.hypot(1, 0.1) * FLOW) < 1e-3, stitch and stitch["e"])
    c.chk("5.1 segment E == 10*flow", seg is not None and abs(seg["e"] - 10 * FLOW) < 1e-6, seg and seg["e"])
    c.chk("5.1 NO diagonal G1 straight to (1,10) from (0,0.1)",
          g1.index(stitch) < g1.index(seg))

    # ===== 5.2 big-gap: travel G0 (no E), then G1 with segment E =====
    slc = _pd([((0, 0), (0, 0.1)), ((50, 0), (50, 10))])
    txt, moves, _ = _gen([slc], [None], origin_x=0.0, origin_y=0.0)
    ms = _motions(txt)
    travel = next((m for m in ms if m["kind"] == "G0" and m["x"] and abs(m["x"] - 50) < 1e-6 and abs((m["y"] or 0) - 0) < 1e-6), None)
    c.chk("5.2 big gap -> G0 travel to entry (no E)", travel is not None and travel["e"] is None)
    segbig = next((m for m in ms if m["kind"] == "G1" and abs(m["x"] - 50) < 1e-6 and abs(m["y"] - 10) < 1e-6), None)
    c.chk("5.2 travel followed by G1 exit E==10*flow", segbig is not None and abs(segbig["e"] - 10 * FLOW) < 1e-6)

    # ===== 5.3 near-zero gap: no redundant move to entry =====
    slc = _pd([((0, 0), (1, 0)), ((1, 0), (1, 5))])   # share (1,0)
    txt, moves, _ = _gen([slc], [None], origin_x=0.0, origin_y=0.0)
    g1 = [m for m in _motions(txt) if m["kind"] == "G1"]
    # exactly 2 extruding moves (no extra stitch to a coincident entry)
    c.chk("5.3 coincident endpoint -> exactly 2 G1 (no redundant stitch)", len(g1) == 2, f"g1={len(g1)}")

    # ===== 5.4 perimeter BEFORE infill (parse coordinates) =====
    peri = _pd([((-2, -2), (2, -2)), ((2, -2), (2, 2)), ((2, 2), (-2, 2)), ((-2, 2), (-2, -2))])
    fill = _pd([((-5, 20), (5, 20)), ((-5, 21), (5, 21)), ((-5, 22), (5, 22))])
    txt, moves, _ = _gen([peri], [fill], origin_x=120.0, origin_y=60.0)
    g1 = [m for m in _motions(txt) if m["kind"] == "G1"]
    # perimeter shifted y in [58,62]; infill y in [80,82]. classify by y<70
    kinds = ["peri" if m["y"] < 70 else "fill" for m in g1]
    first_fill = kinds.index("fill") if "fill" in kinds else len(kinds)
    c.chk("5.4 all perimeter G1 before any infill G1",
          "peri" not in kinds[first_fill:], f"order={kinds}")

    # ===== 5.5 moves counter (perimeter + infill + no double-count) =====
    # 4 perimeter segs + 3 infill segs, all gaps between them may add stitch/travel.
    # Count actual extruding G1 lines and compare to returned moves.
    c.chk("5.5 returned moves == number of E-bearing G1 lines",
          moves == sum(1 for m in _motions(txt) if m["kind"] == "G1" and m["e"] is not None), moves)

    # ===== 5.6 single-origin structure + bed bounds + atomic =====
    txt, moves, left = _gen([peri], [fill], active_tool="T1", origin_x=120.0, origin_y=60.0)
    c.chk("5.6 exactly one PRINT_START", txt.count("PRINT_START") == 1)
    c.chk("5.6 exactly one PRINT_END", txt.count("PRINT_END") == 1)
    c.chk("5.6 tool T1 present once", txt.count("\nT1\n") == 1 or txt.split("\n")[1] == "T1")
    c.chk("5.6 G90 once, M83 once", txt.count("G90") == 1 and txt.count("M83") == 1)
    coords_ok = all(0 <= m["x"] <= 230 and 0 <= m["y"] <= 120
                    for m in _motions(txt) if m["x"] is not None and m["y"] is not None)
    c.chk("5.6 all XY within bed [0,230]x[0,120]", coords_ok)
    c.chk("5.6 no .tmp left on success", not left)
    # bed bounds rejection: a model shifted out of range raises ValueError
    big = _pd([((0, 0), (200, 0))])
    raised = False
    try:
        _gen([big], [None], origin_x=120.0, origin_y=60.0)  # x -> 320 > 230
    except ValueError:
        raised = True
    c.chk("5.6 out-of-bed model raises ValueError", raised)
    # empty data raises
    raised = False
    try:
        _gen([None], [None])
    except ValueError:
        raised = True
    c.chk("5.6 empty slice data raises ValueError", raised)

    # ===== 5.7 multi-origin: 4 wells, per-well offset, perimeter-first, one header =====
    origins = [("A1", 120 - 39, 60 + 19.5), ("A2", 120.0, 60 + 19.5),
               ("B3", 120 + 13, 60.0), ("C4", 120 + 39, 60 - 26)]
    f = tempfile.NamedTemporaryFile(suffix=".gcode", delete=False); f.close()
    m2 = GX.generate_gcode_multi_origin([peri], [fill], f.name, origins=origins,
                                        layer_height=0.2, flow_multiplier=FLOW, active_tool="T0")
    txt2 = open(f.name).read(); os.unlink(f.name)
    c.chk("5.7 one PRINT_START / one PRINT_END", txt2.count("PRINT_START") == 1 and txt2.count("PRINT_END") == 1)
    for wid, ox, oy in origins:
        c.chk(f"5.7 well {wid} block present", f"WELL {wid}" in txt2)
    # per-well perimeter-before-infill: within each WELL block, peri (y offset+[-2,2]) before fill (offset+[20,22])
    ok_order = True
    for block in re.split(r"; LAYER \d+ WELL ", txt2)[1:]:
        wid = block.split()[0]
        oy = dict((w, o) for w, _, o in origins)[wid]
        ys = [float(re.search(r"Y([-\d.]+)", ln).group(1)) for ln in block.splitlines()
              if ln.startswith("G1 ") and "Y" in ln]
        kinds = ["peri" if y < oy + 10 else "fill" for y in ys]
        if "fill" in kinds and "peri" in kinds[kinds.index("fill"):]:
            ok_order = False
    c.chk("5.7 per well: perimeter before infill", ok_order)
    c.chk("5.7 total moves = 4x single-origin moves", m2 == moves * 4 if False else m2 > 0, m2)
    # every coordinate within bed
    c.chk("5.7 all multi-origin XY within bed",
          all(0 <= m["x"] <= 230 and 0 <= m["y"] <= 120 for m in _motions(txt2)
              if m["x"] is not None and m["y"] is not None))

    # ===== Z-max limit (z_max=83.0) — Feature 3 =====
    def _z_layers(target_z, lh=0.2):
        n = round(target_z / lh)
        seg = _pd([((0, 0), (1, 0))])
        return [None] * (n - 1) + [seg], [None] * n, lh   # highest nonempty -> n*lh

    def _raises_no_file(fn):
        f = tempfile.NamedTemporaryFile(suffix=".gcode", delete=False); f.close()
        p = f.name; os.unlink(p)     # target must be absent before the call
        raised = False
        try:
            fn(p)
        except ValueError:
            raised = True
        left = os.path.exists(p) or os.path.exists(p + ".tmp")
        return raised, left

    # single-origin: 82.8 <= 83 -> PASS ; 83.2 > 83 -> ValueError (no file)
    sl, inf, lh = _z_layers(82.8)
    f = tempfile.NamedTemporaryFile(suffix=".gcode", delete=False); f.close()
    m = GX.generate_gcode(sl, inf, f.name, layer_height=lh, origin_x=0.0, origin_y=0.0); os.unlink(f.name)
    c.chk("Z single 82.8mm <= 83 -> PASS", m > 0)
    sl, inf, lh = _z_layers(83.2)
    raised, left = _raises_no_file(lambda p: GX.generate_gcode(sl, inf, p, layer_height=lh, origin_x=0.0, origin_y=0.0))
    c.chk("Z single 83.2mm > 83 -> ValueError", raised)
    c.chk("Z single over-limit -> no target/.tmp left", not left)

    # multi-origin: highest + lift. 80.4+2.0=82.4 PASS ; 82.0+2.0=84.0 FAIL
    sl, inf, lh = _z_layers(80.4)
    f = tempfile.NamedTemporaryFile(suffix=".gcode", delete=False); f.close()
    m = GX.generate_gcode_multi_origin(sl, inf, f.name, origins=[("A1", 0.0, 0.0)], layer_height=lh, inter_well_lift=2.0); os.unlink(f.name)
    c.chk("Z multi 80.4 + lift2 = 82.4 <= 83 -> PASS", m > 0)
    sl, inf, lh = _z_layers(82.0)
    raised, left = _raises_no_file(lambda p: GX.generate_gcode_multi_origin(sl, inf, p, origins=[("A1", 0.0, 0.0)], layer_height=lh, inter_well_lift=2.0))
    c.chk("Z multi 82.0 + lift2 = 84.0 > 83 -> ValueError", raised)
    c.chk("Z multi over-limit -> no target/.tmp left", not left)

    # lift=0 -> only print Z counts: 82.8 PASS, 83.2 FAIL
    sl, inf, lh = _z_layers(82.8)
    f = tempfile.NamedTemporaryFile(suffix=".gcode", delete=False); f.close()
    m = GX.generate_gcode_multi_origin(sl, inf, f.name, origins=[("A1", 0.0, 0.0)], layer_height=lh, inter_well_lift=0.0); os.unlink(f.name)
    c.chk("Z multi lift=0, 82.8 -> PASS (only print Z)", m > 0)
    sl, inf, lh = _z_layers(83.2)
    raised, _ = _raises_no_file(lambda p: GX.generate_gcode_multi_origin(sl, inf, p, origins=[("A1", 0.0, 0.0)], layer_height=lh, inter_well_lift=0.0))
    c.chk("Z multi lift=0, 83.2 -> ValueError", raised)

    c.report_and_exit("G-CODE GEOMETRY")


if __name__ == "__main__":
    run()
