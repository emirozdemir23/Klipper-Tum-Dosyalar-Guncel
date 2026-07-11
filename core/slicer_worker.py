"""Background STL slicing worker — PURE 2D, BATCHED, GEOS-free (minutes → seconds).

Pipeline (all heavy work delegated to one C++ pass or vectorized numpy):
  * BATCH SLICING: tag points with Z, call ``mesh.contour(isosurfaces=z_mids)``
    ONCE → every layer's cross-section in a single C++ pass. (slice_along_axis is
    NOT used — it is a hidden Python loop of .slice() calls.)
  * VECTORIZED BUCKETING: split the combined contour into per-layer PolyData via
    one ``np.searchsorted`` — no per-cell Python loop.
  * SCANLINE INFILL (numpy, no Shapely/GEOS): for each global grid line, compute
    its even-odd crossings with the layer's contour edges and pair them into
    in-shape spans. Holes/islands are handled automatically because the contour
    contains every ring. This replaced a Shapely ``polygon.intersection(grid)``
    that cost ~40 ms/layer of irreducible GEOS overlay time.

THREAD SAFETY: every emitted dataset is freshly built from numpy (no VTK
pipeline) → standalone; the only pipeline-bound object (the full mesh) is
deep-copied for the ghost overlay.

Profiling: run() prints '[SLICE-PROF]' timings per block so the optimized path
can be confirmed at runtime.
"""
from __future__ import annotations

import math
import time

from PyQt6.QtCore import QObject, pyqtSignal

# Hard ceilings — defend against runaway memory from degenerate input.
MAX_LAYERS = 50_000
MAX_INFILL_SEGMENTS = 200_000
DECIMATE_FACE_THRESHOLD = 30_000


def _bucket_contours_by_layer(contours, z_mids):
    """Split one combined contour PolyData into per-layer line PolyData.

    Each contour point's Z equals the isovalue it was contoured at, so segments
    are assigned to layers via a vectorized ``searchsorted`` on the midpoints
    between consecutive ``z_mids``. Returns a list (len == n_layers) of PolyData
    or None. Each PolyData is freshly built from numpy → standalone/thread-safe.
    """
    import numpy as np
    import pyvista as pv

    n = len(z_mids)
    buckets = [None] * n
    if contours is None or getattr(contours, 'n_points', 0) == 0:
        return buckets

    pts = np.asarray(contours.points)
    lines = np.asarray(contours.lines)
    if lines.size == 0:
        return buckets

    # Parse VTK line cells into 2-point segments (handles polylines too).
    if lines.size % 3 == 0 and np.all(lines.reshape(-1, 3)[:, 0] == 2):
        segs = lines.reshape(-1, 3)[:, 1:]                # (S, 2) fast path
    else:
        seg_pairs = []
        i = 0
        L = len(lines)
        while i < L:
            npts = int(lines[i])
            if npts >= 2:
                ids = lines[i + 1:i + 1 + npts]
                for a, b in zip(ids[:-1], ids[1:]):
                    seg_pairs.append((a, b))
            i += npts + 1
        if not seg_pairs:
            return buckets
        segs = np.asarray(seg_pairs, dtype=np.int64)

    seg_z = pts[segs[:, 0], 2]
    if n > 1:
        edges = (z_mids[:-1] + z_mids[1:]) / 2.0
        layer_idx = np.searchsorted(edges, seg_z)
    else:
        layer_idx = np.zeros(len(segs), dtype=np.int64)
    np.clip(layer_idx, 0, n - 1, out=layer_idx)

    order = np.argsort(layer_idx, kind='stable')
    layer_idx = layer_idx[order]
    segs = segs[order]
    uniq, starts = np.unique(layer_idx, return_index=True)
    starts = list(starts) + [len(segs)]

    for k, li in enumerate(uniq):
        grp = segs[starts[k]:starts[k + 1]]
        if grp.shape[0] == 0:
            continue
        flat = grp.reshape(-1)
        uniq_ids, inv = np.unique(flat, return_inverse=True)
        local_pts = pts[uniq_ids]
        inv = inv.reshape(-1, 2)
        m = inv.shape[0]
        conn = np.empty((m, 3), dtype=np.int64)
        conn[:, 0] = 2
        conn[:, 1:] = inv
        pd = pv.PolyData()
        pd.points = local_pts.astype(np.float64)
        pd.lines = conn.reshape(-1).astype(np.int32)
        buckets[int(li)] = pd

    return buckets


