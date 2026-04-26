"""Entry point for the petproj MVP.

Launches all configured actors (person, 1..N cats) — each one a transparent
always-on-top scene that triggers on user idle. The system-tray icon
exposes Config, Summon, Feed, Drop treat, Pomodoro, Skin, Achievements,
Theme, Cat count, Boss-key (Ctrl+Shift+H) and Quit.
"""
import os
import sys

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QCursor, QIcon, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

import sfx
from achievements import AchievementTracker
from cat_scene import CatScene
from config import Config
from config_window import ConfigWindow
from hotkeys import GlobalHotkey
from pixel_ui import current_theme_name, set_theme
from scene import ASSETS_DIR, Scene
from skins import SKINS
from spritesheet import SpriteSheet

MAX_CATS = 4
TREAT_OFFSET_PX = 80     # stagger between treats when several cats hunt


def _build_tray_icon() -> QIcon:
    """Pixel-art cat-head icon (PixelLab) if present, otherwise fall back to
    the original person sprite head from the sprite sheet."""
    cat_path = os.path.join(ASSETS_DIR, "icons", "cat_head.png")
    if os.path.exists(cat_path):
        pix = QPixmap(cat_path)
        if not pix.isNull():
            pix = pix.scaled(QSize(64, 64), Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.FastTransformation)
            return QIcon(pix)
    sheet = SpriteSheet.load(os.path.join(ASSETS_DIR, "person", "icon"))
    pix = sheet.frame(0, scale=2)
    pix = pix.scaled(QSize(32, 32), Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.FastTransformation)
    return QIcon(pix)


def _icon(name: str, size: int = 24) -> QIcon | None:
    """Load a PixelLab-generated icon from assets/icons/<name>.png if present.

    Returns None when the file is missing — callers should treat that as
    "no icon, just text". Icons are scaled nearest-neighbour to keep the
    pixel-art look at standard menu sizes."""
    path = os.path.join(ASSETS_DIR, "icons", f"{name}.png")
    if not os.path.exists(path):
        return None
    from PyQt6.QtGui import QPixmap
    pix = QPixmap(path)
    if pix.isNull():
        return None
    pix = pix.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.FastTransformation)
    return QIcon(pix)


