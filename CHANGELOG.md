# Changelog

All notable changes to **02Route 3D** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.6.0] - 2026-10-10

Roadmap phase 2: QGIS stays responsive (see docs/ROADMAP.md).

### Changed
- Route calculation, the OD matrix, OpenStreetMap downloads and the global
  DEM download run as QGIS background tasks with progress and cancel. Raster
  layers are read through cloned providers; layers are added to the project
  only on the main thread.
- The global DEM download is planned against a 25,000-point budget. Large
  extents get a coarser grid instead of thousands of sequential requests;
  the layer name shows the real cell size, and the dock asks before more
  than 40 requests or when a DEM layer is already selected.
- Overpass responses are read in chunks, so cancelling a Processing
  algorithm or a dock task stops the OpenStreetMap download.

### Added
- The dock remembers profile scope, focus profile, MCDA weights, playback
  speed, auto-pan and the open tab (QGIS settings); layer choices are stored
  in the project file.
- Scenario files (`*.route3d.json`): points, profiles, weights, layer
  choices and a per-profile result summary. Loading a scenario restores the
  inputs and compares the saved run with the current routes (or with the
  next route computed).
- CI job that installs QGIS 3.34 on Ubuntu 24.04 and runs the plugin smoke
  suite and the Processing algorithm suite.

## [0.5.0] - 2026-10-09

Roadmap phase 1: routes are correct for every profile (see docs/ROADMAP.md).

### Fixed
- One-way streets were one-way for everyone: the reverse edge was never built,
  so pedestrians could not walk against traffic and `oneway:bicycle=no`
  contra-flow lanes were ignored. Direction is now a per-mode access decision
  (`oneway`, `oneway:bicycle`, `oneway:foot`).
- Wheelchair and stroller slope limits were only a penalty; slopes above the
  8.33 % (1:12) ramp maximum, or 10 % for strollers, are now impassable.
  Custom profile JSON keeps its road-class weights (the keys came back as
  strings and were ignored).
- Pareto routes: the time bound assumed 1.5x base speed, slower than real top
  speeds (bikes downhill, cars on motorways), so the fastest route could be
  wrong; it now comes from each profile's speed model. Epsilon dominance was
  inverted; heap entries carry their label instead of matching it by time;
  a truncated search is reported.
- Nodes without elevation were set to 0 m, creating cliffs at the edge of DEM
  coverage; they now take the mean height of their neighbours. Slopes from
  DEM heights are measured over at least 10 m, so DEM noise on short
  segments no longer reads as 30-50 % grades.
- Vehicles ignored posted speed limits: `maxspeed` now raises cost and
  travel time where it is below the road class speed.
- Map matching projected on raw degrees, kept the first ten candidates rather
  than the nearest, and compared straight-line rather than network distance;
  it now uses an edge grid index too.
- Alternative routes forbade every primary edge and failed where the route
  crossed the only bridge; primary edges are now penalised (x4).
- Overpass and Open-Elevation errors were swallowed and reported as "no OSM
  elements found"; the real cause is now shown.
- The local viewer server sent `Access-Control-Allow-Origin: *`, letting any
  web page read the current route from localhost.
- `QAction` import for QGIS 4 / Qt6.
- Solar exposure field aliases pointed at non-existent fields; the viewer's
  "shade & greenery" scenario showed the senior route regardless.

## [0.4.1] - 2026-10-09

### Changed
- The repository and issue tracker moved to GitHub:
  https://github.com/YusufEminoglu/zero2route3d. Plugin metadata, the README
  (including the plugin ecosystem table) and the documentation site link there;
  the user manual stays at https://geophilo.com/zero2route3d/.
- Clearer plugin description and "about" text on the QGIS Plugin Hub.

## [0.4.0] - 2026-09-05

### Added
- Profile-aware modal access filtering for `access`, `foot`, `bicycle`,
  `motor_vehicle`, highway class, lighting and surface attributes.
- `zero2route3d:audit_routing_network`, a fifteenth Processing algorithm that
  annotates network segments with accessibility and topology findings.
- Route diagnostics for origin/destination snap distance, expanded nodes,
  profile-blocked edges and graph health.

### Fixed
- QGIS network-layer extraction now preserves LineStringZ vertices and reads
  one-way, road name, surface, lane, access and speed fields case-insensitively.
- One-way network components are computed as weak components, eliminating
  feature-order-dependent snapping failures.
- Routes no longer create silent connectors up to 2.5 km; snapping is bounded
  to 1 km by default and configurable in the main Processing algorithm.
- Isochrones and Pareto routes now enforce the same profile constraints as the
  primary A* router.
- Isochrone area is measured from the real ordered convex hull instead of a
  fixed circular fill factor, and projected Processing origins are reprojected.

### Removed
- One unused GUI font import and stale documentation claims/counts.

## [0.3.0] - 2026-08-25

### Added
- **Inclusive UX Mobility & Shortest Path Framework:** Enhanced multi-user avatar-driven routing paradigm inspired by Transform Transport / Systematica inclusive mobility research.
- **Official QGIS Hub Ready:** Removed experimental flag for stable publication on the official QGIS Plugin Hub.

