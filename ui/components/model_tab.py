"""Model tab: STL open button + empty 3-D frame (view only).

The PyVista plotter is created and driven by the controller inside ``uc_boyutlu_alan``;
this tab only exposes the button and the host frame.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFrame,
)

from ui.styles import BTN_STYLE


class ModelTab(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self._build(layout)

    def _build(self, layout: QVBoxLayout) -> None:
        main = QHBoxLayout()
        sol = QVBoxLayout()

        title = QLabel("3D Model")
        title.setStyleSheet("font-size:20px; color:#333333; font-weight:bold;")
        sol.addWidget(title)
        sol.addSpacing(10)

        self.open_stl_btn = QPushButton("Open")
        self.open_stl_btn.setStyleSheet(BTN_STYLE)
        sol.addWidget(self.open_stl_btn)
        sol.addStretch()

        main.addLayout(sol, 1)

        self.uc_boyutlu_alan = QFrame()
        self.uc_boyutlu_alan.setStyleSheet(
             "QFrame{background:#f5f5f5;border:2px solid #cccccc;border-radius:8px;}"
        )

        # Placeholder kaldırıldı - boş frame pyvista dolduracak
        uc = QVBoxLayout()
        uc.setContentsMargins(0, 0, 0, 0)
        self.uc_boyutlu_alan.setLayout(uc)

        main.addWidget(self.uc_boyutlu_alan, 4)
        layout.addLayout(main)
