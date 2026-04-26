"""Tiny synthesised UI sound effects.

We don't ship WAV files — instead we generate them once per session into
`%TEMP%/petproj_mvp_sfx/` using the stdlib `wave` module + a couple of
math functions. Tones are picked to feel pixel-arty (square-ish waveform
with quick decay) and stay under 100 ms each so menus don't drag.

Public API:
    sfx.preload()              — generate WAVs on disk (idempotent)
    sfx.play("click")          — queue a sample by short name
    sfx.set_volume(0..1)       — global volume
    sfx.set_enabled(bool)      — kill switch (config-driven)
"""
from __future__ import annotations

import math
import os
import struct
import tempfile
import wave
from typing import Iterable

from PyQt6.QtCore import QObject, QUrl
from PyQt6.QtMultimedia import QSoundEffect


# ---- waveform builders ---------------------------------------------------

SAMPLE_RATE = 22050
BITS = 16
AMP_MAX = 2 ** (BITS - 1) - 1


def _square_with_decay(freq: float, ms: int, *,
                       duty: float = 0.5,
                       decay: float = 4.0) -> bytes:
    """Square wave at `freq` Hz for `ms` milliseconds with exponential
    amplitude decay. `decay` is the e-folding rate per second."""
    n = int(SAMPLE_RATE * ms / 1000)
    out = bytearray()
    period = SAMPLE_RATE / max(1.0, freq)
    high_samples = period * duty
    for i in range(n):
        env = math.exp(-decay * (i / SAMPLE_RATE))
        phase = i % period
        v = AMP_MAX if phase < high_samples else -AMP_MAX
        sample = int(v * env * 0.55)   # leave headroom for sequenced tones
        out += struct.pack("<h", sample)
    return bytes(out)


def _sequence(parts: Iterable[bytes]) -> bytes:
    return b"".join(parts)


def _silence(ms: int) -> bytes:
    n = int(SAMPLE_RATE * ms / 1000)
    return b"\x00\x00" * n


def _save_wav(path: str, frames: bytes) -> None:
    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(BITS // 8)
        f.setframerate(SAMPLE_RATE)
        f.writeframes(frames)


# ---- presets -------------------------------------------------------------

PRESETS: dict[str, bytes] = {}


def _build_presets() -> None:
    """Compose the tiny library of UI tones once."""
    if PRESETS:
        return
    PRESETS["click"] = _square_with_decay(880, 35, decay=22.0)
    PRESETS["hover"] = _square_with_decay(1320, 18, decay=30.0, duty=0.35)
    PRESETS["toggle_on"] = _sequence([
        _square_with_decay(660, 30, decay=20.0),
        _silence(8),
        _square_with_decay(990, 40, decay=18.0),
    ])
    PRESETS["toggle_off"] = _sequence([
        _square_with_decay(990, 30, decay=20.0),
        _silence(8),
        _square_with_decay(660, 40, decay=18.0),
    ])
    PRESETS["slider"] = _square_with_decay(1480, 12, decay=40.0, duty=0.3)
    PRESETS["pop"] = _sequence([
        _square_with_decay(440, 20, decay=22.0),
        _square_with_decay(660, 22, decay=20.0),
        _square_with_decay(990, 26, decay=18.0),
    ])


# ---- on-disk cache + Qt playback ----------------------------------------

_SFX_DIR = os.path.join(tempfile.gettempdir(), "petproj_mvp_sfx")
_EFFECTS: dict[str, QSoundEffect] = {}
_HOLDER: list[QObject] = []           # keeps QSoundEffects parented while alive
_ENABLED = True
_VOLUME = 0.45


def preload(parent: QObject | None = None) -> None:
    """Build WAVs on disk and prime QSoundEffect instances."""
    _build_presets()
    os.makedirs(_SFX_DIR, exist_ok=True)
    for name, frames in PRESETS.items():
        path = os.path.join(_SFX_DIR, f"{name}.wav")
        # Re-write each session — cheap (<5 KB total), avoids stale data
        # if the algorithm changes between releases.
        try:
            _save_wav(path, frames)
        except OSError:
            continue
        eff = QSoundEffect(parent)
        eff.setSource(QUrl.fromLocalFile(path))
        eff.setVolume(_VOLUME)
        eff.setLoopCount(1)
        _EFFECTS[name] = eff
    if parent is not None:
        _HOLDER.append(parent)


def play(name: str) -> None:
    """Play a preset by name. Silently ignores unknown names or when
    sounds are globally disabled."""
    if not _ENABLED:
        return
    eff = _EFFECTS.get(name)
    if eff is None:
        return
    if eff.isPlaying():
        eff.stop()
    eff.play()


def set_volume(v: float) -> None:
    global _VOLUME
    _VOLUME = max(0.0, min(1.0, float(v)))
    for e in _EFFECTS.values():
        e.setVolume(_VOLUME)


def set_enabled(enabled: bool) -> None:
    global _ENABLED
    _ENABLED = bool(enabled)
