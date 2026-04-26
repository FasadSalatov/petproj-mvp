"""Cat actor — second scene, now able to hop on top of windows.

States
------
    OFFSTAGE   — hidden, waiting for idle.
    WALKING    — walks left/right on its current surface (lane ground OR a
                 window-top platform). May randomly choose to lie/sit/jump.
    LYING      — pauses mid-walk, plays the lying frame.
    SITTING    — south-facing rest.
    PREP_JUMP  — wind-up frames before a jump leaves the surface.
    JUMPING    — parabolic arc between two surfaces (or the same surface, or
                 a downward fall when the cat walks off a ledge).
    LANDING    — short squash after touch-down, then back to WALKING.
    FLEEING    — runs to the nearest external edge when the user touches
                 the keyboard/mouse. Mid-air jumps abort and the cat falls
                 to the lane floor before sprinting.

Surfaces
--------
The cat's current surface is either:
    * `self._current_platform = None` → standing on lane ground (taskbar top).
    * a `Platform` from `window_platforms.collect_platforms()` → the top edge
      of a real visible window in absolute virtual-desktop coordinates.

Every `WINDOW_REFRESH_TICKS` ticks the platform list is re-polled. If the
cat's current platform's window has closed, the cat starts falling.

Per-frame movement deltas (config-driven) drive walk/run; jumps use a
linear-X / parabolic-Y interpolation over a pre-computed flight plan.
"""
from __future__ import annotations

import os
import random
from dataclasses import dataclass
from enum import Enum, auto

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from character import SpriteWidget
from config import Config
from idle_detector import seconds_since_last_input
from scene import EXIT_BUFFER_PX, TICK_MS, Lane, discover_lanes
from spritesheet import SpriteSheet
from window_platforms import Platform, collect_platforms, find_platform_under

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
JUMP_HORIZ_SPEED_PX = 9      # px per tick of horizontal travel (sets flight duration)
JUMP_PEAK_BASE_PX = 60       # minimum apex above the higher endpoint
JUMP_PEAK_PER_DX = 0.18      # extra apex per pixel of horizontal travel
JUMP_MIN_DX = 40
JUMP_MAX_DX = 700            # cat can leap roughly across a 1080p screen
JUMP_MAX_UP = 1400           # cat can leap from taskbar all the way to a top-of-screen window
JUMP_MAX_DOWN = 1400
JUMP_CHANCE_PER_TICK = 0.025  # ~1 jump-attempt every ~3 sec while walking
FALL_HOP_PEAK_PX = 8          # tiny upward arc when stepping off a ledge

WINDOW_REFRESH_TICKS = 6     # ~360ms re-poll of EnumWindows

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


