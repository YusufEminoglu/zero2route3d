import * as THREE from './three.module.js';

export class TerrainSlicerSystem {
  constructor(scene, renderer) {
    this.scene = scene;
    this.renderer = renderer;
    this.renderer.localClippingEnabled = true;

    this.sliceHeight = 500.0;
    this.minHeight = -50.0;
    this.maxHeight = 800.0;
    this.isEnabled = false;
    this.clipPlane = new THREE.Plane(new THREE.Vector3(0, -1, 0), this.sliceHeight);

    const capGeo = new THREE.PlaneGeometry(5000, 5000);
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

  attachToTerrain(terrainMesh, buildingsGroup) {
    this.terrainMesh = terrainMesh;
    this.buildingsGroup = buildingsGroup;
  }

  setSliceHeight(height) {
    this.sliceHeight = height;
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

    if (this.buildingsGroup) {
      this.buildingsGroup.traverse((child) => {
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
