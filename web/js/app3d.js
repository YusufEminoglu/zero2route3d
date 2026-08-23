import * as THREE from './three.module.js';
import { OrbitControls } from './OrbitControls.js';
import { KinematicAvatarRig } from './KinematicAvatarRig.js';
import { TerrainSlicerSystem } from './TerrainSlicerSystem.js';
import { VoiceCueSystem } from './VoiceCueSystem.js';

function disposeHierarchy(obj) {
  if (!obj) return;
  obj.traverse((child) => {
    if (child.geometry) {
      child.geometry.dispose();
    }
    if (child.material) {
      if (Array.isArray(child.material)) {
        child.material.forEach((m) => {
          if (m.map) m.map.dispose();
          m.dispose();
        });
      } else {
        if (child.material.map) child.material.map.dispose();
        child.material.dispose();
      }
    }
  });
}

class Studio3DApp {
  constructor() {
    this.container = document.getElementById('canvasContainer');
    this.width = window.innerWidth;
    this.height = window.innerHeight;

    // Renderer
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
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
    this.activeScenario = 'shortest';

    // Feature Toggles & State
    this.isPlaying = false;
    this.progress = 0.0;
    this.playbackSpeed = 1.0;
    this.cameraMode = 'orbit'; // Default to Orbit camera
    this.basemapProvider = 'osm'; // 'osm', 'satellite', 'voyager', 'dark'
    this.activeChartMetric = 'elevation'; // 'elevation', 'slope', 'lst', 'greenery'
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

    this.clock = new THREE.Clock();
    this.animate = this.animate.bind(this);
    requestAnimationFrame(this.animate);
  }

  setupLighting() {
    this.hemiLight = new THREE.HemisphereLight(0xffffff, 0xe2e8f0, 0.95);
    this.scene.add(this.hemiLight);

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
    this.elNeedle = document.getElementById('profileNeedle');
    this.elSvg = document.getElementById('profileSvg');
    this.lblExag = document.getElementById('lblExag');
    this.elMetricLiveVal = document.getElementById('metricLiveVal');
    this.elMetricReadout = document.getElementById('metricReadout');
    this.elLayerList = document.getElementById('layerList');
    this.elLayerCount = document.getElementById('layerCount');
  }

