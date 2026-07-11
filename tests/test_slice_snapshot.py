"""Slice snapshot / dirty-state (Section 10): geometry-affecting settings mark the
slice dirty (block export/print); well/printhead/temp/speed do NOT.
Run: python tests/test_slice_snapshot.py"""
import os, time, tempfile
from _util import Checker, np, pv
from PyQt6.QtWidgets import QApplication
from ui.main_window import KlipperArayuzu

_app = QApplication.instance()


def _simulate_slice(w):
    """Reproduce exactly what _slice_model + _on_slice_done do for the snapshot."""
    w._pending_slice_snapshot = w._current_slice_params()      # slice start
    w._slices = [pv.lines_from_points(np.array([[0, 0, 0.2], [1, 0, 0.2]], float))]
    w._slice_snapshot = w._pending_slice_snapshot              # finished OK


def run():
    c = Checker()
    w = KlipperArayuzu()

    # real STL on disk (path/size/mtime feed the snapshot)
    box = pv.Box(bounds=(-10, 10, -10, 10, 0, 10)).triangulate()
    f = tempfile.NamedTemporaryFile(suffix=".stl", delete=False); f.close()
    box.save(f.name)
    w.stl_dosya_yolu = f.name
    w.kutu_layer.setValue(0.20)
    w.kutu_distance.setValue(0.40)

    # before any slice -> dirty (no snapshot / no slices)
    c.chk("no snapshot -> dirty", w._slice_is_dirty() is True)

    _simulate_slice(w)
    c.chk("right after slice -> NOT dirty", w._slice_is_dirty() is False)

    # --- geometry-affecting changes -> DIRTY ---
    w.kutu_layer.setValue(0.10)
    c.chk("change Layer Thickness -> dirty", w._slice_is_dirty() is True)
    w.kutu_layer.setValue(0.20)
    c.chk("restore Layer Thickness -> not dirty", w._slice_is_dirty() is False)

    w.kutu_distance.setValue(1.00)
    c.chk("change Grid Distance -> dirty", w._slice_is_dirty() is True)
    w.kutu_distance.setValue(0.40)
    c.chk("restore Grid Distance -> not dirty", w._slice_is_dirty() is False)

    # change STL mtime/size (edit the file) -> dirty
    time.sleep(0.01)
    box2 = pv.Box(bounds=(-12, 12, -12, 12, 0, 10)).triangulate()
    box2.save(f.name)   # same path, new size+mtime
    c.chk("STL file modified (size/mtime) -> dirty", w._slice_is_dirty() is True)
    _simulate_slice(w)  # re-slice snapshot
    c.chk("re-slice after STL change -> not dirty", w._slice_is_dirty() is False)

    # change STL path to a missing file -> dirty
    w.stl_dosya_yolu = f.name + ".missing.stl"
    c.chk("STL path change -> dirty", w._slice_is_dirty() is True)
    w.stl_dosya_yolu = f.name
    _simulate_slice(w)
    c.chk("restore STL + re-slice -> not dirty", w._slice_is_dirty() is False)

    # --- NON-geometry changes -> NOT dirty (same slice, different origin/feed/temp) ---
    w._selected_wells = {"A1", "A2", "B3", "C4"}
    c.chk("change selected wells -> NOT dirty (multi-origin reuse)", w._slice_is_dirty() is False)

    if w.kutu_speed:
        w.kutu_speed.setValue(25)
        c.chk("change Print Speed -> NOT dirty", w._slice_is_dirty() is False)
    if w.ph_buton_grubu:
        btn = w.ph_buton_grubu.button(2)
        if btn:
            btn.setChecked(True)
        c.chk("change Printhead -> NOT dirty", w._slice_is_dirty() is False)
    if w.kutu_ph_temp:
        w.kutu_ph_temp.blockSignals(True); w.kutu_ph_temp.setValue(35.0); w.kutu_ph_temp.blockSignals(False)
        c.chk("change Printhead Temp -> NOT dirty", w._slice_is_dirty() is False)

    # export layer height comes from SNAPSHOT, not the widget
    snap_lh = w._slice_snapshot["layer_height"]
    w.kutu_layer.blockSignals(True); w.kutu_layer.setValue(0.10); w.kutu_layer.blockSignals(False)
    # (now dirty; export would be blocked, but the value it WOULD use is the snapshot's)
    c.chk("export layer_height source is snapshot (0.20), not widget (0.10)",
          abs(snap_lh - 0.20) < 1e-9 and abs((w._slice_snapshot or {}).get("layer_height") - 0.20) < 1e-9)
    c.chk("after that widget change -> dirty (export blocked)", w._slice_is_dirty() is True)

    os.unlink(f.name)
    w.close()
    c.report_and_exit("SLICE SNAPSHOT / DIRTY-STATE")


if __name__ == "__main__":
    run()