## [0.2.9] - 2026-08-25

### Added
- **Full Map Extent DEM Fetching:** Updated DEM acquisition from Open-Elevation API to query the full active QGIS map canvas extent without corridor clipping, loading a complete continuous GeoTIFF DEM.

### Changed
- **Relative Elevation Normalization & -5m Skirt Extrusion:** Ensured 3D WebGL terrain and diorama skirts are strictly normalized to the scene's minimum elevation with -5.0m plinth cap instead of absolute sea level (0m), preventing monolithic extrusions in high-altitude settlements.

## [0.2.8] - 2026-08-25

### Fixed
- **Point A & Point B 3D Billboard Text Sizing:** Dynamically measured text width and scaled the pill textbox background and sprite aspect ratio so that "Point A (Origin)" and "Point B (Destination)" fit with comfortable margins.

## [0.2.7] - 2026-08-25

### Changed
- **Rollback to Web Basemap Milestone:** Restored Web 3D studio codebase to the verified v0.2.1 basemap milestone while retaining updated transparent 3D diorama brand icons.

## [0.2.1] - 2026-08-25

### Fixed
- **Web 3D Basemap Coverage:** Resolved basemap tile cutoff issue across extensive model areas by dynamically determining zoom level and fully covering the model bounding box with no tile truncation.
- **Point A & Point B Dual Visual Markers:** Added distinct letter badge ("A" and "B") geolocator map pin markers and camera-facing billboard sprites in the 3D WebGL viewer, and enhanced QGIS vector point layers with composite font marker symbology and crisp white-buffered labels.

## [0.2.0] - 2026-08-24

A correctness and honesty release. Every fabricated output and unsupported claim
has been removed, and the defects below were found by a new end-to-end harness
that actually runs the Processing algorithms; the previous suites only checked
that they loaded.

### Removed - fabricated data and outputs
- **Synthetic "Copernicus Sentinel-2" rasters.** The NDVI, LST and NDBI grids
  were a sine/cosine hash of the pixel indices, written to disk, symbolised,
  loaded into the project under a satellite-labelled group and fed into the
  routing cost. No satellite imagery was ever fetched. Only the real elevation
  raster remains; supply your own NDVI/LST rasters through the existing
  selectors.
- **The built-in demo route.** The 3D studio silently loaded a fabricated
  "Coastal Corridor Sample" - with invented distance, climb and calorie figures
  and invented street names - whenever QGIS had not computed a route. The empty
  state is shown instead.
- **Procedural greenery.** Trees were generated every 18 m at six fixed lateral
  offsets, with height, canopy, trunk and species derived from
  `abs(hash(coordinate))`, and returned in the same list as real OSM trees.
  Only trees that exist in OSM are returned; park boundary vertices are no
  longer treated as tree positions.
- **Hard-coded Izmir coordinates.** Four export algorithms substituted a
  two-point line in Izmir when the input held no usable geometry, then reported
  statistics for it. The dock did the same with a default bounding box.
- **Invented fallbacks for missing data.** Missing elevation became 0.0 (a real
  sea-level value), missing LST became 0.5 and missing greenery 0.4, so a
  network with no DEM looked perfectly flat and fully ADA-compliant. All three
  now report "no data" and the criterion is dropped rather than invented.
- **Placeholder street names.** Every cue read "Urban Path" while real OSM name
  tags were parsed and discarded. Real names are now carried through the graph.
- **Invented energy figures.** Cycling used a flat 28 kcal/km and a car was
  credited with 15 kcal/km of metabolic energy. Cycling now uses a real power
  balance; motorised travel reports none.

### Fixed - correctness
- **Minetti (2002) metabolic cost** had three wrong coefficients, overstating
  walking energy roughly fivefold. Level walking now returns 2.5 J/kg/m.
- **QgsField(name, <int>) raises TypeError on QGIS 4**, so every sink-producing
  algorithm failed before writing a feature. Field construction is now version
  safe.
- **No algorithm reprojected its inputs.** Projected coordinates were consumed
  as WGS84 degrees, Overpass bounding boxes were built from projected extents,
  and several outputs were tagged with a CRS they were not in.
- **The isochrone profile list** was hand-written with nine entries and indexed
  into the fifteen-entry registry, so "Passenger Car" routed as a mountain bike
  and six profiles were unreachable.
- **The accessibility algorithm** crashed on its first feature
  (`fromPointXY(QgsPoint)`), and derived its scores from a default population of
  1000 and capacity of 100. Both are now required field parameters.
- **The four "3D" exports** read Z from the flat vertex list, which has no
  `z()`, so the Z axis was dead in all of them.
- **The walkability audit** measured geographic length in degrees as metres,
  producing gradients around 100000% and a score of zero for every segment. It
  now measures on the ellipsoid and refuses to run without a DEM.
- **The MCDA cost surface** declared an output and three input rasters, read
  none of them and wrote nothing. It now produces a real AHP-weighted friction
  raster and rejects inconsistent weights.
- **A\* was inadmissible**: the heuristic returned metres while the search
  accumulated impedance, so "optimal" routes were not optimal.
