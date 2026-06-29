"""KlipperArayuzu — the Controller / Mediator.

Assembles the side menu + the seven tab components inside a QStackedWidget, then
binds each tab's public widgets onto ``self`` (facade references) so the original
business-logic methods (timers, protocol CRUD, slicing, layer preview) run
unmodified. Only three call-site redirects differ from the monolith:
  * render helpers          -> core.viewport.build_platform_grid / build_axis_arrows
  * JSON I/O + formatting    -> core.data_manager.DataManager (self.dm)
  * infill geometry          -> lives in core.slicer_worker (used by the worker)
"""
from __future__ import annotations

import threading
from copy import deepcopy
from pathlib import Path
from typing import Optional

try:
    import requests   # Moonraker HTTP API (aynı Pi'deki Klipper host)
except ImportError:   # 'requests' kurulu değilse uygulama yine de açılsın
    requests = None

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QStackedWidget, QLabel,
    QFrame, QButtonGroup, QListWidgetItem, QFileDialog, QInputDialog, QMessageBox,
    QApplication,
)
from PyQt6.QtCore import QTimer, QThread, Qt

from core.viewport import pv, QtInteractor, build_platform_grid, build_axis_arrows
from core.slicer_worker import SliceWorker
from core.gcode_exporter import generate_gcode
from core.data_manager import DataManager
from ui.components import (
    SterilizationTab, ProtocolTab, PlatformTab, ModelTab,
    SettingsTab, PreviewTab, PrintTab,
)


def _thread_alive(thread) -> bool:
    """True only if the QThread is still running. Dead/deleted wrappers → False.

    Used to prune retired slice threads WITHOUT calling the blocking wait().
    """
    try:
        return bool(thread is not None and thread.isRunning())
    except Exception:
        return False


