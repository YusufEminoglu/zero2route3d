import * as THREE from './three.module.js';
import { OrbitControls } from './OrbitControls.js';
import { KinematicAvatarRig } from './KinematicAvatarRig.js';
import { TerrainSlicerSystem } from './TerrainSlicerSystem.js';
import { VoiceCueSystem } from './VoiceCueSystem.js';

// Accepts #rgb, #rrggbb, and the CSS colour keywords the profiles actually use.
const SAFE_COLOR = /^(#[0-9a-fA-F]{3,8}|[a-zA-Z]{3,20})$/;

// Every texture slot a material can hold. Disposing only `.map` leaked the rest.
const TEXTURE_SLOTS = [
  'map', 'normalMap', 'roughnessMap', 'metalnessMap', 'emissiveMap', 'aoMap',
  'bumpMap', 'displacementMap', 'alphaMap', 'envMap', 'lightMap', 'specularMap',
];

function disposeMaterial(material, seen) {
  if (!material || seen.has(material)) return;
  // Materials are shared between meshes (the building palette, for one), so
  // disposing per-mesh double-disposed them.
  seen.add(material);
  TEXTURE_SLOTS.forEach((slot) => {
    const tex = material[slot];
    if (tex && typeof tex.dispose === 'function') tex.dispose();
  });
  material.dispose();
}

function disposeHierarchy(obj) {
  if (!obj) return;
  const seenMaterials = new Set();
  const seenGeometries = new Set();
  obj.traverse((child) => {
    if (child.geometry && !seenGeometries.has(child.geometry)) {
      seenGeometries.add(child.geometry);
      child.geometry.dispose();
    }
    if (child.material) {
      if (Array.isArray(child.material)) {
        child.material.forEach((m) => disposeMaterial(m, seenMaterials));
      } else {
        disposeMaterial(child.material, seenMaterials);
      }
    }
  });
}

// Concatenate non-indexed copies of geometries that share one material.
// (three.module.js ships without BufferGeometryUtils.)
function mergeGeometries(geometries) {
  const parts = geometries.map((g) => (g.index ? g.toNonIndexed() : g));
  const names = ['position', 'normal', 'uv'].filter((name) => parts.every((g) => g.attributes[name]));
  const merged = new THREE.BufferGeometry();
  names.forEach((name) => {
    const itemSize = parts[0].attributes[name].itemSize;
    const total = parts.reduce((n, g) => n + g.attributes[name].array.length, 0);
    const array = new Float32Array(total);
    let offset = 0;
    parts.forEach((g) => {
      array.set(g.attributes[name].array, offset);
      offset += g.attributes[name].array.length;
    });
    merged.setAttribute(name, new THREE.BufferAttribute(array, itemSize));
  });
  parts.forEach((g, i) => {
    if (g !== geometries[i]) g.dispose();
  });
  merged.computeBoundingSphere();
  return merged;
}

// "Nice" axis ticks (1, 2, 5 x 10^n steps) covering [lo, hi].
function niceTicks(lo, hi, target) {
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return [0];
  if (hi - lo < 1e-9) return [lo];
  const raw = (hi - lo) / Math.max(1, target);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 5, 10].map((k) => k * mag).find((v) => v >= raw) || raw;
  const ticks = [];
  for (let t = Math.floor(lo / step) * step; t <= hi + step * 0.5; t += step) {
    ticks.push(Number(t.toFixed(10)));
  }
  return ticks;
}

