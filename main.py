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


def main() -> int:
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
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
