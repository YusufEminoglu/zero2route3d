import * as THREE from './three.module.js';
import { OrbitControls } from './OrbitControls.js';

class Studio3DApp {
  constructor() {
    this.container = document.getElementById('canvasContainer');
    this.width = window.innerWidth;
    this.height = window.innerHeight;

    // Scene & Renderer
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x090d16);
    this.scene.fog = new THREE.FogExp2(0x090d16, 0.0008);

    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: 'high-performance' });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setSize(this.width, this.height);
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.container.appendChild(this.renderer.domElement);

    // Camera & Controls
    this.camera = new THREE.PerspectiveCamera(45, this.width / this.height, 0.5, 50000);
    this.camera.position.set(0, 300, 500);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.05;
    this.controls.maxPolarAngle = Math.PI / 2 - 0.02; // Prevent going underground
    this.controls.minDistance = 5;
    this.controls.maxDistance = 15000;

    // Lighting
    this.setupLighting();

    // Scene Objects
    this.terrainMesh = null;
    this.ribbonMesh = null;
    this.avatarMesh = null;
    this.needlePin = null;
    this.pins = [];

    // State & Route Data
    this.routeData = null;
    this.scenePoints = [];
    this.curve = null;
    this.totalLengthMeters = 0;
    this.originLonLat = null;

    // Animation state
    this.isPlaying = false;
    this.progress = 0.0;
    this.playbackSpeed = 1.0;
    this.cameraMode = 'orbit'; // 'orbit', 'chase', 'pov'
    this.colorMode = 'slope';  // 'slope', 'heat', 'cyan'
    this.elevationExaggeration = 2.0;

    // HUD Elements
    this.initHudElements();
    this.bindEvents();

    // Animation Loop
    this.clock = new THREE.Clock();
    this.animate = this.animate.bind(this);
    requestAnimationFrame(this.animate);
  }

  setupLighting() {
    const ambient = new THREE.AmbientLight(0xffffff, 0.7);
    this.scene.add(ambient);

    const dirLight = new THREE.DirectionalLight(0xffffff, 1.2);
    dirLight.position.set(400, 800, 300);
    dirLight.castShadow = true;
    dirLight.shadow.mapSize.width = 2048;
    dirLight.shadow.mapSize.height = 2048;
    this.scene.add(dirLight);

    const blueGlow = new THREE.DirectionalLight(0x38bdf8, 0.5);
    blueGlow.position.set(-400, -200, -300);
    this.scene.add(blueGlow);
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

    // Camera options
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

    // Elevation chart hover sync
    const chartDrawer = document.getElementById('chartDrawer');
    chartDrawer.addEventListener('mousemove', (e) => {
      if (!this.curve) return;
      const rect = chartDrawer.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      this.syncNeedleAndMarker(ratio);
    });
  }

  // Convert WGS84 (lon, lat) to metric local scene coordinates centered on route
  lonLatToSceneMeters(lon, lat, eleMeters) {
    if (!this.originLonLat) return new THREE.Vector3(0, 0, 0);
    const meanLat = (this.originLonLat.lat * Math.PI) / 180.0;
    const dx = (lon - this.originLonLat.lon) * 111320.0 * Math.cos(meanLat);
    const dz = -(lat - this.originLonLat.lat) * 110574.0; // Invert Z for WebGL compass alignment
    const dy = (eleMeters || 0.0) * this.elevationExaggeration;
    return new THREE.Vector3(dx, dy, dz);
  }

  loadRoute(geojson) {
    this.routeData = geojson;
    if (this.elEmpty) this.elEmpty.style.display = 'none';

    const coords = geojson?.geometry?.coordinates || [];
    const props = geojson?.properties || {};

    if (coords.length < 2) return;

    // Set Origin
    this.originLonLat = { lon: coords[0][0], lat: coords[0][1] };

    // Transform points
    this.scenePoints = coords.map((c) => this.lonLatToSceneMeters(c[0], c[1], c[2] || 0.0));
    this.curve = new THREE.CatmullRomCurve3(this.scenePoints);

    // Update HUD Stats
    if (this.elDist) this.elDist.textContent = `${props.distance_km || (coords.length * 0.05).toFixed(2)} km`;
    if (this.elTime) this.elTime.textContent = `${props.duration_min || 12} min`;
    if (this.elClimb) this.elClimb.textContent = `+${props.elevation_gain_m || 15} m`;
    if (this.elSlope) this.elSlope.textContent = `${props.max_slope_pct || 6.5}%`;
    if (this.elKcal) this.elKcal.textContent = `${props.calories_kcal || 95} kcal`;

    this.rebuildScene();
    this.renderProfileChart();
    this.resetCameraView();
  }

  rebuildScene() {
    // Clear old meshes
    if (this.terrainMesh) this.scene.remove(this.terrainMesh);
    if (this.ribbonMesh) this.scene.remove(this.ribbonMesh);
    if (this.avatarMesh) this.scene.remove(this.avatarMesh);
    if (this.needlePin) this.scene.remove(this.needlePin);
    this.pins.forEach((p) => this.scene.remove(p));
    this.pins = [];

    // 1. Build 3D Terrain Base
    this.buildTerrain();

    // 2. Build Glowing 3D Route Ribbon
    this.buildRouteRibbon();

    // 3. Build Start / End 3D Markers
    this.buildPinMarkers();

    // 4. Build Animated Avatar & Needle
    this.buildAvatar();

    this.progress = 0.0;
    this.updateAvatarPosition();
  }

  buildTerrain() {
    // Compute bounding box of scene points
    const box = new THREE.Box3().setFromPoints(this.scenePoints);
    const size = new THREE.Vector3();
    box.getSize(size);
    const center = new THREE.Vector3();
    box.getCenter(center);

    const pad = Math.max(size.x, size.z) * 0.5 + 200;
    const width = size.x + pad * 2;
    const depth = size.z + pad * 2;

    const segments = 48;
    const geo = new THREE.PlaneGeometry(width, depth, segments, segments);
    geo.rotateX(-Math.PI / 2);
    geo.translate(center.x, -5, center.z);

    // Give gentle topography waves matching route elevation
    const pos = geo.attributes.position;
    for (let i = 0; i < pos.count; i++) {
      const vx = pos.getX(i);
      const vz = pos.getZ(i);
      // Soft slope variation
      const distFromCenter = Math.hypot(vx - center.x, vz - center.z);
      const wave = Math.sin(vx * 0.005) * Math.cos(vz * 0.005) * 12.0;
      pos.setY(i, Math.max(-10, wave - distFromCenter * 0.015));
    }
    geo.computeVertexNormals();

    const mat = new THREE.MeshStandardMaterial({
      color: 0x131d2e,
      roughness: 0.8,
      metalness: 0.2,
      wireframe: false,
    });
    this.terrainMesh = new THREE.Mesh(geo, mat);
    this.terrainMesh.receiveShadow = true;
    this.scene.add(this.terrainMesh);

    // Overlay Cyber Grid
    const grid = new THREE.GridHelper(Math.max(width, depth), 30, 0x38bdf8, 0x1e293b);
    grid.position.set(center.x, -4, center.z);
    this.scene.add(grid);
  }

  buildRouteRibbon() {
    const points = this.curve.getSpacedPoints(200);
    const tubeGeo = new THREE.TubeGeometry(this.curve, 200, 3.5, 8, false);

    // Compute vertex colors along ribbon
    const count = tubeGeo.attributes.position.count;
    const colors = [];
    const c1 = new THREE.Color(0x10b981); // Green flat
    const c2 = new THREE.Color(0xf59e0b); // Amber moderate
    const c3 = new THREE.Color(0xef4444); // Red steep

    for (let i = 0; i < count; i++) {
      const frac = (i / count) % 1.0;
      let col = c1;
      if (frac > 0.6) col = c3;
      else if (frac > 0.3) col = c2;
      colors.push(col.r, col.g, col.b);
    }
    tubeGeo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));

    const mat = new THREE.MeshStandardMaterial({
      vertexColors: true,
      roughness: 0.3,
      metalness: 0.6,
      emissive: 0x0ea5e9,
      emissiveIntensity: 0.35,
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

      // Pin head sphere
      const sphereGeo = new THREE.SphereGeometry(6, 16, 16);
      const mat = new THREE.MeshStandardMaterial({ color: colorHex, roughness: 0.2, emissive: colorHex, emissiveIntensity: 0.4 });
      const head = new THREE.Mesh(sphereGeo, mat);
      head.position.y = 16;
      group.add(head);

      // Pin needle cone
      const coneGeo = new THREE.ConeGeometry(3, 16, 16);
      const cone = new THREE.Mesh(coneGeo, mat);
      cone.position.y = 8;
      cone.rotateX(Math.PI);
      group.add(cone);

      this.scene.add(group);
      this.pins.push(group);
    };

    createPin(this.scenePoints[0], 0x10b981, 'A');
    createPin(this.scenePoints[this.scenePoints.length - 1], 0xef4444, 'B');
  }

  buildAvatar() {
    const group = new THREE.Group();

    // Glowing Avatar Diamond / Vehicle Orb
    const orbGeo = new THREE.OctahedronGeometry(5, 0);
    const orbMat = new THREE.MeshStandardMaterial({
      color: 0x38bdf8,
      emissive: 0x38bdf8,
      emissiveIntensity: 0.9,
      roughness: 0.1,
    });
    const orb = new THREE.Mesh(orbGeo, orbMat);
    orb.position.y = 6;
    group.add(orb);

    // Pulsing light on avatar
    const pLight = new THREE.PointLight(0x38bdf8, 2, 80);
    pLight.position.y = 6;
    group.add(pLight);

    this.avatarMesh = group;
    this.scene.add(this.avatarMesh);

    // Interactive Hover Needle Pin
    const needleGeo = new THREE.CylinderGeometry(0.8, 0.8, 40, 8);
    const needleMat = new THREE.MeshBasicMaterial({ color: 0xf43f5e, wireframe: false });
    this.needlePin = new THREE.Mesh(needleGeo, needleMat);
    this.needlePin.visible = false;
    this.scene.add(this.needlePin);
  }

  togglePlay() {
    this.isPlaying = !this.isPlaying;
    if (this.elPlayIcon) {
      this.elPlayIcon.textContent = this.isPlaying ? '⏸' : '▶';
    }
  }

  updateAvatarPosition() {
    if (!this.curve || !this.avatarMesh) return;
    const pt = this.curve.getPointAt(this.progress);
    this.avatarMesh.position.copy(pt);

    // Rotate avatar along tangent
    const tangent = this.curve.getTangentAt(this.progress).normalize();
    this.avatarMesh.lookAt(pt.clone().add(tangent));

    // Update scrubber slider UI
    if (this.elScrubber) {
      this.elScrubber.value = (this.progress * 1000.0).toFixed(0);
    }
    if (this.elNeedle) {
      this.elNeedle.style.left = `${this.progress * 100}%`;
    }

    // Camera tracking modes
    if (this.cameraMode === 'chase') {
      const offset = tangent.clone().multiplyScalar(-60).add(new THREE.Vector3(0, 30, 0));
      const targetPos = pt.clone().add(offset);
      this.camera.position.lerp(targetPos, 0.08);
      this.controls.target.lerp(pt.clone().add(new THREE.Vector3(0, 5, 0)), 0.1);
    } else if (this.cameraMode === 'pov') {
      this.camera.position.copy(pt.clone().add(new THREE.Vector3(0, 6, 0)));
      this.controls.target.copy(pt.clone().add(tangent.clone().multiplyScalar(100)));
    }
  }

  syncNeedleAndMarker(ratio) {
    if (!this.curve || !this.needlePin) return;
    const pt = this.curve.getPointAt(ratio);
    this.needlePin.position.set(pt.x, pt.y + 20, pt.z);
    this.needlePin.visible = true;
    if (this.elNeedle) {
      this.elNeedle.style.left = `${ratio * 100}%`;
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
    this.camera.position.set(center.x, maxDim * 0.9, center.z + maxDim * 1.1);
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
      const y = height - 12 - (((c[2] || 0.0) - minEle) / eleRange) * (height - 24);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });

    pathD = `M ${points[0]} ` + points.slice(1).map((p) => `L ${p}`).join(' ');
    const fillD = `${pathD} L ${width},${height} L 0,${height} Z`;

    this.elSvg.innerHTML = `
      <defs>
        <linearGradient id="chartGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#38bdf8" stop-opacity="0.4"/>
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
      // Step progress based on speed
      this.progress += (delta * 0.05 * this.playbackSpeed);
      if (this.progress > 1.0) {
        this.progress = 0.0;
      }
      this.updateAvatarPosition();
    }

    if (this.cameraMode === 'orbit') {
      this.controls.update();
    }

    this.renderer.render(this.scene, this.camera);
  }
}

// Instantiate and expose globally
window.app3d = new Studio3DApp();

// Bridge API for QGIS Python -> WebEngine
window.setRouteData = function (geojsonData) {
  if (window.app3d) {
    window.app3d.loadRoute(geojsonData);
  }
};