class TrayController:
    def __init__(
        self, app: QApplication, config: Config,
        person_scene: Scene, cat_scenes: list[CatScene],
        achievements: AchievementTracker,
    ) -> None:
        self.app = app
        self.config = config
        self.person_scene = person_scene
        self.cat_scenes = cat_scenes
        self.achievements = achievements
        self._config_window: ConfigWindow | None = None
        self._hidden_by_boss_key = False

        self.tray = QSystemTrayIcon(_build_tray_icon(), parent=app)
        self._refresh_tooltip()

        self.menu = QMenu()
        self.config_action = self.menu.addAction("Config")
        cog = _icon("cog")
        if cog: self.config_action.setIcon(cog)
        self.config_action.triggered.connect(self._open_config_window)
        self.summon_action = self.menu.addAction("Summon to cursor")
        magnet = _icon("magnet")
        if magnet: self.summon_action.setIcon(magnet)
        self.summon_action.triggered.connect(self._on_summon)
        self.feed_action = self.menu.addAction("Feed cat")
        feed = _icon("feed")
        if feed: self.feed_action.setIcon(feed)
        self.feed_action.triggered.connect(self._on_feed)
        self.treat_action = self.menu.addAction("Drop treat at cursor")
        fish = _icon("fish")
        if fish: self.treat_action.setIcon(fish)
        self.treat_action.triggered.connect(self._on_drop_treat)
        self.menu.addSeparator()

        self.pomodoro_action = self.menu.addAction("Start Pomodoro (25/5)")
        tomato = _icon("tomato")
        if tomato: self.pomodoro_action.setIcon(tomato)
        self.pomodoro_action.triggered.connect(self._on_toggle_pomodoro)

        # Cat count submenu (live-applied).
        self.count_menu = self.menu.addMenu("Cat count")
        self._count_actions = []
        for n in range(1, MAX_CATS + 1):
            act = self.count_menu.addAction(str(n))
            act.setCheckable(True)
            act.setChecked(n == self.config.cat.count)
            act.triggered.connect(lambda _c=False, n=n: self._on_set_cat_count(n))
            self._count_actions.append(act)

        # Skins submenu.
        self.skins_menu = self.menu.addMenu("Skin")
        self._skin_actions = []
        for skin in SKINS:
            act = self.skins_menu.addAction(skin.name)
            act.setCheckable(True)
            act.setChecked(skin.name == self.config.cat.skin)
            act.triggered.connect(
                lambda _checked=False, name=skin.name: self._on_pick_skin(name)
            )
            self._skin_actions.append(act)

        self.menu.addSeparator()
        self.achievements_action = self.menu.addAction("Achievements…")
        trophy = _icon("trophy")
        if trophy: self.achievements_action.setIcon(trophy)
        self.achievements_action.triggered.connect(self._on_show_achievements)

        # Theme submenu — light / dark.
        self.theme_menu = self.menu.addMenu("Theme")
        self._theme_actions = []
        for name in ("light", "dark"):
            act = self.theme_menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(name == self.config.behaviour.theme)
            act.triggered.connect(lambda _c=False, n=name: self._on_pick_theme(n))
            self._theme_actions.append(act)

        # Sounds toggle.
        self.sounds_action = self.menu.addAction("Sound effects")
        self.sounds_action.setCheckable(True)
        self.sounds_action.setChecked(self.config.behaviour.sounds)
        self.sounds_action.triggered.connect(self._on_toggle_sounds)

        self.menu.addSeparator()
        self.reload_action = self.menu.addAction("Reload sprites")
        self.reload_action.triggered.connect(self._on_reload)
        self.menu.addSeparator()
        self.boss_action = self.menu.addAction("Hide all (Ctrl+Shift+H)")
        self.boss_action.triggered.connect(self.toggle_boss_key)
        self.menu.addSeparator()
        self.quit_action = self.menu.addAction("Quit")
        self.quit_action.triggered.connect(self._on_quit)

        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_activated)
        self.tray.show()

    # ---- helpers ----------------------------------------------------

    def _refresh_tooltip(self) -> None:
        name = self.config.cat.name or "tabby"
        n = len(self.cat_scenes)
        suffix = "" if n <= 1 else f" ×{n}"
        self.tray.setToolTip(f"hopper · {name}{suffix}")

    def _open_config_window(self) -> None:
        if self._config_window is not None and self._config_window.isVisible():
            self._config_window.raise_()
            self._config_window.activateWindow()
            return
        self._config_window = ConfigWindow(
            self.config, self._on_config_changed, self._on_step,
            cat_scenes=self.cat_scenes,
            achievements=self.achievements,
        )
        self._config_window.show()

    def _on_config_changed(self, key: str) -> None:
        if key == "monitors.multi_monitor":
            self.person_scene.set_multi_monitor(self.config.monitors.multi_monitor)
            for c in self.cat_scenes:
                c.set_multi_monitor(self.config.monitors.multi_monitor)
        elif key == "cat.scale":
            for c in self.cat_scenes:
                c.set_scale(self.config.cat.scale)
        elif key == "cat.name":
            self._refresh_tooltip()

    def _on_step(self) -> None:
        self.person_scene.step_one_frame()
        for c in self.cat_scenes:
            c.step_one_frame()

    def _on_reload(self) -> None:
        try:
            for c in self.cat_scenes:
                c.reload_sprite()
            print("[main] cat sprites reloaded", flush=True)
        except Exception as e:
            print(f"[main] reload failed: {e}", flush=True)

    # ---- per-action implementations ---------------------------------

    def _on_summon(self) -> None:
        if not self.config.actor_enabled("cat"):
            return
        pos = QCursor.pos()
        for i, c in enumerate(self.cat_scenes):
            # Stagger the cats horizontally so they don't pile up in one spot.
            offset = (i - (len(self.cat_scenes) - 1) / 2) * 60
            c.summon_to(int(pos.x() + offset), pos.y())

    def _on_feed(self) -> None:
        if not self.config.actor_enabled("cat"):
            return
        for c in self.cat_scenes:
            c.feed()

    def _on_drop_treat(self) -> None:
        if not self.config.actor_enabled("cat"):
            return
        x = QCursor.pos().x()
        for i, c in enumerate(self.cat_scenes):
            # Different x per cat so each has its own treat to chase.
            stagger = (i - (len(self.cat_scenes) - 1) / 2) * TREAT_OFFSET_PX
            c.drop_treat_at(int(x + stagger))

    def _on_toggle_pomodoro(self) -> None:
        # All cats run their own pomodoro counter — toggle them in sync.
        any_on = any(c._pomodoro_phase != "off" for c in self.cat_scenes)
        if any_on:
            for c in self.cat_scenes:
                c.stop_pomodoro()
            self.pomodoro_action.setText("Start Pomodoro (25/5)")
        else:
            for c in self.cat_scenes:
                c.start_pomodoro(25, 5)
            self.pomodoro_action.setText("Stop Pomodoro")

    def _on_pick_skin(self, name: str) -> None:
        for c in self.cat_scenes:
            c.set_skin(name)
        for act in self._skin_actions:
            act.setChecked(act.text() == name)

    def _on_pick_theme(self, name: str) -> None:
        self.config.behaviour.theme = name
        self.config.save()
        set_theme(name)
        for act in self._theme_actions:
            act.setChecked(act.text() == name)

    def _on_toggle_sounds(self, checked: bool) -> None:
        self.config.behaviour.sounds = bool(checked)
        self.config.save()
        sfx.set_enabled(bool(checked))

    def _on_show_achievements(self) -> None:
        rows = []
        for ach, unlocked, value in self.achievements.all_progress():
            mark = "[x]" if unlocked else "[ ]"
            rows.append(f"{mark}  {ach.label}  ({int(value)}/{ach.threshold} {ach.stat})")
        QMessageBox.information(None, "Achievements", "\n".join(rows))

    def _on_set_cat_count(self, n: int) -> None:
        n = max(1, min(MAX_CATS, n))
        self.config.cat.count = n
        self.config.save()
        self._resize_cats_to(n)
        for act in self._count_actions:
            act.setChecked(act.text() == str(n))
        self._refresh_tooltip()

    def _resize_cats_to(self, n: int) -> None:
        # Shrink: stop and discard surplus cats.
        while len(self.cat_scenes) > n:
            cat = self.cat_scenes.pop()
            cat.timer.stop()
            cat.cat.hide()
            cat.bubble.hide()
            cat.effects.hide_all()
            cat.deleteLater()
        # Grow: spawn more, sharing the achievements tracker.
        while len(self.cat_scenes) < n:
            idx = len(self.cat_scenes)
            scene = CatScene(
                self.config,
                achievements=self.achievements,
                instance_id=idx,
            )
            scene.state_changed.connect(
                lambda s, i=idx: print(f"[cat{i}] -> {s}", flush=True)
            )
            self.cat_scenes.append(scene)

    # ---- boss key & quit --------------------------------------------

    def toggle_boss_key(self) -> None:
        if self._hidden_by_boss_key:
            self._hidden_by_boss_key = False
            self.boss_action.setText("Hide all (Ctrl+Shift+H)")
            for c in self.cat_scenes:
                c.timer.start(c.timer.interval())
            self.person_scene.timer.start(self.person_scene.timer.interval())
        else:
            self._hidden_by_boss_key = True
            self.boss_action.setText("Show all (Ctrl+Shift+H)")
            for c in self.cat_scenes:
                c.timer.stop()
            self.person_scene.timer.stop()
            for c in self.cat_scenes:
                c._exit_now()
            self.person_scene._exit()

    def _on_quit(self) -> None:
        self.person_scene.timer.stop()
        self.person_scene.person.hide()
        self.person_scene._hide_props()
        for c in self.cat_scenes:
            c.timer.stop()
            c.cat.hide()
            c.bubble.hide()
            c.effects.hide_all()
        if self._config_window is not None:
            self._config_window.close()
        self.tray.hide()
        self.app.quit()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._open_config_window()


