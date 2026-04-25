"""Config window — a small dialog accessible from the tray menu.

Shows every persisted setting in `Config` plus a toggle per registered actor.
Changes are applied immediately and saved to config.json.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)

from config import Config


class ConfigWindow(QDialog):
    """Modeless dialog. Stays on top so the user can tweak while watching."""

    def __init__(self, config: Config, on_change) -> None:
        super().__init__()
        self.setWindowTitle("petproj-mvp — Config")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self._config = config
        self._on_change = on_change

        root = QVBoxLayout(self)

        # ---- Behaviour group ----
        beh = QGroupBox("Behaviour")
        beh_form = QFormLayout(beh)

        self.idle_spin = QDoubleSpinBox()
        self.idle_spin.setRange(1.0, 60.0)
        self.idle_spin.setSingleStep(0.5)
        self.idle_spin.setSuffix(" s")
        self.idle_spin.setValue(config.idle_threshold_s)
        self.idle_spin.valueChanged.connect(self._on_idle_changed)
        beh_form.addRow("Idle threshold:", self.idle_spin)

        root.addWidget(beh)

        # ---- Monitors group ----
        mon = QGroupBox("Monitors")
        mon_form = QFormLayout(mon)

        self.multi_check = QCheckBox()
        self.multi_check.setChecked(config.multi_monitor)
        self.multi_check.toggled.connect(self._on_multi_changed)
        mon_form.addRow("Multi-monitor:", self.multi_check)

        self.primary_combo = QComboBox()
        for i, screen in enumerate(QGuiApplication.screens()):
            self.primary_combo.addItem(f"[{i}] {screen.name()}", i)
        self.primary_combo.setCurrentIndex(
            min(max(config.primary_screen_index, 0), self.primary_combo.count() - 1)
        )
        self.primary_combo.currentIndexChanged.connect(self._on_primary_changed)
        mon_form.addRow("Primary screen:", self.primary_combo)
        mon_form.addRow(QLabel("(used when multi-monitor is off)"))

        root.addWidget(mon)

        # ---- Actors group ----
        act = QGroupBox("Actors")
        act_layout = QVBoxLayout(act)
        self._actor_checks: dict[str, QCheckBox] = {}
        for name in sorted(config.actors.keys()):
            cb = QCheckBox(name)
            cb.setChecked(config.actor_enabled(name))
            cb.toggled.connect(lambda checked, n=name: self._on_actor_changed(n, checked))
            act_layout.addWidget(cb)
            self._actor_checks[name] = cb

        root.addWidget(act)

        # ---- Close button ----
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    # ---- handlers (each persists immediately) -------------------------

    def _on_idle_changed(self, v: float) -> None:
        self._config.idle_threshold_s = float(v)
        self._config.save()
        self._on_change("idle_threshold_s")

    def _on_multi_changed(self, v: bool) -> None:
        self._config.multi_monitor = bool(v)
        self._config.save()
        self._on_change("multi_monitor")

    def _on_primary_changed(self, idx: int) -> None:
        self._config.primary_screen_index = max(0, idx)
        self._config.save()
        self._on_change("primary_screen_index")

    def _on_actor_changed(self, name: str, checked: bool) -> None:
        self._config.actors[name] = bool(checked)
        self._config.save()
        self._on_change(f"actors.{name}")
