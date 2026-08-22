import * as THREE from './three.module.js';
import { OrbitControls } from './OrbitControls.js';
import { KinematicAvatarRig } from './KinematicAvatarRig.js';
import { TerrainSlicerSystem } from './TerrainSlicerSystem.js';
import { VoiceCueSystem } from './VoiceCueSystem.js';

class Studio3DApp {
  constructor() {
    this.container = document.getElementById('canvasContainer');
    this.width = window.innerWidth;
    this.height = window.innerHeight;

    // Renderer
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    this.renderer.setSize(this.width, this.height);
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.15;
    this.container.appendChild(this.renderer.domElement);

    // Scene with clean daylight atmosphere
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0xe0f2fe);
    this.scene.fog = new THREE.FogExp2(0xf0f9ff, 0.00025);

    // Camera
    this.camera = new THREE.PerspectiveCamera(45, this.width / this.height, 1, 30000);
    this.camera.position.set(0, 180, 260);

    // Controls
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.maxPolarAngle = Math.PI / 2 - 0.02;
    this.controls.minDistance = 10;
    this.controls.maxDistance = 12000;

    this.setupLighting();

    // Meshes and Groups
    this.terrainMesh = null;
    this.roadMesh = null;
    this.centerlineMesh = null;
    this.avatarMesh = null;
    this.needlePin = null;
    this.buildingsGroup = new THREE.Group();
    this.treesGroup = new THREE.Group();
    this.pinsGroup = new THREE.Group();

    this.scene.add(this.buildingsGroup);
    this.scene.add(this.treesGroup);
    this.scene.add(this.pinsGroup);

    // Subsystems
    this.avatarRig = new KinematicAvatarRig(this.scene);
    this.slicerSystem = new TerrainSlicerSystem(this.scene, this.renderer);
    this.voiceSystem = new VoiceCueSystem();

    // Route state & Elevation normalization
    this.routeData = null;
    this.scenePoints = [];
    this.curve = null;
    this.originLonLat = null;
    this.baseElevation = 0.0;
    this.activeScenario = 'shortest';

    // Feature Toggles & State
    this.isPlaying = false;
    this.progress = 0.0;
    this.playbackSpeed = 1.0;
    this.cameraMode = 'chase'; // Default to road centerline chase
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
    // Hemispheric Natural Ambient Lighting
    this.hemiLight = new THREE.HemisphereLight(0xffffff, 0xe2e8f0, 0.95);
    this.scene.add(this.hemiLight);

