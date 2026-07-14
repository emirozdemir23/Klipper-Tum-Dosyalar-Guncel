"""Build-platform view and canonical per-well printhead assignment editor."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.printhead import (
    PRINTHEAD_IDS,
    normalize_well_assignments,
    normalize_well_format,
    valid_well_ids,
)
from ui.styles import PH_BTN_STYLE


WELL_PALETTE = {
    0: ("#FFFFFF", "#1F5F99"),
    1: ("#D9EEFF", "#173A5E"),
    2: ("#78BDF2", "#173A5E"),
    3: ("#1F78D1", "#FFFFFF"),
}


class PlatformTab(QWidget):
    platform_changed = pyqtSignal(dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # This dictionary is the sole well-selection source at runtime.  The main
        # window exposes the exact same object as ``self.well_assignments``.
        self.well_assignments: dict[str, int] = {}
        self.well_buttons: dict[str, QPushButton] = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(5, 4, 5, 4)
        root.setSpacing(5)
        self._build(root)
        self._connect_internal()
        self._update_well_grid()
        self._on_platform_type_clicked(0, emit_signal=False)

    def _build(self, root: QVBoxLayout) -> None:
        title = QLabel("Built Platform")
        title.setStyleSheet("font-size:20px; font-weight:bold; color:#333333;")
        title.setFixedHeight(25)
        root.addWidget(title)

        type_row = QHBoxLayout()
        type_row.setSpacing(6)
        self.btn_petri = QPushButton("Petri Dish")
        self.btn_well = QPushButton("Well Plate")
        self.btn_glass = QPushButton("Glass Slide")
        self.bp_buton_grubu = QButtonGroup(self)
        self.bp_buton_grubu.setExclusive(True)
        for index, button in enumerate((self.btn_petri, self.btn_well, self.btn_glass)):
            button.setCheckable(True)
            button.setFixedHeight(34)
            button.setStyleSheet(PH_BTN_STYLE)
            self.bp_buton_grubu.addButton(button, index)
            type_row.addWidget(button)
        self.btn_petri.setChecked(True)
        root.addLayout(type_row)

        self.bp_stacked = QStackedWidget()
        self.bp_stacked.setMinimumHeight(285)
        self.bp_stacked.addWidget(self._build_petri_page())
        self.bp_stacked.addWidget(self._build_well_page())
        self.bp_stacked.addWidget(self._build_glass_page())
        root.addWidget(self.bp_stacked, 1)

        self.confirm_platform_btn = QPushButton("Apply Continue")
        self.confirm_platform_btn.setFixedHeight(36)
        self.confirm_platform_btn.setStyleSheet("""
            QPushButton { font-size:15px; font-weight:bold; color:white;
                background:#1976D2; border:none; border-radius:5px; }
            QPushButton:hover { background:#1565C0; }
        """)
        root.addWidget(self.confirm_platform_btn)

        # The single summary belongs to the page root so it is geometrically
        # below Apply Continue, at the very bottom of the Built Platform page.
        self.assignment_summary_label = QLabel()
        self.assignment_summary_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.assignment_summary_label.setFixedHeight(25)
        self.assignment_summary_label.setStyleSheet(
            "font-size:13px; font-weight:bold; color:#173A5E; background:#EAF3FA; "
            "border:1px solid #B8D4E8; border-radius:4px; padding:1px;")
        self.assignment_summary_label.setVisible(False)
        root.addWidget(self.assignment_summary_label)

    def _build_petri_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.addStretch()
        shape = QFrame()
        shape.setFixedSize(180, 180)
        shape.setStyleSheet(
            "QFrame { background:#FFFFFF; border:3px solid #1976D2; border-radius:90px; }")
        shape_row = QHBoxLayout()
        shape_row.addStretch(); shape_row.addWidget(shape); shape_row.addStretch()
        layout.addLayout(shape_row)
        input_row = QHBoxLayout()
        input_row.addStretch()
        input_row.addWidget(QLabel("Diameter (mm)"))
        self.in_dia = QLineEdit("60")
        self.in_dia.setFixedSize(90, 28)
        self.in_dia.setAlignment(Qt.AlignmentFlag.AlignCenter)
        input_row.addWidget(self.in_dia)
        input_row.addStretch()
        layout.addLayout(input_row)
        layout.addStretch()
        return page

    def _build_glass_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.addStretch()
        shape = QFrame()
        shape.setFixedSize(250, 120)
        shape.setStyleSheet(
            "QFrame { background:#FFFFFF; border:3px solid #1976D2; border-radius:7px; }")
        shape_row = QHBoxLayout()
        shape_row.addStretch(); shape_row.addWidget(shape); shape_row.addStretch()
        layout.addLayout(shape_row)
        input_row = QHBoxLayout()
        input_row.addStretch()
        input_row.addWidget(QLabel("Size (mm)"))
        self.in_size_x = QLineEdit("20")
        self.in_size_y = QLineEdit("60")
        for field in (self.in_size_x, self.in_size_y):
            field.setFixedSize(70, 28)
            field.setAlignment(Qt.AlignmentFlag.AlignCenter)
        input_row.addWidget(self.in_size_x)
        input_row.addWidget(QLabel("×"))
        input_row.addWidget(self.in_size_y)
        input_row.addStretch()
        layout.addLayout(input_row)
        layout.addStretch()
        return page

    def _build_well_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(5, 3, 5, 3)
        layout.setSpacing(3)

        assignment_row = QHBoxLayout()
        assignment_row.addStretch()
        assignment_row.addWidget(QLabel("Assign:"))
        self.assignment_head_group = QButtonGroup(self)
        self.assignment_head_group.setExclusive(True)
        self.assignment_head_buttons: dict[int, QPushButton] = {}
        for head in PRINTHEAD_IDS:
            button = QPushButton(f"PH{head}")
            button.setCheckable(True)
            button.setFixedSize(62, 29)
            button.setStyleSheet("""
                QPushButton { font-size:13px; font-weight:bold; background:#EAF3FA;
                    border:1px solid #8EB6D5; border-radius:4px; }
                QPushButton:checked { background:#1976D2; color:white; }
            """)
            self.assignment_head_group.addButton(button, head)
            self.assignment_head_buttons[head] = button
            setattr(self, f"assign_ph{head}_btn", button)
            assignment_row.addWidget(button)
        self.assignment_head_buttons[1].setChecked(True)
        assignment_row.addStretch()
        layout.addLayout(assignment_row)

        format_row = QHBoxLayout()
        format_row.addStretch()
        format_row.addWidget(QLabel("Well format:"))
        self.btn_6 = QPushButton("6-well")
        self.btn_12 = QPushButton("12-well")
        self.well_grup = QButtonGroup(self)
        self.well_grup.setExclusive(True)
        for fmt, button in ((6, self.btn_6), (12, self.btn_12)):
            button.setCheckable(True)
            button.setFixedSize(78, 28)
            self.well_grup.addButton(button, fmt)
            format_row.addWidget(button)
        self.btn_6.setChecked(True)
        format_row.addStretch()
        layout.addLayout(format_row)

        self.well_grid_frame = QFrame()
        self.well_grid_frame.setStyleSheet(
            "QFrame { background:#F7FBFE; border:1px solid #B8D4E8; border-radius:7px; }")
        self.well_grid_layout = QGridLayout(self.well_grid_frame)
        self.well_grid_layout.setContentsMargins(8, 5, 8, 5)
        self.well_grid_layout.setHorizontalSpacing(7)
        self.well_grid_layout.setVerticalSpacing(5)
        grid_row = QHBoxLayout()
        grid_row.addStretch(); grid_row.addWidget(self.well_grid_frame); grid_row.addStretch()
        layout.addLayout(grid_row, 1)

        return page

    def _connect_internal(self) -> None:
        self.bp_buton_grubu.idClicked.connect(self._on_platform_type_clicked)
        self.well_grup.idClicked.connect(self._on_well_format_clicked)
        self.in_dia.textChanged.connect(lambda _text: self._emit_config())
        self.in_size_x.textChanged.connect(lambda _text: self._emit_config())
        self.in_size_y.textChanged.connect(lambda _text: self._emit_config())

    def current_well_format(self) -> int:
        return 12 if self.btn_12.isChecked() else 6

    def _clear_grid(self) -> None:
        while self.well_grid_layout.count():
            item = self.well_grid_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.well_buttons.clear()

    def _update_well_grid(self) -> None:
        self._clear_grid()
        well_format = self.current_well_format()
        valid = set(valid_well_ids(well_format))
        for well in tuple(self.well_assignments):
            if well not in valid:
                del self.well_assignments[well]

        rows = ("A", "B", "C") if well_format == 12 else ("A", "B")
        columns = range(1, 5) if well_format == 12 else range(1, 4)
        for column_index, column in enumerate(columns, 1):
            label = QLabel(str(column))
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet("font-size:14px; font-weight:bold; border:none;")
            self.well_grid_layout.addWidget(label, 0, column_index)
        for row_index, row_name in enumerate(rows, 1):
            label = QLabel(row_name)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet("font-size:14px; font-weight:bold; border:none;")
            self.well_grid_layout.addWidget(label, row_index, 0)
            for column_index, column in enumerate(columns, 1):
                well_id = f"{row_name}{column}"
                button = QPushButton("")
                button.setObjectName(f"well_{well_id}")
                button.setFixedSize(43, 43)
                button.clicked.connect(
                    lambda _checked=False, wid=well_id: self._on_well_clicked(wid))
                self.well_buttons[well_id] = button
                self.well_grid_layout.addWidget(button, row_index, column_index)
        self._refresh_assignment_view()

    def _on_well_clicked(self, well_id: str) -> None:
        active_head = self.assignment_head_group.checkedId()
        if active_head not in PRINTHEAD_IDS:
            active_head = 1
        if self.well_assignments.get(well_id) == active_head:
            del self.well_assignments[well_id]
        else:
            self.well_assignments[well_id] = active_head
        self._refresh_assignment_view()
        self._emit_config()

    def _refresh_assignment_view(self) -> None:
        for well_id, button in self.well_buttons.items():
            head = self.well_assignments.get(well_id, 0)
            background, foreground = WELL_PALETTE[head]
            button.setText(f"PH{head}" if head else "")
            button.setStyleSheet(
                "QPushButton {"
                f"background:{background}; color:{foreground}; "
                "border:2px solid #1976D2; border-radius:21px; "
                "font-size:12px; font-weight:bold; padding:0px;"
                "} QPushButton:pressed { border:3px solid #0D47A1; }"
            )
        counts = {head: 0 for head in PRINTHEAD_IDS}
        for head in self.well_assignments.values():
            if head in counts:
                counts[head] += 1
        empty = len(valid_well_ids(self.current_well_format())) - sum(counts.values())
        self.assignment_summary_label.setText(
            f"PH1: {counts[1]} | PH2: {counts[2]} | PH3: {counts[3]} | Empty: {empty}")

    def _on_well_format_clicked(self, well_format: int) -> None:
        normalized = normalize_well_format(well_format)
        if normalized == 12:
            self.btn_12.setChecked(True)
        else:
            self.btn_6.setChecked(True)
        self._update_well_grid()
        self._emit_config()

    def _on_platform_type_clicked(self, kind_id: int,
                                  emit_signal: bool = True) -> None:
        if kind_id not in (0, 1, 2):
            kind_id = 0
        self.bp_stacked.setCurrentIndex(kind_id)
        self.assignment_summary_label.setVisible(kind_id == 1)
        if emit_signal:
            self._emit_config()

    def get_platform_config(self) -> dict:
        kind_id = self.bp_buton_grubu.checkedId()
        if kind_id == 1:
            return {
                "type": "well_plate",
                "well_format": self.current_well_format(),
                "well_assignments": dict(self.well_assignments),
            }
        if kind_id == 2:
            return {
                "type": "glass",
                "size_x": self.in_size_x.text(),
                "size_y": self.in_size_y.text(),
                "well_assignments": {},
            }
        return {
            "type": "petri",
            "diameter": self.in_dia.text(),
            "well_assignments": {},
        }

    def _emit_config(self) -> None:
        self.platform_changed.emit(self.get_platform_config())

    def set_well_assignments(self, assignments: object,
                             emit_signal: bool = True) -> None:
        normalized = normalize_well_assignments(
            assignments, self.current_well_format())
        self.well_assignments.clear()
        self.well_assignments.update(normalized)
        self._refresh_assignment_view()
        if emit_signal:
            self._emit_config()

    def load_platform(self, bp_type: object, well_format: object,
                      diameter: object, glass_size: object,
                      assignments: object) -> None:
        """Protocol load helper; all controls are changed without emitting signals."""
        kind_id = bp_type if isinstance(bp_type, int) and not isinstance(bp_type, bool) else 0
        if kind_id not in (0, 1, 2):
            kind_id = 0
        controls = [self.bp_buton_grubu, self.well_grup, self.in_dia,
                    self.in_size_x, self.in_size_y]
        previous = [(control, control.blockSignals(True)) for control in controls]
        try:
            button = self.bp_buton_grubu.button(kind_id)
            if button:
                button.setChecked(True)
            normalized_format = normalize_well_format(well_format)
            (self.btn_12 if normalized_format == 12 else self.btn_6).setChecked(True)
            self.in_dia.setText(str(diameter or "60"))
            parts = str(glass_size or "20x60").split("x", 1)
            self.in_size_x.setText(parts[0] if parts else "20")
            self.in_size_y.setText(parts[1] if len(parts) > 1 else "60")
            self.bp_stacked.setCurrentIndex(kind_id)
            self.assignment_summary_label.setVisible(kind_id == 1)
            self._update_well_grid()
            self.set_well_assignments(assignments, emit_signal=False)
        finally:
            for control, was_blocked in reversed(previous):
                control.blockSignals(was_blocked)
