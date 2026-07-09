"""core/gcode_exporter.py — continuous-extrusion G-code post-processor.

Turns the slicer's per-layer line PolyData (perimeters + infill) into a flat
G-code program for the bioprinter's "1 dummy extruder + N extruder_stepper"
architecture. RELATIVE extrusion (M83) is used so each move's E is simply the
segment length × flow_multiplier.

RPi4 (2 GB) memory notes:
  * The file is written layer-by-layer (streamed) — there is never a
    whole-program string buffer in RAM.
  * The greedy nearest-neighbour planner allocates only small per-layer numpy
    scratch arrays ((S,) distances, (S,2) endpoints) — one layer at a time.

No heavy libraries: only `math` + `numpy`.
"""
from __future__ import annotations

import math
import os

import numpy as np

# Gaps longer than this (mm) become a non-printing travel move; shorter ones are
# bridged by the next extrusion (continuous bioprinting tolerates sub-mm stitch).
_TRAVEL_THRESHOLD = 1.5

# Klipper [stepper_x]/[stepper_y] limitleri (klipper.txt: position_min 0,
# position_max 230/120). Bu araligin disina yazilan TEK bir koordinat bile
# calisirken "Move out of range" ile baskiyi yarida kesecegi icin dosya
# YAZILMADAN once dogrulanir.
_BED_X_MAX = 230.0
_BED_Y_MAX = 120.0

# XY feedrate tavani = klipper.txt [printer] max_velocity: 30 mm/s (1800 mm/dk).
# Klipper fazlasini zaten sessizce kirpar; dosyaya makinenin YAPABILDIGI degeri
# yazmak dosyayi durust tutar. SIRINGA GUVENLIGI NOTU: G1 X..Y..E.. hamlesinde F
# takim kafasi hizidir, siringa E hizi = XY_hizi x flow_multiplier
# (30 mm/s x 0.05 = 1.5 mm/s -> 0.8 mm/tur recine vidasinda ~112 RPM, guvenli).
# Bu dosyada E-only hamle YOK; tek E-only komut PRINT_END'in G1 E-0.5 F60'idir.
_MAX_XY_FEED = 1800.0


def _segments_xy(pd) -> np.ndarray:
    """Extract 2-point XY line segments from a pyvista line PolyData.

    Returns an (S, 2, 2) float array indexed [segment, 0=start/1=end, 0=x/1=y],
    or an empty (0, 2, 2) array. Parses VTK line cells, handling both pure
    2-point segments (fast path) and longer polylines (split into consecutive
    pairs) — the same disconnected "line soup" the vectorized slicer emits.
    """
    if pd is None or getattr(pd, 'n_points', 0) == 0:
        return np.empty((0, 2, 2), dtype=np.float64)
    pts = np.asarray(pd.points, dtype=np.float64)[:, :2]
    lines = np.asarray(pd.lines)
    if lines.size == 0:
        return np.empty((0, 2, 2), dtype=np.float64)

    # Fast path: every cell is a 2-point segment → [2, a, b, 2, c, d, ...].
    if lines.size % 3 == 0 and np.all(lines.reshape(-1, 3)[:, 0] == 2):
        idx = lines.reshape(-1, 3)[:, 1:]
    else:
        # General path: walk variable-length polyline cells, pair consecutive ids.
        pairs = []
        i, L = 0, len(lines)
        while i < L:
            n = int(lines[i])
            if n >= 2:
                ids = lines[i + 1:i + 1 + n]
                for a, b in zip(ids[:-1], ids[1:]):
                    pairs.append((a, b))
            i += n + 1
        if not pairs:
            return np.empty((0, 2, 2), dtype=np.float64)
        idx = np.asarray(pairs, dtype=np.int64)

    a = pts[idx[:, 0]]
    b = pts[idx[:, 1]]
    return np.stack([a, b], axis=1)


