"""Preview tab: simple layer view (LIGHT theme).

Layout — a 3D viewport on the left and a single VERTICAL layer slider on the
right. Selecting a layer renders that layer fully, instantly. There is NO
intra-layer simulation (no horizontal slider, no play button, no timer).

    ┌────────────────────────────┬──┐
    │                            │▕▏│  ← layer_slider (VERTICAL): pick layer
    │        3D viewport         │▕▏│     (Layer 1 at the bottom)
    │     (layer_plotter_frame)  │▕▏│
    │                            │N │  ← layer_nav_label ("L / N")
    └────────────────────────────┴──┘
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QSlider, QPushButton,
)
from PyQt6.QtCore import Qt

# ── Light theme palette (matches the rest of the app) ──
_BG_VIEWPORT = "#F5F5F5"
_BAR_BG      = "#FFFFFF"
_ACCENT      = "#1C7ED6"
_ACCENT_DK   = "#1565C0"
_TEXT        = "#212121"


class PreviewTab(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._build(layout)

    def _build(self, layout: QVBoxLayout) -> None:
        # ── Row: 3D viewport (left, stretches) + vertical layer slider (right) ──
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        self.layer_plotter_frame = QFrame()
        self.layer_plotter_frame.setStyleSheet(
            f"QFrame {{ background:{_BG_VIEWPORT}; border:none; }}"
        )
        row.addWidget(self.layer_plotter_frame, stretch=1)

        # Right rail: "Katman" caption, vertical slider, "L / N" counter.
        rail = QWidget()
        rail.setFixedWidth(70)
        rail.setStyleSheet(f"QWidget {{ background:{_BAR_BG}; border-left:1px solid #E0E0E0; }}")
        rail_lay = QVBoxLayout(rail)
        rail_lay.setContentsMargins(6, 10, 6, 10)
        rail_lay.setSpacing(8)

        cap = QLabel("Katman")
        cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cap.setStyleSheet("QLabel { font-size:11px; color:#607D8B; background:transparent; }")
        rail_lay.addWidget(cap)

        self.layer_slider = QSlider(Qt.Orientation.Vertical)
        self.layer_slider.setMinimum(0)
        self.layer_slider.setMaximum(0)
        self.layer_slider.setValue(0)
        # NOTE: Qt's VERTICAL QSlider default is ALREADY min-at-bottom /
        # max-at-top (dragging UP increases the value). The previous explicit
        # setInvertedAppearance(True) was what flipped this slider upside down
        # (value grew top-to-bottom). Keep BOTH False: Layer 1 sits at the
        # BOTTOM and dragging/scrolling UP INCREASES the layer — like Cura.
        self.layer_slider.setInvertedAppearance(False)
        self.layer_slider.setInvertedControls(False)
        self.layer_slider.setStyleSheet(
            "QSlider::groove:vertical {"
            "  width:6px; background:#E0E0E0; border-radius:3px;"
            "}"
            f"QSlider::sub-page:vertical {{ background:{_ACCENT}; border-radius:3px; }}"
            "QSlider::handle:vertical {"
            "  height:18px; width:18px; margin:0 -7px;"
            f"  background:#FFFFFF; border-radius:9px; border:2px solid {_ACCENT}; }}"
            f"QSlider::handle:vertical:hover {{ border-color:{_ACCENT_DK}; }}"
        )
        rail_lay.addWidget(self.layer_slider, stretch=1, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.layer_nav_label = QLabel("— / —")
        self.layer_nav_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.layer_nav_label.setStyleSheet(
            f"QLabel {{ font-size:11px; font-weight:bold; color:{_TEXT}; background:transparent; }}"
        )
        rail_lay.addWidget(self.layer_nav_label)

        row.addWidget(rail)
        layout.addLayout(row, stretch=1)

        # ── Bottom action bar: Export G-Code (right-aligned) ────────────────
        bar = QHBoxLayout()
        bar.setContentsMargins(8, 6, 8, 6)
        bar.addStretch(1)
        self.export_gcode_btn = QPushButton("Export G-Code")
        self.export_gcode_btn.setStyleSheet(
            "QPushButton {"
            f"  background:{_ACCENT}; color:#FFFFFF; border:none; border-radius:5px;"
            "  padding:7px 18px; font-size:12px; font-weight:bold; }"
            f"QPushButton:hover {{ background:{_ACCENT_DK}; }}"
            "QPushButton:disabled { background:#B0BEC5; color:#ECEFF1; }"
        )
        bar.addWidget(self.export_gcode_btn)
        layout.addLayout(bar)
