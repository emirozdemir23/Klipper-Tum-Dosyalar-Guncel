"""Entry point: configure OpenGL/VTK globals, then launch the main window.

IMPORTANT: this file must stay a THIN launcher. The real app lives in the
modular packages:
    ui/main_window.py   -> KlipperArayuzu (controller)
    core/slicer_worker.py -> SliceWorker (vectorized 2D slicer)

If this file is ever replaced by the old monolith again, slicing reverts to the
slow per-layer mesh.slice()/clip_box() path (~minutes). The legacy monolith is
kept only as main.py.monolith.bak — do NOT copy it back over main.py.
"""
from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QSurfaceFormat


def _configure_opengl() -> None:
    """RPi4-safe surface format: no forced version/profile, MSAA off.

    Forcing OpenGL 3.2 *Core* (``setVersion(3, 2)`` + ``CoreProfile``) creates NO
    context on the Raspberry Pi 4, whose V3D driver only exposes OpenGL ES 3.1 —
    the result is a black viewport / VTK context loss. We drop the version+profile
    forcing so each platform falls back to its own safe default context (desktop
    GL on the dev box, GLES on the Pi). MSAA stays off for performance and to keep
    clear of the VTK 'Windows Error 2004' path.

    (If GLES must be pinned on the Pi specifically, set
    ``fmt.setRenderableType(QSurfaceFormat.RenderableType.OpenGLES)`` here — left
    off by default so it cannot break the Windows dev machine.)
    """
    fmt = QSurfaceFormat()
    fmt.setSamples(0)  # MSAA kapalı
    QSurfaceFormat.setDefaultFormat(fmt)


def _install_excepthook() -> None:
    """R6: Global istisna korumasi — kiosk'un tek noktadan olumunu engeller.

    PyQt6, bir slot/callback icindeki yakalanmamis Python istisnasinda
    sys.excepthook VARSAYILAN hook ise qFatal() ile TUM sureci abort eder
    (kiosk aninda olur). OZEL bir hook kuruluysa PyQt yalnizca hook'u cagirir
    ve uygulama YASAMAYA devam eder. Bu hook traceback'i stderr'e ve proje
    kokundeki crash.log'a yazar. BILEREK hicbir dialog/pencere ACMAZ: kiosk'ta
    modal = kilit riski ve istisna aninda GUI durumu belirsiz olabilir.
    """
    import traceback
    from datetime import datetime
    from pathlib import Path

    log_path = Path(__file__).resolve().parent / "crash.log"

    def _hook(exc_type, exc_value, exc_tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        print(f"[GUARD] Yakalanmamis istisna (uygulama yasatildi):\n{text}",
              file=sys.stderr)
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(f"\n--- {datetime.now().isoformat(timespec='seconds')} ---\n")
                fh.write(text)
        except OSError:
            pass   # disk dolu/salt-okunur: loglama ugruna asla cokme

    sys.excepthook = _hook


def main() -> int:
    _install_excepthook()   # R6: slot istisnalari artik qFatal/abort ETMEZ
    _configure_opengl()
    try:
        import vtk
        vtk.vtkObject.GlobalWarningDisplayOff()  # VTK konsol spam'ini durdur
    except ImportError:
        pass

    # Imported after the surface format is set, matching the original ordering.
    from ui.main_window import KlipperArayuzu

    app = QApplication(sys.argv)
    window = KlipperArayuzu()
    # KIOSK MODE: 800x480 RPi dokunmatik ekranda tam ekran baslat.
    # PC'de test icin: Esc -> tam ekrandan cik, F11 -> toggle (bkz. keyPressEvent).
    # Tam ekranda mahsur kalmamak icin Ayarlar sekmesinde "Exit Application" butonu var.
    window.showFullScreen()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
