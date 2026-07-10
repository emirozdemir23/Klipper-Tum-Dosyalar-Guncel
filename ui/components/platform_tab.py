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
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIntValidator

from ui.styles import BTN_STYLE_CHECKABLE


class PlatformTab(QWidget):
    # Platform tipi / well formati / kuyu secimi degisince yayilan TEK sinyal.
    # Payload ornegi: {"type":"well_plate","well_format":6,"selected_wells":["A1",...]}
    platform_changed = pyqtSignal(dict)

    @staticmethod
    def _well_btn_style(sz: int) -> str:
        """DAIRESEL kuyu butonu QSS: width==height==sz, border-radius=sz/2 -> tam cember.

        Gercek laboratuvar well-plate hissi: secili = dolu acik mavi, bos = beyaz +
        mavi cerceve. :checked pseudo-state programatik setChecked'te de uygulanir.
        border-radius kuyu capina (sz) bagli oldugundan buton-basi uretilir.
        """
        r = sz // 2
        fs = 15 if sz >= 50 else 12
        return (
            f"QPushButton {{ background:#FFFFFF; color:#1565C0; border:2px solid #1E88E5;"
            f"              border-radius:{r}px; font-size:{fs}px; font-weight:bold; }}"
            f"QPushButton:hover   {{ background:#E3F2FD; }}"
            f"QPushButton:checked {{ background:#4FC3F7; color:#FFFFFF; border:2px solid #0277BD; }}"
        )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Merkezi kuyu-secim state'i (G-code'a kadar tasinan tek dogru kaynak).
        self.selected_wells: set[str] = set()
        self.well_buttons: dict[str, QPushButton] = {}
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

        # Well shape container - içinde grid dinamik değişecek.
        # Acik gri "plaka" zemin + yuvarlak kose: dairesel kuyular bir well-plate
        # yuzeyine oturmus gibi gorunsun (QFrame'e scope'lu -> cocuklara sizmaz).
        self.well_shape_container = QFrame()
        self.well_shape_container.setStyleSheet(
            "QFrame { border:3px solid #90CAF9; border-radius:16px; background:#EEF5FC; }")
        self.well_grid_layout = QGridLayout(self.well_shape_container)
        self.well_grid_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.well_grid_layout.setContentsMargins(18, 18, 18, 18)
        self.well_grid_layout.setSpacing(14)

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

        # NOT: "Selected wells: ..." bilgi etiketi UI'dan KALDIRILDI (kullanici
        # istegi). selected_wells state'i arka planda AYNEN yasar (preview / export
        # / print bu state'i kullanmaya devam eder); yalnizca gorsel etiket yok.
        # _update_selected_wells_label() bu yuzden getattr ile guvenli no-op kalir.

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
        # Platform tipi degisimi: Well disina cikilirsa secim temizlenir + sinyal.
        self.bp_buton_grubu.idClicked.connect(self._on_type_clicked)
        # 6 / 12: toggled -> grid'i yeniden kur (programatik setChecked'te de calisir,
        # ornegin protokol yuklemede). clicked -> yalnizca kullanici; sinyali yayar.
        self.btn_6.toggled.connect(lambda c: self._on_format_toggled(6, c))
        self.btn_12.toggled.connect(lambda c: self._on_format_toggled(12, c))
        self.btn_6.clicked.connect(self._emit_platform_changed)
        self.btn_12.clicked.connect(self._emit_platform_changed)
        # Petri/Glass olcu girisi TAMAMLANINCA (parca-girise degil) sinyal yayilir.
        self.in_dia.editingFinished.connect(self._emit_platform_changed)
        self.in_size_x.editingFinished.connect(self._emit_platform_changed)
        self.in_size_y.editingFinished.connect(self._emit_platform_changed)

    # ==========================================================
    # INTERNAL VISUAL LOGIC
    # ==========================================================
    def _update_well_grid(self) -> None:
        """6/12 secimine gore kuyu grid'ini CHECKABLE butonlarla yeniden kurar.

        Format degisince eski secimler temizlenir (self.selected_wells + butonlar).
        Butonlar :checked pseudo-state ile otomatik renklenir (dolu mavi / bos beyaz).
        """
        if not hasattr(self, 'well_grid_layout'):
            return

        # Eski widget'lari temizle
        while self.well_grid_layout.count():
            item = self.well_grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Format degisti -> eski secimler gecersiz; state'i sifirla.
        self.selected_wells.clear()
        self.well_buttons = {}

        is_12 = bool(self.btn_12 and self.btn_12.isChecked())
        header_style = "font-size:16px; color:#333333; font-weight:bold; border:none;"

        if is_12:
            # 12-well: 3 satir (A,B,C) x 4 sutun (1..4)
            rows, cols, sz = ['A', 'B', 'C'], 4, 40
        else:
            # 6-well: 2 satir (A,B) x 3 sutun (1..3)
            rows, cols, sz = ['A', 'B'], 3, 55

        # Sutun basliklari (1, 2, 3, ...)
        for i in range(cols):
            n = QLabel(str(i + 1))
            n.setStyleSheet(header_style)
            self.well_grid_layout.addWidget(n, 0, i + 1, alignment=Qt.AlignmentFlag.AlignCenter)

        # Satir basligi + her kuyu icin checkable buton (uzerinde kuyu adi yazar).
        for row_idx, row_label in enumerate(rows, start=1):
            h = QLabel(row_label)
            h.setStyleSheet(header_style)
            self.well_grid_layout.addWidget(h, row_idx, 0, alignment=Qt.AlignmentFlag.AlignCenter)
            for col in range(1, cols + 1):
                wid = f"{row_label}{col}"          # A1, A2, ... B1 ...
                btn = QPushButton(wid)
                btn.setCheckable(True)
                btn.setFixedSize(sz, sz)
                btn.setStyleSheet(self._well_btn_style(sz))
                btn.toggled.connect(
                    lambda checked, w=wid: self._on_well_button_toggled(w, checked))
                self.well_grid_layout.addWidget(btn, row_idx, col,
                                                alignment=Qt.AlignmentFlag.AlignCenter)
                self.well_buttons[wid] = btn

        # Container (plaka) boyutu: kenar bosluklari (mrg) + satir/sutun basligi
        # payi (hdr) + dairesel kuyular (sz) + aralarindaki grid bosluklari (spc).
        # Bu degerler yukaridaki setContentsMargins(18)/setSpacing(14) ile eslesir;
        # hafif bollugu AlignCenter merkezler, cemberler kirpilmaz.
        mrg, spc, hdr = 18, 14, 24
        total_width = 2 * mrg + hdr + cols * sz + cols * spc
        total_height = 2 * mrg + hdr + len(rows) * sz + len(rows) * spc
        self.well_shape_container.setFixedSize(total_width, total_height)
        self._update_selected_wells_label()

    # ==========================================================
    # WELL SELECTION — state + signal
    # ==========================================================
    @staticmethod
    def _float_or(txt, default: float) -> float:
        try:
            return float(str(txt).strip())
        except (ValueError, TypeError):
            return default

    def _sorted_wells(self) -> list:
        """A1,A2,...,B1 dogal siralamasi (once harf satiri, sonra sayi sutunu)."""
        def _key(w: str):
            return (w[0], int(w[1:])) if len(w) >= 2 and w[1:].isdigit() else (w, 0)
        return sorted(self.selected_wells, key=_key)

    def _update_selected_wells_label(self) -> None:
        lbl = getattr(self, 'selected_wells_lbl', None)
        if lbl is None:
            return
        wells = self._sorted_wells()
        lbl.setText("Selected wells: " + (", ".join(wells) if wells else "None"))

    def _on_well_button_toggled(self, well_id: str, checked: bool) -> None:
        """Kullanici bir kuyu butonuna tikladi (programatik degisiklikler blocked)."""
        if checked:
            self.selected_wells.add(well_id)
        else:
            self.selected_wells.discard(well_id)
        self._update_selected_wells_label()
        self._emit_platform_changed()

    def _on_format_toggled(self, fmt: int, checked: bool) -> None:
        # toggled her iki butonda da atesler; grid'i YALNIZCA aktif olanda kur.
        if checked:
            self._update_well_grid()

    def _on_type_clicked(self, kind_id: int) -> None:
        # Well Plate disina (Petri/Glass) gecilirse kuyu secimleri temizlenir.
        if kind_id != 1 and self.selected_wells:
            self.selected_wells.clear()
            for btn in self.well_buttons.values():
                if btn.isChecked():
                    btn.blockSignals(True)
                    btn.setChecked(False)
                    btn.blockSignals(False)
            self._update_selected_wells_label()
        self._emit_platform_changed()

    def _emit_platform_changed(self) -> None:
        self.platform_changed.emit(self.get_platform_config())

    # ==========================================================
    # PUBLIC API (controller / protokol yukleme / 3D viewport picking)
    # ==========================================================
    def get_platform_config(self) -> dict:
        """Mevcut platform secimini normalize edilmis dict olarak dondurur."""
        kind_id = self.bp_buton_grubu.checkedId() if self.bp_buton_grubu else 0
        if kind_id == 1:            # Well Plate
            fmt = 12 if (self.btn_12 and self.btn_12.isChecked()) else 6
            return {"type": "well_plate", "well_format": fmt,
                    "selected_wells": self._sorted_wells()}
        if kind_id == 2:            # Glass Slide
            return {"type": "glass",
                    "size_x": self._float_or(self.in_size_x.text(), 20.0),
                    "size_y": self._float_or(self.in_size_y.text(), 60.0),
                    "selected_wells": []}
        return {"type": "petri",    # Petri Dish (varsayilan)
                "diameter": self._float_or(self.in_dia.text(), 60.0),
                "selected_wells": []}

    def set_selected_wells(self, wells, emit_signal: bool = True) -> None:
        """Verilen kuyulari sec (gecersizleri yok sayar). Butonlar SESSIZCE guncellenir.

        emit_signal=False: 3D viewport picking ve protokol yuklemesi bu yolu kullanir
        (butonlari senkronlar ama geri-sinyal DONGUSU olusturmaz).
        """
        valid = set(self.well_buttons.keys())
        new_sel = {w for w in (wells or []) if w in valid}
        self.selected_wells = new_sel
        for wid, btn in self.well_buttons.items():
            want = wid in new_sel
            if btn.isChecked() != want:
                btn.blockSignals(True)
                btn.setChecked(want)
                btn.blockSignals(False)
        self._update_selected_wells_label()
        if emit_signal:
            self._emit_platform_changed()

    def clear_selected_wells(self, emit_signal: bool = True) -> None:
        """Tum kuyu secimlerini temizler (butonlar sessizce cozulur)."""
        self.selected_wells.clear()
        for btn in self.well_buttons.values():
            if btn.isChecked():
                btn.blockSignals(True)
                btn.setChecked(False)
                btn.blockSignals(False)
        self._update_selected_wells_label()
        if emit_signal:
            self._emit_platform_changed()
