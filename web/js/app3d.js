import * as THREE from './three.module.js';
import { OrbitControls } from './OrbitControls.js';
import { KinematicAvatarRig } from './KinematicAvatarRig.js';
import { TerrainSlicerSystem } from './TerrainSlicerSystem.js';
import { VoiceCueSystem } from './VoiceCueSystem.js';
import { disposeHierarchy, RoutePath } from './scene_geometry.js';
import { lonLatToLocal } from './geo.js';
import { startLiveSync, stopLiveSync } from './live_sync.js';
import { lightingMethods } from './lighting.js';
import { controlsMethods } from './controls.js';
import { routeLayerMethods } from './route_layers.js';
import { terrainMethods } from './terrain.js';
import { basemapMethods } from './basemap.js';
import { sceneBuilderMethods } from './scene_builders.js';
import { captureMethods } from './capture.js';
import { chartMethods } from './charts.js';

class Studio3DApp {
  constructor() {
    this.container = document.getElementById('canvasContainer');
    this.width = window.innerWidth;
    this.height = window.innerHeight;

    // Renderer. No preserveDrawingBuffer: captures render a frame and read it
    // back in the same task, and keeping the buffer costs every frame.
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setSize(this.width, this.height);
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.15;
    if (this.container) {
      this.container.appendChild(this.renderer.domElement);
    }

    // Scene with clean daylight atmosphere
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0xe0f2fe);
    this.scene.fog = new THREE.FogExp2(0xf0f9ff, 0.00025);

    // Camera
    this.camera = new THREE.PerspectiveCamera(45, this.width / Math.max(1, this.height), 1, 30000);
    this.camera.position.set(0, 220, 320);

    // Controls - Free 360 Orbit Camera by default
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.maxPolarAngle = Math.PI / 2 - 0.02;
    this.controls.minDistance = 10;
    this.controls.maxDistance = 15000;
    // Render on demand: a frame is drawn only when something changed (camera,
    // playback, a loaded tile, a toggle), so an idle viewer costs no GPU time.
    this.needsRender = true;
    this.framesRendered = 0;
    this.controls.addEventListener('change', () => this.requestRender());
    this.reducedMotion = Boolean(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    if (this.reducedMotion) this.controls.enableDamping = false;

    this.setupLighting();

    // Meshes and Groups
    this.terrainMesh = null;
    this.skirtMesh = null;
    this.roadMesh = null;
    this.centerlineMesh = null;
    this.glowTubeMesh = null;
    this.pulseBeacon = null;
    this.avatarMesh = null;
    this.needlePin = null;
    this.buildingsGroup = new THREE.Group();
    this.treesGroup = new THREE.Group();
    this.pinsGroup = new THREE.Group();
    this.routeOverlayGroup = new THREE.Group();

    this.scene.add(this.buildingsGroup);
    this.scene.add(this.treesGroup);
    this.scene.add(this.pinsGroup);
    this.scene.add(this.routeOverlayGroup);

    // Subsystems
    this.avatarRig = new KinematicAvatarRig(this.scene);
    this.slicerSystem = new TerrainSlicerSystem(this.scene, this.renderer);
    this.voiceSystem = new VoiceCueSystem();

    // Route state & Elevation normalization
    this.routeData = null;
    this.routeCollection = null;
    this.routeFeatures = [];
    this.activeProfileKey = null;
    this.selectedProfileKeys = new Set();
    this.profileOpacity = new Map();
    this.routeVisuals = [];
    this.avatarRigs = [];
    this.scenePoints = [];
    this.curve = null;
    this.originLonLat = null;
    this.baseElevation = 0.0;
    this.activePath = null;
    this.terrainGrid = null;
    this.terrainSource = 'none';
    this.activeScenario = 'shortest';

    // Feature Toggles & State
    this.isPlaying = false;
    this.progress = 0.0;
    this.playbackSpeed = 1.0;
    this.cameraMode = 'orbit'; // Default to Orbit camera
    this.basemapProvider = 'osm'; // 'osm', 'satellite', 'voyager', 'dark'
    this.activeMetrics = [];
    this.elevationExaggeration = 1.5;
    this.showBuildings = true;
    this.showTrees = true;
    this.showBasemap = true;
    this.isRecording = false;
    this.mediaRecorder = null;
    this.recordedChunks = [];

    // Radar & HUD
    this.radarCanvas = document.getElementById('radarCanvas');
    this.radarCtx = this.radarCanvas ? this.radarCanvas.getContext('2d') : null;
    this.compassRose = document.getElementById('compassRose');

    this.initHudElements();
    this.bindEvents();
    this.applySun();
    this.watchBottomPanel();

    this.clock = new THREE.Clock();
    this.animate = this.animate.bind(this);
    requestAnimationFrame(this.animate);
  }