// A flat ribbon of the given width that follows a route path at its true
// heights. (The old ribbons were tubes squashed with geometry.scale(1, 0.12, 1),
// which also squashed the route's heights to 12 %: on hills the ribbon sank
// under the terrain.)
function ribbonGeometry(path, width, yOffset = 0) {
  const samples = Math.max(64, Math.min(4000, Math.round(path.total / 3)));
  const half = width / 2;
  const positions = new Float32Array((samples + 1) * 6);
  const normals = new Float32Array((samples + 1) * 6);
  const indices = [];
  for (let i = 0; i <= samples; i++) {
    const { point, tangent } = path.at(i / samples);
    let sx = -tangent.z;
    let sz = tangent.x;
    const len = Math.hypot(sx, sz) || 1;
    sx /= len;
    sz /= len;
    positions.set([point.x + sx * half, point.y + yOffset, point.z + sz * half,
      point.x - sx * half, point.y + yOffset, point.z - sz * half], i * 6);
    normals.set([0, 1, 0, 0, 1, 0], i * 6);
    if (i < samples) {
      const a = i * 2;
      indices.push(a, a + 2, a + 1, a + 1, a + 2, a + 3);
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geo.setAttribute('normal', new THREE.BufferAttribute(normals, 3));
  geo.setIndex(indices);
  geo.computeBoundingSphere();
  return geo;
}

// A route in scene space, parameterised by horizontal distance travelled.
// CatmullRomCurve3.getPointAt() walks by 3D arc length, which the elevation
// exaggeration distorts; walking by real distance keeps playback at a steady
// speed and lets the elevation chart and the 3D marker agree exactly.
class RoutePath {
  constructor(points) {
    this.points = points;
    this.curve = new THREE.CatmullRomCurve3(points, false, 'catmullrom', 0.15);
    this.cumulative = [0];
    let total = 0;
    for (let i = 1; i < points.length; i++) {
      total += Math.hypot(points[i].x - points[i - 1].x, points[i].z - points[i - 1].z);
      this.cumulative.push(total);
    }
    this.total = total;
  }

  paramAt(fraction) {
    const n = this.points.length;
    if (n < 2 || this.total <= 0) return 0;
    const d = Math.max(0, Math.min(1, fraction)) * this.total;
    let lo = 0;
    let hi = n - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (this.cumulative[mid] <= d) lo = mid;
      else hi = mid;
    }
    const span = this.cumulative[hi] - this.cumulative[lo];
    const f = span > 0 ? (d - this.cumulative[lo]) / span : 0;
    return Math.min(1, (lo + f) / (n - 1));
  }

  at(fraction) {
    const t = this.paramAt(fraction);
    return { point: this.curve.getPoint(t), tangent: this.curve.getTangent(t).normalize() };
  }
}

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

  setupLighting() {
    this.hemiLight = new THREE.HemisphereLight(0xffffff, 0xe2e8f0, 0.95);
    this.scene.add(this.hemiLight);

    this.ambientLight = new THREE.AmbientLight(0xffffff, 0.45);
    this.scene.add(this.ambientLight);

    this.sunLight = new THREE.DirectionalLight(0xfffaed, 1.35);
    this.sunLight.position.set(400, 800, 300);
    this.sunLight.castShadow = true;
    this.sunLight.shadow.mapSize.width = 2048;
    this.sunLight.shadow.mapSize.height = 2048;
    this.sunLight.shadow.camera.near = 10;
    this.sunLight.shadow.camera.far = 10000;
    const d = 1400;
    this.sunLight.shadow.camera.left = -d;
    this.sunLight.shadow.camera.right = d;
    this.sunLight.shadow.camera.top = d;
    this.sunLight.shadow.camera.bottom = -d;
    this.sunLight.shadow.bias = -0.0003;
    this.scene.add(this.sunLight);
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

  bindEvents() {
    window.addEventListener('resize', () => {
      this.width = window.innerWidth;
      this.height = window.innerHeight;
      this.camera.aspect = this.width / Math.max(1, this.height);
      this.camera.updateProjectionMatrix();
      this.renderer.setSize(this.width, this.height);
      if (this.routeData) this.renderProfileChart();
      this.requestRender();
    });
    // Any interaction with the panels can change the scene (toggles, opacity,
    // basemap); with on-demand rendering it must ask for a frame.
    ['click', 'input', 'keydown', 'pointerup'].forEach((type) => {
      document.addEventListener(type, () => this.requestRender(), true);
    });

    // Keyboard: Space plays/pauses, arrows move along the route (Shift: 5 %),
    // Home/End jump to the ends, R resets the view.
    window.addEventListener('keydown', (e) => {
      const target = e.target;
      const tag = (target?.tagName || '').toLowerCase();
      if (['input', 'textarea', 'select'].includes(tag) || target?.isContentEditable) return;
      if (target?.closest && target.closest('#elevationChart')) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const step = e.shiftKey ? 0.05 : 0.01;
      if (e.key === ' ' && tag !== 'button') {
        e.preventDefault();
        this.togglePlay();
      } else if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
        e.preventDefault();
        this.setProgress(this.progress + (e.key === 'ArrowRight' ? step : -step));
      } else if (e.key === 'Home' || e.key === 'End') {
        e.preventDefault();
        this.setProgress(e.key === 'Home' ? 0 : 1);
      } else if (e.key === 'r' || e.key === 'R') {
        this.resetCameraView();
      }
    });

    const btnPlay = document.getElementById('btnPlay');
    if (btnPlay) {
      btnPlay.addEventListener('click', () => this.togglePlay());
    }

    if (this.elScrubber) {
      this.elScrubber.addEventListener('input', (e) => {
        this.progress = Math.max(0.0, Math.min(1.0, parseFloat(e.target.value) / 1000.0));
        this.updateAvatarPosition();
      });
    }

    // Scenario Switcher
    document.querySelectorAll('.scenario-btn').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        document.querySelectorAll('.scenario-btn').forEach((b) => b.classList.remove('active'));
        e.currentTarget.classList.add('active');
        this.switchScenario(e.currentTarget.dataset.scenario || 'shortest');
      });
    });

    // Speed options
    document.querySelectorAll('.speed-opt').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        document.querySelectorAll('.speed-opt').forEach((b) => b.classList.remove('active'));
        e.currentTarget.classList.add('active');
        this.playbackSpeed = parseFloat(e.currentTarget.dataset.speed || 1.0);
      });
    });

    // Camera modes
    document.querySelectorAll('.cam-btn').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        document.querySelectorAll('.cam-btn').forEach((b) => b.classList.remove('active'));
        e.currentTarget.classList.add('active');
        this.cameraMode = e.currentTarget.dataset.cam || 'orbit';
        this.controls.enabled = (this.cameraMode === 'orbit');
      });
    });

    // Basemap Provider Selection (OSM, Satellite, Voyager, Dark)
    document.querySelectorAll('.bmap-btn').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        document.querySelectorAll('.bmap-btn').forEach((b) => b.classList.remove('active'));
        e.currentTarget.classList.add('active');
        this.basemapProvider = e.currentTarget.dataset.provider || 'osm';
        this.updateBasemapAttribution();
        this.updateBasemapTexture();
      });
    });

    // Exaggeration button
    const btnExag = document.getElementById('btnExaggeration');
    if (btnExag) {
      btnExag.addEventListener('click', () => {
        const exags = [1.0, 1.5, 2.0, 3.0];
        const nextIdx = (exags.indexOf(this.elevationExaggeration) + 1) % exags.length;
        this.elevationExaggeration = exags[nextIdx];
        if (this.lblExag) this.lblExag.textContent = `${this.elevationExaggeration.toFixed(1)}x`;
        // Scene heights depend on the exaggeration, so the route points (not
        // just the meshes) must be recomputed.
        if (this.routeData) this.refreshActiveRoute(false);
      });
    }

    // Toggle Buildings
    const btnBld = document.getElementById('btnBuildings');
    if (btnBld) {
      btnBld.addEventListener('click', () => {
        this.showBuildings = !this.showBuildings;
        this.buildingsGroup.visible = this.showBuildings;
        btnBld.classList.toggle('active', this.showBuildings);
        const b = btnBld.querySelector('b');
        if (b) b.textContent = this.showBuildings ? 'ON' : 'OFF';
      });
    }

    // Toggle Trees
    const btnTrees = document.getElementById('btnTrees');
    if (btnTrees) {
      btnTrees.addEventListener('click', () => {
        this.showTrees = !this.showTrees;
        this.treesGroup.visible = this.showTrees;
        btnTrees.classList.toggle('active', this.showTrees);
        const b = btnTrees.querySelector('b');
        if (b) b.textContent = this.showTrees ? 'ON' : 'OFF';
      });
    }

    // Toggle Basemap
    const btnBasemap = document.getElementById('btnBasemap');
    if (btnBasemap) {
      btnBasemap.addEventListener('click', () => {
        this.showBasemap = !this.showBasemap;
        if (this.terrainMesh) {
          this.terrainMesh.visible = this.showBasemap;
        }
        if (this.skirtMesh) {
          this.skirtMesh.visible = this.showBasemap;
        }
        btnBasemap.classList.toggle('active', this.showBasemap);
        const b = btnBasemap.querySelector('b');
        if (b) b.textContent = this.showBasemap ? 'ON' : 'OFF';
      });
    }

    // Slicer button
    const btnSlicer = document.getElementById('btnSlicer');
    if (btnSlicer) {
      btnSlicer.addEventListener('click', () => {
        const active = this.slicerSystem.toggle();
        btnSlicer.classList.toggle('active', active);
      });
    }

    // Voice cues button
    const btnVoice = document.getElementById('btnVoice');
    if (btnVoice) {
      btnVoice.addEventListener('click', () => {
        const active = this.voiceSystem.toggle();
        btnVoice.classList.toggle('active', active);
        const b = btnVoice.querySelector('b');
        if (b) b.textContent = active ? 'ON' : 'OFF';
      });
    }

    // Reset view
    const btnReset = document.getElementById('btnResetView');
    if (btnReset) {
      btnReset.addEventListener('click', () => this.resetCameraView());
    }

    // Snapshot button
    const btnSnap = document.getElementById('btnSnapshot');
    if (btnSnap) {
      btnSnap.addEventListener('click', (e) => this.takeSnapshot(e.shiftKey ? 4 : 2));
    }

    // Video record button
    const btnRec = document.getElementById('btnRecord');
    if (btnRec) {
      btnRec.addEventListener('click', () => this.toggleRecordVideo());
    }

    // Compass click -> snap to north
    if (this.compassRose) {
      this.compassRose.addEventListener('click', () => this.snapToNorth());
    }
  }

  lonLatToSceneMeters(lon, lat, eleMeters) {
    if (!this.originLonLat) return new THREE.Vector3(0, 0, 0);
    const meanLat = (this.originLonLat.lat * Math.PI) / 180.0;
    const dx = (lon - this.originLonLat.lon) * 111320.0 * Math.cos(meanLat);
    const dz = -(lat - this.originLonLat.lat) * 110574.0;
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

  selectProfile(profileKey) {
    if (!this.routeFeatures.some((feature) => (feature?.properties?.profile_key || 'adult') === profileKey)) return;
    if (this.activeProfileKey === profileKey && this.selectedProfileKeys.has(profileKey)) return;
    this.activeProfileKey = profileKey;
    this.selectedProfileKeys.add(profileKey);
    this.refreshActiveRoute(true);
  }

  toggleProfileAnimation(profileKey) {
    if (this.selectedProfileKeys.has(profileKey)) this.selectedProfileKeys.delete(profileKey);
    else this.selectedProfileKeys.add(profileKey);
    if (profileKey === this.activeProfileKey && !this.selectedProfileKeys.has(profileKey)) {
      this.activeProfileKey = [...this.selectedProfileKeys][0] || null;
    }
    this.refreshActiveRoute(false);
    this.updateAvatarPosition();
  }

  setProfileOpacity(profileKey, value) {
    const opacity = Math.max(0.0, Math.min(1.0, Number(value)));
    this.profileOpacity.set(profileKey, opacity);
    this.applyProfileAppearance();
  }

  applyProfileAppearance() {
    const opacityFor = (key) => this.profileOpacity.has(key) ? this.profileOpacity.get(key) : 1.0;
    this.routeVisuals.forEach((visual) => {
      const opacity = opacityFor(visual.key);
      if (visual.rig?.subMeshGroup) {
        visual.rig.subMeshGroup.traverse((node) => {
          if (!node.material) return;
          const materials = Array.isArray(node.material) ? node.material : [node.material];
          materials.forEach((material) => {
            material.transparent = opacity < 1.0;
            material.opacity = opacity;
            material.needsUpdate = true;
          });
        });
      }
    });
    this.routeOverlayGroup.children.forEach((line) => {
      const key = line.userData?.profileKey;
      if (key && line.material) {
        line.material.opacity = opacityFor(key);
        line.material.transparent = line.material.opacity < 1.0;
      }
    });
    const activeOpacity = opacityFor(this.activeProfileKey);
    [this.centerlineMesh, this.glowTubeMesh].forEach((mesh) => {
      if (!mesh?.material) return;
      mesh.material.opacity = activeOpacity;
      mesh.material.transparent = activeOpacity < 1.0;
      mesh.material.needsUpdate = true;
    });
  }

  profileColor(feature) {
    const palette = ['#0284c7', '#8b5cf6', '#d946ef', '#10b981', '#f59e0b', '#ef4444', '#14b8a6', '#6366f1'];
    const color = feature?.properties?.profile_color;
    if (typeof color === 'string' && color.trim()) return color;
    const index = Math.max(0, this.routeFeatures.indexOf(feature));
    return palette[index % palette.length];
  }

  renderLayerPanel() {
    if (!this.elLayerList) return;
    this.elLayerList.innerHTML = '';
    if (this.elLayerCount) this.elLayerCount.textContent = String(this.routeFeatures.length);
    if (!this.routeFeatures.length) {
      this.elLayerList.innerHTML = '<div class="layer-empty">Waiting for QGIS routes...</div>';
      return;
    }
    this.routeFeatures.forEach((feature) => {
      const props = feature.properties || {};
      const key = props.profile_key || 'adult';
      const color = this.profileColor(feature);
      const row = document.createElement('div');
      row.className = `layer-row${key === this.activeProfileKey ? ' active' : ''}${this.selectedProfileKeys.has(key) ? ' selected' : ''}`;
      row.style.setProperty('--layer-color', color);
      row.title = `${props.profile_name || key} | ${Number(props.distance_km || 0).toFixed(2)} km | ${Number(props.duration_min || 0).toFixed(1)} min | +${Number(props.elevation_gain_m || 0).toFixed(1)} m | ${Number(props.max_slope_pct || 0).toFixed(1)}% | ${Number(props.calories_kcal || 0).toFixed(0)} kcal`;
      row.addEventListener('click', () => this.selectProfile(key));

      const toggle = document.createElement('button');
      toggle.className = `layer-toggle${this.selectedProfileKeys.has(key) ? ' on' : ''}`;
      toggle.type = 'button';
      toggle.textContent = this.selectedProfileKeys.has(key) ? '●' : '○';
      toggle.setAttribute('aria-label', `Toggle ${props.profile_name || key} animation`);
      toggle.addEventListener('click', (event) => {
        event.stopPropagation();
        this.toggleProfileAnimation(key);
      });

      const text = document.createElement('div');
      text.className = 'layer-name';
      text.textContent = props.profile_name || key;
      const meta = document.createElement('span');
      meta.className = 'layer-meta';
      meta.textContent = `${Number(props.distance_km || 0).toFixed(2)} km · ${Number(props.duration_min || 0).toFixed(1)} min`;
      text.appendChild(meta);

      const opacity = document.createElement('input');
      opacity.type = 'range';
      opacity.className = 'layer-opacity';
      opacity.min = '0';
      opacity.max = '100';
      opacity.step = '1';
      opacity.value = String(Math.round((this.profileOpacity.get(key) ?? 1.0) * 100));
      opacity.title = 'Layer opacity';
      opacity.addEventListener('click', (event) => event.stopPropagation());
      opacity.addEventListener('input', (event) => {
        event.stopPropagation();
        this.setProfileOpacity(key, Number(event.target.value) / 100.0);
      });

      const swatch = document.createElement('span');
      swatch.className = 'layer-swatch';
      row.appendChild(toggle);
      row.appendChild(text);
      row.appendChild(opacity);
      row.appendChild(swatch);
      this.elLayerList.appendChild(row);
    });
    this.renderComparisonTable();
  }

  // Side-by-side numbers for every computed profile, with the difference to
  // the selected one, so routes can be compared without hovering each layer.
  renderComparisonTable() {
    const host = document.getElementById('compareTable');
    if (!host) return;
    host.innerHTML = '';
    if (this.routeFeatures.length < 2) return;
    const active = this.routeFeatures.find((f) => (f?.properties?.profile_key || 'adult') === this.activeProfileKey);
    const ref = active?.properties || {};
    const cols = [
      ['Profile', null, null],
      ['km', 'distance_km', 2],
      ['min', 'duration_min', 1],
      ['climb m', 'elevation_gain_m', 0],
      ['max %', 'max_slope_pct', 1],
    ];
    const table = document.createElement('table');
    table.className = 'compare-table';
    const caption = document.createElement('caption');
    caption.textContent = 'Compared with the selected profile';
    table.appendChild(caption);
    const head = table.createTHead().insertRow();
    cols.forEach(([label]) => {
      const th = document.createElement('th');
      th.scope = 'col';
      th.textContent = label;
      head.appendChild(th);
    });
    const body = table.createTBody();
    this.routeFeatures.forEach((feature) => {
      const props = feature.properties || {};
      const key = props.profile_key || 'adult';
      const tr = body.insertRow();
      if (key === this.activeProfileKey) tr.className = 'active';
      cols.forEach(([, field, digits], i) => {
        const cell = i === 0 ? document.createElement('th') : tr.insertCell();
        if (i === 0) {
          cell.scope = 'row';
          cell.style.setProperty('--layer-color', this.profileColor(feature));
          cell.textContent = props.profile_name || key;
          tr.appendChild(cell);
          return;
        }
        const value = Number(props[field] || 0);
        cell.textContent = value.toFixed(digits);
        if (key !== this.activeProfileKey && Number.isFinite(Number(ref[field]))) {
          const diff = value - Number(ref[field]);
          if (Math.abs(diff) >= Math.pow(10, -digits) / 2) {
            const delta = document.createElement('span');
            delta.className = diff > 0 ? 'delta up' : 'delta down';
            delta.textContent = ` ${diff > 0 ? '+' : ''}${diff.toFixed(digits)}`;
            cell.appendChild(delta);
          }
        }
      });
    });
    host.appendChild(table);
  }

  updateHudMetrics() {
    if (!this.routeData || !this.routeData.properties) {
      if (this.elDist) this.elDist.textContent = '—';
      if (this.elTime) this.elTime.textContent = '—';
      if (this.elClimb) this.elClimb.textContent = '—';
      if (this.elSlope) this.elSlope.textContent = '—';
      if (this.elKcal) this.elKcal.textContent = '—';
      return;
    }
    const props = this.routeData.properties || {};
    const dist = Number(props.distance_km || 0.0);
    const dur = Number(props.duration_min || 0.0);
    const climb = Number(props.elevation_gain_m || 0.0);
    const slope = Number(props.max_slope_pct || 0.0);
    const kcal = Number(props.calories_kcal || 0.0);

    if (this.elDist) this.elDist.textContent = `${dist.toFixed(2)} km`;
    if (this.elTime) this.elTime.textContent = `${dur.toFixed(1)} min`;
    if (this.elClimb) this.elClimb.textContent = `+${climb.toFixed(1)} m`;
    if (this.elSlope) this.elSlope.textContent = `${slope.toFixed(1)}%`;
    if (this.elKcal) this.elKcal.textContent = `${kcal.toFixed(0)} kcal`;
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
    this.updateAvatarPosition();
  }

  setTerrainGrid(grid) {
    const valid = grid && Array.isArray(grid.bbox) && grid.bbox.length === 4
      && Number.isInteger(grid.cols) && Number.isInteger(grid.rows) && grid.cols >= 2 && grid.rows >= 2
      && Array.isArray(grid.heights) && grid.heights.length === grid.cols * grid.rows
      && grid.bbox.every(Number.isFinite) && grid.heights.every(Number.isFinite);
    this.terrainGrid = valid ? grid : null;
  }

  sceneToLonLat(x, z) {
    const meanLat = (this.originLonLat.lat * Math.PI) / 180.0;
    return {
      lon: this.originLonLat.lon + x / (111320.0 * Math.cos(meanLat)),
      lat: this.originLonLat.lat - z / 110574.0,
    };
  }

  // Ground height (scene y) under a scene point: the DEM grid when QGIS sent
  // one, otherwise the approximate surface fitted through the route.
  groundY(x, z) {
    const g = this.terrainGrid;
    if (!g || !this.originLonLat) return this.getTerrainElevationAt(x, z);
    const { lon, lat } = this.sceneToLonLat(x, z);
    const [west, south, east, north] = g.bbox;
    const fx = Math.max(0, Math.min(g.cols - 1, ((lon - west) / (east - west)) * (g.cols - 1)));
    const fy = Math.max(0, Math.min(g.rows - 1, ((north - lat) / (north - south)) * (g.rows - 1)));
    const c0 = Math.floor(fx);
    const r0 = Math.floor(fy);
    const c1 = Math.min(g.cols - 1, c0 + 1);
    const r1 = Math.min(g.rows - 1, r0 + 1);
    const tx = fx - c0;
    const ty = fy - r0;
    const h = (r, c) => g.heights[r * g.cols + c];
    const top = h(r0, c0) * (1 - tx) + h(r0, c1) * tx;
    const bottom = h(r1, c0) * (1 - tx) + h(r1, c1) * tx;
    const elevation = top * (1 - ty) + bottom * ty;
    return (elevation - this.baseElevation) * this.elevationExaggeration;
  }

  getTerrainElevationAt(x, z) {
    if (!this.curve || !this.scenePoints.length) return 0.0;
    const sampleSteps = 32;
    let bestDistSq = Infinity;
    let nearestY = this.scenePoints[0].y;
    for (let s = 0; s <= sampleSteps; s++) {
      const u = s / sampleSteps;
      const spt = this.curve.getPointAt(u);
      const distSq = (x - spt.x) * (x - spt.x) + (z - spt.z) * (z - spt.z);
      if (distSq < bestDistSq) {
        bestDistSq = distSq;
        nearestY = spt.y;
      }
    }
    return nearestY;
  }

  buildTerrain() {
    if (!this.scenePoints.length) return;
    const built = this.terrainGrid ? this.buildDemTerrainGeometry() : this.buildApproximateTerrainGeometry();
    const { geo, width, depth, center, nx, ny } = built;
    this.terrainSource = this.terrainGrid ? 'dem' : 'approximate';
    this.updateTerrainNote();
    const posAttr = geo.attributes.position;
    let minTerrainY = Infinity;
    for (let i = 0; i < posAttr.count; i++) minTerrainY = Math.min(minTerrainY, posAttr.getY(i));
    posAttr.needsUpdate = true;
    geo.computeVertexNormals();

    this.terrainWidth = width;
    this.terrainDepth = depth;
    this.terrainCenter = center;

    // Load real Slippy Map basemap tiles (OSM, Satellite, Voyager, Dark)
    const basemapTexture = this.loadRealBasemapTiles(width, depth, center);

    const mat = new THREE.MeshStandardMaterial({
      map: basemapTexture,
      roughness: 0.85,
      metalness: 0.05,
    });

    this.terrainMesh = new THREE.Mesh(geo, mat);
    this.terrainMesh.receiveShadow = true;
    this.terrainMesh.visible = this.showBasemap;
    this.scene.add(this.terrainMesh);

    // -------------------------------------------------------------
    // Tight Architectural Diorama Plinth Skirt (5m below min point)
    // -------------------------------------------------------------
    const baseSkirtY = minTerrainY - 5.0;
    const stride = nx + 1;
    const skirtVerts = [];
    const skirtNorms = [];

    const addSkirtQuad = (x1, y1, z1, x2, y2, z2) => {
      const dx = x2 - x1;
      const dz = z2 - z1;
      const len = Math.sqrt(dx * dx + dz * dz) || 1.0;
      const nx = dz / len;
      const nz = -dx / len;

      skirtVerts.push(
        x1, y1, z1,        x1, baseSkirtY, z1,  x2, baseSkirtY, z2,
        x1, y1, z1,        x2, baseSkirtY, z2,  x2, y2, z2
      );
      for (let k = 0; k < 6; k++) {
        skirtNorms.push(nx, 0, nz);
      }
    };

    // North Edge (row 0: ix from 0 to nx-1)
    for (let ix = 0; ix < nx; ix++) {
      const i1 = ix;
      const i2 = ix + 1;
      addSkirtQuad(
        posAttr.getX(i1), posAttr.getY(i1), posAttr.getZ(i1),
        posAttr.getX(i2), posAttr.getY(i2), posAttr.getZ(i2)
      );
    }
    // East Edge (col nx: iy from 0 to ny-1)
    for (let iy = 0; iy < ny; iy++) {
      const i1 = iy * stride + nx;
      const i2 = (iy + 1) * stride + nx;
      addSkirtQuad(
        posAttr.getX(i1), posAttr.getY(i1), posAttr.getZ(i1),
        posAttr.getX(i2), posAttr.getY(i2), posAttr.getZ(i2)
      );
    }
    // South Edge (row ny: ix from nx down to 1)
    for (let ix = nx; ix > 0; ix--) {
      const i1 = ny * stride + ix;
      const i2 = ny * stride + (ix - 1);
      addSkirtQuad(
        posAttr.getX(i1), posAttr.getY(i1), posAttr.getZ(i1),
        posAttr.getX(i2), posAttr.getY(i2), posAttr.getZ(i2)
      );
    }
    // West Edge (col 0: iy from ny down to 1)
    for (let iy = ny; iy > 0; iy--) {
      const i1 = iy * stride;
      const i2 = (iy - 1) * stride;
      addSkirtQuad(
        posAttr.getX(i1), posAttr.getY(i1), posAttr.getZ(i1),
        posAttr.getX(i2), posAttr.getY(i2), posAttr.getZ(i2)
      );
    }

    // Bottom Base Plinth Cap
    const nwX = posAttr.getX(0), nwZ = posAttr.getZ(0);
    const neX = posAttr.getX(nx), neZ = posAttr.getZ(nx);
    const seX = posAttr.getX(ny * stride + nx), seZ = posAttr.getZ(ny * stride + nx);
    const swX = posAttr.getX(ny * stride), swZ = posAttr.getZ(ny * stride);

    skirtVerts.push(
      nwX, baseSkirtY, nwZ,  swX, baseSkirtY, swZ,  seX, baseSkirtY, seZ,
      nwX, baseSkirtY, nwZ,  seX, baseSkirtY, seZ,  neX, baseSkirtY, neZ
    );
    for (let k = 0; k < 6; k++) {
      skirtNorms.push(0, -1, 0);
    }

    const skirtGeo = new THREE.BufferGeometry();
    skirtGeo.setAttribute('position', new THREE.Float32BufferAttribute(skirtVerts, 3));
    skirtGeo.setAttribute('normal', new THREE.Float32BufferAttribute(skirtNorms, 3));

    const skirtMat = new THREE.MeshStandardMaterial({
      color: 0x1e293b,
      roughness: 0.85,
      metalness: 0.15,
      side: THREE.DoubleSide,
    });

    this.skirtMesh = new THREE.Mesh(skirtGeo, skirtMat);
    this.skirtMesh.receiveShadow = true;
    this.skirtMesh.castShadow = true;
    this.skirtMesh.visible = this.showBasemap;
    this.scene.add(this.skirtMesh);
  }

  // Terrain mesh from the DEM grid QGIS sampled around the routes: one vertex
  // per grid sample, so relief away from the route is the real relief.
  buildDemTerrainGeometry() {
    const g = this.terrainGrid;
    const [west, south, east, north] = g.bbox;
    const nw = this.lonLatToSceneMeters(west, north, this.baseElevation);
    const se = this.lonLatToSceneMeters(east, south, this.baseElevation);
    const width = Math.max(1, se.x - nw.x);
    const depth = Math.max(1, se.z - nw.z);
    const center = new THREE.Vector3((nw.x + se.x) / 2, 0, (nw.z + se.z) / 2);
    const nx = g.cols - 1;
    const ny = g.rows - 1;
    const geo = new THREE.PlaneGeometry(width, depth, nx, ny);
    geo.rotateX(-Math.PI / 2);
    geo.translate(center.x, 0, center.z);
    // PlaneGeometry rows run north to south after the rotation, matching the
    // grid's north-first row order, so vertex i is grid sample i.
    const posAttr = geo.attributes.position;
    for (let i = 0; i < posAttr.count; i++) {
      posAttr.setY(i, (g.heights[i] - this.baseElevation) * this.elevationExaggeration - 0.35);
    }
    return { geo, width, depth, center, nx, ny };
  }

  // Without a DEM: a surface fitted through points along the route. Relief
  // away from the route is a guess, and the viewer says so (updateTerrainNote).
  buildApproximateTerrainGeometry() {
    // Collect all active route points, corridor buildings, and trees for unified diorama bounds
    const allRoutePoints = [];
    if (this.routeVisuals && this.routeVisuals.length) {
      this.routeVisuals.forEach((v) => {
        if (v.points) allRoutePoints.push(...v.points);
      });
    }
    if (!allRoutePoints.length) allRoutePoints.push(...this.scenePoints);
    this.corridorBuildings().forEach((bld) => {
      const coords = bld.polygon || bld.coordinates || [];
      coords.forEach(([lon, lat]) => {
        allRoutePoints.push(this.lonLatToSceneMeters(lon, lat, this.baseElevation || 0.0));
      });
    });
    this.corridorTrees().forEach((tr) => {
      const coords = tr.coordinates || [];
      if (coords.length >= 2) {
        allRoutePoints.push(this.lonLatToSceneMeters(coords[0], coords[1], this.baseElevation || 0.0));
      }
    });

    const box = new THREE.Box3().setFromPoints(allRoutePoints);
    const size = new THREE.Vector3();
    box.getSize(size);
    const center = new THREE.Vector3();
    box.getCenter(center);
    const pad = Math.max(50, Math.min(140, Math.max(size.x, size.z) * 0.12));
    const width = Math.max(size.x + pad * 2, 100);
    const depth = Math.max(size.z + pad * 2, 100);

    const segments = 96;
    const geo = new THREE.PlaneGeometry(width, depth, segments, segments);
    geo.rotateX(-Math.PI / 2);
    geo.translate(center.x, 0, center.z);
    const posAttr = geo.attributes.position;

    const routeSamples = [];
    const sampleSteps = Math.max(30, Math.min(180, this.scenePoints.length * 2));
    for (let s = 0; s <= sampleSteps; s++) {
      routeSamples.push(this.curve.getPointAt(s / sampleSteps));
    }

    // Least-squares plane through the route for the regional slope.
    let sumX = 0, sumZ = 0, sumY = 0, sumXX = 0, sumZZ = 0, sumXZ = 0, sumXY = 0, sumZY = 0;
    const n = this.scenePoints.length;
    for (const p of this.scenePoints) {
      const x = p.x - center.x;
      const z = p.z - center.z;
      sumX += x; sumZ += z; sumY += p.y;
      sumXX += x * x; sumZZ += z * z; sumXZ += x * z;
      sumXY += x * p.y; sumZY += z * p.y;
    }
    const denom = (sumXX * sumZZ - sumXZ * sumXZ);
    const gradX = Math.abs(denom) > 1e-4 ? (sumXY * sumZZ - sumZY * sumXZ) / denom : 0;
    const gradZ = Math.abs(denom) > 1e-4 ? (sumZY * sumXX - sumXY * sumXZ) / denom : 0;
    const meanY = sumY / Math.max(1, n);

    for (let i = 0; i < posAttr.count; i++) {
      const vx = posAttr.getX(i);
      const vz = posAttr.getZ(i);
      let weightedY = 0;
      let totalWeight = 0;
      let minDist = Infinity;
      for (const sample of routeSamples) {
        const dist = Math.hypot(vx - sample.x, vz - sample.z);
        if (dist < minDist) minDist = dist;
        const w = 1.0 / Math.pow(Math.max(10.0, dist), 1.6);
        weightedY += sample.y * w;
        totalWeight += w;
      }
      const localRouteEle = totalWeight > 0 ? (weightedY / totalWeight) : meanY;
      const regionalEle = meanY + gradX * (vx - center.x) + gradZ * (vz - center.z);
      const blend = Math.max(0.0, Math.min(1.0, (minDist - 20.0) / 60.0));
      posAttr.setY(i, (1.0 - blend) * localRouteEle + blend * regionalEle - 0.35);
    }
    return { geo, width, depth, center, nx: segments, ny: segments };
  }

  corridorBuildings() {
    const list = this.routeData?.properties?.corridor_buildings || this.routeCollection?.properties?.corridor_buildings;
    return Array.isArray(list) ? list : [];
  }

  corridorTrees() {
    const list = this.routeData?.properties?.corridor_trees || this.routeCollection?.properties?.corridor_trees;
    return Array.isArray(list) ? list : [];
  }

  updateTerrainNote() {
    const el = document.getElementById('terrainNote');
    if (!el) return;
    if (this.terrainSource === 'dem') {
      const g = this.terrainGrid;
      const cell = Number(g.cell_m);
      const coverage = Number(g.coverage);
      el.textContent = `Terrain: DEM grid${Number.isFinite(cell) ? ` ~${cell.toFixed(0)} m` : ''}`
        + (Number.isFinite(coverage) && coverage < 0.999 ? ` (${Math.round(coverage * 100)}% sampled)` : '');
      el.classList.remove('approximate');
    } else {
      el.textContent = 'Terrain: approximate (no DEM selected in QGIS)';
      el.classList.add('approximate');
    }
  }

  updateBasemapTexture() {
    if (!this.terrainMesh || !this.terrainWidth) return;
    if (this.terrainMesh.material && this.terrainMesh.material.map) {
      this.terrainMesh.material.map.dispose();
    }
    const newTex = this.loadRealBasemapTiles(this.terrainWidth, this.terrainDepth, this.terrainCenter);
    this.terrainMesh.material.map = newTex;
    this.terrainMesh.material.needsUpdate = true;
  }

  loadRealBasemapTiles(width, depth, center) {
    const textureResolution = 4096;
    const canvas = document.createElement('canvas');
    canvas.width = textureResolution;
    canvas.height = textureResolution;
    const ctx = canvas.getContext('2d');

    // Background color based on provider; no fake grid is drawn while tiles load.
    const isDark = this.basemapProvider === 'dark';
    const isSat = this.basemapProvider === 'satellite';
    ctx.fillStyle = isDark ? '#0f172a' : (isSat ? '#1e293b' : '#f8fafc');
    ctx.fillRect(0, 0, textureResolution, textureResolution);

    const tex = new THREE.CanvasTexture(canvas);
    tex.wrapS = THREE.ClampToEdgeWrapping;
    tex.wrapT = THREE.ClampToEdgeWrapping;
    tex.generateMipmaps = true;
    tex.minFilter = THREE.LinearMipmapLinearFilter;
    tex.magFilter = THREE.LinearFilter;
    if (this.renderer?.capabilities) {
      tex.anisotropy = Math.min(8, this.renderer.capabilities.getMaxAnisotropy());
    }

    if (!this.originLonLat) return tex;

    const latRad = (this.originLonLat.lat * Math.PI) / 180.0;
    const mPerDegLon = Math.max(1.0, 111320.0 * Math.cos(latRad));
    const mPerDegLat = 110574.0;

    const minLon = this.originLonLat.lon + (center.x - width / 2) / mPerDegLon;
    const maxLon = this.originLonLat.lon + (center.x + width / 2) / mPerDegLon;
    const minLat = this.originLonLat.lat - (center.z + depth / 2) / mPerDegLat;
    const maxLat = this.originLonLat.lat - (center.z - depth / 2) / mPerDegLat;

    const lon2tile = (lon, z) => {
      const numTiles = Math.pow(2, z);
      return Math.max(0, Math.min(numTiles - 1, Math.floor(((lon + 180) / 360) * numTiles)));
    };
    const lat2tile = (lat, z) => {
      const numTiles = Math.pow(2, z);
      const clampedLat = Math.max(-85.0511, Math.min(85.0511, lat));
      const rad = (clampedLat * Math.PI) / 180.0;
      const val = (1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2;
      return Math.max(0, Math.min(numTiles - 1, Math.floor(val * numTiles)));
    };
    const tile2lon = (x, z) => (x / Math.pow(2, z)) * 360 - 180;
    const tile2lat = (y, z) => {
      const n = Math.PI - (2 * Math.PI * y) / Math.pow(2, z);
      return (180 / Math.PI) * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n)));
    };

    // Calculate optimal zoom level dynamically:
    // Highest zoom level where the entire model area is covered by <= 8 tiles along each axis
    // to guarantee crisp detail and zero gaps across 100% of the terrain surface.
    const MAX_TILES_AXIS = 8;
    let zoom = 19;
    while (zoom > 10) {
      const minX = lon2tile(minLon, zoom);
      const maxX = lon2tile(maxLon, zoom);
      const minY = lat2tile(maxLat, zoom);
      const maxY = lat2tile(minLat, zoom);
      const countX = maxX - minX + 1;
      const countY = maxY - minY + 1;
      if (countX <= MAX_TILES_AXIS && countY <= MAX_TILES_AXIS) {
        break;
      }
      zoom--;
    }

    const minTileX = lon2tile(minLon, zoom);
    const maxTileX = lon2tile(maxLon, zoom);
    const minTileY = lat2tile(maxLat, zoom);
    const maxTileY = lat2tile(minLat, zoom);

    const getTileUrl = (x, y, z) => {
      if (this.basemapProvider === 'satellite') {
        return `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${z}/${y}/${x}`;
      } else if (this.basemapProvider === 'voyager') {
        return `https://basemaps.cartocdn.com/rastertiles/voyager/${z}/${x}/${y}.png`;
      } else if (this.basemapProvider === 'dark') {
        return `https://basemaps.cartocdn.com/rastertiles/dark_all/${z}/${x}/${y}.png`;
      }
      return `https://tile.openstreetmap.org/${z}/${x}/${y}.png`;
    };

    const lonSpan = maxLon - minLon || 1.0;
    const latSpan = maxLat - minLat || 1.0;

    // Tiles requested for a previous scene must not write into a texture that
    // rebuildScene() has already disposed. Track this build and abort the old one.
    this.cancelPendingTiles();
    const buildToken = { cancelled: false, images: [], uploadTimer: null };
    this.pendingTileBuild = buildToken;
    const totalTiles = (maxTileX - minTileX + 1) * (maxTileY - minTileY + 1);
    let failedTiles = 0;
    let doneTiles = 0;
    if (this.elBasemapNote) this.elBasemapNote.textContent = '';

    // Uploading a 4096 px texture after every tile re-sent 64 MB to the GPU up
    // to 64 times; uploads are now batched every 250 ms and once at the end.
    const scheduleUpload = (final) => {
      if (final) {
        clearTimeout(buildToken.uploadTimer);
        buildToken.uploadTimer = null;
        tex.needsUpdate = true;
        this.requestRender();
        return;
      }
      if (buildToken.uploadTimer !== null) return;
      buildToken.uploadTimer = setTimeout(() => {
        buildToken.uploadTimer = null;
        if (buildToken.cancelled) return;
        tex.needsUpdate = true;
        this.requestRender();
      }, 250);
    };
    const tileFinished = () => {
      doneTiles += 1;
      if (doneTiles < totalTiles) {
        scheduleUpload(false);
        return;
      }
      if (failedTiles === totalTiles) {
        // Offline or blocked: show the real relief instead of a blank plate.
        this.drawHillshade(ctx, textureResolution, { minLon, maxLon, minLat, maxLat }, isDark);
        if (this.elBasemapNote) {
          this.elBasemapNote.textContent = this.terrainGrid
            ? 'Basemap tiles unavailable (offline?) - showing DEM hillshade.'
            : 'Basemap tiles unavailable (offline?).';
        }
      } else if (failedTiles > 0 && this.elBasemapNote) {
        this.elBasemapNote.textContent = `${failedTiles} of ${totalTiles} basemap tiles failed to load.`;
      }
      scheduleUpload(true);
    };

    // Map tiles are Web Mercator: within a tile, rows are not evenly spaced in
    // latitude, while the terrain is. Each tile is drawn in horizontal strips
    // placed at their true latitudes so roads line up with the route.
    const STRIPS = 8;
    for (let tx = minTileX; tx <= maxTileX; tx++) {
      for (let ty = minTileY; ty <= maxTileY; ty++) {
        const img = new Image();
        buildToken.images.push(img);
        img.crossOrigin = 'anonymous';
        img.onload = () => {
          if (buildToken.cancelled) return;
          const px0 = ((tile2lon(tx, zoom) - minLon) / lonSpan) * textureResolution;
          const px1 = ((tile2lon(tx + 1, zoom) - minLon) / lonSpan) * textureResolution;
          const srcStrip = img.height / STRIPS;
          for (let k = 0; k < STRIPS; k++) {
            const latTop = tile2lat(ty + k / STRIPS, zoom);
            const latBottom = tile2lat(ty + (k + 1) / STRIPS, zoom);
            const py0 = ((maxLat - latTop) / latSpan) * textureResolution;
            const py1 = ((maxLat - latBottom) / latSpan) * textureResolution;
            ctx.drawImage(
              img, 0, k * srcStrip, img.width, srcStrip,
              px0, py0, Math.max(1, px1 - px0), Math.max(0.5, py1 - py0),
            );
          }
          tileFinished();
        };
        img.onerror = () => {
          if (buildToken.cancelled) return;
          failedTiles += 1;
          tileFinished();
        };
        img.src = getTileUrl(tx, ty, zoom);
      }
    }

    return tex;
  }

  // Shaded relief from the DEM grid, drawn into the basemap canvas when no
  // tiles could be loaded. Without a grid, a neutral plate stays.
  drawHillshade(ctx, size, extent, dark) {
    const g = this.terrainGrid;
    if (!g) return;
    const [west, south, east, north] = g.bbox;
    const midLat = ((south + north) / 2) * Math.PI / 180;
    const dx = ((east - west) / (g.cols - 1)) * 111320 * Math.cos(midLat);
    const dy = ((north - south) / (g.rows - 1)) * 110574;
    const img = ctx.createImageData(g.cols, g.rows);
    const h = (r, c) => g.heights[Math.max(0, Math.min(g.rows - 1, r)) * g.cols + Math.max(0, Math.min(g.cols - 1, c))];
    // Light from the north-west, 45 degrees up (the cartographic convention).
    const lx = -0.5;
    const ly = 0.5;
    const lz = Math.SQRT1_2;
    for (let r = 0; r < g.rows; r++) {
      for (let c = 0; c < g.cols; c++) {
        // z-factor 5: city-scale relief is gentle and would barely show.
        const sx = (h(r, c + 1) - h(r, c - 1)) / (2 * dx) * 5.0;
        const sy = (h(r - 1, c) - h(r + 1, c)) / (2 * dy) * 5.0;
        const len = Math.hypot(sx, sy, 1);
        const shade = Math.max(0, (-sx * lx - sy * ly + lz) / len);
        const v = dark ? 25 + shade * 110 : 95 + shade * 160;
        const i = (r * g.cols + c) * 4;
        img.data[i] = v;
        img.data[i + 1] = v;
        img.data[i + 2] = v + (dark ? 10 : 0);
        img.data[i + 3] = 255;
      }
    }
    const tmp = document.createElement('canvas');
    tmp.width = g.cols;
    tmp.height = g.rows;
    tmp.getContext('2d').putImageData(img, 0, 0);
    const lonSpan = extent.maxLon - extent.minLon;
    const latSpan = extent.maxLat - extent.minLat;
    const x0 = ((west - extent.minLon) / lonSpan) * size;
    const x1 = ((east - extent.minLon) / lonSpan) * size;
    const y0 = ((extent.maxLat - north) / latSpan) * size;
    const y1 = ((extent.maxLat - south) / latSpan) * size;
    ctx.imageSmoothingEnabled = true;
    ctx.drawImage(tmp, x0, y0, x1 - x0, y1 - y0);
  }

  updateBasemapAttribution() {
    if (!this.elBasemapCredit) return;
    // Each provider requires its own credit line to be displayed.
    const credits = {
      osm: '© OpenStreetMap contributors',
      voyager: '© OpenStreetMap contributors · © CARTO',
      dark: '© OpenStreetMap contributors · © CARTO',
      satellite: 'Imagery © Esri, Maxar, Earthstar Geographics',
    };
    this.elBasemapCredit.textContent = credits[this.basemapProvider] || credits.osm;
    if (this.elBasemapNote) this.elBasemapNote.textContent = '';
  }

  cancelPendingTiles() {
    const pending = this.pendingTileBuild;
    if (!pending) return;
    pending.cancelled = true;
    clearTimeout(pending.uploadTimer);
    // Dropping the src aborts the in-flight request in every current browser.
    pending.images.forEach((img) => {
      img.onload = null;
      img.onerror = null;
      img.src = '';
    });
    this.pendingTileBuild = null;
  }

  buildClassyRoadRibbon() {
    if (!this.activePath || this.scenePoints.length < 2) return;
    const profColor = new THREE.Color(this.profileColor(this.routeData));
    const side = THREE.DoubleSide;

    // Layer 1: asphalt band, 7.5 m wide, 0.8 m above the route line.
    this.roadMesh = new THREE.Mesh(
      ribbonGeometry(this.activePath, 7.5, 0.8),
      new THREE.MeshStandardMaterial({
        color: 0x1e293b, roughness: 0.7, metalness: 0.15, side,
        polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -2,
      }),
    );
    this.roadMesh.receiveShadow = true;
    this.roadMesh.renderOrder = 8;
    this.scene.add(this.roadMesh);

    // Layer 2: translucent glow band in the profile colour.
    this.glowTubeMesh = new THREE.Mesh(
      ribbonGeometry(this.activePath, 2.6, 1.1),
      new THREE.MeshBasicMaterial({
        color: profColor, transparent: true, opacity: 0.35, side,
        polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -4,
      }),
    );
    this.glowTubeMesh.renderOrder = 9;
    this.scene.add(this.glowTubeMesh);

    // Layer 3: bright centre line.
    this.centerlineMesh = new THREE.Mesh(
      ribbonGeometry(this.activePath, 0.9, 1.4),
      new THREE.MeshStandardMaterial({
        color: 0xffffff, emissive: profColor, emissiveIntensity: 0.95, roughness: 0.15, side,
        polygonOffset: true, polygonOffsetFactor: -3, polygonOffsetUnits: -6,
      }),
    );
    this.centerlineMesh.renderOrder = 10;
    this.scene.add(this.centerlineMesh);

    // Layer 4: pulse beacon (moves ahead of the walker during playback).
    this.pulseBeacon = new THREE.Mesh(
      new THREE.SphereGeometry(1.6, 16, 16),
      new THREE.MeshBasicMaterial({ color: 0x38bdf8, transparent: true, opacity: 0.9 }),
    );
    this.scene.add(this.pulseBeacon);
  }

  buildPinMarkers() {
    if (!this.scenePoints.length) return;
    const pStart = this.scenePoints[0].clone();
    const pEnd = this.scenePoints[this.scenePoints.length - 1].clone();

    // Helper: Canvas texture for 3D circular letter badge
    const createLetterTexture = (letter, bgColorHex, textColorHex = '#ffffff') => {
      const c = document.createElement('canvas');
      c.width = 256;
      c.height = 256;
      const cx = c.getContext('2d');
      cx.fillStyle = bgColorHex;
      cx.beginPath();
      cx.arc(128, 128, 120, 0, Math.PI * 2);
      cx.fill();
      cx.lineWidth = 14;
      cx.strokeStyle = '#ffffff';
      cx.stroke();
      cx.fillStyle = textColorHex;
      cx.font = 'bold 140px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
      cx.textAlign = 'center';
      cx.textBaseline = 'middle';
      cx.fillText(letter, 128, 134);
      const t = new THREE.CanvasTexture(c);
      t.minFilter = THREE.LinearFilter;
      return t;
    };

    // Helper: Floating billboard badge sprite with geolocator icon + Letter + Label
    const createFloatingBadgeSprite = (letter, label, colorHex) => {
      const measureCanvas = document.createElement('canvas');
      const mCtx = measureCanvas.getContext('2d');
      const textFont = 'bold 36px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
      mCtx.font = textFont;
      const textMetrics = mCtx.measureText(label);
      const textWidth = Math.ceil(textMetrics.width);

      const circleD = 60;
      const padLeft = 14;
      const spacing = 16;
      const padRight = 26;
      const pillW = padLeft + circleD + spacing + textWidth + padRight;
      const pillH = 86;
      const margin = 14;

      const canvasW = Math.ceil(pillW + margin * 2);
      const canvasH = Math.ceil(pillH + margin * 2);

      const canvas = document.createElement('canvas');
      canvas.width = canvasW;
      canvas.height = canvasH;
      const ctx = canvas.getContext('2d');

      const x = margin;
      const y = margin;
      const r = pillH / 2;

      ctx.save();
      // Drop Shadow
      ctx.shadowColor = 'rgba(0, 0, 0, 0.45)';
      ctx.shadowBlur = 12;
      ctx.shadowOffsetX = 0;
      ctx.shadowOffsetY = 6;

      // Outer pill background
      ctx.fillStyle = colorHex;
      ctx.beginPath();
      if (typeof ctx.roundRect === 'function') {
        ctx.roundRect(x, y, pillW, pillH, r);
      } else {
        ctx.moveTo(x + r, y);
        ctx.lineTo(x + pillW - r, y);
        ctx.quadraticCurveTo(x + pillW, y, x + pillW, y + r);
        ctx.lineTo(x + pillW, y + pillH - r);
        ctx.quadraticCurveTo(x + pillW, y + pillH, x + pillW - r, y + pillH);
        ctx.lineTo(x + r, y + pillH);
        ctx.quadraticCurveTo(x, y + pillH, x, y + pillH - r);
        ctx.lineTo(x, y + r);
        ctx.quadraticCurveTo(x, y, x + r, y);
        ctx.closePath();
      }
      ctx.fill();

      // Crisp white border
      ctx.shadowColor = 'transparent';
      ctx.lineWidth = 4;
      ctx.strokeStyle = '#ffffff';
      ctx.stroke();

      // White circle for letter badge on the left
      const badgeCenterX = x + padLeft + circleD / 2;
      const badgeCenterY = y + pillH / 2;
      ctx.fillStyle = '#ffffff';
      ctx.beginPath();
      ctx.arc(badgeCenterX, badgeCenterY, circleD / 2, 0, Math.PI * 2);
      ctx.fill();

      // Bold letter inside circle
      ctx.fillStyle = colorHex;
      ctx.font = 'bold 44px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(letter, badgeCenterX, badgeCenterY + 2);

      // Label text
      ctx.fillStyle = '#ffffff';
      ctx.font = textFont;
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      ctx.fillText(label, x + padLeft + circleD + spacing, y + pillH / 2 + 1);

      ctx.restore();

      const texture = new THREE.CanvasTexture(canvas);
      texture.minFilter = THREE.LinearFilter;
      texture.magFilter = THREE.LinearFilter;
      const mat = new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false });
      const sprite = new THREE.Sprite(mat);
      const spriteHeight = 7.5;
      const spriteWidth = (canvasW / canvasH) * spriteHeight;
      sprite.scale.set(spriteWidth, spriteHeight, 1);
      return sprite;
    };

    const createGeolocatorPin = (letter, label, colorHex, colorCss) => {
      const pinGroup = new THREE.Group();

      // 1. Inverted cone pointer to ground (tip touches ground at y=0)
      const coneGeo = new THREE.ConeGeometry(3.2, 10, 24);
      coneGeo.rotateX(Math.PI);
      const pinMat = new THREE.MeshStandardMaterial({
        color: colorHex,
        roughness: 0.3,
        metalness: 0.2,
        emissive: colorHex,
        emissiveIntensity: 0.25,
      });
      const cone = new THREE.Mesh(coneGeo, pinMat);
      cone.position.y = 5.0;
      cone.castShadow = true;
      pinGroup.add(cone);

      // 2. Upper bulb / circular head
      const sphereGeo = new THREE.SphereGeometry(3.6, 24, 24);
      const sphere = new THREE.Mesh(sphereGeo, pinMat);
      sphere.position.y = 11.5;
      sphere.castShadow = true;
      pinGroup.add(sphere);

      // 3. 3D circular letter badges on front and back of the sphere
      const letterTex = createLetterTexture(letter, colorCss, '#ffffff');
      const discGeo = new THREE.CircleGeometry(2.6, 24);
      const discMat = new THREE.MeshBasicMaterial({ map: letterTex, side: THREE.DoubleSide });

      const frontDisc = new THREE.Mesh(discGeo, discMat);
      frontDisc.position.set(0, 11.5, 3.65);
      pinGroup.add(frontDisc);

      const backDisc = new THREE.Mesh(discGeo, discMat);
      backDisc.position.set(0, 11.5, -3.65);
      backDisc.rotateY(Math.PI);
      pinGroup.add(backDisc);

      // 4. Ground Target / Radar Ring
      const ringGeo = new THREE.RingGeometry(2.0, 4.8, 32);
      ringGeo.rotateX(-Math.PI / 2);
      const ringMat = new THREE.MeshBasicMaterial({
        color: colorHex,
        side: THREE.DoubleSide,
        transparent: true,
        opacity: 0.8,
      });
      const ring = new THREE.Mesh(ringGeo, ringMat);
      ring.position.y = 0.15;
      pinGroup.add(ring);

      // 5. Floating Billboard Badge Sprite (always faces camera, clear at any distance)
      const badgeSprite = createFloatingBadgeSprite(letter, label, colorCss);
      badgeSprite.position.y = 20.5;
      pinGroup.add(badgeSprite);

      return pinGroup;
    };

    // Point A: Emerald Green (#059669)
    const pinA = createGeolocatorPin('A', 'Point A (Origin)', 0x059669, '#059669');
    pinA.position.copy(pStart);
    this.pinsGroup.add(pinA);

    // Point B: Ruby Red (#dc2626)
    const pinB = createGeolocatorPin('B', 'Point B (Destination)', 0xdc2626, '#dc2626');
    pinB.position.copy(pEnd);
    this.pinsGroup.add(pinB);
  }

  buildAvatar() {
    const activeKey = this.activeProfileKey || this.routeData?.properties?.profile_key || 'adult';
    const activeColor = this.profileColor(this.routeData);
    this.avatarRig.setProfile(activeKey, activeColor);
    this.avatarRigs = [this.avatarRig];

    this.routeVisuals.forEach((visual) => {
      const color = this.profileColor(visual.feature);
      if (visual.key === activeKey) {
        visual.rig = this.avatarRig;
        return;
      }
      const rig = new KinematicAvatarRig(this.scene);
      rig.setProfile(visual.key, color);
      visual.rig = rig;
      this.avatarRigs.push(rig);
    });
    this.buildRouteOverlays();
    this.applyProfileAppearance();
  }

  // The other profiles: coloured ribbons wide enough to compare at any zoom
  // (WebGL draws THREE.Line one pixel wide on most systems), stacked a few
  // centimetres apart so overlapping routes do not flicker.
  buildRouteOverlays() {
    while (this.routeOverlayGroup.children.length) {
      const child = this.routeOverlayGroup.children[0];
      this.routeOverlayGroup.remove(child);
      disposeHierarchy(child);
    }
    let layer = 0;
    this.routeVisuals.forEach((visual) => {
      if (visual.key === this.activeProfileKey || visual.points.length < 2) return;
      layer += 1;
      const material = new THREE.MeshBasicMaterial({
        color: this.profileColor(visual.feature), transparent: true, opacity: 0.85, side: THREE.DoubleSide,
        polygonOffset: true, polygonOffsetFactor: -1 - layer, polygonOffsetUnits: -2 - layer,
      });
      const ribbon = new THREE.Mesh(ribbonGeometry(visual.path, 2.4, 0.9 + layer * 0.12), material);
      ribbon.userData.profileKey = visual.key;
      ribbon.renderOrder = 7;
      this.routeOverlayGroup.add(ribbon);
    });
  }

  buildUrbanEnvironment() {
    this.buildBuildings(this.corridorBuildings());
    this.buildTrees(this.corridorTrees());
    this.buildingsGroup.visible = this.showBuildings;
    this.treesGroup.visible = this.showTrees;
  }

  // Buildings: one merged mesh per facade material (five draw calls in all)
  // instead of one mesh per building. Each footprint sits on the lowest ground
  // under its corners, so no building floats above a slope.
  buildBuildings(buildings) {
    const palette = [0xf8fafc, 0xe2e8f0, 0xcbd5e1, 0x94a3b8, 0x64748b];
    const buckets = palette.map(() => []);
    buildings.forEach((bld, idx) => {
      const coords = bld.polygon || bld.coordinates || [];
      if (coords.length < 3) return;
      const scenePts = coords.map(([lon, lat]) => this.lonLatToSceneMeters(lon, lat, this.baseElevation));
      let groundY = Infinity;
      scenePts.forEach((p) => {
        groundY = Math.min(groundY, this.groundY(p.x, p.z));
      });
      if (!Number.isFinite(groundY)) {
        const fallbackEle = bld.base_elevation_m !== undefined ? Number(bld.base_elevation_m) : this.baseElevation;
        groundY = (fallbackEle - this.baseElevation) * this.elevationExaggeration;
      }
      const shape = new THREE.Shape();
      shape.moveTo(scenePts[0].x, -scenePts[0].z);
      for (let i = 1; i < scenePts.length; i++) shape.lineTo(scenePts[i].x, -scenePts[i].z);
      shape.closePath();
      const height = Math.max(6.0, Number(bld.height_m) || (bld.levels ? bld.levels * 3.2 : 12.0));
      const geo = new THREE.ExtrudeGeometry(shape, {
        depth: height, bevelEnabled: true, bevelSegments: 1, steps: 1, bevelSize: 0.2, bevelThickness: 0.2,
      });
      geo.rotateX(-Math.PI / 2);
      geo.translate(0, groundY, 0);
      buckets[idx % palette.length].push(geo);
    });
    buckets.forEach((geos, i) => {
      if (!geos.length) return;
      const merged = mergeGeometries(geos);
      geos.forEach((g) => g.dispose());
      const mat = new THREE.MeshStandardMaterial({ color: palette[i], roughness: 0.7, metalness: 0.08 });
      const mesh = new THREE.Mesh(merged, mat);
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      this.buildingsGroup.add(mesh);
    });
  }

  // Trees: one InstancedMesh per part and colour (seven draw calls for any
  // number of trees) instead of four to five meshes, each with its own
  // material, per tree.
  buildTrees(trees) {
    const parts = {
      trunk: { geo: new THREE.CylinderGeometry(0.75, 1.15, 1, 7).translate(0, 0.5, 0), color: 0x452b19, flat: false, mats: [] },
      leaf0: { geo: new THREE.DodecahedronGeometry(1, 1), color: 0x15803d, flat: true, mats: [] },
      leaf1: { geo: new THREE.DodecahedronGeometry(1, 1), color: 0x16a34a, flat: true, mats: [] },
      leaf2: { geo: new THREE.DodecahedronGeometry(1, 1), color: 0x22c55e, flat: true, mats: [] },
      pine0: { geo: new THREE.ConeGeometry(1, 1, 7), color: 0x14532d, flat: true, mats: [] },
      pine1: { geo: new THREE.ConeGeometry(1, 1, 7), color: 0x166534, flat: true, mats: [] },
      pine2: { geo: new THREE.ConeGeometry(1, 1, 7), color: 0x15803d, flat: true, mats: [] },
    };
    const m = new THREE.Matrix4();
    const q = new THREE.Quaternion();
    const place = (part, x, y, z, sx, sy, sz) => {
      parts[part].mats.push(m.compose(new THREE.Vector3(x, y, z), q, new THREE.Vector3(sx, sy, sz)).clone());
    };
    trees.forEach((tree) => {
      const coords = tree.coordinates || [];
      if (coords.length < 2) return;
      const pos = this.lonLatToSceneMeters(Number(coords[0]), Number(coords[1]), this.baseElevation);
      const y0 = this.groundY(pos.x, pos.z);
      const height = Math.max(4.5, Number(tree.height_m || 8.0));
      const canopyR = Math.max(1.8, Number(tree.canopy_radius_m || 3.2));
      const trunkH = Math.max(1.2, Number(tree.trunk_height_m || (height * 0.35)));
      const trunkR = Math.max(0.18, Number(tree.trunk_radius_m || 0.28));
      place('trunk', pos.x, y0, pos.z, trunkR, trunkH, trunkR);
      if (tree.tree_type === 'conifer' || tree.tree_type === 'pine') {
        const crown = height - trunkH;
        place('pine0', pos.x, y0 + trunkH + crown * 0.26, pos.z, canopyR, crown * 0.52, canopyR);
        place('pine1', pos.x, y0 + trunkH + crown * 0.52, pos.z, canopyR * 0.78, crown * 0.48, canopyR * 0.78);
        place('pine2', pos.x, y0 + trunkH + crown * 0.78, pos.z, canopyR * 0.52, crown * 0.42, canopyR * 0.52);
      } else {
        place('leaf0', pos.x, y0 + trunkH + canopyR * 0.72, pos.z, canopyR * 0.95 * 1.1, canopyR * 0.95 * 0.82, canopyR * 0.95 * 1.05);
        place('leaf1', pos.x, y0 + trunkH + canopyR * 1.18, pos.z, canopyR * 0.82 * 0.96, canopyR * 0.82 * 0.88, canopyR * 0.82 * 0.96);
        place('leaf2', pos.x, y0 + trunkH + canopyR * 1.58, pos.z, canopyR * 0.58 * 0.85, canopyR * 0.58 * 0.95, canopyR * 0.58 * 0.85);
      }
    });
    Object.values(parts).forEach((part) => {
      if (!part.mats.length) {
        part.geo.dispose();
        return;
      }
      const mat = new THREE.MeshStandardMaterial({
        color: part.color, roughness: part.flat ? 0.78 : 0.92, metalness: 0.04, flatShading: part.flat,
      });
      const mesh = new THREE.InstancedMesh(part.geo, mat, part.mats.length);
      part.mats.forEach((matrix, i) => mesh.setMatrixAt(i, matrix));
      mesh.instanceMatrix.needsUpdate = true;
      mesh.computeBoundingSphere();
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      this.treesGroup.add(mesh);
    });
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

  updateMetricLiveReadout() {
    if (!this.activeMetrics || !this.activeMetrics.length || !this.routeData) return;
    const safeProgress = Math.max(0.0, Math.min(1.0, this.progress));

    this.activeMetrics.forEach((m) => {
      if (!m.elVal) return;
      const n = m.values.length;
      if (n === 0) {
        m.elVal.textContent = '—';
        return;
      }
      // Value at the walker's distance along the route.
      const total = m.distances[n - 1] || 1;
      const target = safeProgress * total;
      let idx = 0;
      while (idx < n - 1 && m.distances[idx + 1] <= target) idx += 1;
      const val = m.values[idx];
      m.elVal.textContent = (val !== null && val !== undefined && !isNaN(val)) ? m.formatter(val) : 'no data';
    });
  }

  updateRadarMap() {
    if (!this.radarCtx) return;
    const ctx = this.radarCtx;
    ctx.clearRect(0, 0, 130, 130);
    if (!this.curve || !this.scenePoints.length) return;

    const box = new THREE.Box3().setFromPoints(this.scenePoints);
    const size = new THREE.Vector3();
    box.getSize(size);
    const min = box.min;
    const maxDim = Math.max(size.x, size.z, 50);

    const toRadar = (p) => {
      const rx = 15 + ((p.x - min.x) / maxDim) * 100;
      const ry = 115 - ((p.z - min.z) / maxDim) * 100;
      return { x: rx, y: ry };
    };

    ctx.beginPath();
    ctx.strokeStyle = this.routeData?.properties?.profile_color || '#0284c7';
    ctx.lineWidth = 3.5;
    this.scenePoints.forEach((p, i) => {
      const r = toRadar(p);
      if (i === 0) ctx.moveTo(r.x, r.y);
      else ctx.lineTo(r.x, r.y);
    });
    ctx.stroke();

    if (this.scenePoints.length >= 2 && this.activePath) {
      const safeProgress = Math.max(0.0, Math.min(1.0, this.progress));
      const av = toRadar(this.activePath.at(safeProgress).point);
      ctx.beginPath();
      ctx.fillStyle = '#ef4444';
      ctx.arc(av.x, av.y, 4.5, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  snapToNorth() {
    if (!this.controls) return;
    const target = this.controls.target.clone();
    const offset = this.camera.position.clone().sub(target);
    const radius = Math.sqrt(offset.x * offset.x + offset.z * offset.z);
    const distance = Math.max(radius, 60);
    this.camera.position.set(target.x, target.y + Math.max(offset.y, 40), target.z + distance);
    this.controls.target.copy(target);
    this.controls.update();
  }

  // Caption burnt into snapshots and videos: profile, totals, the walker's
  // position, and the basemap credit the tile providers require.
  drawCaptureHud(ctx, w, h, scale) {
    const props = this.routeData?.properties || {};
    const pad = 14 * scale;
    const font = (size, weight = 600) => `${weight} ${size * scale}px -apple-system, "Segoe UI", Roboto, sans-serif`;
    const lines = [];
    if (this.routeData) {
      lines.push([font(15, 700), props.profile_name || props.profile_key || 'Route']);
      lines.push([font(13), `${Number(props.distance_km || 0).toFixed(2)} km · ${Number(props.duration_min || 0).toFixed(1)} min · +${Number(props.elevation_gain_m || 0).toFixed(0)} m`]);
      if (this.chart) lines.push([font(12, 500), this.chart.tip.textContent]);
    }
    if (lines.length) {
      ctx.save();
      let boxW = 0;
      lines.forEach(([f, t]) => { ctx.font = f; boxW = Math.max(boxW, ctx.measureText(t).width); });
      const lineH = 20 * scale;
      const boxH = lines.length * lineH + pad;
      ctx.fillStyle = 'rgba(15, 23, 42, 0.72)';
      ctx.fillRect(pad, h - boxH - pad, boxW + pad * 2, boxH);
      ctx.fillStyle = SAFE_COLOR.test(String(props.profile_color || '')) ? props.profile_color : '#38bdf8';
      ctx.fillRect(pad, h - boxH - pad, 4 * scale, boxH);
      ctx.fillStyle = '#ffffff';
      ctx.textBaseline = 'top';
      lines.forEach(([f, t], i) => {
        ctx.font = f;
        ctx.fillText(t, pad * 2, h - boxH - pad + pad / 2 + i * lineH);
      });
      ctx.restore();
    }
    const credit = this.elBasemapCredit?.textContent || '';
    if (credit && this.showBasemap) {
      ctx.save();
      ctx.font = font(11, 500);
      const tw = ctx.measureText(credit).width;
      ctx.fillStyle = 'rgba(255, 255, 255, 0.8)';
      ctx.fillRect(w - tw - pad * 1.5, h - 22 * scale, tw + pad, 18 * scale);
      ctx.fillStyle = '#334155';
      ctx.textBaseline = 'middle';
      ctx.fillText(credit, w - tw - pad, h - 13 * scale);
      ctx.restore();
    }
  }

  // Snapshot at twice the screen resolution (Shift-click: four times),
  // capped by the GPU's maximum texture size, with the caption.
  takeSnapshot(scale = 2) {
    const size = this.renderer.getSize(new THREE.Vector2());
    const oldRatio = this.renderer.getPixelRatio();
    const maxTex = this.renderer.capabilities.maxTextureSize || 4096;
    const ratio = Math.max(1, Math.min(scale, maxTex / Math.max(size.x, size.y, 1)));
    this.renderer.setPixelRatio(ratio);
    this.renderer.setSize(size.x, size.y, false);
    this.renderer.render(this.scene, this.camera);
    const src = this.renderer.domElement;
    const out = document.createElement('canvas');
    out.width = src.width;
    out.height = src.height;
    const ctx = out.getContext('2d');
    ctx.drawImage(src, 0, 0);
    this.drawCaptureHud(ctx, out.width, out.height, ratio);
    this.renderer.setPixelRatio(oldRatio);
    this.renderer.setSize(size.x, size.y, false);
    this.requestRender();
    out.toBlob((blob) => {
      if (!blob) return;
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `02Route3D_${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.png`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
    }, 'image/png');
  }

  pickVideoFormat() {
    if (typeof MediaRecorder === 'undefined') return null;
    // Safari records MP4/H.264 only; Chrome and Firefox record WebM.
    const candidates = [
      ['video/webm;codecs=vp9', 'webm'],
      ['video/webm;codecs=vp8', 'webm'],
      ['video/webm', 'webm'],
      ['video/mp4;codecs=avc1', 'mp4'],
      ['video/mp4', 'mp4'],
    ];
    const hit = candidates.find(([type]) => MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(type));
    return hit ? { mimeType: hit[0], ext: hit[1] } : null;
  }

  drawRecordingFrame() {
    const rec = this.recording;
    if (!rec) return;
    const src = this.renderer.domElement;
    if (rec.canvas.width !== src.width || rec.canvas.height !== src.height) {
      rec.canvas.width = src.width;
      rec.canvas.height = src.height;
    }
    rec.ctx.drawImage(src, 0, 0);
    this.drawCaptureHud(rec.ctx, rec.canvas.width, rec.canvas.height, this.renderer.getPixelRatio());
  }

  // Video with the caption: frames are composited onto a 2D canvas right
  // after each render (no preserveDrawingBuffer needed) and that canvas is
  // recorded.
  toggleRecordVideo() {
    const btnRecord = document.getElementById('btnRecord');
    if (this.isRecording) {
      if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') this.mediaRecorder.stop();
      this.isRecording = false;
      if (btnRecord) {
        btnRecord.classList.remove('recording');
        btnRecord.setAttribute('aria-pressed', 'false');
      }
      return;
    }
    const format = this.pickVideoFormat();
    const canvas = document.createElement('canvas');
    if (!format || typeof canvas.captureStream !== 'function') {
      setLiveStatus('Video recording is not supported in this browser.', 'warning');
      return;
    }
    this.recording = { canvas, ctx: canvas.getContext('2d'), format };
    this.drawRecordingFrame();
    this.recordedChunks = [];
    try {
      this.mediaRecorder = new MediaRecorder(canvas.captureStream(30), { mimeType: format.mimeType });
    } catch (err) {
      setLiveStatus(`Video recording failed to start: ${err.message}`, 'warning');
      this.recording = null;
      return;
    }
    this.mediaRecorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) this.recordedChunks.push(e.data);
    };
    this.mediaRecorder.onstop = () => {
      const blob = new Blob(this.recordedChunks, { type: format.mimeType.split(';')[0] });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `02Route3D_${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.${format.ext}`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
      this.recording = null;
    };
    this.mediaRecorder.start(1000);
    this.isRecording = true;
    if (btnRecord) {
      btnRecord.classList.add('recording');
      btnRecord.setAttribute('aria-pressed', 'true');
    }
  }

  resetCameraView() {
    if (!this.scenePoints.length) return;
    const box = new THREE.Box3().setFromPoints(this.scenePoints);
    const center = new THREE.Vector3();
    box.getCenter(center);
    const size = new THREE.Vector3();
    box.getSize(size);

    const maxDim = Math.max(size.x, size.z, 120);
    this.camera.position.set(center.x, center.y + maxDim * 0.9, center.z + maxDim * 1.0);
    this.controls.target.copy(center);
    this.controls.update();
    if (this.controls.saveState) {
      this.controls.saveState();
    }
  }

  buildWormPath(values, width, height) {
    const n = values.length;
    if (n < 2) return '';
    const known = values.filter((v) => v !== null && v !== undefined && !isNaN(v));
    if (!known.length) return '';
    const minVal = Math.min(...known);
    const maxVal = Math.max(...known);
    const valRange = Math.max(0.001, maxVal - minVal);
    const yMid = height / 2.0;
    const maxHalfT = Math.max(2.0, Math.min(8.0, (height - 6.0) / 2.0));
    const minHalfT = 1.4;

    const topPoints = [];
    const bottomPoints = [];

    for (let i = 0; i < n; i++) {
      const v = values[i];
      const s = i / Math.max(1, n - 1);
      const x = s * width;
      const cleanV = (v !== null && v !== undefined && !isNaN(v)) ? v : minVal;
      const u = Math.max(0.0, Math.min(1.0, (cleanV - minVal) / valRange));
      const env = Math.pow(Math.sin(Math.PI * s), 0.65);
      const halfT = (minHalfT + u * (maxHalfT - minHalfT)) * env;
      topPoints.push(`${x.toFixed(1)},${(yMid - halfT).toFixed(1)}`);
      bottomPoints.push(`${x.toFixed(1)},${(yMid + halfT).toFixed(1)}`);
    }

    let d = `M ${topPoints[0]} `;
    for (let i = 1; i < topPoints.length; i++) {
      d += `L ${topPoints[i]} `;
    }
    for (let i = bottomPoints.length - 1; i >= 0; i--) {
      d += `L ${bottomPoints[i]} `;
    }
    d += 'Z';
    return d;
  }

  // Per-point profile of the active route: distance (m), elevation, slope,
  // speed and the optional raster values. QGIS sends it densified (~6 m); the
  // old code read it by route-vertex index, so the values came from the
  // wrong places along the route.
  profileSeries() {
    const profList = (this.routeData?.properties?.elevation_profile || []).filter(
      (p) => p && Number.isFinite(Number(p.distance_m)) && Number.isFinite(Number(p.elevation_m)),
    );
    if (profList.length >= 2) {
      return profList.map((p) => ({
        d: Number(p.distance_m),
        z: Number(p.elevation_m),
        slope: Number(p.slope_pct) || 0,
        speed: Number(p.speed_kmh),
        lst: p.lst_normalized,
        green: p.ndvi_normalized,
      }));
    }
    // No profile sent (older payloads): fall back to the vertices.
    const coords = this.routeData?.geometry?.coordinates || [];
    const cumulative = this.activePath ? this.activePath.cumulative : coords.map((_, i) => i);
    return coords.map((c, i) => ({ d: cumulative[i] || 0, z: Number(c[2]) || 0, slope: 0, speed: NaN }));
  }

  renderProfileChart() {
    if (this.elMultiMetricRows) this.elMultiMetricRows.innerHTML = '';
    this.activeMetrics = [];
    this.renderElevationChart();
    if (!this.routeData || !this.elMultiMetricRows) return;
    const series = this.profileSeries();
    if (series.length < 2) return;

    const trackWidth = Math.max(100, (this.elMultiMetricRows.clientWidth || 600) - 190);
    const trackHeight = 22;
    const metricsDef = [
      { id: 'slope', name: 'Slope', icon: '📐', color: '#f59e0b', key: 'slope',
        formatter: (v) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%` },
      { id: 'speed', name: 'Speed', icon: '⚡', color: '#6366f1', key: 'speed',
        formatter: (v) => `${v.toFixed(1)} km/h` },
    ];
    // Heat and greenery appear only when a real raster was supplied.
    if (series.some((p) => p.lst !== undefined && p.lst !== null)) {
      metricsDef.push({ id: 'lst', name: 'Heat (LST)', icon: '🌡️', color: '#ef4444', key: 'lst', scale: 100,
        formatter: (v) => `${v.toFixed(0)}%` });
    }
    if (series.some((p) => p.green !== undefined && p.green !== null)) {
      metricsDef.push({ id: 'greenery', name: 'Greenery', icon: '🌳', color: '#10b981', key: 'green', scale: 100,
        formatter: (v) => `${v.toFixed(0)}%` });
    }

    metricsDef.forEach((metric) => {
      const values = series.map((p) => {
        const v = p[metric.key];
        return v === null || v === undefined || !Number.isFinite(Number(v)) ? null : Number(v) * (metric.scale || 1);
      });
      const pathD = this.buildWormPath(values, trackWidth, trackHeight);
      if (!pathD) return;
      const safeColor = SAFE_COLOR.test(metric.color) ? metric.color : '#0284c7';

      const row = document.createElement('div');
      row.className = 'metric-worm-row';
      row.dataset.metric = metric.id;
      const info = document.createElement('div');
      info.className = 'metric-row-info';
      const iconSpan = document.createElement('span');
      iconSpan.className = 'metric-row-icon';
      iconSpan.setAttribute('aria-hidden', 'true');
      iconSpan.textContent = metric.icon;
      const nameSpan = document.createElement('span');
      nameSpan.className = 'metric-row-name';
      nameSpan.textContent = metric.name;
      const valSpan = document.createElement('span');
      valSpan.className = 'metric-row-val';
      valSpan.id = `readout_${metric.id}`;
      valSpan.textContent = '—';
      info.append(iconSpan, nameSpan, valSpan);

      const track = document.createElement('div');
      track.className = 'metric-ribbon-track';
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      svg.setAttribute('class', 'metric-ribbon-svg');
      svg.setAttribute('preserveAspectRatio', 'none');
      svg.setAttribute('viewBox', `0 0 ${trackWidth} ${trackHeight}`);
      svg.setAttribute('aria-hidden', 'true');
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', pathD);
      path.setAttribute('fill', safeColor);
      path.setAttribute('fill-opacity', '0.82');
      path.setAttribute('stroke', safeColor);
      path.setAttribute('stroke-width', '1.0');
      svg.appendChild(path);
      const needle = document.createElement('div');
      needle.className = 'metric-needle';
      track.append(svg, needle);
      track.addEventListener('click', (e) => {
        const rect = track.getBoundingClientRect();
        this.setProgress((e.clientX - rect.left) / Math.max(1, rect.width));
      });
      row.append(info, track);
      this.elMultiMetricRows.appendChild(row);
      this.activeMetrics.push({ ...metric, values, distances: series.map((p) => p.d), elVal: valSpan, elNeedle: needle });
    });
    this.updateMetricLiveReadout();
  }

  setProgress(fraction) {
    this.progress = Math.max(0.0, Math.min(1.0, Number(fraction) || 0));
    this.updateAvatarPosition();
  }

  // Elevation profile with real axes: distance (km) across, elevation (m) up,
  // the area coloured by slope class. Hovering or arrow keys move the walker
  // in 3D; playback moves the chart cursor.
  renderElevationChart() {
    const host = document.getElementById('elevationChart');
    if (!host) return;
    host.innerHTML = '';
    this.chart = null;
    if (!this.routeData) return;
    const series = this.profileSeries();
    if (series.length < 2) return;
    const totalD = series[series.length - 1].d || 1;
    const step = Math.max(1, Math.floor(series.length / 600));
    const pts = series.filter((_, i) => i % step === 0 || i === series.length - 1);

    // The host is hidden while empty, so measure the panel it sits in.
    const W = Math.max(240, host.clientWidth || (host.parentElement?.clientWidth || 632) - 32);
    const H = 96;
    const m = { l: 46, r: 12, t: 8, b: 20 };
    const pw = W - m.l - m.r;
    const ph = H - m.t - m.b;
    let zMin = Infinity;
    let zMax = -Infinity;
    pts.forEach((p) => { zMin = Math.min(zMin, p.z); zMax = Math.max(zMax, p.z); });
    const yTicks = niceTicks(zMin, zMax, 3);
    const yLo = Math.min(zMin, yTicks[0]);
    const yHi = Math.max(zMax, yTicks[yTicks.length - 1], yLo + 1);
    const x = (d) => m.l + (d / totalD) * pw;
    const y = (z) => m.t + (1 - (z - yLo) / (yHi - yLo)) * ph;

    const NS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.setAttribute('width', String(W));
    svg.setAttribute('height', String(H));
    svg.setAttribute('class', 'elevation-svg');
    const el = (name, attrs, text) => {
      const node = document.createElementNS(NS, name);
      Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, String(v)));
      if (text !== undefined) node.textContent = text;
      svg.appendChild(node);
      return node;
    };

    yTicks.forEach((t) => {
      el('line', { x1: m.l, x2: W - m.r, y1: y(t), y2: y(t), class: 'grid' });
      el('text', { x: m.l - 6, y: y(t) + 3, class: 'tick', 'text-anchor': 'end' }, `${Math.round(t)} m`);
    });
    niceTicks(0, totalD / 1000, 5).forEach((t) => {
      if (t * 1000 > totalD + 1e-6) return;
      el('text', { x: x(t * 1000), y: H - 5, class: 'tick', 'text-anchor': 'middle' }, `${t} km`);
    });

    // Area under the line, one polygon per run of the same slope class.
    const slopeClass = (s) => {
      const a = Math.abs(s);
      return a < 3 ? 0 : a < 6 ? 1 : a < 10 ? 2 : 3;
    };
    const colors = ['#10b981', '#eab308', '#f97316', '#ef4444'];
    let runStart = 0;
    for (let i = 1; i <= pts.length; i++) {
      const cls = slopeClass(pts[Math.min(i, pts.length - 1)].slope);
      const runCls = slopeClass(pts[runStart].slope);
      if (i === pts.length || cls !== runCls) {
        const seg = pts.slice(runStart, Math.min(i + 1, pts.length));
        if (seg.length >= 2) {
          const d = `M${x(seg[0].d)},${y(yLo)} ${seg.map((p) => `L${x(p.d).toFixed(1)},${y(p.z).toFixed(1)}`).join(' ')} L${x(seg[seg.length - 1].d)},${y(yLo)} Z`;
          el('path', { d, fill: colors[runCls], 'fill-opacity': 0.55 });
        }
        runStart = i;
      }
    }
    el('path', {
      d: pts.map((p, i) => `${i ? 'L' : 'M'}${x(p.d).toFixed(1)},${y(p.z).toFixed(1)}`).join(' '),
      class: 'profile-line',
    });
    const cursor = el('line', { x1: m.l, x2: m.l, y1: m.t, y2: m.t + ph, class: 'cursor' });
    const dot = el('circle', { cx: m.l, cy: y(pts[0].z), r: 4, class: 'cursor-dot' });

    const climb = Number(this.routeData.properties?.elevation_gain_m || 0);
    host.setAttribute('role', 'slider');
    host.setAttribute('tabindex', '0');
    host.setAttribute('aria-label',
      `Elevation profile, ${(totalD / 1000).toFixed(2)} km, from ${Math.round(zMin)} to ${Math.round(zMax)} m, climb ${Math.round(climb)} m. Arrow keys move along the route.`);
    host.setAttribute('aria-valuemin', '0');
    host.setAttribute('aria-valuemax', '100');
    host.appendChild(svg);
    const tip = document.createElement('div');
    tip.className = 'chart-tip';
    host.appendChild(tip);

    const fromEvent = (e) => {
      const rect = svg.getBoundingClientRect();
      const px = ((e.clientX - rect.left) / Math.max(1, rect.width)) * W;
      return Math.max(0, Math.min(1, (px - m.l) / pw));
    };
    svg.addEventListener('pointermove', (e) => this.setProgress(fromEvent(e)));
    svg.addEventListener('pointerdown', (e) => this.setProgress(fromEvent(e)));
    host.addEventListener('keydown', (e) => {
      const stepFrac = e.shiftKey ? 0.05 : 0.01;
      if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
        e.preventDefault();
        e.stopPropagation();
        this.setProgress(this.progress + (e.key === 'ArrowRight' ? stepFrac : -stepFrac));
      }
    });
    this.chart = { series, totalD, x, y, cursor, dot, tip, host, W };
    this.updateChartCursor();
  }

  updateChartCursor() {
    const c = this.chart;
    if (!c) return;
    const d = Math.max(0, Math.min(1, this.progress)) * c.totalD;
    const s = c.series;
    let lo = 0;
    let hi = s.length - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (s[mid].d <= d) lo = mid;
      else hi = mid;
    }
    const span = s[hi].d - s[lo].d;
    const f = span > 0 ? (d - s[lo].d) / span : 0;
    const z = s[lo].z + (s[hi].z - s[lo].z) * f;
    const px = c.x(d);
    c.cursor.setAttribute('x1', px);
    c.cursor.setAttribute('x2', px);
    c.dot.setAttribute('cx', px);
    c.dot.setAttribute('cy', c.y(z));
    const text = `${(d / 1000).toFixed(2)} km · ${z.toFixed(1)} m · ${s[lo].slope >= 0 ? '+' : ''}${s[lo].slope.toFixed(1)}%`;
    c.tip.textContent = text;
    c.tip.style.left = `${Math.min(c.W - 170, Math.max(50, px + 8))}px`;
    c.host.setAttribute('aria-valuenow', String(Math.round(this.progress * 100)));
    c.host.setAttribute('aria-valuetext', text);
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

window.app3d = new Studio3DApp();

window.setRouteData = function (geojsonData) {
  if (window.app3d && geojsonData) {
    window.app3d.loadRoute(geojsonData);
  }
};

let lastLoadedPayload = null;
let routePollTimer = null;
let routeEvents = null;

function setLiveStatus(message, level = 'info') {
  const el = document.getElementById('liveStatus');
  if (!el) return;
  el.textContent = message || '';
  el.dataset.level = level;
  el.hidden = !message;
}

async function fetchCurrentRoute() {
  let res;
  try {
    res = await fetch(`data/current_route.json?t=${Date.now()}`, { cache: 'no-store' });
  } catch (err) {
    setLiveStatus('Cannot reach QGIS: the 02Route 3D server is not running. Reopen the 3D studio from QGIS.', 'error');
    return false;
  }
  if (res.status === 404) {
    // No route computed yet: the empty state explains what to do.
    setLiveStatus('');
    return false;
  }
  if (!res.ok) {
    setLiveStatus(`Could not load the route from QGIS (HTTP ${res.status}).`, 'error');
    return false;
  }
  let text;
  let data;
  try {
    text = await res.text();
    data = JSON.parse(text);
  } catch (err) {
    setLiveStatus(`The route file from QGIS is not valid JSON: ${err.message}`, 'error');
    return false;
  }
  setLiveStatus('');
  if (data && text !== lastLoadedPayload && window.app3d) {
    lastLoadedPayload = text;
    try {
      window.app3d.loadRoute(data);
    } catch (err) {
      setLiveStatus(`The route could not be shown: ${err.message}`, 'error');
      console.error(err);
    }
  }
  return Boolean(data);
}

function startPolling() {
  if (routePollTimer === null) routePollTimer = setInterval(fetchCurrentRoute, 1500);
}

// Live link to QGIS: the local server pushes an event when QGIS writes a new
// route, so the viewer no longer downloads the whole route every 1.5 s. If the
// event stream is unavailable it falls back to polling.
function startLiveSync() {
  if (!/^https?:$/.test(window.location.protocol)) return; // standalone HTML report
  if (!window.EventSource) {
    fetchCurrentRoute();
    startPolling();
    return;
  }
  let opened = false;
  routeEvents = new EventSource('events');
  routeEvents.addEventListener('open', () => {
    opened = true;
    setLiveStatus('');
  });
  routeEvents.addEventListener('route', () => fetchCurrentRoute());
  routeEvents.addEventListener('error', () => {
    if (!opened) {
      // Older server without /events: poll instead.
      routeEvents.close();
      routeEvents = null;
      fetchCurrentRoute();
      startPolling();
      return;
    }
    setLiveStatus('Live link to QGIS lost - reconnecting...', 'warning');
  });
}

// There is deliberately no built-in demo route: until QGIS computes one the
// studio shows its empty state rather than a fabricated route whose numbers
// would be indistinguishable from real ones.
startLiveSync();

window.addEventListener('beforeunload', () => {
  if (routePollTimer !== null) {
    clearInterval(routePollTimer);
    routePollTimer = null;
  }
  if (routeEvents) routeEvents.close();
});
