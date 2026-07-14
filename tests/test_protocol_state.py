"""Canonical three-printhead Settings, platform assignment and protocol tests."""
import json
import math
import tempfile
from copy import deepcopy
from pathlib import Path

from _util import Checker
from PyQt6.QtWidgets import QApplication, QFormLayout, QInputDialog, QMessageBox
from core.data_manager import DataManager
from core.printhead import (
    normalize_nozzle_diameter, normalize_print_speed,
    normalize_printhead_temperature, normalize_well_assignments,
)
from ui.main_window import KlipperArayuzu

_app = QApplication.instance()


def run():
    c = Checker()
    w = KlipperArayuzu()
    settings = w.settings_tab
    platform = w.platform_tab

    c.chk("old printhead buttons removed", not hasattr(settings, "ph1_btn"))
    c.chk("QTabWidget has three tabs", settings.printhead_tabs.count() == 3)
    c.chk("tab names", [settings.printhead_tabs.tabText(i) for i in range(3)] ==
          ["Printhead 1", "Printhead 2", "Printhead 3"])
    for head in (1, 2, 3):
        widgets = settings.printhead_widgets[head]
        form = settings.printhead_tabs.widget(head - 1).layout()
        labels = [form.itemAt(i, QFormLayout.ItemRole.LabelRole).widget().text()
                  for i in range(form.rowCount())]
        c.chk(f"PH{head} field labels", labels == [
            "Nozzle Diameter", "Print Speed", "Printhead Temperature"])
        nozzle = widgets["nozzle_diameter_mm"]
        c.chk(f"PH{head} nozzle contract", math.isclose(nozzle.minimum(), .1)
              and math.isclose(nozzle.maximum(), .9)
              and math.isclose(nozzle.value(), .4)
              and math.isclose(nozzle.singleStep(), .01)
              and nozzle.decimals() == 2 and nozzle.suffix() == " mm")
    settings.nozzle_diameter_spins[1].setValue(.25)
    c.chk("profiles use distinct nozzle widgets",
          settings.nozzle_diameter_spins[2].value() == .4
          and settings.nozzle_diameter_spins[3].value() == .4)
    settings.print_speed_spins[2].setValue(17)
    c.chk("profiles use distinct speed widgets",
          settings.print_speed_spins[1].value() == 10
          and settings.print_speed_spins[3].value() == 10)
    settings.printhead_temperature_spins[3].setValue(31)
    c.chk("profiles use distinct temperature widgets",
          settings.printhead_temperature_spins[1].value() == 27
          and settings.printhead_temperature_spins[2].value() == 27)

    network, slicing, exporting = [], [], []
    w._send_moonraker_request = lambda *a, **k: network.append((a, k))
    w._slice_model = lambda: slicing.append(1)
    w._on_export_gcode = lambda: exporting.append(1)
    settings.printhead_tabs.setCurrentIndex(2)
    c.chk("tab selects PH3", w.selected_printhead == 3)
    c.chk("tab switch has no side effects", network == [] and slicing == [] and exporting == [])

    c.chk("assignment segmented buttons", platform.assignment_head_group.exclusive()
          and len(platform.assignment_head_buttons) == 3)
    c.chk("no Clear/Auto Assign controls",
          not hasattr(platform, "clear_btn") and not hasattr(platform, "auto_assign_btn"))
    platform.btn_well.click()
    c.chk("6-well empty summary", platform.assignment_summary_label.text() ==
          "PH1: 0 | PH2: 0 | PH3: 0 | Empty: 6")
    c.chk("empty well is white", "#FFFFFF" in platform.well_buttons["A1"].styleSheet())
    platform.well_buttons["A1"].click()
    c.chk("empty well assigns PH1", w.well_assignments == {"A1": 1})
    c.chk("PH1 visual", platform.well_buttons["A1"].text() == "PH1"
          and "#D9EEFF" in platform.well_buttons["A1"].styleSheet())
    platform.well_buttons["A1"].click()
    c.chk("second click removes assignment", w.well_assignments == {}
          and platform.well_buttons["A1"].text() == "")
    platform.well_buttons["A1"].click()
    platform.assignment_head_buttons[2].click()
    platform.well_buttons["A1"].click()
    c.chk("single-click reassignment to PH2", w.well_assignments == {"A1": 2})
    c.chk("PH2 visual", platform.well_buttons["A1"].text() == "PH2"
          and "#78BDF2" in platform.well_buttons["A1"].styleSheet())
    platform.assignment_head_buttons[3].click(); platform.well_buttons["B1"].click()
    c.chk("PH3 visual", platform.well_buttons["B1"].text() == "PH3"
          and "#1F78D1" in platform.well_buttons["B1"].styleSheet())
    c.chk("well interiors never show IDs",
          all(button.text() in ("", "PH1", "PH2", "PH3")
              for button in platform.well_buttons.values()))
    c.chk("summary from assignments", platform.assignment_summary_label.text() ==
          "PH1: 0 | PH2: 1 | PH3: 1 | Empty: 4")
    platform.btn_12.click(); platform.assignment_head_buttons[1].click()
    platform.well_buttons["C1"].click(); platform.well_buttons["A4"].click()
    c.chk("12-well total", platform.assignment_summary_label.text().endswith("Empty: 8"))
    platform.btn_6.click()
    c.chk("format removes hidden assignments", "C1" not in w.well_assignments
          and "A4" not in w.well_assignments and w.well_assignments["A1"] == 2)

    warnings = []
    QMessageBox.warning = staticmethod(lambda *a, **k: warnings.append(a[2]))
    platform.set_well_assignments({}, emit_signal=False)
    w._change_page(2); w._confirm_platform()
    c.chk("Apply blocks empty Well Plate", w.sayfalar_alani.currentIndex() == 2
          and warnings and "Select at least one well" in warnings[-1])
    platform.btn_petri.click(); w._change_page(2); w._confirm_platform()
    c.chk("Petri does not require assignments", w.sayfalar_alani.currentIndex() == 3)
    platform.btn_well.click(); platform.set_well_assignments(
        {"A1": 2, "B1": 3}, emit_signal=True)

    cases = [(None, .4), ("abc", .4), (float("nan"), .4),
             (float("inf"), .4), (True, .4), (-5, .1), (4.0, .9), (.25, .25)]
    for value, expected in cases:
        c.chk(f"nozzle normalize {value!r}",
              math.isclose(normalize_nozzle_diameter(value), expected))
    c.chk("speed rejects bool/text/nonfinite and clamps",
          normalize_print_speed(True) == 10
          and normalize_print_speed("9") == 10
          and normalize_print_speed(float("inf")) == 10
          and normalize_print_speed(-5) == 1
          and normalize_print_speed(99) == 30)
    c.chk("temperature rejects bool/text/nonfinite and clamps",
          normalize_printhead_temperature(False) == 27
          and normalize_printhead_temperature("20") == 27
          and normalize_printhead_temperature(float("nan")) == 27
          and normalize_printhead_temperature(-5) == 4
          and normalize_printhead_temperature(99) == 45)
    c.chk("assignment validation rejects invalid wells/heads/bools",
          normalize_well_assignments(
              {"A1": 1, "A2": True, "B1": 4, "Z9": 2}, 6) == {"A1": 1})

    legacy = {
        "protocol_name": "Legacy", "printhead_number": 2,
        "nozzle_diameter": .55, "print_speed_mm_s": 12,
        "printhead_temperature_c": 29,
        "built_platform": {"type_index": 1, "well_format": 6,
                           "selected_wells": ["A1", "B2", "Z9"]},
    }
    migrated = DataManager.normalize_protocol_payload(legacy)
    c.chk("legacy selected head", migrated["selected_printhead"] == 2)
    c.chk("legacy profile copied to all", all(
        profile == migrated["printheads"][1]
        for profile in migrated["printheads"].values()))
    c.chk("legacy wells assigned to old head",
          migrated["well_assignments"] == {"A1": 2, "B2": 2})
    preferred = deepcopy(legacy)
    preferred["well_assignments"] = {"A3": 3}
    c.chk("canonical assignments ignore legacy list",
          DataManager.normalize_protocol_payload(preferred)["well_assignments"] == {"A3": 3})
    with tempfile.TemporaryDirectory() as migration_tmp:
        legacy_path = Path(migration_tmp) / "legacy.json"
        legacy_path.write_text(json.dumps(legacy), encoding="utf-8")
        before_load = legacy_path.read_bytes()
        migration_dm = DataManager(); migration_dm.protocols_dir = Path(migration_tmp)
        migration_dm.load_protocols()
        c.chk("legacy load does not rewrite user JSON",
              legacy_path.read_bytes() == before_load)

    QMessageBox.information = staticmethod(lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    QMessageBox.critical = staticmethod(lambda *a, **k: None)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    with tempfile.TemporaryDirectory() as tmp:
        w.dm.protocols_dir = Path(tmp)
        w._editing_protocol_name = None
        w.kutu_layer.setValue(.33)
        w.kutu_distance.setValue(.44)
        w.kutu_plat_temp.blockSignals(True); w.kutu_plat_temp.setValue(-22)
        w.kutu_plat_temp.blockSignals(False)
        QInputDialog.getText = staticmethod(lambda *a, **k: ("RoundTrip", True))
        w._save_protocol()
        path = Path(tmp) / w.dm.sanitize_filename("RoundTrip")
        saved = json.loads(path.read_text(encoding="utf-8"))
        c.chk("canonical keys saved", all(k in saved for k in
              ("selected_printhead", "printheads", "well_assignments")))
        c.chk("legacy selection keys absent", "selected_wells" not in saved
              and "bp_selected_wells" not in saved
              and "selected_wells" not in saved["built_platform"])
        c.chk("profile numbers saved as numbers",
              isinstance(saved["printheads"]["ph1"]["nozzle_diameter_mm"], float))
        c.chk("other protocol fields preserved in JSON",
              math.isclose(saved["layer_thickness_mm"], .33)
              and math.isclose(saved["grid_distance_mm"], .44)
              and math.isclose(saved["platform_temperature_c"], -22))
        dm = DataManager(); dm.protocols_dir = Path(tmp); records = dm.load_protocols()
        w.kayitli_protokoller.clear(); w.kayitli_protokoller.update(records)
        w._refresh_protocol_list(); w.protokol_listesi.setCurrentRow(0)
        before_network = list(network)
        w._open_protocol()
        c.chk("Open restores selected tab", w.printhead_tabs.currentIndex() == 2)
        c.chk("Open restores profiles", math.isclose(
            w.settings_tab.nozzle_diameter_spins[1].value(), .25))
        c.chk("Open restores well summary visibility",
              not w.platform_tab.assignment_summary_label.isHidden())
        c.chk("Open causes no network/Slice/Export",
              network == before_network and slicing == [] and exporting == [])
        w._edit_protocol()
        c.chk("Edit uses same load and sets editing state",
              w._editing_protocol_name == "RoundTrip"
              and w.printhead_tabs.currentIndex() == 2)
        c.chk("Edit restores well summary visibility",
              not w.platform_tab.assignment_summary_label.isHidden())

    popups = {"critical": [], "information": []}
    QMessageBox.critical = staticmethod(
        lambda *args, **_kwargs: popups["critical"].append(args[2]))
    QMessageBox.information = staticmethod(
        lambda *args, **_kwargs: popups["information"].append(args[2]))
    QMessageBox.question = staticmethod(
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    w._editing_protocol_name = "DiskFail"
    w.kayitli_protokoller["DiskFail"] = {
        "detay": "old", "degerler": deepcopy(w._collect_settings_data())}
    def boom(*args, **kwargs):
        raise OSError("disk full")
    w.dm.save_to_disk = boom
    w._save_protocol()
    c.chk("save_to_disk OSError shows error popup",
          len(popups["critical"]) == 1 and "disk full" in popups["critical"][0])
    c.chk("failed disk save shows no success popup",
          popups["information"] == [])
    c.chk("failed save remains an active edit",
          w._editing_protocol_name == "DiskFail")
    c.chk("failed save keeps explicit in-memory snapshot",
          "DiskFail" in w.kayitli_protokoller
          and w.kayitli_protokoller["DiskFail"]["degerler"] is not None)
    successful_writes = []
    w.dm.save_to_disk = lambda *args, **kwargs: successful_writes.append(args[0])
    w._save_protocol()
    c.chk("valid retry after disk failure succeeds",
          successful_writes == ["DiskFail"]
          and len(popups["information"]) == 1
          and "saved successfully" in popups["information"][0]
          and w._editing_protocol_name is None)

    w.close()
    c.report_and_exit("PROTOCOL / MULTI-PRINTHEAD STATE")


if __name__ == "__main__":
    run()
