"""Preview render state: active layer shown at its REAL per-layer Z (NOT flattened
to the base), single centered active layer regardless of well count, single ghost,
no base_cap, no per-well actors, no actor accumulation, source PolyData never
mutated by the render.

OFFSCREEN logic/state test (a FakePlotter records add_mesh/remove_actor). This is
NOT a live Qt/OpenGL test — real GL cannot init in this headless environment.
Run: python tests/test_preview_state.py"""
from _util import Checker, np, pv
from types import SimpleNamespace, MethodType
from ui.main_window import KlipperArayuzu

N, LH = 100, 0.2


def _sq(z):
    return pv.lines_from_points(np.array([[-5, -5, z], [5, -5, z], [5, 5, z], [-5, 5, z]], float), close=True)


class FakePlotter:
    def __init__(self):
        self.actors = {}
        self.camera = SimpleNamespace(zoom=lambda *a: None)
        self.interactor = SimpleNamespace(isVisible=lambda: False)
        self.camera_position = None
    def add_mesh(self, mesh, name=None, **kw):
        self.actors[name] = SimpleNamespace(name=name, mesh=mesh, kw=kw)
        return self.actors[name]
    def remove_actor(self, a, render=False):
        self.actors.pop(getattr(a, "name", a), None)
    def render(self): pass
    def reset_camera(self): pass
    def reset_camera_clipping_range(self): pass


def _mk(kind_id, wells):
    f = SimpleNamespace()
    f._closing = False
    f.kutu_layer = SimpleNamespace(value=lambda: LH)
    f.layer_plotter = FakePlotter()
    f._last_plate_size = max(150.0, N * LH * 2)     # skip static-scene rebuild
    f._render_last_idx = -1
    f._original_mesh = pv.Sphere(radius=3.0)
    f.bp_buton_grubu = SimpleNamespace(checkedId=lambda: kind_id)
    f._selected_wells = set(wells)
    f._well_registry = {w: {"center": (0.0, 0.0)} for w in wells}
    f._preview_well_actor_names = []
    f.layer_nav_label = SimpleNamespace(setText=lambda *_: None)
    f.layer_slider = None
    f._C_BASE = KlipperArayuzu._C_BASE
    f._C_WALL_DONE = KlipperArayuzu._C_WALL_DONE
    f._C_FILL_DONE = KlipperArayuzu._C_FILL_DONE
    # source per-layer Z = i*LH  (the "real" z_mid stand-in for this synthetic data)
    f._slices = [_sq(i * LH) for i in range(N)]
    f._infills = [_sq(i * LH) for i in range(N)]
    f._layer_meshes = [_sq(i * LH) for i in range(N)]
    setattr(f, "_add_filament", MethodType(KlipperArayuzu._add_filament, f))   # only helper still used
    return f


def _active(pl):
    return sorted(k for k in pl.actors if k and (k.startswith("active_") or k.startswith("infill_")))


def _zvals(pl, name):
    a = pl.actors.get(name)
    if a is None:
        return None
    return sorted(set(np.round(np.asarray(a.mesh.points)[:, 2], 6)))