  bindEvents() {
    window.addEventListener('resize', () => {
      this.width = window.innerWidth;
      this.height = window.innerHeight;
      this.camera.aspect = this.width / Math.max(1, this.height);
      this.camera.updateProjectionMatrix();
      this.renderer.setSize(this.width, this.height);
      if (this.routeData) this.renderProfileChart();
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
        this.updateBasemapTexture();
      });
    });

    // Multi-Metric Chart Switcher
    document.querySelectorAll('.metric-btn').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        document.querySelectorAll('.metric-btn').forEach((b) => b.classList.remove('active'));
        e.currentTarget.classList.add('active');
        this.activeChartMetric = e.currentTarget.dataset.metric || 'elevation';
        this.renderProfileChart();
        this.updateAvatarPosition();
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
        if (this.routeData) this.rebuildScene();
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
      btnSnap.addEventListener('click', () => this.takeSnapshot());
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
      this.curve = null;
      if (this.elSvg) this.elSvg.innerHTML = '';
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
    const elevations = coords.map((c) => (c[2] !== undefined ? c[2] : 0.0));
    this.baseElevation = Math.min(...elevations);
    this.originLonLat = { lon: coords[0][0], lat: coords[0][1] };
    this.scenePoints = coords.map((c) => this.lonLatToSceneMeters(c[0], c[1], c[2] || 0.0));
    this.curve = new THREE.CatmullRomCurve3(this.scenePoints, false, 'catmullrom', 0.15);
    this.routeVisuals = this.routeFeatures
      .filter((feature) => this.selectedProfileKeys.has(feature?.properties?.profile_key || 'adult'))
      .map((feature) => {
        const featureCoords = feature.geometry.coordinates;
        const points = featureCoords.map((c) => this.lonLatToSceneMeters(c[0], c[1], c[2] || 0.0));
        return {
          key: feature?.properties?.profile_key || 'adult',
          feature,
          points,
          curve: new THREE.CatmullRomCurve3(points, false, 'catmullrom', 0.15),
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
    const scenarioProfiles = { ada: 'wheelchair', cycle: 'bicycle', green: 'senior' };
    const targetKey = scenarioProfiles[scenarioKey] || this.routeCollection?.properties?.primary_profile_key || this.activeProfileKey;
    if (targetKey && targetKey !== this.activeProfileKey && this.routeFeatures.some((feature) => (feature?.properties?.profile_key || 'adult') === targetKey)) {
      this.selectProfile(targetKey);
    } else {
      this.updateHudMetrics();
    }
  }

  clearSceneObjects() {
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

    // Collect all active route points for unified tight corridor bounds
    const allRoutePoints = [];
    if (this.routeVisuals && this.routeVisuals.length) {
      this.routeVisuals.forEach((v) => {
        if (v.points) allRoutePoints.push(...v.points);
      });
    }
    if (!allRoutePoints.length) allRoutePoints.push(...this.scenePoints);

    const box = new THREE.Box3().setFromPoints(allRoutePoints);
    const size = new THREE.Vector3();
    box.getSize(size);
    const center = new THREE.Vector3();
    box.getCenter(center);

    // Tight 30m architectural corridor diorama framing
    const pad = Math.max(35, Math.min(60, Math.max(size.x, size.z) * 0.08));
    const width = Math.max(size.x + pad * 2, 80);
    const depth = Math.max(size.z + pad * 2, 80);

    const segments = 96;
    const geo = new THREE.PlaneGeometry(width, depth, segments, segments);
    geo.rotateX(-Math.PI / 2);
    geo.translate(center.x, 0, center.z);

    // 3D Topographic Elevation Surface Interpolation
    const posAttr = geo.attributes.position;
    const count = posAttr.count;

    const routeSamples = [];
    const sampleSteps = Math.max(30, Math.min(180, this.scenePoints.length * 2));
    for (let s = 0; s <= sampleSteps; s++) {
      const u = s / sampleSteps;
      const spt = this.curve.getPointAt(u);
      routeSamples.push(spt);
    }

    // Topographic regional gradient plane
    let sumX = 0, sumZ = 0, sumY = 0, sumXX = 0, sumZZ = 0, sumXZ = 0, sumXY = 0, sumZY = 0;
    const n = this.scenePoints.length;
    for (let p of this.scenePoints) {
      const x = p.x - center.x;
      const z = p.z - center.z;
      const y = p.y;
      sumX += x; sumZ += z; sumY += y;
      sumXX += x * x; sumZZ += z * z; sumXZ += x * z;
      sumXY += x * y; sumZY += z * y;
    }
    const denom = (sumXX * sumZZ - sumXZ * sumXZ);
    const gradX = Math.abs(denom) > 1e-4 ? (sumXY * sumZZ - sumZY * sumXZ) / denom : 0;
    const gradZ = Math.abs(denom) > 1e-4 ? (sumZY * sumXX - sumXY * sumXZ) / denom : 0;
    const meanY = sumY / Math.max(1, n);

    let minTerrainY = Infinity;
    let maxTerrainY = -Infinity;

    for (let i = 0; i < count; i++) {
      const vx = posAttr.getX(i);
      const vz = posAttr.getZ(i);

      let weightedY = 0;
      let totalWeight = 0;
      let minDist = Infinity;

      for (let s of routeSamples) {
        const dx = vx - s.x;
        const dz = vz - s.z;
        const distSq = dx * dx + dz * dz;
        const dist = Math.sqrt(distSq);
        if (dist < minDist) minDist = dist;

        const w = 1.0 / Math.pow(Math.max(10.0, dist), 1.6);
        weightedY += s.y * w;
        totalWeight += w;
      }

      const localRouteEle = totalWeight > 0 ? (weightedY / totalWeight) : meanY;
      const regionalEle = meanY + gradX * (vx - center.x) + gradZ * (vz - center.z);

      const corridorRadius = 60.0;
      const blend = Math.max(0.0, Math.min(1.0, (minDist - 20.0) / corridorRadius));
      const finalY = (1.0 - blend) * localRouteEle + blend * regionalEle - 0.35;

      posAttr.setY(i, finalY);
      if (finalY < minTerrainY) minTerrainY = finalY;
      if (finalY > maxTerrainY) maxTerrainY = finalY;
    }

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
    // Tight Architectural Diorama Plinth Skirt (with beveled base)
    // -------------------------------------------------------------
    const baseSkirtY = minTerrainY - Math.max(12.0, (maxTerrainY - minTerrainY) * 0.35 + 8.0);
    const N = segments;
    const stride = N + 1;
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

    // North Edge (row 0: ix from 0 to N-1)
    for (let ix = 0; ix < N; ix++) {
      const i1 = ix;
      const i2 = ix + 1;
      addSkirtQuad(
        posAttr.getX(i1), posAttr.getY(i1), posAttr.getZ(i1),
        posAttr.getX(i2), posAttr.getY(i2), posAttr.getZ(i2)
      );
    }
    // East Edge (col N: iy from 0 to N-1)
    for (let iy = 0; iy < N; iy++) {
      const i1 = iy * stride + N;
      const i2 = (iy + 1) * stride + N;
      addSkirtQuad(
        posAttr.getX(i1), posAttr.getY(i1), posAttr.getZ(i1),
        posAttr.getX(i2), posAttr.getY(i2), posAttr.getZ(i2)
      );
    }
    // South Edge (row N: ix from N down to 1)
    for (let ix = N; ix > 0; ix--) {
      const i1 = N * stride + ix;
      const i2 = N * stride + (ix - 1);
      addSkirtQuad(
        posAttr.getX(i1), posAttr.getY(i1), posAttr.getZ(i1),
        posAttr.getX(i2), posAttr.getY(i2), posAttr.getZ(i2)
      );
    }
    // West Edge (col 0: iy from N down to 1)
    for (let iy = N; iy > 0; iy--) {
      const i1 = iy * stride;
      const i2 = (iy - 1) * stride;
      addSkirtQuad(
        posAttr.getX(i1), posAttr.getY(i1), posAttr.getZ(i1),
        posAttr.getX(i2), posAttr.getY(i2), posAttr.getZ(i2)
      );
    }

    // Bottom Base Plinth Cap
    const nwX = posAttr.getX(0), nwZ = posAttr.getZ(0);
    const neX = posAttr.getX(N), neZ = posAttr.getZ(N);
    const seX = posAttr.getX(stride * stride - 1), seZ = posAttr.getZ(stride * stride - 1);
    const swX = posAttr.getX(N * stride), swZ = posAttr.getZ(N * stride);

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

    // Pick zoom from target ground resolution instead of a fixed low-detail zoom.
    const maxDimensionMeters = Math.max(width, depth, 260);
    const targetPixels = 4096;
    const metersPerPixel = maxDimensionMeters / targetPixels;
    let zoom = Math.floor(Math.log2((156543.03392 * Math.max(0.1, Math.cos(latRad))) / metersPerPixel));
    zoom = Math.max(13, Math.min(19, Number.isFinite(zoom) ? zoom : 16));

    const numTiles = Math.pow(2, zoom);
    const lon2tile = (lon) => Math.max(0, Math.min(numTiles - 1, Math.floor(((lon + 180) / 360) * numTiles)));
    const lat2tile = (lat) => {
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

    const minTileX = lon2tile(minLon);
    const maxTileX = lon2tile(maxLon);
    const minTileY = lat2tile(maxLat);
    const maxTileY = lat2tile(minLat);

    // A maximum 6x6 tile window prevents tile storms while retaining detail.
    const spanX = Math.min(5, Math.max(0, maxTileX - minTileX));
    const spanY = Math.min(5, Math.max(0, maxTileY - minTileY));

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

    for (let tx = minTileX; tx <= minTileX + spanX; tx++) {
      for (let ty = minTileY; ty <= minTileY + spanY; ty++) {
        const img = new Image();
        img.crossOrigin = 'anonymous';
        img.onload = () => {
          const tMinLon = tile2lon(tx, zoom);
          const tMaxLon = tile2lon(tx + 1, zoom);
          const tMaxLat = tile2lat(ty, zoom);
          const tMinLat = tile2lat(ty + 1, zoom);

          const px0 = ((tMinLon - minLon) / lonSpan) * textureResolution;
          const px1 = ((tMaxLon - minLon) / lonSpan) * textureResolution;
          const py0 = ((maxLat - tMaxLat) / latSpan) * textureResolution;
          const py1 = ((maxLat - tMinLat) / latSpan) * textureResolution;

          ctx.drawImage(img, px0, py0, Math.max(1, px1 - px0), Math.max(1, py1 - py0));
          tex.needsUpdate = true;
        };
        img.onerror = () => {
          // Fallback gracefully on tile fetch error
        };
        img.src = getTileUrl(tx, ty, zoom);
      }
    }

    return tex;
  }

  buildClassyRoadRibbon() {
    if (!this.curve || this.scenePoints.length < 2) return;

    const tubularSegments = Math.max(160, this.scenePoints.length * 8);
    const roadWidth = 7.5;
    const profColorHex = this.routeData?.properties?.profile_color || '#0ea5e9';
    const profColor = new THREE.Color(profColorHex);

    // Layer 1: Road Asphalt Base (Floating +0.8m above ground to eliminate clipping)
    const roadGeo = new THREE.TubeGeometry(this.curve, tubularSegments, roadWidth * 0.5, 6, false);
    roadGeo.scale(1.0, 0.12, 1.0);

    const roadMat = new THREE.MeshStandardMaterial({
      color: 0x1e293b,
      roughness: 0.7,
      metalness: 0.15,
      polygonOffset: true,
      polygonOffsetFactor: -1,
      polygonOffsetUnits: -2,
    });

    this.roadMesh = new THREE.Mesh(roadGeo, roadMat);
    this.roadMesh.position.y += 0.8;
    this.roadMesh.receiveShadow = true;
    this.roadMesh.renderOrder = 8;
    this.scene.add(this.roadMesh);

    // Layer 2: Glowing Outer Neon Ribbon (+1.2m)
    const glowGeo = new THREE.TubeGeometry(this.curve, tubularSegments, 1.2, 6, false);
    glowGeo.scale(1.0, 0.2, 1.0);
    const glowMat = new THREE.MeshBasicMaterial({
      color: profColor,
      transparent: true,
      opacity: 0.35,
      polygonOffset: true,
      polygonOffsetFactor: -2,
      polygonOffsetUnits: -4,
    });
    this.glowTubeMesh = new THREE.Mesh(glowGeo, glowMat);
    this.glowTubeMesh.position.y += 1.1;
    this.glowTubeMesh.renderOrder = 9;
    this.scene.add(this.glowTubeMesh);

    // Layer 3: Vibrant Core Centerline Neon Beam (+1.5m)
    const centerGeo = new THREE.TubeGeometry(this.curve, tubularSegments, 0.45, 6, false);
    centerGeo.scale(1.0, 0.25, 1.0);
    const centerMat = new THREE.MeshStandardMaterial({
      color: 0xffffff,
      emissive: profColor,
      emissiveIntensity: 0.95,
      roughness: 0.15,
      polygonOffset: true,
      polygonOffsetFactor: -3,
      polygonOffsetUnits: -6,
    });
    this.centerlineMesh = new THREE.Mesh(centerGeo, centerMat);
    this.centerlineMesh.position.y += 1.4;
    this.centerlineMesh.renderOrder = 10;
    this.scene.add(this.centerlineMesh);

    // Layer 4: Dynamic Moving Pulse Light Beacon
    const beaconGeo = new THREE.SphereGeometry(1.6, 16, 16);
    const beaconMat = new THREE.MeshBasicMaterial({ color: 0x38bdf8, transparent: true, opacity: 0.9 });
    this.pulseBeacon = new THREE.Mesh(beaconGeo, beaconMat);
    this.pulseBeacon.position.y += 1.8;
    this.scene.add(this.pulseBeacon);
  }

  buildPinMarkers() {
    if (!this.scenePoints.length) return;
    const pStart = this.scenePoints[0].clone();
    pStart.y += 1.5;
    const pEnd = this.scenePoints[this.scenePoints.length - 1].clone();
    pEnd.y += 1.5;

    const createPin = (colorHex) => {
      const pinGroup = new THREE.Group();
      const coneGeo = new THREE.ConeGeometry(3.5, 12, 16);
      coneGeo.rotateX(Math.PI);
      const coneMat = new THREE.MeshStandardMaterial({ color: colorHex, roughness: 0.3, emissive: colorHex, emissiveIntensity: 0.2 });
      const cone = new THREE.Mesh(coneGeo, coneMat);
      cone.position.y = 12;
      cone.castShadow = true;
      pinGroup.add(cone);

      const sphereGeo = new THREE.SphereGeometry(3.2, 16, 16);
      const sphere = new THREE.Mesh(sphereGeo, coneMat);
      sphere.position.y = 18;
      pinGroup.add(sphere);

      return pinGroup;
    };

    const pinA = createPin(0x059669);
    pinA.position.copy(pStart);
    this.pinsGroup.add(pinA);

    const pinB = createPin(0xdc2626);
    pinB.position.copy(pEnd);
    this.pinsGroup.add(pinB);
  }

  buildAvatar() {
    const activeKey = this.activeProfileKey || this.routeData?.properties?.profile_key || 'adult';
    this.avatarRig.setProfile(activeKey);
    this.avatarRigs = [this.avatarRig];

    this.routeVisuals.forEach((visual) => {
      if (visual.key === activeKey) {
        visual.rig = this.avatarRig;
        return;
      }
      const rig = new KinematicAvatarRig(this.scene);
      rig.setProfile(visual.key);
      visual.rig = rig;
      this.avatarRigs.push(rig);
    });
    this.buildRouteOverlays();
    this.applyProfileAppearance();
  }

  buildRouteOverlays() {
    while (this.routeOverlayGroup.children.length) {
      const child = this.routeOverlayGroup.children[0];
      this.routeOverlayGroup.remove(child);
      disposeHierarchy(child);
    }
    this.routeVisuals.forEach((visual) => {
      if (visual.key === this.activeProfileKey || visual.points.length < 2) return;
      const color = this.profileColor(visual.feature);
      const geometry = new THREE.BufferGeometry().setFromPoints(visual.points);
      const material = new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.72 });
      const line = new THREE.Line(geometry, material);
      line.userData.profileKey = visual.key;
      this.routeOverlayGroup.add(line);
    });
  }

  buildUrbanEnvironment() {
    const buildings = this.routeData?.properties?.corridor_buildings || this.routeCollection?.properties?.corridor_buildings || [];
    const trees = this.routeData?.properties?.corridor_trees || this.routeCollection?.properties?.corridor_trees || [];

    const bldMats = [
      new THREE.MeshStandardMaterial({ color: 0xf8fafc, roughness: 0.7, metalness: 0.08 }),
      new THREE.MeshStandardMaterial({ color: 0xe2e8f0, roughness: 0.65, metalness: 0.12 }),
      new THREE.MeshStandardMaterial({ color: 0xcbd5e1, roughness: 0.6, metalness: 0.18 }),
      new THREE.MeshStandardMaterial({ color: 0x94a3b8, roughness: 0.75, metalness: 0.1 }),
      new THREE.MeshStandardMaterial({ color: 0x64748b, roughness: 0.8, metalness: 0.05 }),
    ];

    // 1. Render real OSM polygon buildings
    if (buildings.length > 0) {
      buildings.forEach((bld, idx) => {
        const coords = bld.polygon || bld.coordinates || [];
        if (coords.length < 3) return;

        const bldBaseEle = bld.base_elevation_m !== undefined ? bld.base_elevation_m : (this.baseElevation || 0.0);
        const scenePts = coords.map(([lon, lat]) => this.lonLatToSceneMeters(lon, lat, bldBaseEle));

        let avgY = 0;
        scenePts.forEach((p) => {
          avgY += p.y;
        });
        avgY /= scenePts.length;

        const shape = new THREE.Shape();
        shape.moveTo(scenePts[0].x, -scenePts[0].z);
        for (let i = 1; i < scenePts.length; i++) {
          shape.lineTo(scenePts[i].x, -scenePts[i].z);
        }
        shape.closePath();

        const height = Math.max(6.0, bld.height_m || (bld.levels ? bld.levels * 3.2 : 12.0));
        const extrudeSettings = {
          depth: height,
          bevelEnabled: true,
          bevelSegments: 1,
          steps: 1,
          bevelSize: 0.2,
          bevelThickness: 0.2,
        };

        const bldGeo = new THREE.ExtrudeGeometry(shape, extrudeSettings);
        bldGeo.rotateX(-Math.PI / 2);

        const mat = bldMats[idx % bldMats.length];
        const bldMesh = new THREE.Mesh(bldGeo, mat);
        bldMesh.position.y = avgY;
        bldMesh.castShadow = true;
        bldMesh.receiveShadow = true;
        this.buildingsGroup.add(bldMesh);
      });
    }
    this.buildingsGroup.visible = this.showBuildings;

    // 2. Render volumetric 3D corridor trees (trunks + lush multi-layer canopies with shadows)
    if (trees.length > 0) {
      trees.forEach((tree) => {
        const coords = tree.coordinates || [];
        if (coords.length < 2) return;
        const lon = coords[0];
        const lat = coords[1];
        const treeBaseEle = tree.base_elevation_m !== undefined ? tree.base_elevation_m : (this.baseElevation || 0.0);
        const pos = this.lonLatToSceneMeters(lon, lat, treeBaseEle);
        const groundY = this.getTerrainElevationAt(pos.x, pos.z);
        pos.y = Math.max(pos.y, groundY);

        const height = Math.max(4.5, Number(tree.height_m || 8.0));
        const canopyR = Math.max(1.8, Number(tree.canopy_radius_m || 3.2));
        const trunkH = Math.max(1.2, Number(tree.trunk_height_m || (height * 0.35)));
        const trunkR = Math.max(0.18, Number(tree.trunk_radius_m || 0.28));
        const treeType = tree.tree_type || 'deciduous';

        const treeGroup = new THREE.Group();

        // Architectural Wood Trunk
        const trunkGeo = new THREE.CylinderGeometry(trunkR * 0.75, trunkR * 1.15, trunkH, 7);
        const trunkMat = new THREE.MeshStandardMaterial({
          color: 0x452b19,
          roughness: 0.92,
          metalness: 0.05,
        });
        const trunkMesh = new THREE.Mesh(trunkGeo, trunkMat);
        trunkMesh.position.y = trunkH / 2;
        trunkMesh.castShadow = true;
        trunkMesh.receiveShadow = true;
        treeGroup.add(trunkMesh);

        // Volumetric Lush Multi-Layer Canopy
        if (treeType === 'conifer' || treeType === 'pine') {
          const pineTiers = [
            { r: canopyR, h: (height - trunkH) * 0.52, y: trunkH + (height - trunkH) * 0.26, color: 0x14532d },
            { r: canopyR * 0.78, h: (height - trunkH) * 0.48, y: trunkH + (height - trunkH) * 0.52, color: 0x166534 },
            { r: canopyR * 0.52, h: (height - trunkH) * 0.42, y: trunkH + (height - trunkH) * 0.78, color: 0x15803d },
          ];
          pineTiers.forEach((tier) => {
            const cGeo = new THREE.ConeGeometry(tier.r, tier.h, 7);
            const cMat = new THREE.MeshStandardMaterial({
              color: tier.color,
              roughness: 0.75,
              metalness: 0.04,
              flatShading: true,
            });
            const cMesh = new THREE.Mesh(cGeo, cMat);
            cMesh.position.y = tier.y;
            cMesh.castShadow = true;
            cMesh.receiveShadow = true;
            treeGroup.add(cMesh);
          });
        } else {
          // Lush multi-tier broadleaf / deciduous canopy
          const decTiers = [
            { r: canopyR * 0.95, y: trunkH + canopyR * 0.72, scale: [1.1, 0.82, 1.05], color: 0x15803d },
            { r: canopyR * 0.82, y: trunkH + canopyR * 1.18, scale: [0.96, 0.88, 0.96], color: 0x16a34a },
            { r: canopyR * 0.58, y: trunkH + canopyR * 1.58, scale: [0.85, 0.95, 0.85], color: 0x22c55e },
          ];
          decTiers.forEach((tier) => {
            const folGeo = new THREE.DodecahedronGeometry(tier.r, 1);
            folGeo.scale(tier.scale[0], tier.scale[1], tier.scale[2]);
            const folMat = new THREE.MeshStandardMaterial({
              color: tier.color,
              roughness: 0.78,
              metalness: 0.04,
              flatShading: true,
            });
            const folMesh = new THREE.Mesh(folGeo, folMat);
            folMesh.position.y = tier.y;
            folMesh.castShadow = true;
            folMesh.receiveShadow = true;
            treeGroup.add(folMesh);
          });
        }

        treeGroup.position.copy(pos);
        this.treesGroup.add(treeGroup);
      });
    }
    this.treesGroup.visible = this.showTrees;
  }

  togglePlay() {
    this.isPlaying = !this.isPlaying;
    if (this.elPlayIcon) {
      this.elPlayIcon.textContent = this.isPlaying ? '⏸' : '▶';
    }
  }

  updateAvatarPosition() {
    if (!this.curve || this.scenePoints.length < 2) return;
    const safeProgress = Math.max(0.0, Math.min(1.0, this.progress));
    const pt = this.curve.getPointAt(safeProgress);
    const tangent = this.curve.getTangentAt(safeProgress).normalize();

    // Determine simulation elapsed time based on max duration among all active routes
    const maxDurationSec = Math.max(
      1.0,
      ...this.routeFeatures.map((f) => Number(f?.properties?.duration_min || 5.0) * 60.0),
    );
    const elapsedSimTimeSec = safeProgress * maxDurationSec;

    const animDelta = this.isPlaying ? 0.016 * Math.max(0.2, this.playbackSpeed) : 0.0;
    this.routeVisuals.forEach((visual) => {
      if (!visual.rig || !visual.curve) return;
      const props = visual.feature?.properties || {};
      const visualDurationSec = Math.max(1.0, Number(props.duration_min || 5.0) * 60.0);
      const visualProgress = Math.max(0.0, Math.min(1.0, elapsedSimTimeSec / visualDurationSec));

      const visualPt = visual.curve.getPointAt(visualProgress);
      const visualTangent = visual.curve.getTangentAt(visualProgress).normalize();
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

    if (this.pulseBeacon) {
      const pulseProg = (safeProgress + (this.clock ? this.clock.getElapsedTime() * 0.1 : 0.0)) % 1.0;
      const bPt = this.curve.getPointAt(pulseProg);
      this.pulseBeacon.position.copy(bPt);
      this.pulseBeacon.position.y += 1.8;
    }

    if (this.voiceSystem) {
      this.voiceSystem.update(safeProgress);
    }

    if (this.elScrubber) {
      this.elScrubber.value = (safeProgress * 1000.0).toFixed(0);
    }
    if (this.elNeedle) {
      this.elNeedle.style.left = `${safeProgress * 100}%`;
    }

    this.updateMetricLiveReadout();

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
    if (!this.elMetricLiveVal) return;
    if (!this.routeData) {
      this.elMetricLiveVal.textContent = '—';
      return;
    }
    const coords = this.routeData?.geometry?.coordinates || [];
    const profList = this.routeData?.properties?.elevation_profile || [];
    if (coords.length === 0) {
      this.elMetricLiveVal.textContent = '—';
      return;
    }
    if (coords.length === 1) {
      const eleVal = coords[0][2] !== undefined ? coords[0][2] : 0.0;
      this.elMetricLiveVal.textContent = `${eleVal.toFixed(1)} m`;
      return;
    }

    const safeProgress = Math.max(0.0, Math.min(1.0, this.progress));
    const idx = Math.min(coords.length - 1, Math.max(0, Math.floor(safeProgress * coords.length)));
    const pData = profList[idx] || {};

    if (this.activeChartMetric === 'slope') {
      const slopeVal = pData.slope_pct !== undefined ? pData.slope_pct : 0.0;
      this.elMetricLiveVal.textContent = `${slopeVal >= 0 ? '+' : ''}${slopeVal.toFixed(1)}%`;
      if (this.elMetricReadout && this.elMetricReadout.firstChild) this.elMetricReadout.firstChild.textContent = 'Slope: ';
    } else if (this.activeChartMetric === 'lst') {
      const lstVal = 24.0 + (pData.thermal_comfort !== undefined ? (1.0 - pData.thermal_comfort) * 16.0 : 6.0);
      this.elMetricLiveVal.textContent = `${lstVal.toFixed(1)} °C`;
      if (this.elMetricReadout && this.elMetricReadout.firstChild) this.elMetricReadout.firstChild.textContent = 'Heat (LST): ';
    } else if (this.activeChartMetric === 'greenery') {
      const greenVal = pData.ndvi !== undefined ? pData.ndvi * 100.0 : 65.0;
      this.elMetricLiveVal.textContent = `${greenVal.toFixed(0)}%`;
      if (this.elMetricReadout && this.elMetricReadout.firstChild) this.elMetricReadout.firstChild.textContent = 'Greenery: ';
    } else {
      const eleVal = coords[idx] && coords[idx][2] !== undefined ? coords[idx][2] : 0.0;
      this.elMetricLiveVal.textContent = `${eleVal.toFixed(1)} m`;
      if (this.elMetricReadout && this.elMetricReadout.firstChild) this.elMetricReadout.firstChild.textContent = 'Elevation: ';
    }
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

    if (this.scenePoints.length >= 2 && this.curve) {
      const safeProgress = Math.max(0.0, Math.min(1.0, this.progress));
      const pt = this.curve.getPointAt(safeProgress);
      const av = toRadar(pt);
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

  takeSnapshot() {
    this.renderer.render(this.scene, this.camera);
    const dataUrl = this.renderer.domElement.toDataURL('image/png');
    const a = document.createElement('a');
    a.href = dataUrl;
    a.download = `02Route3D_${new Date().toISOString().slice(0, 19)}.png`;
    a.click();
  }

  toggleRecordVideo() {
    const btnRecord = document.getElementById('btnRecord');
    if (this.isRecording) {
      if (this.mediaRecorder) {
        this.mediaRecorder.stop();
      }
      this.isRecording = false;
      if (btnRecord) btnRecord.style.color = '';
    } else {
      this.recordedChunks = [];
      try {
        const stream = this.renderer.domElement.captureStream(60);
        this.mediaRecorder = new MediaRecorder(stream, { mimeType: 'video/webm;codecs=vp9' });
        this.mediaRecorder.ondataavailable = (e) => {
          if (e.data && e.data.size > 0) this.recordedChunks.push(e.data);
        };
        this.mediaRecorder.onstop = () => {
          const blob = new Blob(this.recordedChunks, { type: 'video/webm' });
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = `02Route3D_${new Date().toISOString().slice(0, 19)}.webm`;
          a.click();
        };
        this.mediaRecorder.start();
        this.isRecording = true;
        if (btnRecord) btnRecord.style.color = '#ef4444';
      } catch (err) {
        console.warn('Video recording error:', err);
      }
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

  renderProfileChart() {
    if (!this.elSvg) return;
    if (!this.routeData) {
      this.elSvg.innerHTML = '';
      return;
    }
    const coords = this.routeData?.geometry?.coordinates || [];
    const profList = this.routeData?.properties?.elevation_profile || [];
    if (coords.length < 2) {
      this.elSvg.innerHTML = '';
      return;
    }

    const width = Math.max(10, this.elSvg.clientWidth || 400);
    const height = Math.max(10, this.elSvg.clientHeight || 52);

    let values = [];
    let strokeColor = '#0284c7';
    let gradColor = '#0284c7';

    if (this.activeChartMetric === 'slope') {
      values = coords.map((_, i) => (profList[i] && profList[i].slope_pct !== undefined ? profList[i].slope_pct : 0.0));
      strokeColor = '#f59e0b';
      gradColor = '#f59e0b';
    } else if (this.activeChartMetric === 'lst') {
      values = coords.map((_, i) => 24.0 + (profList[i] && profList[i].thermal_comfort !== undefined ? (1.0 - profList[i].thermal_comfort) * 16.0 : 6.0));
      strokeColor = '#ef4444';
      gradColor = '#ef4444';
    } else if (this.activeChartMetric === 'greenery') {
      values = coords.map((_, i) => (profList[i] && profList[i].ndvi !== undefined ? profList[i].ndvi * 100.0 : 65.0));
      strokeColor = '#10b981';
      gradColor = '#10b981';
    } else {
      values = coords.map((c) => (c && c[2] !== undefined ? c[2] : 0.0));
      strokeColor = this.routeData?.properties?.profile_color || '#0284c7';
      gradColor = strokeColor;
    }

    if (values.length < 2) {
      this.elSvg.innerHTML = '';
      return;
    }

    const minVal = Math.min(...values);
    const maxVal = Math.max(...values);
    const valRange = Math.max(0.001, maxVal - minVal);
    const count = values.length;
    const denom = Math.max(1, count - 1);

    const points = values.map((v, i) => {
      const x = (i / denom) * width;
      const y = height - 8 - ((v - minVal) / valRange) * (height - 16);
      const safeY = isNaN(y) ? height / 2 : y;
      return `${x.toFixed(1)},${safeY.toFixed(1)}`;
    });

    const pathD = `M ${points[0]} ` + points.slice(1).map((p) => `L ${p}`).join(' ');
    const fillD = `${pathD} L ${width.toFixed(1)},${height.toFixed(1)} L 0,${height.toFixed(1)} Z`;

    this.elSvg.innerHTML = `
      <defs>
        <linearGradient id="chartGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${gradColor}" stop-opacity="0.38"/>
          <stop offset="100%" stop-color="${gradColor}" stop-opacity="0.0"/>
        </linearGradient>
      </defs>
      <path d="${fillD}" fill="url(#chartGrad)"/>
      <path d="${pathD}" fill="none" stroke="${strokeColor}" stroke-width="2.5"/>
    `;
  }

  animate() {
    requestAnimationFrame(this.animate);
    const delta = this.clock.getDelta();

    if (this.isPlaying && this.curve && this.scenePoints.length >= 2) {
      this.progress += delta * 0.04 * this.playbackSpeed;
      if (this.progress > 1.0) {
        this.progress = 0.0;
      }
      this.updateAvatarPosition();
    }

    if (this.pulseBeacon && this.curve && this.scenePoints.length >= 2) {
      const beaconProg = ((this.clock ? this.clock.getElapsedTime() * 0.15 : 0.0)) % 1.0;
      const bPt = this.curve.getPointAt(beaconProg);
      this.pulseBeacon.position.copy(bPt);
      this.pulseBeacon.position.y += 1.8;
    }

    if (this.cameraMode === 'orbit' && this.controls.enabled) {
      this.controls.update();
    }

    if (this.compassRose && this.controls) {
      const angle = this.controls.getAzimuthalAngle();
      this.compassRose.style.transform = `rotate(${angle}rad)`;
    }

    this.renderer.render(this.scene, this.camera);
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

async function fetchCurrentRoute() {
  try {
    const res = await fetch(`data/current_route.json?t=${Date.now()}`, { cache: 'no-store' });
    if (res.ok) {
      const data = await res.json();
      const dataStr = JSON.stringify(data);
      if (data && dataStr !== lastLoadedPayload && window.app3d) {
        lastLoadedPayload = dataStr;
        window.app3d.loadRoute(data);
        return true;
      }
      return Boolean(data);
    }
  } catch (_) {}
  return false;
}

// Initial load. There is deliberately no built-in demo route: when QGIS has not
// computed one yet the studio shows its empty state rather than a fabricated route
// whose distance, climb and calorie figures would be indistinguishable from real ones.
(async () => {
  await fetchCurrentRoute();

  // Dynamic live sync: picks up newly computed routes from QGIS automatically.
  routePollTimer = setInterval(fetchCurrentRoute, 1500);
})();

window.addEventListener('beforeunload', () => {
  if (routePollTimer !== null) {
    clearInterval(routePollTimer);
    routePollTimer = null;
  }
});
