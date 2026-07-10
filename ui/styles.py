"""Shared Qt style-sheet constants.

Extracted verbatim from the original ``KlipperArayuzu`` class attributes so every
UI component pulls from a single source of truth:

    from ui import styles            # styles.BTN_STYLE
    from ui.styles import BTN_STYLE  # BTN_STYLE
"""
from __future__ import annotations

BTN_STYLE: str = """
    QPushButton {
        font-size: 16px; padding: 10px;
        background-color: #F0F2F5; color: #212121;
        border: 1px solid #CFD8DC; border-radius: 6px; min-width: 100px;
    }
    QPushButton:hover { background-color: #1976D2; color: #FFFFFF; border-color: #1565C0; }
    QPushButton:pressed { background-color: #1565C0; color: #FFFFFF; }
    QPushButton:disabled { background-color: #ECEFF1; color: #90A4AE; border-color: #ECEFF1; }
"""

BTN_STYLE_CHECKABLE: str = """
    QPushButton {
        font-size: 18px; padding: 12px 35px;
        background-color: #F0F2F5; color: #212121;
        border: 2px solid #CFD8DC; border-radius: 6px;
    }
    QPushButton:hover { background-color: #E3F2FD; border-color: #1976D2; color: #1976D2; }
    QPushButton:checked {
        background-color: #1976D2; color: #FFFFFF;
        border: 2px solid #1565C0; font-weight: bold;
    }
"""

PH_BTN_STYLE: str = """
    QPushButton {
        font-size: 16px; padding: 8px 20px;
        background-color: #F0F2F5; color: #212121;
        border-radius: 4px; border: 1px solid #CFD8DC;
    }
    QPushButton:hover { background-color: #E3F2FD; color: #1976D2; border-color: #1976D2; }
    QPushButton:checked {
        background-color: #1976D2; color: #FFFFFF;
        border: 2px solid #1565C0; font-weight: bold;
    }
"""

SPINBOX_STYLE: str = """
    QDoubleSpinBox, QSpinBox {
        font-size: 16px; padding: 5px;
        background-color: #FFFFFF; color: #212121;
        border: 1px solid #CFD8DC; border-radius: 4px;
    }
    QDoubleSpinBox:focus, QSpinBox:focus { border-color: #1976D2; }
    QDoubleSpinBox::up-button, QSpinBox::up-button { width: 20px; }
    QDoubleSpinBox::down-button, QSpinBox::down-button { width: 20px; }
"""

COMBOBOX_STYLE: str = """
    QComboBox {
        font-size: 16px; padding: 5px;
        background-color: #FFFFFF; color: #212121;
        border: 1px solid #CFD8DC; border-radius: 4px; min-width: 100px;
    }
    QComboBox::drop-down { border: none; width: 30px; }
    QComboBox QAbstractItemView {
        background-color: #FFFFFF; color: #212121;
        selection-background-color: #E3F2FD; selection-color: #1565C0;
    }
"""

LABEL_STYLE: str = "font-size: 16px; color: #212121; background: transparent; border: none;"

CARD_STYLE: str = """
    QFrame#KareMekan {
        background-color: #FFFFFF;
        border: 1px solid #CFD8DC;
        border-radius: 8px;
    }
"""

LIST_STYLE: str = """
    QListWidget {
        font-size: 16px; background-color: #FFFFFF; color: #212121;
        border: 1px solid #CFD8DC; border-radius: 4px;
    }
    QListWidget::item { padding: 10px; border-bottom: 1px solid #ECEFF1; }
    QListWidget::item:hover { background-color: #E3F2FD; }
    QListWidget::item:selected { background-color: #BBDEFB; color: #1565C0; font-weight: bold; }
"""

TEXTAREA_STYLE: str = """
    QTextEdit {
        font-size: 16px; background-color: #FFFFFF; color: #212121;
        border: 1px solid #CFD8DC; border-radius: 4px; padding: 15px;
    }
"""

# Diyalog pencereleri icin global QSS (dokunmatik-okunur). App-genelinde verilir.
# GUVENLIK: Tum seciciler QDialog / QMessageBox / QInputDialog (ve alt siniflari)
# ile baslar. Ana pencere KlipperArayuzu bir QWidget'tir (QDialog DEGIL), bu yuzden
# bu kurallar ana arayuzdeki hicbir buton/etiket ile ESLESMEZ -> tasarim korunur.
DIALOG_STYLE: str = """
    QMessageBox, QInputDialog {
        min-width: 380px;
        background-color: #F8F9FA;
    }
    QMessageBox QLabel, QInputDialog QLabel {
        font-size: 16px;
        color: #212121;
    }
    QInputDialog QLineEdit, QMessageBox QLineEdit {
        font-size: 16px;
        min-height: 30px;
        padding: 4px 8px;
        background-color: #FFFFFF; color: #212121;
        border: 1px solid #CFD8DC; border-radius: 4px;
    }
    QMessageBox QPushButton,
    QInputDialog QPushButton,
    QDialog QDialogButtonBox QPushButton {
        font-size: 14px;
        min-height: 35px;
        min-width: 90px;
        padding: 6px 18px;
        background-color: #F0F2F5; color: #212121;
        border: 1px solid #CFD8DC; border-radius: 6px;
    }
    QMessageBox QPushButton:hover,
    QInputDialog QPushButton:hover,
    QDialog QDialogButtonBox QPushButton:hover {
        background-color: #1976D2; color: #FFFFFF; border-color: #1565C0;
    }
    QMessageBox QPushButton:pressed,
    QInputDialog QPushButton:pressed,
    QDialog QDialogButtonBox QPushButton:pressed {
        background-color: #1565C0; color: #FFFFFF;
    }
"""
