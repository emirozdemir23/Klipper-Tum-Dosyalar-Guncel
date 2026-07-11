"""Shared helpers for the Klipper_GUI regression tests.

Runnable WITHOUT pytest: each test file has a __main__ that runs its checks and
exits non-zero on failure. If pytest IS installed the ``test_*`` functions are
also collectable. Qt runs offscreen (no real GL); these are logic/state tests,
NOT live-GUI tests.
"""
import os, sys, tempfile, math
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# make the project root importable when run as `python tests/test_x.py`
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import numpy as np
import pyvista as pv
from PyQt6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])


def slice_mesh(mesh, layer_h=0.2, distance=0.4):
    """Drive the REAL SliceWorker.run() synchronously; return a dict with
    slices/infills/ghost or {'error': msg}."""
    from core.slicer_worker import SliceWorker
    f = tempfile.NamedTemporaryFile(suffix=".stl", delete=False); f.close()
    mesh.save(f.name)
    w = SliceWorker(f.name, layer_h, distance)
    cap = {}
    w.finished.connect(lambda s, lm, inf, om: cap.update(slices=s, layer_meshes=lm,
                                                         infills=inf, ghost=om))
    w.error.connect(lambda m: cap.update(error=m))
    w.run()
    os.unlink(f.name)
    return cap


def seg_dirs(pd):
    """(#horizontal, #vertical, #diagonal, #zero_length) for a line PolyData."""
    if pd is None or getattr(pd, "n_points", 0) == 0:
        return (0, 0, 0, 0)
    lines = np.asarray(pd.lines).reshape(-1, 3)
    ids = lines[:, 1:]
    p = np.asarray(pd.points)
    h = v = d = z = 0
    for a, b in ids:
        dx = abs(p[a, 0] - p[b, 0]); dy = abs(p[a, 1] - p[b, 1])
        if dx < 1e-9 and dy < 1e-9:
            z += 1
        elif dy < 1e-6:
            h += 1
        elif dx < 1e-6:
            v += 1
        else:
            d += 1
    return (h, v, d, z)


def seg_midpoints(pd):
    """(M,2) XY midpoints of each segment."""
    if pd is None or getattr(pd, "n_points", 0) == 0:
        return np.empty((0, 2))
    lines = np.asarray(pd.lines).reshape(-1, 3)
    ids = lines[:, 1:]
    p = np.asarray(pd.points)
    return (p[ids[:, 0], :2] + p[ids[:, 1], :2]) / 2.0


def has_nonfinite(pd):
    if pd is None or getattr(pd, "n_points", 0) == 0:
        return False
    return not np.all(np.isfinite(np.asarray(pd.points)))


class Checker:
    def __init__(self):
        self.rows = []
    def chk(self, name, cond, detail=""):
        self.rows.append((name, "PASS" if cond else "FAIL", str(detail)))
    def report_and_exit(self, title):
        print(f"\n===== {title} =====")
        w = max(len(n) for n, _, _ in self.rows) if self.rows else 10
        for n, r, d in self.rows:
            print(f"  {n:<{w}}  {r:<4} {d}")
        ok = all(r == "PASS" for _, r, _ in self.rows)
        print(f"  --> {'ALL_PASS' if ok else 'SOME_FAIL'} ({sum(r=='PASS' for _,r,_ in self.rows)}/{len(self.rows)})")
        if not ok:
            sys.exit(1)
