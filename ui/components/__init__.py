"""Tab components — each a self-contained QWidget view (no cross-tab logic)."""
from __future__ import annotations

from .sterilization_tab import SterilizationTab
from .protocol_tab import ProtocolTab
from .platform_tab import PlatformTab
from .model_tab import ModelTab
from .settings_tab import SettingsTab
from .preview_tab import PreviewTab
from .print_tab import PrintTab

__all__ = [
    "SterilizationTab",
    "ProtocolTab",
    "PlatformTab",
    "ModelTab",
    "SettingsTab",
    "PreviewTab",
    "PrintTab",
]
