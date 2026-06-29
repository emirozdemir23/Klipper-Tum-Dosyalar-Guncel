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
