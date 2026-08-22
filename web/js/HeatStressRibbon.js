import * as THREE from './three.module.js';

export const HeatStressShader = {
  uniforms: {
    uTime: { value: 0.0 },
    uAvatarProgress: { value: 0.0 },
    uGlowIntensity: { value: 1.2 },
    uThermalThreshold: { value: 0.7 },
    uColorMode: { value: 0 },
    uBaseColor: { value: new THREE.Color(0x38bdf8) },
  },

  vertexShader: `
    uniform float uTime;
    attribute float aProgress;
    attribute float aHeatIndex;
    
    varying vec2 vUv;
    varying float vProgress;
    varying float vHeatIndex;
    varying vec3 vNormal;
    varying vec3 vViewPosition;
    varying vec3 vWorldPosition;

    void main() {
      vUv = uv;
      vProgress = aProgress;
      vHeatIndex = aHeatIndex;
      vNormal = normalize(normalMatrix * normal);

      vec3 displacedPos = position;
      if (aHeatIndex > 0.65) {
        float pulse = sin(uTime * 5.0 + aProgress * 30.0) * 0.45 * (aHeatIndex - 0.65);
        displacedPos += normal * pulse;
      }

      vec4 worldPosition = modelMatrix * vec4(displacedPos, 1.0);
      vWorldPosition = worldPosition.xyz;
      vec4 mvPosition = viewMatrix * worldPosition;
      vViewPosition = -mvPosition.xyz;

      gl_Position = projectionMatrix * mvPosition;
    }
  `,

  fragmentShader: `
    uniform float uTime;
    uniform float uAvatarProgress;
    uniform float uGlowIntensity;
    uniform int uColorMode;
    uniform vec3 uBaseColor;

    varying vec2 vUv;
    varying float vProgress;
    varying float vHeatIndex;
    varying vec3 vNormal;
    varying vec3 vViewPosition;
    varying vec3 vWorldPosition;

    vec3 getHeatGradient(float t) {
      vec3 c0 = vec3(0.063, 0.725, 0.506);
      vec3 c1 = vec3(0.055, 0.647, 0.914);
      vec3 c2 = vec3(0.961, 0.620, 0.043);
      vec3 c3 = vec3(0.937, 0.267, 0.267);
      vec3 c4 = vec3(0.850, 0.050, 0.800);

      if (t < 0.25) return mix(c0, c1, smoothstep(0.0, 0.25, t));
      if (t < 0.55) return mix(c1, c2, smoothstep(0.25, 0.55, t));
      if (t < 0.80) return mix(c2, c3, smoothstep(0.55, 0.80, t));
      return mix(c3, c4, smoothstep(0.80, 1.0, t));
    }

    vec3 getSlopeGradient(float s) {
      vec3 cFlat = vec3(0.063, 0.725, 0.506);
      vec3 cMod = vec3(0.961, 0.620, 0.043);
      vec3 cSteep = vec3(0.937, 0.267, 0.267);
      if (s < 0.4) return mix(cFlat, cMod, s / 0.4);
      return mix(cMod, cSteep, (s - 0.4) / 0.6);
    }

    void main() {
      vec3 viewDir = normalize(vViewPosition);
      vec3 norm = normalize(vNormal);

      vec3 baseCol;
      if (uColorMode == 0) {
        baseCol = getHeatGradient(clamp(vHeatIndex, 0.0, 1.0));
      } else if (uColorMode == 1) {
        baseCol = getSlopeGradient(clamp(vHeatIndex, 0.0, 1.0));
      } else {
        baseCol = uBaseColor;
      }

      float wave = sin(vProgress * 50.0 - uTime * 6.0);
      float wavePulse = smoothstep(0.7, 1.0, wave) * 0.45;

      float fresnel = pow(1.0 - max(dot(viewDir, norm), 0.0), 2.5);
      vec3 rimColor = mix(baseCol, vec3(1.0), 0.4) * fresnel * 0.8;

      float distToAvatar = abs(vProgress - uAvatarProgress);
      float avatarHalo = exp(-distToAvatar * 75.0) * 1.8;
      vec3 haloColor = vec3(1.0, 1.0, 1.0) * avatarHalo;

      float stripe = sin(vUv.x * 3.14159 * 4.0) * 0.08;

      vec3 finalRgb = (baseCol + wavePulse + stripe) * uGlowIntensity + rimColor + haloColor;
      gl_FragColor = vec4(finalRgb, 0.95);
    }
  `,
};

export class HeatStressRibbonManager {
  constructor(scene) {
    this.scene = scene;
    this.mesh = null;
    this.material = null;
  }

  buildRibbon(curve, samplePoints, profileData = {}) {
    if (this.mesh) this.scene.remove(this.mesh);

    const tubularSegments = 300;
    const radius = 3.2;
    const radialSegments = 10;
    const closed = false;

    const tubeGeo = new THREE.TubeGeometry(curve, tubularSegments, radius, radialSegments, closed);
    const count = tubeGeo.attributes.position.count;

    const aProgress = new Float32Array(count);
    const aHeatIndex = new Float32Array(count);

    const elevations = samplePoints.map((p) => p.y);
    const minEle = Math.min(...elevations);
    const maxEle = Math.max(...elevations);
    const eleSpan = Math.max(1.0, maxEle - minEle);

    for (let i = 0; i < count; i++) {
      const u = (i % tubularSegments) / tubularSegments;
      aProgress[i] = u;

      const ptIdx = Math.min(samplePoints.length - 1, Math.floor(u * (samplePoints.length - 1)));
      const nextIdx = Math.min(samplePoints.length - 1, ptIdx + 1);
      const dy = Math.abs(samplePoints[nextIdx].y - samplePoints[ptIdx].y);
      const dxz = samplePoints[nextIdx].clone().setY(0).distanceTo(samplePoints[ptIdx].clone().setY(0));
      const slope = dxz > 0.1 ? Math.min(1.0, (dy / dxz) * 5.0) : 0.0;

      const eleFactor = (samplePoints[ptIdx].y - minEle) / eleSpan;
      aHeatIndex[i] = slope * 0.6 + eleFactor * 0.4;
    }

    tubeGeo.setAttribute('aProgress', new THREE.BufferAttribute(aProgress, 1));
    tubeGeo.setAttribute('aHeatIndex', new THREE.BufferAttribute(aHeatIndex, 1));

    this.material = new THREE.ShaderMaterial({
      uniforms: THREE.UniformsUtils.clone(HeatStressShader.uniforms),
      vertexShader: HeatStressShader.vertexShader,
      fragmentShader: HeatStressShader.fragmentShader,
      transparent: true,
      depthWrite: true,
      side: THREE.DoubleSide,
    });

    this.mesh = new THREE.Mesh(tubeGeo, this.material);
    this.mesh.castShadow = true;
    this.scene.add(this.mesh);
    return this.mesh;
  }

  update(delta, progress, mode = 0) {
    if (!this.material) return;
    this.material.uniforms.uTime.value += delta;
    this.material.uniforms.uAvatarProgress.value = progress;
    this.material.uniforms.uColorMode.value = mode;
  }
}
