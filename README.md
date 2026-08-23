# 02Route 3D — Multi-Criteria 3D Mobility Studio

[![QGIS 3.28+](https://img.shields.io/badge/QGIS-3.28%2B-blue.svg)](https://qgis.org/)
[![QGIS 4.x / Qt6](https://img.shields.io/badge/QGIS-4.x%20Ready-emerald.svg)](https://qgis.org/)
[![License: GPL-2.0](https://img.shields.io/badge/License-GPL--2.0--or--later-blue.svg)](LICENSE)
[![GitHub Pages](https://img.shields.io/badge/GitHub%20Pages-Live%20Demo-cyan.svg)](https://yusufeminoglu.github.io/zero2route3d/)
[![Documentation](https://img.shields.io/badge/Docs-Manual-blue.svg)](https://yusufeminoglu.github.io/zero2route3d/MANUAL.html)

**02Route 3D** is a multi-criteria 3D spatial mobility and kinematic routing studio for QGIS. It bridges topological network routing with real-world physical terrain realities, biomechanical human energy expenditure (Tobler, Minetti), urban microclimates, and an embedded 60 FPS Three.js WebGL 3D cockpit.

From wheelchair-accessible barrier-free paths (ADA 5% max grade) to emergency hazard evacuation — every route respects slope, heat, energy, and human physiological limits.

---

## 🌐 Live GitHub Pages & Interactive Labs

Explore the interactive web experience at:  
👉 **[https://yusufeminoglu.github.io/zero2route3d/](https://yusufeminoglu.github.io/zero2route3d/)**

- 🏔 **Parallax Kinematic Hero Animation:** Real-time avatar rig adapting to Tobler/Minetti slope physics.
- ⚡ **Interactive MCDA Cost Surface Lab:** Adjust analytical weights ($w_{\text{slope}}$, $w_{\text{heat}}$, $w_{\text{green}}$, $w_{\text{road}}$) to see dynamic least-cost path re-routing.
- 🎯 **Interactive 4D Pareto Frontier Explorer:** Inspect trade-offs between duration, climb, heat dose, and calories across 5 archetypes.
- 📐 **Interactive Tobler Curve:** Mouse crosshair tooltips along the empirical walking velocity function.

---

## 🏃 15 Specialized Mobility Profiles

| Key | Name | Category | Base Speed | Max Slope | Stairs Policy | Smoothness |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `adult` | Standard Adult | Pedestrian | 5.0 km/h | 25.0% | Allowed (1.2× penalty) | 0.2 |
| `senior` | Senior / Elderly | Pedestrian | 3.2 km/h | 10.0% | Heavy Penalty (8.0×) | 0.6 |
| `child` | Child / Safe Walk | Pedestrian | 3.5 km/h | 12.0% | Allowed (2.0×) | 0.4 |
| `stroller` | Stroller / Pram | Pedestrian | 4.0 km/h | 6.0% | Strictly Blocked (100×) | 0.8 |
| `wheelchair` | Wheelchair (ADA) | Pedestrian | 3.8 km/h | 5.0% | Strictly Blocked (1000×) | 0.9 |
| `jogger` | Runner / Jogger | Pedestrian | 9.5 km/h | 20.0% | Allowed (1.5×) | 0.3 |
| `sightseer` | Scenic / Panoramic | Pedestrian | 4.2 km/h | 20.0% | Allowed (1.0×) | 0.3 |
| `night_walk` | Illuminated Night | Pedestrian | 4.8 km/h | 15.0% | Allowed (3.0×) | 0.7 |
| `bicycle` | Commuter Bike | Micromobility | 18.0 km/h | 15.0% | Blocked (50×) | 0.6 |
| `mtb` | Mountain Bike | Micromobility | 16.0 km/h | 28.0% | Allowed (5.0×) | 0.1 |
| `scooter` | E-Scooter | Micromobility | 20.0 km/h | 12.0% | Blocked (100×) | 0.85 |
| `car` | Passenger Car | Vehicle | 50.0 km/h | 25.0% | Blocked (1000×) | 0.5 |
| `delivery_van` | Delivery Van | Vehicle | 42.0 km/h | 18.0% | Blocked (1000×) | 0.6 |
| `truck` | Heavy Freight | Vehicle | 40.0 km/h | 7.0% | Blocked (1000×) | 0.7 |
| `paramedic` | Emergency EMS | Vehicle | 65.0 km/h | 22.0% | Blocked (1000×) | 0.4 |

---

## ⚙️ 14 Headless Processing Algorithms

All algorithms are fully scriptable via QGIS Graphical Modeler, PyQGIS, and standalone Python:

1. `zero2route3d:compute_3d_route` — 3D Least-Cost Route (LineStringZ output).
2. `zero2route3d:service_area_3d` — 3D Isochrones & Wavefront Service Catchments.
3. `zero2route3d:accessibility_equity` — E2SFCA Accessibility & Spatial Justice Scorecard (Gini / Palma).
4. `zero2route3d:multi_criteria_cost_surface` — Multi-Criteria AHP Normalized Friction Surface.
5. `zero2route3d:pareto_3d_routes` — NAMOA* 4D Pareto Frontier Multi-Objective Routing.
6. `zero2route3d:map_matching_3d` — GPS 3D Map Matching via Hidden Markov Models.
7. `zero2route3d:walkability_audit` — ADA Walkability & Cross-Slope Compliance Audit.
8. `zero2route3d:evacuation_routing` — Hazard Evacuation Dynamic Egress Pathfinding.
9. `zero2route3d:solar_exposure` — Solar Shadow & Thermal Radiation Analysis.
10. `zero2route3d:batch_3d_route` — High-Throughput Batch Point-Pair Routing.
11. `zero2route3d:od_matrix_3d` — N×M 3D Origin-Destination Cost Matrix.
12. `zero2route3d:generate_analytical_report` — Publication-grade HTML Audit Scorecard.
13. `zero2route3d:export_route_to_dxf` — AutoCAD DXF 3D Polyline & Elevation Profile Exporter.
14. `zero2route3d:export_standalone_html` — Standalone Offline 3D WebGL HTML Viewer.

---

## 📐 Scientific & Mathematical Foundations

02Route 3D uses established, peer-reviewed mathematical formulations:

- **Tobler's Hiking Function (1993):**
  $$W(s) = 6.0 \cdot \exp\left(-3.5 \cdot |s + 0.05|\right) \cdot \frac{v_{\text{base}}}{5.0} \quad [\text{km/h}]$$
- **Minetti Metabolic Cost (2002):**
  $$C_w(s) = 280.5s^5 - 58.7s^4 - 228.1s^3 - 10.3s^2 + 233.5s + 2.155 \quad [\text{J}/(\text{kg}\cdot\text{m})]$$
- **Keys' 16-Point Bicubic Convolution Spline ($a = -0.5$):**
  Ensures $\mathcal{C}^1$-continuous elevation derivatives across discrete DEM raster pixels.
- **Saaty AHP Consistency Validation:**
  Guarantees multi-criteria pairwise comparison matrix consistency ($CR \le 0.10$).

---

## 💻 Installation

### Method 1: QGIS Plugin Manager (Recommended)
1. Open QGIS (v3.28 or later).
2. Go to **Plugins > Manage and Install Plugins...**
3. Search for **02Route 3D** and click **Install Plugin**.

### Method 2: Install from ZIP
Download the latest `zero2route3d.zip` release and install via **Plugins > Install from ZIP**.

---

## 📄 License & Affiliation

- **Author:** Yusuf Eminoğlu (<yusuf.eminoglu@deu.edu.tr>)
- **Affiliation:** Dokuz Eylül University, Department of City and Regional Planning
- **License:** GNU General Public License v2.0 or later ([GPL-2.0-or-later](LICENSE))
