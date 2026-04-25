"""Procedural pixel sprites — generated as ASCII grids, rasterised to PNG.

Side-view (profile) character. Faces right by default; mirrored for left-walking.
Person frames are 18 wide × 28 tall (sprite-pixel units), so they share a
common bounding box and switching frames doesn't make the widget jump.

Higher level: `export_sprites.py` writes these grids out to PNG sheets +
Aseprite-compatible JSON. The runtime can then load from PNG (in `assets/`)
and you can edit those PNGs in Aseprite/LibreSprite/Piskel without touching code.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QPixmap

PALETTE = {
    ".": (0, 0, 0, 0),
    "K": (30, 30, 35, 255),       # outline / dark
    "S": (255, 220, 180, 255),    # skin
    "H": (90, 50, 30, 255),       # hair
    "B": (60, 110, 200, 255),     # shirt
    "P": (40, 50, 100, 255),      # pants
    "W": (240, 240, 240, 255),    # eye white
    "R": (180, 60, 60, 255),      # mouth
    "G": (140, 95, 55, 255),      # wood
    "L": (180, 180, 195, 255),    # laptop body
    "D": (35, 40, 55, 255),       # laptop screen
    "Y": (255, 230, 80, 255),     # accent
}


# -------------------- person, profile facing right --------------------
# Width 18, height 28.
# Head (rows 0..15), body (rows 16..27).

# Head + neck — identical across all walk/stand frames.
# Silhouette weaves to suggest a real profile:
#   forehead recessed (col 15) → brow protrudes (col 16) → eye recess (col 15)
#   → cheek (col 16) → nose tip (col 17, max) → below mouth recedes (15)
#   → chin (14) → jaw recedes back to neck (13 → 12).
_HEAD = [
    "......HHHHHH......",  # 0  crown narrow
    "....HHHHHHHHHH....",  # 1  crown wider
    "..HHHHHHHHHHHHK...",  # 2  K col 14 (top-right)
    ".HHHHHHHHHHHHHK...",  # 3  K col 14
    ".HHHHHHHHHHHHHHK..",  # 4  K col 15
    ".HHHHHHHSSSSSSSK..",  # 5  forehead — K col 15 (recess)
    ".HHHHHHSSSSSSSSSK.",  # 6  brow ridge bump — K col 16
    ".HHHHHSSSSSSSSSK..",  # 7  eye-socket recess — K col 15
    ".HHHHSSSSSSKSSSSK.",  # 8  eye top (1x2 K eye at col 11)
    ".HHHHSSSSSSKSSSSK.",  # 9  eye bottom
    ".HHHSSSSSSSSSSSSK.",  # 10 cheek — K col 16
    ".HHHSSSSSSSSSSSSSK",  # 11 nose tip — K col 17 (max)
    "..HHHSSSSSSSSSSSK.",  # 12 below nose — K col 16
    "..HHSSSSSSSRRRSSK.",  # 13 mouth (R cols 11-13)
    "...HHSSSSSSSSSSK..",  # 14 below mouth — K col 15
    "....HSSSSSSSSSK...",  # 15 chin — K col 14
    "....KSSSSSSSSK....",  # 16 jaw → neck (K cols 4 and 13)
    "......KSSSSSK.....",  # 17 neck (K cols 6 and 12)
]

# Body — 10 rows (rows 18..27). Arms hanging at sides.
_BODY_NEUTRAL = [
    "...KKBBBBBBBKK....",  # 18 collar
    "..KSBBBBBBBBBSK...",  # 19 shoulders + arms (S = visible hand)
    "..KSBBBBBBBBBSK...",  # 20
    "..KSBBBBBBBBBSK...",  # 21
    "...KBBBBBBBBBK....",  # 22 hands tucked, waist
    "...KPPPPPPPPPK....",  # 23 pants top
    "...KPPPPPPPPPK....",  # 24
    "...KPPPP.PPPPK....",  # 25 small leg gap
    "...KPP...PPPK.....",  # 26
    "..KKK....KKK......",  # 27 shoes
]

# Body — front arm swung 1 col forward (used in walking strides).
# Just the upper portion (5 rows) — legs come from a separate block.
_BODY_ARM_FWD_UPPER = [
    "...KKBBBBBBBKK....",  # 18 collar
    "..KSBBBBBBBBBSK...",  # 19 shoulders
    "..KSBBBBBBBBBSSK..",  # 20 hand 1 col forward
    "..KSBBBBBBBBBSK...",  # 21
    "...KBBBBBBBBBK....",  # 22 waist
]

# Legs (rows 23..27) in mid-/wide stride — wide split (back leg trailing).
_LEGS_STRIDE_WIDE = [
    "...KPPPPPPPPPK....",  # 23
    "..KPPPPPPPPPPK....",  # 24
    "..KPP..PPPPPPK....",  # 25
    ".KPP......PPPK....",  # 26
    "KKK........KKK....",  # 27
]

# Legs in small stride.
_LEGS_STRIDE_SMALL = [
    "...KPPPPPPPPPK....",
    "..KPPPPPPPPPPK....",
    "..KPP..PPPPPPK....",
    "..KP....PPPPK.....",
    ".KK......KKK......",
]


def _compose(*chunks):
    out = []
    for chunk in chunks:
        out.extend(chunk)
    return out


PERSON_STAND = _HEAD + _BODY_NEUTRAL

# 4-frame walk cycle: contact (wide), pass, contact (small), pass.
PERSON_WALK_A = _HEAD + _BODY_ARM_FWD_UPPER + _LEGS_STRIDE_WIDE
PERSON_WALK_PASS = _HEAD + _BODY_NEUTRAL
PERSON_WALK_B = _HEAD + _BODY_ARM_FWD_UPPER + _LEGS_STRIDE_SMALL

PERSON_WALK_CYCLE = [PERSON_WALK_A, PERSON_WALK_PASS, PERSON_WALK_B, PERSON_WALK_PASS]

# Sitting / typing. 10 rows (18..27). Upper body present; arms forward to keyboard;
# legs hidden under table.
_SIT_BODY_A = [
    "...KKBBBBBBBKK....",  # 18 collar
    "..KSBBBBBBBBBSK...",  # 19 shoulders
    "..KSBBBBBBBBSK....",  # 20 arm starts forward
    "..KSBBBBBBSSSSK...",  # 21 forearm reaches forward
    "..KSBBBBSSSSSSSK..",  # 22 forearm extends right
    "..KSBBSSKKKKKSK...",  # 23 hand on keyboard (KKKKK = fingers)
    "..KBBBBBBBK.......",  # 24 belt
    "..KPPPPPPPPP......",  # 25 thigh going horizontally
    "..KKKKKKKKKK......",  # 26 underside of thigh (under table line)
    "..................",  # 27 empty (knees/lower hidden)
]

_SIT_BODY_B = [
    "...KKBBBBBBBKK....",
    "..KSBBBBBBBBBSK...",
    "..KSBBBBBBBBSK....",
    "..KSBBBBBBSSSSK...",
    "..KSBBBBSSSKSSSK..",  # different finger stretch
    "..KSBBSSKKKBKSK...",  # different hand position
    "..KBBBBBBBK.......",
    "..KPPPPPPPPP......",
    "..KKKKKKKKKK......",
    "..................",
]

PERSON_SIT = _HEAD + _SIT_BODY_A
PERSON_SIT_TYPE = _HEAD + _SIT_BODY_B


# -------------------- props (kept profile-side) --------------------

TABLE = [
    "GGGGGGGGGGGGGGGGGGGG",
    "GGGGGGGGGGGGGGGGGGGG",
    "KKKKKKKKKKKKKKKKKKKK",
    ".K................K.",
    ".K................K.",
    ".K................K.",
    ".K................K.",
    ".K................K.",
    ".K................K.",
    ".K................K.",
]

LAPTOP = [
    ".........KKKK.",
    ".........KDDK.",
    ".........KDDK.",
    ".........KDDK.",
    ".........KDDK.",
    ".........KDDK.",
    "KKKKKKKKKKKKK.",
    "KLLLLLLLLLLLK.",
    "KKKKKKKKKKKKK.",
]

CHAIR = [
    "KKKKKKKK",  # 0  seat top
    "GKKKKKKG",  # 1  seat
    ".K....K.",  # 2  legs
    ".K....K.",  # 3
    ".K....K.",  # 4
    ".K....K.",  # 5
    ".KK..KK.",  # 6  feet
]


# -------------------- icon (for tray) --------------------
ICON_HEAD = [
    "....HHHHH.....",
    "..HHHHHHHHK...",
    ".HHHHHHHHHK...",
    ".HHHSSSSSSK...",
    ".HHHSKKSSSKK..",
    ".HHHSWWSSSSKKK",
    ".HHHSKKSSSSKK.",
    "..HHSSSSSSK...",
    "..HHSSRRRRSK..",
    "...HSSSSSSK...",
    "....KSSSSSK...",
    "....KBBBBBK...",
    "...KBBBBBBBK..",
    "....KKKKKKK...",
]


# -------------------- renderer --------------------

def _grid_size(grid):
    return len(grid[0]), len(grid)


def render_frame(grid, scale=4, mirror=False):
    """Rasterise an ASCII grid into a QPixmap, optional horizontal mirror."""
    w, h = _grid_size(grid)
    pix = QPixmap(w * scale, h * scale)
    pix.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            rgba = PALETTE.get(ch, PALETTE["."])
            if rgba[3] == 0:
                continue
            sx = (w - 1 - x) if mirror else x
            painter.fillRect(
                sx * scale, y * scale, scale, scale,
                QColor(*rgba),
            )
    painter.end()
    return pix
