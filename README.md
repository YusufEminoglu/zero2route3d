<h1 align="center">02Route 3D — Multi-Criteria 3D Mobility Studio</h1>

<p align="center">
 <strong>Routes that understand terrain, body, and urban microclimates.</strong>
</p>

<p align="center">
 A multi-criteria 3D spatial mobility and kinematic routing studio for QGIS. Combines 15 specialized mobility profiles, 15 headless Processing algorithms, profile-aware road access, biomechanical human energy expenditure (Tobler, Minetti), raster resistance surfaces, and an embedded Three.js WebGL 3D cockpit.
</p>

<p align="center">
 <a href="metadata.txt"><img alt="QGIS" src="https://img.shields.io/badge/QGIS-3.28%2B%20LTR%20%7C%204.x%20Ready-5da85d?style=for-the-badge"></a>
 <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-GPL--2.0--or--later-111827?style=for-the-badge"></a>
 <a href="https://yusufeminoglu.github.io/zero2route3d/"><img alt="Interactive Landing Page" src="https://img.shields.io/badge/demo-Interactive_Labs-06b6d4?style=for-the-badge"></a>
 <a href="https://yusufeminoglu.github.io/zero2route3d/MANUAL.html"><img alt="Reference Manual" src="https://img.shields.io/badge/docs-Reference_Manual-10b981?style=for-the-badge"></a>
 <img alt="Three.js WebGL" src="https://img.shields.io/badge/viewer-WebGL-2f4858?style=for-the-badge">
</p>

<p align="center">
 <a href="#the-product-promise">Product Promise</a> |
 <a href="#quick-start">Quick Start</a> |
 <a href="#why-it-matters">Why It Matters</a> |
 <a href="#signature-features">Signature Features</a> |
 <a href="#mobility-profiles-catalog">15 Profiles</a> |
 <a href="#processing-algorithms-catalog">15 Algorithms</a> |
 <a href="#scientific--mathematical-foundations">Science &amp; Math</a> |
 <a href="#viewer-experience">WebGL Studio</a> |
 <a href="#showcase-playbook">Showcase Playbook</a> |
 <a href="#repository-map">Repo Map</a> |
 <a href="#-part-of-the-planx-ecosystem">PlanX Ecosystem</a>
</p>

---

## 📖 Live Documentation & Interactive Labs

