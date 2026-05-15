from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Optional, Tuple

from PyQt6.QtWidgets import (
    QApplication, QWidget, QHBoxLayout, QVBoxLayout,
    QPushButton, QStackedWidget, QLabel, QFrame, QButtonGroup,
    QSpinBox, QListWidget, QListWidgetItem, QTextEdit, QFormLayout, QLineEdit,
    QGridLayout, QDoubleSpinBox, QComboBox, QFileDialog, QInputDialog, QMessageBox,QSlider,
)
from PyQt6.QtCore import Qt, QTimer

try:
    import pyvista as pv
    from pyvistaqt import QtInteractor
except ImportError:
    pv = None
    QtInteractor = None
    print("Warning: pyvista or pyvistaqt not installed. STL viewing disabled.")

try:
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure
    import shapely.geometry as sg
    import numpy as np
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False
    print("Warning: matplotlib veya shapely eksik.")

class KlipperArayuzu(QWidget):
    # ==========================================================
    # STYLE CONSTANTS
    # ==========================================================
    BTN_STYLE: str = """
        QPushButton {
            font-size: 16px; padding: 10px; background-color: #e0e0e0; color: black;
            border-radius: 5px; min-width: 100px;
        }
        QPushButton:hover { background-color: #333333; color: white; }
    """

    BTN_STYLE_CHECKABLE: str = """
        QPushButton {
            font-size: 18px; padding: 12px 35px; background-color: #e0e0e0; color: black;
            border: 2px solid transparent; border-radius: 5px;
        }
        QPushButton:hover { background-color: #333333; color: white; }
        QPushButton:checked {
            background-color: #64B5F6; color: black;
            border: 2px solid #1E88E5; font-weight: bold;
        }
    """

    PH_BTN_STYLE: str = """
        QPushButton {
            font-size: 16px; padding: 8px 20px; background-color: #e0e0e0; color: black;
            border-radius: 4px; border: 2px solid transparent;
        }
        QPushButton:hover { background-color: #bbbbbb; }
        QPushButton:checked {
            background-color: #1E88E5; color: white;
            border: 2px solid #1565C0; font-weight: bold;
        }
    """

    SPINBOX_STYLE: str = """
        QDoubleSpinBox, QSpinBox {
            font-size: 16px; padding: 5px; background-color: white; color: black;
            border: 1px solid #cccccc; border-radius: 3px;
        }
        QDoubleSpinBox::up-button, QSpinBox::up-button { width: 20px; }
        QDoubleSpinBox::down-button, QSpinBox::down-button { width: 20px; }
    """

    COMBOBOX_STYLE: str = """
        QComboBox {
            font-size: 16px; padding: 5px; background-color: white; color: black;
            border: 1px solid #cccccc; border-radius: 3px; min-width: 100px;
        }
        QComboBox::drop-down { border: none; width: 30px; }
        QComboBox QAbstractItemView { background-color: white; color: black; }
    """

    LABEL_STYLE: str = "font-size: 16px; color: #333333; background: transparent; border: none;"

    CARD_STYLE: str = """
        QFrame#KareMekan {
            background-color: #f5f5f5;
            border: 1px solid #cccccc;
            border-radius: 8px;
        }
    """

    LIST_STYLE: str = """
        QListWidget {
            font-size: 16px; background-color: white; color: black;
            border: 1px solid #cccccc;
        }
        QListWidget::item { padding: 10px; border-bottom: 1px solid #eee; }
        QListWidget::item:selected { background-color: #64B5F6; color: black; }
    """

    TEXTAREA_STYLE: str = """
        QTextEdit {
            font-size: 18px; background-color: #f9f9f9; color: #333333;
            border: 1px solid #cccccc; padding: 15px;
        }
    """

    # ==========================================================
    # INITIALIZATION
    # ==========================================================
    def __init__(self) -> None:
        super().__init__()
        self._init_widgets()       # ← _load_protocols YOK
        self._setup_window()
        self._create_main_layout()
        self._create_side_menu()
        self._create_pages()        # ← protokol_listesi burada oluşur
        self._load_protocols()      # ← SADECE BURADA
        self._connect_signals()     # ← sinyalleri bağla
        self._update_platform_info()


    def _init_widgets(self) -> None:
        """Initialize all widget references to None."""
        self.ana_duzen: Optional[QHBoxLayout] = None
        self.sol_menu_duzeni: Optional[QVBoxLayout] = None
        self.buton_grubu: Optional[QButtonGroup] = None
        self.sayfalar_alani: Optional[QStackedWidget] = None

        # Sterilization
        self.uv_zaman_kutusu: Optional[QSpinBox] = None
        self.uv_start_btn: Optional[QPushButton] = None
        self.uv_stop_btn: Optional[QPushButton] = None
        self.uv_kalan_sure_lbl: Optional[QLabel] = None
        self.hepa_zaman_kutusu: Optional[QSpinBox] = None
        self.hepa_start_btn: Optional[QPushButton] = None
        self.hepa_stop_btn: Optional[QPushButton] = None
        self.hepa_kalan_sure_lbl: Optional[QLabel] = None

        # Protocol
        self.protokol_listesi: Optional[QListWidget] = None
        self.open_btn: Optional[QPushButton] = None
        self.edit_btn: Optional[QPushButton] = None
        self.delete_btn: Optional[QPushButton] = None
        self.protokol_detay_alani: Optional[QTextEdit] = None

        # Built Platform
        self.btn_petri: Optional[QPushButton] = None
        self.btn_well: Optional[QPushButton] = None
        self.btn_glass: Optional[QPushButton] = None
        self.bp_buton_grubu: Optional[QButtonGroup] = None
        self.bp_stacked: Optional[QStackedWidget] = None
        self.in_dia: Optional[QLineEdit] = None
        self.btn_6: Optional[QPushButton] = None
        self.btn_12: Optional[QPushButton] = None
        self.well_grup: Optional[QButtonGroup] = None
        self.in_size: Optional[QLineEdit] = None

        # Model
        self.open_stl_btn: Optional[QPushButton] = None
        self.uc_boyutlu_alan: Optional[QFrame] = None

        # Settings
        self.ph_buton_grubu: Optional[QButtonGroup] = None
        self.ph1_btn: Optional[QPushButton] = None
        self.ph2_btn: Optional[QPushButton] = None
        self.ph3_btn: Optional[QPushButton] = None
        self.ph_type_combo: Optional[QComboBox] = None
        self.kutu_layer: Optional[QDoubleSpinBox] = None
        self.kutu_speed: Optional[QDoubleSpinBox] = None
        self.kutu_grid: Optional[QComboBox] = None
        self.kutu_distance: Optional[QDoubleSpinBox] = None
        self.kutu_ph_temp: Optional[QDoubleSpinBox] = None
        self.kutu_plat_temp: Optional[QDoubleSpinBox] = None
        self.slice_btn: Optional[QPushButton] = None
        self.save_btn: Optional[QPushButton] = None
        self.bp_info_lbl: Optional[QLabel] = None

        # Print
        self.print_btn: Optional[QPushButton] = None
        self.pause_btn: Optional[QPushButton] = None
        self.stop_btn: Optional[QPushButton] = None
        self.elapsed_deger: Optional[QLabel] = None
        self.remaining_deger: Optional[QLabel] = None

        # Timers
        self.uv_timer: Optional[QTimer] = None
        self.uv_kalan_saniye: int = 0
        self.hepa_timer: Optional[QTimer] = None
        self.hepa_kalan_saniye: int = 0

        # 3D Model
        self.stl_dosya_yolu: Optional[str] = None
        self.plotter = None

        # Protocol storage: each entry is an isolated snapshot.
        # Structure: {name: {"detay": str, "degerler": dict|None}}
        self.kayitli_protokoller: dict[str, dict] = {}

        # --- JSON Depolama ---
        # main.py'nin bulunduğu klasörden data/protocols/ yolunu oluştur
        self._protocols_dir = Path(__file__).resolve().parent / "data" / "protocols"
        self._protocols_dir.mkdir(parents=True, exist_ok=True)

        # Settings - Layer preview
        self.layer_preview_stack: Optional[QStackedWidget] = None
        self.layer_preview_label: Optional[QLabel] = None

        # Tracks which protocol is currently being edited so Save can update it directly.
        self._editing_protocol_name: Optional[str] = None

        # Slice sonuçları
        self._slices: list = []           # Her katmanın pyvista slice objesi
        self._current_layer_idx: int = 0  # Şu an gösterilen katman numarası
        self._layer_meshes: list = []

        # Layer preview kontrolleri (Settings sağ paneli)
        self.layer_slider: Optional[QSlider] = None   # QSlider
        self.layer_nav_label: Optional[QLabel] = None # "Katman 3 / 45"
        self.layer_frame: Optional[QFrame] = None     # Plotter'ı koyacağın frame

        self._slice_mode: bool = False

        # Settings - Layer preview (sağ paneldeki plotter)
        self.layer_plotter = None           # QtInteractor nesnesi (Settings sekmesine ait)
        self.layer_plotter_frame = None     # İçine yerleştirileceği QFrame

    # ==========================================================
    # WINDOW & LAYOUT
    # ==========================================================
    def _setup_window(self) -> None:
        self.setWindowTitle("Klipper Control Interface")
        self.resize(800, 480)
        self.setStyleSheet("QWidget { background-color: #F5F5F5; color: #333333; }")

    def _create_main_layout(self) -> None:
        self.ana_duzen = QHBoxLayout()
        self.ana_duzen.setContentsMargins(10, 10, 10, 10)
        self.setLayout(self.ana_duzen)

    def _create_side_menu(self) -> None:
        self.sol_menu_duzeni = QVBoxLayout()
        self.sol_menu_duzeni.setSpacing(5)
        self.buton_grubu = QButtonGroup(self)
        self.buton_grubu.setExclusive(True)
        self.sayfalar_alani = QStackedWidget()
        self.sekme_isimleri = [
            "Sterilization", "Protocol", "Built Platform",
            "Model", "Settings", "Print",
        ]
        for index, name in enumerate(self.sekme_isimleri):
            btn = QPushButton(name)
            btn.setMinimumHeight(60)
            btn.setCheckable(True)
            if index == 0:
                btn.setChecked(True)
            btn.setStyleSheet("""
                QPushButton {
                    font-size: 16px; font-weight: bold; color: black;
                    background-color: #f0f0f0; border: 1px solid #cccccc; border-radius: 5px;
                }
                QPushButton:hover { background-color: #e0e0e0; }
                QPushButton:checked {
                    background-color: #64B5F6; color: black; border: 2px solid #1E88E5;
                }
            """)
            self.buton_grubu.addButton(btn, index)
            self.sol_menu_duzeni.addWidget(btn)
        self.sol_menu_duzeni.addStretch()

    def _create_pages(self) -> None:
        for index, name in enumerate(self.sekme_isimleri):
            page = QWidget()
            page_layout = QVBoxLayout()
            page.setLayout(page_layout)

            if name == "Sterilization":
                self._build_sterilization_page(page_layout)
            elif name == "Protocol":
                self._build_protocol_page(page_layout)
            elif name == "Built Platform":
                self._build_platform_page(page, page_layout)
            elif name == "Model":
                self._build_model_page(page_layout)
            elif name == "Settings":
                self._build_settings_page(page_layout)
            elif name == "Print":
                self._build_print_page(page_layout)

            self.sayfalar_alani.addWidget(page)

        self.ana_duzen.addLayout(self.sol_menu_duzeni, 1)
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        self.ana_duzen.addWidget(line)
        self.ana_duzen.addWidget(self.sayfalar_alani, 4)

    # ==========================================================
    # SIGNAL CONNECTIONS
    # ==========================================================
    def _connect_signals(self) -> None:
        if self.buton_grubu:
            self.buton_grubu.idClicked.connect(self._change_page)

        if self.protokol_listesi is not None:
            self.protokol_listesi.currentItemChanged.connect(self._on_protocol_selected)

        if self.open_btn:
            self.open_btn.clicked.connect(self._open_protocol)
        if self.edit_btn:
            self.edit_btn.clicked.connect(self._edit_protocol)
        if self.delete_btn:
            self.delete_btn.clicked.connect(self._delete_protocol)

        if self.bp_buton_grubu and self.bp_stacked:
            self.bp_buton_grubu.idClicked.connect(self.bp_stacked.setCurrentIndex)
            self.bp_buton_grubu.idClicked.connect(lambda _: self._update_platform_info())

        if self.in_dia:
            self.in_dia.textChanged.connect(lambda _: self._update_platform_info())
        if self.in_size:
            self.in_size.textChanged.connect(lambda _: self._update_platform_info())
        if self.btn_6:
            self.btn_6.clicked.connect(self._update_platform_info)
        if self.btn_12:
            self.btn_12.clicked.connect(self._update_platform_info)

        if self.uv_start_btn:
            self.uv_start_btn.clicked.connect(self._start_uv)
        if self.uv_stop_btn:
            self.uv_stop_btn.clicked.connect(self._stop_uv)
        if self.hepa_start_btn:
            self.hepa_start_btn.clicked.connect(self._start_hepa)
        if self.hepa_stop_btn:
            self.hepa_stop_btn.clicked.connect(self._stop_hepa)

        if self.btn_6:
            self.btn_6.clicked.connect(self._update_well_grid)
        if self.btn_12:
            self.btn_12.clicked.connect(self._update_well_grid)

        if self.open_stl_btn:
            self.open_stl_btn.clicked.connect(self._select_stl)

        if self.save_btn:
            self.save_btn.clicked.connect(self._save_protocol)

        if self.slice_btn:
            self.slice_btn.clicked.connect(self._slice_model)

        if self.print_btn:
            self.print_btn.clicked.connect(self._start_print)
        if self.pause_btn:
            self.pause_btn.clicked.connect(self._pause_print)
        if self.stop_btn:
            self.stop_btn.clicked.connect(self._stop_print)

    # ==========================================================
    # STERILIZATION PAGE
    # ==========================================================
    def _build_sterilization_page(self, layout: QVBoxLayout) -> None:
        uv_title = QLabel("UV Sterilization")
        uv_title.setStyleSheet("font-size: 20px; font-weight: bold; color: #333333; margin-top: 10px;")
        layout.addWidget(uv_title)
        layout.addSpacing(20)
        self.uv_zaman_kutusu, self.uv_start_btn, self.uv_stop_btn, self.uv_kalan_sure_lbl = \
            self._create_timer_row(layout)

        hepa_title = QLabel("HEPA Fan")
        hepa_title.setStyleSheet("font-size: 20px; font-weight: bold; color: #333333; margin-top: 40px;")
        layout.addWidget(hepa_title)
        layout.addSpacing(20)
        self.hepa_zaman_kutusu, self.hepa_start_btn, self.hepa_stop_btn, self.hepa_kalan_sure_lbl = \
            self._create_timer_row(layout)
        layout.addStretch()

    def _create_timer_row(
        self, parent: QVBoxLayout
    ) -> Tuple[QSpinBox, QPushButton, QPushButton, QLabel]:
        container = QVBoxLayout()
        container.setSpacing(12)

        # Üst satır
        top_row = QHBoxLayout()
        top_row.setSpacing(15)
        
        lbl = QLabel("Timer")
        lbl.setStyleSheet("font-size: 18px; color: #333333;")
        top_row.addWidget(lbl)
        top_row.addSpacing(20)

        sb = QSpinBox()
        sb.setRange(1, 120)
        sb.setValue(10)
        sb.setSuffix(" min")
        sb.setStyleSheet("font-size:16px; padding:5px; background:white; color:black;")
        top_row.addWidget(sb)
        top_row.addSpacing(20)

        start = QPushButton("Start")
        start.setStyleSheet("font-size:18px; padding:10px 30px; background:#e0e0e0; color:black;")
        top_row.addWidget(start)
        top_row.addSpacing(15)

        stop = QPushButton("Stop")
        stop.setStyleSheet("font-size:18px; padding:10px 30px; background:#e0e0e0; color:black;")
        top_row.addWidget(stop)
        top_row.addStretch()
        container.addLayout(top_row)

        # Alt satır: Remaining Time (normal) + --:-- (bold mavi)
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(10)
        
        remaining_lbl = QLabel("Remaining Time:")
        remaining_lbl.setStyleSheet("font-size:18px; color:#333333;")
        bottom_row.addWidget(remaining_lbl)
        
        remaining = QLabel("--:--")
        remaining.setStyleSheet("font-size:18px; color:#333333; font-weight:bold;")
        bottom_row.addWidget(remaining)
        bottom_row.addStretch()
        container.addLayout(bottom_row)

        parent.addLayout(container)
        return sb, start, stop, remaining

    # ==========================================================
    # PROTOCOL PAGE
    # ==========================================================
    def _build_protocol_page(self, layout: QVBoxLayout) -> None:
        main = QHBoxLayout()
        main.setSpacing(20)

        self.protokol_listesi = QListWidget()
       
        self.protokol_listesi.setStyleSheet(self.LIST_STYLE)
        self.protokol_listesi.setFixedWidth(210)
        main.addWidget(self.protokol_listesi)

        btn_col = QVBoxLayout()
        btn_col.setSpacing(15)

        self.open_btn = QPushButton("Open")
        self.edit_btn = QPushButton("Edit")
        self.delete_btn = QPushButton("Delete")

        for btn in (self.open_btn, self.edit_btn):
            btn.setStyleSheet(self.BTN_STYLE)
            btn_col.addWidget(btn)

        self.delete_btn.setStyleSheet(self.BTN_STYLE + """
            QPushButton { color: #B71C1C; }
            QPushButton:hover { background-color: #C62828; color: white; }
        """)
        btn_col.addWidget(self.delete_btn)
        btn_col.addStretch()
        main.addLayout(btn_col)

        self.protokol_detay_alani = QTextEdit()
        self.protokol_detay_alani.setReadOnly(True)
        self.protokol_detay_alani.setText("Select a protocol\nto view details here.")
        self.protokol_detay_alani.setStyleSheet(self.TEXTAREA_STYLE)
        main.addWidget(self.protokol_detay_alani)
        main.addSpacing(20)

        layout.addLayout(main)

    # ==========================================================
    # BUILT PLATFORM PAGE
    # ==========================================================
    def _build_platform_page(self, page: QWidget, layout: QVBoxLayout) -> None:
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

        self.bp_buton_grubu = QButtonGroup(page)
        self.bp_buton_grubu.setExclusive(True)

        for i, btn in enumerate((self.btn_petri, self.btn_well, self.btn_glass)):
            btn.setStyleSheet(self.BTN_STYLE_CHECKABLE)
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

        glass_shape = QFrame()
        glass_shape.setFixedSize(100, 220)
        glass_shape.setStyleSheet("border:4px solid #64B5F6; background:transparent;")

        lbl_size = QLabel("Size (mm)")
        lbl_size.setStyleSheet("font-size:18px; color:#333333;")
        self.in_size = QLineEdit("20x60")
        self.in_size.setFixedWidth(120)
        self.in_size.setStyleSheet("font-size:18px; padding:5px; background:white; color:black;")
        self.in_size.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lg.addWidget(glass_shape, alignment=Qt.AlignmentFlag.AlignCenter)
        lg.addSpacing(30)
        lg.addWidget(lbl_size, alignment=Qt.AlignmentFlag.AlignCenter)
        lg.addSpacing(5)
        lg.addWidget(self.in_size, alignment=Qt.AlignmentFlag.AlignCenter)
        self.bp_stacked.addWidget(p_glass)

        main.addWidget(self.bp_stacked)
        main.addStretch()
        layout.addLayout(main)

        # İlk well grid'i oluştur (6-well varsayılan)
        self._update_well_grid()

    # ==========================================================
    # MODEL PAGE
    # ==========================================================
    def _build_model_page(self, layout: QVBoxLayout) -> None:
        main = QHBoxLayout()
        sol = QVBoxLayout()

        title = QLabel("3D Model")
        title.setStyleSheet("font-size:20px; color:#333333; font-weight:bold;")
        sol.addWidget(title)
        sol.addSpacing(10)

        self.open_stl_btn = QPushButton("Open")
        self.open_stl_btn.setStyleSheet(self.BTN_STYLE)
        sol.addWidget(self.open_stl_btn)
        sol.addStretch()

        main.addLayout(sol, 1)

        self.uc_boyutlu_alan = QFrame()
        self.uc_boyutlu_alan.setStyleSheet(
             "QFrame{background:#f5f5f5;border:2px solid #cccccc;border-radius:8px;}"
        )

        # Placeholder kaldırıldı - boş frame pyvista dolduracak
        uc = QVBoxLayout()
        uc.setContentsMargins(0, 0, 0, 0)
        self.uc_boyutlu_alan.setLayout(uc)

        main.addWidget(self.uc_boyutlu_alan, 4)
        layout.addLayout(main)

    # ==========================================================
    # SETTINGS PAGE
    # ==========================================================
    def _build_settings_page(self, layout: QVBoxLayout) -> None:
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
            btn.setStyleSheet(self.PH_BTN_STYLE)
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
        self.ph_type_combo.setStyleSheet(self.COMBOBOX_STYLE)
        type_row.addWidget(self.ph_type_combo)
        type_row.addStretch()
        sol.addLayout(type_row)

        # --- Print Parameters Kartı ---
        g1 = QFrame()
        g1.setObjectName("KareMekan")
        g1.setStyleSheet(self.CARD_STYLE)
        f1 = QFormLayout(g1)
        f1.setVerticalSpacing(10)
        f1.setHorizontalSpacing(15)
        f1.setContentsMargins(15, 12, 15, 12)

        self.kutu_layer = self._create_spinbox(f1, "Layer Thickness", 0.1, 0.2, 2, 0.01, " mm", 0.2)
        self.kutu_speed = self._create_spinbox(f1, "Print Speed", 1, 60, 0, 1, " mm/s", 10)

        lbl_grid = QLabel("Grid Type")
        lbl_grid.setStyleSheet(self.LABEL_STYLE)
        lbl_grid.setFixedWidth(190)
        self.kutu_grid = QComboBox()
        self.kutu_grid.addItems(["Linear", "Gyroid", "Honeycomb", "Rectilinear"])
        self.kutu_grid.setCurrentText("Linear")
        self.kutu_grid.setFixedWidth(120)
        self.kutu_grid.setStyleSheet(self.COMBOBOX_STYLE)
        f1.addRow(lbl_grid, self.kutu_grid)

        self.kutu_distance = self._create_spinbox(f1, "Grid Distance", 0.01, 5.0, 2, 0.01, " mm", 0.2)
        sol.addWidget(g1)

        # --- Temperature Kartı ---
        g2 = QFrame()
        g2.setObjectName("KareMekan")
        g2.setStyleSheet(self.CARD_STYLE)
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
                background:#EBF5FB; border:1px solid #AED6F1; border-radius:6px;
            }
        """)
        f3 = QHBoxLayout(g3)
        f3.setContentsMargins(12, 8, 12, 8)

        bp_title = QLabel("Build Platform:")
        bp_title.setStyleSheet("font-size:14px; color:#555; font-weight:bold;")
        f3.addWidget(bp_title)

        self.bp_info_lbl = QLabel("—")
        self.bp_info_lbl.setStyleSheet("font-size:14px; color:#1565C0; font-weight:bold;")
        self.bp_info_lbl.setWordWrap(True)
        f3.addWidget(self.bp_info_lbl)
        f3.addStretch()
        sol.addWidget(g3)

        # --- Slice + Save Butonları ---
        bottom_row = QHBoxLayout()
        bottom_row.addStretch()

        self.slice_btn = QPushButton("Slice")
        self.slice_btn.setFixedHeight(45)
        self.slice_btn.setStyleSheet("""
            QPushButton {
                font-size:18px; font-weight:bold; padding:10px 40px;
                background:#42A5F5; color:white; border-radius:5px;
            }
            QPushButton:hover { background:#1E88E5; }
            QPushButton:pressed { background:#1565C0; }
        """)

        self.save_btn = QPushButton("Save Protocol")
        self.save_btn.setFixedHeight(45)
        self.save_btn.setStyleSheet("""
            QPushButton {
                font-size:18px; font-weight:bold; padding:10px 40px;
                background:#66BB6A; color:white; border-radius:5px;
            }
            QPushButton:hover { background:#43A047; }
        """)

        bottom_row.addWidget(self.slice_btn)
        bottom_row.addSpacing(15)
        bottom_row.addWidget(self.save_btn)
        bottom_row.addStretch()
        sol.addLayout(bottom_row)
        sol.addStretch()

        main.addLayout(sol, 2)

        # ==================== SAĞ PANEL (Layer Preview) ====================
        # ==================== SAĞ PANEL ====================
        right = QFrame()
        right.setStyleSheet("""
            QFrame {
                background:#f8f9fa; border:2px dashed #bbb; border-radius:10px;
            }
        """)
        rg = QVBoxLayout(right)
        rg.setContentsMargins(15, 15, 15, 15)

        # Katman görselleştirme için frame (pyvista plotter buraya yerleşecek)
        self.layer_plotter_frame = QFrame()
        self.layer_plotter_frame.setStyleSheet("background:#f5f5f5; border:1px solid #ccc; border-radius:8px;")
        rg.addWidget(self.layer_plotter_frame, stretch=4)

        # Katman navigasyon paneli (etiket + slider + butonlar)
        nav_panel = QWidget()
        nav_layout = QVBoxLayout(nav_panel)
        nav_layout.setSpacing(5)

        self.layer_nav_label = QLabel("Katman — / —")
        self.layer_nav_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.layer_nav_label.setStyleSheet("font-size:13px; color:#333; font-weight:bold;")
        nav_layout.addWidget(self.layer_nav_label)

        # Slider + Prev/Next
        slider_row = QHBoxLayout()
        slider_row.setSpacing(8)

        prev_btn = QPushButton("◄")
        prev_btn.setFixedSize(36, 36)
        prev_btn.setStyleSheet("font-size:16px; background:#e0e0e0; border-radius:4px;")
        prev_btn.clicked.connect(self._prev_layer)
        slider_row.addWidget(prev_btn)

        self.layer_slider = QSlider(Qt.Orientation.Horizontal)
        self.layer_slider.setMinimum(0)
        self.layer_slider.setMaximum(0)
        self.layer_slider.setValue(0)
        self.layer_slider.valueChanged.connect(self._on_layer_slider_changed)
        slider_row.addWidget(self.layer_slider, stretch=1)

        next_btn = QPushButton("►")
        next_btn.setFixedSize(36, 36)
        next_btn.setStyleSheet("font-size:16px; background:#e0e0e0; border-radius:4px;")
        next_btn.clicked.connect(self._next_layer)
        slider_row.addWidget(next_btn)

        nav_layout.addLayout(slider_row)
        rg.addWidget(nav_panel, stretch=1)

        main.addWidget(right, 2)
        layout.addLayout(main)

        
    def _create_spinbox(
        self, form: QFormLayout, label: str,
        min_v: float, max_v: float, dec: int, step: float,
        suffix: str, default: float,
    ) -> QDoubleSpinBox:
        lbl = QLabel(label)
        lbl.setStyleSheet(self.LABEL_STYLE)
        lbl.setFixedWidth(190)

        sb = QDoubleSpinBox()
        sb.setRange(min_v, max_v)
        sb.setDecimals(dec)
        sb.setSingleStep(step)
        sb.setSuffix(suffix)
        sb.setValue(default)
        sb.setFixedWidth(120)
        sb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sb.setStyleSheet(self.SPINBOX_STYLE)
        form.addRow(lbl, sb)
        return sb

    # ==========================================================
    # PRINT PAGE
    # ==========================================================
    def _build_print_page(self, layout: QVBoxLayout) -> None:
        main = QVBoxLayout()
        main.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self.print_btn = QPushButton("Print")
        self.pause_btn = QPushButton("Pause")
        self.stop_btn = QPushButton("Stop")

        style = """
            QPushButton{font-size:20px;padding:15px 40px;background:#e0e0e0;color:black;border-radius:5px;}
            QPushButton:hover{background:#333333;color:white;}
        """

        for btn in (self.print_btn, self.pause_btn, self.stop_btn):
            btn.setStyleSheet(style)
            btn_row.addWidget(btn)
            if btn is not self.stop_btn:
                btn_row.addSpacing(30)

        btn_row.addStretch()
        main.addLayout(btn_row)
        main.addSpacing(60)

        time_row = QHBoxLayout()
        time_row.addStretch()
        for attr, title in (("elapsed_deger", "Total Elapsed Time"), ("remaining_deger", "Time Remaining")):
            block = QVBoxLayout()
            t = QLabel(title)
            t.setStyleSheet("font-size:18px;color:#555555;")
            t.setAlignment(Qt.AlignmentFlag.AlignCenter)

            v = QLabel("0:00 min")
            v.setStyleSheet("font-size:24px;color:#000000;font-weight:bold;")
            v.setAlignment(Qt.AlignmentFlag.AlignCenter)

            setattr(self, attr, v)
            block.addWidget(t)
            block.addSpacing(10)
            block.addWidget(v)
            time_row.addLayout(block)
            if title == "Total Elapsed Time":
                time_row.addSpacing(80)

        time_row.addStretch()
        main.addLayout(time_row)
        main.addStretch()
        layout.addLayout(main)

    # ==========================================================
    # PAGE SWITCHING
    # ==========================================================
    def _change_page(self, index: int) -> None:
        if self.sayfalar_alani is not None:
            self.sayfalar_alani.setCurrentIndex(index)

    # ==========================================================
    # PROTOCOL LIST SELECTION
    # ==========================================================
    def _on_protocol_selected(
        self,
        current: Optional[QListWidgetItem],
        previous: Optional[QListWidgetItem],
    ) -> None:
        if previous:
            f = previous.font()
            f.setBold(False)
            previous.setFont(f)

        if current:
            f = current.font()
            f.setBold(True)
            current.setFont(f)
            record = self.kayitli_protokoller.get(current.text())
            if record and self.protokol_detay_alani:
                self.protokol_detay_alani.setText(record["detay"])
        else:
            if self.protokol_detay_alani:
                self.protokol_detay_alani.setText("Select a protocol\nto view details here.")

    # ==========================================================
    # PROTOCOL: OPEN
    # ==========================================================
    def _open_protocol(self) -> None:
        if not self.protokol_listesi:
            return
        item = self.protokol_listesi.currentItem()
        if item is None:
            QMessageBox.information(self, "No Selection", "Please select a protocol.")
            return
        record = self.kayitli_protokoller.get(item.text())
        if record and self.protokol_detay_alani:
            self.protokol_detay_alani.setText(record["detay"])

    # ==========================================================
    # PROTOCOL: EDIT
    # ==========================================================
    def _edit_protocol(self) -> None:
        """Load selected protocol values into Settings and switch to Settings tab."""
        if not self.protokol_listesi:
            return

        item = self.protokol_listesi.currentItem()
        if item is None:
            QMessageBox.information(self, "No Selection", "Please select a protocol to edit.")
            return

        name = item.text()
        record = self.kayitli_protokoller.get(name)

        if not record or not record.get("degerler"):
            QMessageBox.information(
                self, "Information",
                f"'{name}' has no saved values.\n"
                "You can enter new values in the Settings tab and save over it.",
            )
            self._switch_to_settings()
            return

        d = deepcopy(record["degerler"])

        ph_btn = self.ph_buton_grubu.button(d.get("ph_id", 1)) if self.ph_buton_grubu else None
        if ph_btn:
            ph_btn.setChecked(True)

        bp_type = d.get("bp_type", 0)
        bp_btn = self.bp_buton_grubu.button(bp_type) if self.bp_buton_grubu else None
        if bp_btn:
            bp_btn.setChecked(True)
        if self.bp_stacked:
            self.bp_stacked.setCurrentIndex(bp_type)

        if bp_type == 0 and self.in_dia:
            self.in_dia.setText(d.get("bp_dia", "60"))
        elif bp_type == 1:
            wf = d.get("bp_well_format", 6)
            if wf == 6 and self.btn_6:
                self.btn_6.setChecked(True)
            elif wf == 12 and self.btn_12:
                self.btn_12.setChecked(True)
        elif bp_type == 2 and self.in_size:
            self.in_size.setText(d.get("bp_size", "20x60"))

        if self.kutu_layer:
            self.kutu_layer.setValue(d.get("layer", 0.2))
        if self.kutu_speed:
            self.kutu_speed.setValue(d.get("speed", 10.0))
        if self.kutu_grid:
            self.kutu_grid.setCurrentText(d.get("grid", "Linear"))
        if self.kutu_distance:
            self.kutu_distance.setValue(d.get("distance", 0.2))
        if self.kutu_ph_temp:
            self.kutu_ph_temp.setValue(d.get("ph_temp", 27.0))
        if self.kutu_plat_temp:
            self.kutu_plat_temp.setValue(d.get("plat_temp", -30.0))

        self._update_platform_info()

        self._editing_protocol_name = name
        self._switch_to_settings()

    def _switch_to_settings(self) -> None:
        """Helper to switch view to the Settings tab (index 4)."""
        if self.buton_grubu:
            btn = self.buton_grubu.button(4)
            if btn:
                btn.setChecked(True)
        self._change_page(4)

    # ==========================================================
    # PROTOCOL: DELETE
    # ==========================================================
    def _delete_protocol(self) -> None:
        if not self.protokol_listesi:
            return

        item = self.protokol_listesi.currentItem()
        if item is None:
            QMessageBox.information(self, "No Selection", "Please select a protocol to delete.")
            return

        name = item.text()
        reply = QMessageBox.question(
            self, "Delete Protocol",
            f"Are you sure you want to delete the protocol '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.kayitli_protokoller.pop(name, None)
        self.protokol_listesi.takeItem(self.protokol_listesi.row(item))

        # Diskten de sil
        self._delete_protocol_from_disk(name)

        if self._editing_protocol_name == name:
            self._editing_protocol_name = None

        if self.protokol_detay_alani:
            self.protokol_detay_alani.setText("Select a protocol\nto view details here.")

        print(f"System: '{name}' deleted.")

    # ==========================================================
    # SETTINGS DATA COLLECTION
    # ==========================================================
    def _collect_settings_data(self) -> dict:
        """Gather all current values from the Settings page into a plain dict."""
        ph_id = self.ph_buton_grubu.checkedId() if self.ph_buton_grubu else 1
        bp_type = self.bp_buton_grubu.checkedId() if self.bp_buton_grubu else 0

        bp_dia = self.in_dia.text() if self.in_dia else "60"
        bp_wf = 12 if (self.btn_12 and self.btn_12.isChecked()) else 6
        bp_size = self.in_size.text() if self.in_size else "20x60"

        return {
            "ph_id": ph_id,
            "bp_type": bp_type,
            "bp_dia": bp_dia,
            "bp_well_format": bp_wf,
            "bp_size": bp_size,
            "layer": self.kutu_layer.value() if self.kutu_layer else 0.0,
            "speed": self.kutu_speed.value() if self.kutu_speed else 0.0,
            "grid": self.kutu_grid.currentText() if self.kutu_grid else "Linear",
            "distance": self.kutu_distance.value() if self.kutu_distance else 0.0,
            "ph_temp": self.kutu_ph_temp.value() if self.kutu_ph_temp else 0.0,
            "plat_temp": self.kutu_plat_temp.value() if self.kutu_plat_temp else 0.0,
        }

    def _format_protocol_detail(self, name: str, d: dict, bp_text: str) -> str:
        """Generate the human-readable detail text for a protocol snapshot."""
        return (
            f"Selected Protocol: {name}\n\n"
            f"Build Platform - {bp_text}\n\n"
            f"Selected Printhead - Printhead {d.get('ph_id', 1)}\n\n"
            f"Printhead Temperature - {d.get('ph_temp', 0.0):.1f} °C\n\n"
            f"Platform Temperature - {d.get('plat_temp', 0.0):.1f} °C\n\n"
            f"Layer Thickness - {d.get('layer', 0.0):.2f} mm\n\n"
            f"Print Speed - {d.get('speed', 0.0):.1f} mm/s\n\n"
            f"Grid Type - {d.get('grid', 'Linear')}\n\n"
            f"Grid Distance - {d.get('distance', 0.0):.2f} mm"
        )

    # ==========================================================
    # PROTOCOL: SAVE
    # ==========================================================
    def _save_protocol(self) -> None:
        """
        Save current Settings as an isolated protocol snapshot.
        If a protocol was just loaded via Edit, offer to update it directly.
        """
        name: Optional[str] = None

        if self._editing_protocol_name:
            reply = QMessageBox.question(
                self,
                "Update Protocol",
                f"Update existing protocol '{self._editing_protocol_name}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            )
            if reply == QMessageBox.StandardButton.Cancel:
                return
            elif reply == QMessageBox.StandardButton.Yes:
                name = self._editing_protocol_name

        if name is None:
            text, ok = QInputDialog.getText(self, "Save Protocol", "Enter protocol name:")
            if not ok or not text.strip():
                return
            name = text.strip()

        if name in self.kayitli_protokoller and name != self._editing_protocol_name:
            reply = QMessageBox.question(
                self, "Overwrite?",
                f"'{name}' already exists. Overwrite?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        data = deepcopy(self._collect_settings_data())

        if data["bp_type"] == 0:
            bp_text = f"Petri Dish  |  Diameter = {data['bp_dia']} mm"
        elif data["bp_type"] == 1:
            bp_text = f"Well Plate  |  {data['bp_well_format']}-well"
        elif data["bp_type"] == 2:
            bp_text = f"Glass Slide  |  Size = {data['bp_size']} mm"
        else:
            bp_text = "—"

        detail = self._format_protocol_detail(name, data, bp_text)

        self.kayitli_protokoller[name] = {
            "detay": detail,
            "degerler": deepcopy(data),
        }

        if self.protokol_listesi:
            existing = [self.protokol_listesi.item(i).text() for i in range(self.protokol_listesi.count())]
            if name not in existing:
                self.protokol_listesi.addItem(name)
            for i in range(self.protokol_listesi.count()):
                if self.protokol_listesi.item(i).text() == name:
                    self.protokol_listesi.setCurrentRow(i)
                    break

        if self.protokol_listesi and self.protokol_detay_alani:
            current_item = self.protokol_listesi.currentItem()
            if current_item and current_item.text() == name:
                self.protokol_detay_alani.setText(detail)

        self._editing_protocol_name = None

        # Diske yaz (kalıcı depolama)
        self._save_protocol_to_disk(name, self.kayitli_protokoller[name])

        print(f"System: Protocol saved -> '{name}'")
        QMessageBox.information(self, "Saved", f"Protocol '{name}' saved successfully.")

    # ==========================================================
    # BUILD PLATFORM INFO CARD
    # ==========================================================
    def _update_platform_info(self) -> None:
        if not self.bp_info_lbl or not self.bp_buton_grubu:
            return

        sid = self.bp_buton_grubu.checkedId()
        if sid == 0:
            dia = self.in_dia.text() if self.in_dia else "?"
            text = f"Petri Dish  |  Diameter = {dia} mm"
        elif sid == 1:
            fmt = "12-well" if (self.btn_12 and self.btn_12.isChecked()) else "6-well"
            text = f"Well Plate  |  {fmt}"
        elif sid == 2:
            size = self.in_size.text() if self.in_size else "?"
            text = f"Glass Slide  |  Size = {size} mm"
        else:
            text = "—"

        self.bp_info_lbl.setText(text)

    # ==========================================================
    # UV STERILIZATION
    # ==========================================================
    def _send_uv_command(self, state: bool) -> None:
        print(f"Klipper API: UV Lamp {'ON' if state else 'OFF'}.")
    
    def _send_hepa_command(self, state: bool) -> None:
        print(f"Klipper API: HEPA Fan {'ON' if state else 'OFF'}.")

    def _start_uv(self) -> None:
        if not self.uv_zaman_kutusu or not self.uv_start_btn or not self.uv_kalan_sure_lbl:
            return

        if self.uv_timer is None:
            self.uv_timer = QTimer(self)
            self.uv_timer.timeout.connect(self._uv_tick)

        self.uv_kalan_saniye = self.uv_zaman_kutusu.value() * 60
        self.uv_start_btn.setEnabled(False)
        self.uv_zaman_kutusu.setEnabled(False)
        self.uv_start_btn.setText("Running...")
        self.uv_start_btn.setStyleSheet(
            "font-size:18px; padding:10px 30px; background:#81C784; color:black;"
        )

        mins, secs = divmod(self.uv_kalan_saniye, 60)
        self.uv_kalan_sure_lbl.setText(f"{mins:02d}:{secs:02d}")
        self._send_uv_command(True)
        self.uv_timer.start(1000)

    def _stop_uv(self) -> None:
        if self.uv_timer and self.uv_timer.isActive():
            self.uv_timer.stop()
        if self.uv_start_btn and self.uv_zaman_kutusu and self.uv_kalan_sure_lbl:
            self.uv_start_btn.setEnabled(True)
            self.uv_zaman_kutusu.setEnabled(True)
            self.uv_start_btn.setText("Start")
            self.uv_start_btn.setStyleSheet(
                "font-size:18px; padding:10px 30px; background:#e0e0e0; color:black;"
            )
            self.uv_kalan_sure_lbl.setText("--:--")
        self._send_uv_command(False)

    def _uv_tick(self) -> None:
        self.uv_kalan_saniye -= 1
        mins, secs = divmod(max(self.uv_kalan_saniye, 0), 60)
        if self.uv_kalan_sure_lbl:
            self.uv_kalan_sure_lbl.setText(f"{mins:02d}:{secs:02d}")
        if self.uv_kalan_saniye <= 0:
            self._stop_uv()
    # ==========================================================
    # HEPA FAN
    # ==========================================================
    def _start_hepa(self) -> None:
        if not self.hepa_zaman_kutusu or not self.hepa_start_btn or not self.hepa_kalan_sure_lbl:
            return

        if self.hepa_timer is None:
            self.hepa_timer = QTimer(self)
            self.hepa_timer.timeout.connect(self._hepa_tick)

        self.hepa_kalan_saniye = self.hepa_zaman_kutusu.value() * 60
        self.hepa_start_btn.setEnabled(False)
        self.hepa_zaman_kutusu.setEnabled(False)
        self.hepa_start_btn.setText("Running...")
        self.hepa_start_btn.setStyleSheet(
            "font-size:18px; padding:10px 30px; background:#81C784; color:black;"
        )

        mins, secs = divmod(self.hepa_kalan_saniye, 60)
        self.hepa_kalan_sure_lbl.setText(f"{mins:02d}:{secs:02d}")
        self._send_hepa_command(True)
        self.hepa_timer.start(1000)

    def _stop_hepa(self) -> None:
        if self.hepa_timer and self.hepa_timer.isActive():
            self.hepa_timer.stop()
        if self.hepa_start_btn and self.hepa_zaman_kutusu and self.hepa_kalan_sure_lbl:
            self.hepa_start_btn.setEnabled(True)
            self.hepa_zaman_kutusu.setEnabled(True)
            self.hepa_start_btn.setText("Start")
            self.hepa_start_btn.setStyleSheet(
                "font-size:18px; padding:10px 30px; background:#e0e0e0; color:black;"
            )
            self.hepa_kalan_sure_lbl.setText("--:--")
        self._send_hepa_command(False)

    def _hepa_tick(self) -> None:
        self.hepa_kalan_saniye -= 1
        mins, secs = divmod(max(self.hepa_kalan_saniye, 0), 60)
        if self.hepa_kalan_sure_lbl:
            self.hepa_kalan_sure_lbl.setText(f"{mins:02d}:{secs:02d}")
        if self.hepa_kalan_saniye <= 0:
            self._stop_hepa()

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

    # ==========================================================
    # 3D MODEL
    # ==========================================================
    def _select_stl(self) -> None:
        if pv is None or QtInteractor is None:
            QMessageBox.warning(
                self,
                "Missing Library",
                "pyvista and pyvistaqt must be installed for STL viewing.",
            )
            return

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select STL Model",
            "",
            "STL Files (*.stl);;All Files (*)"
        )
        if path:
            self.stl_dosya_yolu = path
            self._show_stl(path)

    def _show_stl(self, path: str) -> None:
        if pv is None or QtInteractor is None or self.uc_boyutlu_alan is None:
            return

        try:
            if self.plotter is None:
                lay = self.uc_boyutlu_alan.layout()
                if lay:
                    while lay.count():
                        it = lay.takeAt(0)
                        if it.widget():
                            it.widget().deleteLater()
                else:
                    self.uc_boyutlu_alan.setLayout(QVBoxLayout())

                self.uc_boyutlu_alan.setStyleSheet(
                    "QFrame{background:#f5f5f5;border:2px solid #cccccc;border-radius:8px;}"
                )
                self.plotter = QtInteractor(self.uc_boyutlu_alan)
                self.uc_boyutlu_alan.layout().addWidget(self.plotter.interactor)

            self.plotter.clear()
            self.plotter.set_background("#f5f5f5")

            # STL'yi oku
            mesh = pv.read(path)

            # Modeli X,Y'de ortala, Z'de altını platforma (Z=0) oturt
            z_min = mesh.bounds[4]
            center_xy = mesh.center[:2]
            mesh.translate((-center_xy[0], -center_xy[1], -z_min), inplace=True)

            # --- BASKI TABLASI (Cura stili) ---
            plate_size = 150  # 150x150 mm sabit platform

            # Gri zemin
            platform = pv.Plane(
                center=(0, 0, -0.05),
                direction=(0, 0, 1),
                i_size=plate_size,
                j_size=plate_size
            )
            self.plotter.add_mesh(
                platform,
                color="#e0e0e0",
                show_edges=False,
                lighting=False
            )

            # Kare grid çizgileri (10mm aralıklar)
            spacing = 10
            n_lines = int(plate_size / (2 * spacing))
            for i in range(-n_lines, n_lines + 1):
                x = i * spacing
                line_y = pv.Line((x, -plate_size/2, 0), (x, plate_size/2, 0))
                self.plotter.add_mesh(line_y, color="#bbbbbb", line_width=1)
                line_x = pv.Line((-plate_size/2, x, 0), (plate_size/2, x, 0))
                self.plotter.add_mesh(line_x, color="#bbbbbb", line_width=1)

            # --- MODEL (Canlı Mavi - Cura tarzı parlak) ---
            self.plotter.add_mesh(
                mesh,
                color="#29b6f6",         # Daha canlı, açık mavi
                show_edges=False,
                edge_color="#01579b",    # Koyu mavi kenarlar
                lighting=True,
                smooth_shading=False,    # Düz shading - Cura'daki gibi
                specular=1.0,            # Tam parlama
                specular_power=128,      # Keskin parlama noktaları
                ambient=0.15,            # Düşük ortam ışığı (daha fazla kontrast)
                diffuse=0.95             # Yüksek yayılmış ışık
            )

            # Ana ışık - üstten sağdan
            light1 = pv.Light(
                position=(200, 200, 300),
                focal_point=(0, 0, 0),
                intensity=1.2
            )
            self.plotter.add_light(light1)

            # Dolgu ışığı - soldan alttan
            light2 = pv.Light(
                position=(-200, -200, 100),
                focal_point=(0, 0, 0),
                intensity=0.7
            )
            self.plotter.add_light(light2)

            # Arka ışık - arkadan
            light3 = pv.Light(
                position=(0, -300, 150),
                focal_point=(0, 0, 0),
                intensity=0.5
            )
            self.plotter.add_light(light3)

            # --- EKSENLER (Tabanın sol alt köşesinde) ---
            axis_origin = [-plate_size/2, -plate_size/2, 0]
            axis_len = 25

            # X ekseni (Kırmızı)
            x_line = pv.Line(axis_origin, [axis_origin[0]+axis_len, axis_origin[1], axis_origin[2]])
            self.plotter.add_mesh(x_line, color="#d32f2f", line_width=4)
            x_cone = pv.Cone(
                center=[axis_origin[0]+axis_len, axis_origin[1], axis_origin[2]],
                direction=[1, 0, 0],
                height=4,
                radius=2
            )
            self.plotter.add_mesh(x_cone, color="#d32f2f")

            # Y ekseni (Yeşil)
            y_line = pv.Line(axis_origin, [axis_origin[0], axis_origin[1]+axis_len, axis_origin[2]])
            self.plotter.add_mesh(y_line, color="#388e3c", line_width=4)
            y_cone = pv.Cone(
                center=[axis_origin[0], axis_origin[1]+axis_len, axis_origin[2]],
                direction=[0, 1, 0],
                height=4,
                radius=2
            )
            self.plotter.add_mesh(y_cone, color="#388e3c")

            # Z ekseni (Mavi)
            z_line = pv.Line(axis_origin, [axis_origin[0], axis_origin[1], axis_origin[2]+axis_len])
            self.plotter.add_mesh(z_line, color="#1976d2", line_width=4)
            z_cone = pv.Cone(
                center=[axis_origin[0], axis_origin[1], axis_origin[2]+axis_len],
                direction=[0, 0, 1],
                height=4,
                radius=2
            )
            self.plotter.add_mesh(z_cone, color="#1976d2")

            # Eksen etiketleri (X, Y, Z)
            label_pts = [
                [axis_origin[0]+axis_len+5, axis_origin[1], axis_origin[2]],
                [axis_origin[0], axis_origin[1]+axis_len+5, axis_origin[2]],
                [axis_origin[0], axis_origin[1], axis_origin[2]+axis_len+5]
            ]
            label_colors = ["#d32f2f", "#388e3c", "#1976d2"]
            for pt, lbl, clr in zip(label_pts, ["X", "Y", "Z"], label_colors):
                self.plotter.add_point_labels(
                    [pt], [lbl],
                    text_color=clr,
                    font_size=14,
                    bold=True,
                    point_size=0,
                    shape=None,
                    always_visible=True
                )

            # Kamera
            self.plotter.camera_position = 'iso'
            self.plotter.reset_camera()
            self.plotter.camera.zoom(1.0)

        except Exception as e:
            QMessageBox.critical(self, "STL Error", f"Could not load STL:\n{e}")

        self._slice_mode = False

    # ==========================================================
    # PLACEHOLDERS FOR SLICE / PRINT
    # ==========================================================
    def _slice_model(self) -> None:
        if pv is None or QtInteractor is None:
            QMessageBox.warning(self, "Eksik Kütüphane", "pyvista ve pyvistaqt gerekli.")
            return

        if not self.stl_dosya_yolu:
            QMessageBox.warning(self, "Model Yok",
                "Lütfen önce Model sekmesinden bir STL dosyası yükleyin.")
            return

        layer_h = self.kutu_layer.value() if self.kutu_layer else 0.2
        if layer_h <= 0:
            QMessageBox.warning(self, "Hata", "Katman kalınlığı 0'dan büyük olmalı.")
            return

        try:
            mesh = pv.read(self.stl_dosya_yolu)

            # Modeli Z=0'a oturt
            z_min = mesh.bounds[4]
            mesh.translate((0, 0, -z_min), inplace=True)
            total_h = mesh.bounds[5] - mesh.bounds[4]

            n_layers = max(1, int(total_h / layer_h))

            # 1) 2D slice'ları al
            self._slices = []
            for i in range(n_layers):
                z = (i + 0.5) * layer_h
                try:
                    slc = mesh.slice(normal='z', origin=(0, 0, z))
                    self._slices.append(slc)
                except Exception:
                    self._slices.append(None)

            # 2) 2D slice'ları 3D slab'lara çevir
            self._layer_meshes = []
            for i, slc in enumerate(self._slices):
                if slc is None or slc.n_points == 0:
                    self._layer_meshes.append(None)
                    continue
                try:
                    surface = slc.delaunay_2d()
                    extruded = surface.extrude((0, 0, layer_h), capping=True)
                    z = (i + 0.5) * layer_h
                    extruded.translate((0, 0, z - layer_h / 2), inplace=True)
                    self._layer_meshes.append(extruded)
                except Exception:
                    self._layer_meshes.append(slc)

            # 3) Settings sekmesindeki plotter'ı hazırla
            self._init_settings_plotter()   # Yeni yardımcı fonksiyon

            # Slider ayarları
            if self.layer_slider is not None:
                self.layer_slider.setMaximum(n_layers - 1)
                self.layer_slider.setValue(0)

            self._current_layer_idx = 0
            self._show_layer(0)   # Artık bu fonksiyon Settings plotter'ını kullanacak

            print(f"Slice tamamlandı: {n_layers} katman, layer_h={layer_h} mm")
            QMessageBox.information(self, "Başarılı", f"{n_layers} katman oluşturuldu.\nSettings sekmesinin sağ panelinde görüntüleyebilirsiniz.")

        except Exception as e:
            QMessageBox.critical(self, "Slice Hatası", str(e))

    def _start_print(self) -> None:
        QMessageBox.information(self, "Print", "Print start not yet connected.")

    def _pause_print(self) -> None:
        QMessageBox.information(self, "Pause", "Print pause not yet connected.")

    def _stop_print(self) -> None:
        QMessageBox.information(self, "Stop", "Print stop not yet connected.")

    # ==========================================================
    # JSON DISK I/O
    # ==========================================================
    def _sanitize_filename(self, name: str) -> str:
        """Protokol adını güvenli ve benzersiz bir dosya adına çevirir."""
        safe = re.sub(r'[^\w\s-]', '', name).strip().replace(' ', '_')
        if not safe:
            safe = "protocol"
        
        # Aynı safe ismin çakışmasını önlemek için kısa hash ekle
        unique_hash = hashlib.md5(name.encode('utf-8')).hexdigest()[:8]
        return f"{safe}_{unique_hash}.json"
    
    def _save_protocol_to_disk(self, name: str, record: dict) -> None:
        """Protokolü data/protocols/ altına sadece ham verilerle JSON olarak yazar."""
        path = self._protocols_dir / self._sanitize_filename(name)

        d = record.get("degerler", {})
        payload = {
            "protocol_name": name,
            "printhead_number": d.get("ph_id", 1),
            "layer_thickness_mm": d.get("layer", 0.0),
            "print_speed_mm_s": d.get("speed", 0.0),
            "grid_type": d.get("grid", "Linear"),
            "grid_distance_mm": d.get("distance", 0.0),
            "printhead_temperature_c": d.get("ph_temp", 0.0),
            "platform_temperature_c": d.get("plat_temp", 0.0),
            "built_platform": {
                "type_index": d.get("bp_type", 0),
                "petri_diameter_mm": d.get("bp_dia") if d.get("bp_type") == 0 else None,
                "well_format": d.get("bp_well_format") if d.get("bp_type") == 1 else None,
                "glass_size_mm": d.get("bp_size") if d.get("bp_type") == 2 else None,
            }
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _delete_protocol_from_disk(self, name: str) -> None:
        """Diskteki JSON dosyasını siler."""
        path = self._protocols_dir / self._sanitize_filename(name)
        if path.exists():
            path.unlink()

    def _load_protocols(self) -> None:
        """Program açılırken data/protocols/ klasöründeki JSON'ları okur."""
        print(f"[DEBUG] _load_protocols başladı")
        print(f"[DEBUG] protocols_dir: {self._protocols_dir}")
        print(f"[DEBUG] exists: {self._protocols_dir.exists()}")
        print(f"[DEBUG] protokol_listesi: {self.protokol_listesi}")

        # Sadece dict'i temizle, listeyi _load_protocols değil, 
        # _build_protocol_page veya _refresh_protocol_list halletsin
        self.kayitli_protokoller.clear()

        if not self._protocols_dir.exists():
            print("[DEBUG] Dizin yok, çıkılıyor")
            return

        dosyalar = list(self._protocols_dir.glob("*.json"))
        print(f"[DEBUG] Bulunan JSON sayısı: {len(dosyalar)}")

        for json_file in sorted(dosyalar):
            try:
                print(f"[DEBUG] Okunuyor: {json_file.name}")
                with open(json_file, "r", encoding="utf-8") as f:
                    p = json.load(f)

                name = p.get("protocol_name") or json_file.stem
                print(f"[DEBUG] Protokol adı: {name}")

                bp = p.get("built_platform", {})
                bp_type = bp.get("type_index", 0)

                degerler = {
                    "ph_id": p.get("printhead_number", 1),
                    "layer": p.get("layer_thickness_mm", 0.2),
                    "speed": p.get("print_speed_mm_s", 10.0),
                    "grid": p.get("grid_type", "Linear"),
                    "distance": p.get("grid_distance_mm", 0.2),
                    "ph_temp": p.get("printhead_temperature_c", 27.0),
                    "plat_temp": p.get("platform_temperature_c", -30.0),
                    "bp_type": bp_type,
                    "bp_dia": str(bp.get("petri_diameter_mm") or "60") if bp_type == 0 else "60",
                    "bp_well_format": bp.get("well_format") or 6 if bp_type == 1 else 6,
                    "bp_size": str(bp.get("glass_size_mm") or "20x60") if bp_type == 2 else "20x60",
                }

                bp_text = self._built_platform_text_from_data(degerler)
                detail = self._format_protocol_detail(name, degerler, bp_text)

                self.kayitli_protokoller[name] = {
                    "detay": detail,
                    "degerler": degerler,
                }

                print(f"[DEBUG] Dict'e eklendi: {name}")

            except Exception as e:
                print(f"[HATA] {json_file.name}: {e}")

        print(f"[DEBUG] Toplam dict içinde: {len(self.kayitli_protokoller)}")

        # Listeyi burada doldur - sinyaller henüz bağlı değilse manuel detay göster
        if self.protokol_listesi  is not None:
            self.protokol_listesi.clear()
            for name in sorted(self.kayitli_protokoller.keys()):
                self.protokol_listesi.addItem(name)
            
            print(f"[DEBUG] Listeye eklenen: {self.protokol_listesi.count()}")
            
            if self.protokol_listesi.count() > 0:
                self.protokol_listesi.setCurrentRow(0)
                # Sinyaller bağlı değilse manuel olarak detay göster
                first_item = self.protokol_listesi.item(0)
                if first_item and self.protokol_detay_alani:
                    name = first_item.text()
                    record = self.kayitli_protokoller.get(name)
                    if record:
                        self.protokol_detay_alani.setText(record["detay"])
                        f = first_item.font()
                        f.setBold(True)
                        first_item.setFont(f)
                        print(f"[DEBUG] Detay gösterildi: {name}")
        else:
            print("[DEBUG] protokol_listesi henüz yok!")

    def _built_platform_text_from_data(self, d: dict) -> str:
        """Ham veri dict'inden Build Platform info metni üretir."""
        bp_type = d.get("bp_type", 0)
        if bp_type == 0:
            return f"Petri Dish  |  Diameter = {d.get('bp_dia', '?')} mm"
        elif bp_type == 1:
            return f"Well Plate  |  {d.get('bp_well_format', 6)}-well"
        elif bp_type == 2:
            return f"Glass Slide  |  Size = {d.get('bp_size', '?')} mm"
        return "—"

    def _show_layer(self, idx: int) -> None:
        if not self._layer_meshes or self.layer_plotter is None:
            return

        idx = max(0, min(idx, len(self._layer_meshes) - 1))
        self._current_layer_idx = idx
        plotter = self.layer_plotter

        plotter.clear()
        plotter.set_background('#f5f5f5')

        # --- Tüm katmanları çiz (ghost efekti) ---
        layer_h = self.kutu_layer.value() if self.kutu_layer else 0.2
        total_h = len(self._layer_meshes) * layer_h

        for i, mesh in enumerate(self._layer_meshes):
            if mesh is None:
                continue

            if i < idx:
                color, opacity, show_edges = '#CFD8DC', 0.25, False
            elif i == idx:
                color, opacity, show_edges = '#29B6F6', 1.0, True
            else:
                color, opacity, show_edges = '#E3F2FD', 0.08, False

            plotter.add_mesh(
                mesh,
                color=color,
                opacity=opacity,
                show_edges=show_edges,
                edge_color='#01579B',
                line_width=1.5,
                lighting=True,
                smooth_shading=True,
                specular=0.8,
                specular_power=64,
            )

        # --- Platform (referans düzlemi) ---
        plate_size = max(150, total_h * 2)
        platform = pv.Plane(
            center=(0, 0, -0.05),
            direction=(0, 0, 1),
            i_size=plate_size,
            j_size=plate_size
        )
        plotter.add_mesh(platform, color='#e0e0e0', show_edges=False, lighting=False)

        # 10mm grid
        spacing = 10
        n = int(plate_size / (2 * spacing))
        for i in range(-n, n + 1):
            x = i * spacing
            plotter.add_mesh(pv.Line((x, -plate_size/2, 0), (x, plate_size/2, 0)), color='#bbbbbb', line_width=1)
            plotter.add_mesh(pv.Line((-plate_size/2, x, 0), (plate_size/2, x, 0)), color='#bbbbbb', line_width=1)

        # --- Koordinat eksenleri ---
        axis_origin = [-plate_size/2, -plate_size/2, 0]
        axis_len = 25
        for direction, color in [([1,0,0], '#d32f2f'), ([0,1,0], '#388e3c'), ([0,0,1], '#1976d2')]:
            end = [axis_origin[j] + direction[j]*axis_len for j in range(3)]
            plotter.add_mesh(pv.Line(axis_origin, end), color=color, line_width=4)
            cone = pv.Cone(center=end, direction=direction, height=4, radius=2)
            plotter.add_mesh(cone, color=color)

        # --- Işıklandırma ---
        plotter.remove_all_lights()
        for pos, intensity in [((200, 200, 300), 1.0), ((-200, -200, 100), 0.6), ((0, -300, 150), 0.4)]:
            light = pv.Light(position=pos, focal_point=(0, 0, total_h/2), intensity=intensity)
            plotter.add_light(light)

        # --- Kamera ayarları ---
        plotter.view_isometric()
        plotter.reset_camera()
        plotter.camera.zoom(1.1)

        # --- UI güncelleme ---
        if self.layer_nav_label:
            self.layer_nav_label.setText(f"Katman {idx + 1} / {len(self._layer_meshes)}")

        if self.layer_slider and self.layer_slider.value() != idx:
            self.layer_slider.blockSignals(True)
            self.layer_slider.setValue(idx)
            self.layer_slider.blockSignals(False)

        self._slice_mode = True

    def _on_layer_slider_changed(self, value: int) -> None:
        self._show_layer(value)

    def _prev_layer(self) -> None:
        self._show_layer(self._current_layer_idx - 1)

    def _next_layer(self) -> None:
        self._show_layer(self._current_layer_idx + 1)

    def _init_settings_plotter(self) -> None:
        """Settings sekmesinin sağ panelinde pyvista plotter oluştur."""
        if pv is None or QtInteractor is None:
            return

        if self.layer_plotter is None and self.layer_plotter_frame is not None:
            # Eski içeriği temizle
            lay = self.layer_plotter_frame.layout()
            if lay is None:
                lay = QVBoxLayout(self.layer_plotter_frame)
                lay.setContentsMargins(0, 0, 0, 0)
            else:
                while lay.count():
                    item = lay.takeAt(0)
                    if item.widget():
                        item.widget().deleteLater()
            # Yeni plotter oluştur
            self.layer_plotter = QtInteractor(self.layer_plotter_frame)
            lay.addWidget(self.layer_plotter.interactor)
            self.layer_plotter.set_background("#f5f5f5")

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = KlipperArayuzu()
    window.show()
    sys.exit(app.exec())