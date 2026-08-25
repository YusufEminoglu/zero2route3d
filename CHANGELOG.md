# Changelog

All notable changes to **02Route 3D** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.3] - 2026-08-25

### Fixed
- **Web 3D Pulse Beacon Removal:** Removed the detached spherical pulse beacon that travelled along the route curve independently of the kinematic avatar.
- **Web 3D Drone Chase & POV Camera Controls:** Enhanced Chase, POV, and Tour camera following modes with continuous per-frame orientation tracking, profile-aware eye heights, and instant mode-switch snapping.

## [0.2.2] - 2026-08-25

### Fixed
- **Compute Button Label Reset:** Fixed button text becoming stuck on "Computing..." after route execution so that both Quick and Advanced compute buttons restore their original labels immediately.

### Changed
- **Official Plugin Icon Refresh:** Replaced plugin icon with an edge-to-edge transparent 3D diorama design featuring 3D topographic contours, rising neon elevation route ribbon, and dual lettered A/B geolocator pins.

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
