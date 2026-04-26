"""Rebuild assets/cat/cat.{png,json} from a freshly extracted PixelLab ZIP.

Reads frames from `assets/cat/_pixellab_zip/animations/<hash>/<dir>/frame_NNN.png`,
auto-trims each animation by the union bbox of its non-transparent content
(so the cat is bottom-aligned in every frame and there's no floating-above-
ground padding), pads all frames to a global (max_w × max_h) so widget sizing
is consistent across states, and writes the final sheet + Aseprite-style JSON.

Usage:
    python rebuild_cat_sheet.py

Requires the ZIP to already be extracted under assets/cat/_pixellab_zip/.

Output tags (our project convention, not PixelLab's names):
    stand   ← idle (east)               — single direction is fine, mirrored at runtime
    walk    ← slow-run (east)
    run     ← running-8-frames (east)
    lie     ← seated-on-belly-idle (east)
    sit     ← sitting (south)           — frontal projection, no mirroring needed
    jump    ← jumping (east)            — used by cat_scene for prep / air / land
    angry   ← acting_angry (east)       — shown on flee + extreme hunger
    groom   ← licking (east)            — occasional self-grooming idle
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication


@dataclass
class AnimSpec:
    tag: str                  # our project tag name (walk, run, ...)
    folder_glob_match: str    # substring to match against PixelLab folder names
    direction: str            # "east" | "west" | "south" | "north"
    frame_count: int          # expected number of frames (for sanity check + folder picking)
    cycle_direction: str      # forward | pingpong | reverse


# Order matters — this defines frame indices in the output sheet.
# folder_glob_match values reflect the ACTUAL folder names PixelLab puts in the ZIP,
# which differ from the template_animation_id (e.g. slow-run → running_slowly-,
# sitting → sitting_down-). Multiple matches are disambiguated via frame_count.
ANIMS: list[AnimSpec] = [
    AnimSpec("stand", "standing_up-",        "east",  9,  "pingpong"),
    AnimSpec("walk",  "running_slowly-",     "east",  8,  "forward"),
    AnimSpec("run",   "running-",            "east",  8,  "forward"),   # running-8-frames
    AnimSpec("lie",   "lying_on_belly-",     "east",  10, "pingpong"),
    AnimSpec("sit",   "sitting_down-",       "south", 8,  "pingpong"),
    AnimSpec("jump",  "jumping-",            "east",  8,  "forward"),
    AnimSpec("angry", "acting_angry-",       "east",  7,  "pingpong"),
    AnimSpec("groom", "licking-",            "east",  12, "forward"),
]


def find_anim_folder(animations_root: Path, spec: AnimSpec) -> Path:
    """Locate the folder for this animation by name substring + frame count check.

    PixelLab uses hash-suffixed names (e.g. running-32159d5f, animation-4427259d).
    The 8-frame `running-` folder is running-8-frames (since we also have running-4-frames).
    `frame_count==0` means we don't filter by count (only one folder should match).
    """
    candidates = sorted(p for p in animations_root.iterdir() if p.is_dir() and spec.folder_glob_match in p.name)
    matched: list[Path] = []
    for c in candidates:
        dir_path = c / spec.direction
        if not dir_path.exists():
            continue
        n = sum(1 for f in dir_path.iterdir() if f.suffix == ".png")
        if spec.frame_count == 0 or n == spec.frame_count:
            matched.append(c)
    if not matched:
        raise SystemExit(
            f"No matching folder for tag={spec.tag} "
            f"(glob='{spec.folder_glob_match}', dir={spec.direction}, "
            f"frames={spec.frame_count}). Candidates: {[c.name for c in candidates]}"
        )
    if len(matched) > 1:
        raise SystemExit(
            f"Multiple matches for tag={spec.tag}: {[m.name for m in matched]}. "
            f"Tighten folder_glob_match."
        )
    return matched[0] / spec.direction


def load_frames(anim_dir: Path) -> list[QImage]:
    files = sorted(anim_dir.glob("frame_*.png"))
    if not files:
        raise SystemExit(f"No frame_*.png in {anim_dir}")
    return [QImage(str(f)) for f in files]


def find_bbox(img: QImage, alpha_threshold: int = 10) -> tuple[int, int, int, int] | None:
    """Tight bbox of non-transparent pixels in `img`. None if entirely transparent."""
    w, h = img.width(), img.height()
    x_min, y_min, x_max, y_max = w, h, -1, -1
    for y in range(h):
        for x in range(w):
            if img.pixelColor(x, y).alpha() > alpha_threshold:
                if x < x_min: x_min = x
                if y < y_min: y_min = y
                if x > x_max: x_max = x
                if y > y_max: y_max = y
    if x_max < 0:
        return None
    return (x_min, y_min, x_max + 1, y_max + 1)  # half-open


def union_bbox(boxes: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int]:
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


@dataclass
class TrimmedAnim:
    spec: AnimSpec
    frames: list[QImage]            # cropped frames, all same size
    bbox: tuple[int, int, int, int]
    width: int
    height: int


def trim_animation(spec: AnimSpec, anim_dir: Path) -> TrimmedAnim:
    raw_frames = load_frames(anim_dir)
    boxes = []
    for img in raw_frames:
        b = find_bbox(img)
        if b is None:
            raise SystemExit(f"empty frame in {anim_dir}")
        boxes.append(b)
    union = union_bbox(boxes)
    x0, y0, x1, y1 = union
    cropped = []
    for img in raw_frames:
        sub = img.copy(x0, y0, x1 - x0, y1 - y0)
        cropped.append(sub)
    return TrimmedAnim(
        spec=spec, frames=cropped, bbox=union, width=x1 - x0, height=y1 - y0,
    )


def main() -> int:
    app = QApplication(sys.argv)  # noqa: F841

    here = Path(__file__).parent
    animations_root = here / "assets" / "cat" / "_pixellab_zip" / "animations"
    if not animations_root.exists():
        raise SystemExit(
            f"Expected extracted ZIP at {animations_root}. Download via curl + unzip first."
        )

    out_png = here / "assets" / "cat" / "cat.png"
    out_json = here / "assets" / "cat" / "cat.json"

    # Trim each animation by its own union bbox.
    trimmed: list[TrimmedAnim] = []
    for spec in ANIMS:
        anim_dir = find_anim_folder(animations_root, spec)
        ta = trim_animation(spec, anim_dir)
        print(
            f"  {spec.tag:6s} {len(ta.frames)} frames, bbox={ta.bbox}, "
            f"size={ta.width}x{ta.height} (from {anim_dir.parent.name}/{anim_dir.name})"
        )
        trimmed.append(ta)

    # Global max so all frames in the sheet share dimensions — keeps the
    # SpriteWidget size constant when transitioning between states.
    max_w = max(ta.width for ta in trimmed)
    max_h = max(ta.height for ta in trimmed)
    print(f"\nglobal frame size: {max_w}x{max_h}")

    total_frames = sum(len(ta.frames) for ta in trimmed)
    sheet = QPixmap(max_w * total_frames, max_h)
    sheet.fill(Qt.GlobalColor.transparent)
    painter = QPainter(sheet)

    frame_meta: dict = {}
    tag_meta: list = []
    frame_idx = 0
    for ta in trimmed:
        tag_from = frame_idx
        for i, img in enumerate(ta.frames):
            # Pad to (max_w, max_h): horizontally centered, vertically bottom-aligned.
            x_offset = (max_w - ta.width) // 2
            y_offset = max_h - ta.height
            slot_x = frame_idx * max_w
            painter.drawImage(slot_x + x_offset, y_offset, img)
            name = f"{ta.spec.tag}_{i}"
            frame_meta[f"cat {name}.aseprite"] = {
                "frame": {"x": slot_x, "y": 0, "w": max_w, "h": max_h},
                "rotated": False, "trimmed": False,
                "spriteSourceSize": {"x": 0, "y": 0, "w": max_w, "h": max_h},
                "sourceSize": {"w": max_w, "h": max_h},
                "duration": 100,
            }
            frame_idx += 1
        tag_meta.append({
            "name": ta.spec.tag,
            "from": tag_from,
            "to": frame_idx - 1,
            "direction": ta.spec.cycle_direction,
        })
    painter.end()
    sheet.save(str(out_png))
    print(f"\nwrote {out_png}")

    meta = {
        "frames": frame_meta,
        "meta": {
            "app": "petproj rebuild_cat_sheet.py",
            "version": "2",
            "image": "cat.png",
            "format": "RGBA8888",
            "size": {"w": max_w * total_frames, "h": max_h},
            "scale": "1",
            "frameTags": tag_meta,
            "layers": [{"name": "main", "opacity": 255, "blendMode": "normal"}],
            "_notes": (
                "Auto-trimmed per-animation by union bbox; padded to common "
                f"({max_w}x{max_h}) with cat bottom-aligned in every frame. "
                "Built from PixelLab character d5ffdf62-8497-4365-ae0e-6fc4aefb5d15 "
                "(Tabby Hopper, side view, 64px, with jump animation)."
            ),
        },
    }
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    print(f"wrote {out_json}")
    print(f"\ntotal frames: {total_frames}, tags: {[t['name'] for t in tag_meta]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
