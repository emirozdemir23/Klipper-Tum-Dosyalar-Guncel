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

from core.printhead import PRINTHEAD_TO_TOOL

# Gaps longer than this (mm) become a non-printing travel move; shorter ones are
# bridged by the next extrusion (continuous bioprinting tolerates sub-mm stitch).
_TRAVEL_THRESHOLD = 1.5

# Klipper [stepper_x]/[stepper_y] limitleri (klipper.txt: position_min 0,
# position_max 230/120). Bu araligin disina yazilan TEK bir koordinat bile
# calisirken "Move out of range" ile baskiyi yarida kesecegi icin dosya
# YAZILMADAN once dogrulanir.
_BED_X_MAX = 230.0
_BED_Y_MAX = 120.0

# Makine Z tavani (mm). klipper.txt z1/z2 position_max ~82; guvenli tavan 83.
# En ust dolu katmanin baski Z'si (multi-origin'de + kuyu-gecis lift'i) bu degeri
# ASAMAZ; asarsa dosya YAZILMADAN ValueError verilir.
_BED_Z_MAX = 83.0

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


def _highest_nonempty_layer(slices: list, infills: list) -> int:
    """En ustteki (en buyuk index) BOS OLMAYAN katmanin indeksi; hicbiri yoksa -1.

    Ustten asagi tarar (dolu model icin ilk kontrolde durur). Bir katman, perimetre
    ya da infill segmenti varsa 'dolu' sayilir.
    """
    has_infills = bool(infills)
    for i in range(len(slices) - 1, -1, -1):
        s = _segments_xy(slices[i] if i < len(slices) else None)
        inf = _segments_xy(infills[i] if has_infills and i < len(infills) else None)
        if s.shape[0] or inf.shape[0]:
            return i
    return -1


