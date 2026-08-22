"""AutoCAD DXF 3D Polyline and Longitudinal Profile Exporter.

Generates standard ASCII DXF (R12/2000) files containing 3D polyline route geometry
and cross-sectional elevation profile drawings for CAD / BIM interoperability.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Sequence, Tuple

from .kinematics import haversine_distance_2d


def export_route_to_dxf_3d(
    coords_3d: Sequence[Tuple[float, float, float]],
    target_path: Path,
    include_profile_section: bool = True,
    profile_vertical_scale: float = 5.0,
    layer_name: str = "3D_ROUTE",
) -> None:
    """Write 3D route coordinates to standard AutoCAD DXF file."""
    if len(coords_3d) < 2:
        return

    lines = [
        "0",
        "SECTION",
        "2",
        "HEADER",
        "9",
        "$ACADVER",
        "1",
        "AC1009",  # AutoCAD R12 compatibility
        "0",
        "ENDSEC",
        "0",
        "SECTION",
        "2",
        "TABLES",
        "0",
        "TABLE",
        "2",
        "LAYER",
        "70",
        "2",
        "0",
        "LAYER",
        "2",
        layer_name,
        "70",
        "0",
        "62",
        "4",  # Cyan color
        "6",
        "CONTINUOUS",
        "0",
        "LAYER",
        "2",
        "PROFILE_GRID",
        "70",
        "0",
        "62",
        "1",  # Red color
        "6",
        "CONTINUOUS",
        "0",
        "ENDTAB",
        "0",
        "ENDSEC",
        "0",
        "SECTION",
        "2",
        "ENTITIES",
    ]

    # 1. 3D Polyline Entity
    lines.extend([
        "0",
        "POLYLINE",
        "8",
        layer_name,
        "66",
        "1",  # Vertices follow
        "70",
        "8",  # 3D Polyline flag
        "10",
        "0.0",
        "20",
        "0.0",
        "30",
        "0.0",
    ])

    for pt in coords_3d:
        lines.extend([
            "0",
            "VERTEX",
            "8",
            layer_name,
            "70",
            "32",  # 3D Polyline vertex
            "10",
            f"{pt[0]:.6f}",
            "20",
            f"{pt[1]:.6f}",
            "30",
            f"{pt[2]:.2f}",
        ])

    lines.extend(["0", "SEQEND"])

    # 2. Longitudinal Profile Drawing in Model Space (Offset to the side)
    if include_profile_section and len(coords_3d) >= 2:
        accum_dist = 0.0
        min_elev = min(p[2] for p in coords_3d)

        # Baseline offset for profile drawing
        x_base = coords_3d[0][0]
        y_base = coords_3d[0][1] - 0.02

        profile_pts = []
        for i in range(len(coords_3d)):
            if i > 0:
                d = haversine_distance_2d(coords_3d[i - 1], coords_3d[i])
                accum_dist += d
            dz = coords_3d[i][2] - min_elev
            px = x_base + (accum_dist / 111320.0)
            py = y_base + ((dz * profile_vertical_scale) / 110574.0)
            profile_pts.append((px, py, 0.0))

        # Profile Polyline
        lines.extend([
            "0",
            "POLYLINE",
            "8",
            "PROFILE_GRID",
            "66",
            "1",
            "70",
            "0",
            "10",
            "0.0",
            "20",
            "0.0",
            "30",
            "0.0",
        ])

        for p in profile_pts:
            lines.extend([
                "0",
                "VERTEX",
                "8",
                "PROFILE_GRID",
                "10",
                f"{p[0]:.6f}",
                "20",
                f"{p[1]:.6f}",
                "30",
                "0.0",
            ])

        lines.extend(["0", "SEQEND"])

    # Close Entities & File
    lines.extend([
        "0",
        "ENDSEC",
        "0",
        "EOF",
    ])

    target_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
