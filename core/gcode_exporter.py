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

import numpy as np

# Gaps longer than this (mm) become a non-printing travel move; shorter ones are
# bridged by the next extrusion (continuous bioprinting tolerates sub-mm stitch).
_TRAVEL_THRESHOLD = 1.5


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


def generate_gcode(slices: list, infills: list, save_path: str,
                   layer_height: float = 0.2, flow_multiplier: float = 0.05,
                   active_tool: str = "T0",
                   origin_x: float = 115.0, origin_y: float = 60.0) -> int:
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
            (position_min: 0). Default = centre of a 230×120 bed.

    Returns:
        int — number of extrusion (G1) moves written.

    Streams to disk layer-by-layer; only one layer's segments are ever in RAM.
    """
    n_layers = len(slices)
    moves = 0
    has_infills = bool(infills)

    with open(save_path, 'w', encoding='utf-8') as f:
        # ── Header (relative extrusion is critical) ──
        f.write(f"PRINT_START\n{active_tool}\nG90\nG92 E0\nM83\n")

        cur_xy = (0.0, 0.0)
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
            buf = [f"; LAYER {i} (z={z:.3f})", f"G0 Z{z:.3f} F600"]

            for entry, exit_, gap in _order_layer_segments(segs, cur_xy):
                if gap > _TRAVEL_THRESHOLD:
                    buf.append(f"G0 X{entry[0]:.3f} Y{entry[1]:.3f} F3000")
                seg_len = math.hypot(exit_[0] - entry[0], exit_[1] - entry[1])
                e_val = seg_len * flow_multiplier
                buf.append(f"G1 X{exit_[0]:.3f} Y{exit_[1]:.3f} E{e_val:.4f} F600")
                moves += 1
                cur_xy = (float(exit_[0]), float(exit_[1]))

            f.write("\n".join(buf))
            f.write("\n")
            # Drop the layer's arrays before the next iteration (RPi4 RAM hygiene).
            del segs, parts, p_seg, i_seg

        # ── Footer ──
        f.write("PRINT_END\n")

    return moves
