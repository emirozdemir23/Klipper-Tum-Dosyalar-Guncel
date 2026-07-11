"""Preview render state (Section 7): NO base_cap fake rectangle, single centered
active layer regardless of well count, single ghost, no per-well actors, no actor
accumulation across slider moves.

OFFSCREEN logic/state test (a FakePlotter records add_mesh/remove_actor). This is
NOT a live Qt/OpenGL test — real GL cannot init in this headless environment.
Run: python tests/test_preview_state.py"""
from _util import Checker, np, pv
from types import SimpleNamespace, MethodType
from ui.main_window import KlipperArayuzu

N, LH, IDX = 60, 0.2, 25


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
    f._last_plate_size = max(150.0, N * LH * 2)
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
    f._slices = [_sq(i * LH) for i in range(N)]
    f._infills = [_sq(i * LH) for i in range(N)]
    f._layer_meshes = [_sq(i * LH) for i in range(N)]
    for m in ("_add_filament", "_flatten_polydata_for_preview"):
        setattr(f, m, MethodType(getattr(KlipperArayuzu, m), f))
    return f


def _active(pl):
    return sorted(k for k in pl.actors if k and (k.startswith("active_") or k.startswith("infill_")))


def run():
    c = Checker()

    # petri + 4-well -> identical single-origin preview
    fp = _mk(0, [])
    KlipperArayuzu._render_layer(fp, IDX)
    c.chk("no base_cap fake rectangle actor", "base_cap" not in fp.layer_plotter.actors)
    c.chk("single active_perimeter", sum(k == "active_perimeter" for k in fp.layer_plotter.actors) == 1)
    c.chk("single active_infill", sum(k == "active_infill" for k in fp.layer_plotter.actors) == 1)
    c.chk("single preview_ghost", sum(k == "preview_ghost" for k in fp.layer_plotter.actors) == 1)
    c.chk("no legacy 'ghost'/'infill_v'", "ghost" not in fp.layer_plotter.actors and "infill_v" not in fp.layer_plotter.actors)

    f4 = _mk(1, ["A1", "A2", "B3", "C4"])
    KlipperArayuzu._render_layer(f4, IDX)
    c.chk("4-well: NO per-well active_perimeter_* / infill_*",
          not any(k.startswith("active_perimeter_") or k.startswith("infill_") for k in f4.layer_plotter.actors))
    c.chk("4-well active set == petri active set (well-count independent)",
          _active(f4.layer_plotter) == _active(fp.layer_plotter))
    c.chk("4-well active set is exactly {active_infill, active_perimeter}",
          _active(f4.layer_plotter) == ["active_infill", "active_perimeter"])

    # actor accumulation across slider moves
    fc = _mk(1, ["A1", "A2", "B3", "C4"])
    for i in (IDX, N - 1, IDX, 5, 0, N // 2, N - 2):
        KlipperArayuzu._render_layer(fc, i)
    c.chk("no actor accumulation (exactly 2 active after 7 moves)",
          _active(fc.layer_plotter) == ["active_infill", "active_perimeter"])
    c.chk("still no base_cap after many moves", "base_cap" not in fc.layer_plotter.actors)
    c.chk("exactly one preview_ghost after many moves",
          sum(k == "preview_ghost" for k in fc.layer_plotter.actors) == 1)

    c.report_and_exit("PREVIEW STATE (offscreen, NOT live-GL)")


if __name__ == "__main__":
    run()
