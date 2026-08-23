"""Version-safe QgsField construction.

Every algorithm declared its output fields as ``QgsField("dist_km", 6)``, relying
on the bare integer matching the old ``QVariant`` enum value. That does not work
on QGIS 4 / PyQt6: the int overload is gone and the call raises

    TypeError: QgsField(): arguments did not match any overloaded call

so every sink-producing algorithm failed on QGIS 4.x before writing a feature.
The plugin's own test suites never called processAlgorithm, so this went unseen.

``QgsField(name, QMetaType.Type.X)`` exists only from QGIS 3.38, and the plugin
declares qgisMinimumVersion 3.28, so the overload is probed once at import and
the ``QVariant`` form is used when it is unavailable (TRAPS 3.2).
"""
from __future__ import annotations

import contextlib

from qgis.core import QgsField

# Logical type names, so call sites stop carrying magic numbers.
STRING = "string"
DOUBLE = "double"
INT = "int"
BOOL = "bool"

# Legacy QVariant integers, kept only to translate the old call sites.
_LEGACY_INT_TO_NAME = {
    1: BOOL,
    2: INT,
    6: DOUBLE,
    10: STRING,
}


def _build_factory():
    """Return a callable (name, type_name) -> QgsField that works on this QGIS."""
    metatypes = None
    with contextlib.suppress(Exception):
        from qgis.PyQt.QtCore import QMetaType

        candidate = {
            STRING: QMetaType.Type.QString,
            DOUBLE: QMetaType.Type.Double,
            INT: QMetaType.Type.Int,
            BOOL: QMetaType.Type.Bool,
        }
        # Importing QMetaType proves nothing; the overload must actually accept it.
        QgsField("probe", candidate[STRING])
        metatypes = candidate

    if metatypes is not None:
        return lambda name, type_name: QgsField(name, metatypes[type_name])

    from qgis.PyQt.QtCore import QVariant

    variants = {
        STRING: QVariant.String,
        DOUBLE: QVariant.Double,
        INT: QVariant.Int,
        BOOL: QVariant.Bool,
    }
    return lambda name, type_name: QgsField(name, variants[type_name])


_FACTORY = _build_factory()


def make_field(name: str, field_type) -> QgsField:
    """Build a QgsField from a logical type name, or a legacy QVariant integer."""
    if isinstance(field_type, int):
        type_name = _LEGACY_INT_TO_NAME.get(field_type)
        if type_name is None:
            raise ValueError(f"unsupported legacy field type {field_type!r} for {name!r}")
    else:
        type_name = field_type
    return _FACTORY(name, type_name)