  requestRender() {
    this.needsRender = true;
  }

  // Side panels stop above the bottom panel, whose height changes with the
  // number of metric rows; publish it as a CSS variable.
  watchBottomPanel() {
    const panel = document.querySelector('.bottom-controls');
    if (!panel || typeof ResizeObserver === 'undefined') return;
    const publish = () => {
      document.documentElement.style.setProperty('--bottom-panel-h', `${Math.ceil(panel.getBoundingClientRect().height)}px`);
    };
    new ResizeObserver(publish).observe(panel);
    publish();
  }

  initHudElements() {
    this.elEmpty = document.getElementById('emptyOverlay');
    this.elDist = document.getElementById('valDistance');
    this.elTime = document.getElementById('valDuration');
    this.elClimb = document.getElementById('valClimb');
    this.elSlope = document.getElementById('valMaxSlope');
    this.elKcal = document.getElementById('valCalories');
    this.elPlayIcon = document.getElementById('playIcon');
    this.elScrubber = document.getElementById('scrubber');
    this.elMultiMetricRows = document.getElementById('multiMetricRows');
    this.elBasemapCredit = document.getElementById('basemapCredit');
    this.elBasemapNote = document.getElementById('basemapNote');
    this.lblExag = document.getElementById('lblExag');
    this.elLayerList = document.getElementById('layerList');
    this.elLayerCount = document.getElementById('layerCount');
    this.activeMetrics = [];
  }

  lonLatToSceneMeters(lon, lat, eleMeters) {
    if (!this.originLonLat) return new THREE.Vector3(0, 0, 0);
    const { x: dx, z: dz } = lonLatToLocal(this.originLonLat, lon, lat);
    const baseEle = this.baseElevation !== undefined ? this.baseElevation : 0.0;
    const dy = ((eleMeters || 0.0) - baseEle) * this.elevationExaggeration;
    return new THREE.Vector3(dx, dy, dz);
  }

  // Web Guide consumes the same FeatureCollection that QGIS animates.
  // Each feature is a route layer; the eye control decides which layers move.
  loadRoute(geojson) {
    const features = geojson?.type === 'FeatureCollection'
      ? (Array.isArray(geojson.features) ? geojson.features : [])
      : (geojson?.type === 'Feature' ? [geojson] : []);
    const validFeatures = features.filter((feature) => {
      const coords = feature?.geometry?.coordinates || [];
      return feature?.geometry?.type === 'LineString' && Array.isArray(coords) && coords.length >= 2;
    });

    if (!validFeatures.length) {
      this.routeData = null;
      this.routeCollection = null;
      this.routeFeatures = [];
      this.activeProfileKey = null;
      this.selectedProfileKeys.clear();
      if (this.elEmpty) this.elEmpty.style.display = 'block';
      this.scenePoints = [];
      if (this.elMultiMetricRows) this.elMultiMetricRows.innerHTML = '';
      this.activeMetrics = [];
      if (this.radarCtx) this.radarCtx.clearRect(0, 0, 130, 130);
      this.clearSceneObjects();
      this.updateHudMetrics();
      this.renderLayerPanel();
      return;
    }

    this.routeCollection = geojson;
    this.routeFeatures = validFeatures;
    const collectionPrimary = geojson?.properties?.primary_profile_key;
    const firstKey = validFeatures[0]?.properties?.profile_key || 'adult';
    this.activeProfileKey = validFeatures.some((feature) => feature?.properties?.profile_key === collectionPrimary)
      ? collectionPrimary
      : firstKey;
    this.selectedProfileKeys = new Set(validFeatures.map((feature) => feature?.properties?.profile_key || 'adult'));
    if (this.elEmpty) this.elEmpty.style.display = 'none';
    this.refreshActiveRoute(true);
    this.isPlaying = false;
    if (this.elPlayIcon) this.elPlayIcon.textContent = '▶';
  }

