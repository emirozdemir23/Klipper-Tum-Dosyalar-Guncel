"""Model-change state reset: a NEW successfully-loaded STL fully invalidates the
old Preview/Slice/Export state; a cancelled / failed / invalid load preserves it;
a fresh slice recovers the UI.

OFFSCREEN logic/state test — QtInteractor GL cannot init headless, so the real
KlipperArayuzu drives off-screen pv.Plotter substitutes for the Model and Preview
plotters. NOT a live-GUI test. Run: python tests/test_model_change_state.py"""
import os, tempfile
from _util import Checker, np, pv
from types import SimpleNamespace
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox
from ui.main_window import KlipperArayuzu

_app = QApplication.instance()
_tmp = []


def _offscreen():
    p = pv.Plotter(off_screen=True, window_size=(320, 260))
    pv.set_new_attribute(p, "interactor", SimpleNamespace(isVisible=lambda: False))
    return p


def _line(z):
    return pv.lines_from_points(np.array([[-4, -4, z], [4, -4, z], [4, 4, z], [-4, 4, z]], float), close=True)


def _stl(bounds=(-5, 5, -5, 5, 0, 10)):
    f = tempfile.NamedTemporaryFile(suffix=".stl", delete=False); f.close(); _tmp.append(f.name)
    pv.Box(bounds=bounds).triangulate().save(f.name)
    return f.name


def _new_window_with_old_state(nlayers=40):
    """Fresh window with an old sliced model + valid snapshots + preview actors +
    enabled slider/export, both plotters off-screen."""
    w = KlipperArayuzu()
    w.plotter = _offscreen()
    w.layer_plotter = _offscreen()
    gc = tempfile.NamedTemporaryFile(suffix=".gcode", delete=False); gc.write(b"PRINT_START\nPRINT_END\n"); gc.close()
    _tmp.append(gc.name)
    w.stl_dosya_yolu = _stl()
    w._slices = [_line(i * 0.2) for i in range(nlayers)]
    w._infills = [_line(i * 0.2) for i in range(nlayers)]
    w._layer_meshes = list(w._slices)
    w._original_mesh = pv.Sphere(radius=3.0)
    snap = {"stl_path": w.stl_dosya_yolu, "stl_size": 1, "stl_mtime_ns": 1,
            "layer_height": 0.2, "grid_distance": 0.4, "grid_type": "Linear"}
    w._slice_snapshot = dict(snap)
    w._pending_slice_snapshot = dict(snap)
    w._export_snapshot = {"slice_snapshot": dict(snap), "origins": ("single", 120.0, 60.0),
                          "active_tool": "T0", "print_speed": 10.0,
                          "gcode_path": gc.name, "gcode_size": 24, "gcode_mtime_ns": 1}
    w._last_gcode_path = gc.name
    w._last_gcode_filename = os.path.basename(gc.name)
    for nm in ("preview_ghost", "active_perimeter", "active_infill"):
        w.layer_plotter.add_mesh(_line(9.6), name=nm)
    w.layer_plotter.add_mesh(pv.Plane(i_size=150, j_size=150), name="lp_platform")
    if w.layer_slider:
        w.layer_slider.setEnabled(True); w.layer_slider.setMinimum(0)
        w.layer_slider.setMaximum(nlayers - 1); w.layer_slider.setValue(20)
    if w.layer_nav_label:
        w.layer_nav_label.setText("21 / 40")
    if w.export_gcode_btn:
        w.export_gcode_btn.setEnabled(True)
    return w, gc.name


