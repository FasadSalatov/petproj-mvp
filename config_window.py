"""Config window — modeless settings dialog opened from the tray menu.

Layout: tabs for the four config groups (Behaviour / Monitors / Person / Cat)
plus a Debug strip at the bottom for live frame-stepping. Every change is
saved to config.json immediately and a callback fires so scenes can react.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QPushButton, QSpinBox, QTabWidget, QVBoxLayout,
    QWidget,
)

from config import Config


class ConfigWindow(QDialog):
    """Stays on top so the user can tweak while watching the actors."""

    def __init__(self, config: Config, on_change, on_step) -> None:
        """`on_change(key)` fires when any value changes; key is a dot-path
        like "monitors.multi_monitor" or "cat.walk_frame_deltas".
        `on_step()` fires when the Step button is clicked."""
        super().__init__()
        self.setWindowTitle("petproj-mvp — Config")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self._config = config
        self._on_change = on_change
        self._on_step = on_step

        root = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._build_behaviour_tab(), "Behaviour")
        tabs.addTab(self._build_monitors_tab(), "Monitors")
        tabs.addTab(self._build_person_tab(), "Person")
        tabs.addTab(self._build_cat_tab(), "Cat")
        root.addWidget(tabs)

        # ---- Debug strip (always visible at bottom) ----
        dbg = QGroupBox("Debug")
        dbg_row = QHBoxLayout(dbg)

        self.pause_check = QCheckBox("Pause")
        self.pause_check.setChecked(config.behaviour.debug_paused)
        self.pause_check.toggled.connect(self._on_pause_toggled)
        dbg_row.addWidget(self.pause_check)

        self.step_btn = QPushButton("Step ▶")
        self.step_btn.setEnabled(config.behaviour.debug_paused)
        self.step_btn.clicked.connect(self._do_step)
        dbg_row.addWidget(self.step_btn)

        dbg_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        dbg_row.addWidget(close_btn)

        root.addWidget(dbg)

    # ---- tab builders ---------------------------------------------------

    def _build_behaviour_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)

        self.idle_spin = QDoubleSpinBox()
        self.idle_spin.setRange(1.0, 60.0)
        self.idle_spin.setSingleStep(0.5)
        self.idle_spin.setSuffix(" s")
        self.idle_spin.setValue(self._config.behaviour.idle_threshold_s)
        self.idle_spin.valueChanged.connect(
            lambda v: self._set("behaviour.idle_threshold_s", float(v))
        )
        f.addRow("Idle threshold:", self.idle_spin)

        self.always_on_check = QCheckBox()
        self.always_on_check.setChecked(self._config.behaviour.debug_always_on)
        self.always_on_check.toggled.connect(
            lambda v: self._set("behaviour.debug_always_on", bool(v))
        )
        f.addRow("Always on (skip idle gate):", self.always_on_check)
        return w

    def _build_monitors_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)

        self.multi_check = QCheckBox()
        self.multi_check.setChecked(self._config.monitors.multi_monitor)
        self.multi_check.toggled.connect(
            lambda v: self._set("monitors.multi_monitor", bool(v))
        )
        f.addRow("Multi-monitor:", self.multi_check)

        self.primary_combo = QComboBox()
        for i, screen in enumerate(QGuiApplication.screens()):
            self.primary_combo.addItem(f"[{i}] {screen.name()}", i)
        self.primary_combo.setCurrentIndex(
            min(max(self._config.monitors.primary_screen_index, 0),
                self.primary_combo.count() - 1)
        )
        self.primary_combo.currentIndexChanged.connect(
            lambda i: self._set("monitors.primary_screen_index", max(0, i))
        )
        f.addRow("Primary screen:", self.primary_combo)
        f.addRow(QLabel("(used when multi-monitor is off)"))
        return w

    def _build_person_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.person_check = QCheckBox()
        self.person_check.setChecked(self._config.person.enabled)
        self.person_check.toggled.connect(
            lambda v: self._set("person.enabled", bool(v))
        )
        f.addRow("Enable:", self.person_check)
        return w

    def _build_cat_tab(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)

        # ---- Sizing group ----
        size_box = QGroupBox("Sizing")
        size_form = QFormLayout(size_box)

        self.cat_check = QCheckBox()
        self.cat_check.setChecked(self._config.cat.enabled)
        self.cat_check.toggled.connect(lambda v: self._set("cat.enabled", bool(v)))
        size_form.addRow("Enable:", self.cat_check)

        self.cat_scale_spin = QDoubleSpinBox()
        self.cat_scale_spin.setRange(0.5, 4.0)
        self.cat_scale_spin.setSingleStep(0.25)
        self.cat_scale_spin.setDecimals(2)
        self.cat_scale_spin.setValue(self._config.cat.scale)
        self.cat_scale_spin.valueChanged.connect(
            lambda v: self._set("cat.scale", float(v))
        )
        size_form.addRow("Scale:", self.cat_scale_spin)

        self.cat_y_offset_spin = QSpinBox()
        self.cat_y_offset_spin.setRange(-100, 100)
        self.cat_y_offset_spin.setSuffix(" px")
        self.cat_y_offset_spin.setValue(self._config.cat.y_offset_px)
        self.cat_y_offset_spin.valueChanged.connect(
            lambda v: self._set("cat.y_offset_px", int(v))
        )
        size_form.addRow("Y-offset:", self.cat_y_offset_spin)

        outer.addWidget(size_box)

        # ---- Walk gait (pace + multiplier + per-frame deltas) ----
        outer.addWidget(self._build_deltas_group(
            "Walk gait", "cat.walk_frame_deltas", self._config.cat.walk_frame_deltas,
            "cat.walk_frame_hold", self._config.cat.walk_frame_hold,
            "cat.walk_stride_multiplier", self._config.cat.walk_stride_multiplier,
        ))

        # ---- Run gait ----
        outer.addWidget(self._build_deltas_group(
            "Run gait", "cat.run_frame_deltas", self._config.cat.run_frame_deltas,
            "cat.run_frame_hold", self._config.cat.run_frame_hold,
            "cat.run_stride_multiplier", self._config.cat.run_stride_multiplier,
        ))

        outer.addStretch()
        return w

    def _build_deltas_group(self, title: str, deltas_key: str, values: list[float],
                             hold_key: str, hold_value: int,
                             mult_key: str, mult_value: float) -> QGroupBox:
        """A row of compact spinboxes (per-frame deltas) plus pace/multiplier
        controls for the same animation."""
        box = QGroupBox(title)
        v = QVBoxLayout(box)

        # Pace + multiplier row.
        pace_row = QHBoxLayout()
        pace_row.addWidget(QLabel("Frame hold (ticks):"))
        hold_spin = QSpinBox()
        hold_spin.setRange(1, 30)
        hold_spin.setValue(hold_value)
        hold_spin.valueChanged.connect(
            lambda v_: self._set(hold_key, int(v_))
        )
        pace_row.addWidget(hold_spin)
        pace_row.addSpacing(16)

        pace_row.addWidget(QLabel("Stride ×:"))
        mult_spin = QDoubleSpinBox()
        mult_spin.setRange(0.0, 5.0)
        mult_spin.setSingleStep(0.05)
        mult_spin.setDecimals(2)
        mult_spin.setValue(mult_value)
        mult_spin.valueChanged.connect(
            lambda v_: self._set(mult_key, float(v_))
        )
        pace_row.addWidget(mult_spin)
        pace_row.addStretch()
        v.addLayout(pace_row)

        # Per-frame deltas.
        spins_row = QHBoxLayout()
        spins: list[QDoubleSpinBox] = []
        for i, val in enumerate(values):
            label = QLabel(f"F{i}")
            spin = QDoubleSpinBox()
            spin.setRange(-30.0, 30.0)
            spin.setDecimals(1)
            spin.setSingleStep(0.5)
            spin.setValue(val)
            spin.setMaximumWidth(64)
            spin.valueChanged.connect(
                lambda val_, key_=deltas_key, i_=i: self._set_delta(key_, i_, float(val_))
            )
            col = QVBoxLayout()
            col.addWidget(label, alignment=Qt.AlignmentFlag.AlignCenter)
            col.addWidget(spin)
            spins_row.addLayout(col)
            spins.append(spin)
        v.addLayout(spins_row)

        sum_label = QLabel(self._sum_text(values, mult_value))
        v.addWidget(sum_label)
        # Stash references for live sum updates from delta or multiplier changes.
        slot = deltas_key.replace('.', '_')
        setattr(self, f"_sum_label_{slot}", sum_label)
        setattr(self, f"_mult_spin_{slot}", mult_spin)
        return box

    def _sum_text(self, values: list[float], multiplier: float = 1.0) -> str:
        raw = sum(values)
        if abs(multiplier - 1.0) < 1e-6:
            return f"  Cycle total: {raw:.1f} px"
        return f"  Cycle total: {raw:.1f} × {multiplier:.2f} = {raw * multiplier:.1f} px"

    # ---- handlers -------------------------------------------------------

    def _set(self, key: str, value) -> None:
        """key: dot-path like 'cat.scale'."""
        sub_attr, field_name = key.split(".", 1)
        sub = getattr(self._config, sub_attr)
        setattr(sub, field_name, value)
        self._config.save()
        self._on_change(key)

    def _set_delta(self, key: str, index: int, value: float) -> None:
        sub_attr, field_name = key.split(".", 1)
        sub = getattr(self._config, sub_attr)
        arr = list(getattr(sub, field_name))
        if 0 <= index < len(arr):
            arr[index] = value
            setattr(sub, field_name, arr)
            self._config.save()
            sum_label = getattr(self, f"_sum_label_{key.replace('.', '_')}", None)
            mult_spin = getattr(self, f"_mult_spin_{key.replace('.', '_')}", None)
            mult = mult_spin.value() if mult_spin is not None else 1.0
            if sum_label is not None:
                sum_label.setText(self._sum_text(arr, mult))
            self._on_change(key)

    def _on_pause_toggled(self, paused: bool) -> None:
        self._config.behaviour.debug_paused = bool(paused)
        self._config.save()
        self.step_btn.setEnabled(paused)
        self._on_change("behaviour.debug_paused")

    def _do_step(self) -> None:
        # Step regardless of pause state; fires once per click.
        self._on_step()
