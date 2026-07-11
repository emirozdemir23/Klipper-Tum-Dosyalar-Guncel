"""Export snapshot / freshness (Feature 1): after a successful export, changing
the well selection / printhead / print-speed, or deleting/editing the G-code,
makes the export DIRTY (blocks Print with the right message); temperature /
slider / UV-HEPA do NOT. Slice stays clean throughout these changes.
Run: python tests/test_export_snapshot.py"""
import os, tempfile
from _util import slice_mesh, Checker, np, pv
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox
from ui.main_window import KlipperArayuzu

_app = QApplication.instance()


def run():
    c = Checker()
    w = KlipperArayuzu()

    # --- real STL + real slice; set slice snapshot as _slice_model/_on_slice_done do ---
    box = pv.Box(bounds=(-6, 6, -6, 6, 0, 10)).triangulate()
    stl = tempfile.NamedTemporaryFile(suffix=".stl", delete=False); stl.close()
    box.save(stl.name)
    w.stl_dosya_yolu = stl.name
    w.kutu_layer.setValue(0.20); w.kutu_distance.setValue(0.40)
    cap = slice_mesh(box, 0.20, 0.40)
    w._slices, w._infills, w._layer_meshes = cap["slices"], cap["infills"], cap["layer_meshes"]
    w._original_mesh = cap["ghost"]
    w._pending_slice_snapshot = w._current_slice_params()
    w._slice_snapshot = w._pending_slice_snapshot

    # --- platform: 6-well, select A1 ---
    w.platform_tab.btn_well.setChecked(True)
    w.platform_tab.btn_6.setChecked(True)
    w.platform_tab.well_buttons["A1"].setChecked(True)
    if w.kutu_speed:
        w.kutu_speed.setValue(10)

    # --- run the REAL export (monkeypatched dialogs) ---
    gcode = tempfile.NamedTemporaryFile(suffix=".gcode", delete=False); gcode.close()
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (gcode.name, ""))
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    QMessageBox.critical = staticmethod(lambda *a, **k: None)
    w._on_export_gcode()

    c.chk("export snapshot created", w._export_snapshot is not None)
    c.chk("right after export: slice NOT dirty", w._slice_is_dirty() is False)
    c.chk("right after export: export NOT dirty", w._export_is_dirty() is False)

    # --- well selection change -> export dirty, slice clean ---
    w.platform_tab.well_buttons["B3"].setChecked(True)   # now A1+B3
    c.chk("A1 export then select B3: slice clean", w._slice_is_dirty() is False)
    c.chk("A1 export then select B3: export DIRTY", w._export_is_dirty() is True)
    w.platform_tab.well_buttons["B3"].setChecked(False)  # back to A1
    c.chk("restore well selection -> export not dirty", w._export_is_dirty() is False)

    # --- printhead / tool change -> export dirty ---
    if w.ph_buton_grubu:
        w.ph_buton_grubu.button(2).setChecked(True)      # Printhead 2 -> T1
        c.chk("T0 export then Printhead 2: slice clean", w._slice_is_dirty() is False)
        c.chk("T0 export then Printhead 2: export DIRTY", w._export_is_dirty() is True)
        w.ph_buton_grubu.button(1).setChecked(True)
        c.chk("restore printhead -> export not dirty", w._export_is_dirty() is False)

    # --- print speed change -> export dirty ---
    w.kutu_speed.setValue(20)
    c.chk("speed 10 export then speed 20: slice clean", w._slice_is_dirty() is False)
    c.chk("speed 10 export then speed 20: export DIRTY", w._export_is_dirty() is True)
    w.kutu_speed.setValue(10)
    c.chk("restore speed -> export not dirty", w._export_is_dirty() is False)

    # --- temperature-only change -> export NOT dirty ---
    if w.kutu_ph_temp:
        w.kutu_ph_temp.blockSignals(True); w.kutu_ph_temp.setValue(40.0); w.kutu_ph_temp.blockSignals(False)
    if w.kutu_plat_temp:
        w.kutu_plat_temp.blockSignals(True); w.kutu_plat_temp.setValue(-25.0); w.kutu_plat_temp.blockSignals(False)
    c.chk("temperature change only -> export NOT dirty", w._export_is_dirty() is False)

    # --- G-code file deleted -> dirty ---
    os.unlink(gcode.name)
    c.chk("G-code deleted -> export dirty", w._export_is_dirty() is True)
    # re-export -> clean again
    gcode2 = tempfile.NamedTemporaryFile(suffix=".gcode", delete=False); gcode2.close()
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (gcode2.name, ""))
    w._on_export_gcode()
    c.chk("re-export -> export NOT dirty", w._export_is_dirty() is False)

    # --- G-code externally modified -> dirty ---
    import time as _t; _t.sleep(0.01)
    with open(gcode2.name, "a", encoding="utf-8") as fh:
        fh.write("; tampered\n")
    c.chk("G-code content modified -> export dirty", w._export_is_dirty() is True)

    # --- _start_print BLOCKED when export dirty (right message, no upload) ---
    uploaded = []
    w._begin_gcode_upload = lambda *a, **k: uploaded.append(1)
    w._motion_preflight = lambda *a, **k: True   # bypass offline motion gate
    w._print_paused = False
    w._print_start_inflight = False
    warned = {"n": 0, "last": ""}
    QMessageBox.warning = staticmethod(lambda *a, **k: (warned.__setitem__("n", warned["n"] + 1),
                                                        warned.__setitem__("last", a[2] if len(a) > 2 else "")))
    w._start_print()
    c.chk("dirty export -> _start_print did NOT upload", uploaded == [])
    c.chk("dirty export -> _start_print warned user", warned["n"] >= 1, warned["last"][:40])

    # ================= RE-SLICE FRESHNESS (Item 1): new slice snapshot dirties export =====
    import ui.main_window as MWmod
    temp_files = [stl.name, gcode2.name]

    def _mark_sliced():                # simulate a successful re-slice with CURRENT settings
        w._pending_slice_snapshot = w._current_slice_params()
        w._slice_snapshot = w._pending_slice_snapshot

    def _reexport():                   # fresh successful export -> clean export snapshot
        g = tempfile.NamedTemporaryFile(suffix=".gcode", delete=False); g.close()
        temp_files.append(g.name)
        QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (g.name, ""))
        QMessageBox.information = staticmethod(lambda *a, **k: None)
        QMessageBox.warning = staticmethod(lambda *a, **k: None)
        QMessageBox.critical = staticmethod(lambda *a, **k: None)
        w._on_export_gcode()
        return g.name

    def _blocked():                    # returns (uploaded_empty, warned_text) for _start_print
        up = []; wn = {"t": ""}
        w._begin_gcode_upload = lambda *a, **k: up.append(1)
        w._motion_preflight = lambda *a, **k: True
        w._print_paused = False; w._print_start_inflight = False
        QMessageBox.warning = staticmethod(lambda *a, **k: wn.__setitem__("t", a[2] if len(a) > 2 else ""))
        w._start_print()
        return (up == [], wn["t"])

    # baseline @ layer 0.20, grid 0.40, STL A
    w.stl_dosya_yolu = stl.name
    w.kutu_layer.setValue(0.20); w.kutu_distance.setValue(0.40); w.kutu_speed.setValue(10)
    w.ph_buton_grubu.button(1).setChecked(True)
    _mark_sliced(); _reexport()
    c.chk("baseline re-export clean", not w._export_is_dirty())

    # TEST A: layer 0.20 export -> layer 0.10 + re-slice (slice clean, export dirty)
    w.kutu_layer.setValue(0.10); _mark_sliced()
    c.chk("A layer 0.10 re-slice -> slice CLEAN", not w._slice_is_dirty())
    c.chk("A layer 0.10 re-slice -> export DIRTY", w._export_is_dirty())
    up_ok, txt = _blocked()
    c.chk("A _start_print did NOT upload", up_ok)
    c.chk("A _start_print warned 'Export'", "Export" in txt, txt[:36])

    # TEST B: grid 0.40 export -> grid 1.00 + re-slice
    w.kutu_layer.setValue(0.20); w.kutu_distance.setValue(0.40); _mark_sliced(); _reexport()
    w.kutu_distance.setValue(1.00); _mark_sliced()
    c.chk("B grid re-slice -> slice CLEAN", not w._slice_is_dirty())
    c.chk("B grid re-slice -> export DIRTY", w._export_is_dirty())

    # TEST C: STL A export -> STL B + re-slice (slice clean, export dirty, print blocked)
    w.kutu_distance.setValue(0.40); w.stl_dosya_yolu = stl.name; _mark_sliced(); _reexport()
    stlB = tempfile.NamedTemporaryFile(suffix=".stl", delete=False); stlB.close()
    pv.Box(bounds=(-7, 7, -7, 7, 0, 10)).triangulate().save(stlB.name); temp_files.append(stlB.name)
    w.stl_dosya_yolu = stlB.name; _mark_sliced()
    c.chk("C different STL re-slice -> slice CLEAN", not w._slice_is_dirty())
    c.chk("C different STL re-slice -> export DIRTY", w._export_is_dirty())
    up_ok, _ = _blocked()
    c.chk("C print blocked (no upload)", up_ok)

    # TEST D: same-params re-slice -> export NOT dirty (deterministic)
    w.stl_dosya_yolu = stlB.name; _mark_sliced(); _reexport()
    _mark_sliced()   # identical params again
    c.chk("D same-snapshot re-slice -> export NOT dirty", not w._export_is_dirty())

    # ================= FAILED EXPORT invalidates old export (Item 3) =====
    w.stl_dosya_yolu = stl.name; _mark_sliced(); _reexport()
    c.chk("pre-fail: snapshot+path valid", w._export_snapshot is not None and w._last_gcode_path is not None)
    _og, _ogm = MWmod.generate_gcode, MWmod.generate_gcode_multi_origin
    def _raise(*a, **k):
        raise ValueError("boom export")
    MWmod.generate_gcode = _raise; MWmod.generate_gcode_multi_origin = _raise
    gF = tempfile.NamedTemporaryFile(suffix=".gcode", delete=False); gF.close(); temp_files.append(gF.name)
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (gF.name, ""))
    QMessageBox.critical = staticmethod(lambda *a, **k: None)
    w._on_export_gcode()
    MWmod.generate_gcode, MWmod.generate_gcode_multi_origin = _og, _ogm
    c.chk("failed export -> _export_snapshot is None", w._export_snapshot is None)
    c.chk("failed export -> _last_gcode_path is None", w._last_gcode_path is None)
    up_ok, _ = _blocked()
    c.chk("failed export -> print does not start", up_ok)

    # ================= DIALOG CANCEL preserves old export =====
    _mark_sliced(); gKeep = _reexport()
    snap_before, path_before = w._export_snapshot, w._last_gcode_path
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: ("", ""))   # Cancel
    w._on_export_gcode()
    c.chk("dialog Cancel -> export snapshot PRESERVED",
          w._export_snapshot is snap_before and w._last_gcode_path == path_before)

    # cleanup
    for p in set(temp_files):
        try: os.unlink(p)
        except OSError: pass
    w.close()
    c.report_and_exit("EXPORT SNAPSHOT / FRESHNESS")


if __name__ == "__main__":
    run()
