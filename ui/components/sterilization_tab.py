"""Sterilization tab: UV lamp + HEPA fan timer rows (view only).

Exposes the spinbox / start / stop / remaining-label widgets for each row so the
controller can drive the countdown timers. No timer logic lives here.
"""
from __future__ import annotations

from typing import Tuple

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSpinBox, QPushButton, QLabel,
)


class SterilizationTab(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self._build(layout)

    def _build(self, layout: QVBoxLayout) -> None:
        uv_title = QLabel("UV Sterilization")
        uv_title.setStyleSheet("font-size: 20px; font-weight: bold; color: #333333; margin-top: 10px;")
        layout.addWidget(uv_title)
        layout.addSpacing(20)
        self.uv_zaman_kutusu, self.uv_start_btn, self.uv_stop_btn, self.uv_kalan_sure_lbl = \
            self._create_timer_row(layout)

        hepa_title = QLabel("HEPA Fan")
        hepa_title.setStyleSheet("font-size: 20px; font-weight: bold; color: #333333; margin-top: 40px;")
        layout.addWidget(hepa_title)
        layout.addSpacing(20)
        self.hepa_zaman_kutusu, self.hepa_start_btn, self.hepa_stop_btn, self.hepa_kalan_sure_lbl = \
            self._create_timer_row(layout)
        layout.addStretch()

    def _create_timer_row(
        self, parent: QVBoxLayout
    ) -> Tuple[QSpinBox, QPushButton, QPushButton, QLabel]:
        container = QVBoxLayout()
        container.setSpacing(12)

        # Üst satır
        top_row = QHBoxLayout()
        top_row.setSpacing(15)

        lbl = QLabel("Timer")
        lbl.setStyleSheet("font-size: 18px; color: #333333;")
        top_row.addWidget(lbl)
        top_row.addSpacing(20)

        sb = QSpinBox()
        sb.setRange(1, 120)
        sb.setValue(10)
        sb.setSuffix(" min")
        sb.setStyleSheet("font-size:16px; padding:5px; background:white; color:black;")
        top_row.addWidget(sb)
        top_row.addSpacing(20)

        start = QPushButton("Start")
        start.setStyleSheet("font-size:18px; padding:10px 30px; background:#e0e0e0; color:black;")
        top_row.addWidget(start)
        top_row.addSpacing(15)

        stop = QPushButton("Stop")
        stop.setStyleSheet("font-size:18px; padding:10px 30px; background:#e0e0e0; color:black;")
        top_row.addWidget(stop)
        top_row.addStretch()
        container.addLayout(top_row)

        # Alt satır: Remaining Time (normal) + --:-- (bold mavi)
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(10)

        remaining_lbl = QLabel("Remaining Time:")
        remaining_lbl.setStyleSheet("font-size:18px; color:#333333;")
        bottom_row.addWidget(remaining_lbl)

        remaining = QLabel("--:--")
        remaining.setStyleSheet("font-size:18px; color:#333333; font-weight:bold;")
        bottom_row.addWidget(remaining)
        bottom_row.addStretch()
        container.addLayout(bottom_row)

        parent.addLayout(container)
        return sb, start, stop, remaining
