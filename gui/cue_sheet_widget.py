"""Turn-by-turn navigation cue sheet table widget."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import List, Sequence

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.profile_stats import CueInstruction


class CueSheetWidget(QWidget):
    """Interactive table displaying step-by-step turn-by-turn route navigation cues."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cues: List[CueInstruction] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # Header controls
        hdr_row = QHBoxLayout()
        lbl = QLabel("Turn-by-Turn Navigation Cues")
        lbl.setStyleSheet("font-weight: 700; color: #38bdf8; font-size: 13px;")
        hdr_row.addWidget(lbl)
        hdr_row.addStretch()

        self.btn_export_csv = QPushButton("💾 CSV")
        self.btn_export_csv.clicked.connect(self.export_csv)
        self.btn_export_csv.setEnabled(False)
        hdr_row.addWidget(self.btn_export_csv)

        self.btn_export_html = QPushButton("🌐 HTML")
        self.btn_export_html.clicked.connect(self.export_html)
        self.btn_export_html.setEnabled(False)
        hdr_row.addWidget(self.btn_export_html)

        layout.addLayout(hdr_row)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            "#",
            "Direction",
            "Distance",
            "Elev Δ",
            "Slope %",
            "Instruction",
        ])
        header = self.table.horizontalHeader()
        _ResizeToContents = getattr(getattr(QHeaderView, "ResizeMode", QHeaderView), "ResizeToContents", 3)
        _Stretch = getattr(getattr(QHeaderView, "ResizeMode", QHeaderView), "Stretch", 1)
        header.setSectionResizeMode(0, _ResizeToContents)
        header.setSectionResizeMode(1, _ResizeToContents)
        header.setSectionResizeMode(2, _ResizeToContents)
        header.setSectionResizeMode(3, _ResizeToContents)
        header.setSectionResizeMode(4, _ResizeToContents)
        header.setSectionResizeMode(5, _Stretch)
        self.table.setAlternatingRowColors(True)
        _SelectRows = getattr(getattr(QTableWidget, "SelectionBehavior", QTableWidget), "SelectRows", 1)
        self.table.setSelectionBehavior(_SelectRows)
        layout.addWidget(self.table)

    def load_cues(self, cues: Sequence[CueInstruction]) -> None:
        """Populate table with cue instructions."""
        self.cues = list(cues)
        self.table.setRowCount(len(self.cues))

        dir_icons = {
            "depart": "🚀 Depart",
            "left": "⬅️ Left",
            "right": "➡️ Right",
            "slight_left": "↖️ Bear Left",
            "slight_right": "↗️ Bear Right",
            "straight": "⬆️ Straight",
            "arrive": "🏁 Arrive",
        }

        _AlignCenter = getattr(getattr(Qt, "AlignmentFlag", Qt), "AlignCenter", 0x0084)
        _AlignRight = getattr(getattr(Qt, "AlignmentFlag", Qt), "AlignRight", 0x0002)
        _AlignVCenter = getattr(getattr(Qt, "AlignmentFlag", Qt), "AlignVCenter", 0x0080)

        for row, c in enumerate(self.cues):
            step_no = getattr(c, "step_number", row + 1)
            item_num = QTableWidgetItem(str(step_no))
            item_num.setTextAlignment(_AlignCenter)
            self.table.setItem(row, 0, item_num)

            direction = getattr(c, "direction", "straight")
            dir_label = dir_icons.get(str(direction), str(direction))
            item_dir = QTableWidgetItem(str(dir_label))
            self.table.setItem(row, 1, item_dir)

            try:
                dist_val = float(getattr(c, "distance_m", 0.0))
            except (ValueError, TypeError):
                dist_val = 0.0
            item_dist = QTableWidgetItem(f"{dist_val:.0f} m")
            item_dist.setTextAlignment(_AlignRight | _AlignVCenter)
            self.table.setItem(row, 2, item_dist)

            try:
                dz_val = float(getattr(c, "elevation_delta_m", 0.0))
            except (ValueError, TypeError):
                dz_val = 0.0
            item_dz = QTableWidgetItem(f"{dz_val:+.1f} m")
            item_dz.setTextAlignment(_AlignRight | _AlignVCenter)
            self.table.setItem(row, 3, item_dz)

            try:
                slope_val = float(getattr(c, "slope_pct", 0.0))
            except (ValueError, TypeError):
                slope_val = 0.0
            item_slope = QTableWidgetItem(f"{slope_val:.1f}%")
            item_slope.setTextAlignment(_AlignRight | _AlignVCenter)
            if abs(slope_val) > 10.0:
                item_slope.setForeground(QColor("#dc2626"))
            self.table.setItem(row, 4, item_slope)

            instr_text = str(getattr(c, "instruction", ""))
            warning = str(getattr(c, "warning", ""))
            if warning:
                instr_text += f" [{warning}]"
            item_instr = QTableWidgetItem(str(instr_text))
            self.table.setItem(row, 5, item_instr)

        self.btn_export_csv.setEnabled(len(self.cues) > 0)
        self.btn_export_html.setEnabled(len(self.cues) > 0)

    def export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export Navigation Cues to CSV", "", "CSV Files (*.csv)")
        if path:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Step", "Direction", "Distance_m", "Elevation_Delta_m", "Slope_Pct", "Instruction", "Warning"])
                for c in self.cues:
                    writer.writerow([c.step_number, c.direction, c.distance_m, c.elevation_delta_m, c.slope_pct, c.instruction, c.warning])
            QMessageBox.information(self, "Export Successful", f"Saved navigation cues to {path}")

    def export_html(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export Navigation Cues to HTML", "", "HTML Files (*.html)")
        if path:
            rows_html = "".join(
                f"<tr><td>{c.step_number}</td><td>{c.direction}</td><td>{c.distance_m:.0f} m</td><td>{c.elevation_delta_m:+.1f} m</td><td>{c.slope_pct:.1f}%</td><td>{c.instruction}</td></tr>"
                for c in self.cues
            )
            html_content = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>02Route 3D — Navigation Cue Sheet</title>
  <style>
    body {{ font-family: sans-serif; margin: 24px; background: #0f172a; color: #f8fafc; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 16px; }}
    th, td {{ padding: 10px 14px; border: 1px solid #334155; text-align: left; }}
    th {{ background: #1e293b; color: #38bdf8; }}
    tr:nth-child(even) {{ background: #1e293b; }}
  </style>
</head>
<body>
  <h2>02Route 3D — Turn-by-Turn Navigation Cue Sheet</h2>
  <table>
    <thead>
      <tr><th>#</th><th>Direction</th><th>Distance</th><th>Elev Δ</th><th>Slope</th><th>Instruction</th></tr>
    </thead>
    <tbody>
      {rows_html}
    </tbody>
  </table>
</body>
</html>"""
            Path(path).write_text(html_content, encoding="utf-8")
            QMessageBox.information(self, "Export Successful", f"Saved HTML cue sheet to {path}")
