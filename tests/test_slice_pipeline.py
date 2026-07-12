"""Slice pipeline safety (Section 4/G): empty-slice rejection, infill-exception
FAILS the slice (error, not finished), list-length consistency.
Run: python tests/test_slice_pipeline.py"""
from _util import slice_mesh, Checker, np, pv
import core.slicer_worker as SW


def run():
    c = Checker()

    # ---- infill exception -> slice FAILS (error emitted, NOT finished) ----
    box = pv.Box(bounds=(-10, 10, -10, 10, 0, 10)).triangulate()
    orig = SW.build_infill_grid_2d
    def boom(*a, **k):
        raise ValueError("injected infill failure")
    SW.build_infill_grid_2d = boom
    try:
        cap = slice_mesh(box, 0.2, 0.4)   # distance>0 -> infill attempted -> boom
    finally:
        SW.build_infill_grid_2d = orig
    c.chk("infill exception -> 'error' emitted", "error" in cap, cap.get("error", "")[:60])
    c.chk("infill exception -> NO finished (no 'slices')", "slices" not in cap)
    c.chk("error message names a layer", "error" in cap and "Layer" in cap["error"], cap.get("error", "")[:80])
    c.chk("error message mentions infill", "error" in cap and "infill" in cap["error"].lower())

    # ---- empty slice (flat plane, no cross-section) -> rejected ----
    plane = pv.Plane(i_size=20, j_size=20)   # zero-thickness sheet at z=0
    cap = slice_mesh(plane, 0.2, 0.4)
    c.chk("empty slice -> 'error' emitted", "error" in cap, cap.get("error", "")[:60])
    c.chk("empty slice -> NO finished (no 'slices')", "slices" not in cap)
    c.chk("empty-slice error says 'bos'/empty", "error" in cap and "bos" in cap["error"].lower())

    # ---- healthy slice -> finished with consistent list lengths ----
    cap = slice_mesh(box, 0.2, 0.4)
    ok = "slices" in cap
    c.chk("healthy slice -> finished (has 'slices')", ok, cap.get("error", ""))
    if ok:
        c.chk("len(slices)==len(layer_meshes)==len(infills)",
              len(cap["slices"]) == len(cap["layer_meshes"]) == len(cap["infills"]),
              f"{len(cap['slices'])}/{len(cap['layer_meshes'])}/{len(cap['infills'])}")
        c.chk("at least one valid contour",
              any(s is not None and getattr(s, 'n_points', 0) > 0 for s in cap["slices"]))

    # ---- master_grid=None (Feature 2): Linear infill requested but grid unbuildable ----
    # 1) normal grid -> success AND infill actually present (not a silent infill-less slice)
    cap = slice_mesh(box, 0.2, 0.4)
    c.chk("normal grid -> finished OK", "slices" in cap)
    c.chk("normal grid -> infill present (>=1 non-empty infill layer)",
          "infills" in cap and any(f is not None and getattr(f, "n_points", 0) > 0
                                    for f in cap["infills"]))

    # 2) tiny distance trips the segment safety ceiling -> too-dense -> ERROR (no finished),
    #    message must tell the user to INCREASE Grid Distance.
    cap = slice_mesh(box, 0.2, 0.0001)
    c.chk("tiny distance (safety ceiling) -> error", "error" in cap, cap.get("error", "")[:60])
    c.chk("tiny distance -> NO finished", "slices" not in cap)
    c.chk("tiny-distance error suggests increasing Grid Distance",
          "error" in cap and "grid distance" in cap["error"].lower()
          and ("artir" in cap["error"].lower() or "buyu" in cap["error"].lower()),
          cap.get("error", "")[:80])

    # 3) degenerate XY bounds (zero X extent) -> master_grid None via bounds-invalid branch
    #    -> ERROR (never a finished infill-less slice); distinct 'model bounds invalid' msg.
    sliver = pv.Box(bounds=(0, 0, -6, 6, 0, 10)).triangulate()
    cap = slice_mesh(sliver, 0.2, 0.4)
    c.chk("degenerate XY bounds -> error", "error" in cap, cap.get("error", "")[:70])
    c.chk("degenerate XY bounds -> NO finished", "slices" not in cap)
    c.chk("degenerate-bounds error names model bounds (not just density)",
          "error" in cap and ("sinir" in cap["error"].lower() or "dejenere" in cap["error"].lower()),
          cap.get("error", "")[:80])

    # 4) monkeypatch _build_master_grid -> None with a valid distance -> ERROR, no finished,
    #    and NO silent infill-less success (neither 'slices' nor 'infills').
    orig_bmg = SW._build_master_grid
    SW._build_master_grid = lambda *a, **k: None
    try:
        cap = slice_mesh(box, 0.2, 0.4)
    finally:
        SW._build_master_grid = orig_bmg
    c.chk("master_grid=None (monkeypatch) -> error", "error" in cap)
    c.chk("master_grid=None -> finished NOT emitted", "slices" not in cap)
    c.chk("master_grid=None -> no infill-less success", "slices" not in cap and "infills" not in cap)

    # ---- Grid Distance invalid (Feature 4): None/0/negatif/NaN/Inf -> slice ERROR ----
    for dval, tag in ((0.0, "0"), (-1.0, "-1"),
                      (float("nan"), "NaN"), (float("inf"), "Inf")):
        cap = slice_mesh(box, 0.2, dval)
        c.chk(f"distance={tag} -> error emitted", "error" in cap, cap.get("error", "")[:40])
        c.chk(f"distance={tag} -> NO finished", "slices" not in cap)
    c.chk("distance=None -> error", "error" in slice_mesh(box, 0.2, None))
    c.chk("distance=normal(0.4) -> finished", "slices" in slice_mesh(box, 0.2, 0.4))

    c.report_and_exit("SLICE PIPELINE SAFETY")


if __name__ == "__main__":
    run()
