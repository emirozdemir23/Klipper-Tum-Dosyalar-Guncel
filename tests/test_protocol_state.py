"""Protocol + platform state (Section 11): save disk-failure must NOT also show
'saved successfully'; selected_wells stays synced between PlatformTab and window.
Run: python tests/test_protocol_state.py"""
from _util import Checker, np, pv
from PyQt6.QtWidgets import QApplication, QMessageBox, QInputDialog
from ui import main_window as MW
from ui.main_window import KlipperArayuzu

_app = QApplication.instance()


def run():
    c = Checker()
    w = KlipperArayuzu()

    # ---- monkeypatch dialogs to record which popups fire ----
    rec = {"info": 0, "critical": 0}
    QMessageBox.information = staticmethod(lambda *a, **k: rec.__setitem__("info", rec["info"] + 1))
    QMessageBox.critical = staticmethod(lambda *a, **k: rec.__setitem__("critical", rec["critical"] + 1))
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)

    w._editing_protocol_name = "TestP"     # -> question(Yes) path, no name dialog
    w.kayitli_protokoller = {}

    # (1) disk write FAILS -> critical shown, success NOT shown
    def boom(*a, **k):
        raise OSError("disk full (injected)")
    w.dm.save_to_disk = boom
    rec["info"] = rec["critical"] = 0
    w._editing_protocol_name = "TestP"
    w._save_protocol()
    c.chk("disk failure -> 'Save Error' (critical) shown", rec["critical"] == 1, rec)
    c.chk("disk failure -> NO 'saved successfully' (info) shown", rec["info"] == 0, rec)

    # (2) disk write SUCCEEDS -> success shown, no critical
    w.dm.save_to_disk = lambda *a, **k: None
    rec["info"] = rec["critical"] = 0
    w._editing_protocol_name = "TestP"
    w._save_protocol()
    c.chk("disk success -> 'saved successfully' (info) shown", rec["info"] == 1, rec)
    c.chk("disk success -> NO 'Save Error' (critical)", rec["critical"] == 0, rec)

    # ---- selected_wells sync between PlatformTab and window ----
    w.platform_tab.btn_well.setChecked(True)
    w.platform_tab.btn_6.setChecked(True)
    for wid in ("A1", "A3", "B2"):
        w.platform_tab.well_buttons[wid].setChecked(True)
    c.chk("PlatformTab selection -> window._selected_wells synced",
          w._selected_wells == {"A1", "A3", "B2"} == set(w.platform_tab.selected_wells))
    # window-side registry has centers for the same wells (export/preview source)
    c.chk("window _well_registry covers selected wells",
          {"A1", "A3", "B2"}.issubset(set(w._well_registry.keys())))

    w.close()
    c.report_and_exit("PROTOCOL / PLATFORM STATE")


if __name__ == "__main__":
    run()