@dataclass
class _JumpPlan:
    """One scheduled flight: linear-X, parabolic-Y over `total_ticks`."""
    src_x: float
    src_y: int
    dst_x: float
    dst_y: int
    target: Platform | None      # None = landing on lane ground
    total_ticks: int
    peak_dy: int                 # offset from midpoint y; negative = upward
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

        self.state = State.OFFSTAGE
        self.x = 0.0                 # float for fractional per-tick movement
        self.y_bottom = 0            # absolute screen y of cat's feet (used in JUMPING)
        self.target_x = 0
        self.facing_left = False
        self.frame_idx = 0
        self.lie_ticks_left = 0
        self.sit_ticks_left = 0
        self.exit_side: str = "right"
        self._step_ticks_remaining = 0

        # Window-platform tracking.
        self._platforms: list[Platform] = []
        self._platforms_tick = 0
        self._current_platform: Platform | None = None

        # Jump bookkeeping.
        self._jump: _JumpPlan | None = None
        self._jump_tick = 0
        self._land_tick = 0
        self._prep_tick = 0

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

        # Optional jump animation: if the sheet ships with a "jump" tag we
        # use it for prep/air/land; otherwise we fall back to the run cycle
        # (cat looks "in motion" mid-air, which is acceptable for v1).
        if "jump" in s.tags:
            self._jump_right = s.animation("jump", scale=scale)
            self._jump_left = s.animation("jump", scale=scale, mirror=True)
            self._has_jump_anim = True
        else:
            self._jump_right = self._run_right
            self._jump_left = self._run_left
            self._has_jump_anim = False

    def reload_sprite(self) -> None:
        """Re-read assets/cat/cat.{png,json} from disk and rebuild caches.
        Triggered from the tray Reload action."""
        self._sheet = SpriteSheet.load(os.path.join(ASSETS_DIR, "cat", "cat"))
        self.set_scale(self.config.cat.scale)

    def set_scale(self, scale: float) -> None:
        self._rebuild_frames(scale)
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

    def _ground_y(self) -> int:
        return self.lane.ground_y + self.config.cat.y_offset_px

    def _surface_y(self) -> int:
        """Screen y for the cat's BOTTOM on its current surface."""
        if self._current_platform is None:
            return self._ground_y()
        return self._current_platform.y + self.config.cat.y_offset_px

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
        """Cat lies/sits only when fully on its current surface with margin."""
        if self._current_platform is not None:
            margin = self.cat.width() // 4
            p = self._current_platform
            return self.x >= p.x1 + margin and self.x + self.cat.width() <= p.x2 - margin
        margin = self.cat.width() // 3
        return (
            self.x >= self.lane.full.left() + margin
            and self.x + self.cat.width() <= self.lane.full.right() - margin
        )

    # ---- platform polling ---------------------------------------------

    def _refresh_platforms(self) -> None:
        bounds = (
            self.lane.full.left(), self.lane.full.top(),
            self.lane.full.right(), self.lane.full.bottom(),
        )
        self._platforms = collect_platforms(desktop_bounds=bounds)
        # Re-bind current platform by hwnd in case its rect changed.
        if self._current_platform is not None:
            same = next(
                (p for p in self._platforms if p.hwnd == self._current_platform.hwnd),
                None,
            )
            self._current_platform = same  # may become None → cat falls

    def _platforms_in_lane(self) -> list[Platform]:
        l = self.lane.full.left()
        r = self.lane.full.right()
        t = self.lane.full.top()
        b = self.lane.full.bottom()
        return [p for p in self._platforms if p.x2 > l and p.x1 < r and t <= p.y <= b]

    def _choose_jump_target(self) -> tuple[Platform | None, float, int] | None:
        """Pick a reachable destination. Returns (platform, dst_x_center, dst_y)
        or None if nothing nearby."""
        cand: list[tuple[Platform | None, float, int]] = []
        cur_y = self._surface_y()
        center_x = self.x + self.cat.width() / 2

        # Hopping down to lane floor is always an option from a window.
        if self._current_platform is not None:
            direction = -1 if self.facing_left else 1
            jump_dx = random.randint(JUMP_MIN_DX, JUMP_MAX_DX // 2)
            cand.append((None, center_x + direction * jump_dx, self._ground_y()))

        for p in self._platforms_in_lane():
            if (self._current_platform is not None
                    and p.hwnd == self._current_platform.hwnd):
                continue
            # Horizontal reach test against the closest edge of p.
            if p.x2 < center_x - JUMP_MAX_DX or p.x1 > center_x + JUMP_MAX_DX:
                continue
            dst_y = p.y + self.config.cat.y_offset_px
            dy = dst_y - cur_y
            if dy < -JUMP_MAX_UP or dy > JUMP_MAX_DOWN:
                continue
            # Pick a landing spot near whichever edge is closer.
            if p.x2 < center_x:
                landing_x = p.x2 - self.cat.width() / 2 - 4
            elif p.x1 > center_x:
                landing_x = p.x1 + self.cat.width() / 2 + 4
            else:
                # Cat is horizontally over this platform — aim for its centre.
                landing_x = (p.x1 + p.x2) / 2
            dx = abs(landing_x - center_x)
            if dx < JUMP_MIN_DX or dx > JUMP_MAX_DX:
                continue
            cand.append((p, landing_x, dst_y))

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
        }
        self._step_ticks_remaining = holds.get(self.state, 1)

    def _tick_inner(self) -> None:
        if not self.config.actor_enabled(ACTOR_NAME):
            if self.state != State.OFFSTAGE:
                self._exit_now()
            return

        # Re-poll windows periodically (skipped while off-stage to save CPU).
        if self.state != State.OFFSTAGE:
            self._platforms_tick = (self._platforms_tick + 1) % WINDOW_REFRESH_TICKS
            if self._platforms_tick == 0:
                self._refresh_platforms()
                # Rug pulled: window closed under our feet.
                if (self._current_platform is None and self.state == State.WALKING
                        and self.y_bottom != self._ground_y()):
                    # Defensive: shouldn't normally hit, _refresh_platforms
                    # nulls _current_platform if the hwnd disappears.
                    pass

        # User came back → flee. Aborts mid-jump.
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

    # ---- transitions: enter / exit -------------------------------------

    def _enter(self) -> None:
        self.lane = random.choice(self.lanes)
        edges = self.lane.external_edges
        spawn_side = random.choice(edges)
        self.facing_left = spawn_side == "right"
        self.x = float(self._spawn_x(spawn_side))
        self.target_x = self._exit_target_x(
            "left" if spawn_side == "right" else "right"
        )
        self.frame_idx = 0
        self._current_platform = None
        self._refresh_platforms()
        frames = self._walk_left if self.facing_left else self._walk_right
        self.cat.set_pixmap(frames[0])
        self.cat.move_to(int(self.x), self._surface_y())
        self.cat.show()
        self._set_state(State.WALKING)

    def _exit_now(self) -> None:
        self.cat.hide()
        self._jump = None
        self._current_platform = None
        self._set_state(State.OFFSTAGE)

    # ---- WALKING -------------------------------------------------------

    def _tick_walk(self) -> None:
        # Platform vanished underneath us between polls → fall.
        if self._current_platform is None and self.y_bottom != self._ground_y():
            # Shouldn't normally happen; reset y for safety.
            self.y_bottom = self._ground_y()

        if self._has_clearance_to_rest():
            r = random.random()
            if r < LIE_CHANCE_PER_TICK:
                self._begin_lie()
                return
            if r < LIE_CHANCE_PER_TICK + SIT_CHANCE_PER_TICK:
                self._begin_sit()
                return

        if random.random() < JUMP_CHANCE_PER_TICK:
            choice = self._choose_jump_target()
            if choice is not None:
                self._begin_prep(choice)
                return

        hold = self._walk_hold()
        mult = self.config.cat.walk_stride_multiplier
        delta = self._delta_for_tick(self.config.cat.walk_frame_deltas, hold) * mult
        new_x = self.x + (-delta if self.facing_left else delta)

        # Stepping off the current platform's edge → fall.
        if self._current_platform is not None:
            p = self._current_platform
            cx_new = new_x + self.cat.width() / 2
            if cx_new < p.x1 or cx_new > p.x2:
                self._begin_fall(new_x)
                return

        self.x = new_x
        self._draw_walk()

        # Lane exit only applies when on ground.
        if self._current_platform is None:
            if (not self.facing_left and self.x >= self.target_x) or (
                self.facing_left and self.x <= self.target_x
            ):
                self._exit_now()

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

    def _begin_prep(self, choice: tuple[Platform | None, float, int]) -> None:
        target, dst_center_x, dst_y = choice
        cx = self.x + self.cat.width() / 2
        self.facing_left = dst_center_x < cx

        src_x = float(self.x)
        src_y = self._surface_y()
        dst_x = dst_center_x - self.cat.width() / 2
        dx = abs(dst_center_x - cx)
        ticks = max(8, int(dx / JUMP_HORIZ_SPEED_PX))

        # Apex must clear the higher of the two endpoints by JUMP_PEAK_BASE_PX,
        # plus a bit extra proportional to dx. Translate "screen y of apex"
        # into the parabola's `peak_dy` (= apex offset from midpoint y).
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
            self._current_platform = self._jump.target
            self._jump = None
            self._begin_land()

    def _begin_fall(self, new_x: float) -> None:
        """Step-off-the-edge transition. Build a downward arc to whatever
        platform (or the floor) is below the new_x point."""
        cx = new_x + self.cat.width() / 2
        cur_y = self._surface_y()
        below = find_platform_under(int(cx), self._platforms_in_lane(), cur_y + 4)
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

    def _tick_land(self) -> None:
        self._land_tick += 1
        # Quick "settle" using stand frames so the cat clearly stops moving.
        frames = self._stand_left if self.facing_left else self._stand_right
        idx = self._land_tick % max(1, len(frames))
        self.cat.set_pixmap(frames[idx])
        self.cat.move_to(int(self.x), self._surface_y())
        if self._land_tick >= JUMP_LAND_TICKS:
            self._set_state(State.WALKING)

    # ---- FLEE ----------------------------------------------------------

    def _begin_flee(self) -> None:
        # Mid-air? Snap to floor instantly — no pretty fall during a panic.
        if self.state in (State.JUMPING, State.PREP_JUMP, State.LANDING):
            self._jump = None
            self._current_platform = None
            self.y_bottom = self._ground_y()
        else:
            self._current_platform = None
        self.exit_side = self._nearest_external_edge()
        self.facing_left = self.exit_side == "left"
        self.target_x = self._exit_target_x(self.exit_side)
        self.frame_idx = 0
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
        if st == State.WALKING:
            frames = self._walk_left if self.facing_left else self._walk_right
            hold = self._walk_hold()
        elif st == State.LYING:
            frames = self._lie_left if self.facing_left else self._lie_right
            hold = LIE_FRAME_HOLD
        elif st == State.SITTING:
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