def _build_master_grid(bounds, distance):
    """Pre-compute GLOBAL infill scan-line positions ONCE: (xs, ys) numpy arrays.

    Anchoring to global bounds keeps infill aligned across layers. Returns None on
    degenerate bounds / distance / segment-count overflow.
    """
    if distance is None or distance <= 0:
        return None
    import numpy as np
    x_min, x_max, y_min, y_max = bounds[0], bounds[1], bounds[2], bounds[3]
    if not all(math.isfinite(v) for v in (x_min, x_max, y_min, y_max)):
        return None
    if x_max <= x_min or y_max <= y_min:
        return None
    est = (x_max - x_min) / distance + (y_max - y_min) / distance
    if not math.isfinite(est) or est > MAX_INFILL_SEGMENTS:
        return None
    xs = np.arange(x_min, x_max + distance * 0.5, distance)
    ys = np.arange(y_min, y_max + distance * 0.5, distance)
    if xs.size == 0 and ys.size == 0:
        return None
    return (xs, ys)


def _slc_segments_xy(slc):
    """Extract slice line-cells as a vectorized (S, 2, 2) array of XY segments."""
    import numpy as np
    lines = np.asarray(slc.lines)
    pts = np.asarray(slc.points)
    if lines.size == 0:
        return None
    if lines.size % 3 == 0:
        tri = lines.reshape(-1, 3)
        if np.all(tri[:, 0] == 2):
            ids = tri[:, 1:]
            return pts[ids][:, :, :2]
    segs = []
    i = 0
    L = len(lines)
    while i < L:
        npts = int(lines[i])
        if npts >= 2:
            idl = lines[i + 1:i + 1 + npts]
            for a, b in zip(idl[:-1], idl[1:]):
                segs.append([[pts[a][0], pts[a][1]], [pts[b][0], pts[b][1]]])
        i += npts + 1
    return np.asarray(segs, dtype=np.float64) if segs else None


def _scan_axis(p1, p2, q1, q2, levels):
    """Even-odd scanline crossings along one axis.

    p1,p2 = the crossing-axis coords of each contour-edge endpoint (e.g. y for
    horizontal scans); q1,q2 = the other axis (x). For each level in ``levels``
    finds edges straddling it, interpolates the q crossing, sorts, and pairs
    (even-odd) into inside spans. Returns (level_per_span, q_a, q_b) arrays.
    """
    import numpy as np
    out_lvl = []
    out_a = []
    out_b = []
    lo = np.minimum(p1, p2)
    hi = np.maximum(p1, p2)
    denom = (p2 - p1)
    for lv in levels:
        # half-open [lo, hi) avoids double-counting shared vertices / flat edges
        mask = (lo <= lv) & (lv < hi)
        if not mask.any():
            continue
        a = p1[mask]
        t = (lv - a) / denom[mask]
        q = q1[mask] + t * (q2[mask] - q1[mask])
        q.sort()
        npair = q.shape[0] // 2
        if npair == 0:
            continue
        qa = q[0:2 * npair:2]
        qb = q[1:2 * npair:2]
        out_lvl.append(np.full(npair, lv))
        out_a.append(qa)
        out_b.append(qb)
    if not out_lvl:
        return None
    return (np.concatenate(out_lvl), np.concatenate(out_a), np.concatenate(out_b))


