"""Real Qt offscreen containment checks for the 800x480 target."""
from _util import Checker
from PyQt6.QtCore import QPoint, QLocale, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel, QScrollBar
from ui import main_window as main_module
from ui.main_window import KlipperArayuzu

_app = QApplication.instance()


def run():
    c = Checker()
    # Prevent even the periodic status timer from being created in this UI-only test.
    main_module.requests = None
    w = KlipperArayuzu()
    w.resize(800, 480)
    w.show()
    _app.processEvents()

    def inside(widget):
        if widget is None or not widget.isVisibleTo(w):
            return False
        top_left = widget.mapTo(w, QPoint(0, 0))
        return (top_left.x() >= 0 and top_left.y() >= 0
                and top_left.x() + widget.width() <= w.width()
                and top_left.y() + widget.height() <= w.height())

    w._change_page(2)
    w.platform_tab.btn_well.click()
    _app.processEvents()
    c.chk("window exactly 800x480", w.size().width() == 800 and w.size().height() == 480)
    c.chk("platform type buttons visible", all(inside(x) for x in
          (w.btn_petri, w.btn_well, w.btn_glass)))
    c.chk("assignment segment visible", all(inside(x) for x in
          w.platform_tab.assignment_head_buttons.values()))
    c.chk("format buttons visible", inside(w.btn_6) and inside(w.btn_12))
    c.chk("well plate fully visible", inside(w.platform_tab.well_grid_frame))
    c.chk("all well hit buttons visible", all(inside(x) for x in
          w.platform_tab.well_buttons.values()))
    labels = [label for label in w.platform_tab.findChildren(QLabel)
              if label.text() in ("A", "B", "C", "1", "2", "3", "4")]
    c.chk("external row/column labels visible", len(labels) >= 5 and all(inside(x) for x in labels))
    c.chk("summary visible", inside(w.platform_tab.assignment_summary_label))
    c.chk("summary text not clipped",
          w.platform_tab.assignment_summary_label.fontMetrics().horizontalAdvance(
              w.platform_tab.assignment_summary_label.text())
          <= w.platform_tab.assignment_summary_label.contentsRect().width())
    c.chk("Apply Continue visible", inside(w.confirm_platform_btn))
    summary_top = w.platform_tab.assignment_summary_label.mapTo(
        w.platform_tab, QPoint(0, 0)).y()
    apply_bottom = (w.confirm_platform_btn.mapTo(
        w.platform_tab, QPoint(0, 0)).y() + w.confirm_platform_btn.height())
    c.chk("summary is below Apply Continue", summary_top >= apply_bottom,
          f"summary_top={summary_top}, apply_bottom={apply_bottom}")
    w.platform_tab.well_buttons["A1"].click()
    c.chk("assignment updates bottom summary",
          w.platform_tab.assignment_summary_label.text() ==
          "PH1: 1 | PH2: 0 | PH3: 0 | Empty: 5")
    w.platform_tab.btn_12.click()
    c.chk("format updates Empty count",
          w.platform_tab.assignment_summary_label.text().endswith("Empty: 11"))
    w.platform_tab.btn_petri.click(); _app.processEvents()
    c.chk("summary hidden for Petri",
          not w.platform_tab.assignment_summary_label.isVisibleTo(w))
    w.platform_tab.btn_glass.click(); _app.processEvents()
    c.chk("summary hidden for Glass",
          not w.platform_tab.assignment_summary_label.isVisibleTo(w))
    w.platform_tab.btn_well.click(); _app.processEvents()
    c.chk("summary visible again for Well Plate",
          inside(w.platform_tab.assignment_summary_label))
    c.chk("platform has no visible scrollbars",
          not any(bar.isVisibleTo(w) for bar in w.platform_tab.findChildren(QScrollBar)))

    w._change_page(4)
    _app.processEvents()
    c.chk("Settings QTabWidget visible", inside(w.printhead_tabs))
    bar = w.printhead_tabs.tabBar()
    c.chk("three tab headers fit", all(
        bar.tabRect(i).right() < bar.width() for i in range(bar.count())))
    c.chk("active profile fields visible", all(inside(widget) for widget in
          w.settings_tab.printhead_widgets[w.settings_tab.selected_printhead()].values()))
    nozzle = w.settings_tab.nozzle_diameter_spins[1]
    nozzle.setLocale(QLocale(QLocale.Language.Turkish, QLocale.Country.Turkey))
    nozzle.lineEdit().setFocus(); nozzle.lineEdit().selectAll()
    QTest.keyClicks(nozzle.lineEdit(), "0,25")
    QTest.keyClick(nozzle.lineEdit(), Qt.Key.Key_Return)
    _app.processEvents()
    c.chk("Turkish comma manual nozzle entry", abs(nozzle.value() - .25) < 1e-9)
    c.chk("global fields visible", all(inside(widget) for widget in
          (w.kutu_layer, w.kutu_grid, w.kutu_distance, w.kutu_plat_temp)))
    c.chk("Settings actions visible", all(inside(widget) for widget in
          (w.save_btn, w.slice_btn, w.exit_app_btn)))
    c.chk("Settings has no visible scrollbars",
          not any(bar.isVisibleTo(w) for bar in w.settings_tab.findChildren(QScrollBar)))
    c.chk("settings root stays in window", inside(w.settings_tab))
    w.close()
    c.report_and_exit("800x480 OFFSCREEN UI")


if __name__ == "__main__":
    run()
