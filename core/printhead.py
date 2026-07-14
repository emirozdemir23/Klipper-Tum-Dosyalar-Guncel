"""Canonical multi-printhead data model and validation helpers.

This module is intentionally Qt-free.  Protocol migration, UI collection,
freshness snapshots and G-code export all consume the same normalized model.
"""
from __future__ import annotations

import math
from typing import Mapping


PRINTHEAD_IDS = (1, 2, 3)
PRINTHEAD_TO_TOOL = {1: "T0", 2: "T1", 3: "T2"}
PRINTHEAD_TO_HEATER = {1: "peltier_1", 2: "peltier_2", 3: "peltier_3"}

NOZZLE_DIAMETER_MIN = 0.10
NOZZLE_DIAMETER_MAX = 0.90
NOZZLE_DIAMETER_DEFAULT = 0.40

PRINT_SPEED_MIN = 1.0
PRINT_SPEED_MAX = 30.0
PRINT_SPEED_DEFAULT = 10.0

PRINTHEAD_TEMPERATURE_MIN = 4.0
PRINTHEAD_TEMPERATURE_MAX = 45.0
PRINTHEAD_TEMPERATURE_DEFAULT = 27.0

WELL_IDS_BY_FORMAT = {
    6: ("A1", "A2", "A3", "B1", "B2", "B3"),
    12: (
        "A1", "A2", "A3", "A4",
        "B1", "B2", "B3", "B4",
        "C1", "C2", "C3", "C4",
    ),
}


def _normalize_number(value: object, default: float,
                      minimum: float, maximum: float) -> float:
    """Reject non-numbers/bools/non-finite values, otherwise clamp."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return float(default)
    number = float(value)
    if not math.isfinite(number):
        return float(default)
    return max(float(minimum), min(float(maximum), number))


def normalize_nozzle_diameter(value: object) -> float:
    return _normalize_number(
        value, NOZZLE_DIAMETER_DEFAULT,
        NOZZLE_DIAMETER_MIN, NOZZLE_DIAMETER_MAX,
    )


def normalize_print_speed(value: object) -> float:
    return _normalize_number(
        value, PRINT_SPEED_DEFAULT, PRINT_SPEED_MIN, PRINT_SPEED_MAX,
    )


def normalize_printhead_temperature(value: object) -> float:
    return _normalize_number(
        value, PRINTHEAD_TEMPERATURE_DEFAULT,
        PRINTHEAD_TEMPERATURE_MIN, PRINTHEAD_TEMPERATURE_MAX,
    )


def normalize_selected_printhead(value: object) -> int:
    """Return a valid printhead ID; bool is never accepted as integer one/zero."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 1
    return value if value in PRINTHEAD_IDS else 1


def default_printhead_profile() -> dict[str, float]:
    return {
        "nozzle_diameter_mm": NOZZLE_DIAMETER_DEFAULT,
        "print_speed_mm_s": PRINT_SPEED_DEFAULT,
        "temperature_c": PRINTHEAD_TEMPERATURE_DEFAULT,
    }


def default_printhead_profiles() -> dict[int, dict[str, float]]:
    return {head: default_printhead_profile() for head in PRINTHEAD_IDS}


def normalize_printhead_profile(value: object) -> dict[str, float]:
    raw = value if isinstance(value, Mapping) else {}
    return {
        "nozzle_diameter_mm": normalize_nozzle_diameter(
            raw.get("nozzle_diameter_mm")),
        "print_speed_mm_s": normalize_print_speed(raw.get("print_speed_mm_s")),
        "temperature_c": normalize_printhead_temperature(raw.get("temperature_c")),
    }


def normalize_printhead_profiles(value: object) -> dict[int, dict[str, float]]:
    """Normalize JSON-style (``ph1`` keys) or runtime-style (integer keys) profiles."""
    raw = value if isinstance(value, Mapping) else {}
    normalized: dict[int, dict[str, float]] = {}
    for head in PRINTHEAD_IDS:
        profile = raw.get(f"ph{head}", raw.get(head, {}))
        normalized[head] = normalize_printhead_profile(profile)
    return normalized


def profiles_to_json(value: object) -> dict[str, dict[str, float]]:
    profiles = normalize_printhead_profiles(value)
    return {f"ph{head}": dict(profiles[head]) for head in PRINTHEAD_IDS}


def normalize_well_format(value: object) -> int:
    if isinstance(value, bool):
        return 6
    return 12 if value == 12 else 6


def valid_well_ids(well_format: object) -> tuple[str, ...]:
    return WELL_IDS_BY_FORMAT[normalize_well_format(well_format)]


def normalize_well_assignments(value: object, well_format: object) -> dict[str, int]:
    """Keep only valid wells and exact integer printhead IDs."""
    if not isinstance(value, Mapping):
        return {}
    valid = set(valid_well_ids(well_format))
    normalized: dict[str, int] = {}
    for well_id, head in value.items():
        if not isinstance(well_id, str) or well_id not in valid:
            continue
        if isinstance(head, bool) or not isinstance(head, int) or head not in PRINTHEAD_IDS:
            continue
        normalized[well_id] = head
    return normalized


def used_printheads(platform_type: object, selected_printhead: object,
                    well_assignments: object) -> tuple[int, ...]:
    """Return sorted heads that participate in the current print plan."""
    if platform_type == 1 or platform_type == "well_plate":
        if not isinstance(well_assignments, Mapping):
            return ()
        heads = {
            head for head in well_assignments.values()
            if isinstance(head, int) and not isinstance(head, bool)
            and head in PRINTHEAD_IDS
        }
        return tuple(sorted(heads))
    return (normalize_selected_printhead(selected_printhead),)
