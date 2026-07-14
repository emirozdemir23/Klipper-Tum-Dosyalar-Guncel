"""Compact Settings view with three independent printhead profile tabs."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.printhead import (
    NOZZLE_DIAMETER_DEFAULT,
    NOZZLE_DIAMETER_MAX,
    NOZZLE_DIAMETER_MIN,
    PRINTHEAD_IDS,
    PRINTHEAD_TEMPERATURE_DEFAULT,
    PRINTHEAD_TEMPERATURE_MAX,
    PRINTHEAD_TEMPERATURE_MIN,
    PRINT_SPEED_DEFAULT,
    PRINT_SPEED_MAX,
    PRINT_SPEED_MIN,
    normalize_printhead_profiles,
    normalize_selected_printhead,
)
from ui.styles import CARD_STYLE, COMBOBOX_STYLE, LABEL_STYLE, SPINBOX_STYLE


class SettingsTab(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(5, 4, 5, 4)
        root.setSpacing(4)
        self._build(root)

    def _build(self, layout: QVBoxLayout) -> None:
        type_row = QHBoxLayout()
        type_row.setSpacing(8)
        type_label = QLabel("Printhead Type")
        type_label.setStyleSheet("font-size:14px; color:#555555; font-weight:bold;")
        self.ph_type_combo = QComboBox()
        self.ph_type_combo.addItem("Temperature Control")
        self.ph_type_combo.setFixedWidth(175)
        self.ph_type_combo.setFixedHeight(28)
        self.ph_type_combo.setStyleSheet(COMBOBOX_STYLE)
        type_row.addWidget(type_label)
        type_row.addWidget(self.ph_type_combo)
        type_row.addStretch()
        layout.addLayout(type_row)

        self.printhead_tabs = QTabWidget()
        self.printhead_tabs.setObjectName("printhead_tabs")
        self.printhead_tabs.setFixedHeight(146)
        self.printhead_tabs.setStyleSheet("""
            QTabWidget::pane { border:1px solid #D6E2EA; border-radius:5px; background:#FFFFFF; }
            QTabBar::tab {
                min-width:112px; min-height:27px; padding:2px 10px;
                font-size:14px; font-weight:bold; color:#333333;
                background:#EAF3FA; border:1px solid #C5D9E8;
            }
            QTabBar::tab:selected { background:#64B5F6; color:#102A43; }
        """)
        self.printhead_widgets: dict[int, dict[str, QDoubleSpinBox]] = {}
        self.nozzle_diameter_spins: dict[int, QDoubleSpinBox] = {}
        self.print_speed_spins: dict[int, QDoubleSpinBox] = {}
        self.printhead_temperature_spins: dict[int, QDoubleSpinBox] = {}

        for head in PRINTHEAD_IDS:
            page = QWidget()
            form = QFormLayout(page)
            form.setContentsMargins(12, 7, 12, 7)
            form.setVerticalSpacing(4)
            form.setHorizontalSpacing(12)
            nozzle = self._create_spinbox(
                form, "Nozzle Diameter",
                NOZZLE_DIAMETER_MIN, NOZZLE_DIAMETER_MAX, 2, 0.01, " mm",
                NOZZLE_DIAMETER_DEFAULT,
            )
            nozzle.setKeyboardTracking(True)
            speed = self._create_spinbox(
                form, "Print Speed",
                PRINT_SPEED_MIN, PRINT_SPEED_MAX, 0, 1, " mm/s",
                PRINT_SPEED_DEFAULT,
            )
            temperature = self._create_spinbox(
                form, "Printhead Temperature",
                PRINTHEAD_TEMPERATURE_MIN, PRINTHEAD_TEMPERATURE_MAX,
                0, 1, " °C", PRINTHEAD_TEMPERATURE_DEFAULT,
            )
            temperature.setKeyboardTracking(False)
            widgets = {
                "nozzle_diameter_mm": nozzle,
                "print_speed_mm_s": speed,
                "temperature_c": temperature,
            }
            self.printhead_widgets[head] = widgets
            self.nozzle_diameter_spins[head] = nozzle
            self.print_speed_spins[head] = speed
            self.printhead_temperature_spins[head] = temperature
            setattr(self, f"ph{head}_nozzle_diameter_spin", nozzle)
            setattr(self, f"ph{head}_print_speed_spin", speed)
            setattr(self, f"ph{head}_temperature_spin", temperature)
            self.printhead_tabs.addTab(page, f"Printhead {head}")
        layout.addWidget(self.printhead_tabs)

        globals_frame = QFrame()
        globals_frame.setObjectName("KareMekan")
        globals_frame.setStyleSheet(CARD_STYLE)
        globals_grid = QGridLayout(globals_frame)
        globals_grid.setContentsMargins(10, 6, 10, 6)
        globals_grid.setHorizontalSpacing(10)
        globals_grid.setVerticalSpacing(4)

        self.kutu_layer = self._create_grid_spinbox(
            globals_grid, 0, 0, "Layer Thickness", 0.05, 2.0, 2, 0.01, " mm", 0.2)
        self.kutu_grid = QComboBox()
        self.kutu_grid.addItem("Linear")
        self.kutu_grid.setCurrentText("Linear")
        self.kutu_grid.setFixedWidth(105)
        self.kutu_grid.setFixedHeight(27)
        self.kutu_grid.setStyleSheet(COMBOBOX_STYLE)
        self._add_grid_field(globals_grid, 0, 1, "Grid Type", self.kutu_grid)
        self.kutu_distance = self._create_grid_spinbox(
            globals_grid, 1, 0, "Grid Distance", 0.01, 5.0, 2, 0.01, " mm", 0.2)
        self.kutu_plat_temp = self._create_grid_spinbox(
            globals_grid, 1, 1, "Platform Temperature", -30.0, 40, 0, 1, " °C", -30.0)
        self.kutu_plat_temp.setKeyboardTracking(False)
        layout.addWidget(globals_frame)

        info_frame = QFrame()
        info_frame.setStyleSheet(
            "QFrame { background:#FFFFFF; border:1px solid #E0E0E0; border-radius:5px; }")
        info_row = QHBoxLayout(info_frame)
        info_row.setContentsMargins(8, 4, 8, 4)
        info_title = QLabel("Build Platform:")
        info_title.setStyleSheet("font-size:13px; color:#555; font-weight:bold;")
        self.bp_info_lbl = QLabel("—")
        self.bp_info_lbl.setStyleSheet("font-size:13px; color:#1565C0; font-weight:bold;")
        info_row.addWidget(info_title)
        info_row.addWidget(self.bp_info_lbl)
        info_row.addStretch()
        layout.addWidget(info_frame)

        self.slice_progress = QProgressBar()
        self.slice_progress.setRange(0, 100)
        self.slice_progress.setValue(0)
        self.slice_progress.setFormat("Slicing… %p%")
        self.slice_progress.setFixedHeight(18)
        self.slice_progress.setVisible(False)
        layout.addWidget(self.slice_progress)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.exit_app_btn = QPushButton("Exit Application")
        self.save_btn = QPushButton("Save Protocol")
        self.slice_btn = QPushButton("Slice")
        for button in (self.exit_app_btn, self.save_btn, self.slice_btn):
            button.setFixedHeight(36)
            actions.addWidget(button)
        self.exit_app_btn.setStyleSheet(self._action_style("#D32F2F", 14))
        self.save_btn.setStyleSheet(self._action_style("#43A047", 14))
        self.slice_btn.setStyleSheet(self._action_style("#1976D2", 15))
        layout.addLayout(actions)
        layout.addStretch(1)

    @staticmethod
    def _action_style(color: str, font_size: int) -> str:
        return (
            "QPushButton {"
            f"font-size:{font_size}px; font-weight:bold; background:{color}; "
            "color:white; border:none; border-radius:5px; padding:3px 9px;"
            "} QPushButton:disabled { background:#B0BEC5; }"
        )

    def _create_spinbox(self, form: QFormLayout, label: str,
                        min_v: float, max_v: float, decimals: int, step: float,
                        suffix: str, default: float) -> QDoubleSpinBox:
        label_widget = QLabel(label)
        label_widget.setStyleSheet(LABEL_STYLE)
        label_widget.setFixedWidth(175)
        spin = self._configured_spinbox(
            min_v, max_v, decimals, step, suffix, default)
        form.addRow(label_widget, spin)
        return spin

    def _create_grid_spinbox(self, grid: QGridLayout, row: int, column: int,
                             label: str, min_v: float, max_v: float,
                             decimals: int, step: float, suffix: str,
                             default: float) -> QDoubleSpinBox:
        spin = self._configured_spinbox(
            min_v, max_v, decimals, step, suffix, default)
        self._add_grid_field(grid, row, column, label, spin)
        return spin

    @staticmethod
    def _add_grid_field(grid: QGridLayout, row: int, column: int,
                        label: str, widget: QWidget) -> None:
        cell = QHBoxLayout()
        cell.setSpacing(5)
        label_widget = QLabel(label)
        label_widget.setStyleSheet("font-size:13px; color:#555555;")
        label_widget.setMinimumWidth(112)
        cell.addWidget(label_widget)
        cell.addWidget(widget)
        cell.addStretch()
        grid.addLayout(cell, row, column)

    @staticmethod
    def _configured_spinbox(min_v: float, max_v: float, decimals: int,
                            step: float, suffix: str,
                            default: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(min_v, max_v)
        spin.setDecimals(decimals)
        spin.setSingleStep(step)
        spin.setSuffix(suffix)
        spin.setValue(default)
        spin.setFixedWidth(105)
        spin.setFixedHeight(27)
        spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        spin.setStyleSheet(SPINBOX_STYLE)
        return spin

    def selected_printhead(self) -> int:
        return normalize_selected_printhead(self.printhead_tabs.currentIndex() + 1)

    def collect_printhead_profiles(self) -> dict[int, dict[str, float]]:
        return {
            head: {
                key: float(widget.value())
                for key, widget in self.printhead_widgets[head].items()
            }
            for head in PRINTHEAD_IDS
        }

    def load_printhead_profiles(self, profiles: object,
                                selected_printhead: object) -> None:
        """Set every profile and active tab with all relevant signals blocked."""
        normalized = normalize_printhead_profiles(profiles)
        blockers: list[tuple[QWidget, bool]] = []
        widgets = [self.printhead_tabs]
        widgets.extend(
            widget for head in PRINTHEAD_IDS
            for widget in self.printhead_widgets[head].values()
        )
        try:
            for widget in widgets:
                blockers.append((widget, widget.blockSignals(True)))
            for head in PRINTHEAD_IDS:
                for key, widget in self.printhead_widgets[head].items():
                    widget.setValue(normalized[head][key])
            selected = normalize_selected_printhead(selected_printhead)
            self.printhead_tabs.setCurrentIndex(selected - 1)
        finally:
            for widget, was_blocked in reversed(blockers):
                widget.blockSignals(was_blocked)