def build_infill_grid_2d(slc, z_mid, grid, orientation=0):
    """Pure-numpy scanline infill (NO Shapely/GEOS) — LINEAR (tek yon/katman).

    ``grid`` = (xs, ys) global scan-line positions.
    ``orientation``: 0 = YALNIZCA yatay cizgiler (sabit y); 1 = YALNIZCA dikey
    (sabit x). Cagiran ``i % 2`` gecerek ardisik katmanlarda yonu 90° dondurur
    (gercek Linear infill; ayni katmanda cross-hatch URETILMEZ). Contour ile
    even-odd kesisim delik/ada'lari tek yonde de otomatik klipler (hole
    doldurulmaz). Bir fresh PyVista PolyData ya da bos ise None dondurur.
    """
    if grid is None or slc is None:
        return None
    import numpy as np
    import pyvista as pv

    seg = _slc_segments_xy(slc)
    if seg is None or len(seg) == 0:
        return None
    x1 = seg[:, 0, 0]; y1 = seg[:, 0, 1]
    x2 = seg[:, 1, 0]; y2 = seg[:, 1, 1]

    grid_xs, grid_ys = grid
    bminx = min(x1.min(), x2.min()); bmaxx = max(x1.max(), x2.max())
    bminy = min(y1.min(), y2.min()); bmaxy = max(y1.max(), y2.max())

    pts_a = []  # (x, y) span endpoints, collected as arrays
    pts_b = []

    # LINEAR: tek yon/katman. orientation 0 -> yatay (sabit y), 1 -> dikey (sabit x).
    if orientation == 0:
        # Horizontal scan lines (constant y): crossing axis = y, other = x.
        ys = grid_ys[(grid_ys >= bminy) & (grid_ys <= bmaxy)]
        if ys.size:
            r = _scan_axis(y1, y2, x1, x2, ys)
            if r is not None:
                lvl, xa, xb = r
                pts_a.append(np.column_stack([xa, lvl]))
                pts_b.append(np.column_stack([xb, lvl]))
    else:
        # Vertical scan lines (constant x): crossing axis = x, other = y.
        xs = grid_xs[(grid_xs >= bminx) & (grid_xs <= bmaxx)]
        if xs.size:
            r = _scan_axis(x1, x2, y1, y2, xs)
            if r is not None:
                lvl, ya, yb = r
                pts_a.append(np.column_stack([lvl, ya]))
                pts_b.append(np.column_stack([lvl, yb]))

    if not pts_a:
        return None
    A = np.vstack(pts_a)        # (P, 2) span starts
    B = np.vstack(pts_b)        # (P, 2) span ends
    P = A.shape[0]
    if P == 0:
        return None

    pts3 = np.empty((2 * P, 3), dtype=np.float64)
    pts3[0::2, :2] = A
    pts3[1::2, :2] = B
    pts3[:, 2] = z_mid
    conn = np.empty((P, 3), dtype=np.int64)
    conn[:, 0] = 2
    conn[:, 1] = np.arange(0, 2 * P, 2)
    conn[:, 2] = np.arange(1, 2 * P, 2)

    pd = pv.PolyData()
    pd.points = pts3
    pd.lines = conn.reshape(-1).astype(np.int32)
    return pd