- **The offline HTML export** was a blank page for every user: Three.js and
  OrbitControls are ES modules and were injected into classic script tags.
- **DXF export** wrote X/Y in degrees and Z in metres, so routes opened in CAD
  as a vertical line.
- Also: `oneway=-1` and roundabouts treated as bidirectional, the "alternative
  route" hard-coded to the sightseer profile, vehicle travel time ignoring road
  hierarchy so a paramedic matched a truck, a corrupted AHP matrix diagonal, a
  population-count Palma ratio, an even-length median, `exec_()` on Qt6, and a
  `groupId` collision that split the toolbox into two identical folders.

### Fixed - interface
- Closing the dock deleted the user's route layers and permanently broke layer
  tracking; the panel was a zombie after one close/reopen.
- Every picker switch leaked a map tool and left an orphaned canvas marker, and
  Point B always drew Point A's marker.
- The route handed to the 3D studio was written non-atomically against a 1.5 s
  poller, so the viewer could read truncated JSON and silently show a stale scene.
- Compute could be re-entered on the frozen UI; Quick mode showed no progress and
  never reset its KPIs or export buttons.
- The 3D studio's Heat chart read exactly 30.0 degrees C and Greenery exactly 65%
  for every route on Earth. Both now show real values or "no data".
- Basemap tiles in flight wrote into disposed textures; textures other than
  `.map` leaked and shared materials were disposed repeatedly.
- Avatar gait ran at double speed on a 120 Hz display.
- Added the OpenStreetMap, CARTO and Esri attribution their terms require.

### Changed
- Removed roughly 1,000 lines of dead code, including two viewer modules that
  were never imported but were advertised in the interface as a "Thermal Heat
  Ribbon" and a "24-hr Solar Cycle".
- Corrected 11 of 14 documented algorithm IDs; every published snippet used to
  raise "Algorithm not found".
- Replaced a 19-line truncated LICENSE - missing the grant of rights and the
  warranty disclaimer - with the complete GPL-2 text.
- Removed unsupported claims: "60 FPS" as a specification, an "official
  Copernicus COG downloader" that only built URL strings, numpy/scipy
  dependencies that are not imported, hazard-polygon evacuation and
  building-massing solar analysis that have no inputs.

### Testing
- Added an end-to-end harness that runs the algorithms on both QGIS 3.44 LTR and
  4.2 and asserts on values, renderers and field aliases; it is wired into
  `pf verify`.
- Replaced tests that could not fail: the Copernicus test asserted the
  fabricated rasters were valid, and the DEM tests asserted `>= 0.0`, which
  total failure satisfies.
- Added reference-value assertions for Minetti, Tobler and Gini. Every equation
  was previously asserted only by inequality, which is how three wrong
  coefficients shipped unnoticed.
- CI ran neither suite; both now run, alongside detect-secrets.

## [0.1.0] - 2026-08-22

### Added
- **Interactive QGIS Dock Studio:** Modern 5-tab card-based UI with dark/light adaptive styling, point picker map tools, and live canvas animators.
- **15 Universal 3D Mobility Profiles:** Standard Adult, Senior, Child, Stroller, Wheelchair (ADA), Runner, Sightseer, Night Walk, Commuter Bike, Mountain Bike, E-Scooter, Passenger Car, Delivery Van, Heavy Freight, and Paramedic EMS.
- **Biomechanical Kinematics Engine:** Tobler hiking velocity equation, Minetti 5th-order polynomial metabolic energy expenditure, senior fatigue decay, and micromobility aerodynamic drag modeling.
- **Multi-Criteria AHP Cost Surfaces:** Pairwise comparison matrix with Saaty consistency validation ($CR \le 0.10$) combining DEM slope, Land Surface Temperature (LST), NDVI greenery canopy, and road hierarchy.
- **NAMOA\* 4D Pareto Frontier Routing:** Multi-objective optimization computing non-dominated trade-offs across travel duration, cumulative climb, thermal heat exposure, and metabolic calories.
- **Embedded Three.js WebGL 3D Studio:** Hardware-accelerated 3D cockpit embedded directly inside QGIS, with Orbit/Chase/POV/Tour camera modes, glowing GLSL heat stress ribbons, 24-hour solar trajectory simulation, and subsurface terrain slicing.
- **14 Headless QGIS Processing Algorithms:** Fully scriptable algorithms for batch point-pair routing, 3D isochrone catchments, E2SFCA spatial equity scoring, GPS 3D map matching (HMM), hazard evacuation, and solar shadow analysis.
- **Multi-Format 3D Data Export:** Native QGIS 3D vector layer (`LineStringZ` in GeoPackage), GPX 1.1 with elevation, GeoJSON 3D, AutoCAD DXF (AC1009) 3D Polylines and longitudinal profiles, CSV/HTML cue sheets, and standalone self-contained offline 3D HTML web apps.
- **Publication-Grade GitHub Pages Site:** Animated landing page with multi-layer parallax terrain, interactive MCDA cost surface lab, 4D Pareto explorer, and comprehensive technical reference manual.
