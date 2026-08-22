"""Adaptive Qt Theme & QSS Stylesheet Manager for 02Route 3D."""
from __future__ import annotations

from qgis.PyQt.QtGui import QColor, QPalette
from qgis.PyQt.QtWidgets import QApplication, QWidget


def _hex(color: QColor) -> str:
    return color.name(QColor.NameFormat.HexRgb)


def _mix(first: QColor, second: QColor, amount: float) -> QColor:
    amount = max(0.0, min(1.0, float(amount)))
    return QColor(
        round(first.red() * (1.0 - amount) + second.red() * amount),
        round(first.green() * (1.0 - amount) + second.green() * amount),
        round(first.blue() * (1.0 - amount) + second.blue() * amount),
    )


def dock_color_tokens(palette: QPalette | None = None) -> dict[str, str]:
    """Derive coherent UI color tokens from active QGIS/Qt palette."""
    active = palette or QApplication.palette()
    window = active.color(QPalette.ColorRole.Window)
    base = active.color(QPalette.ColorRole.Base)
    text = active.color(QPalette.ColorRole.WindowText)
    input_text = active.color(QPalette.ColorRole.Text)
    dark = window.lightness() < 128

    white = QColor("#FFFFFF")
    accent_cyan = QColor("#38BDF8" if dark else "#0284C7")
    accent_emerald = QColor("#34D399" if dark else "#059669")
    accent_rose = QColor("#FB7185" if dark else "#E11D48")

    surface = _mix(window, white, 0.08) if dark else _mix(window, base, 0.70)
    card = _mix(window, white, 0.14) if dark else base
    border = _mix(text, surface, 0.75 if dark else 0.85)
    subtle = _mix(text, surface, 0.40 if dark else 0.40)
    primary_btn = QColor("#0EA5E9" if dark else "#0284C7")

    return {
        "window": _hex(window),
        "surface": _hex(surface),
        "card": _hex(card),
        "text": _hex(text),
        "input_text": _hex(input_text),
        "subtle": _hex(subtle),
        "border": _hex(border),
        "accent_cyan": _hex(accent_cyan),
        "accent_emerald": _hex(accent_emerald),
        "accent_rose": _hex(accent_rose),
        "primary_btn": _hex(primary_btn),
    }


def build_dock_qss(palette: QPalette | None = None) -> str:
    """Generate modern, card-based QSS stylesheet for the studio dock."""
    c = dock_color_tokens(palette)
    return f"""
    QWidget#route3dRoot {{
        background: {c['window']};
        color: {c['text']};
    }}
    QScrollArea#route3dScrollArea {{
        background: transparent;
        border: none;
    }}
    QFrame.route3dCard {{
        background: {c['card']};
        border: 1px solid {c['border']};
        border-radius: 10px;
        margin-bottom: 8px;
    }}
    QLabel.route3dCardTitle {{
        font-weight: 700;
        font-size: 13px;
        color: {c['accent_cyan']};
    }}
    QLabel.route3dSubtle {{
        color: {c['subtle']};
        font-size: 11px;
    }}
    QPushButton.route3dPrimaryBtn {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {c['primary_btn']}, stop:1 #2563eb);
        color: #ffffff;
        font-weight: 700;
        font-size: 13px;
        padding: 10px 16px;
        border: none;
        border-radius: 8px;
    }}
    QPushButton.route3dPrimaryBtn:hover {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #38bdf8, stop:1 #3b82f6);
    }}
    QPushButton.route3dToolBtn {{
        background: {c['surface']};
        border: 1px solid {c['border']};
        color: {c['text']};
        padding: 6px 10px;
        border-radius: 6px;
        font-weight: 600;
        font-size: 11px;
    }}
    QPushButton.route3dToolBtn:hover {{
        background: {c['card']};
        border-color: {c['accent_cyan']};
    }}
    QPushButton.route3dToolBtn:checked {{
        background: {c['accent_cyan']};
        color: #0f172a;
        font-weight: 700;
    }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background: {c['surface']};
        color: {c['input_text']};
        border: 1px solid {c['border']};
        border-radius: 6px;
        padding: 5px 8px;
        font-size: 12px;
    }}
    QLineEdit:focus, QComboBox:focus {{
        border-color: {c['accent_cyan']};
    }}
    QSlider::groove:horizontal {{
        height: 6px;
        background: {c['surface']};
        border-radius: 3px;
        border: 1px solid {c['border']};
    }}
    QSlider::sub-page:horizontal {{
        background: {c['accent_cyan']};
        border-radius: 3px;
    }}
    QSlider::handle:horizontal {{
        background: #ffffff;
        border: 2px solid {c['accent_cyan']};
        width: 14px;
        margin-top: -5px;
        margin-bottom: -5px;
        border-radius: 7px;
    }}
    QGroupBox {{
        border: 1px solid {c['border']};
        border-radius: 8px;
        margin-top: 10px;
        font-weight: 600;
        font-size: 11px;
        color: {c['subtle']};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 4px;
    }}
    """


def apply_adaptive_theme(widget: QWidget) -> None:
    """Apply the adaptive styling to the target widget."""
    widget.setStyleSheet(build_dock_qss(widget.palette()))