def _order_layer_segments(segs: np.ndarray, start_xy):
    """Greedy nearest-neighbour ordering of ONE layer's segments.

    Yields (entry_xy, exit_xy, gap) per segment: ``gap`` is the travel distance
    from the previous exit to this segment's nearer endpoint (either end may be
    the entry — whichever is closer, so segments are traversed in the cheaper
    direction). O(S^2) but each pick is vectorised; only (S,) scratch is held,
    so it stays RPi4 memory-safe.
    """
    s = segs.shape[0]
    if s == 0:
        return
    starts = segs[:, 0, :]
    ends = segs[:, 1, :]
    visited = np.zeros(s, dtype=bool)
    cur = np.asarray(start_xy, dtype=np.float64)

    for _ in range(s):
        ds = np.hypot(starts[:, 0] - cur[0], starts[:, 1] - cur[1])
        de = np.hypot(ends[:, 0] - cur[0], ends[:, 1] - cur[1])
        ds[visited] = np.inf
        de[visited] = np.inf
        si = int(np.argmin(ds))
        ei = int(np.argmin(de))
        if ds[si] <= de[ei]:
            seg, entry, exit_, gap = si, starts[si], ends[si], ds[si]
        else:
            seg, entry, exit_, gap = ei, ends[ei], starts[ei], de[ei]
        visited[seg] = True
        cur = exit_
        yield entry, exit_, float(gap)


def _xy_extents(slices: list, infills: list):
    """Tum katmanlarin (perimetre + infill) HAM XY min/max'i — katman katman.

    Segmentler bir kerede TEK katman icin cikarilir (RPi4 RAM disiplini yazma
    dongusuyle ayni). Hic segment yoksa None doner.
    """
    xmin = ymin = math.inf
    xmax = ymax = -math.inf
    found = False
    has_infills = bool(infills)
    for i in range(len(slices)):
        for pd in (slices[i], infills[i] if has_infills and i < len(infills) else None):
            seg = _segments_xy(pd)
            if seg.shape[0] == 0:
                continue
            found = True
            xmin = min(xmin, float(seg[:, :, 0].min()))
            xmax = max(xmax, float(seg[:, :, 0].max()))
            ymin = min(ymin, float(seg[:, :, 1].min()))
            ymax = max(ymax, float(seg[:, :, 1].max()))
    return (xmin, xmax, ymin, ymax) if found else None