  refreshActiveRoute(resetCamera = false) {
    const activeFeature = this.routeFeatures.find(
      (feature) => (feature?.properties?.profile_key || 'adult') === this.activeProfileKey,
    ) || this.routeFeatures[0];
    if (!activeFeature) return;
    this.routeData = activeFeature;
    this.activeProfileKey = activeFeature.properties?.profile_key || 'adult';
    const coords = activeFeature.geometry.coordinates;

    const allElevations = [];
    this.routeFeatures.forEach((feat) => {
      const featCoords = feat?.geometry?.coordinates || [];
      featCoords.forEach((c) => {
        if (c[2] !== undefined && !isNaN(c[2])) allElevations.push(Number(c[2]));
      });
    });
    const buildings = this.routeData?.properties?.corridor_buildings || this.routeCollection?.properties?.corridor_buildings || [];
    if (Array.isArray(buildings)) {
      buildings.forEach((bld) => {
        if (bld.base_elevation_m !== undefined && !isNaN(bld.base_elevation_m)) {
          allElevations.push(Number(bld.base_elevation_m));
        }
      });
    }
    this.setTerrainGrid(this.routeCollection?.properties?.terrain);
    if (this.terrainGrid) {
      // Math.min(...array) overflows the call stack on large grids.
      allElevations.push(this.terrainGrid.heights.reduce((m, h) => Math.min(m, h), Infinity));
    }
    this.baseElevation = allElevations.length ? allElevations.reduce((m, h) => Math.min(m, h), Infinity) : 0.0;

    this.originLonLat = { lon: coords[0][0], lat: coords[0][1] };
    this.scenePoints = coords.map((c) => this.lonLatToSceneMeters(c[0], c[1], c[2] || 0.0));
    this.activePath = new RoutePath(this.scenePoints);
    this.curve = this.activePath.curve;
    this.routeVisuals = this.routeFeatures
      .filter((feature) => this.selectedProfileKeys.has(feature?.properties?.profile_key || 'adult'))
      .map((feature) => {
        const featureCoords = feature.geometry.coordinates;
        const points = featureCoords.map((c) => this.lonLatToSceneMeters(c[0], c[1], c[2] || 0.0));
        const path = new RoutePath(points);
        return {
          key: feature?.properties?.profile_key || 'adult',
          feature,
          points,
          path,
          curve: path.curve,
        };
      });
    this.updateHudMetrics();
    if (this.voiceSystem && activeFeature.properties?.cue_sheet) {
      this.voiceSystem.loadCues(activeFeature.properties.cue_sheet);
    }
    this.renderLayerPanel();
    this.rebuildScene();
    this.renderProfileChart();
    if (resetCamera) this.resetCameraView();
  }

  switchScenario(scenarioKey) {
    this.activeScenario = scenarioKey;
    // Each scenario shows the first computed route among its profiles. The
    // shade & greenery corridor prefers the scenic walk (strongest greenery
    // preference), then the most heat-sensitive walkers.
    const scenarioProfiles = {
      ada: ['wheelchair', 'stroller'],
      cycle: ['bicycle', 'mtb', 'scooter'],
      green: ['sightseer', 'senior', 'jogger', 'child'],
    };
    const computed = new Set(this.routeFeatures.map((feature) => feature?.properties?.profile_key || 'adult'));
    const preferred = (scenarioProfiles[scenarioKey] || []).find((key) => computed.has(key));
    const targetKey = preferred || this.routeCollection?.properties?.primary_profile_key || this.activeProfileKey;
    if (targetKey && targetKey !== this.activeProfileKey && this.routeFeatures.some((feature) => (feature?.properties?.profile_key || 'adult') === targetKey)) {
      this.selectProfile(targetKey);
    } else {
      this.updateHudMetrics();
    }
  }

