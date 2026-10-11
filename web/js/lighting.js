// Lighting and the sun: date and solar time place the sun light with the
// same formula as the QGIS shade analysis (solar.js).
// Methods of Studio3DApp (app3d.js), mixed into its prototype.
import * as THREE from './three.module.js';
import { compassPoint, DEFAULT_SUN, formatSolarHour, isoFromDayOfYear, parseSunParam, sunDirection, sunPosition } from './solar.js';

export const lightingMethods = {
  setupLighting() {
    this.hemiLight = new THREE.HemisphereLight(0xffffff, 0xe2e8f0, 0.95);
    this.scene.add(this.hemiLight);

    this.ambientLight = new THREE.AmbientLight(0xffffff, 0.45);
    this.scene.add(this.ambientLight);

    this.sunLight = new THREE.DirectionalLight(0xfffaed, 1.35);
    this.sunLight.position.set(400, 800, 300);
    this.sunLight.castShadow = true;
    const shadowSize = Math.min(4096, this.renderer.capabilities.maxTextureSize || 2048);
    this.sunLight.shadow.mapSize.width = shadowSize;
    this.sunLight.shadow.mapSize.height = shadowSize;
    this.sunLight.shadow.camera.near = 10;
    this.sunLight.shadow.camera.far = 10000;
    const d = 1400;
    this.sunLight.shadow.camera.left = -d;
    this.sunLight.shadow.camera.right = d;
    this.sunLight.shadow.camera.top = d;
    this.sunLight.shadow.camera.bottom = -d;
    this.sunLight.shadow.bias = -0.0003;
    this.scene.add(this.sunLight);
    this.scene.add(this.sunLight.target);
    let fromUrl = null;
    try {
      fromUrl = parseSunParam(new URLSearchParams(window.location.search).get('sun'));
    } catch (err) {
      fromUrl = null;
    }
    this.sun = { ...(fromUrl || DEFAULT_SUN) };
  },

  // Place the sun for the route's latitude, date and solar time: the light
  // direction (and so every building and tree shadow) follows the same
  // formula as the QGIS shade analysis. Below the horizon the sun light is
  // off and the sky light dims.
  applySun() {
    const lat = this.originLonLat ? this.originLonLat.lat : 45;
    const { azimuthDeg, elevationDeg } = sunPosition(lat, this.sun.dayOfYear, this.sun.solarHour);
    const up = elevationDeg > 0;
    const dir = sunDirection(azimuthDeg, Math.max(elevationDeg, 2));
    const pts = this.scenePoints || [];
    let cx = 0;
    let cz = 0;
    let half = 300;
    if (pts.length) {
      const xs = pts.map((p) => p.x);
      const zs = pts.map((p) => p.z);
      cx = (Math.min(...xs) + Math.max(...xs)) / 2;
      cz = (Math.min(...zs) + Math.max(...zs)) / 2;
      half = Math.max(150, (Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...zs) - Math.min(...zs))) / 2 + 120);
    }
    // The shadow map covers the route corridor only (not a fixed 2.8 km
    // square), so building shadows stay sharp.
    const cam = this.sunLight.shadow.camera;
    if (cam.right !== half) {
      cam.left = -half;
      cam.right = half;
      cam.top = half;
      cam.bottom = -half;
      cam.far = half * 4 + 4000;
      cam.updateProjectionMatrix();
    }
    const distance = half * 2 + 1000;
    this.sunLight.target.position.set(cx, 0, cz);
    this.sunLight.position.set(cx + dir.x * distance, dir.y * distance, cz + dir.z * distance);
    this.sunLight.target.updateMatrixWorld();
    // Low sun is weaker and warmer; at night only a dim sky light remains.
    // Sky and ambient light stay well below the sun so shadows read clearly.
    const strength = up ? Math.min(1, 0.35 + Math.sin((elevationDeg * Math.PI) / 180) * 1.3) : 0;
    this.sunLight.intensity = 1.7 * strength;
    this.sunLight.castShadow = up;
    this.sunLight.color.setHSL(0.11, up ? 0.9 - 0.6 * strength : 0.5, 0.96 - 0.08 * (1 - strength));
    this.hemiLight.intensity = up ? 0.45 + 0.25 * strength : 0.3;
    this.ambientLight.intensity = up ? 0.18 + 0.1 * strength : 0.15;
    // Sky colour: day blue, a warmer haze near the horizon, deep blue at night.
    const sky = up ? (strength > 0.6 ? 0xe0f2fe : 0xfde7c7) : 0x0f1e3a;
    this.scene.background.setHex(sky);
    this.scene.fog.color.setHex(up ? (strength > 0.6 ? 0xf0f9ff : 0xfdf0dc) : 0x0f1e3a);
    this.sunState = { azimuthDeg, elevationDeg, up };
    this.updateSunControls();
    this.requestRender();
  },

  updateSunControls() {
    const date = document.getElementById('sunDate');
    const hour = document.getElementById('sunHour');
    const label = document.getElementById('lblSunTime');
    const note = document.getElementById('sunNote');
    if (date) date.value = isoFromDayOfYear(this.sun.dayOfYear);
    if (hour) hour.value = String(this.sun.solarHour);
    if (label) label.textContent = formatSolarHour(this.sun.solarHour);
    if (note && this.sunState) {
      const { azimuthDeg, elevationDeg, up } = this.sunState;
      note.textContent = up
        ? `Sun ${elevationDeg.toFixed(0)}\u00b0 up, from the ${compassPoint(azimuthDeg)} (${azimuthDeg.toFixed(0)}\u00b0)`
        : 'Sun below the horizon';
    }
  },

  setSun(changes) {
    this.sun = { ...this.sun, ...changes };
    this.applySun();
  },
};