def generate_gcode(slices: list, infills: list, save_path: str,
                   layer_height: float = 0.2, flow_multiplier: float = 0.05,
                   active_tool: str = "T0",
                   origin_x: float = 120.0, origin_y: float = 60.0,
                   print_speed: float = 600.0,
                   x_max: float = _BED_X_MAX, y_max: float = _BED_Y_MAX) -> int:
    """Write a continuous-extrusion G-code program to ``save_path``.

    Args:
        slices:  per-layer perimeter line PolyData (or None per layer).
        infills: per-layer infill line PolyData (or None / shorter / None list).
        save_path: output .gcode path.
        layer_height: mm per layer → Z of layer i is (i+1)*layer_height.
        flow_multiplier: relative E per mm of extruded travel (M83).
        active_tool: printhead macro emitted in the header (T0/T1/T2).
        origin_x, origin_y: XY shift (mm) added to every point so the slicer's
            origin-centred (signed) coords land in Klipper's positive build area
            (position_min: 0). Default = Klipper macro bed centre (X120, Y60); the
            caller passes (bed_centre + selected-well offset) when a well is picked.
        print_speed: extrusion (G1) feedrate in mm/min — _MAX_XY_FEED (1800 =
            30 mm/s, klipper.txt max_velocity) ile kelepcelenir. Travel (G0)
            _MAX_XY_FEED, Z inis/cikis F600 (10 mm/s < max_z_velocity 15) kullanir.
        x_max, y_max: Klipper eksen limitleri; kaydirilmis model bu araligin
            ([0, x_max] × [0, y_max]) disina tasarsa ValueError (dosya YAZILMAZ).

    Returns:
        int — number of extrusion (G1) moves written.

    Raises:
        ValueError: dilim verisi bos ya da model yatak limitleri disina tasiyor.

    Streams to disk layer-by-layer; only one layer's segments are ever in RAM.
    Yazim ONCE ``save_path + '.tmp'`` dosyasina yapilir, basariyla bitince
    os.replace ile atomik tasinir: yarim kalan bir yazim ASLA yazdirilabilir
    ama PRINT_END'siz (isiticilari acik birakan) bir dosya birakamaz.

    STATIONARY-BED SAFETY: header hicbir Z hamlesi icermez; homing +
    Z-offset dogrulamasi tamamen PRINT_START makrosunun kilidindedir
    (klipper.txt: home degilse ya da CALIBRATE_Z_OFFSET yapilmadiysa
    action_raise_error ile dosya daha ilk hamleden once iptal olur).
    """
    n_layers = len(slices)
    moves = 0
    has_infills = bool(infills)
    # Makine tavaninin ustunde feedrate yazma (bkz. _MAX_XY_FEED notu).
    print_speed = min(float(print_speed), _MAX_XY_FEED)
    travel_feed = _MAX_XY_FEED

    # ── ON-DOGRULAMA (dosyaya tek satir yazilmadan) ──
    ext = _xy_extents(slices, infills)
    if ext is None:
        raise ValueError("Dilim verisi bos: hicbir katmanda cizgi yok - G-code uretilmedi.")
    sx_min, sx_max, sy_min, sy_max = ext
    fx_min, fx_max = sx_min + origin_x, sx_max + origin_x
    fy_min, fy_max = sy_min + origin_y, sy_max + origin_y
    if fx_min < 0.0 or fx_max > x_max or fy_min < 0.0 or fy_max > y_max:
        raise ValueError(
            "Model yatak limitlerinin disina tasiyor:\n"
            f"X [{fx_min:.1f} .. {fx_max:.1f}] (izinli 0 .. {x_max:.0f}), "
            f"Y [{fy_min:.1f} .. {fy_max:.1f}] (izinli 0 .. {y_max:.0f}).\n"
            "Daha kucuk bir model kullanin ya da farkli bir kuyu/konum secin.")

    tmp_path = save_path + ".tmp"
    try:
        with open(tmp_path, 'w', encoding='utf-8') as f:
            # ── Header (relative extrusion is critical). Z HAMLESI YOK:
            # PRINT_START kilidi (home + Z-offset kalibre) gecilmeden dosya
            # zaten ilerleyemez; ilk Z inisi asagida, ilk XY yaklasimindan SONRA.
            f.write(f"PRINT_START\n{active_tool}\nG90\nG92 E0\nM83\n")

            cur_xy = (0.0, 0.0)
            first_descent_done = False   # ilk katmanda XY→Z sirasi (asagiya bkz.)
            for i in range(n_layers):
                slc = slices[i] if i < len(slices) else None
                inf = infills[i] if has_infills and i < len(infills) else None

                parts = []
                p_seg = _segments_xy(slc)
                if p_seg.shape[0]:
                    parts.append(p_seg)
                i_seg = _segments_xy(inf)
                if i_seg.shape[0]:
                    parts.append(i_seg)
                if not parts:
                    continue   # empty layer → no Z move, nothing to print
                segs = parts[0] if len(parts) == 1 else np.concatenate(parts, axis=0)
                # The slicer centres the model on the XY origin, so raw coords are
                # SIGNED (negative on half the bed). Klipper enforces position_min: 0
                # on X/Y → negative coords throw "Move out of range". Shift into the
                # positive build area (default = bed centre) so every X/Y is in range.
                if origin_x or origin_y:
                    segs = segs + np.array([origin_x, origin_y], dtype=np.float64)

                z = (i + 1) * layer_height
                buf = [f"; LAYER {i} (z={z:.3f})"]
                if first_descent_done:
                    # Katmanlar arasi Z YALNIZCA YUKARI gider → once Z, sonra XY
                    # (yeni yazilmis katmanin icinden gecmemek icin dogru sira).
                    buf.append(f"G0 Z{z:.3f} F600")

                for entry, exit_, gap in _order_layer_segments(segs, cur_xy):
                    if gap > _TRAVEL_THRESHOLD or not first_descent_done:
                        buf.append(f"G0 X{entry[0]:.3f} Y{entry[1]:.3f} F{travel_feed:.0f}")
                    if not first_descent_done:
                        # ILK inis: XY yaklasimini KALIBRASYON yuksekliginde
                        # (CALIBRATE_Z_OFFSET cikisinda gcode Z5) yapip Z'yi ancak
                        # ilk baski noktasinin UZERINDEyken indir. Z0.2'de kap/
                        # kuyu kenarinin uzerinden surtunerek gecmeyi onler.
                        buf.append(f"G0 Z{z:.3f} F600")
                        first_descent_done = True
                    seg_len = math.hypot(exit_[0] - entry[0], exit_[1] - entry[1])
                    e_val = seg_len * flow_multiplier
                    buf.append(f"G1 X{exit_[0]:.3f} Y{exit_[1]:.3f} E{e_val:.4f} F{print_speed:.0f}")
                    moves += 1
                    cur_xy = (float(exit_[0]), float(exit_[1]))

                f.write("\n".join(buf))
                f.write("\n")
                # Drop the layer's arrays before the next iteration (RPi4 RAM hygiene).
                del segs, parts, p_seg, i_seg

            # ── Footer ──
            f.write("PRINT_END\n")
    except BaseException:
        # Yarim dosya birakma: tmp'yi sil ve hatayi cagirana yukselt.
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    os.replace(tmp_path, save_path)   # atomik: hedefte ya TAM dosya olur ya hic

    return moves


