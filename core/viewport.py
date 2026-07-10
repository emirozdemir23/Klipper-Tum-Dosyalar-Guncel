"""PyVista bootstrap + shared 3-D render helpers.

Centralizes the single pyvista import and global-theme configuration, then
re-exports ``pv``, ``np`` and ``QtInteractor`` so no other module configures the
theme twice. Also hosts the static scene builders (platform grid, axis arrows)
shared by the Model and Preview viewports.

Contains NO Qt-widget or ``ui.*`` imports — depends only on pyvista / numpy.
"""
from __future__ import annotations

try:
    import pyvista as pv
    import numpy as np
    from pyvistaqt import QtInteractor

    # VTK Windows Code 2004 hatasını önlemek için global render ayarlarını sıfırla
    pv.global_theme.multi_samples = 0
    pv.global_theme.depth_peeling.enabled = False
    pv.global_theme.allow_empty_mesh = True
except ImportError:
    pv = None
    np = None
    QtInteractor = None
    print("Warning: pyvista or pyvistaqt not installed. STL viewing disabled.")


def build_platform_grid(plotter, plate_size: float, z_grid: float = 0.01,
                        name_prefix: str = '') -> None:
    """Major (50 mm) / minor (10 mm) grid çizer; her ikisi Z+z_grid'de."""
    spacing = 10
    n = int(plate_size / (2 * spacing))
    half = plate_size / 2
    minor_segs, major_segs = [], []
    for i in range(-n, n + 1):
        x = i * spacing
        target = major_segs if (x % 50 == 0) else minor_segs
        target.append(pv.Line((x, -half, z_grid), (x, half, z_grid)))
        target.append(pv.Line((-half, x, z_grid), (half, x, z_grid)))
    kw_m = dict(lighting=False, render=False) if name_prefix else dict(lighting=False)
    if minor_segs:
        kwargs = {**kw_m, **({"name": f"{name_prefix}grid_minor"} if name_prefix else {})}
        plotter.add_mesh(pv.merge(minor_segs), color='#E0E0E0', line_width=1.0, **kwargs)
    if major_segs:
        kwargs = {**kw_m, **({"name": f"{name_prefix}grid_major"} if name_prefix else {})}
        plotter.add_mesh(pv.merge(major_segs), color='#B8B8B8', line_width=1.8, **kwargs)


def build_axis_arrows(plotter, origin, length: float = 20,
                      name_prefix: str = '', render: bool = False) -> None:
    """Tabanın köşesine RGB eksen okları çizer."""
    axes = [([1, 0, 0], '#F44336'), ([0, 1, 0], '#4CAF50'), ([0, 0, 1], '#2196F3')]
    for direction, clr in axes:
        end = [origin[j] + direction[j] * length for j in range(3)]
        kw = {"render": render} if name_prefix else {}
        nm_l = f"{name_prefix}ax_{clr}" if name_prefix else None
        nm_c = f"{name_prefix}cone_{clr}" if name_prefix else None
        if nm_l:
            plotter.add_mesh(pv.Line(origin, end), color=clr, line_width=2,
                             name=nm_l, **kw)
            plotter.add_mesh(
                pv.Cone(center=end, direction=direction, height=3.5, radius=1.8),
                color=clr, name=nm_c, **kw)
        else:
            plotter.add_mesh(pv.Line(origin, end), color=clr, line_width=2)
            plotter.add_mesh(
                pv.Cone(center=end, direction=direction, height=3.5, radius=1.8),
                color=clr)


