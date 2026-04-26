"""Entry point for the petproj MVP.

Launches all configured actors (person, cat, ...) — each one a transparent
always-on-top scene that triggers on user idle. The system-tray icon
exposes a Config window and a Quit action.
"""
import os
import sys

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from cat_scene import CatScene
from config import Config
from config_window import ConfigWindow
from scene import ASSETS_DIR, Scene
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

        self.tray = QSystemTrayIcon(_build_tray_icon(), parent=app)
        self.tray.setToolTip("petproj-mvp")

        # Tray menu: Config + Reload + Quit (everything else lives in the dialog).
        self.menu = QMenu()
        self.config_action = self.menu.addAction("Config")
        self.config_action.triggered.connect(self._open_config_window)
        self.reload_action = self.menu.addAction("Reload sprites")
        self.reload_action.triggered.connect(self._on_reload)
        self.menu.addSeparator()
        self.quit_action = self.menu.addAction("Quit")
        self.quit_action.triggered.connect(self._on_quit)

        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_activated)
        self.tray.show()

    def _open_config_window(self) -> None:
        # Reuse the same window if already open.
        if self._config_window is not None and self._config_window.isVisible():
            self._config_window.raise_()
            self._config_window.activateWindow()
            return
        self._config_window = ConfigWindow(
            self.config, self._on_config_changed, self._on_step,
        )
        self._config_window.show()

    def _on_config_changed(self, key: str) -> None:
        # Forward live updates to scenes that care. Most fields are read
        # directly from config each tick, so this only handles the ones that
        # need an explicit re-render or re-discovery.
        if key == "monitors.multi_monitor":
            self.person_scene.set_multi_monitor(self.config.monitors.multi_monitor)
            self.cat_scene.set_multi_monitor(self.config.monitors.multi_monitor)
        elif key == "cat.scale":
            self.cat_scene.set_scale(self.config.cat.scale)

    def _on_step(self) -> None:
        # Single-step both scenes — caller is the Step button while paused.
        self.person_scene.step_one_frame()
        self.cat_scene.step_one_frame()

    def _on_reload(self) -> None:
        # Re-read sprites from disk so live-edited PNGs are picked up without
        # restarting the app. Currently only the cat is hand-edited.
        try:
            self.cat_scene.reload_sprite()
            print("[main] cat sprite reloaded", flush=True)
        except Exception as e:
            print(f"[main] reload failed: {e}", flush=True)

    def _on_quit(self) -> None:
        self.person_scene.timer.stop()
        self.person_scene.person.hide()
        self.person_scene._hide_props()
        self.cat_scene.timer.stop()
        self.cat_scene.cat.hide()
        if self._config_window is not None:
            self._config_window.close()
        self.tray.hide()
        self.app.quit()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        # Double-click as a quick shortcut to open Config.
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

    if QSystemTrayIcon.isSystemTrayAvailable():
        app._tray = TrayController(app, config, person_scene, cat_scene)  # type: ignore[attr-defined]
    else:
        print("[main] system tray not available — quit via Ctrl+C in console",
              flush=True)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
