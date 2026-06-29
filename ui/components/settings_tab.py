"""Settings tab: printhead selection, print parameters, temperatures, and the
Save / Slice action buttons (view only).

The build-platform info label (``bp_info_lbl``) is populated by the controller;
Save / Slice behavior is wired by the controller. The right-hand region is an
intentional transparent placeholder — the layer preview lives in PreviewTab.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QPushButton, QButtonGroup,
    QLabel, QFrame, QComboBox, QDoubleSpinBox, QProgressBar,
)
from PyQt6.QtCore import Qt

from ui.styles import (
    PH_BTN_STYLE, COMBOBOX_STYLE, CARD_STYLE, LABEL_STYLE, SPINBOX_STYLE,
)


class SettingsTab(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self._build(layout)

    def _build(self, layout: QVBoxLayout) -> None:
        main = QHBoxLayout()
        main.setSpacing(20)

        # ==================== SOL PANEL ====================
        sol = QVBoxLayout()
        sol.setSpacing(12)

        # --- Printhead Seçimi ---
        title = QLabel("Printheads")
        title.setStyleSheet("font-size:22px; font-weight:bold; color:#333333;")
        sol.addWidget(title)

        ph_row = QHBoxLayout()
        ph_row.setSpacing(10)
        self.ph1_btn = QPushButton("Printhead 1")
        self.ph2_btn = QPushButton("Printhead 2")
        self.ph3_btn = QPushButton("Printhead 3")
        self.ph_buton_grubu = QButtonGroup(self)
        self.ph_buton_grubu.setExclusive(True)

        for i, btn in enumerate((self.ph1_btn, self.ph2_btn, self.ph3_btn), 1):
            btn.setStyleSheet(PH_BTN_STYLE)
            btn.setCheckable(True)
            btn.setFixedHeight(45)
            self.ph_buton_grubu.addButton(btn, i)
            ph_row.addWidget(btn)

        self.ph1_btn.setChecked(True)
        ph_row.addStretch()
        sol.addLayout(ph_row)

        # --- Printhead Type (ComboBox) ---
        type_row = QHBoxLayout()
        type_row.setSpacing(15)

        type_lbl = QLabel("Printhead Type")
        type_lbl.setStyleSheet("font-size:16px; color:#555555;")
        type_lbl.setFixedWidth(190)
        type_row.addWidget(type_lbl)

        self.ph_type_combo = QComboBox()
        self.ph_type_combo.addItem("Temperature Control")
        self.ph_type_combo.setFixedWidth(180)
        self.ph_type_combo.setStyleSheet(COMBOBOX_STYLE)
        type_row.addWidget(self.ph_type_combo)
        type_row.addStretch()
        sol.addLayout(type_row)

        # --- Print Parameters Kartı ---
        g1 = QFrame()
        g1.setObjectName("KareMekan")
        g1.setStyleSheet(CARD_STYLE)
        f1 = QFormLayout(g1)
        f1.setVerticalSpacing(10)
        f1.setHorizontalSpacing(15)
        f1.setContentsMargins(15, 12, 15, 12)

        self.kutu_layer = self._create_spinbox(f1, "Layer Thickness", 0.05, 2.0, 2, 0.01, " mm", 0.2)
        self.kutu_speed = self._create_spinbox(f1, "Print Speed", 1, 60, 0, 1, " mm/s", 10)

        lbl_grid = QLabel("Grid Type")
        lbl_grid.setStyleSheet(LABEL_STYLE)
        lbl_grid.setFixedWidth(190)
        self.kutu_grid = QComboBox()
        self.kutu_grid.addItems(["Linear", "Gyroid", "Honeycomb", "Rectilinear"])
        self.kutu_grid.setCurrentText("Linear")
        self.kutu_grid.setFixedWidth(120)
        self.kutu_grid.setStyleSheet(COMBOBOX_STYLE)
        f1.addRow(lbl_grid, self.kutu_grid)

        self.kutu_distance = self._create_spinbox(f1, "Grid Distance", 0.01, 5.0, 2, 0.01, " mm", 0.2)
        sol.addWidget(g1)

        # --- Temperature Kartı ---
        g2 = QFrame()
        g2.setObjectName("KareMekan")
        g2.setStyleSheet(CARD_STYLE)
        f2 = QFormLayout(g2)
        f2.setVerticalSpacing(10)
        f2.setHorizontalSpacing(15)
        f2.setContentsMargins(15, 12, 15, 12)

        self.kutu_ph_temp = self._create_spinbox(f2, "Printhead Temperature", 4, 45, 0, 1, " °C", 27.0)
        self.kutu_plat_temp = self._create_spinbox(f2, "Platform Temperature", -30.0, 40, 0, 1, " °C", -30.0)
        sol.addWidget(g2)

        # --- Build Platform Info (Kompakt) ---
        g3 = QFrame()
        g3.setStyleSheet("""
            QFrame {
                background:#FFFFFF; border:1px solid #E0E0E0; border-radius:6px;
            }
        """)
        f3 = QHBoxLayout(g3)
        f3.setContentsMargins(12, 8, 12, 8)

        bp_title = QLabel("Build Platform:")
        bp_title.setStyleSheet("font-size:14px; color:#555; font-weight:bold;")
        f3.addWidget(bp_title)

        self.bp_info_lbl = QLabel("—")
        self.bp_info_lbl.setStyleSheet("font-size:14px; color:#1565C0; font-weight:bold;")
        f3.addWidget(self.bp_info_lbl)
        f3.addStretch()
        sol.addWidget(g3)

        # --- Save Protocol (sol, dar) + Slice (sağ, geniş/birincil) ---
        bottom_row = QHBoxLayout()
        bottom_row.addStretch()

        self.save_btn = QPushButton("Save Protocol")
        self.save_btn.setFixedHeight(45)
        self.save_btn.setStyleSheet("""
            QPushButton {
                font-size:16px; font-weight:bold; padding:8px 22px;
                background:#66BB6A; color:white; border-radius:5px;
            }
            QPushButton:hover  { background:#43A047; }
            QPushButton:pressed{ background:#388E3C; }
        """)

        self.slice_btn = QPushButton("Slice")
        self.slice_btn.setFixedHeight(45)
        self.slice_btn.setStyleSheet("""
            QPushButton {
                font-size:18px; font-weight:bold; padding:10px 55px;
                background:#1976D2; color:white; border-radius:5px;
            }
            QPushButton:hover  { background:#1565C0; }
            QPushButton:pressed{ background:#0D47A1; }
            QPushButton:disabled{ background:#90CAF9; color:#e0e0e0; }
        """)

        bottom_row.addWidget(self.save_btn)
        bottom_row.addSpacing(15)
        bottom_row.addWidget(self.slice_btn)
        bottom_row.addStretch()
        sol.addLayout(bottom_row)

        # --- Slice progress bar (hidden until a slice is running) ---
        self.slice_progress = QProgressBar()
        self.slice_progress.setRange(0, 100)
        self.slice_progress.setValue(0)
        self.slice_progress.setTextVisible(True)
        self.slice_progress.setFormat("Slicing… %p%")
        self.slice_progress.setFixedHeight(22)
        self.slice_progress.setVisible(False)
        self.slice_progress.setStyleSheet("""
            QProgressBar {
                font-size:13px; color:#212121;
                background:#ECEFF1; border:1px solid #CFD8DC;
                border-radius:5px; text-align:center;
            }
            QProgressBar::chunk { background:#1976D2; border-radius:4px; }
        """)
        sol.addSpacing(8)
        sol.addWidget(self.slice_progress)
        sol.addStretch()

        main.addLayout(sol, 1)

        right_placeholder = QFrame()
        right_placeholder.setMinimumWidth(300)
        right_placeholder.setStyleSheet("QFrame { background: transparent; border: none; }")
        main.addWidget(right_placeholder, 1)

        layout.addLayout(main)

    def _create_spinbox(
        self, form: QFormLayout, label: str,
        min_v: float, max_v: float, dec: int, step: float,
        suffix: str, default: float,
    ) -> QDoubleSpinBox:
        lbl = QLabel(label)
        lbl.setStyleSheet(LABEL_STYLE)
        lbl.setFixedWidth(190)

        sb = QDoubleSpinBox()
        sb.setRange(min_v, max_v)
        sb.setDecimals(dec)
        sb.setSingleStep(step)
        sb.setSuffix(suffix)
        sb.setValue(default)
        sb.setFixedWidth(120)
        sb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sb.setStyleSheet(SPINBOX_STYLE)
        form.addRow(lbl, sb)
        return sb