# ----------------------------------------------------------------------------
# Referans baski kabi (Petri / Well-plate / Glass) — wireframe cizimi
# ----------------------------------------------------------------------------
# Kap olculeri icin merkezi veri yapisi. UI'den gelmeyenler (yukseklik, well
# CAD olculeri) buradan okunur. TUM kaplar (petri/glass/well) yatagin (grid)
# tam ortasinda durur: geometrik merkez (0,0,0). Well plate'de footprint merkezi
# origin'e oturur; kuyucuklar bu merkeze gore SIMETRIK yerlesir.
CONTAINER_DEFAULTS = {
    "petri": {"height": 15.0},
    "glass": {"height": 2.0},
    "well": {
        "footprint": (127.50, 87.50),   # dis olcu (X, Y) mm — geometrik merkez (0,0)
        "height": 14.0,
        # rows/cols, well capi (mm), pitch (X,Y). Kuyucuklar footprint merkezine
        # gore SIMETRIK yerlesir (a1_inset YOK; CAD A1 kose-ofseti kullanilmiyor).
        6:  {"rows": 2, "cols": 3, "well_d": 35.00, "pitch": (39.0, 39.0)},
        12: {"rows": 3, "cols": 4, "well_d": 22.50, "pitch": (26.0, 26.0)},
    },
}


def well_centers(fmt: int) -> dict:
    """well_id -> (cx, cy) for a well-plate format, from CONTAINER_DEFAULTS.

    Pure data (NO plotter, NO actors): lets Preview + G-code export learn each
    well's bed-local center WITHOUT the Model tab drawing an interactive well
    container. Uses the exact same symmetric-layout math as
    ``build_container_reference`` so both agree on well positions.
    """
    cfg = CONTAINER_DEFAULTS["well"]
    wc = cfg.get(int(fmt))
    if wc is None:
        return {}
    px, py = wc["pitch"]
    rows, cols = wc["rows"], wc["cols"]
    row_labels = [chr(ord("A") + i) for i in range(rows)]
    out = {}
    for row in range(rows):
        cy = ((rows - 1) / 2.0 - row) * py       # A(+Y ust) -> son satir(-Y alt)
        for col in range(cols):
            cx = (col - (cols - 1) / 2.0) * px   # sutun 1(-X) -> N(+X)
            out[f"{row_labels[row]}{col + 1}"] = (cx, cy)
    return out


def _ring(cx, cy, z, r, n=72):
    """(cx,cy) merkezli, z yuksekliginde, r yaricapli kapali cember (cizgiler)."""
    t = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    pts = np.column_stack([cx + r * np.cos(t), cy + r * np.sin(t), np.full(n, z)])
    return pv.lines_from_points(pts, close=True)


def _cyl_cage(cx, cy, r, z0, z1, n=72, verticals=8):
    """Alt+ust cember + birkac dikey cizgiden silindir tel kafes."""
    parts = [_ring(cx, cy, z0, r, n), _ring(cx, cy, z1, r, n)]
    for k in range(verticals):
        a = 2.0 * np.pi * k / verticals
        x, y = cx + r * np.cos(a), cy + r * np.sin(a)
        parts.append(pv.Line((x, y, z0), (x, y, z1)))
    return pv.merge(parts)


def _box_edges(x0, x1, y0, y1, z0, z1):
    """Dikdortgen prizmanin 12 temiz kenari (wireframe)."""
    return pv.Box(bounds=(x0, x1, y0, y1, z0, z1)).extract_feature_edges()