def main() -> int:
    config = Config.load()

    # GPU rendering: ask Qt to use OpenGL when available so widget compositing
    # rides the dedicated GPU rather than the CPU raster path. Must be set
    # before QApplication is constructed; falls back silently on machines
    # without a GL driver.
    if config.behaviour.gpu_render:
        try:
            QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseDesktopOpenGL)
            QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("hopper")
    app.setApplicationDisplayName("hopper · pixel pet")
    # Window/taskbar icon picked up by Alt+Tab and the OS taskbar.
    cat_head = os.path.join(ASSETS_DIR, "icons", "cat_head.png")
    if os.path.exists(cat_head):
        app.setWindowIcon(QIcon(cat_head))

    # Apply persisted theme + preload UI sounds before any pixel widget is built.
    set_theme(config.behaviour.theme or "light")
    sfx.preload(parent=app)
    sfx.set_enabled(bool(config.behaviour.sounds))

    person_scene = Scene(config)

    achievements = AchievementTracker()
    initial_count = max(1, min(MAX_CATS, int(config.cat.count or 1)))
    cat_scenes: list[CatScene] = []
    for idx in range(initial_count):
        cs = CatScene(config, achievements=achievements, instance_id=idx)
        cs.state_changed.connect(
            lambda s, i=idx: print(f"[cat{i}] -> {s}", flush=True)
        )
        cat_scenes.append(cs)

    person_scene.state_changed.connect(lambda s: print(f"[person] -> {s}", flush=True))

    tray: TrayController | None = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = TrayController(app, config, person_scene, cat_scenes, achievements)
        app._tray = tray  # type: ignore[attr-defined]
    else:
        print("[main] system tray not available — quit via Ctrl+C in console",
              flush=True)

    if tray is not None:
        GlobalHotkey.register_all(app, [
            (("ctrl", "shift", "h"), tray.toggle_boss_key),
        ])

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