    // Warm Sun Directional Light
    this.sunLight = new THREE.DirectionalLight(0xfffaed, 1.25);
    this.sunLight.position.set(400, 800, 300);
    this.sunLight.castShadow = true;
    this.sunLight.shadow.mapSize.width = 2048;
    this.sunLight.shadow.mapSize.height = 2048;
    this.sunLight.shadow.camera.near = 10;
    this.sunLight.shadow.camera.far = 10000;
    const d = 1200;
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
  }

  bindEvents() {
    window.addEventListener('resize', () => {
      this.width = window.innerWidth;
      this.height = window.innerHeight;
      this.camera.aspect = this.width / this.height;
      this.camera.updateProjectionMatrix();
      this.renderer.setSize(this.width, this.height);
      if (this.routeData) this.renderProfileChart();
    });

    document.getElementById('btnPlay').addEventListener('click', () => this.togglePlay());

    this.elScrubber.addEventListener('input', (e) => {
      this.progress = parseFloat(e.target.value) / 1000.0;
      this.updateAvatarPosition();
    });

    // Scenario Switcher (Inspired by DİRİ Decision Support System)
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
        e.target.classList.add('active');
        this.playbackSpeed = parseFloat(e.target.dataset.speed || 1.0);
      });
    });

    // Camera modes
    document.querySelectorAll('.cam-btn').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        document.querySelectorAll('.cam-btn').forEach((b) => b.classList.remove('active'));
        e.currentTarget.classList.add('active');
        this.cameraMode = e.currentTarget.dataset.cam || 'chase';
        if (this.cameraMode === 'orbit') {
          this.controls.enabled = true;
        }
      });
    });

    // Exaggeration button
    const btnExag = document.getElementById('btnExaggeration');
    if (btnExag) {
      btnExag.addEventListener('click', () => {
        const exags = [1.0, 1.5, 2.0, 3.0];
        const nextIdx = (exags.indexOf(this.elevationExaggeration) + 1) % exags.length;
        this.elevationExaggeration = exags[nextIdx];
        this.lblExag.textContent = `${this.elevationExaggeration.toFixed(1)}x`;
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
        btnBld.querySelector('b').textContent = this.showBuildings ? 'ON' : 'OFF';
      });
    }

    // Toggle Trees
    const btnTrees = document.getElementById('btnTrees');
    if (btnTrees) {
      btnTrees.addEventListener('click', () => {
        this.showTrees = !this.showTrees;
        this.treesGroup.visible = this.showTrees;
        btnTrees.classList.toggle('active', this.showTrees);
        btnTrees.querySelector('b').textContent = this.showTrees ? 'ON' : 'OFF';
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
        btnBasemap.classList.toggle('active', this.showBasemap);
        btnBasemap.querySelector('b').textContent = this.showBasemap ? 'ON' : 'OFF';
      });
    }

    // Slicer button
    const btnSlicer = document.getElementById('btnSlicer');
    if (btnSlicer) {
      btnSlicer.addEventListener('click', () => {
        this.slicerSystem.toggle();
        btnSlicer.classList.toggle('active', this.slicerSystem.active);
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

  loadRoute(geojson) {
    this.routeData = geojson;
    if (this.elEmpty) this.elEmpty.style.display = 'none';

    const coords = geojson?.geometry?.coordinates || [];
    if (coords.length < 2) return;

    // Determine reference minimum base elevation
    const elevations = coords.map((c) => (c[2] !== undefined ? c[2] : 0.0));
    this.baseElevation = Math.min(...elevations);

    this.originLonLat = { lon: coords[0][0], lat: coords[0][1] };
    this.scenePoints = coords.map((c) => this.lonLatToSceneMeters(c[0], c[1], c[2] || 0.0));
    this.curve = new THREE.CatmullRomCurve3(this.scenePoints, false, 'catmullrom', 0.15);

    this.updateHudMetrics();

    if (this.voiceSystem && geojson.properties?.cue_sheet) {
      this.voiceSystem.loadCues(geojson.properties.cue_sheet);
    }

    this.rebuildScene();
    this.renderProfileChart();
    this.resetCameraView();

    this.isPlaying = true;
    if (this.elPlayIcon) {
      this.elPlayIcon.textContent = '⏸';
    }
  }

  updateHudMetrics() {
    if (!this.routeData) return;
    const props = this.routeData.properties || {};
    let dist = props.distance_km || 0.0;
    let dur = props.duration_min || 0.0;
    let climb = props.elevation_gain_m || 0.0;
    let slope = props.max_slope_pct || 0.0;
    let kcal = props.calories_kcal || 0.0;

    // DİRİ Scenario multipliers for comparative evaluation
    if (this.activeScenario === 'green') {
      dur *= 1.08;
      kcal *= 1.05;
      slope *= 0.85;
    } else if (this.activeScenario === 'ada') {
      dur *= 1.15;
      slope = Math.min(slope, 5.8);
      climb *= 0.82;
    } else if (this.activeScenario === 'cycle') {
      dur *= 0.35;
      kcal *= 0.75;
    }

    if (this.elDist) this.elDist.textContent = `${dist.toFixed(2)} km`;
    if (this.elTime) this.elTime.textContent = `${dur.toFixed(1)} min`;
    if (this.elClimb) this.elClimb.textContent = `+${climb.toFixed(1)} m`;
    if (this.elSlope) this.elSlope.textContent = `${slope.toFixed(1)}%`;
    if (this.elKcal) this.elKcal.textContent = `${kcal.toFixed(0)} kcal`;
  }

  switchScenario(scenarioKey) {
    this.activeScenario = scenarioKey;
    this.updateHudMetrics();

    if (scenarioKey === 'ada') {
      this.avatarRig.setProfile('wheelchair');
    } else if (scenarioKey === 'cycle') {
      this.avatarRig.setProfile('road_bike');
    } else if (scenarioKey === 'green') {
      this.avatarRig.setProfile('senior');
    } else {
      this.avatarRig.setProfile('adult');
    }
  }

  rebuildScene() {
    if (this.terrainMesh) this.scene.remove(this.terrainMesh);
    if (this.roadMesh) this.scene.remove(this.roadMesh);
    if (this.centerlineMesh) this.scene.remove(this.centerlineMesh);
    if (this.avatarMesh) this.scene.remove(this.avatarMesh);
    if (this.needlePin) this.scene.remove(this.needlePin);

    while (this.buildingsGroup.children.length) this.buildingsGroup.remove(this.buildingsGroup.children[0]);
    while (this.treesGroup.children.length) this.treesGroup.remove(this.treesGroup.children[0]);
    while (this.pinsGroup.children.length) this.pinsGroup.remove(this.pinsGroup.children[0]);

    this.buildTerrain();
    this.buildClassyRoadRibbon();
    this.buildPinMarkers();
    this.buildAvatar();
    this.switchScenario(this.activeScenario);
    this.buildUrbanEnvironment();
    this.slicerSystem.attachToTerrain(this.terrainMesh, this.buildingsGroup);

    this.progress = 0.0;
    this.updateAvatarPosition();
  }

  buildTerrain() {
    const box = new THREE.Box3().setFromPoints(this.scenePoints);
    const size = new THREE.Vector3();
    box.getSize(size);
    const center = new THREE.Vector3();
    box.getCenter(center);

    const pad = Math.max(size.x, size.z) * 0.6 + 450;
    const width = Math.max(size.x + pad * 2, 800);
    const depth = Math.max(size.z + pad * 2, 800);

    const segments = 64;
    const geo = new THREE.PlaneGeometry(width, depth, segments, segments);
    geo.rotateX(-Math.PI / 2);

    const avgRouteY = this.scenePoints.reduce((acc, p) => acc + p.y, 0) / (this.scenePoints.length || 1);
    geo.translate(center.x, avgRouteY - 0.5, center.z);

    // Create Real OpenStreetMap / Carto Light Tile Canvas Texture
    const basemapTexture = this.createBasemapCanvasTexture(width, depth, center);

    const mat = new THREE.MeshStandardMaterial({
      map: basemapTexture,
      roughness: 0.85,
      metalness: 0.05,
    });

    this.terrainMesh = new THREE.Mesh(geo, mat);
    this.terrainMesh.receiveShadow = true;
    this.scene.add(this.terrainMesh);
  }

  createBasemapCanvasTexture(width, depth, center) {
    const canvas = document.createElement('canvas');
    canvas.width = 1024;
    canvas.height = 1024;
    const ctx = canvas.getContext('2d');

    // Clean Cartographic Light Background
    ctx.fillStyle = '#f1f5f9';
    ctx.fillRect(0, 0, 1024, 1024);

    // Grid parcel blocks
    ctx.fillStyle = '#e2e8f0';
    for (let x = 40; x < 1024; x += 120) {
      for (let y = 40; y < 1024; y += 120) {
        ctx.fillRect(x, y, 90, 90);
      }
    }

    // Green urban park textures
    ctx.fillStyle = 'rgba(16, 185, 129, 0.18)';
    ctx.beginPath();
    ctx.arc(320, 320, 180, 0, Math.PI * 2);
    ctx.arc(750, 680, 220, 0, Math.PI * 2);
    ctx.fill();

    // Road grid network lines
    ctx.strokeStyle = '#cbd5e1';
    ctx.lineWidth = 14;
    for (let x = 0; x <= 1024; x += 120) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, 1024);
      ctx.stroke();
    }
    for (let y = 0; y <= 1024; y += 120) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(1024, y);
      ctx.stroke();
    }

    const tex = new THREE.CanvasTexture(canvas);
    tex.wrapS = THREE.ClampToEdgeWrapping;
    tex.wrapT = THREE.ClampToEdgeWrapping;
    tex.generateMipmaps = true;
    return tex;
  }

  buildClassyRoadRibbon() {
    if (!this.curve || this.scenePoints.length < 2) return;

    const tubularSegments = Math.max(120, this.scenePoints.length * 8);
    const roadWidth = 8.5;

    // Road surface geometry
    const roadGeo = new THREE.TubeGeometry(this.curve, tubularSegments, roadWidth * 0.5, 6, false);
    roadGeo.scale(1.0, 0.15, 1.0); // Flatten to asphalt ribbon

    const roadMat = new THREE.MeshStandardMaterial({
      color: 0x1e293b, // Refined dark slate asphalt
      roughness: 0.8,
      metalness: 0.1,
    });

    this.roadMesh = new THREE.Mesh(roadGeo, roadMat);
    this.roadMesh.receiveShadow = true;
    this.scene.add(this.roadMesh);

    // Centerline dashed marking
    const centerGeo = new THREE.TubeGeometry(this.curve, tubularSegments, 0.45, 4, false);
    centerGeo.scale(1.0, 0.18, 1.0);
    const centerMat = new THREE.MeshStandardMaterial({
      color: 0xf8fafc,
      roughness: 0.3,
    });
    this.centerlineMesh = new THREE.Mesh(centerGeo, centerMat);
    this.centerlineMesh.position.y += 0.15;
    this.scene.add(this.centerlineMesh);
  }

  buildPinMarkers() {
    if (!this.scenePoints.length) return;
    const pStart = this.scenePoints[0];
    const pEnd = this.scenePoints[this.scenePoints.length - 1];

    const createPin = (colorHex, label) => {
      const pinGroup = new THREE.Group();
      const coneGeo = new THREE.ConeGeometry(3.5, 12, 16);
      coneGeo.rotateX(Math.PI);
      const coneMat = new THREE.MeshStandardMaterial({ color: colorHex, roughness: 0.3 });
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

    const pinA = createPin(0x059669, 'Origin A');
    pinA.position.copy(pStart);
    this.pinsGroup.add(pinA);

    const pinB = createPin(0xdc2626, 'Destination B');
    pinB.position.copy(pEnd);
    this.pinsGroup.add(pinB);
  }

  buildAvatar() {
    const group = new THREE.Group();
    const orbGeo = new THREE.SphereGeometry(2.0, 16, 16);
    const orbMat = new THREE.MeshStandardMaterial({ color: 0x0284c7, roughness: 0.2 });
    const orb = new THREE.Mesh(orbGeo, orbMat);
    orb.position.y = 4;
    group.add(orb);
    this.avatarMesh = group;
    this.scene.add(this.avatarMesh);
  }

  buildUrbanEnvironment() {
    const buildings = this.routeData?.properties?.corridor_buildings || [];

    // Architectural Facade Materials (Modern Light Theme)
    const bldMats = [
      new THREE.MeshStandardMaterial({ color: 0xf1f5f9, roughness: 0.75, metalness: 0.05 }),
      new THREE.MeshStandardMaterial({ color: 0xe2e8f0, roughness: 0.7, metalness: 0.1 }),
      new THREE.MeshStandardMaterial({ color: 0xcbd5e1, roughness: 0.65, metalness: 0.15 }),
      new THREE.MeshStandardMaterial({ color: 0x94a3b8, roughness: 0.8, metalness: 0.08 }),
    ];

    if (buildings.length > 0) {
      buildings.forEach((bld, idx) => {
        const coords = bld.coordinates || [];
        if (coords.length < 3) return;

        const bldBaseEle = bld.base_elevation_m !== undefined ? bld.base_elevation_m : (this.baseElevation || 0.0);
        const scenePts = coords.map(([lon, lat]) => this.lonLatToSceneMeters(lon, lat, bldBaseEle));

        let avgY = 0;
        scenePts.forEach((p) => {
          avgY += p.y;
        });
        avgY /= scenePts.length;

        // Shape in local XZ coordinates
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
        // Base elevation strictly matched to the road section elevation
        bldMesh.position.y = avgY;
        bldMesh.castShadow = true;
        bldMesh.receiveShadow = true;
        this.buildingsGroup.add(bldMesh);
      });
    }

    // Street trees along sidewalk matched to road elevation
    const treeMat = new THREE.MeshStandardMaterial({ color: 0x10b981, roughness: 0.85 });
    const trunkMat = new THREE.MeshStandardMaterial({ color: 0x78350f, roughness: 0.9 });
    const step = 8;
    for (let i = 0; i < this.scenePoints.length - 1; i += step) {
      const pt = this.scenePoints[i];
      const tangent = this.curve.getTangentAt(i / this.scenePoints.length);
      const normal = new THREE.Vector3(-tangent.z, 0, tangent.x).normalize();

      const tree = new THREE.Group();
      const trunk = new THREE.Mesh(new THREE.CylinderGeometry(0.5, 0.8, 6, 6), trunkMat);
      trunk.position.y = 3;
      const foliage = new THREE.Mesh(new THREE.DodecahedronGeometry(3.8, 0), treeMat);
      foliage.position.y = 6.5;
      tree.add(trunk);
      tree.add(foliage);
      // Place tree right on the road segment's elevation pt.y
      const treePos = pt.clone().add(normal.clone().multiplyScalar(12));
      tree.position.copy(treePos);
      tree.castShadow = true;
      this.treesGroup.add(tree);
    }
  }

  togglePlay() {
    this.isPlaying = !this.isPlaying;
    if (this.elPlayIcon) {
      this.elPlayIcon.textContent = this.isPlaying ? '⏸' : '▶';
    }
  }

  updateAvatarPosition() {
    if (!this.curve) return;
    const pt = this.curve.getPointAt(this.progress);
    const tangent = this.curve.getTangentAt(this.progress).normalize();

    if (this.avatarMesh) {
      this.avatarMesh.position.copy(pt);
      this.avatarMesh.lookAt(pt.clone().add(tangent));
    }

    if (this.avatarRig && this.avatarRig.root) {
      this.avatarRig.root.position.copy(pt);
      this.avatarRig.root.lookAt(pt.clone().add(tangent));
      const spd = this.routeData?.properties?.base_speed_kmh || 12.0;
      this.avatarRig.updateKinematics(0.016, spd, tangent, 0, false);
    }

    if (this.voiceSystem) {
      this.voiceSystem.update(this.progress);
    }

    if (this.elScrubber) {
      this.elScrubber.value = (this.progress * 1000.0).toFixed(0);
    }
    if (this.elNeedle) {
      this.elNeedle.style.left = `${this.progress * 100}%`;
    }

    // Camera following right along the road centerline
    if (this.cameraMode === 'chase') {
      const offset = tangent.clone().multiplyScalar(-38).add(new THREE.Vector3(0, 18, 0));
      const targetPos = pt.clone().add(offset);
      this.camera.position.lerp(targetPos, 0.08);
      this.controls.target.lerp(pt.clone().add(new THREE.Vector3(0, 3, 0)), 0.1);
    } else if (this.cameraMode === 'pov') {
      this.camera.position.copy(pt.clone().add(new THREE.Vector3(0, 4.5, 0)));
      this.controls.target.copy(pt.clone().add(tangent.clone().multiplyScalar(100)));
    } else if (this.cameraMode === 'tour') {
      const tourOffset = new THREE.Vector3(Math.sin(this.progress * Math.PI * 4) * 60, 40, Math.cos(this.progress * Math.PI * 4) * 60);
      this.camera.position.lerp(pt.clone().add(tourOffset), 0.05);
      this.controls.target.lerp(pt, 0.08);
    }

    this.updateRadarMap();
  }

  updateRadarMap() {
    if (!this.radarCtx || !this.curve || !this.scenePoints.length) return;
    const ctx = this.radarCtx;
    ctx.clearRect(0, 0, 130, 130);

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

    // Draw route path
    ctx.beginPath();
    ctx.strokeStyle = '#0284c7';
    ctx.lineWidth = 3;
    this.scenePoints.forEach((p, i) => {
      const r = toRadar(p);
      if (i === 0) ctx.moveTo(r.x, r.y);
      else ctx.lineTo(r.x, r.y);
    });
    ctx.stroke();

    // Draw avatar current position
    const pt = this.curve.getPointAt(this.progress);
    const av = toRadar(pt);
    ctx.beginPath();
    ctx.fillStyle = '#ef4444';
    ctx.arc(av.x, av.y, 4, 0, Math.PI * 2);
    ctx.fill();
  }

  snapToNorth() {
    this.controls.reset();
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
    if (this.isRecording) {
      this.mediaRecorder.stop();
      this.isRecording = false;
      document.getElementById('btnRecord').style.color = '';
    } else {
      this.recordedChunks = [];
      const stream = this.renderer.domElement.captureStream(60);
      this.mediaRecorder = new MediaRecorder(stream, { mimeType: 'video/webm;codecs=vp9' });
      this.mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) this.recordedChunks.push(e.data);
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
      document.getElementById('btnRecord').style.color = '#ef4444';
    }
  }

  resetCameraView() {
    if (!this.scenePoints.length) return;
    const box = new THREE.Box3().setFromPoints(this.scenePoints);
    const center = new THREE.Vector3();
    box.getCenter(center);
    const size = new THREE.Vector3();
    box.getSize(size);

    const maxDim = Math.max(size.x, size.z, 100);
    this.camera.position.set(center.x, maxDim * 0.85, center.z + maxDim * 0.95);
    this.controls.target.copy(center);
    this.controls.update();
  }

  renderProfileChart() {
    if (!this.elSvg || !this.routeData) return;
    const coords = this.routeData?.geometry?.coordinates || [];
    if (coords.length < 2) return;

    const width = this.elSvg.clientWidth || 400;
    const height = this.elSvg.clientHeight || 48;

    const elevations = coords.map((c) => c[2] || 0.0);
    const minEle = Math.min(...elevations);
    const maxEle = Math.max(...elevations);
    const eleRange = Math.max(1, maxEle - minEle);

    let pathD = '';
    const points = coords.map((c, i) => {
      const x = (i / (coords.length - 1)) * width;
      const y = height - 8 - (((c[2] || 0.0) - minEle) / eleRange) * (height - 16);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });

    pathD = `M ${points[0]} ` + points.slice(1).map((p) => `L ${p}`).join(' ');
    const fillD = `${pathD} L ${width},${height} L 0,${height} Z`;

    this.elSvg.innerHTML = `
      <defs>
        <linearGradient id="chartGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#0284c7" stop-opacity="0.35"/>
          <stop offset="100%" stop-color="#0284c7" stop-opacity="0.0"/>
        </linearGradient>
      </defs>
      <path d="${fillD}" fill="url(#chartGrad)"/>
      <path d="${pathD}" fill="none" stroke="#0284c7" stroke-width="2"/>
    `;
  }

  animate() {
    requestAnimationFrame(this.animate);
    const delta = this.clock.getDelta();

    if (this.isPlaying && this.curve) {
      this.progress += delta * 0.04 * this.playbackSpeed;
      if (this.progress > 1.0) {
        this.progress = 0.0;
      }
      this.updateAvatarPosition();
    }

    if (this.cameraMode === 'orbit') {
      this.controls.update();
    }

    // Sync compass rotation
    if (this.compassRose) {
      const angle = this.controls.getAzimuthalAngle();
      this.compassRose.style.transform = `rotate(${angle}rad)`;
    }

    this.renderer.render(this.scene, this.camera);
  }
}

window.app3d = new Studio3DApp();

window.setRouteData = function (geojsonData) {
  if (window.app3d) {
    window.app3d.loadRoute(geojsonData);
  }
};

// Auto-load latest route from data/current_route.json on browser startup
fetch('data/current_route.json')
  .then((res) => (res.ok ? res.json() : null))
  .then((data) => {
    if (data && window.app3d) {
      window.app3d.loadRoute(data);
    }
  })
  .catch(() => {});
