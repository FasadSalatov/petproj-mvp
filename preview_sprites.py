"""Render every sprite as a PNG to ./preview/ for visual inspection.

Usage:
    python preview_sprites.py

After running, open the petproj/MVP/preview/ folder to view the PNGs at 10×
zoom. Walk frames are also stitched into a single horizontal strip so the
animation cycle is easy to read at a glance.
"""
import os
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication

import sprites

PREVIEW_SCALE = 10  # 1 source pixel = 10 screen pixels


def render_strip(grids, scale, gap=4):
    """Render a list of grids side-by-side with a small gap, for cycle previews."""
    sub_pixs = [sprites.render_frame(g, scale) for g in grids]
    h = max(p.height() for p in sub_pixs)
    total_w = sum(p.width() for p in sub_pixs) + gap * (len(sub_pixs) - 1)
    out = QPixmap(total_w, h)
    out.fill(QColor(40, 40, 50))  # dark gray bg so transparent shows
    p = QPainter(out)
    x = 0
    for sp in sub_pixs:
        p.drawPixmap(x, 0, sp)
        x += sp.width() + gap
    p.end()
    return out


def render_with_bg(grid, scale, bg=QColor(40, 40, 50)):
    pix = sprites.render_frame(grid, scale)
    out = QPixmap(pix.size())
    out.fill(bg)
    p = QPainter(out)
    p.drawPixmap(0, 0, pix)
    p.end()
    return out


def main() -> int:
    app = QApplication(sys.argv)  # noqa: F841 needed for QPixmap

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "preview")
    os.makedirs(out_dir, exist_ok=True)

    singles = {
        "person_stand": sprites.PERSON_STAND,
        "person_walk_a": sprites.PERSON_WALK_A,
        "person_walk_pass": sprites.PERSON_WALK_PASS,
        "person_walk_b": sprites.PERSON_WALK_B,
        "person_sit": sprites.PERSON_SIT,
        "person_sit_type": sprites.PERSON_SIT_TYPE,
        "table": sprites.TABLE,
        "laptop": sprites.LAPTOP,
        "chair": sprites.CHAIR,
        "icon_head": sprites.ICON_HEAD,
    }

    for name, grid in singles.items():
        path = os.path.join(out_dir, f"{name}.png")
        render_with_bg(grid, PREVIEW_SCALE).save(path)
        print(f"  {path}")

    # Walk cycle as a strip (4 frames left-to-right).
    strip_path = os.path.join(out_dir, "walk_cycle_strip.png")
    render_strip(sprites.PERSON_WALK_CYCLE, PREVIEW_SCALE).save(strip_path)
    print(f"  {strip_path}")

    # Mirrored walk cycle (facing left).
    mirrored = [
        sprites.render_frame(g, PREVIEW_SCALE, mirror=True)
        for g in sprites.PERSON_WALK_CYCLE
    ]
    h = mirrored[0].height()
    gap = 4
    total_w = sum(p.width() for p in mirrored) + gap * (len(mirrored) - 1)
    strip = QPixmap(total_w, h)
    strip.fill(QColor(40, 40, 50))
    p = QPainter(strip)
    x = 0
    for sp in mirrored:
        p.drawPixmap(x, 0, sp)
        x += sp.width() + gap
    p.end()
    strip.save(os.path.join(out_dir, "walk_cycle_strip_left.png"))
    print(f"  {os.path.join(out_dir, 'walk_cycle_strip_left.png')}")

    print(f"\nDone. {len(singles) + 2} files written to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
