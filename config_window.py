"""Pixel-art Config window — modeless settings dialog opened from the tray.

Replaces the old QFormLayout dialog with our own pixel widgets from
`pixel_ui.py`. Layout: a sidebar of pixel "tabs" on the left and a
content panel on the right. Sections:

    Cat         — enable / name / count / scale / skin / pace controls
    Person      — enable
    Monitors    — multi-monitor / primary screen
    Behaviour   — idle threshold / debug always-on
    Stats       — live hunger / cpu / pomodoro / pet count
    Achievements — progress per achievement + reset

Every change saves config.json immediately and fires a callback so scenes
can react. The Stats panel polls the cat scene a few times a second to
show live values.
"""
from __future__ import annotations

import os
from typing import Callable

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QGuiApplication, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
    QLabel,
)

from achievements import ACHIEVEMENTS, AchievementTracker
from config import Config
from pixel_ui import (
    DEFAULT_SCALE, LabeledRow, PixelButton, PixelCheckbox, PixelComboBox,
    PixelHRule, PixelLabel, PixelPanel, PixelProgress, PixelSlider,
    PixelSpinBox, PixelTitleBar, THEME, draw_text, measure_text,
)
from skins import SKINS

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
ICONS_DIR = os.path.join(ASSETS_DIR, "icons")

WINDOW_W = 720
WINDOW_H = 520


def _load_icon(name: str, size: int = 32) -> QPixmap | None:
    path = os.path.join(ICONS_DIR, f"{name}.png")
    if not os.path.exists(path):
        return None
    pix = QPixmap(path)
    if pix.isNull():
        return None
    return pix.scaled(size, size,
                      Qt.AspectRatioMode.KeepAspectRatio,
                      Qt.TransformationMode.FastTransformation)


class _Sidebar(QWidget):
    """Vertical pixel-art tab strip; each entry is a clickable PixelButton."""

    def __init__(self, items: list[tuple[str, str | None]],
                 on_pick: Callable[[int], None]) -> None:
        super().__init__()
        self._on_pick = on_pick
        self._buttons: list[PixelButton] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(DEFAULT_SCALE * 4, DEFAULT_SCALE * 4,
                                  DEFAULT_SCALE * 2, DEFAULT_SCALE * 4)
        layout.setSpacing(DEFAULT_SCALE * 3)
        title = PixelLabel("hopper", scale=DEFAULT_SCALE + 1)
        layout.addWidget(title)
        sub = PixelLabel("settings", scale=DEFAULT_SCALE,
                         color=QColor(THEME.accent_dim))
        layout.addWidget(sub)
        layout.addSpacing(DEFAULT_SCALE * 4)
        for i, (label, _icon) in enumerate(items):
            btn = PixelButton(label, scale=DEFAULT_SCALE, padding_px=8)
            btn.clicked.connect(lambda i=i: self._pick(i))
            layout.addWidget(btn)
            self._buttons.append(btn)
        layout.addStretch(1)

    def _pick(self, idx: int) -> None:
        self._on_pick(idx)

    def setActive(self, idx: int) -> None:
        # Visual hint: active tab uses accent text. Re-paint via setText to
        # the same value (forces repaint in our PixelButton).
        for i, b in enumerate(self._buttons):
            b.update()


