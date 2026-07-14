"""Deterministic multi-head G-code and temperature-preflight tests."""
import inspect
import os
import tempfile

from _util import Checker, np, pv
from PyQt6.QtWidgets import QApplication
from core import gcode_exporter as GX
from core.printhead import PRINTHEAD_TO_HEATER, PRINTHEAD_TO_TOOL
from ui.main_window import KlipperArayuzu
from ui import main_window as main_module

_app = QApplication.instance()


def _line(y):
    return pv.lines_from_points(np.array([[-1.0, y, 0.0], [1.0, y, 0.0]]))


def run():
    c = Checker()
    c.chk("central tool map", PRINTHEAD_TO_TOOL == {1: "T0", 2: "T1", 3: "T2"})
    c.chk("central heater map", PRINTHEAD_TO_HEATER == {
        1: "peltier_1", 2: "peltier_2", 3: "peltier_3"})
    c.chk("multi-head API has no nozzle parameter",
          "nozzle" not in inspect.signature(GX.generate_gcode_multi_head).parameters)
    exporter_source = inspect.getsource(GX.generate_gcode_multi_head)
    c.chk("exporter reads central tool map",
          "PRINTHEAD_TO_TOOL[head]" in exporter_source)
    c.chk("exporter has no head-minus-one tool calculation",
          "T{head - 1}" not in exporter_source and "head - 1" not in exporter_source)

    slices = [_line(0.0), _line(0.2)]
    infills = [None, None]
    plan = {
        1: [("A1", 80.0, 80.0), ("A2", 120.0, 80.0)],
        2: [("B1", 80.0, 40.0)],
        3: [("C1", 160.0, 40.0)],
    }
    speeds = {1: 360.0, 2: 540.0, 3: 240.0}
    target = tempfile.NamedTemporaryFile(suffix=".gcode", delete=False)
    target.close()
    moves = GX.generate_gcode_multi_head(
        slices, infills, target.name, plan, speeds, layer_height=.2)
    text = open(target.name, encoding="utf-8").read()
    os.unlink(target.name)
    c.chk("PRINT_START once", text.count("PRINT_START") == 1)
    c.chk("PRINT_END once", text.count("PRINT_END") == 1)
    c.chk("single header", text.count("G90") == 1 and text.count("M83") == 1)
    expected_comments = [
        f"; LAYER {layer} PH{head} WELL {well}"
        for layer in (0, 1)
        for head, wells in ((1, ("A1", "A2")), (2, ("B1",)), (3, ("C1",)))
        for well in wells
    ]
    positions = [text.index(comment) for comment in expected_comments]
    c.chk("layer/head/well ordering", positions == sorted(positions))
    c.chk("layer 1 completes before layer 2", text.index("; LAYER 1") >
          text.rindex("; LAYER 0"))
    c.chk("T0/T1/T2 per layer", text.count("\nT0\n") == 2
          and text.count("\nT1\n") == 2 and text.count("\nT2\n") == 2)
    c.chk("no adjacent duplicate tools", "T0\nT0" not in text
          and "T1\nT1" not in text and "T2\nT2" not in text)
    c.chk("PH1 speed F360", "F360" in text[text.index("PH1 WELL A1"):text.index("PH2 WELL B1")])
    c.chk("PH2 speed F540", "F540" in text[text.index("PH2 WELL B1"):text.index("PH3 WELL C1")])
    c.chk("PH3 speed F240", "F240" in text[text.index("PH3 WELL C1"):text.index("; LAYER 1")])
    c.chk("all assigned wells printed each layer", moves == 8)
    c.chk("unassigned well absent", "WELL B2" not in text)

    one_head = tempfile.NamedTemporaryFile(suffix=".gcode", delete=False); one_head.close()
    GX.generate_gcode_multi_head(
        slices, infills, one_head.name, {2: [("B1", 80, 40)]}, {2: 540})
    one_text = open(one_head.name, encoding="utf-8").read(); os.unlink(one_head.name)
    c.chk("unused tools omitted", "\nT0\n" not in one_text and "\nT2\n" not in one_text)
    c.chk("used PH2 is T1", one_text.count("\nT1\n") == 1)
    try:
        invalid = tempfile.NamedTemporaryFile(suffix=".gcode", delete=False)
        invalid.close()
        GX.generate_gcode_multi_head(
            slices, infills, invalid.name, {4: [("A1", 80, 80)]}, {4: 360})
        invalid_rejected = False
    except ValueError as exc:
        invalid_rejected = "Gecersiz printhead ID" in str(exc)
    finally:
        try: os.unlink(invalid.name)
        except OSError: pass
    c.chk("invalid head raises controlled error", invalid_rejected)

    atomic = tempfile.NamedTemporaryFile(suffix=".gcode", delete=False); atomic.close()
    open(atomic.name, "w", encoding="utf-8").write("KEEP")
    try:
        GX.generate_gcode_multi_head(
            slices, infills, atomic.name, plan, speeds, abort_check=lambda: True)
    except RuntimeError:
        pass
    c.chk("abort preserves prior atomic target",
          open(atomic.name, encoding="utf-8").read() == "KEEP"
          and not os.path.exists(atomic.name + ".tmp"))
    os.unlink(atomic.name)

    w = KlipperArayuzu()
    w.platform_tab.btn_well.click()
    w.platform_tab.well_assignments.update({"A1": 1, "B1": 3})
    w.settings_tab.printhead_temperature_spins[1].setValue(25)
    w.settings_tab.printhead_temperature_spins[3].setValue(35)
    plan_temp = w._print_temperature_plan()
    c.chk("Well Plate uses assignment heads only",
          [head for head, _heater, _target in plan_temp] == [1, 3])
    delivered = []
    w._post_moonraker_blocking = lambda endpoint, payload: (
        delivered.append(payload["script"]) or (True, "ok"))
    c.chk("temperature preflight succeeds",
          w._deliver_temperature_preflight_blocking(plan_temp)[0])
    c.chk("only peltier 1/3 delivered", len(delivered) == 2
          and "peltier_1" in delivered[0] and "peltier_3" in delivered[1]
          and all("peltier_2" not in command for command in delivered))
    w.platform_tab.btn_petri.click(); w.printhead_tabs.setCurrentIndex(1)
    c.chk("Petri selected PH2 only", [x[0] for x in w._print_temperature_plan()] == [2])
    petri_export = w._current_export_params()
    c.chk("Petri export uses selected T1 and PH2 speed",
          petri_export["tool"] == "T1" and petri_export["speed"] == 10)
    w.platform_tab.btn_glass.click(); w.printhead_tabs.setCurrentIndex(2)
    c.chk("Glass selected PH3 only", [x[0] for x in w._print_temperature_plan()] == [3])
    glass_export = w._current_export_params()
    c.chk("Glass export uses selected T2 and PH3 speed",
          glass_export["tool"] == "T2" and glass_export["speed"] == 10)

    partial_commands = []
    def partial_post(_endpoint, payload):
        command = payload["script"]
        partial_commands.append(command)
        if "peltier_1" in command:
            return True, "ok"
        return False, "offline"
    w._post_moonraker_blocking = partial_post
    w._upload_gcode_to_moonraker = lambda _path: (True, "uploaded.gcode")
    original_sleep = main_module.time.sleep
    main_module.time.sleep = lambda _seconds: None
    try:
        preflight_ok, message = w._upload_and_preflight(
            "fake.gcode", (
                (1, "peltier_1", 25.0),
                (2, "peltier_2", 30.0),
                (3, "peltier_3", 35.0),
            ))
    finally:
        main_module.time.sleep = original_sleep
    c.chk("partial failure: PH1 succeeds then PH2 fails",
          not preflight_ok and partial_commands[0].endswith("TARGET=25.0")
          and sum("peltier_2" in command for command in partial_commands) == 3)
    c.chk("partial failure: PH3 is not sent",
          all("peltier_3" not in command for command in partial_commands))
    c.chk("delivered PH1 and unknown state are explicit",
          "PH2" in message and "PH1 (peltier_1)" in message
          and "UNKNOWN" in message and "manual safety decision" in message)
    c.chk("unverified TARGET=0 rollback is not guessed",
          all("TARGET=0" not in command for command in partial_commands))

    starts = []
    w._start_uploaded_gcode = lambda filename: starts.append(filename)
    w._set_print_btn_states = lambda *a, **k: None
    w._show_banner = lambda *a, **k: None
    w._on_gcode_upload_finished(False, "", message)
    c.chk("failed target blocks print start", starts == [])
    w._on_gcode_upload_finished(True, "ok.gcode", "ok")
    c.chk("successful preflight allows print start", starts == ["ok.gcode"])
    w.close()
    c.report_and_exit("MULTI-PRINTHEAD GCODE / PREFLIGHT")


if __name__ == "__main__":
    run()
