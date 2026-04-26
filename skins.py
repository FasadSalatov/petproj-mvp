"""Runtime skin tinting via HSV hue/saturation/value transform.

A skin is a single (hue_delta, sat_factor, val_factor) triple applied to
every coloured pixel of the cat's sprite atlas. Near-black outline pixels
and near-white belly pixels are preserved (low saturation = outline/fur
detail; we only re-tint mid-saturation body colours).
"""
from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtGui import QColor, QImage, QPixmap


@dataclass(frozen=True)
class Skin:
    name: str            # menu label
    hue_delta: int       # degrees (0..360)
    sat_factor: float    # multiply S channel
    val_factor: float    # multiply V channel
    # Below this saturation we treat the pixel as outline/grey and DON'T
    # tint it, so the eyes/whiskers/outline stay readable.
    sat_floor: int = 28


SKINS: tuple[Skin, ...] = (
    Skin("tabby (orange)",   0,   1.00, 1.00),
    Skin("black",            0,   0.20, 0.45),
    Skin("grey",             0,   0.10, 0.95),
    Skin("white",            0,   0.05, 1.55),
    Skin("blue",             170, 1.00, 1.00),
    Skin("calico (purple)",  280, 0.90, 1.00),
    Skin("mint",             105, 0.80, 1.05),
)


def hue_shifted_pixmap(src: QPixmap, skin: Skin) -> QPixmap:
    """Return a new QPixmap with skin colour transform applied. Skips fully
    transparent pixels and low-saturation outline/eye pixels."""
    img = src.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    w, h = img.width(), img.height()
    for y in range(h):
        for x in range(w):
            argb = img.pixel(x, y)
            color = QColor.fromRgba(argb)
            if color.alpha() == 0:
                continue
            hh, ss, vv, aa = color.getHsv()
            if ss < skin.sat_floor:
                # Greyscale-ish pixel — outline / belly highlights. Only
                # apply the value scale (keeps black black, brightens
                # white when val_factor > 1).
                new_v = max(0, min(255, int(vv * skin.val_factor)))
                if new_v == vv:
                    continue
                new_color = QColor.fromHsv(hh if hh >= 0 else 0, ss, new_v)
            else:
                new_h = (hh + skin.hue_delta) % 360 if hh >= 0 else 0
                new_s = max(0, min(255, int(ss * skin.sat_factor)))
                new_v = max(0, min(255, int(vv * skin.val_factor)))
                new_color = QColor.fromHsv(new_h, new_s, new_v)
            new_color.setAlpha(aa)
            img.setPixel(x, y, new_color.rgba())
    return QPixmap.fromImage(img)
