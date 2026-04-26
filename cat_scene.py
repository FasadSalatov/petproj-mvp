"""Cat actor — second scene, with proper occlusion-aware window hopping.

States
------
    OFFSTAGE   — hidden, waiting for idle.
    WALKING    — walks left/right on its current surface (lane ground or a
                 window-top segment). May randomly choose to lie/sit/jump.
    LYING      — pauses mid-walk, plays the lying frame.
    SITTING    — south-facing rest.
    PREP_JUMP  — wind-up frames before a jump leaves the surface.
    JUMPING    — parabolic arc between two surfaces, or downward fall when
                 the cat walks off a ledge / its window gets closed / a
                 front window covers its current segment.
    LANDING    — short squash after touch-down, then back to WALKING.
    FLEEING    — runs to the nearest external edge when the user touches
                 input; mid-air jumps abort and the cat snaps to the floor.

Surfaces
--------
The cat's current surface is either:
    * `self._current_seg = None`  → standing on lane ground (taskbar top).
    * a `WalkSegment`             → a non-occluded slice of some window's
                                    top edge.

`window_platforms.compute_segments()` already removes the parts of every
window's top edge that are covered by front windows, so the cat can only
stand where it would actually be visible.

Multi-monitor
-------------
With `multi_monitor=False`, the cat is restricted to one lane (the
selected primary screen). When that lane has a neighbour on the side
the cat is heading toward, we never let the cat exit through the
seam — it does a round trip instead.
"""
from __future__ import annotations

import os
import random
from dataclasses import dataclass
from enum import Enum, auto

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

import ctypes
import datetime as _dt

from PyQt6.QtGui import QCursor

from bubble import SpeechBubble, line_for
from character import SpriteWidget
from config import Config
from effects import EffectsLayer
from idle_detector import seconds_since_last_input
from scene import EXIT_BUFFER_PX, TICK_MS, Lane, discover_lanes
from spritesheet import SpriteSheet
from window_platforms import (
    WalkSegment, collect_windows, compute_segments, find_segment_under,
)

try:
    import psutil  # type: ignore
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False

# GetAsyncKeyState — used to detect click-on-cat without intercepting input.
_VK_LBUTTON = 0x01
try:
    _GetAsyncKeyState = ctypes.windll.user32.GetAsyncKeyState
    _GetAsyncKeyState.argtypes = [ctypes.c_int]
    _GetAsyncKeyState.restype = ctypes.c_short
except (AttributeError, OSError):
    _GetAsyncKeyState = None

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

LIE_FRAME_HOLD = 8
LIE_CHANCE_PER_TICK = 0.003
LIE_DURATION_TICKS = 80
SIT_FRAME_HOLD = 8
SIT_CHANCE_PER_TICK = 0.003
SIT_DURATION_TICKS = 100

JUMP_FRAME_HOLD = 4
JUMP_PREP_TICKS = 6
JUMP_LAND_TICKS = 4
JUMP_HORIZ_SPEED_PX = 9      # px per tick of horizontal travel during flight
JUMP_PEAK_BASE_PX = 60       # minimum apex above the higher endpoint
JUMP_PEAK_PER_DX = 0.18      # extra apex per pixel of horizontal travel
JUMP_MIN_DX = 40
JUMP_MAX_DX = 700
JUMP_MAX_UP = 1400           # cat can leap from taskbar all the way up
JUMP_MAX_DOWN = 1400
JUMP_CHANCE_PER_TICK = 0.025  # ~1 jump-attempt every ~3 sec while walking
FALL_HOP_PEAK_PX = 8          # tiny upward arc when stepping off a ledge

WINDOW_REFRESH_TICKS = 4     # ~240ms re-poll of EnumWindows

# Hunger model: 0 = stuffed, 100 = starving. Increments per tick; at full
# starvation the cat lies twice as much and walks at 60% pace. "Feed cat"
# resets to 0 with a happy bubble.
HUNGER_PER_TICK = 0.05       # ~83 sec from 0 to 100
HUNGER_FULL = 100.0
HUNGER_TIRED = 60.0           # threshold where pacing/posture starts to change

# Cursor curiosity: ~30% of the time the cat picks a wander target near
# the current mouse cursor instead of the random interior choice — looks
# like the cat is checking on the user.
CURSOR_CURIOSITY = 0.30

# Treat hunt: the cat heads to a dropped treat, eats it, hunger resets.
TREAT_REACH_PX = 12          # close-enough distance to start eating
TREAT_EAT_TICKS = 90         # ~5.4s of eating animation
TREAT_TIMEOUT_TICKS = 600    # 36s — treat vanishes if cat can't reach it

# CPU awareness: high CPU = anxious, more pacing & jumps. Low CPU = relaxed.
CPU_POLL_TICKS = 30          # ~1.8s

# Pomodoro: cat acts excited during "work" sprints, lazy during "break".
POMODORO_TICK_PER_SECOND = 1000 // TICK_MS  # ticks in a second

ACTOR_NAME = "cat"


class State(Enum):
    OFFSTAGE = auto()
    WALKING = auto()
    LYING = auto()
    SITTING = auto()
    FLEEING = auto()
    PREP_JUMP = auto()
    JUMPING = auto()
    LANDING = auto()
    TREAT_HUNT = auto()      # walking straight to a dropped treat
    EATING = auto()          # consuming the treat (sit pose for ~5s)


