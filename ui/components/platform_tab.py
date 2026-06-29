"""Built-platform tab: Petri / Well / Glass selection with a dynamic well grid.

Internal visual logic kept here:
  * switching the stacked sub-page when Petri/Well/Glass is toggled,
  * rebuilding the 6- vs 12-well grid when that format changes.

Cross-tab effects (e.g. updating the Settings build-platform info label, advancing
to the next page on Apply) are wired by the controller against the exposed widgets.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QStackedWidget,
    QPushButton, QButtonGroup, QLabel, QFrame, QLineEdit,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIntValidator

from ui.styles import BTN_STYLE_CHECKABLE


class PlatformTab(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self._build(layout)
        self._connect_internal()
        # İlk well grid'i oluştur (6-well varsayılan)
        self._update_well_grid()

    # ==========================================================
    # VIEW
    # ==========================================================
    def _build(self, layout: QVBoxLayout) -> None:
        main = QVBoxLayout()
        main.setSpacing(20)

        title = QLabel("Select Built Platform")
        title.setStyleSheet("font-size:26px; font-weight:bold; color:#333333;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main.addWidget(title)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self.btn_petri = QPushButton("Petri Dish")
        self.btn_well = QPushButton("Well Plate")
        self.btn_glass = QPushButton("Glass Slide")

        self.bp_buton_grubu = QButtonGroup(self)
        self.bp_buton_grubu.setExclusive(True)

        for i, btn in enumerate((self.btn_petri, self.btn_well, self.btn_glass)):
            btn.setStyleSheet(BTN_STYLE_CHECKABLE)
            btn.setCheckable(True)
            self.bp_buton_grubu.addButton(btn, i)
            btn_row.addWidget(btn)
            if btn is not self.btn_glass:
                btn_row.addSpacing(20)

        self.btn_petri.setChecked(True)
        btn_row.addStretch()
        main.addLayout(btn_row)
        main.addSpacing(30)

        self.bp_stacked = QStackedWidget()

        # Petri Dish
        p_petri = QWidget()
        lp = QVBoxLayout(p_petri)
        lp.setAlignment(Qt.AlignmentFlag.AlignCenter)

        petri_shape = QFrame()
        petri_shape.setFixedSize(200, 200)
        petri_shape.setStyleSheet("border:4px solid #64B5F6; border-radius:100px; background:transparent;")

        lbl_dia = QLabel("Diameter (mm)")
        lbl_dia.setStyleSheet("font-size:18px; color:#333333;")
        self.in_dia = QLineEdit("60")
        self.in_dia.setFixedWidth(120)
        self.in_dia.setStyleSheet("font-size:18px; padding:5px; background:white; color:black;")
        self.in_dia.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.in_dia.setValidator(QIntValidator(1, 500))

        lp.addWidget(petri_shape, alignment=Qt.AlignmentFlag.AlignCenter)
        lp.addSpacing(30)
        lp.addWidget(lbl_dia, alignment=Qt.AlignmentFlag.AlignCenter)
        lp.addSpacing(5)
        lp.addWidget(self.in_dia, alignment=Qt.AlignmentFlag.AlignCenter)
        self.bp_stacked.addWidget(p_petri)

        # Well Plate - Dinamik 6 ve 12 well
        p_well = QWidget()
        lw = QVBoxLayout(p_well)
        lw.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Well shape container - içinde grid dinamik değişecek
        self.well_shape_container = QFrame()
        self.well_shape_container.setStyleSheet("border:4px solid #64B5F6; border-radius:10px; background:transparent;")
        self.well_grid_layout = QGridLayout(self.well_shape_container)
        self.well_grid_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.well_grid_layout.setSpacing(10)

        lw.addWidget(self.well_shape_container, alignment=Qt.AlignmentFlag.AlignCenter)
        lw.addSpacing(30)

        lbl_wells = QLabel("Well Format")
        lbl_wells.setStyleSheet("font-size:18px; color:#333333;")
        lw.addWidget(lbl_wells, alignment=Qt.AlignmentFlag.AlignCenter)
        lw.addSpacing(5)

        well_btn_style = """
            QPushButton { font-size:18px; padding:6px 20px; background:#e0e0e0; color:black;
                          border-radius:4px; border:2px solid transparent; }
            QPushButton:hover { background:#333333; color:white; }
            QPushButton:checked { background:#64B5F6; color:black; border:2px solid #1E88E5; font-weight:bold; }
        """

        self.btn_6 = QPushButton("6")
        self.btn_6.setStyleSheet(well_btn_style)
        self.btn_6.setCheckable(True)

        self.btn_12 = QPushButton("12")
        self.btn_12.setStyleSheet(well_btn_style)
        self.btn_12.setCheckable(True)

        self.well_grup = QButtonGroup(p_well)
        self.well_grup.setExclusive(True)
        self.well_grup.addButton(self.btn_6)
        self.well_grup.addButton(self.btn_12)
        self.btn_6.setChecked(True)

        well_btn_row = QHBoxLayout()
        well_btn_row.addWidget(self.btn_6)
        well_btn_row.addSpacing(15)
        well_btn_row.addWidget(self.btn_12)
        lw.addLayout(well_btn_row)
        self.bp_stacked.addWidget(p_well)

        # Glass Slide
        p_glass = QWidget()
        lg = QVBoxLayout(p_glass)
        lg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lg.setSpacing(30)

        glass_shape = QFrame()
        glass_shape.setFixedSize(100, 220)
        glass_shape.setStyleSheet("border:4px solid #64B5F6; background:transparent;")
        lg.addWidget(glass_shape, alignment=Qt.AlignmentFlag.AlignCenter)

        lbl_size = QLabel("Size (mm)")
        lbl_size.setStyleSheet("font-size:18px; color:#333333;")

        self.in_size_x = QLineEdit("20")
        self.in_size_x.setFixedWidth(70)
        self.in_size_x.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.in_size_x.setStyleSheet("font-size:18px; padding:5px; background:white; color:black;")
        self.in_size_x.setValidator(QIntValidator(1, 999, self))

        self.in_size_y = QLineEdit("60")
        self.in_size_y.setFixedWidth(70)
        self.in_size_y.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.in_size_y.setStyleSheet("font-size:18px; padding:5px; background:white; color:black;")
        self.in_size_y.setValidator(QIntValidator(1, 999, self))

        size_x_lbl = QLabel("x")
        size_x_lbl.setStyleSheet("font-size:18px; color:#333333;")
        size_mm_lbl = QLabel("mm")
        size_mm_lbl.setStyleSheet("font-size:16px; color:#555555;")

        # Stretches on both ends keep the fixed-width inputs centered horizontally.
        size_row = QHBoxLayout()
        size_row.setSpacing(6)
        size_row.addStretch()
        size_row.addWidget(self.in_size_x)
        size_row.addWidget(size_x_lbl)
        size_row.addWidget(self.in_size_y)
        size_row.addWidget(size_mm_lbl)
        size_row.addStretch()

        # glass_shape (top) → lbl_size → size_row, all stacked & centered.
        lg.addWidget(lbl_size, alignment=Qt.AlignmentFlag.AlignCenter)
        lg.addLayout(size_row)

        self.bp_stacked.addWidget(p_glass)

        main.addWidget(self.bp_stacked)
        main.addStretch()
        layout.addLayout(main)

        # Apply & Continue butonu
        confirm_row = QHBoxLayout()
        confirm_row.addStretch()
        self.confirm_platform_btn = QPushButton("Apply & Continue ➔")
        self.confirm_platform_btn.setFixedHeight(48)
        self.confirm_platform_btn.setStyleSheet("""
            QPushButton {
                font-size:18px; font-weight:bold; padding:10px 45px;
                background:#1976D2; color:white; border-radius:6px;
            }
            QPushButton:hover  { background:#1565C0; }
            QPushButton:pressed{ background:#0D47A1; }
        """)
        confirm_row.addWidget(self.confirm_platform_btn)
        confirm_row.addStretch()
        layout.addLayout(confirm_row)
        layout.addSpacing(10)

    # ==========================================================
    # INTERNAL SIGNALS (intra-tab only)
    # ==========================================================
    def _connect_internal(self) -> None:
        # Petri/Well/Glass toggles its own stacked sub-page.
        self.bp_buton_grubu.idClicked.connect(self.bp_stacked.setCurrentIndex)
        # 6 / 12 rebuilds its own grid.
        self.btn_6.clicked.connect(self._update_well_grid)
        self.btn_12.clicked.connect(self._update_well_grid)

    # ==========================================================
    # INTERNAL VISUAL LOGIC
    # ==========================================================
    def _update_well_grid(self) -> None:
        """6 veya 12 well seçimine göre grid'i yeniden oluşturur."""
        if not hasattr(self, 'well_grid_layout'):
            return

        # Eski widget'ları temizle
        while self.well_grid_layout.count():
            item = self.well_grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        is_12 = self.btn_12 and self.btn_12.isChecked()

        header_style = "font-size:16px; color:#333333; font-weight:bold; border:none;"

        if is_12:
            # 12-well: 3 satır (A,B,C) x 4 sütun (1,2,3,4)
            rows = ['A', 'B', 'C']
            cols = 4
            circle_size = 35
        else:
            # 6-well: 2 satır (A,B) x 3 sütun (1,2,3)
            rows = ['A', 'B']
            cols = 3
            circle_size = 50

        # Sütun başlıkları (1, 2, 3, ...)
        for i in range(cols):
            n = QLabel(str(i + 1))
            n.setStyleSheet(header_style)
            self.well_grid_layout.addWidget(n, 0, i + 1, alignment=Qt.AlignmentFlag.AlignCenter)

        # Satırlar ve daireler
        for row_idx, row_label in enumerate(rows, start=1):
            h = QLabel(row_label)
            h.setStyleSheet(header_style)
            self.well_grid_layout.addWidget(h, row_idx, 0, alignment=Qt.AlignmentFlag.AlignCenter)

            for col in range(1, cols + 1):
                c = QFrame()
                c.setFixedSize(circle_size, circle_size)
                c.setStyleSheet("border:2px solid #64B5F6; border-radius:{}px;".format(circle_size // 2))
                self.well_grid_layout.addWidget(c, row_idx, col, alignment=Qt.AlignmentFlag.AlignCenter)

        # Container boyutunu ayarla
        total_width = cols * (circle_size + 10) + 40
        total_height = len(rows) * (circle_size + 10) + 40
        self.well_shape_container.setFixedSize(total_width, total_height)