  clearSceneObjects() {
    // Stop any basemap tiles still in flight before their target texture goes.
    this.cancelPendingTiles();
    const removeAndDispose = (mesh) => {
      if (!mesh) return;
      this.scene.remove(mesh);
      disposeHierarchy(mesh);
    };

    removeAndDispose(this.terrainMesh);
    this.terrainMesh = null;
    removeAndDispose(this.skirtMesh);
    this.skirtMesh = null;
    removeAndDispose(this.roadMesh);
    this.roadMesh = null;
    removeAndDispose(this.centerlineMesh);
    this.centerlineMesh = null;
    removeAndDispose(this.glowTubeMesh);
    this.glowTubeMesh = null;
    removeAndDispose(this.pulseBeacon);
    this.pulseBeacon = null;
    removeAndDispose(this.avatarMesh);
    this.avatarMesh = null;
    removeAndDispose(this.needlePin);
    this.needlePin = null;

    const clearGroup = (group) => {
      while (group.children.length) {
        const child = group.children[0];
        group.remove(child);
        disposeHierarchy(child);
      }
    };

    clearGroup(this.buildingsGroup);
    clearGroup(this.treesGroup);
    clearGroup(this.pinsGroup);
    clearGroup(this.routeOverlayGroup);

    const rigs = new Set(this.avatarRigs || []);
    if (this.avatarRig) rigs.add(this.avatarRig);
    rigs.forEach((rig) => {
      if (rig && typeof rig.dispose === 'function') rig.dispose();
    });
    this.avatarRig = new KinematicAvatarRig(this.scene);
    this.avatarRigs = [];
  }

  rebuildScene() {
    this.clearSceneObjects();

    if (!this.curve || this.scenePoints.length < 2) return;

    this.buildTerrain();
    this.buildClassyRoadRibbon();
    this.buildPinMarkers();
    this.buildAvatar();
    this.buildUrbanEnvironment();
    this.slicerSystem.attachToTerrain(this.terrainMesh, this.buildingsGroup, this.treesGroup, this.skirtMesh);

    this.progress = 0.0;
    this.applySun();
    this.updateAvatarPosition();
  }

  togglePlay() {
    this.isPlaying = !this.isPlaying;
    const btnPlay = document.getElementById('btnPlay');
    if (btnPlay) {
      btnPlay.setAttribute('aria-label', this.isPlaying ? 'Pause' : 'Play');
      btnPlay.setAttribute('aria-pressed', String(this.isPlaying));
    }
    this.requestRender();
    if (this.elPlayIcon) {
      this.elPlayIcon.textContent = this.isPlaying ? '⏸' : '▶';
    }
  }

  updateAvatarPosition() {
    if (!this.activePath || this.scenePoints.length < 2) return;
    const safeProgress = Math.max(0.0, Math.min(1.0, this.progress));
    const { point: pt, tangent } = this.activePath.at(safeProgress);
    this.requestRender();

    // Determine simulation elapsed time based on max duration among all active routes
    const maxDurationSec = Math.max(
      1.0,
      ...this.routeFeatures.map((f) => Number(f?.properties?.duration_min || 5.0) * 60.0),
    );
    const elapsedSimTimeSec = safeProgress * maxDurationSec;

    // Real frame delta, not a fixed 16 ms step: gait cadence and wheel spin were
    // running twice as fast on a 120 Hz display and stalling when this was called
    // from the scrubber rather than the render loop.
    const frameDelta = Number.isFinite(this.lastFrameDelta) ? this.lastFrameDelta : 0.016;
    const animDelta = this.isPlaying ? frameDelta * Math.max(0.2, this.playbackSpeed) : 0.0;
    this.routeVisuals.forEach((visual) => {
      if (!visual.rig || !visual.path) return;
      const props = visual.feature?.properties || {};
      const visualDurationSec = Math.max(1.0, Number(props.duration_min || 5.0) * 60.0);
      const visualProgress = Math.max(0.0, Math.min(1.0, elapsedSimTimeSec / visualDurationSec));

      const { point: visualPt, tangent: visualTangent } = visual.path.at(visualProgress);
      visual.rig.root.position.copy(visualPt);
      visual.rig.root.position.y += 0.2;
      const targetYaw = Math.atan2(visualTangent.x, visualTangent.z);
      let curYaw = visual.rig.root.rotation.y;
      let diff = targetYaw - curYaw;
      while (diff < -Math.PI) diff += Math.PI * 2;
      while (diff > Math.PI) diff -= Math.PI * 2;
      visual.rig.root.rotation.y = curYaw + diff * 0.35;
      const spd = Number(props.base_speed_kmh || 5.0);
      visual.rig.updateKinematics(animDelta, spd, visualTangent, 0, false);
    });

    if (this.pulseBeacon && !this.isPlaying) {
      // At rest the beacon marks the current position (it runs ahead only in playback).
      this.pulseBeacon.position.copy(pt);
      this.pulseBeacon.position.y += 1.8;
    }

    if (this.voiceSystem) {
      this.voiceSystem.update(safeProgress);
    }

    if (this.elScrubber) {
      this.elScrubber.value = (safeProgress * 1000.0).toFixed(0);
      const totalKm = Number(this.routeData?.properties?.distance_km || 0);
      this.elScrubber.setAttribute('aria-valuetext',
        `${(safeProgress * totalKm).toFixed(2)} of ${totalKm.toFixed(2)} km`);
    }
    if (this.activeMetrics && this.activeMetrics.length) {
      const leftPct = `${safeProgress * 100}%`;
      this.activeMetrics.forEach((m) => {
        if (m.elNeedle) m.elNeedle.style.left = leftPct;
      });
    }

    this.updateMetricLiveReadout();
    this.updateChartCursor();

    // Camera following modes
    if (this.cameraMode === 'chase') {
      const offset = tangent.clone().multiplyScalar(-40).add(new THREE.Vector3(0, 20, 0));
      const targetPos = pt.clone().add(offset);
      this.camera.position.lerp(targetPos, 0.08);
      this.controls.target.lerp(pt.clone().add(new THREE.Vector3(0, 3, 0)), 0.1);
    } else if (this.cameraMode === 'pov') {
      this.camera.position.copy(pt.clone().add(new THREE.Vector3(0, 5.0, 0)));
      this.controls.target.copy(pt.clone().add(tangent.clone().multiplyScalar(100)));
    } else if (this.cameraMode === 'tour') {
      const tourOffset = new THREE.Vector3(Math.sin(safeProgress * Math.PI * 4) * 65, 45, Math.cos(safeProgress * Math.PI * 4) * 65);
      this.camera.position.lerp(pt.clone().add(tourOffset), 0.05);
      this.controls.target.lerp(pt, 0.08);
    }

    this.updateRadarMap();
  }

