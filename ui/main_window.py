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
import time
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
from PyQt6.QtCore import QTimer, QThread, Qt, QObject, pyqtSignal

from core.viewport import (pv, QtInteractor, build_platform_grid, build_axis_arrows,
                           build_container_reference, CONTAINER_DEFAULTS, well_centers)
from ui.styles import DIALOG_STYLE
from core.slicer_worker import SliceWorker
from core.gcode_exporter import generate_gcode, generate_gcode_multi_origin
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


class _MoonrakerBridge(QObject):
    """R1: Arka plan POST thread'lerinden GUI thread'ine GUVENLI hata koprusu.

    Sinyaller Python daemon thread'lerinden emit edilir; alici slotlar
    (KlipperArayuzu) GUI thread'inde yasadigi icin Qt baglantiyi otomatik
    QUEUED yapar — slot HER ZAMAN GUI thread'inde kosar, widget'lara dokunmak
    guvenlidir. Worker'lar widget'lara ASLA dogrudan dokunmaz.
    """
    failed = pyqtSignal(str)     # teslim edilemedi / reddedildi → kirmizi banner
    recovered = pyqtSignal(str)  # yeniden denemede basari → yesil bilgi banner'i
    polled = pyqtSignal(object)  # R3: 5 sn'lik durum yoklamasi sonucu (dict)
    # G-code upload sonucu (arka plan thread'den GUI'ye): (success, filename_or_empty, message)
    gcode_upload_finished = pyqtSignal(bool, str, str)


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

        # R3: durum yoklamasini durdur (yikim sirasinda yeni tick gelmesin;
        # ucustaki son yoklama _closing kontrolune takilip zararsiz duser).
        if getattr(self, '_status_timer', None) is not None:
            try:
                self._status_timer.stop()
            except Exception:
                pass

        # Worker sinyallerini önce kopar: quit/wait sırasında kuyruğa düşmüş bir
        # finished/error, ana thread'de yıkım anında slot tetiklemesin.
        if getattr(self, '_slice_worker', None) is not None:
            try:
                self._slice_worker.finished.disconnect()
                self._slice_worker.error.disconnect()
                self._slice_worker.aborted.disconnect()
            except Exception:
                pass

        # İşbirlikçi iptal isteği: worker bayrağı blok sınırında görüp ms içinde
        # döner → aşağıdaki 5 sn'lik wait/terminate() son çaresine pratikte hiç
        # düşülmez (terminate RPi4'te yarım VTK/GIL durumu bırakabilir).
        workers = [getattr(self, '_slice_worker', None)]
        workers.extend(wk for (wk, _th) in getattr(self, '_retired_threads', []))
        for _wk in workers:
            if _wk is not None:
                try:
                    _wk.request_abort()
                except Exception:
                    pass   # deleteLater ile C++ tarafı silinmiş olabilir

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

    def keyPressEvent(self, event) -> None:
        """Kiosk/tam ekran kisayollari: Esc -> tam ekrandan cik, F11 -> toggle.

        Esc yalnizca tam ekrandayken etkilidir (PC'de test kolayligi); diger tuslar
        normal isleyise (super) gider.
        """
        key = event.key()
        if key == Qt.Key.Key_Escape and self.isFullScreen():
            self.showNormal()
            return
        if key == Qt.Key.Key_F11:
            self.showNormal() if self.isFullScreen() else self.showFullScreen()
            return
        super().keyPressEvent(event)

    # ==========================================================
    # R1: NON-MODAL ALERT BANNER (Moonraker teslim uyarilari)
    # ==========================================================
    _BANNER_CSS = {
        "error": ("QLabel { background:#D32F2F; color:white; font-size:14px; "
                  "font-weight:bold; padding:8px 12px; border-radius:6px; }"),
        "info":  ("QLabel { background:#2E7D32; color:white; font-size:14px; "
                  "font-weight:bold; padding:8px 12px; border-radius:6px; }"),
    }

    def _on_moonraker_failed(self, msg: str) -> None:
        self._show_banner(msg, kind="error")

    def _on_moonraker_recovered(self, msg: str) -> None:
        self._show_banner(msg, kind="info")

    def _show_banner(self, msg: str, kind: str = "error") -> None:
        """Ust kenarda suzulen, MODAL OLMAYAN uyari seridi (kiosk-guvenli).

        * Dokunuslara SEFFAF (WA_TransparentForMouseEvents) → altindaki hicbir
          kontrolu asla bloklamaz; kapanma otomatiktir (error 12 sn, info 4 sn;
          yeni mesaj sureyi bastan baslatir).
        * YALNIZCA GUI thread'inden cagrilmalidir — bridge sinyalleri zaten
          queued gelir; worker thread'ler bu metodu dogrudan CAGIRMAZ.
        """
        if self._closing:
            return
        if self._alert_banner is None:
            self._alert_banner = QLabel(self)
            self._alert_banner.setWordWrap(True)
            self._alert_banner.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self._banner_timer = QTimer(self)
            self._banner_timer.setSingleShot(True)
            self._banner_timer.timeout.connect(self._hide_banner)
        self._alert_banner.setStyleSheet(
            self._BANNER_CSS.get(kind, self._BANNER_CSS["error"]))
        self._alert_banner.setText(msg)
        self._position_banner()
        self._alert_banner.setVisible(True)
        self._alert_banner.raise_()
        self._banner_timer.start(12000 if kind == "error" else 4000)

    def _hide_banner(self) -> None:
        if self._alert_banner is not None:
            self._alert_banner.setVisible(False)

    def _position_banner(self) -> None:
        if self._alert_banner is None:
            return
        margin = 12
        self._alert_banner.setFixedWidth(max(100, self.width() - 2 * margin))
        self._alert_banner.adjustSize()
        self._alert_banner.move(margin, 8)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._alert_banner is not None and self._alert_banner.isVisible():
            self._position_banner()

    # ==========================================================
    # R3: PRINTER STATUS POLL  +  R4: MOTION PRE-FLIGHT
    # ==========================================================
    def _poll_printer_status(self) -> None:
        """R3: Moonraker durum yoklamasi — GUI'yi ASLA bloklamaz.

        TEK GET ile uc nesne: webhooks (Klipper ready mi), print_stats
        (state + print_duration) ve toolhead.homed_axes. Blocking istek daemon
        thread'de kosar; sonuc bridge.polled (queued) ile GUI'ye doner. Ust uste
        binme _poll_inflight ile onlenir (timeout 1.5 s < 5 s kadans; bayrak
        sonuc slotunda dusurulur).
        """
        if requests is None or self._closing or self._poll_inflight:
            return
        self._poll_inflight = True
        bridge = self._moonraker_bridge
        url = (f"{self._MOONRAKER_URL}/printer/objects/query"
               "?webhooks&print_stats=state,print_duration&toolhead=homed_axes")

        def _worker() -> None:
            st = {"online": False, "ready": False, "homed": "",
                  "print_state": "", "print_duration": None, "detail": ""}
            try:
                resp = requests.get(url, timeout=1.5)
                if resp.status_code == 200:
                    data = resp.json().get("result", {}).get("status", {})
                    wh = data.get("webhooks") or {}
                    ps = data.get("print_stats") or {}
                    th = data.get("toolhead") or {}
                    st.update(
                        online=True,
                        ready=(str(wh.get("state", "")) == "ready"),
                        homed=str(th.get("homed_axes", "") or ""),
                        print_state=str(ps.get("state", "") or ""),
                        print_duration=ps.get("print_duration"),
                        detail=str(wh.get("state_message", "") or "")[:200],
                    )
                else:
                    # Moonraker ayakta ama Klipper bagli/hazir degil (tipik 503).
                    st.update(online=True, ready=False,
                              detail=f"HTTP {resp.status_code}")
            except Exception as exc:          # RequestException + JSON hatalari
                st["detail"] = str(exc)[:200]
            try:
                bridge.polled.emit(st)        # kapanis yarisina karsi sarili
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True, name="moonraker-poll").start()

    def _on_printer_status(self, st: dict) -> None:
        """R3 (GUI thread'i): yoklama sonucu → durum alanlari + gosterge + butonlar."""
        self._poll_inflight = False
        if self._closing or not isinstance(st, dict):
            return
        self._moonraker_online = bool(st.get("online"))
        self._klippy_ready = bool(st.get("ready"))
        self._homed_axes = st.get("homed") or ""
        if self._klippy_ready:
            self._print_state = st.get("print_state") or ""
        conn = ("ready" if self._klippy_ready
                else ("not-ready" if self._moonraker_online else "offline"))
        if conn != self._last_conn_state:   # konsola yalnizca DEGISIMDE yaz
            print(f"[R3-POLL] durum={conn} homed='{self._homed_axes}' "
                  f"print='{self._print_state}' detay='{st.get('detail', '')}'")
            self._last_conn_state = conn
        self._update_conn_label()
        # Buton/sayac gercegi YALNIZCA saglikli veriyle surulur; offline ya da
        # Klipper hazir degilken DOKUNMA (dev makinesi + Moonraker restart'i
        # lokal durumu bozmasin). Kullanici eyleminden sonraki 2.5 sn'lik
        # iyimser pencerede de dokunma: eylemden ONCE yola cikmis bir yoklama
        # sonucu butonlari geri cevirip titretmesin.
        if self._klippy_ready and \
                (time.monotonic() - self._last_print_action_ts) >= 2.5:
            self._apply_print_state(st)

    def _apply_print_state(self, st: dict) -> None:
        """print_stats.state → Print/Pause/Stop butonlari + sayac resync.

        R2 yerel sayaci yalnizca gosterge yapmisti; R3 ile gercek durum burada
        baglanir: elapsed her yoklamada print_duration'a esitlenir (drift yok),
        dis kaynakli (ornegin dogrudan Moonraker'dan baslatilan) baski da GUI'ye
        yansir. Baski yokken elapsed SIFIRLANMAZ (son deger okunur kalir; yeni
        baslangic _start_print'te sifirlar).
        """
        state = st.get("print_state") or ""
        dur = st.get("print_duration")
        if state in ("printing", "paused") and isinstance(dur, (int, float)) and dur >= 0:
            self._print_elapsed = int(dur)
        if state == "printing":
            self._print_paused = False
            if self.print_timer is None:
                self.print_timer = QTimer(self)
                self.print_timer.timeout.connect(self._print_tick)
            if not self.print_timer.isActive():
                self.print_timer.start(1000)
            self._set_print_btn_states(printing=True, paused=False)
        elif state == "paused":
            self._print_paused = True
            if self.print_timer and self.print_timer.isActive():
                self.print_timer.stop()
            self._set_print_btn_states(printing=False, paused=True)
        else:
            # standby / complete / cancelled / error → baski yok.
            self._print_paused = False
            if self.print_timer and self.print_timer.isActive():
                self.print_timer.stop()
            self._set_print_btn_states(printing=False, paused=False)
        self._update_print_display()

    def _update_conn_label(self) -> None:
        lbl = getattr(self, "conn_status_lbl", None)
        if lbl is None:
            return
        if requests is None:
            text, color = "●  'requests' kurulu değil", "#9E9E9E"
        elif self._moonraker_online is None:
            text, color = "●  Bağlantı: bekleniyor…", "#9E9E9E"
        elif not self._moonraker_online:
            text, color = "●  Bağlantı yok", "#D32F2F"
        elif not self._klippy_ready:
            text, color = "●  Klipper hazır değil", "#EF6C00"
        elif self._print_state == "printing":
            text, color = "●  Yazdırıyor", "#2E7D32"
        elif self._print_state == "paused":
            text, color = "●  Duraklatıldı", "#EF6C00"
        elif "xyz" not in self._homed_axes:
            text, color = "●  Hazır — G28 gerekli", "#EF6C00"
        else:
            text, color = "●  Hazır", "#2E7D32"
        lbl.setText(text)
        lbl.setStyleSheet(
            f"font-size:12px; font-weight:bold; color:{color};"
            " padding:4px 2px; background:transparent;")

    def _motion_allowed(self) -> bool:
        """R4: hareket baslatan komutlarin on-kosulu.

        Makine durumu POZITIF biliniyorsa (Moonraker ulasilabilir + Klipper
        ready) homed sarti aranir. Baglanti yok / hazir degil / henuz
        yoklanmadi durumlarinda True doner (fail-open): dev makinesinde UI
        kullanilabilir kalir ve baglanti sorunlari zaten status etiketi + R1
        banner'lariyla gorunur. SERT kapi her kosulda firmware'dedir
        (PRINT_START kilidi + jog makrolarindaki homed kapilari).
        """
        if not (self._moonraker_online and self._klippy_ready):
            return True
        return "xyz" in self._homed_axes

    def _motion_preflight(self, label: str) -> bool:
        """R4: engellendiyse nedenini banner'da acikla ve False don."""
        if self._motion_allowed():
            return True
        self._show_banner(
            f"{label} engellendi: eksenler home değil. Önce G28, sonra "
            "CALIBRATE_Z_OFFSET çalıştır.", kind="error")
        return False

    def _send_jog_macro(self, macro: str) -> None:
        """R4: jog butonlari icin ON-KONTROLLU gonderim (gelecek jog UI bunu kullanacak).

        Klipper'daki jog kapisi REDDI HTTP 200 ile doner (makro icindeki
        RESPOND TYPE=error Moonraker icin 'basari'dir) → teslim kontrolu (R1)
        reddi YAKALAYAMAZ. Bu yuzden homed durumu R3 yoklamasindan ONCEDEN
        bilinir ve komut daha hic gonderilmeden engellenip aciklanir.
        """
        if not self._motion_preflight(f"JOG ({macro})"):
            return
        self._send_moonraker_request("/printer/gcode/script", {"script": macro})

    def _exit_application(self) -> None:
        """Kiosk modunda OS'e donmek icin uygulamayi GUVENLE kapat.

        Ham QApplication.quit() yerine ONCE self.close() cagrilir: closeEvent zaten
        slice thread'lerini durdurur + VTK plotter'lari kapatir (RPi4'te segfault
        onleme). Ardindan quit() event loop'u kesin sonlandirir -> OS'e doner.
        """
        reply = QMessageBox.question(
            self, "Exit Application",
            "Uygulamadan cikip isletim sistemine donulsun mu?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.close()            # closeEvent: thread + VTK plotter teardown
            QApplication.quit()     # garanti cikis -> OS

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
        # Baski baslatma (Export → Moonraker upload → /printer/print/start) durumu.
        self._last_gcode_path: Optional[str] = None       # son basarili export dosya yolu
        self._last_gcode_filename: Optional[str] = None    # yalnizca dosya adi
        # Slice snapshot (Section 10): Slice basinda alinan geometri-etkileyen
        # parametreler. _slice_snapshot YALNIZCA basarili finished sonrasi gecerli
        # sayilir; _pending_slice_snapshot slice ucustayken tutulur.
        self._slice_snapshot: Optional[dict] = None
        self._pending_slice_snapshot: Optional[dict] = None
        # Export snapshot (bu tur): basarili G-code export'unda alinan parametreler
        # (slice_snapshot + origins + tool + speed + G-code dosya kimligi). Print
        # oncesi "eski/yanlis G-code" freshness kontrolu bunu kullanir.
        self._export_snapshot: Optional[dict] = None
        self._print_start_inflight: bool = False           # upload ucustayken cift-tik engeli

        # 3D Model
        self.stl_dosya_yolu: Optional[str] = None
        self.plotter = None
        self._model_actor = None              # (legacy) tek model aktoru — artik kullanilmiyor
        self._well_registry: dict = {}        # well_id -> {"center":(x,y), "wire":actor, "hitbox_name":str}
        self._container_actor_names: list = []  # mevcut kap aktor isimleri (temizlik)
        self._selected_well = None            # (legacy) tek kuyu — coklu icin _selected_wells kullanilir
        self._picking_enabled: bool = False   # enable_mesh_picking yalnizca 1 kez kurulur
        # --- COKLU KUYU (well-plate) merkezi state ---
        self._selected_wells: set = set()        # secili kuyu id kumesi (UI + 3D senkron)
        self._loaded_model_mesh = None           # normalize STL mesh (memory'de; kuyulara kopyalanir)
        self._well_model_actor_names: list = []  # sahnedeki model kopya aktorlerinin adlari
        self.platform_config: dict = {}          # PlatformTab'den gelen son config
        self._container_signature = None         # kap yeniden cizimi yalnizca type/format/olcu degisince

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
        # Dilimleme surerken YENI model yuklenirse: ucustaki sonuc artik yanlis
        # modele ait -> bayatlanir; _on_slice_done sonucu SAKLAMADAN atar (yanlis
        # modelin katmanlarini onizleyip/exportlayip BASMAYI onler).
        self._slice_stale: bool = False
        # Retired (finishing) threads kept alive until they self-finish, so we
        # never GC a running thread and never block the GUI with wait().
        self._retired_threads: list = []

        # R1: Moonraker teslim koprusu + modal olmayan uyari banner'i.
        # Bridge self'e parent'li → GUI thread'inde yasar; sinyaller daemon
        # thread'lerden emit edilse de slotlar queued olarak GUI'de kosar.
        # Banner widget'i LAZY kurulur (_show_banner ilk cagrida olusturur).
        self._moonraker_bridge = _MoonrakerBridge(self)
        self._moonraker_bridge.failed.connect(self._on_moonraker_failed)
        self._moonraker_bridge.recovered.connect(self._on_moonraker_recovered)
        # Arka plan G-code upload sonucu GUI thread'inde islenir (queued sinyal).
        self._moonraker_bridge.gcode_upload_finished.connect(self._on_gcode_upload_finished)
        self._alert_banner = None                      # lazy QLabel overlay
        self._banner_timer: Optional[QTimer] = None

        # R3: 5 sn'lik Moonraker durum yoklamasi (webhooks + print_stats +
        # toolhead.homed_axes, TEK GET). Sonuc daemon thread'den bridge.polled
        # (queued) ile GUI'ye gelir. Buton/sayac gercegi YALNIZCA Klipper
        # 'ready' verisiyle surulur: offline/haberslik yoklama lokal durumu
        # BOZMAZ (dev makinesinde UI aynen calisir). R4 on-kontrolu de
        # (_motion_allowed) bu alanlari okur.
        self._moonraker_bridge.polled.connect(self._on_printer_status)
        self._moonraker_online: Optional[bool] = None  # None = henuz yoklanmadi
        self._klippy_ready: bool = False
        self._homed_axes: str = ""
        self._print_state: str = ""                    # print_stats.state
        self._poll_inflight: bool = False
        self._last_print_action_ts: float = 0.0        # iyimser tutma penceresi
        self._last_conn_state: str = ""                # konsola yalnizca degisimde yaz
        self._status_timer: Optional[QTimer] = None
        if requests is not None:
            self._status_timer = QTimer(self)
            self._status_timer.timeout.connect(self._poll_printer_status)
            self._status_timer.start(5000)
            # Ilk yoklamayi bekletme: event loop basladiktan hemen sonra sor.
            QTimer.singleShot(400, self._poll_printer_status)

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
        # Preview'de well-plate kopya aktorlerinin adlari (kare-basi temizlik icin).
        self._preview_well_actor_names: list = []

    # ==========================================================
    # WINDOW & LAYOUT
    # ==========================================================
    def _setup_window(self) -> None:
        self.setWindowTitle("Klipper Control Interface")
        self.resize(800, 480)
        self.setStyleSheet("QWidget { background-color: #F8F9FA; color: #212121; }")

        # Diyalog pencerelerini (QMessageBox / QInputDialog / QDialog) dokunmatik-
        # okunur yap. APP-GENELINDE verilir ki ad-hoc olusturulan diyaloglara da
        # ulassin. Seciciler yalnizca QDialog ve alt siniflarini hedefler; bu pencere
        # bir QWidget (QDialog DEGIL) oldugundan ana arayuz ETKILENMEZ. Mevcut app
        # stiline EKLENIR (clobber etmez).
        _app = QApplication.instance()
        if _app is not None:
            _app.setStyleSheet((_app.styleSheet() or "") + DIALOG_STYLE)

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

        # R3: baglanti/durum gostergesi — 5 sn'lik yoklamayla renklenir
        # (gri=bekliyor, kirmizi=baglanti yok, turuncu=Klipper hazir degil /
        # G28 gerekli, yesil=hazir/yazdiriyor). Kiosk'ta operatorun Print'in
        # neden gri oldugunu GORDUGU yer burasi.
        self.conn_status_lbl = QLabel(
            "●  'requests' kurulu değil" if requests is None
            else "●  Bağlantı: bekleniyor…")
        self.conn_status_lbl.setWordWrap(True)
        self.conn_status_lbl.setFixedWidth(140)
        self.conn_status_lbl.setStyleSheet(
            "font-size:12px; font-weight:bold; color:#9E9E9E;"
            " padding:4px 2px; background:transparent;")
        self.sol_menu_duzeni.addWidget(self.conn_status_lbl)

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
        self.exit_app_btn = self.settings_tab.exit_app_btn

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

        # 3D referans kap + COKLU model kopyalari: PlatformTab tek "platform_changed"
        # sinyaliyle (type/format/olcu/kuyu secimi) tum viewport guncellemesini surer.
        # (Eski dagitik _draw_container baglantilari bu tek yola tasindi.)
        self.platform_tab.platform_changed.connect(self._on_platform_config_changed)

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

        if getattr(self, "exit_app_btn", None):
            self.exit_app_btn.clicked.connect(self._exit_application)

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
            # Desteklenen TEK grid tipi Linear: kayitli deger ne olursa olsun
            # (eski / bilinmeyen / bos dahil) ACIKCA Linear'a normalize et.
            self.kutu_grid.setCurrentText("Linear")
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

        # Well Plate secili kuyularini arayuze + merkezi state'e yansit (emit YOK →
        # dongu olmaz). _show_stl (varsa) kabi + model kopyalarini bu secime gore cizer.
        wells = d.get("bp_selected_wells", [])
        self.platform_tab.set_selected_wells(wells, emit_signal=False)
        self._selected_wells = set(wells)

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
            # Desteklenen TEK grid tipi Linear: kayitli deger ne olursa olsun
            # (eski / bilinmeyen / bos dahil) ACIKCA Linear'a normalize et.
            self.kutu_grid.setCurrentText("Linear")
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

        # Well Plate secili kuyularini arayuze + merkezi state'e yansit (emit YOK).
        wells = d.get("bp_selected_wells", [])
        self.platform_tab.set_selected_wells(wells, emit_signal=False)
        self._selected_wells = set(wells)
        # Preview/export icin merkezleri tazele; sonra Model sahnesinde referans
        # kabi (salt-gorsel) + secili kuyulara model kopyalarini tazele
        # (plotter yoksa hepsi no-op).
        self._rebuild_well_registry()
        self._draw_container()
        self._update_model_copies()

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
            "bp_selected_wells": sorted(self._selected_wells),
            "layer": self.kutu_layer.value() if self.kutu_layer else 0.0,
            "speed": self.kutu_speed.value() if self.kutu_speed else 0.0,
            "grid": "Linear",   # desteklenen tek deger; currentText'e bagli kalma
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
        # UI still update. AMA basari mesaji YALNIZCA gercekten diske yazildiysa
        # gosterilir (Section L: ayni anda "Save Error" + "saved successfully"
        # celiskisini onler).
        wrote_disk = True
        try:
            self.dm.save_to_disk(name, self.kayitli_protokoller[name])
        except OSError as e:
            wrote_disk = False
            QMessageBox.critical(
                self, "Save Error",
                f"Protokol diske yazılamadı (yalnızca bu oturumun belleğinde tutuldu):\n{e}")

        self._refresh_protocol_list()

        if self.protokol_listesi:
            for i in range(self.protokol_listesi.count()):
                if self.protokol_listesi.item(i).text() == name:
                    self.protokol_listesi.setCurrentRow(i)
                    break

        if self.protokol_detay_alani:
            self.protokol_detay_alani.setText(detail)

        self._editing_protocol_name = None

        # Basari mesaji SADECE disk yazimi basariliysa. Aksi halde yukarida
        # "Save Error" gosterildi; burada YANILTICI "saved successfully" gostermeyiz.
        if wrote_disk:
            print(f"System: Protocol saved -> '{name}'")
            QMessageBox.information(self, "Saved", f"Protocol '{name}' saved successfully.")
        else:
            print(f"System: Protocol '{name}' KEPT IN MEMORY ONLY (disk write failed).")

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

    def _send_moonraker_request(self, endpoint: str, payload: dict = None,
                                critical: Optional[str] = None) -> None:
        """POST to Moonraker WITHOUT ever blocking the GUI thread.

        A synchronous requests.post() on the GUI thread stalls the Qt event loop
        for up to `timeout` seconds whenever Moonraker is offline or restarting
        (config save / firmware restart) — the exact freeze this project keeps
        designing out. So the blocking call runs on a short-lived DAEMON thread
        and the GUI returns instantly; the 1.5 s timeout merely bounds each
        attempt so a hung host can't pile up threads.

        critical=None (varsayilan): eski at-ve-unut davranisi — hata yalnizca
        loglanir. Kozmetik komutlar icin (M117, sicaklik hedefi, START_*...).

        critical="STOP_UV" gibi bir etiketle TESLIM DOGRULANIR (R1):
          * Ag hatasi / timeout (Moonraker kapali ya da yeniden basliyor):
            1-2-3 sn artan arayla TOPLAM 4 deneme. Her basarisiz denemede ve
            nihai basarisizlikta GUI'ye banner cikar (queued sinyal uzerinden).
          * HTTP != 200 (sunucu ayakta ama komut REDDEDILDI): yeniden deneme
            ANLAMSIZ — sunucunun hata mesaji bir kez banner'da gosterilir.

        Thread safety: worker Qt widget'larina ASLA dokunmaz; yalnizca
        _MoonrakerBridge sinyali emit eder (alici slot GUI thread'inde kosar).
        Kapanis yarisina karsi emit try/except ile sarilidir.
        """
        if requests is None:
            print(f"[Moonraker] 'requests' yok → {endpoint} atlandı.")
            if critical:
                # GUI thread'indeyiz (cagiranlar slot) → dogrudan banner guvenli.
                self._on_moonraker_failed(
                    f"{critical} gonderilemedi: 'requests' kutuphanesi kurulu degil!")
            return

        url = f"{self._MOONRAKER_URL}{endpoint}"
        bridge = self._moonraker_bridge
        attempts = 4 if critical else 1

        def _emit(sig_name: str, msg: str) -> None:
            # Kapanis sirasinda koprunun C++ tarafi silinmis olabilir → yut.
            try:
                getattr(bridge, sig_name).emit(msg)
            except Exception:
                pass

        def _worker() -> None:
            for attempt in range(1, attempts + 1):
                try:
                    resp = requests.post(url, json=payload, timeout=1.5)
                except requests.exceptions.RequestException as exc:
                    # Offline / restarting / timeout.
                    print(f"[Moonraker] POST {endpoint} başarısız "
                          f"(deneme {attempt}/{attempts}): {exc}")
                    if not critical:
                        return
                    if attempt < attempts:
                        _emit("failed",
                              f"{critical} iletilemedi (deneme {attempt}/{attempts}) "
                              "— yeniden deneniyor...")
                        time.sleep(attempt)          # 1, 2, 3 sn artan bekleme
                        continue
                    _emit("failed",
                          f"{critical} {attempts} DENEMEDE ILETILEMEDI! Cihaz komutu "
                          "almamis olabilir — Moonraker/Klipper baglantisini kontrol "
                          "et ve cihaz durumunu FIZIKSEL olarak dogrula.")
                    return
                except Exception as exc:
                    # Beklenmeyen — GUI'ye sizdirma, logla (+kritikse banner).
                    print(f"[Moonraker] POST {endpoint} beklenmeyen hata: {exc}")
                    if critical:
                        _emit("failed", f"{critical} gonderilemedi: {exc}")
                    return

                if resp.status_code == 200:
                    if critical and attempt > 1:
                        _emit("recovered",
                              f"{critical} iletildi (deneme {attempt}/{attempts}).")
                    return
                # HTTP hata yaniti: sunucu ulasilabilir ama komut reddedildi →
                # yeniden deneme anlamsiz; mesaji cikar ve bitir.
                try:
                    detail = str(resp.json().get("error", {}).get("message", ""))
                except Exception:
                    detail = (resp.text or "")[:120]
                print(f"[Moonraker] POST {endpoint} HTTP {resp.status_code}: {detail}")
                if critical:
                    _emit("failed",
                          f"{critical} reddedildi (HTTP {resp.status_code}): {detail}")
                return

        threading.Thread(target=_worker, daemon=True, name="moonraker-post").start()

    # (şimdilik PA8 ve PC5 pinleri bu işlem için atanmıştır. daha sonra değiştirilebilir)
    def _send_uv_command(self, state: bool) -> None:
        # START_UV / STOP_UV Klipper makrosu — Moonraker gcode/script üzerinden.
        # R1: STOP_UV guvenlik-kritik → teslim dogrulanir (lamba fiziksel olarak
        # ACIK kalmasin). START_UV kozmetik kalir: iletilmezse lamba hic yanmaz,
        # tehlike olusmaz (sayac calisir ama sterilizasyon olmaz — operator gorur).
        self._send_moonraker_request(
            "/printer/gcode/script",
            {"script": "START_UV" if state else "STOP_UV"},
            critical=None if state else "STOP_UV",
        )

    # (şimdilik PA8 ve PC5 pinleri bu işlem için atanmıştır. daha sonra değiştirilebilir)
    def _send_hepa_command(self, state: bool) -> None:
        # START_HEPA / STOP_HEPA Klipper makrosu.
        # R1: STOP_HEPA teslimi dogrulanir (fan acik kalmasin); START kozmetik.
        self._send_moonraker_request(
            "/printer/gcode/script",
            {"script": "START_HEPA" if state else "STOP_HEPA"},
            critical=None if state else "STOP_HEPA",
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

    def _current_container_spec(self):
        """PlatformTab secimini normalize spec dict'e cevirir; gecersiz/bos -> None."""
        grp = getattr(self, "bp_buton_grubu", None)
        if grp is None:
            return None
        kind_id = grp.checkedId()        # 0=Petri, 1=Well, 2=Glass
        try:
            if kind_id == 0:             # Petri Dish
                txt = self.in_dia.text().strip() if self.in_dia else ""
                d = float(txt) if txt else 0.0
                if d <= 0:
                    return None
                return {"kind": "petri", "diameter": d,
                        "height": CONTAINER_DEFAULTS["petri"]["height"]}
            if kind_id == 2:             # Glass Slide
                tx = self.in_size_x.text().strip() if self.in_size_x else ""
                ty = self.in_size_y.text().strip() if self.in_size_y else ""
                x = float(tx) if tx else 0.0
                y = float(ty) if ty else 0.0
                if x <= 0 or y <= 0:
                    return None
                return {"kind": "glass", "x": x, "y": y,
                        "height": CONTAINER_DEFAULTS["glass"]["height"]}
            if kind_id == 1:             # Well Plate (A1 @ origin)
                fmt = 12 if (self.btn_12 and self.btn_12.isChecked()) else 6
                return {"kind": "well", "format": fmt}
        except (ValueError, TypeError):
            return None
        return None

    def _draw_container(self) -> None:
        """Model viewport'una REFERANS kap geometrisini (petri/glass/well) cizer.

        SALT-GORSEL: create_hitboxes=False -> hicbir pickable hitbox uretilmez,
        mesh-picking YOK, tiklama ile ekleme/silme YOK. Amac yalnizca modellerin
        HANGI kuyularda oldugunu gostermek (footprint + kuyu wireframe'leri).
        Kuyu-merkez registry'si burada DEGIL, _rebuild_well_registry() icinde
        (veriden) kurulur. Model henuz yuklenmemisse (plotter yok) sessizce gecer.
        """
        if pv is None or getattr(self, "plotter", None) is None:
            return
        # Onceki kap aktorlerini (footprint + tum kuyu wireframe'leri) temizle.
        for nm in getattr(self, "_container_actor_names", []):
            try:
                self.plotter.remove_actor(nm, render=False)
            except Exception:
                pass
        self._container_actor_names = []
        spec = self._current_container_spec()
        try:
            if spec is not None:
                # create_hitboxes=False -> yalnizca gorunur wireframe; pickable YOK.
                result = build_container_reference(self.plotter, spec,
                                                   create_hitboxes=False)
                self._container_actor_names = result.get("actor_names", [])
            # Kap imzasini CIZILEN spec'ten kur; boylece _on_platform_config_changed
            # ayni type/format icin gereksiz yeniden cizim yapmaz (RPi4).
            if not spec:
                self._container_signature = (None,)
            elif spec.get("kind") == "well":
                self._container_signature = ("well", spec.get("format"))
            elif spec.get("kind") == "glass":
                self._container_signature = ("glass", spec.get("x"), spec.get("y"))
            else:
                self._container_signature = ("petri", spec.get("diameter"))
            self.plotter.render()
        except Exception as e:
            print(f"[container] cizim hatasi: {e}")

    def _rebuild_well_registry(self) -> None:
        """_well_registry'yi (well_id -> {"center":(cx,cy)}) SADECE VERIDEN kurar.

        Model sekmesi salt-goruntuleme oldugundan artik interaktif kap CIZILMEZ;
        ama Preview (_local_preview_origins) ve G-code export (_on_export_gcode)
        kuyu bed-yerel merkezlerini bu registry'den okur. Bu yuzden secim/format
        degisince registry'yi viewport.well_centers() ile (cizim YOK) yeniden kurariz.
        Well Plate disindaki platformlarda registry bostur.
        """
        self._well_registry = {}
        kind_id = self.bp_buton_grubu.checkedId() if self.bp_buton_grubu else 0
        if kind_id == 1:                       # Well Plate
            fmt = 12 if (self.btn_12 and self.btn_12.isChecked()) else 6
            self._well_registry = {wid: {"center": c}
                                   for wid, c in well_centers(fmt).items()}
            # Guvenlik agi: format degisince gecersiz kalan secimleri ayikla.
            self._selected_wells &= set(self._well_registry.keys())

    # ==========================================================
    # PLATFORM CONFIG (PlatformTab.platform_changed alicisi)
    # ==========================================================
    def _on_platform_config_changed(self, config: dict) -> None:
        """PlatformTab secim/format/tip degisimini merkezi state'e + Model sahnesine
        yansitir. Kuyu seciminin TEK kaynagi PlatformTab'dir (Model sekmesinde
        tiklama secim degistirmez). Sira:
          1) _selected_wells merkezi state'i (Preview + export kaynagi) guncellenir,
          2) kuyu-merkez registry'si veriden yeniden kurulur (Preview/export icin),
          3) kap REFERANS geometrisi YALNIZCA type/format degisince yeniden cizilir
             (RPi4: her kuyu tiklamasi tum kap aktorlerini yeniden kurmasin),
          4) model kopyalari HER secim degisiminde tazelenir (secili kuyu = 1 kopya),
          5) Settings build-platform bilgi etiketi tazelenir.
        """
        self.platform_config = config or {}
        self._selected_wells = set(self.platform_config.get("selected_wells", []))
        self._rebuild_well_registry()
        sig = self._container_signature_of(self.platform_config)
        if sig != self._container_signature:
            self._draw_container()          # _container_signature'i gunceller
        self._update_model_copies()         # secili her kuyuya bir kopya
        self._update_platform_info()

    @staticmethod
    def _container_signature_of(cfg: dict):
        """Kap yeniden cizimini tetikleyen imza (kuyu SECIMI bunu degistirmez)."""
        t = cfg.get("type")
        if t == "well_plate":
            return ("well", cfg.get("well_format"))
        if t == "glass":
            return ("glass", cfg.get("size_x"), cfg.get("size_y"))
        if t == "petri":
            return ("petri", cfg.get("diameter"))
        return (t,)

    def _apply_well_selection_colors(self, render: bool = True) -> None:
        """Secili kuyu wireframe'lerini acik mavi, digerlerini turuncu yapar."""
        for wid, info in getattr(self, "_well_registry", {}).items():
            try:
                info["wire"].prop.color = "#33B5E5" if wid in self._selected_wells else "#F4511E"
            except Exception:
                pass
        if render:
            try:
                if self.plotter is not None:
                    self.plotter.render()
            except Exception:
                pass

    def _add_model_view_actor(self, mesh, actor_name: str):
        """Model sekmesindeki STL kopyalarini ESKI PARLAK malzemeyle ekler.

        Kok neden: 3ad18bd'deki orijinal parlak model ayarlari
        (ambient=0.6 / diffuse=0.7 / specular=0.20 / specular_power=32,
        smooth_shading=False) c00a616'da tek-model -> coklu-kopyaya gecerken
        DUSMUS; bare add_mesh(lighting=True) PyVista varsayilanlariyla (ambient~0,
        specular~0) golgeli yuzleri karartip mat/lacivert gorunume yol acmisti.
        Bu helper o ISPATLANMIS ayarlari AYNEN geri getirir. YALNIZCA Model
        sekmesi STL kopyalari icin; Preview aktorleri (_add_filament) KULLANMAZ.
        Tum kopyalar pickable=False (salt-goruntuleme). render=False -> cagiran
        tek render yapar. Global isik/plotter'a DOKUNMAZ (actor-level malzeme).
        """
        self.plotter.add_mesh(
            mesh,
            name=actor_name,
            color="#29b6f6",
            show_edges=False,
            edge_color="#01579b",
            lighting=True,
            smooth_shading=False,
            specular=0.20,
            specular_power=32,
            ambient=0.6,
            diffuse=0.7,
            pickable=False,
            render=False,
        )

    def _update_model_copies(self) -> None:
        """Yuklenmis modeli aktif platforma gore Model sekmesinde kopyalar.

        - Petri / Glass  -> tek kopya, yatak merkezinde (ad: model_copy_center).
        - Well Plate     -> Built Platform'da secili HER gecerli kuyu icin bir
                            deep-copy; kuyu merkezine (cx,cy,0) translate edilir
                            (ad: model_copy_<well_id>). Secili kuyu YOKSA kopya yok.
        Kopya sayisi = secili kuyu sayisi. TUM kopyalar pickable=False (Model
        sekmesi salt-goruntuleme; tiklama ile ekleme/silme YOK). Coklu-kuyu
        cogaltimi ayrica Preview + generate_gcode_multi_origin tarafinda yasar.
        Kuyu SECIMININ TEK kaynagi PlatformTab.selected_wells'tir.
        """
        if pv is None or getattr(self, "plotter", None) is None:
            return
        # Onceki model kopyalarini temizle.
        for nm in list(self._well_model_actor_names):
            try:
                self.plotter.remove_actor(nm, render=False)
            except Exception:
                pass
        self._well_model_actor_names = []

        mesh = self._loaded_model_mesh
        if mesh is None:
            try:
                self.plotter.render()
            except Exception:
                pass
            return

        kind_id = self.bp_buton_grubu.checkedId() if self.bp_buton_grubu else 0
        if kind_id != 1:
            # Petri / Glass -> tek kopya, yatak merkezinde (deep-copy gereksiz).
            self._add_model_view_actor(mesh, "model_copy_center")
            self._well_model_actor_names.append("model_copy_center")
        else:
            # Well Plate -> secili her gecerli kuyu icin bir kopya, kuyu merkezinde.
            for well_id in sorted(self._selected_wells):
                info = self._well_registry.get(well_id)
                if not info:
                    continue
                cx, cy = info["center"]
                mesh_copy = mesh.copy(deep=True)
                mesh_copy.translate((cx, cy, 0.0), inplace=True)
                actor_name = f"model_copy_{well_id}"
                self._add_model_view_actor(mesh_copy, actor_name)
                self._well_model_actor_names.append(actor_name)
        try:
            self.plotter.render()
        except Exception:
            pass

    def _local_preview_origins(self) -> list:
        """Secili kuyularin yerel merkezleri: (well_id, cx, cy) listesi.

        NOT: Preview ARTIK bu helper'i KULLANMAZ. Preview her zaman tek merkez
        modeli (origin 0,0) gosterir; coklu-kuyu cogaltimi yalnizca Model
        sekmesinde (_update_model_copies) ve G-code export'ta yasar. Export kendi
        origin listesini _well_registry[wid]["center"]'dan kurar. Bu helper
        genel bir kuyu-merkez yardimcisi olarak (ve testler icin) korunur.

        Petri/Glass ya da kuyu secilmemis -> [("", 0.0, 0.0)] (tek merkez).
        Well-plate + secili kuyular -> her kuyu icin yerel merkez.
        """
        kind_id = self.bp_buton_grubu.checkedId() if self.bp_buton_grubu else 0
        if kind_id != 1:
            return [("", 0.0, 0.0)]
        origins = []
        for wid in sorted(self._selected_wells):
            info = self._well_registry.get(wid)
            if info:
                cx, cy = info["center"]
                origins.append((wid, float(cx), float(cy)))
        return origins

    def _show_stl(self, path: str) -> None:
        if pv is None or QtInteractor is None or self.uc_boyutlu_alan is None:
            return

        # Uçuşta bir dilimleme varsa: sonucu artık YANLIŞ modele ait olacak.
        # Bayatla (geç gelen finished saklanmadan atılır) + işbirlikçi iptal iste
        # (worker blok sınırlarında bayrağı görüp 'aborted' ile erken döner).
        if self._slicing:
            self._slice_stale = True
            try:
                if self._slice_worker is not None:
                    self._slice_worker.request_abort()
            except Exception:
                pass

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

                # NOT: Model sekmesi artik SALT-GORUNTULEME. Onceden burada
                # enable_mesh_picking (well hitbox tiklama -> model kopyala/sil)
                # kuruluyordu; KALDIRILDI. Kuyu secimi YALNIZCA Built Platform
                # sekmesinden yapilir. Bu sekme sadece "actigim STL nasil
                # gorunuyor?" sorusuna cevap verir; hicbir kuyu/hitbox cizilmez.

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
            self._loaded_model_mesh = None
            self._well_model_actor_names = []
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

            # --- MODEL --- Tek aktor yerine memory'de sakla; sahneye kopyalar
            # _update_model_copies() ile eklenir (petri/glass=merkez, well-plate=
            # secili kuyu sayisi kadar). Her kuyu icin dosyadan TEKRAR okunmaz.
            self._loaded_model_mesh = mesh
            self._model_actor = None

            # --- KUYU MERKEZ KAYDI (Preview/export icin; SADECE VERI) ---
            self._rebuild_well_registry()

            # --- REFERANS KAP (SALT-GORSEL: hitbox YOK, mesh-picking YOK) ---
            # Modellerin hangi kuyularda oldugu gorunsun diye footprint + kuyu
            # wireframe'leri cizilir; hicbir aktor pickable degildir.
            self._draw_container()

            # --- MODEL KOPYALARI (petri/glass=merkez; well-plate=secili kuyular) ---
            self._update_model_copies()

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
    def _current_slice_params(self) -> dict:
        """Slice geometrisini ETKILEYEN mevcut parametreler (Section 10).

        Well secimi / printhead / sicaklik / print speed BURADA YOK: bunlar ayni
        geometriyi farkli origin/feed/temperature ile kullanabilir, slice'i
        gecersiz yapmaz. STL degisimini path+size+mtime ile yakalar.
        """
        path = self.stl_dosya_yolu or ""
        size, mtime = -1, -1
        try:
            st = Path(path).stat()
            size, mtime = st.st_size, st.st_mtime_ns
        except OSError:
            pass
        return {
            "stl_path": path,
            "stl_size": size,
            "stl_mtime_ns": mtime,
            "layer_height": float(self.kutu_layer.value()) if self.kutu_layer else 0.2,
            "grid_distance": float(self.kutu_distance.value()) if self.kutu_distance else 1.0,
            "grid_type": "Linear",
        }

    def _slice_is_dirty(self) -> bool:
        """True → mevcut slice sonucu artik gecerli DEGIL (yeniden Slice gerekli).

        Snapshot yoksa, slice verisi yoksa ya da geometri-etkileyen bir parametre
        (STL/layer height/grid distance/grid type) slice'tan bu yana degistiyse
        dirty. Kuyu secimi degisimi dirty YAPMAZ (ayni slice, farkli origin'ler).
        """
        snap = self._slice_snapshot
        if not snap or not self._slices:
            return True
        return self._current_slice_params() != snap

    def _export_origins_signature(self):
        """Export'un uretecegi origin'lerin KARSILASTIRILABILIR imzasi.

        Well Plate: ('well', ((wid, bed_x, bed_y), ...)) — kuyu SECIMI ve FORMAT
        degisimini yakalar (registry merkezleri formatla degisir). Petri/Glass:
        ('single', bed_x, bed_y). Sicaklik/slider/UV-HEPA bunu ETKILEMEZ.
        """
        kind_id = self.bp_buton_grubu.checkedId() if self.bp_buton_grubu else 0
        bed_cx, bed_cy = 120.0, 60.0
        if kind_id == 1:
            sig = []
            for wid in sorted(self._selected_wells):
                info = getattr(self, "_well_registry", {}).get(wid)
                if info and "center" in info:
                    cx, cy = info["center"]
                    sig.append((wid, round(bed_cx + cx, 3), round(bed_cy + cy, 3)))
            return ("well", tuple(sig))
        return ("single", round(bed_cx, 3), round(bed_cy, 3))

    def _current_export_params(self) -> dict:
        """Export'u ETKILEYEN mevcut parametreler: origins + active_tool + speed.

        Printhead SICAKLIGI / platform sicakligi / Preview slider / UV-HEPA
        BURADA YOK (bunlar export'u dirty YAPMAZ). Slice-dirty ayri kontrol edilir.
        """
        ph_id = self.ph_buton_grubu.checkedId() if self.ph_buton_grubu else 1
        active_tool = {1: "T0", 2: "T1", 3: "T2"}.get(ph_id, "T0")
        speed = float(self.kutu_speed.value()) if self.kutu_speed else 10.0
        return {"origins": self._export_origins_signature(),
                "active_tool": active_tool, "print_speed": speed}

    def _export_is_dirty(self) -> bool:
        """True → son export'lanan G-code artik gecerli DEGIL (yeniden Export gerekli).

        Dirty: export snapshot yok · slice dirty · G-code dosyasi silinmis/disaridan
        degistirilmis · origins (kuyu secimi/format) · active_tool (printhead) ya da
        print_speed degismis. Sicaklik/slider/UV-HEPA dirty YAPMAZ.
        """
        snap = self._export_snapshot
        if not snap:
            return True
        if self._slice_is_dirty():           # slice geometri parametresi degistiyse
            return True
        # KRITIK: son export'un dayandigi slice snapshot'i GUNCEL slice snapshot'i
        # ile ayni mi? Ayni ayarlarla YENIDEN slice yapildiginda _slice_is_dirty
        # False olur ama G-code ESKI slice'a aittir (ornegin 0.20 export → 0.10
        # yeniden slice → yeniden export YOK) → export DIRTY. Farkli snapshot kesin
        # dirty; ayni snapshot (deterministik) korunur.
        if snap.get("slice_snapshot") != self._slice_snapshot:
            return True
        try:                                  # G-code dosya kimligi (silinmis/degismis)
            st = Path(snap.get("gcode_path", "")).stat()
            if st.st_size != snap.get("gcode_size") or st.st_mtime_ns != snap.get("gcode_mtime_ns"):
                return True
        except OSError:
            return True
        cur = self._current_export_params()   # origins / tool / speed
        return (cur["origins"] != snap.get("origins")
                or cur["active_tool"] != snap.get("active_tool")
                or cur["print_speed"] != snap.get("print_speed"))

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
            self._slice_worker.aborted.connect(self._on_slice_aborted)
            self._slice_worker.progress.connect(self.slice_progress.setValue)
            self._slice_worker.finished.connect(self._slice_thread.quit)
            self._slice_worker.error.connect(self._slice_thread.quit)
            self._slice_worker.aborted.connect(self._slice_thread.quit)
            self._slice_thread.finished.connect(self._slice_worker.deleteLater)
            self._slice_thread.finished.connect(self._slice_thread.deleteLater)

            # Show + reset the progress bar as the thread starts.
            if self.slice_progress is not None:
                self.slice_progress.setValue(0)
                self.slice_progress.setVisible(True)

            # Section 10: bu Slice'in geometri parametrelerini SAKLA (pending).
            # Basarili finished'da _slice_snapshot'a tasinir; dirty-state bunu kullanir.
            self._pending_slice_snapshot = self._current_slice_params()
            self._slicing = True
            self._slice_stale = False   # bu ucus guncel modele ait
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
            # Bayat sonuç: dilimleme sürerken kullanıcı YENİ model yükledi. Bu
            # sonuç ESKİ modele ait — saklanırsa önizleme/export yanlış modeli
            # gösterir (biyoyazıcıda yanlış yapı basılır). At; finally bloğu
            # butonu/progress'i yine de sıfırlar.
            if self._slice_stale:
                print("[SLICE] Bayat dilim sonucu atıldı (dilimleme sırasında model değişti).")
                return
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
            # Section 10: BASARILI finished → snapshot artik GECERLI. Bu andan
            # itibaren geometri-etkileyen ayar degisirse slice "dirty" olur.
            self._slice_snapshot = self._pending_slice_snapshot

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

            # 5. Dikey katman slider'ı: Layer 1'de (value 0) başlar — Qt dikey
            #    varsayılanıyla EN ALTTA (invertedAppearance KAPALI; açmak
            #    kaydıracı ters çevirir!). Sinyalleri bloklayarak kur.
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
        # Bayat (model değişti) bir işin hatası kullanıcıyı ilgilendirmez —
        # durum yukarıda sıfırlandı, popup gösterme.
        if self._closing or self._slice_stale:
            return
        QMessageBox.critical(self, "Slice Hatası", msg)

    def _on_slice_aborted(self) -> None:
        """İşbirlikçi iptal onayı: worker 'aborted' dedi — durumu SESSİZCE sıfırla.

        finished/error gelmeyeceği için buton/progress kilidi burada açılır;
        popup yok (iptal kasıtlı: model değişti ya da uygulama kapanıyor).
        """
        self._slicing = False
        if self._closing:
            return
        if self.slice_btn:
            self.slice_btn.setEnabled(True)
        if self.slice_progress is not None:
            self.slice_progress.setVisible(False)
        print("[SLICE] Dilimleme iptal edildi (model değişimi/kapanış).")

    # ==========================================================
    # PRINT
    # ==========================================================
    def _set_print_btn_states(self, printing: bool, paused: bool) -> None:
        if self.print_btn:
            # R4 gri-buton: makine BILINEN sekilde home degilse baslatma/devam
            # kapali (neden, yan menudeki status etiketinde: "G28 gerekli").
            self.print_btn.setEnabled((not printing) and self._motion_allowed())
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
        # R2: Sure tahmini dolunca ASLA _stop_print() CAGIRMA — o yol Moonraker'a
        # /printer/print/cancel (→ CANCEL_PRINT) gonderir ve 60. dakikada GERCEK
        # baskiyi oldururdu. Yerel sayac saniyelik GOSTERGE dolgusudur; R3 poll'u
        # her 5 sn'de elapsed'i GERCEK print_stats.print_duration'a esitler ve
        # baski gercekten bitince (state != printing) sayaci durdurur.
        self._print_elapsed += 1
        self._update_print_display()

    def _start_print(self) -> None:
        # Cift-tiklama / yeniden-giris korumasi: bir upload ucustayken yeni
        # baslatma denemesi yok sayilir (buton da o an disable'dir).
        if self._print_start_inflight:
            return
        # R4 on-kontrol: makine hazir ama home DEGILSE baski/resume hic
        # baslatilmaz (banner nedenini soyler). PRINT_START zaten firmware'de
        # kilitli; bu katman reddi dokunmadan ONCE aciklamak icin var.
        if not self._motion_preflight("PRINT"):
            return

        # print_btn duraklatmada RESUME gorevi gorur (etiketi "Resume"): gercek
        # resume gonder ve UI'yi printing moduna al (timer sifirlanmaz).
        if self._print_paused:
            self._send_moonraker_request("/printer/print/resume")
            self._enter_printing_ui(reset_timer=False)
            return

        # --- TAZE BASLANGIC: son export G-code'u Moonraker'a yukle + baslat ---
        if not self._last_gcode_path:
            QMessageBox.warning(
                self, "G-Code Yok",
                "Önce Preview sekmesinden Export G-Code yapmalısınız.")
            return
        if not Path(self._last_gcode_path).exists():
            QMessageBox.warning(
                self, "Dosya Bulunamadı",
                "Son export edilen G-code dosyası bulunamadı.\n"
                "Lütfen Preview sekmesinden tekrar Export G-Code yapın.")
            return
        # Section 10: dirty slice → son G-code artik gecerli parametreye ait DEGIL;
        # eski/yanlis G-code'u BASMA. Yeniden Slice + Export iste.
        if self._slice_is_dirty():
            QMessageBox.warning(
                self, "Yeniden Slice Gerekli",
                "Dilimleme ayarları değişti. Yeniden Slice yapıp Export edin.")
            return
        # Slice guncel fakat EXPORT dirty ise (kuyu secimi / printhead / hiz degisti,
        # ya da G-code dosyasi silindi/degisti) → eski G-code'u YANLIS kuyu/tool/hiz
        # ile BASMA; yalnizca yeniden Export iste.
        if self._export_is_dirty():
            QMessageBox.warning(
                self, "Yeniden Export Gerekli",
                "Export ayarları değişti (kuyu seçimi / printhead / hız) ya da G-code "
                "dosyası değişti/silindi.\nPreview sekmesinden yeniden Export edin.")
            return

        # Upload'i ARKA PLANDA yap (GUI donmasin): UI 'printing' moduna ANCAK upload
        # basarili olunca gecer (_on_gcode_upload_finished).
        self._begin_gcode_upload(self._last_gcode_path)

    def _begin_gcode_upload(self, local_path: str) -> None:
        """Son export G-code'u ARKA PLAN thread'inde Moonraker'a yukler.

        Upload suresince Print butonu disable + _print_start_inflight=True. Sonuc
        gcode_upload_finished sinyaliyle GUI thread'ine doner. WORKER THREAD Qt
        widget'larina ASLA dokunmaz — yalnizca bridge sinyali emit eder.
        """
        self._print_start_inflight = True
        if self.print_btn:
            self.print_btn.setEnabled(False)
        bridge = self._moonraker_bridge

        def _worker() -> None:
            ok, info = self._upload_gcode_to_moonraker(local_path)
            # info = basarida Moonraker dosya adi/path, hatada hata mesaji.
            try:
                bridge.gcode_upload_finished.emit(bool(ok), info if ok else "", info)
            except Exception:
                pass   # kapanista koprunun C++ tarafi silinmis olabilir

        threading.Thread(target=_worker, daemon=True, name="gcode-upload").start()

    def _enter_printing_ui(self, reset_timer: bool) -> None:
        """UI'yi 'printing' moduna al (timer + butonlar). reset_timer=True taze baslangic."""
        if reset_timer:
            self._print_elapsed = 0
            self._print_total = 3600
        self._print_paused = False
        if self.print_timer is None:
            self.print_timer = QTimer(self)
            self.print_timer.timeout.connect(self._print_tick)
        self._update_print_display()
        self._set_print_btn_states(printing=True, paused=False)
        self.print_timer.start(1000)
        # R3: iyimser pencere ac (eylemden once yola cikan yoklama butonlari
        # geri cevirmesin) + 3 sn sonra hizli teyit yoklamasi iste.
        self._last_print_action_ts = time.monotonic()
        QTimer.singleShot(3000, self._poll_printer_status)

    def _upload_gcode_to_moonraker(self, local_path: str) -> tuple[bool, str]:
        """BLOCKING: .gcode dosyasini Moonraker 'gcodes' koklerine yukler.

        ARKA PLAN thread'inden cagrilir (GUI'yi bloklamamak icin); Qt widget'larina
        ASLA dokunmaz. Donus: (True, moonraker_dosya_adi) ya da (False, hata_mesaji).
        """
        if requests is None:
            return False, "'requests' kutuphanesi kurulu degil."
        p = Path(local_path)
        if not p.exists():
            return False, f"Dosya bulunamadi: {local_path}"
        url = f"{self._MOONRAKER_URL}/server/files/upload"
        try:
            # timeout=30: G-code/STL buyuk olabilir; 1.5 sn upload icin yetersiz.
            with open(local_path, "rb") as fh:
                files = {"file": (p.name, fh, "application/octet-stream")}
                data = {"root": "gcodes"}
                resp = requests.post(url, files=files, data=data, timeout=30)
        except requests.exceptions.RequestException as exc:
            return False, f"Yukleme baglanti hatasi: {exc}"
        except Exception as exc:
            return False, f"Yukleme hatasi: {exc}"
        if resp.status_code in (200, 201):
            try:
                body = resp.json()
            except Exception:
                body = {}
            print(f"[Moonraker] upload yaniti: {body}")   # response'u logla (teshis)
            # Yanit sekli surumden surume degisebilir: 'item' ya top-level ya 'result' altinda.
            item = body.get("item") or body.get("result", {}).get("item", {}) or {}
            fname = item.get("path") or item.get("filename") or p.name
            # item.path zaten koke ('gcodes') GORELI gelir; nadiren "gcodes/" one eki
            # gelirse ayikla — /printer/print/start root-relative ad bekler.
            if fname.startswith("gcodes/"):
                fname = fname[len("gcodes/"):]
            return True, fname
        try:
            detail = str(resp.json().get("error", {}).get("message", ""))
        except Exception:
            detail = (resp.text or "")[:120]
        return False, f"Yukleme reddedildi (HTTP {resp.status_code}): {detail}"

    def _start_uploaded_gcode(self, filename: str) -> None:
        """Yuklenen dosyayi /printer/print/start ile baslat (kritik → banner'li teslim)."""
        self._send_moonraker_request(
            "/printer/print/start",
            {"filename": filename},
            critical="START_PRINT",
        )

    def _on_gcode_upload_finished(self, success: bool, filename: str, message: str) -> None:
        """GUI thread slot'u: upload sonucu. Basarida baslat + printing UI; hatada geri al."""
        self._print_start_inflight = False
        if self._closing:
            return
        if not success:
            # UI 'printing' moduna GECMEDI; Print butonunu geri ac + hatayi goster.
            self._set_print_btn_states(printing=False, paused=False)
            self._show_banner(f"Baski baslatilamadi (yukleme): {message}", kind="error")
            return
        # Upload OK → gercek baskiyi baslat.
        self._start_uploaded_gcode(filename)
        # OPTIMISTIC UI: butonlari/timer'i hemen 'printing' yap ki upload sonrasi Print
        # butonu disable takili kalmasin. _enter_printing_ui ayni anda _last_print_action_ts'i
        # kurup 3 sn sonra poll planlar; R3 poll GERCEK print_stats.state ile resync eder
        # (start reddedilirse state != printing gorulur, butonlar otomatik geri alinir).
        self._enter_printing_ui(reset_timer=True)

    def _pause_print(self) -> None:
        if self.print_timer and self.print_timer.isActive():
            self.print_timer.stop()
        self._print_paused = True
        self._set_print_btn_states(printing=False, paused=True)
        # Update the UI instantly (above), then dispatch the pause to Klipper.
        # R1: duraklatma teslimi dogrulanir — basarisizsa baski fiilen SURUYOR
        # demektir; banner operatoru uyarir (UI "paused" gosterse bile).
        self._send_moonraker_request("/printer/print/pause", critical="PAUSE")
        self._last_print_action_ts = time.monotonic()      # R3 iyimser pencere
        QTimer.singleShot(3000, self._poll_printer_status)  # hizli teyit

    def _stop_print(self) -> None:
        if self.print_timer and self.print_timer.isActive():
            self.print_timer.stop()
        self._print_elapsed = 0
        self._print_paused  = False
        self._update_print_display()
        self._set_print_btn_states(printing=False, paused=False)
        # Cancel the running Klipper print (Moonraker maps cancel → CANCEL_PRINT).
        # R1: iptal teslimi dogrulanir — basarisizsa makine hala basiyor/isitiyor
        # olabilir; banner operatoru fiziksel dogrulamaya yonlendirir.
        self._send_moonraker_request("/printer/print/cancel", critical="CANCEL_PRINT")
        self._last_print_action_ts = time.monotonic()      # R3 iyimser pencere
        QTimer.singleShot(3000, self._poll_printer_status)  # hizli teyit

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

    def _flatten_polydata_for_preview(self, pd, z_preview: float = 0.03):
        """Return a deep COPY of ``pd`` with every point pinned to ``z_preview``.

        Preview-only: each active layer is sliced at its real Z (``idx*layer_h``),
        so when the vertical slider sits on a middle layer the bright active
        geometry renders high above the build plate and *looks* like it is
        floating. We flatten a COPY down onto the plate purely for display. The
        original slice PolyData — and therefore the G-code / exporter path — is
        NEVER mutated; this is a visual transform only.
        """
        copied = pd.copy(deep=True)
        pts = copied.points.copy()
        if pts.size:
            pts[:, 2] = z_preview
            copied.points = pts
        return copied

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
            for _n in ('ghost', 'preview_ghost', 'prev_p', 'prev_i', 'base_cap'):
                _a = plotter.actors.get(_n)
                if _a is not None:
                    plotter.remove_actor(_a, render=False)

            # 1. Ghost of the full model (very faint, for context). SINGLE, shared
            #    (_original_mesh by reference — NO per-well copy, NO deep-copy).
            if self._original_mesh is not None:
                # Ghost = tam modelin silueti (Z=0..toplam_yukseklik). Aktif katman
                # artik plate'e (z~0.04) indirildigi icin ghost'un alt kismi onunla
                # ust uste geliyor; opacity'yi 0.06 -> 0.03'e dusurup aktif katmanin
                # ustunu kapatmasini onluyoruz. Ghost KALDIRILMADI, sadece soluklasti.
                plotter.add_mesh(
                    self._original_mesh,
                    color='#B0BEC5', opacity=0.03,
                    show_edges=False, lighting=True, smooth_shading=True,
                    name='preview_ghost', render=False,
                )
            # 2. NOT: Eski "base_cap" (ilk katman bounds'undan uretilen DIKDORTGEN
            #    duz plane) KALDIRILDI. Daire/delikli modelde bu, gercek geometri
            #    degilken sahte bir dikdortgen yuzey gosteriyordu (yaniltici). Taban/
            #    yapisma gostergesi gerekiyorsa gercek ilk contour'dan uretilmeli;
            #    bu ise ayri ve acikca isaretli bir gorsel katman olarak eklenir.
            #    (base_cap aktoru, yukaridaki temizlik demetinde zaten kaldiriliyor.)
            self._render_last_idx = idx

        # ── 3. ACTIVE LAYER (full, instant) — TEK MERKEZ KOPYA ──────────────
        # Preview her ZAMAN tek modeli temsil eder: selected_wells KAC olursa
        # olsun burada TEK aktif katman cizilir (local origin 0,0; XY translate
        # YOK). Coklu-kuyu cogaltimi yalnizca Model sekmesinde ve G-code
        # export'ta (generate_gcode_multi_origin) yasar — Preview'da DEGIL.
        # Onceki kare(ler)den kalabilecek TUM aktif/legacy aktorleri temizle:
        # sabit isimliler + eski per-well (active_perimeter_A1 / infill_A1 /
        # ghost_A1) + eski 'infill_v'. Boylece slider hareketinde geometry birikmez.
        for _nm in list(plotter.actors.keys()):
            if (_nm in ('active_perimeter', 'active_infill', 'infill_v')
                    or _nm.startswith('active_perimeter_')
                    or _nm.startswith('infill_')
                    or _nm.startswith('ghost_')):
                _a = plotter.actors.get(_nm)
                if _a is not None:
                    plotter.remove_actor(_a, render=False)
        self._preview_well_actor_names = []   # legacy alan; artik kullanilmiyor

        # Symmetric bounds guards: worker _layer_meshes / _slices / _infills'i AYNI
        # uzunlukta uretir; yine de her erisimi koruyoruz (bos kare, asla IndexError).
        active = self._layer_meshes[idx] if idx < len(self._layer_meshes) else None
        slc    = self._slices[idx] if idx < len(self._slices) else None
        infill = self._infills[idx] if idx < len(self._infills) else None

        if active is not None and getattr(active, 'n_points', 0) > 0:
            # Preview-only: aktif katmani gercek Z'sinden (idx*layer_h) build plate
            # ustune indiriyoruz ki havada durmasin. Helper HER ZAMAN deep-copy
            # dondurur → G-code/slice verisi degismez. Perimeter ve infill'e minik
            # Z farki (0.04 / 0.05) veriyoruz ki cakismasinlar. XY TRANSLATE YOK →
            # slice zaten (0,0) merkezli; ghost ile ayni koordinat sisteminde durur.
            if slc is not None and slc.n_points > 0:
                slc_c = self._flatten_polydata_for_preview(slc, z_preview=0.04)
                self._add_filament(plotter, slc_c, 0.2, '#FF0000', 'active_perimeter')
            if infill is not None and getattr(infill, 'n_points', 0) > 0:
                inf_c = self._flatten_polydata_for_preview(infill, z_preview=0.05)
                self._add_filament(plotter, inf_c, 0.15, '#FF8C00', 'active_infill')

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
        # Section 10 dirty-state: slice'tan sonra geometri-etkileyen ayar (STL /
        # layer height / grid distance) degistiyse ESKI slice ile export YAPILMAZ.
        if self._slice_is_dirty():
            QMessageBox.warning(self, "Yeniden Slice Gerekli",
                                "Dilimleme ayarları değişti. Yeniden Slice yapın.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "G-Code Dışa Aktar", "output.gcode", "G-Code (*.gcode)")
        if not path:
            return

        # Layer height'i Settings widget'indan DEGIL slice SNAPSHOT'undan al →
        # export edilen G-code, dilimlenen geometriyle her zaman tutarli kalir.
        layer_h = (self._slice_snapshot or {}).get(
            "layer_height", self.kutu_layer.value() if self.kutu_layer else 0.2)
        bed_cx, bed_cy = 120.0, 60.0   # Klipper makro yatak merkezi (X120 Y60)

        # Aktif baski kafasi -> tool makrosu (Printhead 1/2/3 -> T0/T1/T2).
        ph_id = self.ph_buton_grubu.checkedId() if self.ph_buton_grubu else 1
        active_tool = {1: "T0", 2: "T1", 3: "T2"}.get(ph_id, "T0")
        # Baski hizi (mm/s) -> feedrate (mm/dk).
        speed_mms = self.kutu_speed.value() if self.kutu_speed else 10
        print_speed = max(1.0, float(speed_mms)) * 60.0

        # Well Plate ise: secili her kuyuya AYNI modelin kopyasi (tek dosya, coklu
        # origin). Diger platformlarda (petri/glass) eski tek-origin yol korunur.
        is_well = (self.bp_buton_grubu.checkedId() if self.bp_buton_grubu else 0) == 1
        origins = None
        if is_well:
            if not self._selected_wells:
                QMessageBox.warning(
                    self, "Kuyu Secilmedi",
                    "Well Plate secildi fakat kuyu secilmedi.\n"
                    "Lütfen A1, A2 gibi en az bir kuyu seçin.")
                return
            origins = []
            for wid in sorted(self._selected_wells):
                info = getattr(self, "_well_registry", {}).get(wid)
                if info and "center" in info:
                    cx, cy = info["center"]
                    origins.append((wid, bed_cx + cx, bed_cy + cy))
            if not origins:
                QMessageBox.warning(
                    self, "Kuyu Merkezleri Yok",
                    "Secili kuyularin merkezleri bulunamadi. Önce Model sekmesinden "
                    "STL yükleyip Well Plate kabini çizdirin.")
                return

        # Item 3: URETIM BASLAMADAN ONCE eski export'u GECERSIZ kil. Generation
        # (generate_gcode / multi_origin) ya da stat hata verirse eski snapshot/path
        # GUVENILIR kalmaz → Print eski dosyayi otomatik basamaz. Basari yolunda
        # asagida yeniden doldurulur. (Dialog Cancel yukarida donduğu icin bu satira
        # ULASMAZ; iptal eski export'u KORUR.)
        self._export_snapshot = None
        self._last_gcode_path = None
        self._last_gcode_filename = None

        # RPi4: G-code motoru diske stream eder + numpy-vektorize. Ayri worker yerine
        # bekleme imleci + buton kilidi ile GUI durust kalir.
        self.export_gcode_btn.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            if is_well:
                moves = generate_gcode_multi_origin(
                    self._slices, self._infills, path,
                    origins=origins, layer_height=layer_h,
                    active_tool=active_tool, print_speed=print_speed)
            else:
                moves = generate_gcode(self._slices, self._infills, path,
                                       layer_height=layer_h,
                                       origin_x=bed_cx, origin_y=bed_cy,
                                       active_tool=active_tool,
                                       print_speed=print_speed)
        except Exception as exc:
            QMessageBox.critical(self, "Export Hatası",
                                 f"G-Code üretilemedi:\n{exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()
            self.export_gcode_btn.setEnabled(True)

        # Export basarili → Print butonu bu dosyayi yukleyip baslatabilsin.
        self._last_gcode_path = path
        self._last_gcode_filename = Path(path).name

        # Export snapshot: bu G-code HANGI slice + origins + tool + speed ile ve
        # HANGI dosya kimligiyle uretildi. Print oncesi freshness kontrolu bunu okur.
        try:
            _st = Path(path).stat()
            _gsize, _gmtime = _st.st_size, _st.st_mtime_ns
        except OSError:
            _gsize, _gmtime = -1, -1
        _cur_exp = self._current_export_params()
        self._export_snapshot = {
            # deepcopy: sonraki bir slice _slice_snapshot'i degistirse bile bu
            # export'un dayandigi snapshot BAGIMSIZ kalir (referans paylasmaz).
            "slice_snapshot": deepcopy(self._slice_snapshot),
            "origins": _cur_exp["origins"],
            "active_tool": _cur_exp["active_tool"],
            "print_speed": _cur_exp["print_speed"],
            "gcode_path": path,
            "gcode_size": _gsize,
            "gcode_mtime_ns": _gmtime,
        }

        if is_well:
            wells_txt = ", ".join(w for w, _, _ in origins)
            QMessageBox.information(
                self, "G-Code Hazır",
                f"G-Code kaydedildi:\n{path}\n\n"
                f"{moves} ekstrüzyon hamlesi · {len(self._slices)} katman.\n"
                f"Selected wells: {wells_txt}\nCopies: {len(origins)} · "
                f"{active_tool} · {speed_mms:.0f} mm/s")
        else:
            QMessageBox.information(
                self, "G-Code Hazır",
                f"G-Code kaydedildi:\n{path}\n\n"
                f"{moves} ekstrüzyon hamlesi · {len(self._slices)} katman.\n"
                f"Kuyu: yok (yatak merkezi) · Origin ({bed_cx:.1f}, {bed_cy:.1f}) · "
                f"{active_tool} · {speed_mms:.0f} mm/s")

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
