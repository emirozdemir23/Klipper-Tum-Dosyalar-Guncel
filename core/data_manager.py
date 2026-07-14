"""Qt-widget-free protocol persistence, normalization and legacy migration."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from PyQt6.QtCore import QStandardPaths

from core.printhead import (
    NOZZLE_DIAMETER_DEFAULT,
    NOZZLE_DIAMETER_MAX,
    NOZZLE_DIAMETER_MIN,
    PRINTHEAD_IDS,
    PRINT_SPEED_DEFAULT,
    PRINTHEAD_TEMPERATURE_DEFAULT,
    default_printhead_profile,
    normalize_nozzle_diameter,
    normalize_print_speed,
    normalize_printhead_profiles,
    normalize_printhead_temperature,
    normalize_selected_printhead,
    normalize_well_assignments,
    normalize_well_format,
    profiles_to_json,
    valid_well_ids,
)


class DataManager:
    def __init__(self) -> None:
        self.protocols: dict[str, dict] = {}
        self.protocols_dir: Path = self._resolve_protocols_dir()

    @staticmethod
    def _resolve_protocols_dir() -> Path:
        project_root = Path(__file__).resolve().parent.parent
        local_path = project_root / "data" / "protocols"
        fallback_path = Path(QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DocumentsLocation)) / "BioprinterProtocols"
        if local_path.exists() and any(local_path.glob("*.json")):
            return local_path
        fallback_path.mkdir(parents=True, exist_ok=True)
        return fallback_path

    @staticmethod
    def format_protocol_detail(name: str, d: dict, bp_text: str) -> str:
        assignments = d.get("well_assignments", {})
        wells_txt = ", ".join(
            f"{well}=PH{head}" for well, head in sorted(assignments.items())
        ) if assignments else "None"
        profiles = normalize_printhead_profiles(d.get("printheads"))
        profile_lines = "\n".join(
            f"PH{head}: nozzle {profiles[head]['nozzle_diameter_mm']:.2f} mm, "
            f"speed {profiles[head]['print_speed_mm_s']:.1f} mm/s, "
            f"temperature {profiles[head]['temperature_c']:.1f} °C"
            for head in PRINTHEAD_IDS
        )
        return (
            f"Selected Protocol: {name}\n\n"
            f"Build Platform - {bp_text}\n\n"
            f"Well Assignments - {wells_txt}\n\n"
            f"Selected Printhead - Printhead {d.get('selected_printhead', 1)}\n\n"
            f"{profile_lines}\n\n"
            f"Platform Temperature - {d.get('plat_temp', 0.0):.1f} °C\n\n"
            f"Model - {d.get('model_name', 'Not Selected')}\n\n"
            f"Layer Thickness - {d.get('layer', 0.0):.2f} mm\n\n"
            f"Grid Type - {d.get('grid', 'Linear')}\n\n"
            f"Grid Distance - {d.get('distance', 0.0):.2f} mm"
        )

    @staticmethod
    def built_platform_text(d: dict) -> str:
        bp_type = d.get("bp_type", 0)
        if bp_type == 0:
            return f"Petri Dish  |  Diameter = {d.get('bp_dia', '?')} mm"
        if bp_type == 1:
            return f"Well Plate  |  {d.get('bp_well_format', 6)}-well"
        if bp_type == 2:
            return f"Glass Slide  |  Size = {d.get('bp_size', '?')} mm"
        return "—"

    @staticmethod
    def sanitize_filename(name: str) -> str:
        safe = re.sub(r'[^\w\s-]', '', name).strip().replace(' ', '_')
        if not safe:
            safe = "protocol"
        unique_hash = hashlib.md5(name.encode('utf-8')).hexdigest()[:8]
        return f"{safe}_{unique_hash}.json"

    def save_to_disk(self, name: str, record: dict) -> None:
        """Write only the canonical JSON schema; legacy keys are never emitted."""
        path = self.protocols_dir / self.sanitize_filename(name)
        d = record.get("degerler", {})
        well_format = normalize_well_format(d.get("bp_well_format", 6))
        payload = {
            "protocol_name": name,
            "selected_printhead": normalize_selected_printhead(
                d.get("selected_printhead")),
            "printheads": profiles_to_json(d.get("printheads")),
            "well_assignments": normalize_well_assignments(
                d.get("well_assignments"), well_format),
            "layer_thickness_mm": d.get("layer", 0.0),
            "grid_type": "Linear",
            "grid_distance_mm": d.get("distance", 0.0),
            "platform_temperature_c": d.get("plat_temp", 0.0),
            "model_name": d.get("model_name", "Not Selected"),
            "stl_path": d.get("stl_path", ""),
            "built_platform": {
                "type_index": d.get("bp_type", 0),
                "petri_diameter_mm": d.get("bp_dia") if d.get("bp_type") == 0 else None,
                "well_format": well_format if d.get("bp_type") == 1 else None,
                "glass_size_mm": d.get("bp_size") if d.get("bp_type") == 2 else None,
            },
        }
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    def delete_from_disk(self, name: str) -> None:
        path = self.protocols_dir / self.sanitize_filename(name)
        if path.exists():
            path.unlink()

    def load_protocols(self) -> dict:
        """Load protocols and migrate legacy payloads in memory only."""
        self.protocols.clear()
        if not self.protocols_dir.exists():
            return self.protocols
        for json_file in sorted(self.protocols_dir.glob("*.json")):
            try:
                with open(json_file, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                name = payload.get("protocol_name") or json_file.stem
                values = self.normalize_protocol_payload(payload)
                detail = self.format_protocol_detail(
                    name, values, self.built_platform_text(values))
                self.protocols[name] = {"detay": detail, "degerler": values}
            except Exception as exc:
                print(f"[HATA] {json_file.name}: {exc}")
        return self.protocols

    @staticmethod
    def normalize_protocol_payload(payload: object) -> dict:
        """Convert canonical or legacy JSON to the canonical runtime structure.

        The legacy field names below are intentionally restricted to this migration
        boundary.  Reading a legacy file never rewrites it automatically.
        """
        p = payload if isinstance(payload, dict) else {}
        bp_raw = p.get("built_platform")
        bp = bp_raw if isinstance(bp_raw, dict) else {}

        bp_type = bp.get("type_index", p.get("bp_type", 0))
        if isinstance(bp_type, bool) or bp_type not in (0, 1, 2):
            bp_type = 0
        well_format = normalize_well_format(
            bp.get("well_format", p.get("bp_well_format", 6)))
        selected = normalize_selected_printhead(
            p.get("selected_printhead",
                  p.get("printhead_number", p.get("ph_id", 1))))

        if isinstance(p.get("printheads"), dict):
            profiles = normalize_printhead_profiles(p["printheads"])
        else:
            legacy = default_printhead_profile()
            legacy["nozzle_diameter_mm"] = normalize_nozzle_diameter(
                p.get("nozzle_diameter", NOZZLE_DIAMETER_DEFAULT))
            legacy["print_speed_mm_s"] = normalize_print_speed(
                p.get("print_speed_mm_s", p.get("speed", PRINT_SPEED_DEFAULT)))
            legacy["temperature_c"] = normalize_printhead_temperature(
                p.get("printhead_temperature_c",
                      p.get("ph_temp", PRINTHEAD_TEMPERATURE_DEFAULT)))
            profiles = {head: dict(legacy) for head in PRINTHEAD_IDS}

        if "well_assignments" in p:
            assignments = normalize_well_assignments(
                p.get("well_assignments"), well_format)
        else:
            # Legacy well selection fields are read only at this migration boundary.
            legacy_wells = bp.get("selected_wells")
            if legacy_wells is None:
                legacy_wells = p.get("bp_selected_wells", p.get("selected_wells", []))
            valid = set(valid_well_ids(well_format))
            assignments = {}
            if isinstance(legacy_wells, (list, tuple, set)):
                assignments = {
                    well: selected for well in legacy_wells
                    if isinstance(well, str) and well in valid
                }

        return {
            "selected_printhead": selected,
            "printheads": profiles,
            "well_assignments": assignments,
            "layer": p.get("layer_thickness_mm", p.get("layer", 0.2)),
            "grid": "Linear",
            "distance": p.get("grid_distance_mm", p.get("distance", 0.2)),
            "plat_temp": p.get("platform_temperature_c", p.get("plat_temp", -30.0)),
            "bp_type": bp_type,
            "bp_dia": (str(bp.get("petri_diameter_mm") or p.get("bp_dia") or "60")
                       if bp_type == 0 else "60"),
            "bp_well_format": well_format,
            "bp_size": (str(bp.get("glass_size_mm") or p.get("bp_size") or "20x60")
                        if bp_type == 2 else "20x60"),
            "model_name": p.get("model_name", "Not Selected"),
            "stl_path": p.get("stl_path", ""),
        }