class ConfigWindow(QDialog):
    """Stays on top so the user can tweak while the pet is on screen."""

    def __init__(self, config: Config, on_change: Callable[[str], None],
                 on_step: Callable[[], None],
                 cat_scenes: list | None = None,
                 achievements: AchievementTracker | None = None) -> None:
        super().__init__()
        self.setWindowTitle("hopper · pixel pet")
        # Frameless: we draw our own pixel-art title bar below.
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.resize(WINDOW_W, WINDOW_H)
        # Cream paper background to match the bubble style.
        self.setStyleSheet(
            f"background-color: rgb({THEME.paper.red()}, "
            f"{THEME.paper.green()}, {THEME.paper.blue()});"
        )

        self._config = config
        self._on_change = on_change
        self._on_step = on_step
        self._cat_scenes = cat_scenes or []
        self._achievements = achievements

        # Try to load some icons if available; gracefully missing if not.
        self._icons = {
            "cog":     _load_icon("cog"),
            "trophy":  _load_icon("trophy"),
            "tomato":  _load_icon("tomato"),
            "feed":    _load_icon("feed"),
            "fish":    _load_icon("fish"),
            "magnet":  _load_icon("magnet"),
        }

        # ---- root layout: pixel title bar on top, content underneath
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        head_path = os.path.join(ICONS_DIR, "cat_head.png")
        head_pix = QPixmap(head_path) if os.path.exists(head_path) else None
        self._title_bar = PixelTitleBar(
            "hopper · pixel pet config",
            icon_pixmap=head_pix,
            scale=DEFAULT_SCALE,
        )
        self._title_bar.close_clicked.connect(self.close)
        self._title_bar.minimize_clicked.connect(self.showMinimized)
        if head_pix is not None and not head_pix.isNull():
            self.setWindowIcon(QIcon(head_pix))
        root.addWidget(self._title_bar)

        # ---- inner: sidebar + content ------------------------------
        inner = QWidget()
        inner_layout = QHBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(0)

        tabs = [
            ("cat",          "fish"),
            ("person",       None),
            ("monitors",     None),
            ("behaviour",    "cog"),
            ("stats",        "tomato"),
            ("achievements", "trophy"),
        ]
        self._sidebar = _Sidebar(tabs, self._switch_tab)
        inner_layout.addWidget(self._sidebar, 0)

        self._stack = QStackedWidget()
        inner_layout.addWidget(self._stack, 1)

        # Build each tab's content.
        self._stack.addWidget(self._build_cat_tab())
        self._stack.addWidget(self._build_person_tab())
        self._stack.addWidget(self._build_monitors_tab())
        self._stack.addWidget(self._build_behaviour_tab())
        self._stack.addWidget(self._build_stats_tab())
        self._stack.addWidget(self._build_achievements_tab())

        root.addWidget(inner, 1)
        self._switch_tab(0)

        # Live updates for the Stats panel.
        self._poll = QTimer(self)
        self._poll.setInterval(250)
        self._poll.timeout.connect(self._refresh_stats)
        self._poll.start()

    # ---- tab routing -------------------------------------------------

    def _switch_tab(self, idx: int) -> None:
        self._stack.setCurrentIndex(idx)
        self._sidebar.setActive(idx)

    # ---- generic helpers ---------------------------------------------

    def _wrap(self, *children: QWidget) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(DEFAULT_SCALE * 6, DEFAULT_SCALE * 4,
                                  DEFAULT_SCALE * 6, DEFAULT_SCALE * 4)
        layout.setSpacing(DEFAULT_SCALE * 3)
        for c in children:
            layout.addWidget(c)
        layout.addStretch(1)
        return w

    def _set(self, key: str, value) -> None:
        sub_attr, field_name = key.split(".", 1)
        sub = getattr(self._config, sub_attr)
        setattr(sub, field_name, value)
        self._config.save()
        self._on_change(key)

    # ---- tab: cat ----------------------------------------------------

    def _build_cat_tab(self) -> QWidget:
        general = PixelPanel("general")
        en = PixelCheckbox("enable", checked=self._config.cat.enabled)
        en.toggled.connect(lambda v: self._set("cat.enabled", bool(v)))
        general.body.addWidget(en)

        count = PixelSpinBox(1, 4, self._config.cat.count, step=1)
        count.valueChanged.connect(
            lambda v: self._set("cat.count", int(v))
        )
        general.body.addWidget(LabeledRow("count:", count))

        scale = PixelSlider(0.5, 4.0, float(self._config.cat.scale),
                            step=0.25, decimals=2, suffix="x", width_px=260)
        scale.valueChanged.connect(
            lambda v: self._set("cat.scale", float(v))
        )
        general.body.addWidget(LabeledRow("scale:", scale))

        y_off = PixelSpinBox(-100, 100, self._config.cat.y_offset_px, step=1,
                             suffix="px")
        y_off.valueChanged.connect(
            lambda v: self._set("cat.y_offset_px", int(v))
        )
        general.body.addWidget(LabeledRow("y-offset:", y_off))

        # Skin combobox.
        skin_names = [s.name for s in SKINS]
        try:
            cur_idx = skin_names.index(self._config.cat.skin)
        except ValueError:
            cur_idx = 0
        skin = PixelComboBox(skin_names, cur_idx, min_width=200)
        skin.currentIndexChanged.connect(
            lambda i: self._set("cat.skin", skin_names[i])
        )
        general.body.addWidget(LabeledRow("skin:", skin))

        night = PixelCheckbox("night mode", checked=self._config.cat.night_mode)
        night.toggled.connect(lambda v: self._set("cat.night_mode", bool(v)))
        general.body.addWidget(night)

        # Pace panel — walk + run.
        pace = PixelPanel("pace")
        walk_hold = PixelSpinBox(1, 30, self._config.cat.walk_frame_hold, step=1)
        walk_hold.valueChanged.connect(
            lambda v: self._set("cat.walk_frame_hold", int(v))
        )
        pace.body.addWidget(LabeledRow("walk frame hold:", walk_hold))
        walk_mult = PixelSlider(0.0, 5.0, self._config.cat.walk_stride_multiplier,
                                step=0.05, decimals=2, suffix="x")
        walk_mult.valueChanged.connect(
            lambda v: self._set("cat.walk_stride_multiplier", float(v))
        )
        pace.body.addWidget(LabeledRow("walk stride x:", walk_mult))
        run_hold = PixelSpinBox(1, 30, self._config.cat.run_frame_hold, step=1)
        run_hold.valueChanged.connect(
            lambda v: self._set("cat.run_frame_hold", int(v))
        )
        pace.body.addWidget(LabeledRow("run frame hold:", run_hold))
        run_mult = PixelSlider(0.0, 5.0, self._config.cat.run_stride_multiplier,
                               step=0.05, decimals=2, suffix="x")
        run_mult.valueChanged.connect(
            lambda v: self._set("cat.run_stride_multiplier", float(v))
        )
        pace.body.addWidget(LabeledRow("run stride x:", run_mult))

        return self._wrap(general, pace)

    # ---- tab: person -------------------------------------------------

    def _build_person_tab(self) -> QWidget:
        panel = PixelPanel("person actor")
        en = PixelCheckbox("enable", checked=self._config.person.enabled)
        en.toggled.connect(lambda v: self._set("person.enabled", bool(v)))
        panel.body.addWidget(en)
        return self._wrap(panel)

    # ---- tab: monitors -----------------------------------------------

    def _build_monitors_tab(self) -> QWidget:
        panel = PixelPanel("monitors")
        multi = PixelCheckbox("multi-monitor",
                              checked=self._config.monitors.multi_monitor)
        multi.toggled.connect(lambda v: self._set("monitors.multi_monitor", bool(v)))
        panel.body.addWidget(multi)

        screen_names = []
        for i, screen in enumerate(QGuiApplication.screens()):
            name = screen.name() or f"screen-{i}"
            screen_names.append(f"{i} {name}")
        if not screen_names:
            screen_names = ["0"]
        idx = max(0, min(self._config.monitors.primary_screen_index,
                         len(screen_names) - 1))
        primary = PixelComboBox(screen_names, idx, min_width=220)
        primary.currentIndexChanged.connect(
            lambda i: self._set("monitors.primary_screen_index", max(0, i))
        )
        panel.body.addWidget(LabeledRow("primary:", primary))
        hint = PixelLabel("(used when multi-monitor is off)", scale=2,
                          color=THEME.accent_dim)
        panel.body.addWidget(hint)
        return self._wrap(panel)

    # ---- tab: behaviour ----------------------------------------------

    def _build_behaviour_tab(self) -> QWidget:
        panel = PixelPanel("behaviour")
        idle = PixelSlider(1.0, 60.0, float(self._config.behaviour.idle_threshold_s),
                           step=0.5, decimals=1, suffix="s", width_px=300)
        idle.valueChanged.connect(
            lambda v: self._set("behaviour.idle_threshold_s", float(v))
        )
        panel.body.addWidget(LabeledRow("idle threshold:", idle))

        always = PixelCheckbox("always on (skip idle gate)",
                               checked=self._config.behaviour.debug_always_on)
        always.toggled.connect(
            lambda v: self._set("behaviour.debug_always_on", bool(v))
        )
        panel.body.addWidget(always)

        debug = PixelPanel("debug")
        self._pause_check = PixelCheckbox("pause",
                                           checked=self._config.behaviour.debug_paused)
        self._pause_check.toggled.connect(self._on_pause_toggled)
        debug.body.addWidget(self._pause_check)
        self._step_btn = PixelButton("step >", scale=DEFAULT_SCALE)
        self._step_btn.clicked.connect(self._on_step_btn)
        debug.body.addWidget(self._step_btn)
        return self._wrap(panel, debug)

    def _on_pause_toggled(self, paused: bool) -> None:
        self._config.behaviour.debug_paused = bool(paused)
        self._config.save()
        self._on_change("behaviour.debug_paused")

    def _on_step_btn(self) -> None:
        self._on_step()

    # ---- tab: stats (live) -------------------------------------------

    def _build_stats_tab(self) -> QWidget:
        panel = PixelPanel("live stats")
        self._hunger_bar = PixelProgress("hunger 0/100", width_px=300,
                                          fill_color=THEME.fill_good)
        panel.body.addWidget(self._hunger_bar)

        self._cpu_bar = PixelProgress("cpu 0%", width_px=300,
                                       fill_color=THEME.fill_good)
        panel.body.addWidget(self._cpu_bar)

        self._pomo_label = PixelLabel("pomodoro: off")
        panel.body.addWidget(self._pomo_label)

        self._pets_label = PixelLabel("pets received: 0")
        panel.body.addWidget(self._pets_label)

        self._state_label = PixelLabel("cat state: -")
        panel.body.addWidget(self._state_label)

        return self._wrap(panel)

    def _refresh_stats(self) -> None:
        if not self._cat_scenes:
            return
        primary = self._cat_scenes[0]
        # Hunger.
        hunger = float(getattr(primary, "_hunger", 0.0))
        self._hunger_bar.setLabel(f"hunger {int(hunger)}/100")
        frac = max(0.0, min(1.0, hunger / 100.0))
        self._hunger_bar.setFraction(frac)
        if hunger >= 80:
            self._hunger_bar.setFillColor(THEME.fill_bad)
        elif hunger >= 50:
            self._hunger_bar.setFillColor(THEME.fill_warn)
        else:
            self._hunger_bar.setFillColor(THEME.fill_good)
        # CPU.
        cpu = float(getattr(primary, "_cpu_pct", 0.0))
        self._cpu_bar.setFraction(min(1.0, cpu / 100.0))
        self._cpu_bar.setLabel(f"cpu {int(cpu)}%")
        if cpu >= 70:
            self._cpu_bar.setFillColor(THEME.fill_bad)
        elif cpu >= 40:
            self._cpu_bar.setFillColor(THEME.fill_warn)
        else:
            self._cpu_bar.setFillColor(THEME.fill_good)
        # Pomodoro.
        phase = getattr(primary, "_pomodoro_phase", "off")
        secs = int(getattr(primary, "_pomodoro_seconds_left", 0))
        if phase == "off":
            self._pomo_label.setText("pomodoro: off")
        else:
            mm, ss = divmod(max(0, secs), 60)
            self._pomo_label.setText(f"pomodoro {phase}: {mm:02d}:{ss:02d}")
        self._pets_label.setText(
            f"pets received: {int(getattr(primary, '_petted_count', 0))}"
        )
        try:
            self._state_label.setText(f"cat state: {primary.state.name.lower()}")
        except Exception:
            pass

    # ---- tab: achievements ------------------------------------------

    def _build_achievements_tab(self) -> QWidget:
        panel = PixelPanel("achievements")
        self._ach_rows: list[tuple[PixelLabel, PixelProgress]] = []
        if self._achievements is None:
            panel.body.addWidget(PixelLabel("(no tracker connected)"))
            return self._wrap(panel)
        for ach, unlocked, value in self._achievements.all_progress():
            row = QWidget()
            row_l = QHBoxLayout(row)
            row_l.setContentsMargins(0, 0, 0, 0)
            row_l.setSpacing(DEFAULT_SCALE * 3)
            check = PixelCheckbox(checked=unlocked)
            check.setEnabled(False)
            row_l.addWidget(check)
            label = PixelLabel(f"{ach.label} ({ach.threshold} {ach.stat})")
            row_l.addWidget(label)
            bar = PixelProgress("", width_px=160)
            frac = min(1.0, float(value) / max(1, ach.threshold))
            bar.setFraction(frac)
            bar.setFillColor(THEME.accent if unlocked else THEME.fill_warn)
            row_l.addWidget(bar)
            row_l.addStretch(1)
            panel.body.addWidget(row)
            self._ach_rows.append((label, bar))
        reset = PixelButton("reset all", scale=DEFAULT_SCALE)
        reset.clicked.connect(self._reset_achievements)
        panel.body.addWidget(reset)
        return self._wrap(panel)

    def _reset_achievements(self) -> None:
        if self._achievements is None:
            return
        self._achievements.reset()
        # Refresh visuals: reload the achievements tab in place.
        old = self._stack.widget(5)
        new = self._build_achievements_tab()
        self._stack.removeWidget(old)
        old.deleteLater()
        self._stack.insertWidget(5, new)
        self._stack.setCurrentIndex(5)
