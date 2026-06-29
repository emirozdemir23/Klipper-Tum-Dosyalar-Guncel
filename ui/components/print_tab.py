"""Print tab: Print / Pause / Stop buttons + elapsed / remaining time labels (view only).

The print countdown timer and button enable/disable state are managed by the controller.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
)
from PyQt6.QtCore import Qt


class PrintTab(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self._build(layout)

    def _build(self, layout: QVBoxLayout) -> None:
        main = QVBoxLayout()
        main.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self.print_btn = QPushButton("Print")
        self.pause_btn = QPushButton("Pause")
        self.stop_btn = QPushButton("Stop")

        style = """
            QPushButton{font-size:20px;padding:15px 40px;background:#e0e0e0;color:black;border-radius:5px;}
            QPushButton:hover{background:#333333;color:white;}
        """

        for btn in (self.print_btn, self.pause_btn, self.stop_btn):
            btn.setStyleSheet(style)
            btn_row.addWidget(btn)
            if btn is not self.stop_btn:
                btn_row.addSpacing(30)

        btn_row.addStretch()
        main.addLayout(btn_row)
        main.addSpacing(60)

        time_row = QHBoxLayout()
        time_row.addStretch()
        for attr, title in (("elapsed_deger", "Total Elapsed Time"), ("remaining_deger", "Time Remaining")):
            block = QVBoxLayout()
            t = QLabel(title)
            t.setStyleSheet("font-size:18px;color:#555555;")
            t.setAlignment(Qt.AlignmentFlag.AlignCenter)

            v = QLabel("0:00 min")
            v.setStyleSheet("font-size:24px;color:#000000;font-weight:bold;")
            v.setAlignment(Qt.AlignmentFlag.AlignCenter)

            setattr(self, attr, v)
            block.addWidget(t)
            block.addSpacing(10)
            block.addWidget(v)
            time_row.addLayout(block)
            if title == "Total Elapsed Time":
                time_row.addSpacing(80)

        time_row.addStretch()
        main.addLayout(time_row)
        main.addStretch()
        layout.addLayout(main)
