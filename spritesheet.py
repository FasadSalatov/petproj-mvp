"""Load Aseprite-style sprite sheets (PNG + JSON) into QPixmaps for runtime use.

Workflow:
    sheet = SpriteSheet.load("assets/person")
    walk_frames = sheet.animation("walk", scale=5, mirror=False)
    sit_frame = sheet.frame_by_name("sit", scale=5)

The JSON is the same shape Aseprite emits via "Export Sprite Sheet".
You can edit the PNG in any pixel-art editor and the runtime will pick up
changes on next start. Frame coordinates come from JSON, so resizing frames
is fine as long as you re-export the JSON to match.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QPixmap


@dataclass
class FrameInfo:
    name: str
    rect: QRect      # source rect inside the sheet PNG
    duration_ms: int


@dataclass
class TagInfo:
    name: str
    from_idx: int
    to_idx: int
    direction: str   # "forward" | "reverse" | "pingpong"


class SpriteSheet:
    def __init__(self, atlas: QPixmap, frames: list[FrameInfo], tags: dict[str, TagInfo]):
        if atlas.isNull():
            raise ValueError("atlas pixmap is null")
        self.atlas = atlas
        self.frames = frames                          # ordered as in JSON
        self.tags = tags                              # by tag name
        self._by_name = {f.name: f for f in frames}
        # Frame size — assume uniform within a sheet (it is for our exports).
        self.frame_w = frames[0].rect.width() if frames else 0
        self.frame_h = frames[0].rect.height() if frames else 0
        self._scaled_cache: dict[tuple[int, int, bool], QPixmap] = {}

    # ---- factory --------------------------------------------------------

    @classmethod
    def load(cls, base_path: str | os.PathLike) -> "SpriteSheet":
        """`base_path` may be either '<dir>/foo' or '<dir>/foo.png' — we strip
        the extension and look for foo.png + foo.json next to each other."""
        p = Path(base_path)
        if p.suffix:
            p = p.with_suffix("")
        png = p.with_suffix(".png")
        js = p.with_suffix(".json")
        if not png.exists():
            raise FileNotFoundError(png)
        if not js.exists():
            raise FileNotFoundError(js)

        atlas = QPixmap(str(png))
        with open(js, "r", encoding="utf-8") as f:
            meta = json.load(f)

        # Frames may be a dict (Aseprite "Hash" mode) or list ("Array" mode).
        raw_frames = meta["frames"]
        if isinstance(raw_frames, dict):
            entries = list(raw_frames.items())
        else:
            entries = [(item.get("filename", str(i)), item) for i, item in enumerate(raw_frames)]

        frames: list[FrameInfo] = []
        for key, entry in entries:
            r = entry["frame"]
            rect = QRect(r["x"], r["y"], r["w"], r["h"])
            frames.append(FrameInfo(
                name=_strip_ase_suffix(key),
                rect=rect,
                duration_ms=int(entry.get("duration", 100)),
            ))

        tags: dict[str, TagInfo] = {}
        for t in meta.get("meta", {}).get("frameTags", []):
            tags[t["name"]] = TagInfo(
                name=t["name"],
                from_idx=t["from"],
                to_idx=t["to"],
                direction=t.get("direction", "forward"),
            )

        return cls(atlas, frames, tags)

    # ---- access ---------------------------------------------------------

    def frame(self, idx: int, scale: float = 1, mirror: bool = False) -> QPixmap:
        """Extract a single frame by index, scaled (nearest-neighbour) and
        optionally horizontally mirrored. Cached for repeated calls.

        `scale` may be float — useful for downsizing oversized AI-generated
        sprites to match the project's display size."""
        cache_key = (idx, scale, mirror)
        if cache_key in self._scaled_cache:
            return self._scaled_cache[cache_key]
        info = self.frames[idx]
        sub = self.atlas.copy(info.rect)
        if scale != 1:
            target_w = max(1, int(round(info.rect.width() * scale)))
            target_h = max(1, int(round(info.rect.height() * scale)))
            sub = sub.scaled(
                target_w, target_h,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
        if mirror:
            sub = sub.transformed(_mirror_transform())
        self._scaled_cache[cache_key] = sub
        return sub

    def frame_by_name(self, name: str, scale: int = 1, mirror: bool = False) -> QPixmap:
        info = self._by_name[name]
        idx = self.frames.index(info)
        return self.frame(idx, scale, mirror)

    def animation(self, tag_name: str, scale: int = 1, mirror: bool = False) -> list[QPixmap]:
        """Return the frames belonging to `tag_name`, expanded to the cycle order
        described by the tag's direction."""
        if tag_name not in self.tags:
            raise KeyError(f"no such tag: {tag_name}")
        t = self.tags[tag_name]
        idxs = list(range(t.from_idx, t.to_idx + 1))
        if t.direction == "reverse":
            idxs.reverse()
        elif t.direction == "pingpong" and len(idxs) > 2:
            idxs = idxs + idxs[-2:0:-1]
        elif t.direction == "pingpong":
            # 1- or 2-frame ping-pong = no extra reverse.
            pass
        return [self.frame(i, scale, mirror) for i in idxs]


# ---- helpers ------------------------------------------------------------

_ASE_SUFFIX_RE = re.compile(r"^(?:[^ ]+ )?(.+?)(?:\.aseprite)?$")


def _strip_ase_suffix(key: str) -> str:
    """Aseprite default keys look like 'person walk_a.aseprite'.
    Reduce to just 'walk_a' so callers can address frames by short name."""
    m = _ASE_SUFFIX_RE.match(key)
    return m.group(1) if m else key


def _mirror_transform():
    from PyQt6.QtGui import QTransform
    t = QTransform()
    t.scale(-1, 1)
    return t