- 🌐 **[Interactive GitHub Pages & Web Labs](https://yusufeminoglu.github.io/zero2route3d/)** — Multi-layer parallax hero animation with real-time avatar rig slope physics, interactive MCDA cost surface lab, 4D Pareto trade-off explorer, and anisotropic 3D isochrone wavefront simulator.
- 📚 **[Comprehensive Technical Reference Manual](https://yusufeminoglu.github.io/zero2route3d/MANUAL.html)** — Full technical specification for all 15 mobility profiles, 15 Processing algorithms, PyQGIS automation snippets, and troubleshooting guide.

---

## The Product Promise

Traditional GIS shortest-path engines treat cities as flat Euclidean planes, completely overlooking the punishing physiological impact of steep grades, extreme urban heat islands, and barrier-rich streetscapes.

**02Route 3D** transforms 2D vector street networks and digital elevation models (DEMs) into physically realistic 3D kinetic graphs directly inside QGIS:

1. **Topological 3D Graph Construction:** Drapes 2D vector centerlines over discrete DEM rasters using Keys' 16-point bicubic convolution spline with analytical $\mathcal{C}^1$ slope and aspect derivatives.
2. **Multi-Criteria Environmental Impedance:** Fuses DEM slope, Land Surface Temperature (LST), NDVI vegetation canopy, and road hierarchy into a normalized AHP friction surface ($CR \le 0.10$).
3. **Biomechanical Human Kinematics:** Dynamically computes travel velocity using Tobler's hiking function and metabolic energy cost using Minetti's 5th-order polynomial equations.
4. **Multi-Objective Pareto Optimization:** Solves NAMOA* 4D non-dominated trade-offs across travel time, cumulative climb, thermal heat dose, and calories.
5. **Universal Accessibility Auditing:** Enforces strict ADA barrier-free thresholds (5% maximum grade, stair blocking, surface smoothness).
6. **Embedded WebGL 3D Studio:** Inspect routes with chase/orbit/driver cameras, environmental metric ribbons, terrain slicing, and longitudinal profile HUDs.
7. **Profile-Aware Network Safety:** Honors OSM and QGIS-layer access, foot, bicycle, motor-vehicle, one-way, surface, lighting, and road-class attributes before an edge enters a route.

---

## Quick Start

### Install for development

```powershell
$env:QGIS_PLUGINPATH = "C:\Users\YE\PyCharmMiscProject\qgis_plugins"
```

Restart QGIS, enable **02Route 3D** in **Plugins > Installed**, then open the studio dock from the toolbar or plugin menu.

### Use it inside QGIS

1. **Select Network & DEM:** In Tab 1 (Network & Elevation), select your street vector layer and DEM raster, or click **"⛰️ Fetch Full Map Extent Elevation DEM"** to query real 30m topography for the active map extent from the Open-Elevation API.
2. **Select Origin & Destination:** Use the map canvas picker tools to select Point A (Origin) and Point B (Destination).
3. **Pick Mobility Profile:** Choose from 15 profiles (e.g. *Wheelchair ADA*, *Senior*, *Commuter Bike*, *Emergency EMS*).
4. **Tune AHP Weights (Optional):** In Tab 2 (MCDA & AHP), adjust the relative weights for Slope, Heat Island, Greenery, and Road Hierarchy.
5. **Compute & Inspect:** Click **"Compute 3D Least-Cost Route"** to generate the 3D `LineStringZ` layer and launch the embedded WebGL 3D Studio.

---

## Why It Matters

| Need | How 02Route 3D Solves It |
| :--- | :--- |
| **Realistic Walking Times** | Replaces static walking speeds with Tobler's empirical hiking formula ($W(s) = 6.0 \cdot e^{-3.5|s+0.05|}$), slowing uphill travel and speeding gentle descents. |
| **Physical Exhaustion Modeling** | Calculates exact metabolic kilocalorie expenditure using Minetti's 5th-order polynomial equation per kilogram per meter traveled. |
| **ADA & Barrier-Free Planning** | Enforces strict 5% grade limits, stair blocking penalties ($\times 1000$), and smooth pavement requirements for wheelchair and stroller accessibility. |
| **Urban Heat Island Protection** | Routes pedestrian and vulnerable populations through shaded green canopy corridors, actively penalizing extreme LST surface temperatures. |
| **Multi-Objective Trade-offs** | Uses NAMOA* 4D Pareto optimization so planners can choose between Fastest, Flattest, Coolest, and Lowest-Energy routes. |
| **Spatial Justice Analysis** | Calculates E2SFCA healthcare and amenity accessibility with Gini coefficients, Lorenz curves, and Palma spatial equity ratios. |
| **CAD / GIS Interoperability** | Exports native 3D `LineStringZ` GeoPackages, GPX 1.1 with elevation, GeoJSON 3D, and AutoCAD DXF (AC1009) 3D Polylines with longitudinal profile drawings. |

---

## Signature Features

### 🚶 15 Calibrated Mobility Profiles

The engine features 15 distinct mobility models across Pedestrian, Micromobility, Vehicle, and Emergency modes:

| Key | Profile Name | Category | Base Speed | Max Slope | Stairs Policy | Smoothness Req |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `adult` | Standard Adult | Pedestrian | 5.0 km/h | 25.0% | Allowed (1.2× penalty) | 0.2 |
| `senior` | Senior / Elderly | Pedestrian | 3.2 km/h | 10.0% | Heavy Penalty (8.0×) | 0.6 |
| `child` | Child / Safe Walk | Pedestrian | 3.5 km/h | 12.0% | Allowed (2.0× penalty) | 0.4 |
| `stroller` | Stroller / Pram | Pedestrian | 4.0 km/h | 6.0% | Strictly Blocked (100×) | 0.8 |
| `wheelchair` | Wheelchair (ADA) | Pedestrian | 3.8 km/h | 5.0% | Strictly Blocked (1000×) | 0.9 |
| `jogger` | Runner / Jogger | Pedestrian | 9.5 km/h | 20.0% | Allowed (1.5× penalty) | 0.3 |
| `sightseer` | Scenic / Panoramic | Pedestrian | 4.2 km/h | 20.0% | Allowed (1.0× penalty) | 0.3 |
| `night_walk` | Illuminated Night | Pedestrian | 4.8 km/h | 15.0% | Allowed (3.0× penalty) | 0.7 |
| `bicycle` | Commuter Bike | Micromobility | 18.0 km/h | 15.0% | Blocked (50× penalty) | 0.6 |
| `mtb` | Mountain Bike | Micromobility | 16.0 km/h | 28.0% | Allowed (5.0× penalty) | 0.1 |
| `scooter` | E-Scooter | Micromobility | 20.0 km/h | 12.0% | Blocked (100× penalty) | 0.85 |
| `car` | Passenger Car | Vehicle | 50.0 km/h | 25.0% | Blocked (1000× penalty) | 0.5 |
| `delivery_van` | Delivery Van | Vehicle | 42.0 km/h | 18.0% | Blocked (1000× penalty) | 0.6 |
| `truck` | Heavy Logistics | Vehicle | 40.0 km/h | 7.0% | Blocked (1000× penalty) | 0.7 |
| `paramedic` | Emergency EMS | Vehicle | 65.0 km/h | 22.0% | Blocked (1000× penalty) | 0.4 |

---

### ⚙️ 15 Headless Processing Algorithms

Every analytical module is fully scriptable in QGIS Graphical Modeler, PyQGIS, and standalone Python:

| Algorithm Name | Processing ID | Key Output | Tags |
| :--- | :--- | :--- | :--- |
| **3D Least-Cost Route** | `zero2route3d:compute_3d_route` | `LineStringZ` layer | Routing, 3D |
| **3D Isochrones** | `zero2route3d:generate_3d_isochrone` | Multi-tier Polygon layer | Catchment, Wavefront |
| **E2SFCA Accessibility Score** | `zero2route3d:accessibility_equity_scorecard` | Equity Score Point/Grid | Equity, Justice, Gini |
| **Multi-Criteria Cost Surface** | `zero2route3d:mcda_cost_surface` | Normalized Friction Raster | Raster, AHP, MCDA |
| **NAMOA\* Pareto Routing** | `zero2route3d:pareto_3d_routes` | Non-Dominated Routes | Pareto, Multi-Objective |
| **GPS 3D Map Matching** | `zero2route3d:map_match_3d_track` | Snapped 3D Track Layer | GPX, HMM, Viterbi |
| **ADA Walkability Audit** | `zero2route3d:walkability_3d_audit` | Compliance Gradient Audit | Audit, ADA, Barrier-Free |
| **Hazard Evacuation Routing** | `zero2route3d:emergency_evacuation_3d` | Dynamic Egress Paths | Emergency, Hazard |
| **Solar Exposure Analysis** | `zero2route3d:solar_shade_exposure` | Sun/Shade Polyline Layer | Climate, Solar, Shade |
| **Batch 3D Routing** | `zero2route3d:batch_3d_routes` | Batch Route Vector Layer | Batch, Vector, OD |
| **3D OD Cost Matrix** | `zero2route3d:od_matrix_3d` | N×M Matrix CSV / Table | Matrix, OD, Distance |
| **Analytical Report Generator** | `zero2route3d:generate_route_report_html` | HTML Scorecard Document | Report, HTML, SVG |
| **Export Route to AutoCAD DXF** | `zero2route3d:export_3d_route_dxf` | AutoCAD DXF (AC1009) | CAD, DXF, Polyline |
| **Export Standalone 3D HTML** | `zero2route3d:export_3d_html_report` | Self-Contained HTML | Export, WebGL, 3D |
| **Routing Network Readiness Audit** | `zero2route3d:audit_routing_network` | Annotated LineStringZ network | Topology, Access, QA |

---

## Scientific & Mathematical Foundations

02Route 3D avoids arbitrary heuristics, implementing established peer-reviewed mathematical models:

### 1. Tobler's Hiking Velocity Function
Predicts travel velocity based on fractional ground slope $s = \frac{\Delta z}{\Delta x}$:
$$W(s) = 6.0 \cdot \exp\left(-3.5 \cdot |s + 0.05|\right) \cdot \frac{v_{\text{base}}}{5.0} \quad [\text{km/h}]$$

### 2. Minetti Metabolic Energy Expenditure
Quantifies physical exertion $C_w(s)$ in Joules per kilogram per meter traveled:
$$C_w(s) = 280.5s^5 - 58.7s^4 - 228.1s^3 - 10.3s^2 + 233.5s + 2.155 \quad [\text{J}/(\text{kg}\cdot\text{m})]$$
$$E_{\text{kcal}} = \frac{C_w(s) \cdot m_{\text{kg}} \cdot d_{\text{m}}}{4184.0}$$

### 3. Keys' 16-Point Bicubic Convolution Spline
Guarantees $\mathcal{C}^1$-continuous elevation derivatives ($a = -0.5$) across discrete raster cells:
$$W(x) = \begin{cases} (a+2)|x|^3 - (a+3)|x|^2 + 1 & |x| \le 1 \\ a|x|^3 - 5a|x|^2 + 8a|x| - 4a & 1 < |x| < 2 \\ 0 & \text{otherwise} \end{cases}$$

### 4. Saaty AHP Consistency Ratio
Validates pairwise environmental judgment matrices to guarantee mathematical consistency:
$$CR = \frac{CI}{RI} = \frac{(\lambda_{\max} - n) / (n-1)}{RI(n)} \le 0.10$$

### 5. E2SFCA Gaussian Spatial Accessibility Decay
Models continuous distance decay discounting spatial equity based on 3D traversal friction:
$$f(d) = \frac{\exp\left(-\frac{1}{2}(d/d_0)^2\right) - \exp(-0.5)}{1 - \exp(-0.5)}$$

---

## Viewer Experience

The embedded Three.js WebGL 3D Studio delivers hardware-accelerated rendering directly inside QGIS:

| Feature | Description |
| :--- | :--- |
| **4 Camera Modes** | Seamlessly switch between **Orbit** (free inspect), **Chase / Drone** (follows avatar with damping), **POV / Driver** (first-person perspective), and **Tour** (cinematic flight). |
| **GLSL Heat Stress Ribbon** | Route path is rendered as a volumetric 3D ribbon whose vertex colors dynamically reflect slope gradient or thermal comfort index. |
| **Kinematic Avatar Rigs** | Procedural 3D avatar meshes for walkers, wheelchairs, strollers, cyclists, scooters, cars, delivery vans, and ambulances. |
| **Routing Diagnostics** | Route exports and KPI tooltips expose snap distances, expanded graph nodes, and access-blocked edges. |
| **Subsurface Geological Slicer** | Clip plane tool to slice through 3D terrain and inspect subterranean elevation profiles. |
| **Audio Voice Navigation** | Web Speech API integration delivering turn-by-turn spoken audio cues with gradient warnings. |
| **Media Capture** | Export 4K PNG screenshots or record live WebM/MP4 video clips directly from the canvas. |

---

## Showcase Playbook

| Scenario | What to Demonstrate |
| :--- | :--- |
| **Wheelchair ADA Audit** | Route through historic hilly core; show how 5% grade limits and stair blockers bypass impassable paths. |
| **Heat Wave Escape Route** | Set high thermal LST weight ($w_{\text{heat}} = 0.40$); show path routing through shaded parks. |
| **E-Bike Power Optimization** | Demonstrate aerodynamic drag and motor cutoff simulation on steep ascents. |
| **Emergency Paramedic Response** | Rapid response routing overriding pedestrian barriers with road hierarchy prioritization. |
| **Multi-Tier 3D Isochrones** | Anisotropic wavefront expansion demonstrating catchment contraction on mountain slopes. |

---

## Repository Map

| Path | Role |
| :--- | :--- |
| `main_plugin.py` | QGIS plugin lifecycle, action registration, and UI integration. |
| `core/routing_engine.py` | 3D topological graph builder, A* search, and Least-Cost Path solver. |
| `core/kinematics.py` | Tobler, Minetti, Keys bicubic spline, and aerodynamic drag kinematics. |
| `core/mobility_profiles.py` | Definitions and constraints for all 15 mobility profiles. |
| `core/environmental_raster.py` | Multi-criteria raster impedance sampler and AHP consistency engine. |
| `core/network_policy.py` | Profile-aware modal access rules and surface-quality interpretation. |
| `core/network_audit.py` | Connectivity and access readiness diagnostics for routing networks. |
| `core/pareto_router.py` | NAMOA* 4D multi-objective Pareto frontier solver. |
| `core/isochrone_engine.py` | Anisotropic Dijkstra wavefront isochrone propagation. |
| `core/map_matching_3d.py` | Hidden Markov Model 3D GPS map matching with Viterbi decoding. |
| `core/profile_dxf.py` | AutoCAD DXF AC1009 3D Polyline and longitudinal profile exporter. |
| `gui/dock.py` | 5-tab docked studio UI with interactive map pickers and animators. |
| `web/` | Embedded Three.js WebGL studio (HTML, CSS, JS, GLSL shaders). |
| `docs/` | GitHub Pages landing page (`index.html`) and Reference Manual (`MANUAL.html`). |
| `tests/` | Pure logic unit tests and QGIS headless smoke tests. |

---

## 🧩 Part of the PlanX ecosystem

02Route 3D is part of the suite of open-source QGIS plugins for urban planning and geospatial analytics by **Yusuf Eminoğlu**:

| Planning & Analysis | CAD & Production | 3D & Cartography |
| :--- | :--- | :--- |
| [PlanX](https://github.com/YusufEminoglu/PlanX) — Spatial Planning Studio | [PlanX CAD Toolset](https://github.com/YusufEminoglu/PlanX-CAD) — CAD in QGIS | [02Route 3D](https://github.com/YusufEminoglu/zero2route3d) — 3D Mobility Studio |
| [GeoStats Lab](https://github.com/YusufEminoglu/planx_geostats) — Spatial Statistics | [EasyFillet](https://github.com/YusufEminoglu/EasyFillet) — Tangent Arc Fillet | [02CartoLab](https://github.com/YusufEminoglu/zero2cartolab) — Cartographic Studio |
| [Suitability Lab](https://github.com/YusufEminoglu/planx_suitability_lab) — Raster MCDA | [Settlement Toolset](https://github.com/YusufEminoglu/PlanX-Settlement) — Master Plans | [02Multimap](https://github.com/YusufEminoglu/zero2multimap) — Synchronized Multi-Canvas |
| [Urban Resilience](https://github.com/YusufEminoglu/planx_urban_resilience) — Seismic/Flood/Heat | [ParcelFlux](https://github.com/YusufEminoglu/parcelflux) — Parcel Subdivision | [02TrueSize](https://github.com/YusufEminoglu/zero2truesize) — Map Truth Lab |
| [DataCube Lab](https://github.com/YusufEminoglu/planx_datacube) — Space-Time Cubes | [02CadGis](https://github.com/YusufEminoglu/zero2cadgis) — Universal CAD Importer | [3D OSM Model](https://github.com/YusufEminoglu/osm_3d_model) — OSM → 3D City |

---

## Research Inspiration & Acknowledgment

The multi-user persona framework and human-centric accessibility paradigm in **02Route 3D** was inspired by the pioneering inclusive mobility research by **Transform Transport / Systematica**:
- 🌐 [UX-Mobility: Multi-User Walkability Route Planner](https://transformtransport.org/research/inclusive-mobility/ux-mobility-multi-user-walkability-route-planner/) — *Transform Transport (Systematica Research & Innovation)*.

We gratefully acknowledge their innovative work on inclusive pedestrian accessibility and multi-profile routing design.

---

## Data, Credits & License

- **Author:** **Yusuf Eminoğlu** (<yusuf.eminoglu@deu.edu.tr>)
- **Affiliation:** Dokuz Eylül University, Department of City and Regional Planning
- **Elevation Data:** User-supplied DEM rasters or elevations returned by the Open-Elevation service; missing samples remain NoData.
- **License:** GNU General Public License v2.0 or later ([GPL-2.0-or-later](LICENSE)).
- **Attribution:** Shipped under Yusuf Eminoğlu's name alone in accordance with monorepo standards.
