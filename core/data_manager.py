"""Protocol persistence and formatting (Qt-widget-free data layer).

``DataManager`` owns the on-disk protocol store (``data/protocols/*.json``) and
the in-memory ``protocols`` dict: ``{name: {"detay": str, "degerler": dict|None}}``.

All methods operate on plain dicts and **raise** on I/O error; the controller is
responsible for any ``QMessageBox`` feedback and ``QListWidget`` updates. Only
``QtCore.QStandardPaths`` is used (headless-safe path query — no widgets).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from PyQt6.QtCore import QStandardPaths


class DataManager:
    def __init__(self) -> None:
        # name -> {"detay": str, "degerler": dict|None}
        self.protocols: dict[str, dict] = {}
        self.protocols_dir: Path = self._resolve_protocols_dir()

    # ==========================================================
    # STORAGE LOCATION
    # ==========================================================
    @staticmethod
    def _resolve_protocols_dir() -> Path:
        """Local ``<root>/data/protocols`` if populated, else a Documents fallback.

        ``__file__`` is ``<root>/core/data_manager.py``; ``.parent.parent`` walks
        back up to the project root so the local path matches the original
        ``main.py``-relative location.
        """
        project_root = Path(__file__).resolve().parent.parent
        local_path = project_root / "data" / "protocols"
        fallback_path = Path(QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DocumentsLocation)) / "BioprinterProtocols"
        if local_path.exists() and any(local_path.glob("*.json")):
            return local_path
        fallback_path.mkdir(parents=True, exist_ok=True)
        return fallback_path

    # ==========================================================
    # FORMATTING (pure)
    # ==========================================================
    @staticmethod
    def format_protocol_detail(name: str, d: dict, bp_text: str) -> str:
        """Generate the human-readable detail text for a protocol snapshot."""
        wells = d.get('bp_selected_wells', [])
        wells_txt = ", ".join(wells) if wells else "None"
        return (
            f"Selected Protocol: {name}\n\n"
            f"Build Platform - {bp_text}\n\n"
            f"Selected Wells - {wells_txt}\n\n"
            f"Selected Printhead - Printhead {d.get('ph_id', 1)}\n\n"
            f"Printhead Temperature - {d.get('ph_temp', 0.0):.1f} °C\n\n"
            f"Platform Temperature - {d.get('plat_temp', 0.0):.1f} °C\n\n"
            f"Model - {d.get('model_name', 'Not Selected')}\n\n"
            f"Layer Thickness - {d.get('layer', 0.0):.2f} mm\n\n"
            f"Print Speed - {d.get('speed', 0.0):.1f} mm/s\n\n"
            f"Grid Type - {d.get('grid', 'Linear')}\n\n"
            f"Grid Distance - {d.get('distance', 0.0):.2f} mm"
        )

    @staticmethod
    def built_platform_text(d: dict) -> str:
        """Ham veri dict'inden Build Platform info metni üretir."""
        bp_type = d.get("bp_type", 0)
        if bp_type == 0:
            return f"Petri Dish  |  Diameter = {d.get('bp_dia', '?')} mm"
        elif bp_type == 1:
            return f"Well Plate  |  {d.get('bp_well_format', 6)}-well"
        elif bp_type == 2:
            return f"Glass Slide  |  Size = {d.get('bp_size', '?')} mm"
        return "—"

    # ==========================================================
    # FILENAME
    # ==========================================================
    @staticmethod
    def sanitize_filename(name: str) -> str:
        """Protokol adını güvenli ve benzersiz bir dosya adına çevirir."""
        safe = re.sub(r'[^\w\s-]', '', name).strip().replace(' ', '_')
        if not safe:
            safe = "protocol"

        # Aynı safe ismin çakışmasını önlemek için kısa hash ekle
        unique_hash = hashlib.md5(name.encode('utf-8')).hexdigest()[:8]
        return f"{safe}_{unique_hash}.json"

    # ==========================================================
    # DISK I/O
    # ==========================================================
    def save_to_disk(self, name: str, record: dict) -> None:
        """Protokolü data/protocols/ altına sadece ham verilerle JSON olarak yazar.

        Raises ``OSError`` on write failure; the caller decides how to surface it.
        """
        path = self.protocols_dir / self.sanitize_filename(name)

        d = record.get("degerler", {})
        payload = {
            "protocol_name": name,
            "printhead_number": d.get("ph_id", 1),
            "layer_thickness_mm": d.get("layer", 0.0),
            "print_speed_mm_s": d.get("speed", 0.0),
            "grid_type": "Linear",   # desteklenen tek deger; her zaman Linear yaz
            "grid_distance_mm": d.get("distance", 0.0),
            "printhead_temperature_c": d.get("ph_temp", 0.0),
            "platform_temperature_c": d.get("plat_temp", 0.0),
            "model_name": d.get("model_name", "Not Selected"),
            "stl_path": d.get("stl_path", ""),
            "built_platform": {
                "type_index": d.get("bp_type", 0),
                "petri_diameter_mm": d.get("bp_dia") if d.get("bp_type") == 0 else None,
                "well_format": d.get("bp_well_format") if d.get("bp_type") == 1 else None,
                "glass_size_mm": d.get("bp_size") if d.get("bp_type") == 2 else None,
                "selected_wells": d.get("bp_selected_wells", []) if d.get("bp_type") == 1 else [],
            }
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def delete_from_disk(self, name: str) -> None:
        """Diskteki JSON dosyasını siler."""
        path = self.protocols_dir / self.sanitize_filename(name)
        if path.exists():
            path.unlink()

    def load_protocols(self) -> dict:
        """data/protocols/ klasöründeki JSON'ları okuyup ``self.protocols``'a yükler.

        Data-only: returns the populated dict. UI population (list widget,
        selection, detail text) is the controller's responsibility.
        """
        self.protocols.clear()

        if not self.protocols_dir.exists():
            return self.protocols

        dosyalar = list(self.protocols_dir.glob("*.json"))

        for json_file in sorted(dosyalar):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    p = json.load(f)

                name = p.get("protocol_name") or json_file.stem

                bp = p.get("built_platform", {})
                bp_type = bp.get("type_index", 0)

                degerler = {
                    "ph_id": p.get("printhead_number", 1),
                    "layer": p.get("layer_thickness_mm", 0.2),
                    "speed": p.get("print_speed_mm_s", 10.0),
                    "grid": "Linear",   # eski JSON degeri ne olursa olsun Linear'a normalize et
                    "distance": p.get("grid_distance_mm", 0.2),
                    "ph_temp": p.get("printhead_temperature_c", 27.0),
                    "plat_temp": p.get("platform_temperature_c", -30.0),
                    "bp_type": bp_type,
                    "bp_dia": str(bp.get("petri_diameter_mm") or "60") if bp_type == 0 else "60",
                    "bp_well_format": (bp.get("well_format") or 6) if bp_type == 1 else 6,
                    "bp_size": str(bp.get("glass_size_mm") or "20x60") if bp_type == 2 else "20x60",
                    "bp_selected_wells": bp.get("selected_wells", []) if bp_type == 1 else [],
                    "model_name": p.get("model_name", "Not Selected"),
                    "stl_path": p.get("stl_path", ""),
                }

                bp_text = self.built_platform_text(degerler)
                detail = self.format_protocol_detail(name, degerler, bp_text)

                self.protocols[name] = {
                    "detay": detail,
                    "degerler": degerler,
                }

            except Exception as e:
                print(f"[HATA] {json_file.name}: {e}")

        return self.protocols