class KlipperArayuzu(QWidget):
    # ==========================================================
    # INITIALIZATION
    # ==========================================================
    def __init__(self) -> None:
        super().__init__()
        self._init_state()
        self._setup_window()
        self._create_main_layout()
        self._create_side_menu()
        self._create_pages()        # instantiate tab components
        self._bind_facade()         # map tab widgets onto self
        self._load_protocols()
        self._connect_signals()
        self._update_platform_info()

    def closeEvent(self, event) -> None:
        """Ensure VTK renderers are safely closed before application exit to prevent SegFaults."""
        # Bu bayrak, kapanış sırasında kuyrukta kalan slice sinyallerinin
        # silinmiş widget'lara dokunmasını engeller (_on_slice_*, _show_layer).
        self._closing = True

        # Worker sinyallerini önce kopar: quit/wait sırasında kuyruğa düşmüş bir
        # finished/error, ana thread'de yıkım anında slot tetiklemesin.
        if getattr(self, '_slice_worker', None) is not None:
            try:
                self._slice_worker.finished.disconnect()
                self._slice_worker.error.disconnect()
            except Exception:
                pass

        # Çalışan slice thread'ini güvenle durdur: worker bitmeden VTK/ana thread
        # yıkılırsa segfault olur. Kapanışta beklemek kabul edilir AMA gerçek bir
        # zombie thread'de süresiz wait() kapanışı da dondurur → timeout'lu bekle.
        threads = []
        if getattr(self, '_slice_thread', None) is not None:
            threads.append(self._slice_thread)
        threads.extend(th for (_wk, th) in getattr(self, '_retired_threads', []))
        for _th in threads:
            try:
                if _th.isRunning():
                    _th.quit()
                    # Bounded wait so close never hangs indefinitely. If the thread
                    # is a genuine zombie still running after 5 s, forcibly
                    # terminate() it as a last resort (prevents zombie processes on
                    # the RPi4); otherwise it would block app exit forever.
                    if not _th.wait(5000) and _th.isRunning():
                        _th.terminate()
                        _th.wait(1000)
            except Exception:
                pass
        if getattr(self, 'plotter', None) is not None:
            try:
                self.plotter.close()
            except Exception:
                pass
        if getattr(self, 'layer_plotter', None) is not None:
            try:
                self.layer_plotter.close()
            except Exception:
                pass
        event.accept()

    def _init_state(self) -> None:
        """Non-widget state: data layer, timers, slice results, plotter handles."""
        # --- Data layer (JSON persistence) ---
        self.dm = DataManager()
        # Facade to the SAME dict object DataManager mutates in place.
        self.kayitli_protokoller: dict[str, dict] = self.dm.protocols
        self._protocols_dir = self.dm.protocols_dir
        # Tracks which protocol is currently being edited so Save can update it directly.
        self._editing_protocol_name: Optional[str] = None

        # Timers
        self.uv_timer: Optional[QTimer] = None
        self.uv_kalan_saniye: int = 0
        self.hepa_timer: Optional[QTimer] = None
        self.hepa_kalan_saniye: int = 0

        # Print Timer
        self.print_timer: Optional[QTimer] = None
        self._print_elapsed: int = 0
        self._print_total: int = 3600   # default 60 dakika
        self._print_paused: bool = False

        # 3D Model
        self.stl_dosya_yolu: Optional[str] = None
        self.plotter = None

        # Slice sonuçları
        self._slices: list = []           # Her katmanın pyvista slice objesi
        self._current_layer_idx: int = 0  # Şu an gösterilen katman numarası
        self._layer_meshes: list = []
        self._infills: list = []          # Her katmanın infill grid PolyData'sı
        self._all_layers_mesh = None      # single merged mesh w/ 'layer_idx' cell scalars
        self._original_mesh = None        # Z-normalize edilmiş tam model (ghost render)

        # Worker/thread referansları (GC'ye karşı tutulur)
        self._slice_thread: Optional[QThread] = None
        self._slice_worker: Optional[SliceWorker] = None
        # Lifecycle guards: prevent re-entrant slicing and late-slot crashes.
        self._slicing: bool = False      # True while a slice thread is in flight
        self._closing: bool = False      # True once closeEvent has begun teardown
        # Retired (finishing) threads kept alive until they self-finish, so we
        # never GC a running thread and never block the GUI with wait().
        self._retired_threads: list = []

        # Layer-slider debounce (RPi4): valueChanged her pikselde _show_layer'ı
        # tetiklerse CPU çöker. Tek-atış 150 ms timer ile son konuma bir kez render.
        self.slider_debounce_timer: Optional[QTimer] = None
        self._pending_layer_idx: int = 0

        # Layer preview (Preview sekmesindeki plotter)
        self.layer_plotter = None           # QtInteractor nesnesi
        self._last_plate_size: Optional[float] = None

        # ── Önizleme: yalnızca dikey layer_slider ile katman seçimi ─────────
        # _render_last_idx: tamamlanan katmanlar yalnızca katman değişince yeniden
        # çizilsin diye (gereksiz yeniden çizimi önler).
        self._render_last_idx: int = -1

    # ==========================================================
    # WINDOW & LAYOUT
    # ==========================================================
    def _setup_window(self) -> None:
        self.setWindowTitle("Klipper Control Interface")
        self.resize(800, 480)
        self.setStyleSheet("QWidget { background-color: #F8F9FA; color: #212121; }")

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
            "Model", "Settings", "Preview", "Print",
        ]
        self.preview_btn: Optional[QPushButton] = None
        for index, name in enumerate(self.sekme_isimleri):
            btn = QPushButton(name)
            btn.setMinimumHeight(60)
            btn.setFixedWidth(140)
            btn.setCheckable(True)
            if index == 0:
                btn.setChecked(True)
            btn.setStyleSheet("""
                QPushButton {
                    font-size: 15px; font-weight: bold; color: black;
                    background-color: #f0f0f0; border: 1px solid #cccccc; border-radius: 5px;
                }
                QPushButton:hover { background-color: #e0e0e0; }
                QPushButton:checked {
                    background-color: #64B5F6; color: black; border: 2px solid #1E88E5;
                }
            """)
            self.buton_grubu.addButton(btn, index)
            self.sol_menu_duzeni.addWidget(btn)
            if name == "Preview":
                self.preview_btn = btn
        self.sol_menu_duzeni.addStretch()

    def _create_pages(self) -> None:
        """Instantiate the tab components in side-menu order and stack them."""
        self.sterilization_tab = SterilizationTab()
        self.protocol_tab = ProtocolTab()
        self.platform_tab = PlatformTab()
        self.model_tab = ModelTab()
        self.settings_tab = SettingsTab()
        self.preview_tab = PreviewTab()
        self.print_tab = PrintTab()

        for tab in (
            self.sterilization_tab, self.protocol_tab, self.platform_tab,
            self.model_tab, self.settings_tab, self.preview_tab, self.print_tab,
        ):
            self.sayfalar_alani.addWidget(tab)

        self.ana_duzen.addLayout(self.sol_menu_duzeni, 1)
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        self.ana_duzen.addWidget(line)
        self.ana_duzen.addWidget(self.sayfalar_alani, 4)

    # ==========================================================
    # FACADE REFERENCES
    # ----------------------------------------------------------
    # Map each tab's public widgets onto self so the controller's
    # business-logic methods read like the original monolith.
    # ==========================================================
    def _bind_facade(self) -> None:
        # Sterilization
        self.uv_zaman_kutusu = self.sterilization_tab.uv_zaman_kutusu
        self.uv_start_btn = self.sterilization_tab.uv_start_btn
        self.uv_stop_btn = self.sterilization_tab.uv_stop_btn
        self.uv_kalan_sure_lbl = self.sterilization_tab.uv_kalan_sure_lbl
        self.hepa_zaman_kutusu = self.sterilization_tab.hepa_zaman_kutusu
        self.hepa_start_btn = self.sterilization_tab.hepa_start_btn
        self.hepa_stop_btn = self.sterilization_tab.hepa_stop_btn
        self.hepa_kalan_sure_lbl = self.sterilization_tab.hepa_kalan_sure_lbl

        # Protocol
        self.protokol_listesi = self.protocol_tab.protokol_listesi
        self.open_btn = self.protocol_tab.open_btn
        self.edit_btn = self.protocol_tab.edit_btn
        self.delete_btn = self.protocol_tab.delete_btn
        self.protokol_detay_alani = self.protocol_tab.protokol_detay_alani

        # Built Platform
        self.btn_petri = self.platform_tab.btn_petri
        self.btn_well = self.platform_tab.btn_well
        self.btn_glass = self.platform_tab.btn_glass
        self.bp_buton_grubu = self.platform_tab.bp_buton_grubu
        self.bp_stacked = self.platform_tab.bp_stacked
        self.in_dia = self.platform_tab.in_dia
        self.btn_6 = self.platform_tab.btn_6
        self.btn_12 = self.platform_tab.btn_12
        self.well_grup = self.platform_tab.well_grup
        self.in_size_x = self.platform_tab.in_size_x
        self.in_size_y = self.platform_tab.in_size_y
        self.confirm_platform_btn = self.platform_tab.confirm_platform_btn

        # Model
        self.open_stl_btn = self.model_tab.open_stl_btn
        self.uc_boyutlu_alan = self.model_tab.uc_boyutlu_alan

        # Settings
        self.ph1_btn = self.settings_tab.ph1_btn
        self.ph2_btn = self.settings_tab.ph2_btn
        self.ph3_btn = self.settings_tab.ph3_btn
        self.ph_buton_grubu = self.settings_tab.ph_buton_grubu
        self.ph_type_combo = self.settings_tab.ph_type_combo
        self.kutu_layer = self.settings_tab.kutu_layer
        self.kutu_speed = self.settings_tab.kutu_speed
        self.kutu_grid = self.settings_tab.kutu_grid
        self.kutu_distance = self.settings_tab.kutu_distance
        self.kutu_ph_temp = self.settings_tab.kutu_ph_temp
        self.kutu_plat_temp = self.settings_tab.kutu_plat_temp
        self.bp_info_lbl = self.settings_tab.bp_info_lbl
        self.save_btn = self.settings_tab.save_btn
        self.slice_btn = self.settings_tab.slice_btn
        self.slice_progress = self.settings_tab.slice_progress

        # Preview (vertical layer slider only)
        self.layer_plotter_frame = self.preview_tab.layer_plotter_frame
        self.layer_nav_label = self.preview_tab.layer_nav_label
        self.layer_slider = self.preview_tab.layer_slider   # dikey: katman seçimi
        self.export_gcode_btn = self.preview_tab.export_gcode_btn

        # Print
        self.print_btn = self.print_tab.print_btn
        self.pause_btn = self.print_tab.pause_btn
        self.stop_btn = self.print_tab.stop_btn
        self.elapsed_deger = self.print_tab.elapsed_deger
        self.remaining_deger = self.print_tab.remaining_deger

    # ==========================================================
    # SIGNAL CONNECTIONS
    # ----------------------------------------------------------
    # Intra-tab signals (stacked-page switch, well-grid rebuild) are
    # connected inside the components. Only cross-tab / controller-owned
    # signals are wired here.
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

        # Cross-tab: platform selection updates the Settings info label.
        if self.bp_buton_grubu:
            self.bp_buton_grubu.idClicked.connect(lambda _: self._update_platform_info())

        if self.in_dia:
            self.in_dia.textChanged.connect(lambda _: self._update_platform_info())
        if self.in_size_x:
            self.in_size_x.textChanged.connect(lambda _: self._update_platform_info())
        if self.in_size_y:
            self.in_size_y.textChanged.connect(lambda _: self._update_platform_info())
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

        if self.confirm_platform_btn:
            self.confirm_platform_btn.clicked.connect(self._confirm_platform)

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

        # Preview: the vertical layer slider is the ONLY control. On the RPi4,
        # valueChanged fires on every pixel of a drag; running _show_layer (a full
        # VTK re-render) per tick pins the CPU and stutters the UI. DEBOUNCE: the
        # slider only records a pending index + (re)starts a 150 ms single-shot
        # timer; the render fires once, when the timer times out.
        if self.layer_slider is not None:
            self.slider_debounce_timer = QTimer(self)
            self.slider_debounce_timer.setSingleShot(True)
            self.slider_debounce_timer.setInterval(150)
            self.slider_debounce_timer.timeout.connect(self._on_slider_debounced)
            self.layer_slider.valueChanged.connect(self._on_layer_slider_changed)

        if getattr(self, 'export_gcode_btn', None) is not None:
            self.export_gcode_btn.clicked.connect(self._on_export_gcode)

        # Live sicaklik yonlendirme: spinbox degisikligi (ok tuslari dahil) aninda
        # SET_HEATER_TEMPERATURE olarak Moonraker'a gider — ayri "Set" butonu YOK.
        # Hedef peltier, AKTIF Printhead butonuna (ph_buton_grubu) gore secilir.
        if self.kutu_ph_temp:
            self.kutu_ph_temp.valueChanged.connect(self._on_ph_temp_changed)
        if self.kutu_plat_temp:
            self.kutu_plat_temp.valueChanged.connect(self._on_plat_temp_changed)

        # Printhead butonu degisince (Printhead 1↔2↔3): spinbox degeri AYNI kaldigi
        # icin valueChanged atesleme → yeni peltier eski hedefiyle kalir. Bu yuzden
        # buton degisiminde guncel sicakligi YENI peltier'e elle yeniden gonder.
        if self.ph_buton_grubu:
            self.ph_buton_grubu.idClicked.connect(self._on_ph_group_clicked)

        self._set_print_btn_states(printing=False, paused=False)

    # ==========================================================
    # PAGE SWITCHING
    # ==========================================================
    def _change_page(self, index: int) -> None:
        if self.sayfalar_alani is not None:
            self.sayfalar_alani.setCurrentIndex(index)

    # ==========================================================
    # PROTOCOL LIST HELPERS
    # ==========================================================
    def _refresh_protocol_list(self) -> None:
        if self.protokol_listesi is None:
            return
        self.protokol_listesi.clear()
        for name in sorted(self.kayitli_protokoller.keys()):
            self.protokol_listesi.addItem(name)

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
            QMessageBox.warning(self, "No Selection", "Lütfen açmak için bir protokol seçin.")
            return

        name = item.text()
        record = self.kayitli_protokoller.get(name)
        if not record or not record.get("degerler"):
            QMessageBox.information(self, "Information", f"'{name}' has no saved values.")
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
        elif bp_type == 2:
            parts = d.get("bp_size", "20x60").split("x", 1)
            if self.in_size_x:
                self.in_size_x.setText(parts[0] if parts else "20")
            if self.in_size_y:
                self.in_size_y.setText(parts[1] if len(parts) > 1 else "60")

        if self.kutu_layer:
            self.kutu_layer.setValue(d.get("layer", 0.2))
        if self.kutu_speed:
            self.kutu_speed.setValue(d.get("speed", 10.0))
        if self.kutu_grid:
            self.kutu_grid.setCurrentText(d.get("grid", "Linear"))
        if self.kutu_distance:
            self.kutu_distance.setValue(d.get("distance", 0.2))
        # blockSignals: programatik setValue (protokol yukle/sec) valueChanged'i
        # TETIKLEMESIN — yoksa listede gezinmek/protokol acmak bile isiticilara
        # SET_HEATER_TEMPERATURE gonderirdi. SADECE kullanicinin elle degisikligi gider.
        if self.kutu_ph_temp:
            self.kutu_ph_temp.blockSignals(True)
            self.kutu_ph_temp.setValue(d.get("ph_temp", 27.0))
            self.kutu_ph_temp.blockSignals(False)
        if self.kutu_plat_temp:
            self.kutu_plat_temp.blockSignals(True)
            self.kutu_plat_temp.setValue(d.get("plat_temp", -30.0))
            self.kutu_plat_temp.blockSignals(False)

        self._update_platform_info()

        stl_path = d.get("stl_path", "")
        if stl_path and Path(stl_path).exists():
            self.stl_dosya_yolu = stl_path
            self._show_stl(stl_path)

        self._switch_to_settings()

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
        elif bp_type == 2:
            parts = d.get("bp_size", "20x60").split("x", 1)
            if self.in_size_x:
                self.in_size_x.setText(parts[0] if parts else "20")
            if self.in_size_y:
                self.in_size_y.setText(parts[1] if len(parts) > 1 else "60")

        if self.kutu_layer:
            self.kutu_layer.setValue(d.get("layer", 0.2))
        if self.kutu_speed:
            self.kutu_speed.setValue(d.get("speed", 10.0))
        if self.kutu_grid:
            self.kutu_grid.setCurrentText(d.get("grid", "Linear"))
        if self.kutu_distance:
            self.kutu_distance.setValue(d.get("distance", 0.2))
        # blockSignals: programatik setValue (protokol yukle/sec) valueChanged'i
        # TETIKLEMESIN — yoksa listede gezinmek/protokol acmak bile isiticilara
        # SET_HEATER_TEMPERATURE gonderirdi. SADECE kullanicinin elle degisikligi gider.
        if self.kutu_ph_temp:
            self.kutu_ph_temp.blockSignals(True)
            self.kutu_ph_temp.setValue(d.get("ph_temp", 27.0))
            self.kutu_ph_temp.blockSignals(False)
        if self.kutu_plat_temp:
            self.kutu_plat_temp.blockSignals(True)
            self.kutu_plat_temp.setValue(d.get("plat_temp", -30.0))
            self.kutu_plat_temp.blockSignals(False)

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
        self.dm.delete_from_disk(name)
        self._refresh_protocol_list()

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
        bp_size = f"{self.in_size_x.text() if self.in_size_x else '20'}x{self.in_size_y.text() if self.in_size_y else '60'}"

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
            "model_name": Path(self.stl_dosya_yolu).name if self.stl_dosya_yolu else "Not Selected",
            "stl_path": self.stl_dosya_yolu or "",
        }

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

        detail = self.dm.format_protocol_detail(name, data, bp_text)

        self.kayitli_protokoller[name] = {
            "detay": detail,
            "degerler": deepcopy(data),
        }

        # Disk write failure is surfaced but non-fatal: the in-memory store and
        # UI still update, matching the original behavior.
        try:
            self.dm.save_to_disk(name, self.kayitli_protokoller[name])
        except OSError as e:
            QMessageBox.critical(self, "Save Error", str(e))

        self._refresh_protocol_list()

        if self.protokol_listesi:
            for i in range(self.protokol_listesi.count()):
                if self.protokol_listesi.item(i).text() == name:
                    self.protokol_listesi.setCurrentRow(i)
                    break

        if self.protokol_detay_alani:
            self.protokol_detay_alani.setText(detail)

        self._editing_protocol_name = None

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
            sx = self.in_size_x.text() if self.in_size_x else "?"
            sy = self.in_size_y.text() if self.in_size_y else "?"
            text = f"Glass Slide  |  Size = {sx}x{sy} mm"
        else:
            text = "—"

        self.bp_info_lbl.setText(text)

    # ==========================================================
    # UV STERILIZATION
    # ==========================================================
    # Moonraker HTTP API base (Klipper host runs on the SAME Raspberry Pi).
    _MOONRAKER_URL = "http://127.0.0.1:7125"

    def _send_moonraker_request(self, endpoint: str, payload: dict = None) -> None:
        """POST to Moonraker WITHOUT ever blocking the GUI thread (fire-and-forget).

        A synchronous requests.post() on the GUI thread stalls the Qt event loop
        for up to `timeout` seconds whenever Moonraker is offline or restarting
        (config save / firmware restart) — the exact freeze this project keeps
        designing out. So the blocking call runs on a short-lived DAEMON thread
        and the GUI returns instantly; the 1.5 s timeout merely bounds that
        worker's lifetime so a hung host can't pile up threads.

        Thread safety: the worker touches NO Qt objects — only `requests` and
        `print` — so there is no cross-thread widget access. Callers don't read a
        response; failures are logged, never raised (a missing/restarting
        Moonraker must not pop a modal or crash the app).
        """
        if requests is None:
            print(f"[Moonraker] 'requests' yok → {endpoint} atlandı.")
            return

        url = f"{self._MOONRAKER_URL}{endpoint}"

        def _worker() -> None:
            try:
                requests.post(url, json=payload, timeout=1.5)
            except requests.exceptions.RequestException as exc:
                # Offline / restarting / timeout — beklenen, sessizce yut + logla.
                print(f"[Moonraker] POST {endpoint} başarısız (offline/restart?): {exc}")
            except Exception as exc:
                # Beklenmeyen — yine de GUI'ye sızdırma, yalnızca logla.
                print(f"[Moonraker] POST {endpoint} beklenmeyen hata: {exc}")

        threading.Thread(target=_worker, daemon=True, name="moonraker-post").start()

    def _send_uv_command(self, state: bool) -> None:
        # START_UV / STOP_UV Klipper makrosu — Moonraker gcode/script üzerinden.
        self._send_moonraker_request(
            "/printer/gcode/script",
            {"script": "START_UV" if state else "STOP_UV"},
        )

    def _send_hepa_command(self, state: bool) -> None:
        # START_HEPA / STOP_HEPA Klipper makrosu.
        self._send_moonraker_request(
            "/printer/gcode/script",
            {"script": "START_HEPA" if state else "STOP_HEPA"},
        )

    def _on_ph_temp_changed(self, value: float) -> None:
        """Printhead Temperature spinbox → AKTIF peltier'e canli sicaklik hedefi.

        Hangi peltier? ph_buton_grubu'nun checked id'si (Printhead 1/2/3 → grup id
        1/2/3 → peltier_1/2/3). Hicbiri secili degilse (-1) guvenli varsayilan
        peltier_1. Fire-and-forget; GUI'yi asla bloklamaz.
        """
        ph_id = self.ph_buton_grubu.checkedId() if self.ph_buton_grubu else 1
        target = {1: "peltier_1", 2: "peltier_2", 3: "peltier_3"}.get(ph_id, "peltier_1")
        self._send_moonraker_request(
            "/printer/gcode/script",
            {"script": f"SET_HEATER_TEMPERATURE HEATER={target} TARGET={value:.1f}"},
        )

    def _on_plat_temp_changed(self, value: float) -> None:
        """Platform Temperature spinbox → yatak sogutucu (temperature_fan bed_cooler) hedefi."""
        self._send_moonraker_request(
            "/printer/gcode/script",
            {"script": f"SET_TEMPERATURE_FAN_TARGET temperature_fan=bed_cooler target={value:.1f}"},
        )

    def _on_ph_group_clicked(self, checked_id: int) -> None:
        """Printhead butonu degisince guncel spinbox sicakligini YENI peltier'e gonder.

        checked_id = ph_buton_grubu'nun yeni secili buton id'si (1/2/3 →
        peltier_1/2/3); bilinmeyen id guvenli varsayilan peltier_1. Spinbox yoksa
        (kutu_ph_temp None) sessizce cik. Fire-and-forget; GUI'yi bloklamaz.
        Map + format, _on_ph_temp_changed ile bilerek ayni tutuldu.
        """
        if self.kutu_ph_temp is None:
            return
        target = {1: "peltier_1", 2: "peltier_2", 3: "peltier_3"}.get(checked_id, "peltier_1")
        value = self.kutu_ph_temp.value()
        self._send_moonraker_request(
            "/printer/gcode/script",
            {"script": f"SET_HEATER_TEMPERATURE HEATER={target} TARGET={value:.1f}"},
        )

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

    # ==========================================================
    # PLATFORM CONFIRM
    # ==========================================================
    def _confirm_platform(self) -> None:
        self._update_platform_info()
        if self.buton_grubu:
            btn = self.buton_grubu.button(3)
            if btn:
                btn.setChecked(True)
        self._change_page(3)

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
                    "QFrame{background:#EBEBEB;border:2px solid #BDBDBD;border-radius:6px;}"
                )
                self.plotter = QtInteractor(self.uc_boyutlu_alan)
                self.uc_boyutlu_alan.layout().addWidget(self.plotter.interactor)

            # Eski sahneyi ve TÜM slice/preview durumunu temizle: aksi halde
            # önceki modelin ghost'u, cache'leri ve VTK aktörleri bellekte kalır.
            self.plotter.clear()
            try:
                self.plotter.clear_actors()
            except Exception:
                pass
            self._slices = []
            self._layer_meshes = []
            self._infills = []
            self._all_layers_mesh = None
            self._original_mesh = None
            self._current_layer_idx = 0
            self._last_plate_size = None
            # Önizleme durumunu sıfırla: yeni model dilimlenene kadar eski
            # slider/önbellek kalmasın.
            self._render_last_idx = -1
            if getattr(self, 'layer_slider', None) is not None:
                self.layer_slider.blockSignals(True)
                self.layer_slider.setMaximum(0)
                self.layer_slider.setValue(0)
                self.layer_slider.blockSignals(False)
            self.plotter.set_background("#F0F0F0")

            # Dosya hâlâ var mı? (seçimden sonra taşınmış/silinmiş olabilir)
            p = Path(path)
            if not p.exists():
                raise FileNotFoundError(f"File not found:\n{path}")
            # Çok büyük dosyalar için yumuşak uyarı (OOM riski) — yine de devam.
            try:
                size_mb = p.stat().st_size / (1024 * 1024)
            except OSError:
                size_mb = 0
            if size_mb > 200:
                reply = QMessageBox.question(
                    self, "Large File",
                    f"This STL is {size_mb:.0f} MB and may be slow or run out of memory.\n"
                    "Load it anyway?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

            # STL'yi oku
            mesh = pv.read(path)
            if mesh is None or getattr(mesh, 'n_points', 0) == 0:
                raise ValueError("STL file contains no geometry")

            # Modeli X,Y'de ortala, Z'de altını platforma (Z=0) oturt
            z_min = mesh.bounds[4]
            center_xy = mesh.center[:2]
            mesh = mesh.translate((-center_xy[0], -center_xy[1], -z_min))

            # --- BASKI TABLASI (Cura stili) ---
            plate_size = 150

            self.plotter.add_mesh(
                pv.Plane(center=(0, 0, -0.05), direction=(0, 0, 1),
                         i_size=plate_size, j_size=plate_size),
                color="#F8F8F8", show_edges=False, lighting=False,
            )
            build_platform_grid(self.plotter, plate_size, z_grid=0.01)

            # --- MODEL ---
            self.plotter.add_mesh(
                mesh,
                color="#29b6f6",
                show_edges=False,
                edge_color="#01579b",
                lighting=True,
                smooth_shading=False,
                specular=0.20,
                specular_power=32,
                ambient=0.6,
                diffuse=0.7,
            )

            # --- AYDINLATMA ---
            self.plotter.remove_all_lights()
            headlight = pv.Light(light_type='headlight')
            headlight.intensity = 0.85
            self.plotter.add_light(headlight)

            # --- EKSENLER ---
            axis_origin = [-plate_size / 2, -plate_size / 2, 0]
            build_axis_arrows(self.plotter, axis_origin, length=20)

            # Eksen etiketleri
            label_pts = [
                [axis_origin[0] + 26, axis_origin[1], axis_origin[2]],
                [axis_origin[0], axis_origin[1] + 26, axis_origin[2]],
                [axis_origin[0], axis_origin[1], axis_origin[2] + 26],
            ]
            for pt, lbl, clr in zip(label_pts, ["X", "Y", "Z"],
                                    ["#F44336", "#4CAF50", "#2196F3"]):
                self.plotter.add_point_labels(
                    [pt], [lbl], text_color=clr, font_size=12,
                    bold=True, point_size=0, shape=None, always_visible=True,
                )

            # --- KAMERA ---
            self.plotter.camera_position = 'iso'
            self.plotter.reset_camera()
            try:
                self.plotter.camera.elevation = 22
            except Exception:
                pass
            self.plotter.camera.zoom(1.15)
            try:
                self.plotter.reset_camera_clipping_range()
            except Exception:
                pass

        except Exception as e:
            QMessageBox.critical(self, "STL Error", f"Could not load STL:\n{e}")

    # ==========================================================
    # SLICE
    # ==========================================================
    def _slice_model(self) -> None:
        # Re-entrancy guard: ignore a second request while one is in flight.
        # (slice_btn is disabled during slicing, but this hardens other paths.)
        if self._slicing:
            return

        if pv is None or QtInteractor is None:
            QMessageBox.warning(self, "Eksik Kütüphane", "pyvista ve pyvistaqt gerekli.")
            return

        if not self.stl_dosya_yolu:
            QMessageBox.warning(self, "Model Yok",
                "Lütfen önce Model sekmesinden bir STL dosyası yükleyin.")
            return

        # Dosya seçildikten sonra taşınmış/silinmiş olabilir.
        if not Path(self.stl_dosya_yolu).exists():
            QMessageBox.warning(self, "Model Yok",
                f"STL dosyası bulunamadı:\n{self.stl_dosya_yolu}")
            return

        layer_h = self.kutu_layer.value() if self.kutu_layer else 0.2
        if layer_h <= 0:
            QMessageBox.warning(self, "Hata", "Katman kalınlığı 0'dan büyük olmalı.")
            return

        if self.slice_btn:
            self.slice_btn.setEnabled(False)

        # NOTE: Ghost mesh (_original_mesh) is NO LONGER read here. Doing pv.read +
        # translate on the GUI thread froze the UI on large STLs. The worker now
        # reads, centers, deep-copies the mesh and returns it via finished(...);
        # _on_slice_done assigns it. Reset to None until the worker delivers it.
        self._original_mesh = None

        # Retire the previous worker/thread WITHOUT blocking the GUI thread.
        # The _slicing guard above guarantees no slice is in flight here, so the
        # prior thread has already finished and self-cleans via finished->
        # deleteLater. We keep a transient reference so a still-finishing thread
        # is never GC'd mid-run, but we NEVER call quit()/wait() on the GUI
        # thread — that blocking wait was the freeze (measured ~2 s on a live
        # thread). Finished threads are pruned cheaply via isRunning().
        if self._slice_thread is not None:
            self._retired_threads.append((self._slice_worker, self._slice_thread))
        self._slice_worker = None
        self._slice_thread = None
        self._retired_threads = [
            (wk, th) for (wk, th) in self._retired_threads if _thread_alive(th)
        ]

        self._slices = []
        self._layer_meshes = []
        self._infills = []
        self._all_layers_mesh = None

        # Thread kurulumunu da koru: moveToThread/connect nadiren de olsa
        # hata verirse buton kilitli kalmasın.
        try:
            self._slice_thread = QThread()
            distance = self.kutu_distance.value() if self.kutu_distance else 1.0
            self._slice_worker = SliceWorker(self.stl_dosya_yolu, layer_h, distance)
            self._slice_worker.moveToThread(self._slice_thread)

            self._slice_thread.started.connect(self._slice_worker.run)
            self._slice_worker.finished.connect(self._on_slice_done)
            self._slice_worker.error.connect(self._on_slice_error)
            self._slice_worker.progress.connect(self.slice_progress.setValue)
            self._slice_worker.finished.connect(self._slice_thread.quit)
            self._slice_worker.error.connect(self._slice_thread.quit)
            self._slice_thread.finished.connect(self._slice_worker.deleteLater)
            self._slice_thread.finished.connect(self._slice_thread.deleteLater)

            # Show + reset the progress bar as the thread starts.
            if self.slice_progress is not None:
                self.slice_progress.setValue(0)
                self.slice_progress.setVisible(True)

            self._slicing = True
            self._slice_thread.start()
        except Exception as exc:
            self._slicing = False
            self._slice_thread = None
            self._slice_worker = None
            if self.slice_btn:
                self.slice_btn.setEnabled(True)
            if self.slice_progress is not None:
                self.slice_progress.setVisible(False)
            QMessageBox.critical(self, "Slice Hatası", f"Thread başlatılamadı:\n{exc}")

    def _on_slice_done(self, slices: list, layer_meshes: list, infills: list,
                       centered_original_mesh: object = None) -> None:
        # Kapanış sırasında kuyrukta kalmış bir sinyal: silinmiş widget'lara dokunma.
        if self._closing:
            return
        # Tüm gövde korumalı: bir slot istisnası PyQt6'da uygulamayı abort ettirir.
        try:
            # 1. Sonuçları sakla
            self._slices            = slices
            self._layer_meshes      = layer_meshes
            self._infills           = infills
            # Ghost mesh artık worker thread'inde okunup deep-copy ile geliyor
            # (GUI donmasını önlemek için) — ana thread'de pv.read YOK.
            self._original_mesh     = centered_original_mesh
            self._current_layer_idx = 0
            self._last_plate_size   = None  # static sahneyi yeni slice için sıfırla
            n_layers                = len(slices)

            # 2. Sekme geçişini ÖNCE yap → OpenGL bağlamı aktifleşsin
            if self.preview_btn:
                self.preview_btn.setChecked(True)
            self._change_page(5)

            # 3. Plotter hazırla (bağlam artık canlı)
            self._init_settings_plotter()

            layer_h = self.kutu_layer.value() if self.kutu_layer else 0.2
            print(f"Slice tamamlandı: {n_layers} katman, layer_h={layer_h} mm")

            # 4. RPi4: kümülatif "alttaki katmanlar" önbelleği KALDIRILDI (OOM).
            #    Önizleme yalnızca aktif katman + infill + ghost çizer.
            self._render_last_idx = -1

            # 5. Dikey katman slider'ı: Layer 1'de (value 0, invertedAppearance
            #    ile EN ALTTA) başlar. Sinyalleri bloklayarak kur.
            top = max(0, n_layers - 1)
            if self.layer_slider is not None:
                self.layer_slider.blockSignals(True)
                self.layer_slider.setMinimum(0)
                self.layer_slider.setMaximum(top)
                self.layer_slider.setValue(0)      # Layer 1 (alt)
                self.layer_slider.blockSignals(False)
            self._current_layer_idx = 0

            # 6. GL penceresi hazırlanması için 150 ms bekle, sonra ilk katmanı göster.
            QTimer.singleShot(150, lambda: self._show_layer(0))
        except Exception as exc:
            print(f"[WARN] _on_slice_done hata: {exc}")
            QMessageBox.critical(self, "Preview Hatası",
                                 f"Katman önizlemesi hazırlanamadı:\n{exc}")
        finally:
            # Buton ve durum her hâlükârda sıfırlansın (kilit kalmasın).
            self._slicing = False
            if self.slice_btn:
                self.slice_btn.setEnabled(True)
            if self.slice_progress is not None:
                self.slice_progress.setVisible(False)

    def _on_slice_error(self, msg: str) -> None:
        self._slicing = False
        if self.slice_btn:
            self.slice_btn.setEnabled(True)
        if self.slice_progress is not None:
            self.slice_progress.setVisible(False)
        if self._closing:
            return
        QMessageBox.critical(self, "Slice Hatası", msg)

    # ==========================================================
    # PRINT
    # ==========================================================
    def _set_print_btn_states(self, printing: bool, paused: bool) -> None:
        if self.print_btn:
            self.print_btn.setEnabled(not printing)
            self.print_btn.setText("Resume" if paused else "Print")
        if self.pause_btn:
            self.pause_btn.setEnabled(printing)
        if self.stop_btn:
            self.stop_btn.setEnabled(printing or paused)

    def _update_print_display(self) -> None:
        e_m, e_s = divmod(self._print_elapsed, 60)
        r_m, r_s = divmod(max(0, self._print_total - self._print_elapsed), 60)
        if self.elapsed_deger:
            self.elapsed_deger.setText(f"{e_m:02d}:{e_s:02d} min")
        if self.remaining_deger:
            self.remaining_deger.setText(f"{r_m:02d}:{r_s:02d} min")

    def _print_tick(self) -> None:
        self._print_elapsed += 1
        if self._print_elapsed >= self._print_total:
            self._stop_print()
            return
        self._update_print_display()

    def _start_print(self) -> None:
        if self.print_timer is None:
            self.print_timer = QTimer(self)
            self.print_timer.timeout.connect(self._print_tick)

        # The same print_btn doubles as RESUME while paused (its label is "Resume").
        resuming = self._print_paused
        if resuming:
            # Resume the paused Klipper print.
            self._send_moonraker_request("/printer/print/resume")
        else:
            # Fresh start: reset the local timer.
            self._print_elapsed = 0
            self._print_total   = 3600
            # TODO (Phase 2.x): replace with real G-code generation + a true print
            # start (SDCARD_PRINT_FILE / printer.print.start). Placeholder macro for
            # now — just flashes a message on the printer's display.
            self._send_moonraker_request(
                "/printer/gcode/script",
                {"script": "M117 G-Code generator not yet implemented"},
            )

        self._print_paused = False
        self._update_print_display()
        self._set_print_btn_states(printing=True, paused=False)
        self.print_timer.start(1000)

    def _pause_print(self) -> None:
        if self.print_timer and self.print_timer.isActive():
            self.print_timer.stop()
        self._print_paused = True
        self._set_print_btn_states(printing=False, paused=True)
        # Update the UI instantly (above), then dispatch the pause to Klipper.
        self._send_moonraker_request("/printer/print/pause")

    def _stop_print(self) -> None:
        if self.print_timer and self.print_timer.isActive():
            self.print_timer.stop()
        self._print_elapsed = 0
        self._print_paused  = False
        self._update_print_display()
        self._set_print_btn_states(printing=False, paused=False)
        # Cancel the running Klipper print (Moonraker maps cancel → CANCEL_PRINT).
        self._send_moonraker_request("/printer/print/cancel")

    # ==========================================================
    # PROTOCOL LOAD (disk -> memory -> list)
    # ==========================================================
    def _load_protocols(self) -> None:
        """Load JSON protocols via DataManager, then populate the list widget."""
        # DataManager mutates self.kayitli_protokoller in place (same dict object).
        self.dm.load_protocols()

        # Listeyi burada doldur - sinyaller henüz bağlı değilse manuel detay göster
        if self.protokol_listesi is not None:
            self.protokol_listesi.clear()
            for name in sorted(self.kayitli_protokoller.keys()):
                self.protokol_listesi.addItem(name)

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

    # ==========================================================
    # LAYER PREVIEW
    # ==========================================================
    def _add_filament(self, plotter, line_mesh, radius: float, color: str, name: str) -> None:
        """Render a line PolyData as FLAT colored lines (RPi4-safe).

        The Pi 4's V3D driver has NO geometry-shader path, so VTK's shader tube
        rendering (``render_lines_as_tubes``) and PBR/specular lighting fall back
        and crash / lose the GL context. We draw plain wide lines: ``line_width``
        + flat ``color`` with lighting OFF. This is still immune to the
        disconnected 2-point "line soup" that our vectorized scanline infill and
        contour bucketing emit (no geometry is rebuilt), and costs the GPU almost
        nothing.
        """
        # Walls (radius ≥ 0.2) draw a touch wider than infill. Kept modest: the
        # V3D driver clamps aliased line width hard, and thin lines alias less.
        lw = 2.5 if radius >= 0.2 else 1.5
        try:
            plotter.add_mesh(
                line_mesh,
                color=color,
                line_width=lw,
                lighting=False,
                name=name,
                render=False,
            )
        except Exception:
            # Last-resort plain add: an uncaught exception in a Qt slot aborts the
            # whole app under PyQt6 (qFatal), so degrade a bad frame to a no-op.
            try:
                plotter.add_mesh(line_mesh, color=color, name=name, render=False)
            except Exception:
                pass

    # Completed-layer "Line Type" colors (the active layer uses bright literals).
    _C_WALL_DONE = '#C92A2A'   # printed walls (darker red — depth cue)
    _C_FILL_DONE = '#E8590C'   # printed infill (darker orange)
    _C_BASE      = '#A5D8FF'   # first-layer footprint cap (light blue)

    def _show_layer(self, idx: int) -> None:
        # Guarded entry point: an uncaught exception in a Qt slot aborts the whole
        # app under PyQt6 (qFatal). All VTK work lives in _render_layer, wrapped so
        # a single bad frame degrades to a warning instead of a crash.
        if self._closing or not self._slices or self.layer_plotter is None:
            return
        try:
            self._render_layer(idx)
        except Exception as exc:
            print(f"[WARN] _show_layer({idx}) atlandı: {exc}")

    def _render_layer(self, idx: int) -> None:
        """Instant FULL-layer render, driven ONLY by the vertical layer slider.

        RPi4 (2 GB): NO cumulative "printed below" cache. Each frame draws only
        the active layer ``idx`` in full (perimeter + infill) plus the faint
        full-model ghost and a cheap first-layer base cap. No intra-layer
        simulation / partial toolpath / nozzle.
        """
        idx        = max(0, min(idx, len(self._slices) - 1))
        self._current_layer_idx = idx
        plotter    = self.layer_plotter
        layer_h    = self.kutu_layer.value() if self.kutu_layer else 0.2
        n_layers   = len(self._slices)
        if n_layers == 0:
            return
        total_h    = n_layers * layer_h
        plate_size = max(150.0, total_h * 2.0)

        # ── STATIC SCENE: rebuild only when plate_size changes ──────────────
        if getattr(self, '_last_plate_size', None) != plate_size:
            plotter.add_mesh(
                pv.Plane(center=(0, 0, -0.1), direction=(0, 0, 1),
                         i_size=plate_size, j_size=plate_size),
                color='#FFFFFF', opacity=1.0,
                show_edges=False, lighting=False,
                name='lp_platform', render=False,
            )
            build_platform_grid(plotter, plate_size, z_grid=0.01,
                                name_prefix='lp_')
            build_axis_arrows(
                plotter, [-plate_size / 2, -plate_size / 2, 0.0],
                length=20.0, name_prefix='lp_', render=False,
            )
            plotter.camera_position = [
                (plate_size * 1.2, -plate_size * 1.2, total_h + plate_size * 0.8),
                (0.0, 0.0, total_h / 2.0),
                (0.0, 0.0, 1.0),
            ]
            plotter.reset_camera()
            plotter.camera.zoom(1.1)
            try:
                plotter.reset_camera_clipping_range()
            except Exception:
                pass
            self._last_plate_size = plate_size
            self._render_last_idx = -1   # scene reset → force completed-layer redraw

        # ── STATIC CONTEXT — ghost + base cap, refreshed only on layer change ──
        # RPi4 (2 GB): the cumulative "printed below" cache is GONE. It merged
        # every prior layer's perimeter+infill into ever-growing PolyData
        # (hundreds of MB → OOM kernel kill). We draw ONLY the faint full-model
        # ghost plus a cheap first-layer footprint cap; the active layer is drawn
        # in full below.
        if idx != self._render_last_idx:
            for _n in ('ghost', 'prev_p', 'prev_i', 'base_cap'):
                _a = plotter.actors.get(_n)
                if _a is not None:
                    plotter.remove_actor(_a, render=False)

            # 1. Ghost of the full model (very faint, for context).
            if self._original_mesh is not None:
                plotter.add_mesh(
                    self._original_mesh,
                    color='#B0BEC5', opacity=0.06,
                    show_edges=False, lighting=True, smooth_shading=True,
                    name='ghost', render=False,
                )
            # 2. First-layer footprint cap (build-plate adhesion hint). A flat
            #    bounding-box plane from the slice bounds — NOT delaunay_2d(),
            #    whose triangulation on the GUI thread froze the UI on Layer 1.
            base = self._slices[0] if self._slices else None
            if base is not None and getattr(base, 'n_points', 0) > 0:
                try:
                    xmin, xmax, ymin, ymax, _zlo, _zhi = base.bounds
                    plotter.add_mesh(
                        pv.Plane(
                            center=((xmin + xmax) / 2.0, (ymin + ymax) / 2.0, 0.015),
                            direction=(0, 0, 1),
                            i_size=max(1e-3, xmax - xmin),
                            j_size=max(1e-3, ymax - ymin),
                        ),
                        color=self._C_BASE, opacity=0.35,
                        show_edges=False, lighting=False,
                        name='base_cap', render=False,
                    )
                except Exception:
                    pass
            self._render_last_idx = idx

        # ── 3. ACTIVE LAYER (full, instant) ─────────────────────────────────
        # Clear the previous active actors first so an empty layer doesn't leave
        # the prior layer's lines on screen (add_mesh(name=...) replaces in place).
        for _n in ('active_perimeter', 'infill_v'):
            _a = plotter.actors.get(_n)
            if _a is not None:
                plotter.remove_actor(_a, render=False)

        # Symmetric bounds guards: _layer_meshes / _slices / _infills are always
        # built to the SAME length (n_layers) in the worker, so idx (clamped to
        # len(_slices)-1) is in range — but guard every indexed access anyway so a
        # future partial/desynced state degrades to an empty frame, never IndexError.
        active = self._layer_meshes[idx] if idx < len(self._layer_meshes) else None
        slc    = self._slices[idx] if idx < len(self._slices) else None

        if active is not None and getattr(active, 'n_points', 0) > 0:
            if slc is not None and slc.n_points > 0:
                self._add_filament(plotter, slc, 0.2, '#FF0000', 'active_perimeter')

            infill = self._infills[idx] if hasattr(self, '_infills') and idx < len(self._infills) else None
            if infill is not None and getattr(infill, 'n_points', 0) > 0:
                self._add_filament(plotter, infill, 0.15, '#FF8C00', 'infill_v')

        # ── SINGLE RENDER CALL ──────────────────────────────────────────────
        if self.layer_plotter.interactor.isVisible():
            plotter.render()

        # ── UI SYNC ─────────────────────────────────────────────────────────
        if self.layer_nav_label:
            self.layer_nav_label.setText(f"{idx + 1} / {n_layers}")
        if self.layer_slider is not None and self.layer_slider.value() != idx:
            self.layer_slider.blockSignals(True)
            self.layer_slider.setValue(idx)
            self.layer_slider.blockSignals(False)

    def _on_layer_slider_changed(self, value: int) -> None:
        # Vertical slider → choose the active (top) layer. DEBOUNCED: only record
        # the target and (re)start the 150 ms window; a fast drag on the RPi4 thus
        # queues exactly ONE render (on timeout) instead of one per pixel.
        self._pending_layer_idx = value
        if self.slider_debounce_timer is not None:
            self.slider_debounce_timer.start()   # restart the 150 ms window
        else:
            self._show_layer(value)

    def _on_slider_debounced(self) -> None:
        # Debounce window elapsed → render the layer the user finally landed on.
        self._show_layer(self._pending_layer_idx)

    def _on_export_gcode(self) -> None:
        """Export the sliced model to a continuous-extrusion G-code file."""
        if not self._slices:
            QMessageBox.warning(self, "G-Code Yok",
                                "Önce Settings sekmesinden bir model dilimleyin.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "G-Code Dışa Aktar", "output.gcode", "G-Code (*.gcode)")
        if not path:
            return

        layer_h = self.kutu_layer.value() if self.kutu_layer else 0.2

        # RPi4: generate_gcode streams to disk and is numpy-vectorised, so it is
        # fast for realistic bioprint models. Rather than a full worker thread,
        # this deliberate one-off action shows a wait cursor + disables the button
        # so the GUI stays honest while it runs.
        self.export_gcode_btn.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            moves = generate_gcode(self._slices, self._infills, path,
                                   layer_height=layer_h)
        except Exception as exc:
            QMessageBox.critical(self, "Export Hatası",
                                 f"G-Code üretilemedi:\n{exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()
            self.export_gcode_btn.setEnabled(True)

        QMessageBox.information(
            self, "G-Code Hazır",
            f"G-Code kaydedildi:\n{path}\n\n"
            f"{moves} ekstrüzyon hamlesi · {len(self._slices)} katman.")

    def _init_settings_plotter(self) -> None:
        if pv is None or QtInteractor is None or self.layer_plotter_frame is None:
            return

        # Create exactly once — preserve VTK context on subsequent calls.
        if self.layer_plotter is not None:
            return

        # QtInteractor oluşturma GL bağlamı yoksa patlayabilir; çökme yerine
        # önizlemeyi devre dışı bırak.
        try:
            lay = self.layer_plotter_frame.layout()
            if lay is None:
                lay = QVBoxLayout(self.layer_plotter_frame)
                lay.setContentsMargins(0, 0, 0, 0)

            self.layer_plotter = QtInteractor(self.layer_plotter_frame)
            lay.addWidget(self.layer_plotter.interactor)

            # LIGHT viewport background (matches the app + Model tab). NO MSAA —
            # prevents VTK Windows Error 2004.
            self.layer_plotter.set_background("#F5F5F5")
            self.layer_plotter.camera_position = [(200, -200, 250), (0, 0, 0), (0, 0, 1)]
            self.layer_plotter.reset_camera()
        except Exception as exc:
            self.layer_plotter = None
            print(f"[WARN] Layer plotter oluşturulamadı: {exc}")
            QMessageBox.warning(self, "3D Önizleme",
                                f"Katman önizlemesi başlatılamadı:\n{exc}")
