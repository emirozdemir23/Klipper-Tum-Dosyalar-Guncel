"""Slicer geometry + Linear infill (Section 3): orientation alternation, grid
distance accuracy, hole/island clipping, box/cylinder correctness, NaN/zero-len.
Run: python tests/test_slicer_geometry.py"""
from _util import (slice_mesh, seg_dirs, seg_midpoints, has_nonfinite, Checker,
                   np, pv)


def _line_spacing(pd, orientation):
    """Measured spacing between adjacent parallel infill lines (mm)."""
    if pd is None or pd.n_points == 0:
        return None
    lines = np.asarray(pd.lines).reshape(-1, 3)[:, 1:]
    p = np.asarray(pd.points)
    # horizontal (orient 0) lines share a y; vertical (orient 1) share an x
    coord = (p[lines[:, 0], 1] if orientation == 0 else p[lines[:, 0], 0])
    u = np.unique(np.round(coord, 4))
    if u.size < 2:
        return None
    return float(np.min(np.diff(np.sort(u))))


def run():
    c = Checker()

    # ---- BOX 20x20x10 : linear orientation alternation ----
    box = pv.Box(bounds=(-10, 10, -10, 10, 0, 10)).triangulate()
    cap = slice_mesh(box, 0.2, 1.0)
    c.chk("box slices without error", "error" not in cap, cap.get("error", ""))
    inf = cap["infills"]
    h0, v0, *_ = seg_dirs(inf[0]); h1, v1, *_ = seg_dirs(inf[1]); h2, v2, *_ = seg_dirs(inf[2])
    c.chk("layer0: horizontal>0 AND vertical==0", h0 > 0 and v0 == 0, f"h={h0} v={v0}")
    c.chk("layer1: horizontal==0 AND vertical>0", h1 == 0 and v1 > 0, f"h={h1} v={v1}")
    c.chk("layer2: horizontal>0 AND vertical==0 (alternates back)", h2 > 0 and v2 == 0, f"h={h2} v={v2}")
    c.chk("no diagonal infill segments", all(seg_dirs(inf[i])[2] == 0 for i in range(len(inf))))
    c.chk("no zero-length infill segments", all(seg_dirs(inf[i])[3] == 0 for i in range(len(inf))))
    c.chk("no NaN/Inf in any layer", not any(has_nonfinite(s) for s in cap["slices"]) and
          not any(has_nonfinite(s) for s in inf))

    # ---- Grid Distance accuracy: 0.2 / 1.0 / 2.0 mm ----
    for dist in (0.2, 1.0, 2.0):
        cap = slice_mesh(box, 0.2, dist)
        sp = _line_spacing(cap["infills"][0], 0)   # layer 0 horizontal
        c.chk(f"grid distance {dist}mm -> measured line spacing ~= {dist}",
              sp is not None and abs(sp - dist) < 1e-3, f"measured={sp}")

    # ---- CYLINDER d20 h10 : circular contour, single-direction infill inside ----
    cyl = pv.Cylinder(center=(0, 0, 5), direction=(0, 0, 1), radius=10, height=10,
                      resolution=72).triangulate()
    cap = slice_mesh(cyl, 0.2, 0.8)
    c.chk("cylinder slices without error", "error" not in cap, cap.get("error", ""))
    mid = len(cap["slices"]) // 2
    p = np.asarray(cap["slices"][mid].points)
    rr = np.sqrt(p[:, 0] ** 2 + p[:, 1] ** 2)
    c.chk("cylinder mid contour ~ circular r~10", abs(rr.max() - 10) < 0.6 and abs(rr.mean() - 10) < 0.6,
          f"rmax={rr.max():.2f} rmean={rr.mean():.2f}")
    infm = cap["infills"][mid]
    hh, vv, dd, zz = seg_dirs(infm)
    c.chk("cylinder mid infill is single-direction", (hh == 0) != (vv == 0), f"h={hh} v={vv}")
    im = seg_midpoints(infm)
    c.chk("cylinder infill midpoints inside radius (<=10.1)", im.shape[0] > 0 and
          np.all(np.sqrt(im[:, 0] ** 2 + im[:, 1] ** 2) <= 10.1))

    # ---- HOLLOW TUBE : infill must NOT enter the central hole ----
    outer = pv.Box(bounds=(-10, 10, -10, 10, 0, 10)).triangulate()
    inner = pv.Box(bounds=(-4, 4, -4, 4, -1, 11)).triangulate()
    tube = outer.boolean_difference(inner)
    cap = slice_mesh(tube, 0.2, 0.6)
    if "error" in cap or not cap.get("slices"):
        c.chk("tube slices without error", False, cap.get("error", "no slices"))
    else:
        midt = len(cap["slices"]) // 2
        im = seg_midpoints(cap["infills"][midt])
        in_hole = int(np.sum((np.abs(im[:, 0]) < 3.5) & (np.abs(im[:, 1]) < 3.5))) if im.shape[0] else 0
        c.chk("tube: NO infill midpoints inside central hole", in_hole == 0, f"in_hole={in_hole}")
        c.chk("tube: infill present in the ring", im.shape[0] > 0, f"segs={im.shape[0]}")

    c.report_and_exit("SLICER GEOMETRY + LINEAR INFILL")


if __name__ == "__main__":
    run()