def run():
    c = Checker()

    # ================= REAL-Z: active layer at its own Z, not flattened =========
    def _render(idx, kind=0, wells=()):
        f = _mk(kind, list(wells)); KlipperArayuzu._render_layer(f, idx); return f

    # Layer 0: active perimeter/infill Z == source layer-0 Z (== 0.0), NOT 0.04/0.05
    f0 = _render(0)
    src0_p = _zvals(SimpleNamespace(actors={"x": SimpleNamespace(mesh=f0._slices[0])}), "x")
    src0_i = _zvals(SimpleNamespace(actors={"x": SimpleNamespace(mesh=f0._infills[0])}), "x")
    c.chk("layer0 active_perimeter Z == source layer-0 Z", _zvals(f0.layer_plotter, "active_perimeter") == src0_p, _zvals(f0.layer_plotter, "active_perimeter"))
    c.chk("layer0 active_infill Z == source infill-0 Z", _zvals(f0.layer_plotter, "active_infill") == src0_i)
    c.chk("layer0 NOT flattened to 0.04/0.05", _zvals(f0.layer_plotter, "active_perimeter") != [0.04] and _zvals(f0.layer_plotter, "active_infill") != [0.05])
    c.chk("layer0 perimeter & infill share same real Z (no artificial offset)",
          _zvals(f0.layer_plotter, "active_perimeter") == _zvals(f0.layer_plotter, "active_infill"))

    # Layer 48: Z == source layer-48 Z, and > layer-0 Z
    f48 = _render(48)
    z0 = _zvals(f0.layer_plotter, "active_perimeter")[0]
    z48 = _zvals(f48.layer_plotter, "active_perimeter")[0]
    src48 = _zvals(SimpleNamespace(actors={"x": SimpleNamespace(mesh=f48._slices[48])}), "x")
    c.chk("layer48 active_perimeter Z == source layer-48 Z", _zvals(f48.layer_plotter, "active_perimeter") == src48, z48)
    c.chk("layer48 Z > layer0 Z", z48 > z0, f"{z48} > {z0}")

    # Layer 94: Z > layer-48 Z
    f94 = _render(94)
    z94 = _zvals(f94.layer_plotter, "active_perimeter")[0]
    c.chk("layer94 Z > layer48 Z", z94 > z48, f"{z94} > {z48}")
    c.chk("ordering z0 < z48 < z94", z0 < z48 < z94, f"{z0} < {z48} < {z94}")

    # Layer 49 ~ middle of the model height; Layer 95 ~ near the top
    z_first, z_last = 0 * LH, (N - 1) * LH          # source model Z span
    z49 = _zvals(_render(49).layer_plotter, "active_perimeter")[0]
    z95 = _zvals(_render(95).layer_plotter, "active_perimeter")[0]
    model_mid = (z_first + z_last) / 2.0
    c.chk("layer49 ~ middle of model height (within 1 layer)", abs(z49 - model_mid) <= LH + 1e-6, f"z49={z49} mid={model_mid}")
    c.chk("layer95 near top of model (>=90% height)", z95 >= 0.9 * z_last, f"z95={z95} top={z_last}")

    # ================= source PolyData NOT mutated by the render =================
    fS = _mk(0, [])
    before_p = np.asarray(fS._slices[48].points).copy()
    before_i = np.asarray(fS._infills[48].points).copy()
    KlipperArayuzu._render_layer(fS, 48)
    KlipperArayuzu._render_layer(fS, 94)   # render another layer too
    c.chk("source _slices[48] points unchanged after render", np.array_equal(before_p, np.asarray(fS._slices[48].points)))
    c.chk("source _infills[48] points unchanged after render", np.array_equal(before_i, np.asarray(fS._infills[48].points)))
    c.chk("source layer-48 Z still real (9.6), not flattened",
          sorted(set(np.round(np.asarray(fS._slices[48].points)[:, 2], 6))) == [round(48 * LH, 6)])

    # ================= single-origin actors / no base_cap / no per-well =========
    fp = _render(48, kind=0)
    c.chk("no base_cap fake rectangle actor", "base_cap" not in fp.layer_plotter.actors)
    c.chk("actors == {active_infill, active_perimeter} only", _active(fp.layer_plotter) == ["active_infill", "active_perimeter"])
    c.chk("single preview_ghost", sum(k == "preview_ghost" for k in fp.layer_plotter.actors) == 1)
    c.chk("no legacy 'ghost'/'infill_v'", "ghost" not in fp.layer_plotter.actors and "infill_v" not in fp.layer_plotter.actors)

    f4 = _render(48, kind=1, wells=["A1", "A2", "B3", "C4"])
    c.chk("4-well: NO per-well active_perimeter_* / infill_*",
          not any(k.startswith("active_perimeter_") or k.startswith("infill_") for k in f4.layer_plotter.actors))
    c.chk("4-well active set == petri (well-count independent)", _active(f4.layer_plotter) == _active(fp.layer_plotter))

    # ================= actor count identical for 1 / 4 / 12 wells ===============
    f1 = _render(48, kind=1, wells=["A1"])
    f12 = _render(48, kind=1, wells=[f"{r}{col}" for r in "ABC" for col in (1, 2, 3, 4)])
    c.chk("active-actor count == 2 for 1/4/12 wells",
          _active(f1.layer_plotter) == _active(f4.layer_plotter) == _active(f12.layer_plotter) == ["active_infill", "active_perimeter"])
    c.chk("total actor count identical for 1/4/12 wells",
          len(f1.layer_plotter.actors) == len(f4.layer_plotter.actors) == len(f12.layer_plotter.actors))

    # ================= no actor accumulation across slider moves ================
    fc = _mk(1, ["A1", "A2", "B3", "C4"])
    for i in (48, N - 1, 48, 5, 0, N // 2, N - 2, 94):
        KlipperArayuzu._render_layer(fc, i)
    c.chk("no accumulation (exactly 2 active after 8 moves)", _active(fc.layer_plotter) == ["active_infill", "active_perimeter"])
    c.chk("no per-well leftovers after moves", not any(k.startswith("active_perimeter_") or k.startswith("infill_") for k in fc.layer_plotter.actors))
    c.chk("exactly one preview_ghost after moves", sum(k == "preview_ghost" for k in fc.layer_plotter.actors) == 1)
    c.chk("still no base_cap after moves", "base_cap" not in fc.layer_plotter.actors)
    # last render was layer 94 -> active Z is layer-94 real Z, not base
    c.chk("after moves, active Z is last layer's real Z (not base)",
          _zvals(fc.layer_plotter, "active_perimeter") == [round(94 * LH, 6)])

    c.report_and_exit("PREVIEW STATE — REAL-Z (offscreen, NOT live-GL)")


if __name__ == "__main__":
    run()