def generate_gcode_multi_origin(slices: list, infills: list, save_path: str,
                                origins: list,
                                layer_height: float = 0.2,
                                flow_multiplier: float = 0.05,
                                active_tool: str = "T0",
                                print_speed: float = 600.0,
                                x_max: float = _BED_X_MAX,
                                y_max: float = _BED_Y_MAX,
                                inter_well_lift: float = 2.0) -> int:
    """Ayni dilim verisini BIRDEN COK origin'e (well) basan tek-dosya G-code.

    generate_gcode() ile ayni segment-cikarma / greedy siralama / on-dogrulama
    yardimcilarini kullanir; tek fark AYNI modelin her ``origin`` (kuyu) icin
    tekrar basilmasidir. Sira LAYER-MAJOR: her Z icin once tum kuyular basilir,
    sonra bir ust katmana gecilir (Z hep yukari).

    Args:
        origins: ``[(well_id, origin_x, origin_y), ...]`` — origin_x/y Klipper
            yatak koordinatidir (yatak merkezi 120/60 + kuyu yerel ofseti).
        inter_well_lift: kuyu/katman gecisinde XY hareketinden ONCE uygulanan
            guvenli Z kalkis payi (mm). 2.0 = yazili dolgunun uzerinden gecer.
            Z YALNIZCA yukari gider (sabit-yatak guvenligi korunur).
        Diger argumanlar generate_gcode() ile aynidir.

    Returns:
        int — yazilan toplam ekstruzyon (G1) hamlesi (tum kuyular).

    Raises:
        ValueError: origins bos, dilim verisi bos, ya da HERHANGI bir kuyu
            kopyasi yatak limitleri disina tasarsa (dosya YAZILMADAN).

    Header/footer TEK KEZ yazilir; ayri dosya URETILMEZ. generate_gcode()
    tek-origin yolu AYNEN korunur (bu fonksiyon onu cagirmaz, bozmaz).
    """
    n_layers = len(slices)
    moves = 0
    has_infills = bool(infills)
    if not origins:
        raise ValueError("origins bos: en az bir kuyu/origin gerekli.")
    print_speed = min(float(print_speed), _MAX_XY_FEED)
    travel_feed = _MAX_XY_FEED
    lift = max(0.0, float(inter_well_lift))

    # ── ON-DOGRULAMA: her origin (kuyu) icin AYRI limit kontrolu ──
    # Tek bir kuyu bile tasarsa hicbir sey yazilmaz (yarim/gecersiz dosya olmaz).
    ext = _xy_extents(slices, infills)
    if ext is None:
        raise ValueError("Dilim verisi bos: hicbir katmanda cizgi yok - G-code uretilmedi.")
    sx_min, sx_max, sy_min, sy_max = ext
    for wid, ox, oy in origins:
        fx_min, fx_max = sx_min + ox, sx_max + ox
        fy_min, fy_max = sy_min + oy, sy_max + oy
        if fx_min < 0.0 or fx_max > x_max or fy_min < 0.0 or fy_max > y_max:
            raise ValueError(
                f"Kuyu '{wid}' yatak limitlerinin disina tasiyor:\n"
                f"X [{fx_min:.1f} .. {fx_max:.1f}] (izinli 0 .. {x_max:.0f}), "
                f"Y [{fy_min:.1f} .. {fy_max:.1f}] (izinli 0 .. {y_max:.0f}).\n"
                "Daha kucuk bir model kullanin ya da bu kuyuyu secimden cikarin.")

    tmp_path = save_path + ".tmp"
    try:
        with open(tmp_path, 'w', encoding='utf-8') as f:
            # Header TEK KEZ (relative extrusion). Z hamlesi yok — PRINT_START kilidi.
            f.write(f"PRINT_START\n{active_tool}\nG90\nG92 E0\nM83\n")

            cur_xy = (0.0, 0.0)
            first_descent_done = False   # tum baskinin ILK inisi
            for i in range(n_layers):
                slc = slices[i] if i < len(slices) else None
                inf = infills[i] if has_infills and i < len(infills) else None

                parts = []
                p_seg = _segments_xy(slc)
                if p_seg.shape[0]:
                    parts.append(p_seg)
                i_seg = _segments_xy(inf)
                if i_seg.shape[0]:
                    parts.append(i_seg)
                if not parts:
                    continue   # bos katman → hicbir kuyuda Z/baski yok
                base_segs = parts[0] if len(parts) == 1 else np.concatenate(parts, axis=0)
                z = (i + 1) * layer_height

                # Bu katmanda SIRAYLA her kuyu (layer-major).
                for wid, ox, oy in origins:
                    segs = base_segs + np.array([ox, oy], dtype=np.float64)
                    buf = [f"; LAYER {i} WELL {wid} (z={z:.3f})"]
                    well_started = False   # bu kuyunun ilk segmenti henuz basilmadi
                    for entry, exit_, gap in _order_layer_segments(segs, cur_xy):
                        if not first_descent_done:
                            # TUM baskinin ilk segmenti: XY yaklasimini kalibrasyon
                            # yuksekliginde yap, SONRA baski Z'sine in.
                            buf.append(f"G0 X{entry[0]:.3f} Y{entry[1]:.3f} F{travel_feed:.0f}")
                            buf.append(f"G0 Z{z:.3f} F600")
                            first_descent_done = True
                        elif not well_started:
                            # Yeni kuyu/katman gecisi: ONCE guvenli Z (lift), SONRA
                            # XY travel, SONRA baski Z'sine in — yazili dolgudan
                            # gecmeyi onler (Z hep yukari; sabit-yatak guvenligi).
                            buf.append(f"G0 Z{z + lift:.3f} F600")
                            buf.append(f"G0 X{entry[0]:.3f} Y{entry[1]:.3f} F{travel_feed:.0f}")
                            buf.append(f"G0 Z{z:.3f} F600")
                        elif gap > _TRAVEL_THRESHOLD:
                            buf.append(f"G0 X{entry[0]:.3f} Y{entry[1]:.3f} F{travel_feed:.0f}")
                        well_started = True
                        seg_len = math.hypot(exit_[0] - entry[0], exit_[1] - entry[1])
                        e_val = seg_len * flow_multiplier
                        buf.append(f"G1 X{exit_[0]:.3f} Y{exit_[1]:.3f} E{e_val:.4f} F{print_speed:.0f}")
                        moves += 1
                        cur_xy = (float(exit_[0]), float(exit_[1]))

                    f.write("\n".join(buf))
                    f.write("\n")
                    del segs
                del base_segs, parts, p_seg, i_seg

            # Footer TEK KEZ.
            f.write("PRINT_END\n")
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    os.replace(tmp_path, save_path)   # atomik

    return moves
