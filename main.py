"""Entry point for the petproj MVP.

Launches all configured actors (person, cat, ...) — each one a transparent
always-on-top scene that triggers on user idle. The system-tray icon
exposes Config, Summon, Reload sprites, and Quit. A global hotkey
(Ctrl+Shift+H) hides/unhides everything in one keypress (boss key).
"""
import os
import sys

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QCursor, QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from cat_scene import CatScene
from config import Config
from config_window import ConfigWindow
from hotkeys import GlobalHotkey
from scene import ASSETS_DIR, Scene
from skins import SKINS
from spritesheet import SpriteSheet


def _build_tray_icon() -> QIcon:
    sheet = SpriteSheet.load(os.path.join(ASSETS_DIR, "person", "icon"))
    pix = sheet.frame(0, scale=2)
    pix = pix.scaled(QSize(32, 32), Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.FastTransformation)
    return QIcon(pix)


class TrayController:
    def __init__(
        self, app: QApplication, config: Config,
        person_scene: Scene, cat_scene: CatScene,
    ) -> None:
        self.app = app
        self.config = config
        self.person_scene = person_scene
        self.cat_scene = cat_scene
        self._config_window: ConfigWindow | None = None
        self._hidden_by_boss_key = False

        self.tray = QSystemTrayIcon(_build_tray_icon(), parent=app)
        self._refresh_tooltip()

        self.menu = QMenu()
        self.config_action = self.menu.addAction("Config")
        self.config_action.triggered.connect(self._open_config_window)
        self.summon_action = self.menu.addAction("Summon to cursor")
        self.summon_action.triggered.connect(self._on_summon)
        self.feed_action = self.menu.addAction("Feed cat")
        self.feed_action.triggered.connect(self._on_feed)
        self.treat_action = self.menu.addAction("Drop treat at cursor")
        self.treat_action.triggered.connect(self._on_drop_treat)
        self.menu.addSeparator()
        self.pomodoro_action = self.menu.addAction("Start Pomodoro (25/5)")
        self.pomodoro_action.triggered.connect(self._on_toggle_pomodoro)
        self.skins_menu = self.menu.addMenu("Skin")
        for skin in SKINS:
            act = self.skins_menu.addAction(skin.name)
            act.setCheckable(True)
            act.setChecked(skin.name == self.config.cat.skin)
            act.triggered.connect(
                lambda _checked=False, name=skin.name: self._on_pick_skin(name)
            )
        self.menu.addSeparator()
        self.achievements_action = self.menu.addAction("Achievements…")
        self.achievements_action.triggered.connect(self._on_show_achievements)
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

    def _refresh_tooltip(self) -> None:
        name = self.config.cat.name or "petproj-mvp"
        self.tray.setToolTip(f"{name} — petproj-mvp")

    def _open_config_window(self) -> None:
        if self._config_window is not None and self._config_window.isVisible():
            self._config_window.raise_()
            self._config_window.activateWindow()
            return
        self._config_window = ConfigWindow(
            self.config, self._on_config_changed, self._on_step,
        )
        self._config_window.show()

    def _on_config_changed(self, key: str) -> None:
        if key == "monitors.multi_monitor":
            self.person_scene.set_multi_monitor(self.config.monitors.multi_monitor)
            self.cat_scene.set_multi_monitor(self.config.monitors.multi_monitor)
        elif key == "cat.scale":
            self.cat_scene.set_scale(self.config.cat.scale)
        elif key == "cat.name":
            self._refresh_tooltip()

    def _on_step(self) -> None:
        self.person_scene.step_one_frame()
        self.cat_scene.step_one_frame()

    def _on_reload(self) -> None:
        try:
            self.cat_scene.reload_sprite()
            print("[main] cat sprite reloaded", flush=True)
        except Exception as e:
            print(f"[main] reload failed: {e}", flush=True)

    def _on_summon(self) -> None:
        if not self.config.actor_enabled("cat"):
            return
        pos = QCursor.pos()
        self.cat_scene.summon_to(pos.x(), pos.y())

    def _on_feed(self) -> None:
        if not self.config.actor_enabled("cat"):
            return
        self.cat_scene.feed()

    def _on_drop_treat(self) -> None:
        if not self.config.actor_enabled("cat"):
            return
        self.cat_scene.drop_treat_at(QCursor.pos().x())

    def _on_toggle_pomodoro(self) -> None:
        if self.cat_scene._pomodoro_phase == "off":
            self.cat_scene.start_pomodoro(25, 5)
            self.pomodoro_action.setText("Stop Pomodoro")
        else:
            self.cat_scene.stop_pomodoro()
            self.pomodoro_action.setText("Start Pomodoro (25/5)")

    def _on_pick_skin(self, name: str) -> None:
        self.cat_scene.set_skin(name)
        for act in self.skins_menu.actions():
            act.setChecked(act.text() == name)

    def _on_show_achievements(self) -> None:
        # Lightweight tooltip-style dump: every line is "label — locked/unlocked".
        from PyQt6.QtWidgets import QMessageBox
        rows = []
        for ach, unlocked, value in self.cat_scene.achievements.all_progress():
            mark = "[x]" if unlocked else "[ ]"
            rows.append(f"{mark}  {ach.label}  ({int(value)}/{ach.threshold} {ach.stat})")
        QMessageBox.information(None, "Achievements", "\n".join(rows))

    def toggle_boss_key(self) -> None:
        """Boss key: hide both scenes immediately and reset them to OFFSTAGE
        so the cat doesn't keep walking invisibly. Toggle again to bring
        them back — they re-enter from an external edge on the next idle
        tick, just like a fresh launch."""
        if self._hidden_by_boss_key:
            self._hidden_by_boss_key = False
            self.boss_action.setText("Hide all (Ctrl+Shift+H)")
            self.cat_scene.timer.start(self.cat_scene.timer.interval())
            self.person_scene.timer.start(self.person_scene.timer.interval())
        else:
            self._hidden_by_boss_key = True
            self.boss_action.setText("Show all (Ctrl+Shift+H)")
            self.cat_scene.timer.stop()
            self.person_scene.timer.stop()
            # Force both scenes off-stage so resuming them is clean.
            self.cat_scene._exit_now()
            self.person_scene._exit()

    def _on_quit(self) -> None:
        self.person_scene.timer.stop()
        self.person_scene.person.hide()
        self.person_scene._hide_props()
        self.cat_scene.timer.stop()
        self.cat_scene.cat.hide()
        self.cat_scene.bubble.hide()
        self.cat_scene.effects.hide_all()
        if self._config_window is not None:
            self._config_window.close()
        self.tray.hide()
        self.app.quit()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._open_config_window()


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    config = Config.load()
    person_scene = Scene(config)
    cat_scene = CatScene(config)
    person_scene.state_changed.connect(lambda s: print(f"[person] -> {s}", flush=True))
    cat_scene.state_changed.connect(lambda s: print(f"[cat]    -> {s}", flush=True))

    tray: TrayController | None = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = TrayController(app, config, person_scene, cat_scene)
        app._tray = tray  # type: ignore[attr-defined]
    else:
        print("[main] system tray not available — quit via Ctrl+C in console",
              flush=True)

    # Boss key: Ctrl+Shift+H toggles all-hidden mode. No-op when no tray.
    if tray is not None:
        GlobalHotkey.register_all(app, [
            (("ctrl", "shift", "h"), tray.toggle_boss_key),
        ])

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
