"""Cat actor — a second, simpler scene living alongside the person.

States:
    OFFSTAGE  — hidden, waiting for idle.
    WALKING   — walks across the lane at lazy pace, frame cycle from sheet.
    LYING     — pauses mid-walk, plays the lying frame, then resumes.
    FLEEING   — switches to the run animation, exits via nearest external edge.

Movement model: instead of a constant px/tick speed, walk and run advance
the body by **per-frame deltas** (`config.cat.walk_frame_deltas[i]` for
frame i). Each frame is held for FRAME_HOLD ticks; we apply the frame's
delta evenly across those ticks. This locks body movement to animation
phase, which is the standard cure for "ice-skating" feet.
"""
from __future__ import annotations

import os
import random
from enum import Enum, auto

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from character import SpriteWidget
from config import Config
from idle_detector import seconds_since_last_input
from scene import (
    EXIT_BUFFER_PX, TICK_MS, Lane, discover_lanes,
)
from spritesheet import SpriteSheet

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

LIE_FRAME_HOLD = 8          # ticks per lying frame (slow breathing) — fixed
LIE_CHANCE_PER_TICK = 0.003 # ~once every ~30 sec while walking
LIE_DURATION_TICKS = 80     # ~5 s
SIT_FRAME_HOLD = 8          # ticks per sit frame
SIT_CHANCE_PER_TICK = 0.003 # same odds as lie; cat picks one or the other
SIT_DURATION_TICKS = 100    # ~6 s
ACTOR_NAME = "cat"

# walk_frame_hold / run_frame_hold are config-driven (CatCfg), so the user
# can tune animation pace and movement multipliers from the Config dialog.


class State(Enum):
    OFFSTAGE = auto()
    WALKING = auto()
    LYING = auto()
    SITTING = auto()
    FLEEING = auto()


