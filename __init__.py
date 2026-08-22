"""02Route 3D — Multi-Criteria 3D Mobility Studio."""
from __future__ import annotations

from typing import Any


def classFactory(iface: Any):
    """Load Route3DPlugin class from main_plugin."""
    from .main_plugin import Route3DPlugin

    return Route3DPlugin(iface)
