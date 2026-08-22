import * as THREE from './three.module.js';
import { OrbitControls } from './OrbitControls.js';
import { HeatStressRibbonManager } from './HeatStressRibbon.js';
import { SolarNightSystem } from './SolarNightSystem.js';
import { KinematicAvatarRig } from './KinematicAvatarRig.js';
import { TerrainSlicerSystem } from './TerrainSlicerSystem.js';
import { VoiceCueSystem } from './VoiceCueSystem.js';

class Studio3DApp {
  constructor() {
    this.container = document.getElementById('canvasContainer');
    this.width = window.innerWidth;
    this.height = window.innerHeight;

    // Scene & Renderer
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x090d16);
    this.scene.fog = new THREE.FogExp2(0x090d16, 0.0006);

    this.renderer = new THREE.WebGLRenderer({
      antialias: true,
      alpha: false,
      powerPreference: 'high-performance',
      preserveDrawingBuffer: true,
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setSize(this.width, this.height);
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.container.appendChild(this.renderer.domElement);

    // Instantiate Subsystems
    this.heatRibbonMgr = new HeatStressRibbonManager(this.scene);
    this.solarSystem = new SolarNightSystem(this.scene, this.renderer);
    this.avatarRig = new KinematicAvatarRig(this.scene);
    this.slicerSystem = new TerrainSlicerSystem(this.scene, this.renderer);
    this.voiceSystem = new VoiceCueSystem();

    // Camera & Controls
    this.camera = new THREE.PerspectiveCamera(45, this.width / this.height, 0.5, 60000);
    this.camera.position.set(0, 350, 550);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.05;
    this.controls.maxPolarAngle = Math.PI / 2 - 0.01;
    this.controls.minDistance = 5;
    this.controls.maxDistance = 25000;

    this.setupLighting();

    // Meshes and Groups
    this.terrainMesh = null;
    this.ribbonMesh = null;
    this.avatarMesh = null;
    this.needlePin = null;
    this.buildingsGroup = new THREE.Group();
    this.treesGroup = new THREE.Group();
    this.particlesGroup = new THREE.Group();
    this.pinsGroup = new THREE.Group();

    this.scene.add(this.buildingsGroup);
    this.scene.add(this.treesGroup);
    this.scene.add(this.particlesGroup);
    this.scene.add(this.pinsGroup);

    // Route state
    this.routeData = null;
    this.scenePoints = [];
    this.curve = null;
    this.originLonLat = null;

    // Feature Toggles & State
    this.isPlaying = false;
    this.progress = 0.0;
    this.playbackSpeed = 1.0;
    this.cameraMode = 'orbit'; // 'orbit', 'chase', 'pov', 'tour'
    this.colorMode = 'slope';  // 'slope', 'thermal', 'cyan'
    this.elevationExaggeration = 2.0;
    this.showBuildings = true;
    this.showTrees = true;
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
    this.ambientLight = new THREE.AmbientLight(0xffffff, 0.75);
    this.scene.add(this.ambientLight);

    this.sunLight = new THREE.DirectionalLight(0xfffaed, 1.4);
    this.sunLight.position.set(500, 1000, 400);
    this.sunLight.castShadow = true;
    this.sunLight.shadow.mapSize.width = 2048;
    this.sunLight.shadow.mapSize.height = 2048;
    this.sunLight.shadow.camera.near = 10;
    this.sunLight.shadow.camera.far = 15000;
    const d = 1500;
    this.sunLight.shadow.camera.left = -d;
    this.sunLight.shadow.camera.right = d;
    this.sunLight.shadow.camera.top = d;
    this.sunLight.shadow.camera.bottom = -d;
    this.scene.add(this.sunLight);

    const blueHemisphere = new THREE.HemisphereLight(0x38bdf8, 0x090d16, 0.4);
    this.scene.add(blueHemisphere);
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
    this.lblRibbonMode = document.getElementById('lblRibbonMode');
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
        this.cameraMode = e.currentTarget.dataset.cam || 'orbit';
        if (this.cameraMode === 'orbit') {
          this.controls.enabled = true;
        }
      });
    });

    // Exaggeration button
    const btnExag = document.getElementById('btnExaggeration');
    if (btnExag) {
      btnExag.addEventListener('click', () => {
        const exags = [1.0, 2.0, 3.0, 5.0];
        const nextIdx = (exags.indexOf(this.elevationExaggeration) + 1) % exags.length;
        this.elevationExaggeration = exags[nextIdx];
        if (this.lblExag) this.lblExag.textContent = `${this.elevationExaggeration.toFixed(1)}x`;
        if (this.routeData) this.loadRoute(this.routeData);
      });
    }

    // Buildings toggle
    const btnBld = document.getElementById('btnBuildings');
    if (btnBld) {
      btnBld.addEventListener('click', () => {
        this.showBuildings = !this.showBuildings;
        this.buildingsGroup.visible = this.showBuildings;
        btnBld.querySelector('b').textContent = this.showBuildings ? 'ON' : 'OFF';
      });
    }

    // Trees toggle
    const btnTrees = document.getElementById('btnTrees');
    if (btnTrees) {
      btnTrees.addEventListener('click', () => {
        this.showTrees = !this.showTrees;
        this.treesGroup.visible = this.showTrees;
        btnTrees.querySelector('b').textContent = this.showTrees ? 'ON' : 'OFF';
      });
    }

    // Ribbon mode toggle
    const btnRibbon = document.getElementById('btnRibbonMode');
    if (btnRibbon) {
      btnRibbon.addEventListener('click', () => {
        const modes = ['slope', 'thermal', 'cyan'];
        const nextIdx = (modes.indexOf(this.colorMode) + 1) % modes.length;
        this.colorMode = modes[nextIdx];
        if (this.lblRibbonMode) {
          this.lblRibbonMode.textContent = this.colorMode.charAt(0).toUpperCase() + this.colorMode.slice(1);
        }
        if (this.routeData && this.heatRibbonMgr) {
          const mode = this.colorMode === 'thermal' ? 0 : (this.colorMode === 'slope' ? 1 : 2);
          this.heatRibbonMgr.update(0.016, this.progress, mode);
        }
      });
    }

    // Slicer toggle
    const btnSlicer = document.getElementById('btnSlicer');
    if (btnSlicer) {
      let slicerOn = false;
      btnSlicer.addEventListener('click', () => {
        slicerOn = !slicerOn;
        if (this.slicerSystem) {
          this.slicerSystem.toggle(slicerOn);
        }
      });
    }

    // Night Mode toggle
    const btnNight = document.getElementById('btnNightMode');
    if (btnNight) {
      let isNight = false;
      btnNight.addEventListener('click', () => {
        isNight = !isNight;
        if (this.solarSystem) {
          this.solarSystem.setSolarHour(isNight ? 22.0 : 13.0, this.sunLight, this.ambientLight);
        }
      });
    }

    // Voice Navigation toggle
    const btnVoice = document.getElementById('btnVoice');
    if (btnVoice) {
      let isVoice = false;
      btnVoice.addEventListener('click', () => {
        isVoice = !isVoice;
        btnVoice.querySelector('b').textContent = isVoice ? 'ON' : 'OFF';
        if (this.voiceSystem) {
          this.voiceSystem.toggle(isVoice);
        }
      });
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

    // Elevation chart hover sync
    const chartDrawer = document.getElementById('chartDrawer');
    if (chartDrawer) {
      chartDrawer.addEventListener('mousemove', (e) => {
        if (!this.curve) return;
        const rect = chartDrawer.getBoundingClientRect();
        const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
        this.syncNeedleAndMarker(ratio);
      });
    }
  }

  lonLatToSceneMeters(lon, lat, eleMeters) {
    if (!this.originLonLat) return new THREE.Vector3(0, 0, 0);
    const meanLat = (this.originLonLat.lat * Math.PI) / 180.0;
    const dx = (lon - this.originLonLat.lon) * 111320.0 * Math.cos(meanLat);
    const dz = -(lat - this.originLonLat.lat) * 110574.0;
    const dy = (eleMeters || 0.0) * this.elevationExaggeration;
    return new THREE.Vector3(dx, dy, dz);
  }

  loadRoute(geojson) {
    this.routeData = geojson;
    if (this.elEmpty) this.elEmpty.style.display = 'none';

    const coords = geojson?.geometry?.coordinates || [];
    const props = geojson?.properties || {};

    if (coords.length < 2) return;

    this.originLonLat = { lon: coords[0][0], lat: coords[0][1] };
    this.scenePoints = coords.map((c) => this.lonLatToSceneMeters(c[0], c[1], c[2] || 0.0));
    this.curve = new THREE.CatmullRomCurve3(this.scenePoints);

    if (this.elDist) this.elDist.textContent = `${props.distance_km || (coords.length * 0.05).toFixed(2)} km`;
    if (this.elTime) this.elTime.textContent = `${props.duration_min || 12} min`;
    if (this.elClimb) this.elClimb.textContent = `+${props.elevation_gain_m || 15} m`;
    if (this.elSlope) this.elSlope.textContent = `${props.max_slope_pct || 6.5}%`;
    if (this.elKcal) this.elKcal.textContent = `${props.calories_kcal || 95} kcal`;

    if (this.voiceSystem && props.cue_sheet) {
      this.voiceSystem.loadCues(props.cue_sheet);
    }

    this.rebuildScene();
    this.renderProfileChart();
    this.resetCameraView();

    this.isPlaying = true;
    if (this.elPlayIcon) {
      this.elPlayIcon.textContent = '⏸';
    }
  }

  rebuildScene() {
    if (this.terrainMesh) this.scene.remove(this.terrainMesh);
    if (this.ribbonMesh) this.scene.remove(this.ribbonMesh);
    if (this.avatarMesh) this.scene.remove(this.avatarMesh);
    if (this.needlePin) this.scene.remove(this.needlePin);

    while (this.buildingsGroup.children.length) this.buildingsGroup.remove(this.buildingsGroup.children[0]);
    while (this.treesGroup.children.length) this.treesGroup.remove(this.treesGroup.children[0]);
    while (this.particlesGroup.children.length) this.particlesGroup.remove(this.particlesGroup.children[0]);
    while (this.pinsGroup.children.length) this.pinsGroup.remove(this.pinsGroup.children[0]);

    this.buildTerrain();
    this.ribbonMesh = this.heatRibbonMgr.buildRibbon(this.curve, this.scenePoints, this.routeData?.properties);
    this.buildPinMarkers();
    this.buildAvatar();
    this.avatarRig.setProfile(this.routeData?.properties?.profile_key || 'adult');
    this.buildUrbanEnvironment();
    this.buildParticleFlow();
    this.solarSystem.buildStreetlampsAlongRoute(this.curve, this.scenePoints, 35.0);
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

    const pad = Math.max(size.x, size.z) * 0.5 + 300;
    const width = size.x + pad * 2;
    const depth = size.z + pad * 2;

    const segments = 64;
    const geo = new THREE.PlaneGeometry(width, depth, segments, segments);
    geo.rotateX(-Math.PI / 2);
    geo.translate(center.x, -4, center.z);

    const pos = geo.attributes.position;
    for (let i = 0; i < pos.count; i++) {
      const vx = pos.getX(i);
      const vz = pos.getZ(i);
      const dist = Math.hypot(vx - center.x, vz - center.z);
      const wave = Math.sin(vx * 0.004) * Math.cos(vz * 0.004) * 16.0;
      pos.setY(i, Math.max(-12, wave - dist * 0.012));
    }
    geo.computeVertexNormals();

    const mat = new THREE.MeshStandardMaterial({
      color: 0x111c2e,
      roughness: 0.85,
      metalness: 0.15,
      wireframe: false,
    });
    this.terrainMesh = new THREE.Mesh(geo, mat);
    this.terrainMesh.receiveShadow = true;
    this.scene.add(this.terrainMesh);

    const grid = new THREE.GridHelper(Math.max(width, depth), 36, 0x38bdf8, 0x1e293b);
    grid.position.set(center.x, -3.5, center.z);
    this.scene.add(grid);
  }

  buildRouteRibbon() {
    if (this.ribbonMesh) this.scene.remove(this.ribbonMesh);

    const tubeGeo = new THREE.TubeGeometry(this.curve, 250, 3.5, 8, false);
    const count = tubeGeo.attributes.position.count;
    const colors = [];

    const cGreen = new THREE.Color(0x10b981);
    const cAmber = new THREE.Color(0xf59e0b);
    const cRed = new THREE.Color(0xef4444);
    const cCyan = new THREE.Color(0x38bdf8);

    for (let i = 0; i < count; i++) {
      const frac = (i / count) % 1.0;
      let col = cGreen;
      if (this.colorMode === 'cyan') {
        col = cCyan;
      } else if (this.colorMode === 'thermal') {
        col = frac > 0.5 ? cRed : cCyan;
      } else {
        if (frac > 0.65) col = cRed;
        else if (frac > 0.3) col = cAmber;
      }
      colors.push(col.r, col.g, col.b);
    }
    tubeGeo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));

    const mat = new THREE.MeshStandardMaterial({
      vertexColors: true,
      roughness: 0.25,
      metalness: 0.5,
      emissive: 0x0ea5e9,
      emissiveIntensity: 0.4,
    });

    this.ribbonMesh = new THREE.Mesh(tubeGeo, mat);
    this.ribbonMesh.castShadow = true;
    this.scene.add(this.ribbonMesh);
  }

  buildPinMarkers() {
    if (this.scenePoints.length < 2) return;

    const createPin = (pos, colorHex, label) => {
      const group = new THREE.Group();
      group.position.copy(pos);

      const sphereGeo = new THREE.SphereGeometry(6.5, 16, 16);
      const mat = new THREE.MeshStandardMaterial({ color: colorHex, roughness: 0.2, emissive: colorHex, emissiveIntensity: 0.5 });
      const head = new THREE.Mesh(sphereGeo, mat);
      head.position.y = 18;
      group.add(head);

      const coneGeo = new THREE.ConeGeometry(3.5, 18, 16);
      const cone = new THREE.Mesh(coneGeo, mat);
      cone.position.y = 9;
      cone.rotateX(Math.PI);
      group.add(cone);

      this.pinsGroup.add(group);
    };

    createPin(this.scenePoints[0], 0x10b981, 'A');
    createPin(this.scenePoints[this.scenePoints.length - 1], 0xef4444, 'B');
  }

  buildAvatar() {
    const group = new THREE.Group();

    const orbGeo = new THREE.OctahedronGeometry(5.5, 0);
    const orbMat = new THREE.MeshStandardMaterial({
      color: 0x38bdf8,
      emissive: 0x38bdf8,
      emissiveIntensity: 1.0,
      roughness: 0.1,
    });
    const orb = new THREE.Mesh(orbGeo, orbMat);
    orb.position.y = 7;
    group.add(orb);

    const pLight = new THREE.PointLight(0x38bdf8, 2.5, 100);
    pLight.position.y = 7;
    group.add(pLight);

    this.avatarMesh = group;
    this.scene.add(this.avatarMesh);

    const needleGeo = new THREE.CylinderGeometry(0.8, 0.8, 45, 8);
    const needleMat = new THREE.MeshBasicMaterial({ color: 0xf43f5e });
    this.needlePin = new THREE.Mesh(needleGeo, needleMat);
    this.needlePin.visible = false;
    this.scene.add(this.needlePin);
  }

  buildUrbanEnvironment() {
    // Procedural LOD1 3D Massing along route corridor
    const bldMat = new THREE.MeshStandardMaterial({
      color: 0x1e293b,
      roughness: 0.7,
      metalness: 0.3,
      wireframe: false,
    });

    const treeMat = new THREE.MeshStandardMaterial({
      color: 0x10b981,
      roughness: 0.9,
    });

    const step = 8;
    for (let i = 0; i < this.scenePoints.length - 1; i += step) {
      const pt = this.scenePoints[i];
      const tangent = this.curve.getTangentAt(i / this.scenePoints.length);
      const normal = new THREE.Vector3(-tangent.z, 0, tangent.x).normalize();

      // Left building
      const h1 = 20 + ((i * 13) % 45);
      const bGeo1 = new THREE.BoxGeometry(22, h1, 24);
      const bMesh1 = new THREE.Mesh(bGeo1, bldMat);
      bMesh1.position.copy(pt.clone().add(normal.clone().multiplyScalar(38)));
      bMesh1.position.y = pt.y + h1 * 0.5 - 2;
      bMesh1.castShadow = true;
      bMesh1.receiveShadow = true;
      this.buildingsGroup.add(bMesh1);

      // Right building
      const h2 = 18 + ((i * 19) % 55);
      const bGeo2 = new THREE.BoxGeometry(24, h2, 26);
      const bMesh2 = new THREE.Mesh(bGeo2, bldMat);
      bMesh2.position.copy(pt.clone().add(normal.clone().multiplyScalar(-38)));
      bMesh2.position.y = pt.y + h2 * 0.5 - 2;
      bMesh2.castShadow = true;
      bMesh2.receiveShadow = true;
      this.buildingsGroup.add(bMesh2);

      // Trees along sidewalk
      const treeTrunkGeo = new THREE.CylinderGeometry(0.8, 1.2, 8, 6);
      const treeFoliageGeo = new THREE.DodecahedronGeometry(5, 0);
      const tree = new THREE.Group();
      const trunk = new THREE.Mesh(treeTrunkGeo, new THREE.MeshStandardMaterial({ color: 0x5c4033 }));
      const foliage = new THREE.Mesh(treeFoliageGeo, treeMat);
      foliage.position.y = 8;
      tree.add(trunk);
      tree.add(foliage);
      tree.position.copy(pt.clone().add(normal.clone().multiplyScalar(18)));
      tree.position.y = pt.y + 4;
      this.treesGroup.add(tree);
    }
  }

  buildParticleFlow() {
    const particleCount = 60;
    const geo = new THREE.BufferGeometry();
    const positions = new Float32Array(particleCount * 3);

    for (let i = 0; i < particleCount; i++) {
      const frac = i / particleCount;
      const pt = this.curve.getPointAt(frac);
      positions[i * 3] = pt.x;
      positions[i * 3 + 1] = pt.y + 2;
      positions[i * 3 + 2] = pt.z;
    }
    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));

    const mat = new THREE.PointsMaterial({
      color: 0x38bdf8,
      size: 4,
      transparent: true,
      opacity: 0.8,
      blending: THREE.AdditiveBlending,
    });

    this.particleFlow = new THREE.Points(geo, mat);
    this.particlesGroup.add(this.particleFlow);
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
    if (this.avatarMesh) {
      this.avatarMesh.position.copy(pt);
      const tangent = this.curve.getTangentAt(this.progress).normalize();
      this.avatarMesh.lookAt(pt.clone().add(tangent));
    }

    if (this.avatarRig && this.avatarRig.root) {
      this.avatarRig.root.position.copy(pt);
      const tangent = this.curve.getTangentAt(this.progress).normalize();
      this.avatarRig.root.lookAt(pt.clone().add(tangent));
      const spd = this.routeData?.properties?.base_speed_kmh || 12.0;
      this.avatarRig.updateKinematics(0.016, spd, tangent, 0, this.solarSystem ? this.solarSystem.isNight : false);
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

    if (this.cameraMode === 'chase') {
      const offset = tangent.clone().multiplyScalar(-65).add(new THREE.Vector3(0, 32, 0));
      const targetPos = pt.clone().add(offset);
      this.camera.position.lerp(targetPos, 0.08);
      this.controls.target.lerp(pt.clone().add(new THREE.Vector3(0, 5, 0)), 0.1);
    } else if (this.cameraMode === 'pov') {
      this.camera.position.copy(pt.clone().add(new THREE.Vector3(0, 6, 0)));
      this.controls.target.copy(pt.clone().add(tangent.clone().multiplyScalar(120)));
    } else if (this.cameraMode === 'tour') {
      const tourOffset = new THREE.Vector3(Math.sin(this.progress * Math.PI * 4) * 80, 50, Math.cos(this.progress * Math.PI * 4) * 80);
      this.camera.position.lerp(pt.clone().add(tourOffset), 0.05);
      this.controls.target.lerp(pt, 0.08);
    }

    this.updateRadarMap();
  }

  syncNeedleAndMarker(ratio) {
    if (!this.curve || !this.needlePin) return;
    const pt = this.curve.getPointAt(ratio);
    this.needlePin.position.set(pt.x, pt.y + 22, pt.z);
    this.needlePin.visible = true;
    if (this.elNeedle) {
      this.elNeedle.style.left = `${ratio * 100}%`;
    }
  }

  snapToNorth() {
    if (!this.controls) return;
    this.camera.position.set(this.controls.target.x, this.camera.position.y, this.controls.target.z + 400);
    this.controls.update();
  }

  updateRadarMap() {
    if (!this.radarCtx || !this.curve) return;
    const ctx = this.radarCtx;
    ctx.clearRect(0, 0, 120, 120);

    // Radar grid ring
    ctx.strokeStyle = 'rgba(56, 189, 248, 0.3)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(60, 60, 50, 0, Math.PI * 2);
    ctx.stroke();

    // Draw route path
    ctx.strokeStyle = '#38bdf8';
    ctx.lineWidth = 2;
    ctx.beginPath();
    const count = 30;
    for (let i = 0; i < count; i++) {
      const pt = this.curve.getPointAt(i / (count - 1));
      const rx = 60 + (pt.x / 15.0);
      const ry = 60 + (pt.z / 15.0);
      if (i === 0) ctx.moveTo(rx, ry);
      else ctx.lineTo(rx, ry);
    }
    ctx.stroke();

    // Draw active avatar pulse
    const curr = this.curve.getPointAt(this.progress);
    const ax = 60 + (curr.x / 15.0);
    const ay = 60 + (curr.z / 15.0);
    ctx.fillStyle = '#f43f5e';
    ctx.beginPath();
    ctx.arc(ax, ay, 4, 0, Math.PI * 2);
    ctx.fill();
  }

  takeSnapshot() {
    const dataUrl = this.renderer.domElement.toDataURL('image/png');
    const a = document.createElement('a');
    a.href = dataUrl;
    a.download = `02Route3D_Snapshot_${Date.now()}.png`;
    a.click();
  }

  toggleRecordVideo() {
    if (this.isRecording) {
      if (this.mediaRecorder) this.mediaRecorder.stop();
      this.isRecording = false;
      document.getElementById('btnRecord').style.color = '';
    } else {
      const stream = this.renderer.domElement.captureStream(30);
      this.recordedChunks = [];
      this.mediaRecorder = new MediaRecorder(stream, { mimeType: 'video/webm' });
      this.mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) this.recordedChunks.push(e.data);
      };
      this.mediaRecorder.onstop = () => {
        const blob = new Blob(this.recordedChunks, { type: 'video/webm' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `02Route3D_Animation_${Date.now()}.webm`;
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
    this.camera.position.set(center.x, maxDim * 0.95, center.z + maxDim * 1.15);
    this.controls.target.copy(center);
    this.controls.update();
  }

  renderProfileChart() {
    if (!this.elSvg || !this.routeData) return;
    const coords = this.routeData?.geometry?.coordinates || [];
    if (coords.length < 2) return;

    const width = this.elSvg.clientWidth || 400;
    const height = this.elSvg.clientHeight || 80;

    const elevations = coords.map((c) => c[2] || 0.0);
    const minEle = Math.min(...elevations);
    const maxEle = Math.max(...elevations);
    const eleRange = Math.max(1, maxEle - minEle);

    let pathD = '';
    const points = coords.map((c, i) => {
      const x = (i / (coords.length - 1)) * width;
      const y = height - 14 - (((c[2] || 0.0) - minEle) / eleRange) * (height - 28);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });

    pathD = `M ${points[0]} ` + points.slice(1).map((p) => `L ${p}`).join(' ');
    const fillD = `${pathD} L ${width},${height} L 0,${height} Z`;

    this.elSvg.innerHTML = `
      <defs>
        <linearGradient id="chartGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#38bdf8" stop-opacity="0.45"/>
          <stop offset="100%" stop-color="#0ea5e9" stop-opacity="0.0"/>
        </linearGradient>
      </defs>
      <path d="${fillD}" fill="url(#chartGrad)"/>
      <path d="${pathD}" fill="none" stroke="#38bdf8" stroke-width="2.5"/>
      <text x="8" y="14" fill="#94a3b8" font-size="10" font-weight="600">${maxEle.toFixed(0)}m</text>
      <text x="8" y="${height - 4}" fill="#94a3b8" font-size="10" font-weight="600">${minEle.toFixed(0)}m</text>
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

    if (this.heatRibbonMgr) {
      const mode = this.colorMode === 'thermal' ? 0 : (this.colorMode === 'slope' ? 1 : 2);
      this.heatRibbonMgr.update(delta, this.progress, mode);
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
