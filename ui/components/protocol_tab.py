"""Protocol tab: saved-protocol list, Open/Edit/Delete buttons, detail pane (view only).

All list selection / open / edit / delete behavior is wired by the controller —
this class only builds and exposes the widgets.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QPushButton, QTextEdit,
)

from ui.styles import BTN_STYLE, LIST_STYLE, TEXTAREA_STYLE


class ProtocolTab(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self._build(layout)

    def _build(self, layout: QVBoxLayout) -> None:
        main = QHBoxLayout()
        main.setSpacing(20)

        self.protokol_listesi = QListWidget()
        self.protokol_listesi.setStyleSheet(LIST_STYLE)
        self.protokol_listesi.setFixedWidth(160)
        main.addWidget(self.protokol_listesi)

        btn_col = QVBoxLayout()
        btn_col.setSpacing(15)

        self.open_btn = QPushButton("Open")
        self.edit_btn = QPushButton("Edit")
        self.delete_btn = QPushButton("Delete")

        for btn in (self.open_btn, self.edit_btn):
            btn.setStyleSheet(BTN_STYLE)
            btn.setFixedWidth(100)
            btn_col.addWidget(btn)

        self.delete_btn.setStyleSheet(BTN_STYLE + """
            QPushButton { color: #B71C1C; }
            QPushButton:hover { background-color: #C62828; color: white; }
        """)
        self.delete_btn.setFixedWidth(100)
        btn_col.addWidget(self.delete_btn)
        btn_col.addStretch()
        main.addLayout(btn_col)

        self.protokol_detay_alani = QTextEdit()
        self.protokol_detay_alani.setReadOnly(True)
        self.protokol_detay_alani.setText("Select a protocol\nto view details here.")
        self.protokol_detay_alani.setStyleSheet(TEXTAREA_STYLE)
        main.addWidget(self.protokol_detay_alani)
        main.addSpacing(20)

        layout.addLayout(main)