class CatScene(QObject):
    state_changed = pyqtSignal(str)

    def __init__(self, config: Config | None = None) -> None:
        super().__init__()
        self.config = config or Config()
        self._all_lanes = discover_lanes()
        self.lanes = self._select_active_lanes()
        self.lane: Lane = self.lanes[0]

        self._sheet = SpriteSheet.load(os.path.join(ASSETS_DIR, "cat", "cat"))
        self._rebuild_frames(self.config.cat.scale)
        self.cat = SpriteWidget(self._stand_right[0])

        self.state = State.OFFSTAGE
        self.x = 0.0                 # float for fractional per-tick movement
        self.target_x = 0
        self.facing_left = False
        self.frame_idx = 0           # tick counter for animation phase
        self.lie_ticks_left = 0
        self.sit_ticks_left = 0
        self.exit_side: str = "right"
        # Debug stepping: when > 0, the scene un-pauses for this many ticks
        # of normal-rate playback, then re-freezes. Lets you watch a single
        # animation frame transition happen in real time.
        self._step_ticks_remaining = 0

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(TICK_MS)

    # ---- lane helpers (mirrors scene.py logic) -------------------------

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

    # ---- live scale -----------------------------------------------------

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
        # Sit is south-facing (front projection) — single direction, no mirror.
        self._sit = s.animation("sit", scale=scale)

    def reload_sprite(self) -> None:
        """Re-read assets/cat/cat.{png,json} from disk and rebuild frame caches.
        Called by the tray Reload action when the user has been hand-editing
        the PNG and wants to see the result without restarting the app."""
        self._sheet = SpriteSheet.load(os.path.join(ASSETS_DIR, "cat", "cat"))
        # set_scale rebuilds the per-direction frame caches AND refreshes the
        # currently-displayed pixmap to match the new sheet at our scale.
        self.set_scale(self.config.cat.scale)

    def set_scale(self, scale: float) -> None:
        self._rebuild_frames(scale)
        if self.state == State.WALKING:
            frames = self._walk_left if self.facing_left else self._walk_right
            hold = self._walk_hold()
        elif self.state == State.LYING:
            frames = self._lie_left if self.facing_left else self._lie_right
            hold = LIE_FRAME_HOLD
        elif self.state == State.SITTING:
            frames = self._sit
            hold = SIT_FRAME_HOLD
        elif self.state == State.FLEEING:
            frames = self._run_left if self.facing_left else self._run_right
            hold = self._run_hold()
        else:
            frames = self._stand_right
            hold = 1
        idx = (self.frame_idx // hold) % len(frames)
        self.cat.set_pixmap(frames[idx])
        if self.state != State.OFFSTAGE:
            self.cat.move_to(int(self.x), self._ground_y())

    # ---- config-driven anim pace ---------------------------------------

    def _walk_hold(self) -> int:
        return max(1, int(self.config.cat.walk_frame_hold))

    def _run_hold(self) -> int:
        return max(1, int(self.config.cat.run_frame_hold))

    # ---- helpers --------------------------------------------------------

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

    def _set_state(self, s: State) -> None:
        self.state = s
        self.state_changed.emit(s.name)

    def _spawn_x(self, side: str) -> int:
        if side == "left":
            return self.lane.full.left() - self.cat.width() - EXIT_BUFFER_PX
        return self.lane.full.right() + EXIT_BUFFER_PX

    def _exit_target_x(self, side: str) -> int:
        # Overshoot ≈ one run-cycle's distance after applying the stride
        # multiplier, so the target is always reachable.
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
        """Cat can lie / sit only when fully on-screen with margin, otherwise
        it ends up half-cropped at the edge."""
        margin = self.cat.width() // 3
        return (
            self.x >= self.lane.full.left() + margin
            and self.x + self.cat.width() <= self.lane.full.right() - margin
        )

    # ---- per-frame delta machinery -------------------------------------

    def _delta_for_tick(self, deltas: list[float], hold: int) -> float:
        """Return how many px the body should move on THIS tick.

        Each animation frame is held for `hold` ticks; the frame's delta is
        spread across those ticks evenly. Direction (sign) is applied by
        caller via `facing_left`."""
        if not deltas:
            return 0.0
        n_frames = len(deltas)
        frame_i = (self.frame_idx // hold) % n_frames
        return deltas[frame_i] / hold

    # ---- main tick -----------------------------------------------------

    def _tick(self) -> None:
        # Frozen: skip unless we still have step-ticks queued (Step button).
        if self.config.behaviour.debug_paused and self._step_ticks_remaining <= 0:
            return
        if self._step_ticks_remaining > 0:
            self._step_ticks_remaining -= 1
        self._tick_inner()

    def step_one_frame(self) -> None:
        """Queue exactly ONE animation frame worth of ticks. The QTimer plays
        them at normal rate so you SEE the transition happen, then auto-freezes
        again."""
        holds = {
            State.WALKING: self._walk_hold(),
            State.FLEEING: self._run_hold(),
            State.LYING: LIE_FRAME_HOLD,
            State.SITTING: SIT_FRAME_HOLD,
        }
        self._step_ticks_remaining = holds.get(self.state, 1)

    def _tick_inner(self) -> None:
        if not self.config.actor_enabled(ACTOR_NAME):
            if self.state != State.OFFSTAGE:
                self._exit_now()
            return

        if self.state not in (State.OFFSTAGE, State.FLEEING) and self._user_active():
            self._begin_flee()
            return

        if self.state == State.OFFSTAGE:
            if self._user_idle_long_enough():
                self._enter()
        elif self.state == State.WALKING:
            self._tick_walk()
        elif self.state == State.LYING:
            self._tick_lie()
        elif self.state == State.SITTING:
            self._tick_sit()
        elif self.state == State.FLEEING:
            self._tick_flee()

    # ---- transitions ---------------------------------------------------

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
        frames = self._walk_left if self.facing_left else self._walk_right
        self.cat.set_pixmap(frames[0])
        self.cat.move_to(int(self.x), self._ground_y())
        self.cat.show()
        self._set_state(State.WALKING)

    def _tick_walk(self) -> None:
        if self._has_clearance_to_rest():
            r = random.random()
            if r < LIE_CHANCE_PER_TICK:
                self._begin_lie()
                return
            if r < LIE_CHANCE_PER_TICK + SIT_CHANCE_PER_TICK:
                self._begin_sit()
                return

        hold = self._walk_hold()
        mult = self.config.cat.walk_stride_multiplier
        delta = self._delta_for_tick(self.config.cat.walk_frame_deltas, hold) * mult
        self.x += -delta if self.facing_left else delta
        self._draw_walk()
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
        self.cat.move_to(int(self.x), self._ground_y())

    def _begin_lie(self) -> None:
        self.lie_ticks_left = LIE_DURATION_TICKS
        self.frame_idx = 0
        frames = self._lie_left if self.facing_left else self._lie_right
        self.cat.set_pixmap(frames[0])
        self.cat.move_to(int(self.x), self._ground_y())
        self._set_state(State.LYING)

    def _tick_lie(self) -> None:
        self.lie_ticks_left -= 1
        frames = self._lie_left if self.facing_left else self._lie_right
        self.frame_idx = (self.frame_idx + 1) % (len(frames) * LIE_FRAME_HOLD)
        idx = (self.frame_idx // LIE_FRAME_HOLD) % len(frames)
        self.cat.set_pixmap(frames[idx])
        self.cat.move_to(int(self.x), self._ground_y())
        if self.lie_ticks_left <= 0:
            self._set_state(State.WALKING)

    def _begin_sit(self) -> None:
        """Cat plops down facing the camera (south). Doesn't move."""
        self.sit_ticks_left = SIT_DURATION_TICKS
        self.frame_idx = 0
        self.cat.set_pixmap(self._sit[0])
        self.cat.move_to(int(self.x), self._ground_y())
        self._set_state(State.SITTING)

    def _tick_sit(self) -> None:
        self.sit_ticks_left -= 1
        frames = self._sit
        self.frame_idx = (self.frame_idx + 1) % (len(frames) * SIT_FRAME_HOLD)
        idx = (self.frame_idx // SIT_FRAME_HOLD) % len(frames)
        self.cat.set_pixmap(frames[idx])
        self.cat.move_to(int(self.x), self._ground_y())
        if self.sit_ticks_left <= 0:
            self._set_state(State.WALKING)

    def _begin_flee(self) -> None:
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

    def _exit_now(self) -> None:
        self.cat.hide()
        self._set_state(State.OFFSTAGE)
