"""Estimate per-frame walk/run stride deltas from the cat sprite sheet.

Method: in each animation frame, find where the cat's silhouette touches
the ground (lowest non-transparent pixel row, take that row's average x).
That's the "planted paw" contact point. As the body moves forward through
the cycle, the planted paw should appear to move BACKWARD inside the sprite
— so `delta_body[i] = contact_x[i] - contact_x[i+1]` is the body's forward
movement during frame i. Outliers (negative deltas or jumps from plant
changes between feet) are replaced with the median of valid deltas.

Result is multiplied by `cat.scale` (display scale) and written to
`config.cat.walk_frame_deltas` / `run_frame_deltas`.

Usage:
    python estimate_cat_strides.py
    # then restart main.py to see the new gait
"""
from __future__ import annotations

import statistics
import sys
from pathlib import Path

from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

from config import Config
from spritesheet import SpriteSheet


def find_contact_x(img: QImage) -> float | None:
    """Average x of the lowest non-transparent pixel row in `img`. None if
    fully transparent."""
    h, w = img.height(), img.width()
    # Walk rows bottom-up to find the first non-transparent row.
    for y in range(h - 1, -1, -1):
        xs = [x for x in range(w) if img.pixelColor(x, y).alpha() > 10]
        if xs:
            return sum(xs) / len(xs)
    return None


def estimate_deltas(frames: list[QImage]) -> list[float]:
    """Compute per-frame body-forward stride deltas in source pixels.
    Returns deltas[i] = body movement during frame i (frame i → i+1, wrapping)."""
    contacts: list[float | None] = [find_contact_x(f) for f in frames]
    if any(c is None for c in contacts):
        return [0.0] * len(frames)

    n = len(frames)
    raw = [contacts[i] - contacts[(i + 1) % n] for i in range(n)]

    # Valid deltas: positive (body moved forward), within a sane upper bound
    # (a single frame in a 50-wide cat won't legitimately be > ~8 px stride).
    valid = [d for d in raw if 0 < d <= 8]
    if len(valid) >= 2:
        replacement = statistics.median(valid)
    elif valid:
        replacement = valid[0]
    else:
        # Fully degenerate — nothing useful in the contact track.
        # Fall back to a sane default (≈ uniform 5px).
        replacement = 5.0

    return [d if 0 < d <= 8 else replacement for d in raw]


def main() -> int:
    app = QApplication(sys.argv)  # noqa: F841

    here = Path(__file__).parent
    sheet = SpriteSheet.load(str(here / "assets" / "cat" / "cat"))
    config = Config.load()

    out: dict[str, list[float]] = {}
    for tag in ("walk", "run"):
        if tag not in sheet.tags:
            print(f"  {tag}: tag missing in cat.json; skipping")
            continue
        t = sheet.tags[tag]
        idxs = list(range(t.from_idx, t.to_idx + 1))
        imgs = [sheet.frame(i, scale=1).toImage() for i in idxs]
        deltas_src = estimate_deltas(imgs)
        contacts = [find_contact_x(im) for im in imgs]
        out[tag] = deltas_src

        # Diagnostic: show contacts and deltas in source-pixel units.
        contacts_str = ", ".join(f"{c:.1f}" for c in contacts)
        deltas_str = ", ".join(f"{d:+.2f}" for d in deltas_src)
        print(f"  {tag} ({len(idxs)} frames)")
        print(f"    contact x:   {contacts_str}")
        print(f"    delta_src:   {deltas_str}    sum={sum(deltas_src):.2f}")

    scale = config.cat.scale
    print(f"\nScaling by cat.scale = {scale} for runtime deltas:")
    if "walk" in out:
        config.cat.walk_frame_deltas = [round(d * scale, 2) for d in out["walk"]]
        print(f"  cat.walk_frame_deltas = {config.cat.walk_frame_deltas}"
              f"   (cycle total {sum(config.cat.walk_frame_deltas):.1f} px)")
    if "run" in out:
        config.cat.run_frame_deltas = [round(d * scale, 2) for d in out["run"]]
        print(f"  cat.run_frame_deltas  = {config.cat.run_frame_deltas}"
              f"   (cycle total {sum(config.cat.run_frame_deltas):.1f} px)")

    config.save()
    print("\nWritten to config.json. Restart main.py to apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