@dataclass
class _JumpPlan:
    src_x: float
    src_y: int
    dst_x: float
    dst_y: int
    target: WalkSegment | None    # None = landing on lane ground
    total_ticks: int
    peak_dy: int                  # offset from midpoint y; negative = upward
    facing_left: bool


class CatScene(QObject):
    state_changed = pyqtSignal(str)

    def __init__(self, config: Config | None = None) -> None:
        super().__init__()
        self.config = config or Config()
        self._all_lanes = discover_lanes()
        self.lanes = self._select_active_lanes()
        self.lane: Lane = self.lanes[0]

        self._sheet = SpriteSheet.load(os.path.join(ASSETS_DIR, "cat", "cat"))
        self._has_jump_anim = False
        self._rebuild_frames(self.config.cat.scale)
        self.cat = SpriteWidget(self._stand_right[0])
        self.bubble = SpeechBubble(self.cat)
        self.effects = EffectsLayer(self.cat)
        self.effects.configure_scale(max(1, int(round(self.config.cat.scale))))

        self.state = State.OFFSTAGE
        self.x = 0.0
        self.y_bottom = 0
        self.target_x = 0
        self.facing_left = False
        self.frame_idx = 0
        self.lie_ticks_left = 0
        self.sit_ticks_left = 0
        self.exit_side: str = "right"
        self._step_ticks_remaining = 0

        # Window-segment tracking.
        self._segments: list[WalkSegment] = []
        self._segments_tick = 0
        self._current_seg: WalkSegment | None = None

        # Jump bookkeeping.
        self._jump: _JumpPlan | None = None
        self._jump_tick = 0
        self._land_tick = 0
        self._prep_tick = 0

        # Hunger — drives day/night-style pacing tweaks; resettable from tray.
        self._hunger: float = 0.0
        # Treat hunt timing (drops are tray-triggered).
        self._treat_eat_left = 0
        self._treat_age_ticks = 0
        # Click-to-pet: rising-edge detection for LBUTTON.
        self._last_lbutton_pressed: bool = False
        self._petted_count: int = 0
        # CPU awareness state.
        self._cpu_pct: float = 25.0
        self._cpu_poll_tick: int = 0
        # Pomodoro state. _seconds_left counts down at 1Hz; on zero we swap
        # phase or stop. Phase "off" = no schedule.
        self._pomodoro_phase: str = "off"   # "off" | "work" | "break"
        self._pomodoro_seconds_left: int = 0
        self._pomodoro_work_min: int = 25
        self._pomodoro_break_min: int = 5
        self._pomodoro_subtick: int = 0

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(TICK_MS)

    # ---- lane selection ------------------------------------------------

    def _select_active_lanes(self) -> list[Lane]:
        if self.config.monitors.multi_monitor:
            return list(self._all_lanes)
        idx = self.config.monitors.primary_screen_index
        if idx < 0 or idx >= len(self._all_lanes):
            idx = 0
        return [self._all_lanes[idx]]

    def set_multi_monitor(self, enabled: bool) -> None:
        self._all_lanes = discover_lanes()
        self.lanes = self._select_active_lanes()
        if self.lane not in self.lanes:
            self.lane = self.lanes[0]

    # ---- sprite caches -------------------------------------------------

    def _rebuild_frames(self, scale: float) -> None:
        s = self._sheet
        self._walk_right = s.animation("walk", scale=scale)
        self._walk_left = s.animation("walk", scale=scale, mirror=True)
        self._run_right = s.animation("run", scale=scale)
        self._run_left = s.animation("run", scale=scale, mirror=True)
        self._stand_right = s.animation("stand", scale=scale)
        self._stand_left = s.animation("stand", scale=scale, mirror=True)
        self._lie_right = s.animation("lie", scale=scale)
        self._lie_left = s.animation("lie", scale=scale, mirror=True)
        self._sit = s.animation("sit", scale=scale)
        if "jump" in s.tags:
            self._jump_right = s.animation("jump", scale=scale)
            self._jump_left = s.animation("jump", scale=scale, mirror=True)
            self._has_jump_anim = True
        else:
            self._jump_right = self._run_right
            self._jump_left = self._run_left
            self._has_jump_anim = False

    def reload_sprite(self) -> None:
        self._sheet = SpriteSheet.load(os.path.join(ASSETS_DIR, "cat", "cat"))
        self.set_scale(self.config.cat.scale)

    def set_scale(self, scale: float) -> None:
        self._rebuild_frames(scale)
        self.effects.configure_scale(max(1, int(round(scale))))
        self._render_current()

    # ---- pace ----------------------------------------------------------

    def _walk_hold(self) -> int:
        return max(1, int(self.config.cat.walk_frame_hold))

    def _run_hold(self) -> int:
        return max(1, int(self.config.cat.run_frame_hold))

    # ---- helpers -------------------------------------------------------

    def _user_active(self) -> bool:
        if self.config.behaviour.debug_always_on:
            return False
        return seconds_since_last_input() < 1.5

    def _user_idle_long_enough(self) -> bool:
        if self.config.behaviour.debug_always_on:
            return True
        return seconds_since_last_input() >= self.config.behaviour.idle_threshold_s

    def _is_night(self) -> bool:
        """True when the cat should be in 'sleepy' mode based on local time."""
        if not self.config.cat.night_mode:
            return False
        h = _dt.datetime.now().hour
        a = self.config.cat.night_start_hour
        b = self.config.cat.night_end_hour
        if a <= b:
            return a <= h <= b
        # Wraps midnight: a > b → night = [a, 24) ∪ [0, b]
        return h >= a or h <= b

    def _hunger_factor(self) -> float:
        if self._hunger <= HUNGER_TIRED:
            return 0.0
        return min(1.0, (self._hunger - HUNGER_TIRED) / (HUNGER_FULL - HUNGER_TIRED))

    def _cpu_factor(self) -> float:
        """0..1 above 40% CPU; 0 below. Ramps to 1 at 100%."""
        return max(0.0, min(1.0, (self._cpu_pct - 40.0) / 60.0))

    def _walk_pace_factor(self) -> float:
        f = 1.0
        if self._is_night():
            f *= 0.55
        f *= 1.0 - 0.4 * self._hunger_factor()
        f *= 1.0 + 0.30 * self._cpu_factor()
        if self._pomodoro_phase == "break":
            f *= 0.6
        elif self._pomodoro_phase == "work":
            f *= 1.15
        return f

    def _lie_chance(self) -> float:
        f = 1.0
        if self._is_night():
            f *= 3.0
        f *= 1.0 + 1.5 * self._hunger_factor()
        if self._pomodoro_phase == "break":
            f *= 2.5
        elif self._pomodoro_phase == "work":
            f *= 0.4
        return LIE_CHANCE_PER_TICK * f

    def _sit_chance(self) -> float:
        f = 1.0
        if self._is_night():
            f *= 2.5
        f *= 1.0 + 1.0 * self._hunger_factor()
        if self._pomodoro_phase == "break":
            f *= 2.0
        return SIT_CHANCE_PER_TICK * f

    def _jump_chance(self) -> float:
        f = 1.0
        if self._is_night():
            f *= 0.4
        f *= 1.0 - 0.6 * self._hunger_factor()
        f *= 1.0 + 0.5 * self._cpu_factor()
        if self._pomodoro_phase == "break":
            f *= 0.4
        elif self._pomodoro_phase == "work":
            f *= 1.4
        return JUMP_CHANCE_PER_TICK * f

    def feed(self) -> None:
        self._hunger = 0.0
        if self.cat.isVisible():
            self._say("nya", duration_ms=1500)

    # ---- pomodoro -----------------------------------------------------

    def start_pomodoro(self, work_min: int = 25, break_min: int = 5) -> None:
        self._pomodoro_work_min = max(1, int(work_min))
        self._pomodoro_break_min = max(1, int(break_min))
        self._pomodoro_phase = "work"
        self._pomodoro_seconds_left = self._pomodoro_work_min * 60
        self._pomodoro_subtick = 0
        if self.cat.isVisible():
            self._say("hup", duration_ms=1500)

    def stop_pomodoro(self) -> None:
        self._pomodoro_phase = "off"
        self._pomodoro_seconds_left = 0

    def _tick_pomodoro(self) -> None:
        if self._pomodoro_phase == "off":
            return
        self._pomodoro_subtick += 1
        if self._pomodoro_subtick < POMODORO_TICK_PER_SECOND:
            return
        self._pomodoro_subtick = 0
        self._pomodoro_seconds_left -= 1
        if self._pomodoro_seconds_left > 0:
            return
        # Phase boundary — swap.
        if self._pomodoro_phase == "work":
            self._pomodoro_phase = "break"
            self._pomodoro_seconds_left = self._pomodoro_break_min * 60
            if self.cat.isVisible():
                self._say("zzz", duration_ms=1800)
        else:
            self._pomodoro_phase = "work"
            self._pomodoro_seconds_left = self._pomodoro_work_min * 60
            if self.cat.isVisible():
                self._say("hup", duration_ms=1500)

    def _ground_y(self) -> int:
        return self.lane.ground_y + self.config.cat.y_offset_px

    def _surface_y(self) -> int:
        if self._current_seg is None:
            return self._ground_y()
        return self._current_seg.y + self.config.cat.y_offset_px

    def _set_state(self, s: State) -> None:
        self.state = s
        self.state_changed.emit(s.name)

    def _spawn_x(self, side: str) -> int:
        if side == "left":
            return self.lane.full.left() - self.cat.width() - EXIT_BUFFER_PX
        return self.lane.full.right() + EXIT_BUFFER_PX

    def _exit_target_x(self, side: str) -> int:
        mult = self.config.cat.run_stride_multiplier
        cycle_total = sum(abs(d) for d in self.config.cat.run_frame_deltas) * mult or 50.0
        overshoot = max(int(cycle_total / 2), 24)
        if side == "left":
            return self.lane.full.left() - self.cat.width() - EXIT_BUFFER_PX - overshoot
        return self.lane.full.right() + EXIT_BUFFER_PX + overshoot

    def _is_offscreen_for_lane(self) -> bool:
        return (
            self.x + self.cat.width() < self.lane.full.left() - EXIT_BUFFER_PX
            or self.x > self.lane.full.right() + EXIT_BUFFER_PX
        )

    def _nearest_external_edge(self) -> str:
        edges = self.lane.external_edges
        if len(edges) == 1:
            return edges[0]
        cx = self.x + self.cat.width() // 2
        dist_left = cx - self.lane.full.left()
        dist_right = self.lane.full.right() - cx
        return "left" if dist_left < dist_right else "right"

    def _has_clearance_to_rest(self) -> bool:
        if self._current_seg is not None:
            margin = self.cat.width() // 4
            s = self._current_seg
            return self.x >= s.x1 + margin and self.x + self.cat.width() <= s.x2 - margin
        margin = self.cat.width() // 3
        return (
            self.x >= self.lane.full.left() + margin
            and self.x + self.cat.width() <= self.lane.full.right() - margin
        )

    def _pick_wander_target(self) -> int:
        """Pick a new interior x for the cat to head toward. Biased toward
        the opposite side of the lane from current x, with an occasional
        curiosity hop toward wherever the user's cursor is — looks like the
        cat dropping in to check on the human."""
        margin = self.cat.width()
        left = self.lane.full.left() + margin
        right = self.lane.full.right() - margin
        if right <= left:
            return int((self.lane.full.left() + self.lane.full.right()) / 2)
        if random.random() < CURSOR_CURIOSITY:
            cursor_x = QCursor.pos().x()
            if left <= cursor_x <= right:
                return cursor_x
        mid = (left + right) // 2
        if self.x < mid:
            return random.randint(mid, right)
        return random.randint(left, mid)

    # ---- segments polling ---------------------------------------------

    def _refresh_segments(self) -> None:
        cw = max(int(self.cat.width()), 1)
        bounds = (
            self.lane.full.left(), self.lane.full.top(),
            self.lane.full.right(), self.lane.full.bottom(),
        )
        windows = collect_windows(desktop_bounds=bounds)
        # A segment must be wide enough for the cat to fit comfortably.
        min_w = max(40, cw - 8)
        self._segments = compute_segments(
            windows, lane_bounds=bounds, min_segment_width=min_w,
        )
        if self._current_seg is None:
            return
        # Re-bind the current segment by hwnd + cat-center containment.
        cx = int(self.x + cw / 2)
        match = next(
            (s for s in self._segments
             if s.hwnd == self._current_seg.hwnd and s.contains_x(cx)),
            None,
        )
        self._current_seg = match  # may become None → cat will fall

    def _choose_jump_target(self) -> tuple[WalkSegment | None, float, int] | None:
        """Pick a reachable destination (segment or floor). Returns
        (target, dst_center_x, dst_y) or None."""
        cand: list[tuple[WalkSegment | None, float, int]] = []
        cur_y = self._surface_y()
        cw = self.cat.width()
        center_x = self.x + cw / 2

        # From a segment, hopping down to lane floor is always an option.
        if self._current_seg is not None:
            direction = -1 if self.facing_left else 1
            jump_dx = random.randint(JUMP_MIN_DX, JUMP_MAX_DX // 2)
            cand.append((None, center_x + direction * jump_dx, self._ground_y()))

        for s in self._segments:
            # Skip our own segment.
            if (self._current_seg is not None
                    and s.hwnd == self._current_seg.hwnd
                    and s.x1 == self._current_seg.x1
                    and s.x2 == self._current_seg.x2):
                continue
            # Horizontal reach test.
            if s.x2 < center_x - JUMP_MAX_DX or s.x1 > center_x + JUMP_MAX_DX:
                continue
            dst_y = s.y + self.config.cat.y_offset_px
            dy = dst_y - cur_y
            if dy < -JUMP_MAX_UP or dy > JUMP_MAX_DOWN:
                continue
            # Pick a landing centre near the closer edge of this segment.
            if s.x2 < center_x:
                landing_x = s.x2 - cw / 2 - 4
            elif s.x1 > center_x:
                landing_x = s.x1 + cw / 2 + 4
            else:
                landing_x = (s.x1 + s.x2) / 2
            # Body must fit on the segment.
            if landing_x - cw / 2 < s.x1 or landing_x + cw / 2 > s.x2:
                continue
            dx = abs(landing_x - center_x)
            if dx < JUMP_MIN_DX or dx > JUMP_MAX_DX:
                continue
            cand.append((s, landing_x, dst_y))

        if not cand:
            return None
        return random.choice(cand)

    # ---- per-frame delta machinery -------------------------------------

    def _delta_for_tick(self, deltas: list[float], hold: int) -> float:
        if not deltas:
            return 0.0
        n_frames = len(deltas)
        frame_i = (self.frame_idx // hold) % n_frames
        return deltas[frame_i] / hold

    # ---- main tick -----------------------------------------------------

    def _tick(self) -> None:
        if self.config.behaviour.debug_paused and self._step_ticks_remaining <= 0:
            return
        if self._step_ticks_remaining > 0:
            self._step_ticks_remaining -= 1
        self._tick_inner()

    def step_one_frame(self) -> None:
        holds = {
            State.WALKING: self._walk_hold(),
            State.FLEEING: self._run_hold(),
            State.LYING: LIE_FRAME_HOLD,
            State.SITTING: SIT_FRAME_HOLD,
            State.PREP_JUMP: JUMP_FRAME_HOLD,
            State.JUMPING: JUMP_FRAME_HOLD,
            State.LANDING: JUMP_FRAME_HOLD,
            State.TREAT_HUNT: self._walk_hold(),
            State.EATING: SIT_FRAME_HOLD,
        }
        self._step_ticks_remaining = holds.get(self.state, 1)

    def _tick_inner(self) -> None:
        if not self.config.actor_enabled(ACTOR_NAME):
            if self.state != State.OFFSTAGE:
                self._exit_now()
            return

        if self.state != State.OFFSTAGE:
            # Slow hunger drift. Capped so feed-then-idle still applies.
            if self._hunger < HUNGER_FULL:
                self._hunger = min(HUNGER_FULL, self._hunger + HUNGER_PER_TICK)
            # CPU poll once every CPU_POLL_TICKS (psutil uses cached delta
            # since last call, so passing interval=None is the cheap path).
            self._cpu_poll_tick += 1
            if self._cpu_poll_tick >= CPU_POLL_TICKS and _HAS_PSUTIL:
                self._cpu_poll_tick = 0
                try:
                    self._cpu_pct = float(psutil.cpu_percent(interval=None))
                except Exception:
                    pass
            self._tick_pomodoro()
            self._maybe_register_pet()
            self._segments_tick = (self._segments_tick + 1) % WINDOW_REFRESH_TICKS
            if self._segments_tick == 0:
                self._refresh_segments()
                # If the segment we were standing on disappeared between polls
                # (closed/moved/covered), drop the cat to whatever's below.
                if (self._current_seg is None and self.state == State.WALKING
                        and self.y_bottom != 0
                        and self.y_bottom != self._ground_y()):
                    self._begin_fall(self.x)
            # Keep the bubble glued to the cat while it's visible.
            if self.bubble.isVisible():
                self.bubble.update_position()
            # Drive sparkle physics + park the shadow on the current surface.
            self.effects.tick()
            in_air = self.state == State.JUMPING
            if in_air:
                # Project the shadow straight down onto whatever surface
                # the cat will land on (the planned target, or the ground).
                if self._jump is not None and self._jump.target is not None:
                    surface_y = self._jump.target.y + self.config.cat.y_offset_px
                elif self._jump is not None:
                    surface_y = self._jump.dst_y
                else:
                    surface_y = self._ground_y()
                height = max(0, surface_y - self.y_bottom)
            else:
                surface_y = self._surface_y()
                height = 0
            self.effects.update_shadow(surface_y, height_above_surface=height)

        if self.state not in (State.OFFSTAGE, State.FLEEING) and self._user_active():
            self._begin_flee()
            return

        st = self.state
        if st == State.OFFSTAGE:
            if self._user_idle_long_enough():
                self._enter()
        elif st == State.WALKING:
            self._tick_walk()
        elif st == State.LYING:
            self._tick_lie()
        elif st == State.SITTING:
            self._tick_sit()
        elif st == State.PREP_JUMP:
            self._tick_prep()
        elif st == State.JUMPING:
            self._tick_jump()
        elif st == State.LANDING:
            self._tick_land()
        elif st == State.FLEEING:
            self._tick_flee()
        elif st == State.TREAT_HUNT:
            self._tick_treat_hunt()
        elif st == State.EATING:
            self._tick_eat()

    # ---- enter / exit --------------------------------------------------

    def _enter(self) -> None:
        self.lane = random.choice(self.lanes)
        edges = self.lane.external_edges
        spawn_side = random.choice(edges)
        self.facing_left = spawn_side == "right"
        self.x = float(self._spawn_x(spawn_side))
        # First wander target is somewhere in the interior — the cat walks in,
        # then keeps wandering until the user becomes active (FLEEING) or the
        # actor is disabled. No more "walk-across-then-respawn" loop.
        self.target_x = self._pick_wander_target()
        self.frame_idx = 0
        self._current_seg = None
        self.y_bottom = self._ground_y()
        self._refresh_segments()
        frames = self._walk_left if self.facing_left else self._walk_right
        self.cat.set_pixmap(frames[0])
        self.cat.move_to(int(self.x), self._surface_y())
        self.cat.show()
        self._set_state(State.WALKING)
        # Greet the world ~half the time on entry.
        if random.random() < 0.5:
            self._say(line_for("enter"))

    def _exit_now(self) -> None:
        self.cat.hide()
        self.bubble.hide()
        self.effects.hide_all()
        self._jump = None
        self._current_seg = None
        self._set_state(State.OFFSTAGE)

    # ---- click-to-pet -------------------------------------------------

    def _maybe_register_pet(self) -> None:
        """LBUTTON edge detection: when the user left-clicks while the
        cursor is over the cat, count it as a pet. The cat is click-through
        so we don't actually swallow the click — we just observe it."""
        if _GetAsyncKeyState is None or not self.cat.isVisible():
            return
        # GetAsyncKeyState returns a SHORT; high bit set when key pressed.
        pressed = bool(_GetAsyncKeyState(_VK_LBUTTON) & 0x8000)
        if pressed and not self._last_lbutton_pressed:
            cursor = QCursor.pos()
            cx = self.cat.x()
            cy = self.cat.y()
            if (cx <= cursor.x() <= cx + self.cat.width()
                    and cy <= cursor.y() <= cy + self.cat.height()):
                self._on_petted()
        self._last_lbutton_pressed = pressed

    def _on_petted(self) -> None:
        self._petted_count += 1
        # Tiny food bonus + happy reaction. Doesn't change state — the cat
        # keeps doing whatever it was doing, just looks happier.
        self._hunger = max(0.0, self._hunger - 6.0)
        self._say(line_for("happy"), duration_ms=1200)
        scale = max(2, int(round(self.config.cat.scale)))
        cx = self.cat.x() + self.cat.width() // 2
        cy = self.cat.y() + self.cat.height() // 3
        self.effects.burst_sparkles(cx, cy, scale=scale, n=8)

    def summon_to(self, x: int, y: int) -> None:
        """Tray-driven teleport. Snap the cat to (x, y) on the lane ground
        (we don't yet teleport onto a window). Shows a happy bubble."""
        self.x = float(x - self.cat.width() // 2)
        self._current_seg = None
        self.y_bottom = self._ground_y()
        # Pick a fresh wander target so the cat starts walking right after.
        self.target_x = self._pick_wander_target()
        self.facing_left = self.target_x < self.x
        self.frame_idx = 0
        self._jump = None
        self._refresh_segments()
        self.cat.move_to(int(self.x), self._surface_y())
        if not self.cat.isVisible():
            self.cat.show()
        self._set_state(State.WALKING)
        self._say(line_for("happy"))

    # ---- treat hunt ---------------------------------------------------

    def drop_treat_at(self, world_x: int) -> None:
        """Drop a treat near `world_x` (tray-driven). Cat snaps to ground
        and starts walking straight to it. Out-of-lane drops are ignored."""
        if not self.cat.isVisible():
            return
        l = self.lane.full.left() + self.cat.width() // 2 + 8
        r = self.lane.full.right() - self.cat.width() // 2 - 8
        treat_x = max(l, min(r, int(world_x)))
        scale = max(2, int(round(self.config.cat.scale)))
        self.effects.drop_treat(treat_x, self._ground_y(), scale)
        self._current_seg = None
        self.y_bottom = self._ground_y()
        self._jump = None
        self.target_x = treat_x - self.cat.width() // 2
        self.facing_left = self.target_x < self.x
        self.frame_idx = 0
        self._treat_age_ticks = 0
        self._set_state(State.TREAT_HUNT)
        self._say("?", duration_ms=1200)

    def _tick_treat_hunt(self) -> None:
        # Treat lifetime — cat couldn't reach it in time, give up.
        self._treat_age_ticks += 1
        if self._treat_age_ticks > TREAT_TIMEOUT_TICKS:
            self.effects.clear_treat()
            self._set_state(State.WALKING)
            return
        if not self.effects.has_treat():
            # Something else cleared the treat (flee, scene reset).
            self._set_state(State.WALKING)
            return

        hold = self._walk_hold()
        mult = self.config.cat.walk_stride_multiplier * self._walk_pace_factor()
        delta = self._delta_for_tick(self.config.cat.walk_frame_deltas, hold) * mult
        self.x += -delta if self.facing_left else delta
        self._draw_walk()

        # Reach check: cat-center within a small radius of treat position.
        treat_pos = self.effects.treat_pos
        if treat_pos is None:
            return
        cat_cx = self.x + self.cat.width() / 2
        if abs(cat_cx - treat_pos[0]) <= TREAT_REACH_PX:
            self._begin_eat()

    def _begin_eat(self) -> None:
        self.frame_idx = 0
        self._treat_eat_left = TREAT_EAT_TICKS
        self.cat.set_pixmap(self._sit[0])
        self.cat.move_to(int(self.x), self._surface_y())
        self._set_state(State.EATING)
        self._say("yum.", duration_ms=1500)

    def _tick_eat(self) -> None:
        self._treat_eat_left -= 1
        frames = self._sit
        self.frame_idx = (self.frame_idx + 1) % (len(frames) * SIT_FRAME_HOLD)
        idx = (self.frame_idx // SIT_FRAME_HOLD) % len(frames)
        self.cat.set_pixmap(frames[idx])
        self.cat.move_to(int(self.x), self._surface_y())
        if self._treat_eat_left <= 0:
            self._hunger = 0.0
            self.effects.clear_treat()
            self._say(line_for("happy"), duration_ms=1300)
            self._set_state(State.WALKING)

    # ---- speech bubble convenience ------------------------------------

    def _say(self, text: str, *, duration_ms: int = 1800) -> None:
        scale = max(2, int(round(self.config.cat.scale)))
        self.bubble.say(text, scale=scale, duration_ms=duration_ms)

    # ---- WALKING -------------------------------------------------------

    def _tick_walk(self) -> None:
        if self._current_seg is None and self.y_bottom != self._ground_y():
            self.y_bottom = self._ground_y()

        if self._has_clearance_to_rest():
            r = random.random()
            lie_p = self._lie_chance()
            sit_p = self._sit_chance()
            if r < lie_p:
                self._begin_lie()
                return
            if r < lie_p + sit_p:
                self._begin_sit()
                return

        if random.random() < self._jump_chance():
            choice = self._choose_jump_target()
            if choice is not None:
                self._begin_prep(choice)
                return

        hold = self._walk_hold()
        mult = self.config.cat.walk_stride_multiplier * self._walk_pace_factor()
        delta = self._delta_for_tick(self.config.cat.walk_frame_deltas, hold) * mult
        new_x = self.x + (-delta if self.facing_left else delta)

        # Stepping off the current segment's edge → fall.
        if self._current_seg is not None:
            s = self._current_seg
            cx_new = new_x + self.cat.width() / 2
            if cx_new < s.x1 or cx_new > s.x2:
                self._begin_fall(new_x)
                return

        self.x = new_x
        self._draw_walk()

        if self._current_seg is not None:
            return  # platform-bound walking ignores wander target

        # On ground: when we reach the wander target, pick a new one and
        # flip facing if needed. The cat keeps wandering until the user
        # becomes active (which routes through _begin_flee) or is disabled.
        reached = (
            (not self.facing_left and self.x >= self.target_x)
            or (self.facing_left and self.x <= self.target_x)
        )
        if not reached:
            return
        self.target_x = self._pick_wander_target()
        self.facing_left = self.target_x < self.x
        self.frame_idx = 0

    def _draw_walk(self) -> None:
        frames = self._walk_left if self.facing_left else self._walk_right
        hold = self._walk_hold()
        self.frame_idx = (self.frame_idx + 1) % (len(frames) * hold)
        idx = (self.frame_idx // hold) % len(frames)
        self.cat.set_pixmap(frames[idx])
        self.cat.move_to(int(self.x), self._surface_y())

    # ---- LIE / SIT -----------------------------------------------------

    def _begin_lie(self) -> None:
        self.lie_ticks_left = LIE_DURATION_TICKS
        self.frame_idx = 0
        frames = self._lie_left if self.facing_left else self._lie_right
        self.cat.set_pixmap(frames[0])
        self.cat.move_to(int(self.x), self._surface_y())
        self._set_state(State.LYING)
        if random.random() < 0.6:
            self._say(line_for("lie"), duration_ms=2400)

    def _tick_lie(self) -> None:
        self.lie_ticks_left -= 1
        frames = self._lie_left if self.facing_left else self._lie_right
        self.frame_idx = (self.frame_idx + 1) % (len(frames) * LIE_FRAME_HOLD)
        idx = (self.frame_idx // LIE_FRAME_HOLD) % len(frames)
        self.cat.set_pixmap(frames[idx])
        self.cat.move_to(int(self.x), self._surface_y())
        if self.lie_ticks_left <= 0:
            self._set_state(State.WALKING)

    def _begin_sit(self) -> None:
        self.sit_ticks_left = SIT_DURATION_TICKS
        self.frame_idx = 0
        self.cat.set_pixmap(self._sit[0])
        self.cat.move_to(int(self.x), self._surface_y())
        self._set_state(State.SITTING)
        if random.random() < 0.5:
            self._say(line_for("sit"))

    def _tick_sit(self) -> None:
        self.sit_ticks_left -= 1
        frames = self._sit
        self.frame_idx = (self.frame_idx + 1) % (len(frames) * SIT_FRAME_HOLD)
        idx = (self.frame_idx // SIT_FRAME_HOLD) % len(frames)
        self.cat.set_pixmap(frames[idx])
        self.cat.move_to(int(self.x), self._surface_y())
        if self.sit_ticks_left <= 0:
            self._set_state(State.WALKING)

    # ---- JUMP ----------------------------------------------------------

    def _begin_prep(self, choice: tuple[WalkSegment | None, float, int]) -> None:
        target, dst_center_x, dst_y = choice
        cw = self.cat.width()
        cx = self.x + cw / 2
        self.facing_left = dst_center_x < cx

        src_x = float(self.x)
        src_y = self._surface_y()
        dst_x = dst_center_x - cw / 2
        dx = abs(dst_center_x - cx)
        ticks = max(8, int(dx / JUMP_HORIZ_SPEED_PX))

        peak_above = JUMP_PEAK_BASE_PX + int(JUMP_PEAK_PER_DX * dx)
        peak_dy = -peak_above - int(abs(src_y - dst_y) / 2)

        self._jump = _JumpPlan(
            src_x=src_x, src_y=src_y,
            dst_x=dst_x, dst_y=dst_y,
            target=target,
            total_ticks=ticks,
            peak_dy=peak_dy,
            facing_left=self.facing_left,
        )
        self._prep_tick = 0
        self.frame_idx = 0
        self._draw_jump_static()
        self._set_state(State.PREP_JUMP)
        if random.random() < 0.25:
            self._say(line_for("prep"), duration_ms=1200)

    def _tick_prep(self) -> None:
        self._prep_tick += 1
        frames = self._jump_left if self.facing_left else self._jump_right
        self.frame_idx = (self.frame_idx + 1) % max(1, len(frames) * JUMP_FRAME_HOLD)
        idx = (self.frame_idx // JUMP_FRAME_HOLD) % max(1, len(frames))
        self.cat.set_pixmap(frames[idx])
        self.cat.move_to(int(self.x), self._surface_y())
        if self._prep_tick >= JUMP_PREP_TICKS:
            self._jump_tick = 0
            self._set_state(State.JUMPING)

    def _tick_jump(self) -> None:
        if self._jump is None:
            self._set_state(State.WALKING)
            return
        self._jump_tick += 1
        t = min(1.0, self._jump_tick / max(1, self._jump.total_ticks))

        self.x = self._jump.src_x + (self._jump.dst_x - self._jump.src_x) * t
        # Parabola: y(t) = (1-t)*src + t*dst + 4*peak_dy*t*(1-t).
        # peak_dy is negative => apex above midpoint.
        y = ((1 - t) * self._jump.src_y
             + t * self._jump.dst_y
             + 4 * self._jump.peak_dy * t * (1 - t))
        self.y_bottom = int(y)

        frames = self._jump_left if self._jump.facing_left else self._jump_right
        self.frame_idx = (self.frame_idx + 1) % max(1, len(frames) * JUMP_FRAME_HOLD)
        idx = (self.frame_idx // JUMP_FRAME_HOLD) % max(1, len(frames))
        self.cat.set_pixmap(frames[idx])
        self.cat.move_to(int(self.x), self.y_bottom)

        if t >= 1.0:
            self._land_on_target()

    def _land_on_target(self) -> None:
        """Resolve the planned target against the current segments at landing.
        If the target window has closed/moved/got covered, fall to whatever's
        underneath instead of teleporting to a stale rect."""
        plan = self._jump
        self._jump = None
        cw = self.cat.width()
        cx = int(self.x + cw / 2)

        if plan is None or plan.target is None:
            # Target was the floor — done.
            self._current_seg = None
            self._begin_land()
            return

        still = next(
            (s for s in self._segments
             if s.hwnd == plan.target.hwnd and s.contains_x(cx)),
            None,
        )
        if still is None:
            # Planned segment vanished by landing time. Fall.
            self._begin_fall(self.x)
            return
        self._current_seg = still
        self._begin_land()

    def _begin_fall(self, new_x: float) -> None:
        """Step-off-the-edge or rug-pulled fall: build a downward arc to
        whatever is below the new_x point."""
        cw = self.cat.width()
        cx = int(new_x + cw / 2)
        cur_y = self._surface_y()
        below = find_segment_under(
            cx, self._segments, cur_y + 4, min_width=max(40, cw - 8),
        )
        if below is not None:
            target = below
            dst_y = below.y + self.config.cat.y_offset_px
        else:
            target = None
            dst_y = self._ground_y()

        ticks = max(6, int(abs(dst_y - cur_y) / 8))
        self._jump = _JumpPlan(
            src_x=float(self.x), src_y=cur_y,
            dst_x=float(new_x), dst_y=dst_y,
            target=target,
            total_ticks=ticks,
            peak_dy=-FALL_HOP_PEAK_PX,
            facing_left=self.facing_left,
        )
        self._jump_tick = 0
        self.frame_idx = 0
        self._set_state(State.JUMPING)

    def _draw_jump_static(self) -> None:
        frames = self._jump_left if self.facing_left else self._jump_right
        if not frames:
            return
        self.cat.set_pixmap(frames[0])
        self.cat.move_to(int(self.x), self._surface_y())

    def _begin_land(self) -> None:
        self._land_tick = 0
        self.cat.move_to(int(self.x), self._surface_y())
        self._set_state(State.LANDING)
        # Sparkle burst at the feet on every landing.
        landing_x = int(self.x + self.cat.width() / 2)
        landing_y = int(self._surface_y())
        self.effects.burst_sparkles(
            landing_x, landing_y,
            scale=max(2, int(round(self.config.cat.scale))),
        )
        if random.random() < 0.3:
            self._say(line_for("land"), duration_ms=900)

    def _tick_land(self) -> None:
        self._land_tick += 1
        frames = self._stand_left if self.facing_left else self._stand_right
        idx = self._land_tick % max(1, len(frames))
        self.cat.set_pixmap(frames[idx])
        self.cat.move_to(int(self.x), self._surface_y())
        if self._land_tick >= JUMP_LAND_TICKS:
            self._set_state(State.WALKING)

    # ---- FLEE ----------------------------------------------------------

    def _begin_flee(self) -> None:
        if self.state in (State.JUMPING, State.PREP_JUMP, State.LANDING):
            self._jump = None
            self._current_seg = None
            self.y_bottom = self._ground_y()
        else:
            self._current_seg = None
        # Treat is forfeited on flee — user's mouse moved.
        self.effects.clear_treat()
        self.exit_side = self._nearest_external_edge()
        self.facing_left = self.exit_side == "left"
        self.target_x = self._exit_target_x(self.exit_side)
        self.frame_idx = 0
        self.bubble.hide()
        self._set_state(State.FLEEING)

    def _tick_flee(self) -> None:
        hold = self._run_hold()
        mult = self.config.cat.run_stride_multiplier
        delta = self._delta_for_tick(self.config.cat.run_frame_deltas, hold) * mult
        self.x += -delta if self.facing_left else delta
        frames = self._run_left if self.facing_left else self._run_right
        self.frame_idx = (self.frame_idx + 1) % (len(frames) * hold)
        idx = (self.frame_idx // hold) % len(frames)
        self.cat.set_pixmap(frames[idx])
        self.cat.move_to(int(self.x), self._ground_y())
        if self._is_offscreen_for_lane():
            self._exit_now()

    # ---- re-render after scale change ---------------------------------

    def _render_current(self) -> None:
        st = self.state
        if st in (State.WALKING, State.TREAT_HUNT):
            frames = self._walk_left if self.facing_left else self._walk_right
            hold = self._walk_hold()
        elif st == State.LYING:
            frames = self._lie_left if self.facing_left else self._lie_right
            hold = LIE_FRAME_HOLD
        elif st in (State.SITTING, State.EATING):
            frames = self._sit
            hold = SIT_FRAME_HOLD
        elif st == State.FLEEING:
            frames = self._run_left if self.facing_left else self._run_right
            hold = self._run_hold()
        elif st in (State.PREP_JUMP, State.JUMPING, State.LANDING):
            frames = self._jump_left if self.facing_left else self._jump_right
            hold = JUMP_FRAME_HOLD
        else:
            frames = self._stand_right
            hold = 1
        if not frames:
            return
        idx = (self.frame_idx // max(1, hold)) % len(frames)
        self.cat.set_pixmap(frames[idx])
        if st != State.OFFSTAGE:
            y = self.y_bottom if st == State.JUMPING else self._surface_y()
            self.cat.move_to(int(self.x), y)
