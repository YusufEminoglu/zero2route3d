# 02Route 3D — Multi-Criteria 3D Mobility Studio

**02Route 3D** is an advanced, hardware-accelerated 3D route planning and mobility analytics studio for QGIS. It combines multi-criteria least-cost path calculations, biomechanical kinematics (Tobler, Minetti), and an embedded 60 FPS WebGL 3D viewport right inside the QGIS interface.

---

## Key Features

- 🚶‍♂️ **9 Specialized Mobility Profiles:**
  - **Standard Adult:** Balanced walking speed and energy distribution.
  - **Senior / Elderly:** Reduced speed, steep slope avoidance, cool & shaded path preference.
  - **Woman with Stroller / Pram:** Strict stair prohibition, ramp preference, max 6% grade cutoff.
  - **Child / Family:** Safe street prioritization, crossing penalties.
  - **Wheelchair / Barrier-Free:** Strict max 5% slope, zero steps/curbs, smooth pavement.
  - **Bicycle:** Gradient power calculation, dedicated cycling network preference.
  - **Micromobility / E-Scooter:** Surface roughness penalties, 12% grade cutoff.
  - **Passenger Car:** Road hierarchy routing and speed regulations.
  - **Heavy Logistics / Truck:** Bridge height/width constraints and steep grade cutoffs.

- 🏔️ **Multi-Criteria Environmental Resistance Surfaces:**
  - Integrates 3D Digital Elevation Models (DEM), calculated slope %, surface temperature (LST), tree shade, and road hierarchy into weighted impedance.
  - Works out of the box with zero external configuration (auto-fetches network and global terrain) or seamlessly binds to active QGIS raster/vector layers.

- 🎮 **Embedded 60 FPS 3D WebGL Experience (Docked inside QGIS):**
  - Glowing 3D Ribbon colored by slope gradient or thermal comfort.
  - Real-time animated avatar traversal with **Orbit**, **Chase / Follow**, and **First-Person / Driver** camera modes.
  - Interactive Elevation Profile HUD with crosshair laser sync on the 3D map.

- 📦 **Rich Export Options:**
  - Native 3D Vector Layer (`LineStringZ` in GeoPackage).
  - 3D GPX and GeoJSON files.
  - Standalone, self-contained offline 3D HTML web application.

---

## Author & License

- **Author:** Yusuf Eminoğlu (<yusuf.eminoglu@deu.edu.tr>)
- **Affiliation:** Dokuz Eylül University, Department of City and Regional Planning
- **License:** GNU General Public License v2.0 or later (GPL-2.0-or-later)
