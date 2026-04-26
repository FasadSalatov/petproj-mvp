"""Persistent achievement tracking.

Stats accumulate across sessions in `~/.petproj_mvp/achievements.json`.
Reach a threshold and the achievement unlocks once — the cat scene can
react with a celebratory bubble.

Stats:
    jumps             — every successful landing
    pets              — every click-on-cat registered
    treats            — every treat fully eaten
    windows_visited   — distinct window hwnds the cat has stood on
    runtime_minutes   — minutes the app has been running (cumulative)

Add new achievements by extending ACHIEVEMENTS — order matters only for
the unlock sequence shown in the tray submenu.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Achievement:
    key: str
    label: str
    threshold: int
    stat: str


ACHIEVEMENTS: tuple[Achievement, ...] = (
    Achievement("first_jump",    "jumper!",       1,   "jumps"),
    Achievement("ten_jumps",     "sky cat",       10,  "jumps"),
    Achievement("fifty_jumps",   "acrobat",       50,  "jumps"),
    Achievement("first_pet",     "friendly!",     1,   "pets"),
    Achievement("ten_pets",      "cuddly",        10,  "pets"),
    Achievement("fifty_pets",    "best friend",   50,  "pets"),
    Achievement("first_treat",   "yum!",          1,   "treats"),
    Achievement("foodie",        "foodie",        5,   "treats"),
    Achievement("explorer",      "explorer",      3,   "windows_visited"),
    Achievement("nomad",         "nomad",         10,  "windows_visited"),
    Achievement("survivor",      "long shift",    60,  "runtime_minutes"),
)

DEFAULT_PATH = os.path.join(
    os.path.expanduser("~"), ".petproj_mvp", "achievements.json"
)


class AchievementTracker:
    def __init__(self, path: str = DEFAULT_PATH) -> None:
        self.path = path
        self.stats: dict[str, float] = {
            "jumps": 0, "pets": 0, "treats": 0,
            "windows_visited": 0, "runtime_minutes": 0,
        }
        self.unlocked: set[str] = set()
        self._visited_hwnds: set[int] = set()
        self._load()

    # ---- public API --------------------------------------------------

    def increment(self, stat: str, n: float = 1) -> list[str]:
        """Add to a stat, return labels of newly-unlocked achievements."""
        self.stats[stat] = self.stats.get(stat, 0) + n
        return self._check()

    def visit_window(self, hwnd: int) -> list[str]:
        """Track distinct window hwnds for the explorer/nomad chain."""
        if hwnd in self._visited_hwnds:
            return []
        self._visited_hwnds.add(hwnd)
        return self.increment("windows_visited")

    def reset(self) -> None:
        self.stats = {k: 0 for k in self.stats}
        self.unlocked = set()
        self._visited_hwnds.clear()
        self._save()

    # ---- internals ---------------------------------------------------

    def _check(self) -> list[str]:
        newly: list[str] = []
        for a in ACHIEVEMENTS:
            if a.key in self.unlocked:
                continue
            if self.stats.get(a.stat, 0) >= a.threshold:
                self.unlocked.add(a.key)
                newly.append(a.label)
        if newly:
            self._save()
        return newly

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in (data.get("stats") or {}).items():
                if k in self.stats:
                    self.stats[k] = v
            self.unlocked = set(data.get("unlocked") or [])
        except Exception:
            pass

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({
                    "stats": self.stats,
                    "unlocked": sorted(self.unlocked),
                }, f, indent=2)
        except Exception:
            pass

    # ---- iteration helper for the tray menu --------------------------

    def all_progress(self) -> Iterable[tuple[Achievement, bool, float]]:
        for a in ACHIEVEMENTS:
            yield a, a.key in self.unlocked, float(self.stats.get(a.stat, 0))
