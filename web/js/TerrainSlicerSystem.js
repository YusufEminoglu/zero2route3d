import * as THREE from './three.module.js';

export class TerrainSlicerSystem {
  constructor(scene, renderer) {
    this.scene = scene;
    this.renderer = renderer;
    this.renderer.localClippingEnabled = true;

    this.sliceHeight = 25.0;
    this.minHeight = -15.0;
    this.maxHeight = 120.0;
    this.isEnabled = false;
    this.clipPlane = new THREE.Plane(new THREE.Vector3(0, -1, 0), this.sliceHeight);

    const capGeo = new THREE.PlaneGeometry(1, 1);
    capGeo.rotateX(Math.PI / 2);

    this.geologyCapMat = new THREE.ShaderMaterial({
      clippingPlanes: [],
      uniforms: {
        uSliceY: { value: this.sliceHeight },
      },
      vertexShader: `
        varying vec3 vWorldPos;
        void main() {
          vec4 wp = modelMatrix * vec4(position, 1.0);
          vWorldPos = wp.xyz;
          gl_Position = projectionMatrix * viewMatrix * wp;
        }
      `,
      fragmentShader: `
        uniform float uSliceY;
        varying vec3 vWorldPos;
        void main() {
          float depth = uSliceY - vWorldPos.y;
          vec3 topsoil = vec3(0.18, 0.12, 0.08);
          vec3 sandstone = vec3(0.72, 0.54, 0.35);
          vec3 bedrock = vec3(0.12, 0.16, 0.22);

          vec3 col = topsoil;
          if (depth > 15.0) {
            col = mix(sandstone, bedrock, clamp((depth - 15.0) / 20.0, 0.0, 1.0));
          } else if (depth > 3.0) {
            col = mix(topsoil, sandstone, clamp((depth - 3.0) / 12.0, 0.0, 1.0));
          }

          float ring = mod(depth, 5.0) < 0.35 ? 0.2 : 0.0;
          gl_FragColor = vec4(col + ring, 1.0);
        }
      `,
      side: THREE.DoubleSide,
      polygonOffset: true,
      polygonOffsetFactor: -1,
      polygonOffsetUnits: -1,
    });

    this.capMesh = new THREE.Mesh(capGeo, this.geologyCapMat);
    this.capMesh.position.y = this.sliceHeight;
    this.capMesh.visible = false;
    this.scene.add(this.capMesh);
  }

  attachToTerrain(terrainMesh, buildingsGroup, treesGroup = null, skirtMesh = null) {
    this.terrainMesh = terrainMesh;
    this.buildingsGroup = buildingsGroup;
    this.treesGroup = treesGroup;
    this.skirtMesh = skirtMesh;

    const box = new THREE.Box3();
    [terrainMesh, buildingsGroup, treesGroup, skirtMesh].forEach((obj) => {
      if (obj) box.expandByObject(obj);
    });

    if (!box.isEmpty()) {
      const center = new THREE.Vector3();
      const size = new THREE.Vector3();
      box.getCenter(center);
      box.getSize(size);
      this.minHeight = Math.floor(box.min.y - 4.0);
      this.maxHeight = Math.ceil(Math.max(box.max.y + 4.0, this.minHeight + 16.0));
      const defaultSlice = this.minHeight + (this.maxHeight - this.minHeight) * 0.45;
      if (this.capMesh) {
        this.capMesh.position.set(center.x, this.sliceHeight, center.z);
        this.capMesh.scale.set(Math.max(120, size.x * 1.05), 1, Math.max(120, size.z * 1.05));
      }
      this.setSliceHeight(defaultSlice);
    }

    if (this.isEnabled) {
      this.toggle(true);
    }
  }

  setSliceHeight(height) {
    const clamped = Math.max(this.minHeight, Math.min(this.maxHeight, Number(height) || 0));
    this.sliceHeight = clamped;
    this.clipPlane.constant = this.sliceHeight;
    if (this.capMesh) {
      this.capMesh.position.y = this.sliceHeight;
    }
    if (this.geologyCapMat && this.geologyCapMat.uniforms && this.geologyCapMat.uniforms.uSliceY) {
      this.geologyCapMat.uniforms.uSliceY.value = this.sliceHeight;
    }
  }

  toggle(enable) {
    this.isEnabled = enable !== undefined ? Boolean(enable) : !this.isEnabled;
    const planes = this.isEnabled ? [this.clipPlane] : [];

    if (this.terrainMesh && this.terrainMesh.material) {
      this.terrainMesh.material.clippingPlanes = planes;
      this.terrainMesh.material.clipShadows = true;
      this.terrainMesh.material.needsUpdate = true;
    }

    if (this.skirtMesh && this.skirtMesh.material) {
      this.skirtMesh.material.clippingPlanes = planes;
      this.skirtMesh.material.clipShadows = true;
      this.skirtMesh.material.needsUpdate = true;
    }

    if (this.buildingsGroup) {
      this.buildingsGroup.traverse((child) => {
        if (child.isMesh && child.material) {
          child.material.clippingPlanes = planes;
          child.material.needsUpdate = true;
        }
      });
    }

    if (this.treesGroup) {
      this.treesGroup.traverse((child) => {
        if (child.isMesh && child.material) {
          child.material.clippingPlanes = planes;
          child.material.needsUpdate = true;
        }
      });
    }

    if (this.capMesh) {
      this.capMesh.visible = this.isEnabled;
    }
    return this.isEnabled;
  }

  get active() {
    return this.isEnabled;
  }

  dispose() {
    if (this.capMesh) {
      this.scene.remove(this.capMesh);
      if (this.capMesh.geometry) this.capMesh.geometry.dispose();
      if (this.capMesh.material) this.capMesh.material.dispose();
      this.capMesh = null;
    }
  }
}