def run():
    c = Checker()
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    QMessageBox.critical = staticmethod(lambda *a, **k: None)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)

    # ===================== TEST 1 — successful new model -> full reset =========
    w, old_gcode = _new_window_with_old_state()
    c.chk("setup: old _slices populated + preview_ghost present",
          len(w._slices) == 40 and "preview_ghost" in w.layer_plotter.actors)
    pathB = _stl(bounds=(-6, 6, -6, 6, 0, 8))
    w._show_stl(pathB)

    c.chk("T1 _slices == []", w._slices == [])
    c.chk("T1 _infills == []", w._infills == [])
    c.chk("T1 _layer_meshes == []", w._layer_meshes == [])
    c.chk("T1 _slice_snapshot is None", w._slice_snapshot is None)
    c.chk("T1 _pending_slice_snapshot is None", w._pending_slice_snapshot is None)
    c.chk("T1 _export_snapshot is None", w._export_snapshot is None)
    c.chk("T1 _last_gcode_path is None", w._last_gcode_path is None)
    c.chk("T1 _last_gcode_filename is None", w._last_gcode_filename is None)
    c.chk("T1 preview_ghost removed", "preview_ghost" not in w.layer_plotter.actors)
    c.chk("T1 active_perimeter removed", "active_perimeter" not in w.layer_plotter.actors)
    c.chk("T1 active_infill removed", "active_infill" not in w.layer_plotter.actors)
    c.chk("T1 Preview fully empty (0 actors)", len(list(w.layer_plotter.actors)) == 0)
    c.chk("T1 slider disabled", w.layer_slider is not None and not w.layer_slider.isEnabled())
    c.chk("T1 slider at 0", w.layer_slider.value() == 0 and w.layer_slider.maximum() == 0)
    c.chk("T1 layer label empty state '— / —'", w.layer_nav_label.text() == "— / —")
    c.chk("T1 export button disabled", not w.export_gcode_btn.isEnabled())
    c.chk("T1 stl_dosya_yolu == new model path", w.stl_dosya_yolu == pathB)
    c.chk("T1 Model view kept (new mesh loaded)", w._loaded_model_mesh is not None and w._loaded_model_mesh.n_points > 0)

    # ---- TEST 5 (embedded): old G-code cannot start a print after model change ----
    up = []
    w._begin_gcode_upload = lambda *a, **k: up.append(1)
    w._motion_preflight = lambda *a, **k: True
    w._print_paused = False; w._print_start_inflight = False
    w._start_print()
    c.chk("T5 old-gcode Print does NOT upload (path cleared)", up == [])

    # ===================== TEST 4 — recovery after a fresh slice ================
    w._init_settings_plotter = lambda: None           # layer_plotter already off-screen
    w._change_page = lambda *a, **k: None
    new_slices = [_line(i * 0.2) for i in range(30)]
    new_infills = [_line(i * 0.2) for i in range(30)]
    w.stl_dosya_yolu = pathB
    w._pending_slice_snapshot = w._current_slice_params()   # as _slice_model does
    w._slice_stale = False
    w._on_slice_done(new_slices, list(new_slices), new_infills, pv.Sphere(radius=2.0))
    c.chk("T4 slider re-enabled after slice", w.layer_slider.isEnabled())
    c.chk("T4 slider range = 0..29", w.layer_slider.minimum() == 0 and w.layer_slider.maximum() == 29)
    c.chk("T4 export re-enabled after slice", w.export_gcode_btn.isEnabled())
    c.chk("T4 _slice_snapshot restored from pending", w._slice_snapshot == w._pending_slice_snapshot and w._slice_snapshot is not None)
    w._render_layer(15)     # draw the new preview
    c.chk("T4 new preview_ghost drawn", "preview_ghost" in w.layer_plotter.actors)
    c.chk("T4 new active_perimeter drawn", "active_perimeter" in w.layer_plotter.actors)
    c.chk("T4 new active_infill drawn", "active_infill" in w.layer_plotter.actors)
    c.chk("T4 new active layer at real Z (15*0.2=3.0), not base",
          sorted(set(np.round(np.asarray(w.layer_plotter.actors["active_perimeter"].mapper.dataset.points)[:, 2], 6))) == [3.0])
    w.close()

    # ===================== TEST 2 — dialog Cancel preserves everything ==========
    w2, g2 = _new_window_with_old_state()
    old_slices, old_snap, old_exp, old_path, old_stl = (w2._slices, w2._slice_snapshot, w2._export_snapshot,
                                                        w2._last_gcode_path, w2.stl_dosya_yolu)
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: ("", ""))   # Cancel
    w2._select_stl()
    c.chk("T2 Cancel: _slices preserved", w2._slices is old_slices and len(w2._slices) == 40)
    c.chk("T2 Cancel: _slice_snapshot preserved", w2._slice_snapshot is old_snap)
    c.chk("T2 Cancel: _export_snapshot preserved", w2._export_snapshot is old_exp)
    c.chk("T2 Cancel: _last_gcode_path preserved", w2._last_gcode_path == old_path)
    c.chk("T2 Cancel: stl_dosya_yolu preserved", w2.stl_dosya_yolu == old_stl)
    c.chk("T2 Cancel: preview_ghost still present", "preview_ghost" in w2.layer_plotter.actors)
    c.chk("T2 Cancel: slider still enabled", w2.layer_slider.isEnabled())
    w2.close()

    # ===================== TEST 3 — invalid / unreadable STL preserves state ====
    w3, g3 = _new_window_with_old_state()
    o_slices, o_snap, o_path, o_stl = w3._slices, w3._slice_snapshot, w3._last_gcode_path, w3.stl_dosya_yolu
    w3._show_stl("/no/such/dir/does_not_exist_xyz.stl")     # FileNotFoundError
    c.chk("T3 missing file: _slices preserved", w3._slices is o_slices)
    c.chk("T3 missing file: _slice_snapshot preserved", w3._slice_snapshot is o_snap)
    c.chk("T3 missing file: _last_gcode_path preserved", w3._last_gcode_path == o_path)
    c.chk("T3 missing file: stl_dosya_yolu unchanged", w3.stl_dosya_yolu == o_stl)
    c.chk("T3 missing file: preview_ghost preserved", "preview_ghost" in w3.layer_plotter.actors)
    # garbage (non-STL) file -> pv.read raises -> state preserved
    bad = tempfile.NamedTemporaryFile(suffix=".stl", delete=False); bad.write(b"not an stl at all"); bad.close(); _tmp.append(bad.name)
    w3._show_stl(bad.name)
    c.chk("T3 garbage STL: _slices still preserved", w3._slices is o_slices)
    c.chk("T3 garbage STL: stl_dosya_yolu still old", w3.stl_dosya_yolu == o_stl)
    c.chk("T3 garbage STL: preview_ghost still present", "preview_ghost" in w3.layer_plotter.actors)
    w3.close()

    # ========== EDGE 1: in-flight slice NOT aborted on failed/cancelled select ==
    class _FW:                       # fake worker with a request_abort counter
        def __init__(self): self.n = 0
        def request_abort(self): self.n += 1

    def _win_slicing():
        w, _g = _new_window_with_old_state()
        w._slicing = True; w._slice_worker = _FW(); w._slice_stale = False
        return w

    # A — dialog Cancel
    wa = _win_slicing()
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: ("", ""))
    wa._select_stl()
    c.chk("A Cancel: _slice_stale stays False", wa._slice_stale is False)
    c.chk("A Cancel: request_abort NOT called", wa._slice_worker.n == 0)
    c.chk("A Cancel: old _slices preserved", len(wa._slices) == 40)
    wa.close()

    # B — missing file
    wb = _win_slicing()
    wb._show_stl("/no/such/dir/missing_abc.stl")
    c.chk("B missing: _slice_stale False", wb._slice_stale is False)
    c.chk("B missing: request_abort NOT called", wb._slice_worker.n == 0)
    wb.close()

    # C — garbage / invalid STL
    wc = _win_slicing()
    bad = tempfile.NamedTemporaryFile(suffix=".stl", delete=False); bad.write(b"garbage not stl"); bad.close(); _tmp.append(bad.name)
    wc._show_stl(bad.name)
    c.chk("C garbage: _slice_stale False", wc._slice_stale is False)
    c.chk("C garbage: request_abort NOT called", wc._slice_worker.n == 0)
    wc.close()

    # D — large-file question -> No
    wd = _win_slicing()
    big = tempfile.NamedTemporaryFile(suffix=".stl", delete=False)
    big.truncate(220 * 1024 * 1024); big.close(); _tmp.append(big.name)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.No)
    wd._show_stl(big.name)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    c.chk("D large-file No: _slice_stale False", wd._slice_stale is False)
    c.chk("D large-file No: request_abort NOT called", wd._slice_worker.n == 0)
    c.chk("D large-file No: old _slices preserved", len(wd._slices) == 40)
    wd.close()

    # E — successful model change with in-flight slice: abort EXACTLY once + stale
    we = _win_slicing()
    validE = _stl(bounds=(-4, 4, -4, 4, 0, 6))
    we._show_stl(validE)
    c.chk("E success: _slice_stale True", we._slice_stale is True)
    c.chk("E success: request_abort called EXACTLY once", we._slice_worker.n == 1)
    c.chk("E success: state invalidated (_slices == [])", we._slices == [])
    c.chk("E success: _slice_snapshot None", we._slice_snapshot is None)
    we.close()

    # ========== EDGE 2: read OK but Model-scene build fails => ROLLBACK ==========
    wr, _grc = _new_window_with_old_state()
    wr._loaded_model_mesh = pv.Box(bounds=(-3, 3, -3, 3, 0, 5)).triangulate()   # Model A mesh
    wr._slicing = True; wr._slice_worker = _FW(); wr._slice_stale = False
    o_stl, o_mesh = wr.stl_dosya_yolu, wr._loaded_model_mesh
    o_sl, o_inf, o_snap, o_exp, o_gp = (wr._slices, wr._infills, wr._slice_snapshot,
                                        wr._export_snapshot, wr._last_gcode_path)
    def _boom(*a, **k):
        raise RuntimeError("model scene build failed")
    wr._update_model_copies = _boom          # Model-scene step throws
    modelB = _stl(bounds=(-6, 6, -6, 6, 0, 9))
    wr._show_stl(modelB)
    c.chk("R rollback: stl_dosya_yolu == old (Model A)", wr.stl_dosya_yolu == o_stl)
    c.chk("R rollback: _loaded_model_mesh == old", wr._loaded_model_mesh is o_mesh)
    c.chk("R rollback: _slices preserved (untouched)", wr._slices is o_sl and len(wr._slices) == 40)
    c.chk("R rollback: _infills preserved", wr._infills is o_inf)
    c.chk("R rollback: _slice_snapshot preserved", wr._slice_snapshot is o_snap)
    c.chk("R rollback: _export_snapshot preserved", wr._export_snapshot is o_exp)
    c.chk("R rollback: _last_gcode_path preserved", wr._last_gcode_path == o_gp)
    c.chk("R rollback: preview_ghost preserved (invalidate NOT run)", "preview_ghost" in wr.layer_plotter.actors)
    c.chk("R rollback: slider still enabled", wr.layer_slider.isEnabled())
    c.chk("R rollback: export still enabled", wr.export_gcode_btn.isEnabled())
    c.chk("R rollback: request_abort NOT called", wr._slice_worker.n == 0)
    c.chk("R rollback: _slice_stale stays False", wr._slice_stale is False)
    wr.close()

    # ========== EDGE 3: pv.read called EXACTLY once per _show_stl ================
    import pyvista as _pyv
    wq, _gq = _new_window_with_old_state()
    validR = _stl(bounds=(-4, 4, -4, 4, 0, 5))
    _orig_read, _rc = _pyv.read, {"n": 0}
    def _spy(*a, **k):
        _rc["n"] += 1
        return _orig_read(*a, **k)
    _pyv.read = _spy
    try:
        wq._show_stl(validR)
    finally:
        _pyv.read = _orig_read
    c.chk("pv.read called EXACTLY once per _show_stl", _rc["n"] == 1, _rc["n"])
    wq.close()

    for p in set(_tmp):
        try: os.unlink(p)
        except OSError: pass
    c.report_and_exit("MODEL-CHANGE STATE RESET (offscreen, NOT live-GL)")


if __name__ == "__main__":
    run()