  setProgress(fraction) {
    this.progress = Math.max(0.0, Math.min(1.0, Number(fraction) || 0));
    this.updateAvatarPosition();
  }

  animate() {
    this.rafHandle = requestAnimationFrame(this.animate);
    const delta = this.clock.getDelta();
    // Clamped so a backgrounded tab does not resume with one huge jump.
    this.lastFrameDelta = Math.min(0.1, delta);

    const hasRoute = Boolean(this.curve && this.scenePoints.length >= 2);
    if (this.isPlaying && hasRoute) {
      this.progress += this.lastFrameDelta * 0.04 * this.playbackSpeed;
      if (this.progress > 1.0) {
        this.progress = 0.0;
      }
      this.updateAvatarPosition();
      this.needsRender = true;
    }

    // The pulse beacon runs ahead of the walker only during playback, so an
    // idle view does not have to redraw every frame.
    if (this.pulseBeacon && hasRoute && this.isPlaying && !this.reducedMotion) {
      const beaconProg = (this.clock.getElapsedTime() * 0.15) % 1.0;
      const sample = this.activePath ? this.activePath.at(beaconProg) : null;
      if (sample) {
        this.pulseBeacon.position.copy(sample.point);
        this.pulseBeacon.position.y += 1.8;
      }
    }

    if (this.cameraMode === 'orbit' && this.controls.enabled) {
      // update() reports whether damping is still moving the camera.
      if (this.controls.update()) this.needsRender = true;
    } else if (hasRoute) {
      this.needsRender = true;
    }

    if (!this.needsRender && !this.isRecording) return;
    this.needsRender = false;

    if (this.compassRose && this.controls) {
      const angle = this.controls.getAzimuthalAngle();
      this.compassRose.style.transform = `rotate(${angle}rad)`;
    }

    this.renderer.render(this.scene, this.camera);
    this.framesRendered += 1;
    if (this.isRecording) this.drawRecordingFrame();
  }
}

// The methods live in topic modules; the class keeps construction, route
// loading, playback and the render loop.
Object.assign(Studio3DApp.prototype, lightingMethods, controlsMethods, routeLayerMethods, terrainMethods, basemapMethods, sceneBuilderMethods, captureMethods, chartMethods);

window.app3d = new Studio3DApp();

window.setRouteData = function (geojsonData) {
  if (window.app3d && geojsonData) {
    window.app3d.loadRoute(geojsonData);
  }
};

// There is deliberately no built-in demo route: until QGIS computes one the
// studio shows its empty state rather than a fabricated route whose numbers
// would be indistinguishable from real ones.
startLiveSync();

window.addEventListener('beforeunload', stopLiveSync);