class SliceWorker(QObject):
    # 4th arg = the centered, decimated full model (deep-copied) for the ghost
    # overlay. Read on THIS worker thread so the GUI thread never calls pv.read.
    finished = pyqtSignal(list, list, list, object)  # (slices, layer_meshes, infills, centered_original_mesh)
    error    = pyqtSignal(str)
    progress = pyqtSignal(int)                        # 0..100 for the UI progress bar
    aborted  = pyqtSignal()                           # cooperative cancel acknowledged

    def __init__(self, stl_path: str, layer_h: float, distance: float) -> None:
        super().__init__()
        self._stl_path = stl_path
        self._layer_h  = layer_h
        self._distance = distance
        self._abort    = False

    def request_abort(self) -> None:
        """GUI thread'inden cagrilir: ISBIRLIKCI iptal (GIL-atomik bool set).

        run() bayragi blok sinirlarinda ve infill dongusunun her adiminda yoklar;
        gorunce finished/error YERINE ``aborted`` emit edip milisaniyeler icinde
        doner. Boylece kapanista/model degisiminde QThread.terminate() (RPi4'te
        yarim VTK durumu + kilitli GIL riski) son care olmaktan cikar.
        """
        self._abort = True

    def _abort_requested(self) -> bool:
        """True donerse cagiran HEMEN return etmeli; aborted sinyali atilmistir."""
        if not self._abort:
            return False
        print("[SLICE-PROF] === ABORTED (iptal istegi goruldu, temiz cikis) ===",
              flush=True)
        self.aborted.emit()
        return True

    def run(self) -> None:
        # ───────────────────────── PROFILING ─────────────────────────
        # Per-block timing. Prefix '[SLICE-PROF]' so the user can confirm in the
        # console that the OPTIMIZED worker is running and see where time goes.
        t_start = time.perf_counter()
        try:
            import shapely  # infill no longer needs it; kept only for the report
            shp_ver = getattr(shapely, '__version__', '?')
            has_shapely = True
        except Exception:
            has_shapely, shp_ver = False, None
        print(f"[SLICE-PROF] === SliceWorker (VECTORIZED/2D, scanline infill) start === "
              f"has_shapely={has_shapely} shapely={shp_ver} (infill=numpy, no GEOS) "
              f"layer_h={self._layer_h} distance={self._distance}", flush=True)

        try:
            import pyvista as pv
            import numpy as np

            if self._layer_h is None or self._layer_h <= 0:
                self.error.emit("Katman kalınlığı 0'dan büyük olmalı.")
                return

            # Grid Distance dogrulamasi (Linear infill HER ZAMAN aktif): None / 0 /
            # negatif / NaN / Inf gecersizdir → Slice BASARISIZ (finished ATMAZ).
            try:
                _d = float(self._distance) if self._distance is not None else None
            except (TypeError, ValueError):
                _d = None
            if _d is None or not math.isfinite(_d) or _d <= 0.0:
                self.error.emit("Grid Distance 0'dan büyük ve sonlu bir değer olmalıdır.")
                return

            # --- READ + MultiBlock reduce ---
            t0 = time.perf_counter()
            try:
                mesh = pv.read(self._stl_path)
            except Exception as exc:
                self.error.emit(f"Dosya okunamadı veya bozuk:\n{exc}")
                return
            if mesh is None:
                self.error.emit("Model okunamadı (boş sonuç).")
                return
            if not hasattr(mesh, 'bounds') or not hasattr(mesh, 'center'):
                try:
                    mesh = mesh.combine()
                except Exception:
                    self.error.emit("Desteklenmeyen model tipi.")
                    return
            if getattr(mesh, 'n_points', 0) == 0:
                self.error.emit("Model boş veya geometri içermiyor.")
                return
            try:
                n_cells0 = int(mesh.n_cells)
            except Exception:
                n_cells0 = 0
            print(f"[SLICE-PROF] read+combine: {time.perf_counter()-t0:.3f}s ({n_cells0} cells)", flush=True)
            self.progress.emit(5)
            if self._abort_requested():
                return

            # --- DECIMATION (decimate_pro → decimate() fallback) ---
            t0 = time.perf_counter()
            if n_cells0 > DECIMATE_FACE_THRESHOLD:
                target = min(0.95, max(0.5, 1.0 - DECIMATE_FACE_THRESHOLD / float(n_cells0)))
                deci = None
                try:
                    deci = mesh.triangulate().decimate_pro(target, preserve_topology=True)
                except Exception as e:
                    print(f"[SLICE-PROF] decimate_pro FAILED: {e}", flush=True)
                if deci is None or getattr(deci, 'n_cells', 0) == 0 or deci.n_cells >= n_cells0:
                    try:
                        deci = mesh.triangulate().decimate(target)
                        print("[SLICE-PROF] used decimate() fallback", flush=True)
                    except Exception as e:
                        print(f"[SLICE-PROF] decimate() FALLBACK FAILED: {e}", flush=True)
                        deci = None
                if deci is not None and 0 < deci.n_cells < n_cells0:
                    mesh = deci
                print(f"[SLICE-PROF] decimate: {time.perf_counter()-t0:.3f}s "
                      f"({n_cells0} -> {mesh.n_cells} cells, target={target:.2f})", flush=True)
            else:
                print(f"[SLICE-PROF] decimate: skipped ({n_cells0} <= {DECIMATE_FACE_THRESHOLD})", flush=True)
            if self._abort_requested():
                return

            # --- CENTER + ghost deep-copy ---
            t0 = time.perf_counter()
            c_xy = mesh.center[:2]
            z_min = mesh.bounds[4]
            mesh = mesh.translate((-c_xy[0], -c_xy[1], -z_min))
            # RPi4 (2 GB): SHALLOW copy. A deep copy duplicates the whole point/
            # cell array set right before the cross-thread emit, doubling peak RAM
            # and risking an OOM kernel kill. `mesh` is only translated BEFORE this
            # line; afterwards it just gains a '__z' scalar (shared array, harmless
            # for a flat ghost) and contour() returns a NEW object — `mesh` itself
            # is never mutated destructively, so the shared geometry stays valid.
            centered_original_mesh = mesh.copy(deep=False)
            print(f"[SLICE-PROF] center+ghost-copy: {time.perf_counter()-t0:.3f}s", flush=True)
            self.progress.emit(15)

            total_h = mesh.bounds[5] - mesh.bounds[4]
            if not math.isfinite(total_h):
                self.error.emit("Model sınırları geçersiz (NaN/Inf).")
                return
            n_layers = max(1, math.ceil(total_h / self._layer_h - 1e-9))
            if n_layers > MAX_LAYERS:
                self.error.emit(f"Çok fazla katman ({n_layers}). Lütfen katman kalınlığını artırın.")
                return

            z_min = mesh.bounds[4]
            top_z = mesh.bounds[5]
            z_mids = np.empty(n_layers, dtype=np.float64)
            for i in range(n_layers):
                z_mid = z_min + i * self._layer_h + self._layer_h / 2.0
                if z_mid >= top_z:
                    z_mid = top_z - self._layer_h * 0.01
                z_mids[i] = z_mid

            # --- BATCH SLICE: one C++ contour pass ---
            t0 = time.perf_counter()
            try:
                mesh.point_data['__z'] = np.ascontiguousarray(mesh.points[:, 2], dtype=np.float64)
                contours = mesh.contour(isosurfaces=z_mids.tolist(), scalars='__z')
            except Exception as exc:
                self.error.emit(f"Dilimleme (contour) başarısız:\n{exc}")
                return
            print(f"[SLICE-PROF] contour ({n_layers} layers): {time.perf_counter()-t0:.3f}s", flush=True)
            self.progress.emit(30)
            if self._abort_requested():
                return

            # --- BUCKET into per-layer PolyData ---
            t0 = time.perf_counter()
            per_layer = _bucket_contours_by_layer(contours, z_mids)
            # RPi4 (2 GB): kombine kontur TUM katmanlarin nokta/segment kopyasini
            # tutar; bucket'lar kendi kopyalarini cikardi (fancy indexing) → artik
            # gereksiz. Infill dongusu boyunca on MB'larca fazladan tutmamak icin
            # burada birak.
            del contours
            print(f"[SLICE-PROF] bucket: {time.perf_counter()-t0:.3f}s", flush=True)
            self.progress.emit(40)
            if self._abort_requested():
                return

            # --- MASTER GRID positions (built ONCE) ---
            t0 = time.perf_counter()
            master_grid = None
            if self._distance and self._distance > 0:
                master_grid = _build_master_grid(mesh.bounds, self._distance)
                # Infill ISTENDI (distance>0) ama izgara kurulamadi (Grid Distance cok
                # kucuk -> segment guvenlik sinirini asti, ya da model sinirlari
                # gecersiz). Slice SESSIZCE infill'siz TAMAMLANMAZ: hata ver, finished ATMA.
                # (Aksi halde "tamamen infills=None" bir slice basarili gorunurdu.)
                if master_grid is None:
                    self.error.emit(
                        "Infill icin tarama izgarasi olusturulamadi.\n"
                        "Grid Distance cok kucuk (guvenlik sinirini asti) ya da model "
                        "sinirlari gecersiz. Grid Distance'i buyutun veya modeli kontrol edin.")
                    return
            ng = 0 if master_grid is None else (master_grid[0].size + master_grid[1].size)
            print(f"[SLICE-PROF] master_grid: {time.perf_counter()-t0:.3f}s (lines={ng})", flush=True)
            self.progress.emit(50)

            # --- INFILL LOOP (numpy scanline) ---
            t0 = time.perf_counter()
            slices = []
            layer_meshes = []
            infills = []
            infill_fail = 0
            first_infill_err = None   # (layer_idx, "Type: msg") — ilk infill hatasi
            last_pct = 50  # emit only when the integer % changes (avoid signal flood)
            for i in range(n_layers):
                if self._abort_requested():   # ucuz bool kontrolu, her katmanda
                    return
                slc = per_layer[i]
                slices.append(slc)
                layer_meshes.append(slc)   # gate-only, never mutated → share
                if master_grid is not None and slc is not None:
                    try:
                        # LINEAR: ardisik katmanda yon 90° doner (cift=yatay, tek=dikey).
                        infill = build_infill_grid_2d(slc, float(z_mids[i]), master_grid,
                                                      orientation=i % 2)
                    except Exception as _e:
                        # SESSIZ YUTMA YOK: katman numarasiyla logla + ILK hatayi sakla.
                        infill = None
                        infill_fail += 1
                        if first_infill_err is None:
                            first_infill_err = (i, f"{type(_e).__name__}: {_e}")
                        print(f"[SLICE-PROF] Layer {i} infill generation failed: "
                              f"{type(_e).__name__}: {_e}", flush=True)
                else:
                    infill = None
                infills.append(infill)
                pct = 50 + int((i / n_layers) * 49)   # 50 → 99 across the loop
                if pct != last_pct:
                    self.progress.emit(pct)
                    last_pct = pct
            print(f"[SLICE-PROF] infill loop ({n_layers} layers): {time.perf_counter()-t0:.3f}s "
                  f"(infill_fail={infill_fail})", flush=True)

            if self._abort_requested():   # son kontrol: iptal edilen is finished ATMAZ
                return

            # --- GECERLILIK (Section G): tamamen bos slice BASARILI sayilmaz ---
            valid_contours = sum(1 for s in slices
                                 if s is not None and getattr(s, 'n_points', 0) > 0)
            if valid_contours == 0:
                self.error.emit(
                    "Dilimleme bos: hicbir katmanda gecerli kontur uretilemedi.\n"
                    "Model kapali/gecerli bir yuzey mi, yoksa katman kalinligi cok mu buyuk?")
                return
            if not (len(slices) == len(layer_meshes) == len(infills)):
                self.error.emit("Ic hata: dilim listelerinin uzunluklari tutarsiz.")
                return
            # Section 4: infill HATASI SESSIZCE GECILMEZ. Grid istendigi halde herhangi
            # bir katmanda infill uretilemediyse Slice BASARISIZ olur (finished ATILMAZ);
            # boylece yarim/eksik dolgu "Slice tamamlandi" gibi gorunmez.
            if infill_fail > 0:
                _li, _msg = first_infill_err if first_infill_err else (-1, "?")
                self.error.emit(
                    f"{infill_fail} katmanda infill uretilemedi (ilk hata Layer {_li}: {_msg}).\n"
                    "Slice iptal edildi; lutfen modeli/ayarlari kontrol edip yeniden deneyin.")
                return
            print(f"[SLICE-PROF] === TOTAL run(): {time.perf_counter()-t_start:.3f}s "
                  f"for {n_layers} layers ===", flush=True)
            self.progress.emit(100)
            self.finished.emit(slices, layer_meshes, infills, centered_original_mesh)
        except Exception as exc:
            # Konsola tam traceback (teshis icin), GUI'ye tip adiyla kisa mesaj —
            # str(exc) bos olabilen numpy/VTK hatalarinda bos dialog cikmasin.
            import traceback
            traceback.print_exc()
            self.error.emit(f"{type(exc).__name__}: {exc}")
