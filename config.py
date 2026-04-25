"""Persistent user-editable settings for the MVP.

Loaded at startup from `config.json` next to main.py. Sane defaults if the
file is missing or has stale keys. Saved back when the tray toggles a value.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


@dataclass
class Config:
    # Idle seconds before the scene starts.
    idle_threshold_s: float = 5.0

    # If True, scenes can spawn on any monitor. If False, only on the screen
    # whose Qt index matches `primary_screen_index`.
    multi_monitor: bool = True

    # Which screen to use when multi-monitor is disabled. 0 = first screen
    # reported by Qt (typically the primary). If out of range, falls back to 0.
    primary_screen_index: int = 0

    @classmethod
    def load(cls) -> "Config":
        if not os.path.exists(CONFIG_PATH):
            return cls()
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self) -> None:
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(asdict(self), f, indent=2)
        except OSError as e:
            print(f"[config] failed to save: {e}", flush=True)