def generate_gcode(slices: list, infills: list, save_path: str,
                   layer_height: float = 0.2, flow_multiplier: float = 0.05,
                   active_tool: str = PRINTHEAD_TO_TOOL[1],
                   origin_x: float = 120.0, origin_y: float = 60.0,
                   print_speed: float = 600.0,
                   x_max: float = _BED_X_MAX, y_max: float = _BED_Y_MAX,
                   z_max: float = _BED_Z_MAX) -> int:
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

    # ── Z TAVAN KONTROLU (tek origin: kuyu-gecis lift'i YOK) ──
    hi = _highest_nonempty_layer(slices, infills)
    highest_z = (hi + 1) * layer_height if hi >= 0 else 0.0
    if highest_z > z_max:
        raise ValueError(
            "Model yuksekligi makine Z sinirini asiyor:\n"
            f"gereken Z {highest_z:.2f} mm > izin {z_max:.1f} mm.\n"
            "Daha alcak bir model kullanin ya da katman sayisini azaltin.")

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

                # PERIMETRE ONCE, sonra INFILL: ikisi TEK NN havuzunda KARISMAZ
                # (duvarlar dolgudan once basilir). Her grup KENDI icinde greedy
                # nearest-neighbour siralanir. Bos katman atlanir.
                p_seg = _segments_xy(slc)
                i_seg = _segments_xy(inf)
                if p_seg.shape[0] == 0 and i_seg.shape[0] == 0:
                    continue   # empty layer → no Z move, nothing to print
                # The slicer centres the model on the XY origin → raw coords are
                # SIGNED. Klipper enforces position_min: 0 on X/Y → shift each group
                # into the positive build area (default = bed centre).
                shift = np.array([origin_x, origin_y], dtype=np.float64)
                groups = []
                if p_seg.shape[0]:
                    groups.append(p_seg + shift if (origin_x or origin_y) else p_seg)
                if i_seg.shape[0]:
                    groups.append(i_seg + shift if (origin_x or origin_y) else i_seg)

                z = (i + 1) * layer_height
                buf = [f"; LAYER {i} (z={z:.3f})"]
                if first_descent_done:
                    # Katmanlar arasi Z YALNIZCA YUKARI gider → once Z, sonra XY.
                    buf.append(f"G0 Z{z:.3f} F600")

                for grp in groups:   # [0]=perimetre, [1]=infill (varsa) — bu sirayla
                    for entry, exit_, gap in _order_layer_segments(grp, cur_xy):
                        if gap > _TRAVEL_THRESHOLD or not first_descent_done:
                            # Buyuk bosluk (ya da ilk yaklasim): BASKI YOK, entry'ye travel.
                            buf.append(f"G0 X{entry[0]:.3f} Y{entry[1]:.3f} F{travel_feed:.0f}")
                        elif gap > 1e-6:
                            # Sub-mm bosluk: surekli-baski dikisi. Kafa yine de segment
                            # ENTRY'sine ULASMALI → dikisi entry'ye EKSTRUZE et (E =
                            # gercek dikis uzunlugu). Eskiden dogrudan exit'e cizilen G1
                            # entry'yi atliyor + capraz geometri + E-uyusmazligi yapiyordu.
                            buf.append(f"G1 X{entry[0]:.3f} Y{entry[1]:.3f} "
                                       f"E{gap * flow_multiplier:.4f} F{print_speed:.0f}")
                            moves += 1
                            cur_xy = (float(entry[0]), float(entry[1]))
                        if not first_descent_done:
                            # ILK inis: XY yaklasimini KALIBRASYON yuksekliginde yapip
                            # Z'yi ancak ilk baski noktasinin UZERINDEyken indir.
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
                del groups, p_seg, i_seg

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
                                active_tool: str = PRINTHEAD_TO_TOOL[1],
                                print_speed: float = 600.0,
                                x_max: float = _BED_X_MAX,
                                y_max: float = _BED_Y_MAX,
                                z_max: float = _BED_Z_MAX,
                                inter_well_lift: float = 2.0) -> int:
    """Ayni dilim verisini BIRDEN COK origin'e (well) basan tek-dosya G-code.

    generate_gcode() ile ayni segment-cikarma / greedy siralama / on-dogrulama
    yardimcilarini kullanir; tek fark AYNI modelin her ``origin`` (kuyu) icin
    tekrar basilmasidir. Sira LAYER-MAJOR: her Z icin once tum kuyular basilir,
    sonra bir ust katmana gecilir. KATMANLAR arasi net ilerleme YUKARI dogrudur;
    ANCAK kuyu gecisinde once z+lift'e CIKILIR, XY travel yapilir, sonra ayni
    katmanin baski Z'sine geri INILIR (yani kuyu-ici gecis lift + descent icerir).

    Args:
        origins: ``[(well_id, origin_x, origin_y), ...]`` — origin_x/y Klipper
            yatak koordinatidir (yatak merkezi 120/60 + kuyu yerel ofseti).
        inter_well_lift: kuyu/katman gecisinde XY hareketinden ONCE uygulanan
            guvenli Z kalkis payi (mm). 2.0 = yazili dolgunun uzerinden gecer.
            Bu bir LIFT'tir: z+lift'e cikilir, XY travel, sonra baski Z'sine geri
            INILIR (kafa ayni katman Z'sine doner; "Z yalnizca yukari" DEGIL).
            En ust katman icin (highest_z + lift) makine Z tavanini (z_max) asamaz.
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

    # ── Z TAVAN KONTROLU (multi-origin: kuyu gecisinde en ust katman + lift'e cikilir) ──
    hi = _highest_nonempty_layer(slices, infills)
    highest_z = (hi + 1) * layer_height if hi >= 0 else 0.0
    if highest_z + lift > z_max:
        raise ValueError(
            "Model yuksekligi (+kuyu gecis lift'i) makine Z sinirini asiyor:\n"
            f"gereken Z {highest_z:.2f} + lift {lift:.1f} = {highest_z + lift:.2f} mm "
            f"> izin {z_max:.1f} mm.\n"
            "Daha alcak bir model kullanin, lift'i azaltin ya da katman sayisini dusurun.")

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

                # PERIMETRE ONCE, sonra INFILL (tek NN havuzunda KARISMAZ). Bos katman atlanir.
                p_seg = _segments_xy(slc)
                i_seg = _segments_xy(inf)
                if p_seg.shape[0] == 0 and i_seg.shape[0] == 0:
                    continue   # bos katman → hicbir kuyuda Z/baski yok
                z = (i + 1) * layer_height

                # Bu katmanda SIRAYLA her kuyu (layer-major).
                for wid, ox, oy in origins:
                    shift = np.array([ox, oy], dtype=np.float64)
                    well_groups = []
                    if p_seg.shape[0]:
                        well_groups.append(p_seg + shift)   # perimetre
                    if i_seg.shape[0]:
                        well_groups.append(i_seg + shift)   # sonra infill
                    buf = [f"; LAYER {i} WELL {wid} (z={z:.3f})"]
                    well_started = False   # bu kuyunun ilk segmenti henuz basilmadi
                    for grp in well_groups:   # [0]=perimetre, [1]=infill (varsa)
                        for entry, exit_, gap in _order_layer_segments(grp, cur_xy):
                            if not first_descent_done:
                                # TUM baskinin ilk segmenti: XY yaklasimini kalibrasyon
                                # yuksekliginde yap, SONRA baski Z'sine in.
                                buf.append(f"G0 X{entry[0]:.3f} Y{entry[1]:.3f} F{travel_feed:.0f}")
                                buf.append(f"G0 Z{z:.3f} F600")
                                first_descent_done = True
                            elif not well_started:
                                # Yeni kuyu/katman gecisi: ONCE guvenli Z'ye CIK (z+lift),
                                # SONRA XY travel, SONRA ayni katmanin baski Z'sine GERI IN.
                                # NOT: bu bir lift + descent'tir; "Z hep yukari" DEGIL —
                                # kuyu-ici gecis yazili dolgunun ustunden gecmek icin
                                # yukselir, sonra baski yuksekligine doner.
                                buf.append(f"G0 Z{z + lift:.3f} F600")
                                buf.append(f"G0 X{entry[0]:.3f} Y{entry[1]:.3f} F{travel_feed:.0f}")
                                buf.append(f"G0 Z{z:.3f} F600")
                            elif gap > _TRAVEL_THRESHOLD:
                                buf.append(f"G0 X{entry[0]:.3f} Y{entry[1]:.3f} F{travel_feed:.0f}")
                            elif gap > 1e-6:
                                # Sub-mm dikis: kafa segment ENTRY'sine ULASMALI → entry'ye
                                # EKSTRUZE et (E = dikis uzunlugu). Eskiden dogrudan exit'e
                                # cizilen G1 entry'yi atliyor + capraz geometri yapiyordu.
                                buf.append(f"G1 X{entry[0]:.3f} Y{entry[1]:.3f} "
                                           f"E{gap * flow_multiplier:.4f} F{print_speed:.0f}")
                                moves += 1
                                cur_xy = (float(entry[0]), float(entry[1]))
                            well_started = True
                            seg_len = math.hypot(exit_[0] - entry[0], exit_[1] - entry[1])
                            e_val = seg_len * flow_multiplier
                            buf.append(f"G1 X{exit_[0]:.3f} Y{exit_[1]:.3f} E{e_val:.4f} F{print_speed:.0f}")
                            moves += 1
                            cur_xy = (float(exit_[0]), float(exit_[1]))

                    f.write("\n".join(buf))
                    f.write("\n")
                    del well_groups
                del p_seg, i_seg

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


def generate_gcode_multi_head(
        slices: list, infills: list, save_path: str,
        head_origins: dict[int, list], head_print_speeds: dict[int, float],
        layer_height: float = 0.2, flow_multiplier: float = 0.05,
        x_max: float = _BED_X_MAX, y_max: float = _BED_Y_MAX,
        z_max: float = _BED_Z_MAX, inter_well_lift: float = 2.0,
        abort_check=None) -> int:
    """Export layer-major -> printhead-major -> assigned-well G-code.

    ``head_origins`` maps 1/2/3 to ``(well_id, x, y)`` tuples.  Nozzle
    diameter is deliberately absent: the established relative-E mathematics is
    unchanged.  Output is streamed to a temporary file and atomically replaced.
    """
    if not isinstance(head_origins, dict):
        raise ValueError("head_origins bir dict olmalidir.")
    invalid_heads = [
        head for head in head_origins
        if isinstance(head, bool) or head not in PRINTHEAD_TO_TOOL
    ]
    if invalid_heads:
        raise ValueError(
            "Gecersiz printhead ID: " + ", ".join(repr(head) for head in invalid_heads))

    normalized: dict[int, list[tuple[str, float, float]]] = {}
    for head in (1, 2, 3):
        origins = head_origins.get(head, []) if isinstance(head_origins, dict) else []
        if origins:
            normalized[head] = [
                (str(well), float(origin_x), float(origin_y))
                for well, origin_x, origin_y in origins
            ]
    if not normalized:
        raise ValueError("well_assignments bos: en az bir atanmis kuyu gerekli.")

    extents = _xy_extents(slices, infills)
    if extents is None:
        raise ValueError("Dilim verisi bos: hicbir katmanda cizgi yok - G-code uretilmedi.")
    sx_min, sx_max, sy_min, sy_max = extents
    for origins in normalized.values():
        for well_id, origin_x, origin_y in origins:
            fx_min, fx_max = sx_min + origin_x, sx_max + origin_x
            fy_min, fy_max = sy_min + origin_y, sy_max + origin_y
            if fx_min < 0.0 or fx_max > x_max or fy_min < 0.0 or fy_max > y_max:
                raise ValueError(
                    f"Kuyu '{well_id}' yatak limitlerinin disina tasiyor:\n"
                    f"X [{fx_min:.1f} .. {fx_max:.1f}] (izinli 0 .. {x_max:.0f}), "
                    f"Y [{fy_min:.1f} .. {fy_max:.1f}] (izinli 0 .. {y_max:.0f}).")

    lift = max(0.0, float(inter_well_lift))
    highest = _highest_nonempty_layer(slices, infills)
    highest_z = (highest + 1) * layer_height if highest >= 0 else 0.0
    if highest_z + lift > z_max:
        raise ValueError(
            f"Model + lift Z sinirini asiyor: {highest_z + lift:.2f} > {z_max:.1f} mm.")

    speeds = {
        head: min(max(1.0, float(head_print_speeds.get(head, 600.0))), _MAX_XY_FEED)
        for head in normalized
    }
    moves = 0
    current_xy = (0.0, 0.0)
    first_descent_done = False
    active_head = None
    tmp_path = save_path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            handle.write("PRINT_START\nG90\nG92 E0\nM83\n")
            for layer_index in range(len(slices)):
                if abort_check is not None and abort_check():
                    raise RuntimeError("G-code export aborted")
                perimeter = _segments_xy(slices[layer_index])
                infill = _segments_xy(
                    infills[layer_index] if infills and layer_index < len(infills) else None)
                if perimeter.shape[0] == 0 and infill.shape[0] == 0:
                    continue
                z = (layer_index + 1) * layer_height
                for head in (1, 2, 3):
                    origins = normalized.get(head, [])
                    if not origins:
                        continue
                    if active_head != head:
                        handle.write(f"{PRINTHEAD_TO_TOOL[head]}\n")
                        active_head = head
                    speed = speeds[head]
                    for well_id, origin_x, origin_y in origins:
                        shift = np.array([origin_x, origin_y], dtype=np.float64)
                        groups = []
                        if perimeter.shape[0]:
                            groups.append(perimeter + shift)
                        if infill.shape[0]:
                            groups.append(infill + shift)
                        buffer = [
                            f"; LAYER {layer_index} PH{head} WELL {well_id} (z={z:.3f})"]
                        well_started = False
                        for group in groups:
                            for entry, exit_, gap in _order_layer_segments(group, current_xy):
                                if not first_descent_done:
                                    buffer.append(
                                        f"G0 X{entry[0]:.3f} Y{entry[1]:.3f} F{_MAX_XY_FEED:.0f}")
                                    buffer.append(f"G0 Z{z:.3f} F600")
                                    first_descent_done = True
                                elif not well_started:
                                    buffer.append(f"G0 Z{z + lift:.3f} F600")
                                    buffer.append(
                                        f"G0 X{entry[0]:.3f} Y{entry[1]:.3f} F{_MAX_XY_FEED:.0f}")
                                    buffer.append(f"G0 Z{z:.3f} F600")
                                elif gap > _TRAVEL_THRESHOLD:
                                    buffer.append(
                                        f"G0 X{entry[0]:.3f} Y{entry[1]:.3f} F{_MAX_XY_FEED:.0f}")
                                elif gap > 1e-6:
                                    buffer.append(
                                        f"G1 X{entry[0]:.3f} Y{entry[1]:.3f} "
                                        f"E{gap * flow_multiplier:.4f} F{speed:.0f}")
                                    moves += 1
                                    current_xy = (float(entry[0]), float(entry[1]))
                                well_started = True
                                length = math.hypot(exit_[0] - entry[0], exit_[1] - entry[1])
                                buffer.append(
                                    f"G1 X{exit_[0]:.3f} Y{exit_[1]:.3f} "
                                    f"E{length * flow_multiplier:.4f} F{speed:.0f}")
                                moves += 1
                                current_xy = (float(exit_[0]), float(exit_[1]))
                        handle.write("\n".join(buffer) + "\n")
            handle.write("PRINT_END\n")
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    os.replace(tmp_path, save_path)
    return moves
