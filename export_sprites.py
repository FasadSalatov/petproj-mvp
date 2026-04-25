"""Render the ASCII sprite definitions out to PNG sheets + Aseprite-compatible JSON.

Output goes to ./assets/. Each sheet packs all frames of one logical "actor"
(person, table, laptop, chair, icon) horizontally, at sprite-source resolution
(no extra scaling — the runtime scales when drawing).

JSON shape follows Aseprite's "Sprite Sheet" export:
    {
      "frames": {
        "<name> 0.aseprite": {
          "frame": {"x": 0, "y": 0, "w": W, "h": H},
          "duration": 100,
          ...
        },
        ...
      },
      "meta": {
        "image": "person.png",
        "size": {"w": SHEET_W, "h": SHEET_H},
        "frameTags": [
          {"name": "walk", "from": 0, "to": 3, "direction": "forward"},
          ...
        ]
      }
    }

This way you can ALSO open the PNG in Aseprite/LibreSprite/Piskel, edit pixels,
re-export, and the game loads the new art without code changes.
"""
import json
import os
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QPixmap
from PyQt6.QtWidgets import QApplication

import sprites


def _grid_dim(grid):
    return len(grid[0]), len(grid)


def _pack_horizontal(grids, frame_w, frame_h, scale=1):
    """Render each grid into a single horizontal sheet at native pixel size."""
    sheet_w = frame_w * len(grids) * scale
    sheet_h = frame_h * scale
    sheet = QPixmap(sheet_w, sheet_h)
    sheet.fill(Qt.GlobalColor.transparent)
    painter = QPainter(sheet)
    for i, g in enumerate(grids):
        pix = sprites.render_frame(g, scale=scale)
        painter.drawPixmap(i * frame_w * scale, 0, pix)
    painter.end()
    return sheet


def _aseprite_json(actor_name, frames, frame_w, frame_h, tags):
    out = {
        "frames": {},
        "meta": {
            "app": "petproj export_sprites.py",
            "version": "1",
            "image": f"{actor_name}.png",
            "format": "RGBA8888",
            "size": {"w": frame_w * len(frames), "h": frame_h},
            "scale": "1",
            "frameTags": tags,
            "layers": [{"name": "main", "opacity": 255, "blendMode": "normal"}],
        },
    }
    for i, name in enumerate(frames):
        # Aseprite-style key: "<file> <frame>.aseprite". We use <actor> <name>.
        key = f"{actor_name} {name}.aseprite"
        out["frames"][key] = {
            "frame": {"x": i * frame_w, "y": 0, "w": frame_w, "h": frame_h},
            "rotated": False,
            "trimmed": False,
            "spriteSourceSize": {"x": 0, "y": 0, "w": frame_w, "h": frame_h},
            "sourceSize": {"w": frame_w, "h": frame_h},
            "duration": 100,
        }
    return out


def _backup(path: str) -> None:
    """Move <path> to <path>.bak.<timestamp> if it exists, so a regenerate never
    silently destroys hand-edited assets."""
    if not os.path.exists(path):
        return
    import shutil
    import time
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bak = f"{path}.bak.{stamp}"
    shutil.copy2(path, bak)
    print(f"  backup: {os.path.basename(path)} -> {os.path.basename(bak)}")


def export_actor(actor_name, ordered_frames, tags, out_dir):
    """Write <actor>.png + <actor>.json into out_dir.

    `ordered_frames`: list of (frame_name, ascii_grid)
    `tags`: list of {name, from, to, direction} for animation tags
    """
    grids = [g for _, g in ordered_frames]
    names = [n for n, _ in ordered_frames]
    fw, fh = _grid_dim(grids[0])

    # Sanity: every frame must have the same dimensions.
    for n, g in ordered_frames:
        w, h = _grid_dim(g)
        if (w, h) != (fw, fh):
            raise ValueError(
                f"frame {actor_name}/{n} has dim {w}x{h}, expected {fw}x{fh}"
            )

    png_path = os.path.join(out_dir, f"{actor_name}.png")
    json_path = os.path.join(out_dir, f"{actor_name}.json")
    _backup(png_path)
    _backup(json_path)

    sheet = _pack_horizontal(grids, fw, fh, scale=1)
    sheet.save(png_path)

    meta = _aseprite_json(actor_name, names, fw, fh, tags)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    print(f"  {png_path} ({len(grids)} frames @ {fw}x{fh})")
    print(f"  {json_path}")


USAGE = """\
Usage:
    python export_sprites.py <target> [<target> ...]
    python export_sprites.py --all

Targets: person, table, laptop, chair, icon

This script OVERWRITES PNG + JSON in assets/ from the ASCII grids in
sprites.py. If you've hand-edited a PNG (e.g. painted the face), do NOT
run with --all — only re-export the targets you actually want regenerated.
"""


def main() -> int:
    args = [a for a in sys.argv[1:] if a]
    if not args:
        print(USAGE)
        return 1

    valid = {"person", "table", "laptop", "chair", "icon"}
    if args == ["--all"]:
        targets = sorted(valid)
        print(
            "WARNING: regenerating ALL assets — this will OVERWRITE any "
            "hand-edited PNGs in assets/.\n"
        )
    else:
        targets = []
        for a in args:
            if a not in valid:
                print(f"Unknown target: {a!r}\n\n{USAGE}")
                return 2
            targets.append(a)

    app = QApplication([])  # noqa: F841

    here = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(here, "assets")
    os.makedirs(out_dir, exist_ok=True)

    if "person" in targets:
        person_frames = [
            ("walk_a", sprites.PERSON_WALK_A),
            ("walk_pass", sprites.PERSON_WALK_PASS),
            ("walk_b", sprites.PERSON_WALK_B),
            ("walk_pass2", sprites.PERSON_WALK_PASS),
            ("stand", sprites.PERSON_STAND),
            ("sit", sprites.PERSON_SIT),
            ("sit_type", sprites.PERSON_SIT_TYPE),
        ]
        person_tags = [
            {"name": "walk", "from": 0, "to": 3, "direction": "forward"},
            {"name": "stand", "from": 4, "to": 4, "direction": "forward"},
            {"name": "sit", "from": 5, "to": 6, "direction": "pingpong"},
        ]
        export_actor("person", person_frames, person_tags, out_dir)

    # ---- props (single-frame each, no tags) ----
    default_tag = [{"name": "default", "from": 0, "to": 0, "direction": "forward"}]
    if "table" in targets:
        export_actor("table", [("default", sprites.TABLE)], default_tag, out_dir)
    if "laptop" in targets:
        export_actor("laptop", [("default", sprites.LAPTOP)], default_tag, out_dir)
    if "chair" in targets:
        export_actor("chair", [("default", sprites.CHAIR)], default_tag, out_dir)
    if "icon" in targets:
        export_actor("icon", [("head", sprites.ICON_HEAD)],
                     [{"name": "head", "from": 0, "to": 0, "direction": "forward"}],
                     out_dir)

    print(f"\nDone. Wrote: {', '.join(targets)} -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