def build_container_reference(plotter, spec, name="container_ref",
                              color="#F4511E", line_width=2.0,
                              hitbox_opacity=0.05, create_hitboxes=True):
    """Secilen referans kabini (spec) wireframe named-actor olarak cizer.

    Hizalama (HEPSI yatagin tam ortasinda):
      * petri / glass -> geometrik merkez (0,0,0), taban Z=0.
      * well          -> footprint geometrik merkezi (0,0,0); kuyucuklar bu
                         merkeze gore SIMETRIK yerlesir.
    spec ornekleri:
      {"kind":"petri","diameter":60.0,"height":15.0}
      {"kind":"glass","x":20.0,"y":60.0,"height":2.0}
      {"kind":"well","format":6}

    create_hitboxes:
      * True  (varsayilan) -> her kuyuya saydam dolu silindir "hitbox"
        (pickable=True, ad="well_hit_<id>") tiklama hedefi olarak eklenir.
      * False -> SALT-GORSEL: hitbox URETILMEZ, hicbir pickable aktor eklenmez.
        Model sekmesi bunu kullanir (click-to-add/remove KESINLIKLE olmasin).

    Donus: {"actor_names":[...], "wells":{well_id:{"center":(cx,cy),"wire":actor}}}.
    Well plate'de footprint + her kuyu AYRI wireframe aktor. Wireframe/footprint/
    diger her sey pickable=False; yalnizca (create_hitboxes ise) hitbox pickable.
    """
    empty = {"actor_names": [], "wells": {}}
    if pv is None or spec is None or plotter is None:
        return empty
    kind = spec.get("kind")
    actor_names = []
    wells = {}
    if kind == "petri":
        d = float(spec["diameter"])
        h = float(spec.get("height", CONTAINER_DEFAULTS["petri"]["height"]))
        if d <= 0:
            return empty
        plotter.add_mesh(_cyl_cage(0.0, 0.0, d / 2.0, 0.0, h), color=color,
                         line_width=line_width, name=name,
                         pickable=False, render=False)
        actor_names.append(name)
    elif kind == "glass":
        x = float(spec["x"]); y = float(spec["y"])
        h = float(spec.get("height", CONTAINER_DEFAULTS["glass"]["height"]))
        if x <= 0 or y <= 0:
            return empty
        plotter.add_mesh(_box_edges(-x / 2.0, x / 2.0, -y / 2.0, y / 2.0, 0.0, h),
                         color=color, line_width=line_width, name=name,
                         pickable=False, render=False)
        actor_names.append(name)
    elif kind == "well":
        cfg = CONTAINER_DEFAULTS["well"]
        fmt = int(spec.get("format", 6))
        wc = cfg.get(fmt)
        if wc is None:
            return empty
        fx, fy = cfg["footprint"]
        h = cfg["height"]
        # Footprint geometrik merkezi (0,0) — ayri, pickable DEGIL aktor.
        plotter.add_mesh(_box_edges(-fx / 2.0, fx / 2.0, -fy / 2.0, fy / 2.0, 0.0, h),
                         color=color, line_width=line_width,
                         name="container_footprint", pickable=False, render=False)
        actor_names.append("container_footprint")
        r = wc["well_d"] / 2.0
        px, py = wc["pitch"]
        rows, cols = wc["rows"], wc["cols"]
        row_labels = [chr(ord("A") + i) for i in range(rows)]
        # Her kuyu: AYRI wireframe (pickable=False). create_hitboxes ise ayrica
        # saydam dolu silindir hitbox (pickable=True) eklenir; degilse SALT-GORSEL.
        for row in range(rows):
            cy = ((rows - 1) / 2.0 - row) * py      # A(+Y ust) -> son satir(-Y alt)
            for col in range(cols):
                cx = (col - (cols - 1) / 2.0) * px  # sutun 1(-X) -> N(+X)
                wid = f"{row_labels[row]}{col + 1}"   # A1, A2, ... B1 ...
                wname = f"well_wire_{wid}"
                wire = plotter.add_mesh(_cyl_cage(cx, cy, r, 0.0, h), color=color,
                                        line_width=line_width, name=wname,
                                        pickable=False, render=False)
                actor_names.append(wname)
                well_info = {"center": (cx, cy), "wire": wire}
                if create_hitboxes:
                    hb = pv.Cylinder(center=(cx, cy, h / 2.0), direction=(0, 0, 1),
                                     radius=r * 0.92, height=h)
                    hb.field_data["well_idx"] = np.array([len(wells)], dtype=int)
                    hname = f"well_hit_{wid}"
                    plotter.add_mesh(hb, color=color, opacity=hitbox_opacity,
                                     name=hname, pickable=True, render=False)
                    actor_names.append(hname)
                    well_info["hitbox_name"] = hname
                wells[wid] = well_info
    else:
        return empty

    return {"actor_names": actor_names, "wells": wells}
