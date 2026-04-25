"""Entry point for the petproj MVP.

Launches a transparent always-on-top scene that triggers on user idle.
A system-tray icon provides Pause / Quit control.
"""
import os
import sys

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from config import Config
from scene import ASSETS_DIR, Scene
from spritesheet import SpriteSheet


def _build_tray_icon() -> QIcon:
    sheet = SpriteSheet.load(os.path.join(ASSETS_DIR, "icon", "icon"))
    pix = sheet.frame(0, scale=2)
    pix = pix.scaled(QSize(32, 32), Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.FastTransformation)
    return QIcon(pix)


class TrayController:
    def __init__(self, app: QApplication, scene: Scene) -> None:
        self.app = app
        self.scene = scene

        self.tray = QSystemTrayIcon(_build_tray_icon(), parent=app)
        self.tray.setToolTip("petproj — desktop pet")

        # Build the menu — actions belong to the menu so they aren't GC'd.
        self.menu = QMenu()
        self.pause_action = self.menu.addAction("Pause")
        self.pause_action.setCheckable(True)
        self.pause_action.toggled.connect(self._on_pause_toggled)

        self.multi_monitor_action = self.menu.addAction("Multi-monitor")
        self.multi_monitor_action.setCheckable(True)
        self.multi_monitor_action.setChecked(self.scene.config.multi_monitor)
        self.multi_monitor_action.toggled.connect(self._on_multi_monitor_toggled)

        self.menu.addSeparator()

        self.quit_action = self.menu.addAction("Quit")
        self.quit_action.triggered.connect(self._on_quit)

        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_activated)
        self.tray.show()

    def _on_multi_monitor_toggled(self, enabled: bool) -> None:
        self.scene.set_multi_monitor(enabled)

    def _on_pause_toggled(self, paused: bool) -> None:
        if paused:
            self.scene.timer.stop()
            self.scene.person.hide()
            self.scene._hide_props()
            self.tray.setToolTip("petproj — paused")
        else:
            self.scene.timer.start()
            self.tray.setToolTip("petproj — desktop pet")

    def _on_quit(self) -> None:
        self.scene.timer.stop()
        self.scene.person.hide()
        self.scene._hide_props()
        self.tray.hide()
        self.app.quit()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        # Double-click toggles pause as a quick shortcut.
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.pause_action.toggle()


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    config = Config.load()
    scene = Scene(config)
    scene.state_changed.connect(lambda s: print(f"[scene] -> {s}", flush=True))

    if QSystemTrayIcon.isSystemTrayAvailable():
        app._tray = TrayController(app, scene)  # type: ignore[attr-defined]
    else:
        print("[main] system tray not available — quit via Ctrl+C in console",
              flush=True)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
