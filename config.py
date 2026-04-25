"""Persistent user-editable settings for the MVP.

Loaded at startup from `config.json` next to main.py. Sane defaults if the
file is missing or has stale keys. Saved back when the Config window
or tray actions change a value.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def _default_actors() -> dict:
    return {"person": True, "cat": False}


@dataclass
class Config:
    # Idle seconds before the scene starts.
    idle_threshold_s: float = 5.0

    # If True, scenes can spawn on any monitor. If False, only on
    # primary_screen_index.
    multi_monitor: bool = True

    # Which screen to use when multi-monitor is disabled. 0 = first screen
    # reported by Qt (typically the primary). If out of range, falls back to 0.
    primary_screen_index: int = 0

    # Enable/disable each animated object independently.
    # Keys must match the actor names the runtime knows about.
    actors: dict = field(default_factory=_default_actors)

    # Display scale for the cat sprite. Native is 68x68 — scale 1.0 keeps it
    # 68px tall, 2.0 makes it 136px tall, etc. Tune to match the person's
    # apparent size on screen.
    cat_scale: float = 1.5

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
        kwargs = {k: v for k, v in raw.items() if k in known}
        # Merge actor flags with defaults so newly-added actors get a default.
        actors = dict(_default_actors())
        actors.update(kwargs.get("actors") or {})
        kwargs["actors"] = actors
        return cls(**kwargs)

    def save(self) -> None:
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(asdict(self), f, indent=2)
        except OSError as e:
            print(f"[config] failed to save: {e}", flush=True)

    def actor_enabled(self, name: str) -> bool:
        return bool(self.actors.get(name, False))
