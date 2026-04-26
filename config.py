"""Persistent user-editable settings, grouped by domain.

Loaded at startup from `config.json` next to main.py. Saved back from the
Config window (and any code path that mutates a value).

Schema is nested: `Config.behaviour`, `Config.monitors`, `Config.person`,
`Config.cat`. Old flat-key files (everything at the top level, plus
`actors: {person: ..., cat: ...}`) are migrated transparently on load.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from typing import Any

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


# ---------------------------------------------------------------------------
# Sub-configs — one per domain.
# ---------------------------------------------------------------------------

@dataclass
class BehaviourCfg:
    idle_threshold_s: float = 5.0
    debug_always_on: bool = False     # bypass idle gate for both scenes
    debug_paused: bool = False        # freeze tick (for frame-step debug)
    theme: str = "light"              # "light" | "dark"  — UI palette
    sounds: bool = True               # global UI-sound switch
    gpu_render: bool = True           # request OpenGL backend at startup


@dataclass
class MonitorsCfg:
    multi_monitor: bool = True
    primary_screen_index: int = 0     # used when multi_monitor is False


@dataclass
class PersonCfg:
    enabled: bool = True


@dataclass
class CatCfg:
    enabled: bool = False
    name: str = "tabby"            # shown in tray tooltip + speech bubble
    skin: str = "tabby (orange)"   # one of skins.SKINS names
    count: int = 1                 # how many independent cats to spawn (1..4)
    scale: float = 1.5
    y_offset_px: int = 0
    # Day/night behaviour: when True, between `night_start_hour` and
    # `night_end_hour` (system-clock local time) the cat lies/sits more
    # often and walks slower — looks like it's getting drowsy.
    night_mode: bool = True
    night_start_hour: int = 22     # inclusive
    night_end_hour: int = 7        # inclusive (wraps around midnight)

    # Animation pace: ticks per animation frame. Smaller = faster animation
    # AND faster movement (because per-frame deltas are spread across fewer
    # ticks). Use the multipliers below to decouple movement from pace.
    walk_frame_hold: int = 6
    run_frame_hold: int = 3

    # Uniform multiplier on the per-frame deltas. Lets you keep the gait
    # *shape* (relative ratios between frames) but scale total stride.
    walk_stride_multiplier: float = 1.0
    run_stride_multiplier: float = 1.0

    # Per-frame body displacement (screen px) for one frame transition.
    # 8 entries each for walk (slow-run) and run (running-8-frames).
    # The actual delta the runtime applies = deltas[i] * stride_multiplier.
    walk_frame_deltas: list[float] = field(
        default_factory=lambda: [3.0, 3.0, 3.0, 3.0, 3.0, 3.0, 3.0, 3.0]
    )
    run_frame_deltas: list[float] = field(
        default_factory=lambda: [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0]
    )


# ---------------------------------------------------------------------------
# Top-level Config.
# ---------------------------------------------------------------------------

@dataclass
class Config:
    behaviour: BehaviourCfg = field(default_factory=BehaviourCfg)
    monitors: MonitorsCfg = field(default_factory=MonitorsCfg)
    person: PersonCfg = field(default_factory=PersonCfg)
    cat: CatCfg = field(default_factory=CatCfg)

    # ---- API used elsewhere -------------------------------------------------

    def actor_enabled(self, name: str) -> bool:
        actor = getattr(self, name, None)
        return bool(getattr(actor, "enabled", False))

    # ---- IO ---------------------------------------------------------------

    @classmethod
    def load(cls) -> "Config":
        if not os.path.exists(CONFIG_PATH):
            return cls()
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError):
            return cls()
        return _from_raw(raw)

    def save(self) -> None:
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(asdict(self), f, indent=2)
        except OSError as e:
            print(f"[config] failed to save: {e}", flush=True)


# ---------------------------------------------------------------------------
# Loader / migrator.
# ---------------------------------------------------------------------------

# Map old flat keys → (sub_config_attr, field_name) when migrating legacy files.
_FLAT_TO_NESTED: dict[str, tuple[str, str]] = {
    "idle_threshold_s":        ("behaviour", "idle_threshold_s"),
    "debug_always_on":         ("behaviour", "debug_always_on"),
    "debug_paused":            ("behaviour", "debug_paused"),
    "multi_monitor":           ("monitors", "multi_monitor"),
    "primary_screen_index":    ("monitors", "primary_screen_index"),
    "cat_scale":               ("cat", "scale"),
    "cat_y_offset_px":         ("cat", "y_offset_px"),
    # cat_walk_speed_px / cat_run_speed_px are deprecated (replaced by
    # per-frame deltas); ignore on load.
}


def _coerce_dataclass(target_cls, value: Any):
    """Build an instance of `target_cls` from a dict, ignoring unknown keys
    and using defaults for missing fields."""
    if not is_dataclass(target_cls) or not isinstance(value, dict):
        return target_cls()
    known = {f.name: f for f in fields(target_cls)}
    kwargs = {}
    for k, v in value.items():
        if k not in known:
            continue
        # Recurse if the field is itself a dataclass.
        ftype = known[k].type
        if isinstance(ftype, type) and is_dataclass(ftype):
            kwargs[k] = _coerce_dataclass(ftype, v)
        else:
            kwargs[k] = v
    return target_cls(**kwargs)


def _from_raw(raw: dict) -> Config:
    """Build a Config from any of: nested schema, old flat schema, or a mix."""
    cfg = Config()

    # 1) Nested schema: keys are sub-config names ("behaviour", etc.).
    for sub_name in ("behaviour", "monitors", "person", "cat"):
        if sub_name in raw and isinstance(raw[sub_name], dict):
            sub_cls = type(getattr(cfg, sub_name))
            setattr(cfg, sub_name, _coerce_dataclass(sub_cls, raw[sub_name]))

    # 2) Legacy flat keys at the top level.
    for flat_key, (sub_attr, field_name) in _FLAT_TO_NESTED.items():
        if flat_key in raw:
            sub = getattr(cfg, sub_attr)
            if hasattr(sub, field_name):
                setattr(sub, field_name, raw[flat_key])

    # 3) Legacy `actors: {person: bool, cat: bool}`.
    if isinstance(raw.get("actors"), dict):
        actors = raw["actors"]
        if "person" in actors:
            cfg.person.enabled = bool(actors["person"])
        if "cat" in actors:
            cfg.cat.enabled = bool(actors["cat"])

    return cfg
