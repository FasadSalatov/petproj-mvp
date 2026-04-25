"""Cat actor — a second, simpler scene living alongside the person.

States:
    OFFSTAGE  — hidden, waiting for idle.
    WALKING   — walks across the lane at lazy pace, frame cycle from sheet.
    LYING     — pauses mid-walk, plays the lying frame, then resumes.
    FLEEING   — switches to the run animation, exits via nearest external edge.

Reuses the lane discovery and external-edge logic from the person scene. Cat
is loaded from assets/cat/gptcat.{png,json} and rendered at CAT_SCALE (smaller
than its native AI-generated resolution so it sits sensibly next to the
18x28-source person).
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

CAT_SCALE = 1.0             # PixelLab cat is 68x68 native; 1.0 keeps it cat-sized vs person at SCALE=5
CAT_WALK_SPEED_PX = 2       # cats walk lazily
CAT_RUN_SPEED_PX = 8        # but bolt fast when fleeing
WALK_FRAME_HOLD = 6         # ticks per walk frame
RUN_FRAME_HOLD = 3          # ticks per run frame
LIE_FRAME_HOLD = 8          # ticks per lying frame (slow breathing)
LIE_CHANCE_PER_TICK = 0.003 # ~once every ~30 sec while walking
LIE_DURATION_TICKS = 80     # ~5 s
ACTOR_NAME = "cat"


class State(Enum):
    OFFSTAGE = auto()
    WALKING = auto()
    LYING = auto()
    FLEEING = auto()


class CatScene(QObject):
    state_changed = pyqtSignal(str)

    def __init__(self, config: Config | None = None) -> None:
        super().__init__()
        self.config = config or Config()
        self._all_lanes = discover_lanes()
        self.lanes = self._select_active_lanes()
        self.lane: Lane = self.lanes[0]

        sheet = SpriteSheet.load(os.path.join(ASSETS_DIR, "cat", "cat"))
        self._walk_right = sheet.animation("walk", scale=CAT_SCALE)
        self._walk_left = sheet.animation("walk", scale=CAT_SCALE, mirror=True)
        self._run_right = sheet.animation("run", scale=CAT_SCALE)
        self._run_left = sheet.animation("run", scale=CAT_SCALE, mirror=True)
        self._stand_right = sheet.animation("stand", scale=CAT_SCALE)
        self._stand_left = sheet.animation("stand", scale=CAT_SCALE, mirror=True)
        self._lie_right = sheet.animation("lie", scale=CAT_SCALE)
        self._lie_left = sheet.animation("lie", scale=CAT_SCALE, mirror=True)

        self.cat = SpriteWidget(self._stand_right[0])

        self.state = State.OFFSTAGE
        self.x = 0
        self.target_x = 0
        self.facing_left = False
        self.frame_idx = 0
        self.lie_ticks_left = 0
        self.exit_side: str = "right"

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(TICK_MS)

    # ---- lane helpers (mirrors scene.py logic) -------------------------

    def _select_active_lanes(self) -> list[Lane]:
        if self.config.multi_monitor:
            return list(self._all_lanes)
        idx = self.config.primary_screen_index
        if idx < 0 or idx >= len(self._all_lanes):
            idx = 0
        return [self._all_lanes[idx]]

    def set_multi_monitor(self, enabled: bool) -> None:
        self._all_lanes = discover_lanes()
        self.lanes = self._select_active_lanes()
        if self.lane not in self.lanes:
            self.lane = self.lanes[0]

    def _user_active(self) -> bool:
        return seconds_since_last_input() < 1.5

    def _user_idle_long_enough(self) -> bool:
        return seconds_since_last_input() >= self.config.idle_threshold_s

    def _set_state(self, s: State) -> None:
        self.state = s
        self.state_changed.emit(s.name)

    def _spawn_x(self, side: str) -> int:
        if side == "left":
            return self.lane.full.left() - self.cat.width() - EXIT_BUFFER_PX
        return self.lane.full.right() + EXIT_BUFFER_PX

    def _exit_target_x(self, side: str) -> int:
        overshoot = CAT_RUN_SPEED_PX * 4
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

    # ---- tick ----------------------------------------------------------

    def _tick(self) -> None:
        if not self.config.actor_enabled(ACTOR_NAME):
            if self.state != State.OFFSTAGE:
                self._exit_now()
            return

        if self.state != State.OFFSTAGE and self.state != State.FLEEING and self._user_active():
            self._begin_flee()
            return

        if self.state == State.OFFSTAGE:
            if self._user_idle_long_enough():
                self._enter()
        elif self.state == State.WALKING:
            self._tick_walk()
        elif self.state == State.LYING:
            self._tick_lie()
        elif self.state == State.FLEEING:
            self._tick_flee()

    # ---- transitions / state handlers ----------------------------------

    def _enter(self) -> None:
        self.lane = random.choice(self.lanes)
        edges = self.lane.external_edges
        spawn_side = random.choice(edges)
        self.facing_left = spawn_side == "right"  # face inward
        self.x = self._spawn_x(spawn_side)
        self.target_x = self._exit_target_x(
            "left" if spawn_side == "right" else "right"
        )
        self.frame_idx = 0
        frames = self._walk_left if self.facing_left else self._walk_right
        self.cat.set_pixmap(frames[0])
        self.cat.move_to(self.x, self.lane.ground_y)
        self.cat.show()
        self._set_state(State.WALKING)

    def _tick_walk(self) -> None:
        # Maybe stop and lie down occasionally.
        if random.random() < LIE_CHANCE_PER_TICK:
            self._begin_lie()
            return

        step = -CAT_WALK_SPEED_PX if self.facing_left else CAT_WALK_SPEED_PX
        self.x += step
        self._draw_walk()
        if (not self.facing_left and self.x >= self.target_x) or (
            self.facing_left and self.x <= self.target_x
        ):
            self._exit_now()

    def _draw_walk(self) -> None:
        frames = self._walk_left if self.facing_left else self._walk_right
        self.frame_idx = (self.frame_idx + 1) % (len(frames) * WALK_FRAME_HOLD)
        idx = (self.frame_idx // WALK_FRAME_HOLD) % len(frames)
        self.cat.set_pixmap(frames[idx])
        self.cat.move_to(self.x, self.lane.ground_y)

    def _begin_lie(self) -> None:
        self.lie_ticks_left = LIE_DURATION_TICKS
        self.frame_idx = 0
        frames = self._lie_left if self.facing_left else self._lie_right
        self.cat.set_pixmap(frames[0])
        self.cat.move_to(self.x, self.lane.ground_y)
        self._set_state(State.LYING)

    def _tick_lie(self) -> None:
        self.lie_ticks_left -= 1
        # Cycle through lying frames at a slow breathing pace.
        frames = self._lie_left if self.facing_left else self._lie_right
        self.frame_idx = (self.frame_idx + 1) % (len(frames) * LIE_FRAME_HOLD)
        idx = (self.frame_idx // LIE_FRAME_HOLD) % len(frames)
        self.cat.set_pixmap(frames[idx])
        self.cat.move_to(self.x, self.lane.ground_y)
        if self.lie_ticks_left <= 0:
            self._set_state(State.WALKING)

    def _begin_flee(self) -> None:
        self.exit_side = self._nearest_external_edge()
        self.facing_left = self.exit_side == "left"
        self.target_x = self._exit_target_x(self.exit_side)
        self.frame_idx = 0
        self._set_state(State.FLEEING)

    def _tick_flee(self) -> None:
        step = -CAT_RUN_SPEED_PX if self.facing_left else CAT_RUN_SPEED_PX
        self.x += step
        frames = self._run_left if self.facing_left else self._run_right
        self.frame_idx = (self.frame_idx + 1) % (len(frames) * RUN_FRAME_HOLD)
        idx = (self.frame_idx // RUN_FRAME_HOLD) % len(frames)
        self.cat.set_pixmap(frames[idx])
        self.cat.move_to(self.x, self.lane.ground_y)
        if self._is_offscreen_for_lane():
            self._exit_now()

    def _exit_now(self) -> None:
        self.cat.hide()
        self._set_state(State.OFFSTAGE)
