"""Scene controller — drives the typing-person scenario for the MVP.

Multi-monitor aware. Each physical screen is a "lane" with a known ground line
and a set of *external* edges — the screen edges that don't border another
screen. Spawns/exits use external edges only, so the character never warps off
into a phantom area or pops back from the wrong side.

States:
    OFFSTAGE  — nothing visible, waiting for idle.
    ENTERING  — walking from chosen external edge to a target x within the lane.
    SETUP     — table + chair + laptop appear next to character.
    WORKING   — sitting and "typing".
    LEAVING   — props vanish, character walks to a chosen external edge.
    FLEEING   — user came back: rush to nearest external edge.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum, auto

from PyQt6.QtCore import QObject, QRect, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QScreen

import os

from character import SpriteWidget
from config import Config
from idle_detector import seconds_since_last_input
from spritesheet import SpriteSheet

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

SCALE = 5
WALK_SPEED_PX = 4
TICK_MS = 60
WALK_FRAME_HOLD = 4
WORK_DURATION_MIN_TICKS = 180   # ~10.8 s minimum
WORK_DURATION_MAX_TICKS = 420   # ~25 s maximum
GROUND_DROP_PX = 4         # how many px the feet overlap the taskbar top edge
EXIT_BUFFER_PX = 8         # how far past the edge we consider "off-screen"
SIT_LIFT_PX = 30           # raise sit sprite so butt aligns with chair seat top
                           # = chair_height_px - empty_rows_at_bottom_of_sit_sprite_px
                           # chair: 7 rows × SCALE = 35px; sit sprite: 1 empty bottom row × SCALE = 5px
                           # → 35 - 5 = 30


class State(Enum):
    OFFSTAGE = auto()
    ENTERING = auto()
    SETUP = auto()
    WORKING = auto()
    LEAVING = auto()
    FLEEING = auto()


@dataclass
class Lane:
    """One physical screen plus metadata about which edges face the void."""
    screen: QScreen
    full: QRect              # absolute virtual-desktop geometry
    avail: QRect             # availableGeometry — excludes taskbar
    ground_y: int
    has_left_neighbor: bool = False
    has_right_neighbor: bool = False

    @property
    def external_edges(self) -> list[str]:
        edges = []
        if not self.has_left_neighbor:
            edges.append("left")
        if not self.has_right_neighbor:
            edges.append("right")
        return edges or ["left", "right"]  # boxed-in fallback


def discover_lanes() -> list[Lane]:
    """Build Lane info for every physical screen, computing horizontal neighbours
    by checking adjacent absolute coordinates with vertical overlap."""
    lanes: list[Lane] = []
    for s in QGuiApplication.screens():
        full = s.geometry()
        avail = s.availableGeometry()
        ground_y = avail.bottom() + GROUND_DROP_PX
        lanes.append(Lane(screen=s, full=full, avail=avail, ground_y=ground_y))

    for lane in lanes:
        for other in lanes:
            if other is lane:
                continue
            v_overlap = (
                other.full.top() < lane.full.bottom()
                and other.full.bottom() > lane.full.top()
            )
            if not v_overlap:
                continue
            if other.full.right() + 1 == lane.full.left():
                lane.has_left_neighbor = True
            elif other.full.left() == lane.full.right() + 1:
                lane.has_right_neighbor = True
    return lanes


class Scene(QObject):
    state_changed = pyqtSignal(str)

    def __init__(self, config: Config | None = None) -> None:
        super().__init__()
        self.config = config or Config()
        self._all_lanes = discover_lanes()
        self.lanes = self._select_active_lanes()
        self.lane: Lane = self.lanes[0]

        # Load all art from PNG sheets in assets/<actor>/<actor>.{png,json}.
        # Edit those files in any pixel-art editor — they're picked up on next start.
        person_sheet = SpriteSheet.load(os.path.join(ASSETS_DIR, "person", "person"))
        table_sheet = SpriteSheet.load(os.path.join(ASSETS_DIR, "table", "table"))
        laptop_sheet = SpriteSheet.load(os.path.join(ASSETS_DIR, "laptop", "laptop"))
        chair_sheet = SpriteSheet.load(os.path.join(ASSETS_DIR, "chair", "chair"))

        # Pre-render the frames we need in both facings.
        self._walk_right = person_sheet.animation("walk", scale=SCALE)
        self._walk_left = person_sheet.animation("walk", scale=SCALE, mirror=True)
        self._stand_right = person_sheet.frame_by_name("stand", scale=SCALE)
        self._sit_a = person_sheet.frame_by_name("sit", scale=SCALE)
        self._sit_b = person_sheet.frame_by_name("sit_type", scale=SCALE)
        self._table_pix = table_sheet.frame(0, scale=SCALE)
        self._chair_pix = chair_sheet.frame(0, scale=SCALE)
        self._laptop_pix = laptop_sheet.frame(0, scale=SCALE)

        self.person = SpriteWidget(self._stand_right)
        self.table = SpriteWidget(self._table_pix)
        self.chair = SpriteWidget(self._chair_pix)
        self.laptop = SpriteWidget(self._laptop_pix)

        self.state = State.OFFSTAGE
        self.x = -self.person.width()
        self.target_x = 0
        self.exit_side: str = "right"
        self.frame_idx = 0
        self.work_ticks_left = 0

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(TICK_MS)

    # ---- config-driven lane selection ----------------------------------

    def _select_active_lanes(self) -> list[Lane]:
        """Return the lanes the scene is currently allowed to spawn on,
        based on the current config. We KEEP the physical neighbour flags
        so the character never spawns on a coordinate that's actually
        on a different monitor."""
        if self.config.multi_monitor:
            return list(self._all_lanes)
        idx = self.config.primary_screen_index
        if idx < 0 or idx >= len(self._all_lanes):
            idx = 0
        return [self._all_lanes[idx]]

    def set_multi_monitor(self, enabled: bool) -> None:
        """Live toggle. The change takes effect on the next spawn (the current
        scene, if any, finishes on its current lane)."""
        self.config.multi_monitor = enabled
        self.config.save()
        # Re-discover so neighbour flags are correct again if turning multi back on.
        self._all_lanes = discover_lanes()
        self.lanes = self._select_active_lanes()
        if self.lane not in self.lanes:
            self.lane = self.lanes[0]
        print(
            f"[scene] multi_monitor={enabled}, active lanes: "
            f"{[s.screen.name() for s in self.lanes]}",
            flush=True,
        )

    # ---- helpers -------------------------------------------------------

    def _set_state(self, s: State) -> None:
        self.state = s
        self.state_changed.emit(s.name)

    def _hide_props(self) -> None:
        self.table.hide()
        self.chair.hide()
        self.laptop.hide()

    def _show_props_around(self, person_x: int) -> None:
        ty = self.lane.ground_y
        tx = person_x + self.person.width() - 2 * SCALE
        self.table.move_to(tx, ty)
        self.table.show()

        # Laptop sits flush ON the table top (no overlap into the table).
        lx = tx + 2 * SCALE
        ly = ty - self.table.height()
        self.laptop.move_to(lx, ly)
        self.laptop.show()

        # Chair: stand on the floor; its seat top will be at ground - chair height.
        cx = person_x + 2 * SCALE
        self.chair.move_to(cx, ty)
        self.chair.show()

    def _user_active(self) -> bool:
        return seconds_since_last_input() < 1.5

    def _user_idle_long_enough(self) -> bool:
        return seconds_since_last_input() >= self.config.idle_threshold_s

    def _person_center_x(self) -> int:
        return self.x + self.person.width() // 2

    def _nearest_external_edge(self) -> str:
        lane = self.lane
        edges = lane.external_edges
        if len(edges) == 1:
            return edges[0]
        cx = self._person_center_x()
        dist_left = cx - lane.full.left()
        dist_right = lane.full.right() - cx
        return "left" if dist_left < dist_right else "right"

    def _farther_external_edge(self) -> str:
        """Pick the external edge farther from current x — for natural exits."""
        lane = self.lane
        edges = lane.external_edges
        if len(edges) == 1:
            return edges[0]
        cx = self._person_center_x()
        dist_left = cx - lane.full.left()
        dist_right = lane.full.right() - cx
        return "right" if dist_right > dist_left else "left"

    def _spawn_x(self, lane: Lane, side: str) -> int:
        """X coordinate that places the sprite just outside the given edge."""
        if side == "left":
            return lane.full.left() - self.person.width() - EXIT_BUFFER_PX
        return lane.full.right() + EXIT_BUFFER_PX

    def _exit_target_x(self, lane: Lane, side: str) -> int:
        # Push target past the off-screen threshold so we never get stuck
        # oscillating around target == off-screen edge.
        overshoot = WALK_SPEED_PX * 6
        if side == "left":
            return lane.full.left() - self.person.width() - EXIT_BUFFER_PX - overshoot
        return lane.full.right() + EXIT_BUFFER_PX + overshoot

    def _is_offscreen_for_lane(self) -> bool:
        lane = self.lane
        return (
            self.x + self.person.width() < lane.full.left() - EXIT_BUFFER_PX
            or self.x > lane.full.right() + EXIT_BUFFER_PX
        )

    # ---- main tick -----------------------------------------------------

    def _tick(self) -> None:
        if self.state == State.OFFSTAGE:
            if self._user_idle_long_enough():
                self._enter()
            return

        if self.state not in (State.OFFSTAGE, State.FLEEING) and self._user_active():
            self._begin_flee()
            return

        if self.state == State.ENTERING:
            self._tick_walk_to(self.target_x, on_arrive=self._begin_setup)
        elif self.state == State.SETUP:
            self._show_props_around(self.x)
            self.person.set_pixmap(self._sit_a)
            self.person.move_to(self.x, self.lane.ground_y - SIT_LIFT_PX)
            self.work_ticks_left = random.randint(
                WORK_DURATION_MIN_TICKS, WORK_DURATION_MAX_TICKS
            )
            self._set_state(State.WORKING)
        elif self.state == State.WORKING:
            self._tick_work()
        elif self.state == State.LEAVING:
            target = self._exit_target_x(self.lane, self.exit_side)
            self._tick_walk_to(target, on_arrive=self._exit)
        elif self.state == State.FLEEING:
            target = self._exit_target_x(self.lane, self.exit_side)
            going_right = target > self.x
            step = WALK_SPEED_PX * 3 * (1 if going_right else -1)
            self.x += step
            self._draw_walking(facing_left=not going_right)
            if self._is_offscreen_for_lane():
                self._exit()

    # ---- state transitions --------------------------------------------

    def _enter(self) -> None:
        self.lane = random.choice(self.lanes)
        lane_idx = self.lanes.index(self.lane)
        edges = self.lane.external_edges
        spawn_side = random.choice(edges)
        print(
            f"[scene] enter on screen[{lane_idx}] "
            f"({self.lane.screen.name()!r}) from {spawn_side} edge",
            flush=True,
        )

        self.x = self._spawn_x(self.lane, spawn_side)
        # Pick a target inside this lane, biased toward the opposite half
        # so the walk visibly traverses some screen distance.
        if spawn_side == "left":
            self.target_x = random.randint(
                int(self.lane.full.left() + self.lane.full.width() * 0.4),
                int(self.lane.full.left() + self.lane.full.width() * 0.7),
            )
        else:
            self.target_x = random.randint(
                int(self.lane.full.left() + self.lane.full.width() * 0.3),
                int(self.lane.full.left() + self.lane.full.width() * 0.6),
            )

        self.frame_idx = 0
        self._hide_props()
        self.person.set_pixmap(self._walk_right[0])
        self.person.move_to(self.x, self.lane.ground_y)
        self.person.show()
        self._set_state(State.ENTERING)

    def _begin_setup(self) -> None:
        self._set_state(State.SETUP)

    def _tick_work(self) -> None:
        self.work_ticks_left -= 1
        if self.work_ticks_left % 6 == 0:
            pix = self._sit_a if (self.work_ticks_left // 6) % 2 == 0 else self._sit_b
            self.person.set_pixmap(pix)
            self.person.move_to(self.x, self.lane.ground_y - SIT_LIFT_PX)
        if self.work_ticks_left <= 0:
            self._begin_leave()

    def _begin_leave(self) -> None:
        self._hide_props()
        self.exit_side = self._farther_external_edge()
        self.person.set_pixmap(self._walk_right[0])
        self.person.move_to(self.x, self.lane.ground_y)
        self._set_state(State.LEAVING)

    def _begin_flee(self) -> None:
        self._hide_props()
        self.exit_side = self._nearest_external_edge()
        self._set_state(State.FLEEING)

    def _exit(self) -> None:
        self.person.hide()
        self._hide_props()
        self._set_state(State.OFFSTAGE)

    # ---- low-level walking --------------------------------------------

    def _tick_walk_to(self, target: int, on_arrive) -> None:
        going_right = target > self.x
        step = WALK_SPEED_PX if going_right else -WALK_SPEED_PX
        self.x += step
        self._draw_walking(facing_left=not going_right)
        if (going_right and self.x >= target) or (not going_right and self.x <= target):
            on_arrive()

    def _draw_walking(self, facing_left: bool) -> None:
        self.frame_idx = (self.frame_idx + 1) % (len(self._walk_right) * WALK_FRAME_HOLD)
        idx = (self.frame_idx // WALK_FRAME_HOLD) % len(self._walk_right)
        pix = self._walk_left[idx] if facing_left else self._walk_right[idx]
        self.person.set_pixmap(pix)
        self.person.move_to(self.x, self.lane.ground_y)
