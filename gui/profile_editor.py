"""Custom mobility profile builder and preset editor dialog."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.mobility_profiles import MobilityProfile, PROFILES, load_custom_profile_json, save_custom_profile_json


class ProfileEditorDialog(QDialog):
    """Interactive modal dialog to customize or build new mobility profiles."""

    def __init__(self, parent: Optional[QWidget] = None, base_profile_key: str = "adult") -> None:
        super().__init__(parent)
        self.setWindowTitle("02Route 3D — Mobility Profile Builder")
        self.resize(460, 480)

        base_p = PROFILES.get(base_profile_key, PROFILES["adult"])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        # Title
        lbl_title = QLabel("Mobility Profile & Kinematic Constraints")
        lbl_title.setStyleSheet("font-weight: 700; font-size: 14px; color: #38bdf8;")
        layout.addWidget(lbl_title)

        form = QFormLayout()
        form.setSpacing(8)

        self.txt_key = QLineEdit(f"custom_{base_p.key}")
        form.addRow("Profile Identifier (ID):", self.txt_key)

        self.txt_name = QLineEdit(f"Custom {base_p.name}")
        form.addRow("Display Name:", self.txt_name)

        self.cmb_category = QComboBox()
        self.cmb_category.addItems(["pedestrian", "micromobility", "vehicle"])
        self.cmb_category.setCurrentText(base_p.category)
        form.addRow("Transport Category:", self.cmb_category)

        self.spin_speed = QDoubleSpinBox()
        self.spin_speed.setRange(0.5, 150.0)
        self.spin_speed.setValue(base_p.base_speed_kmh)
        self.spin_speed.setSuffix(" km/h")
        form.addRow("Base Cruising Speed:", self.spin_speed)

        self.spin_max_slope = QDoubleSpinBox()
        self.spin_max_slope.setRange(1.0, 60.0)
        self.spin_max_slope.setValue(base_p.max_slope_pct)
        self.spin_max_slope.setSuffix(" %")
        form.addRow("Max Slope Limit:", self.spin_max_slope)

        self.chk_stairs = QCheckBox("Allow Stairways / Steps")
        self.chk_stairs.setChecked(base_p.stair_allowed)
        form.addRow("Stairway Access:", self.chk_stairs)

        self.spin_slope_sens = QDoubleSpinBox()
        self.spin_slope_sens.setRange(0.1, 10.0)
        self.spin_slope_sens.setValue(base_p.slope_sensitivity)
        form.addRow("Slope Impedance Exponent:", self.spin_slope_sens)

        self.spin_heat_sens = QDoubleSpinBox()
        self.spin_heat_sens.setRange(0.0, 1.0)
        self.spin_heat_sens.setSingleStep(0.05)
        self.spin_heat_sens.setValue(base_p.heat_sensitivity)
        form.addRow("Heat (LST) Sensitivity:", self.spin_heat_sens)

        self.spin_green_pref = QDoubleSpinBox()
        self.spin_green_pref.setRange(0.0, 1.0)
        self.spin_green_pref.setSingleStep(0.05)
        self.spin_green_pref.setValue(base_p.green_preference)
        form.addRow("Green Canopy Preference:", self.spin_green_pref)

        self.txt_desc = QLineEdit(base_p.description)
        form.addRow("Description:", self.txt_desc)

        layout.addLayout(form)

        # Buttons
        btn_box = QHBoxLayout()
        self.btn_export = QPushButton("💾 Save Preset (JSON)")
        self.btn_export.clicked.connect(self.export_preset)
        btn_box.addWidget(self.btn_export)

        self.btn_import = QPushButton("📂 Load Preset")
        self.btn_import.clicked.connect(self.import_preset)
        btn_box.addWidget(self.btn_import)

        btn_box.addStretch()

        self.btn_ok = QPushButton("Apply to Studio")
        self.btn_ok.setStyleSheet("background: #0284c7; color: white; font-weight: 700; padding: 6px 14px; border-radius: 6px;")
        self.btn_ok.clicked.connect(self.accept)
        btn_box.addWidget(self.btn_ok)

        layout.addLayout(btn_box)

    def get_built_profile(self) -> MobilityProfile:
        """Construct and return the MobilityProfile dataclass instance."""
        return MobilityProfile(
            key=self.txt_key.text().strip().lower() or "custom",
            name=self.txt_name.text().strip() or "Custom Profile",
            category=self.cmb_category.currentText(),
            base_speed_kmh=self.spin_speed.value(),
            max_slope_pct=self.spin_max_slope.value(),
            stair_allowed=self.chk_stairs.isChecked(),
            stair_penalty=1.2 if self.chk_stairs.isChecked() else 1000.0,
            slope_sensitivity=self.spin_slope_sens.value(),
            heat_sensitivity=self.spin_heat_sens.value(),
            green_preference=self.spin_green_pref.value(),
            surface_smoothness_req=0.5,
            description=self.txt_desc.text().strip(),
            icon_name="custom",
        )

    def export_preset(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export Mobility Profile Preset", "", "JSON Files (*.json)")
        if path:
            p = self.get_built_profile()
            save_custom_profile_json(p, Path(path))
            QMessageBox.information(self, "Export Successful", f"Saved profile preset to {path}")

    def import_preset(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load Mobility Profile Preset", "", "JSON Files (*.json)")
        if path:
            p = load_custom_profile_json(Path(path))
            self.txt_key.setText(p.key)
            self.txt_name.setText(p.name)
            self.cmb_category.setCurrentText(p.category)
            self.spin_speed.setValue(p.base_speed_kmh)
            self.spin_max_slope.setValue(p.max_slope_pct)
            self.chk_stairs.setChecked(p.stair_allowed)
            self.spin_slope_sens.setValue(p.slope_sensitivity)
            self.spin_heat_sens.setValue(p.heat_sensitivity)
            self.spin_green_pref.setValue(p.green_preference)
            self.txt_desc.setText(p.description)
            QMessageBox.information(self, "Import Successful", f"Loaded profile '{p.name}'")
