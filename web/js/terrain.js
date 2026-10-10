// Terrain: the DEM grid from QGIS when there is one, otherwise an
// approximate surface fitted through the route (and labelled as such).
// Methods of Studio3DApp (app3d.js), mixed into its prototype.
import * as THREE from './three.module.js';
import { isValidTerrainGrid, localToLonLat, sampleTerrainGrid } from './geo.js';

export const terrainMethods = {
  setTerrainGrid(grid) {
    this.terrainGrid = isValidTerrainGrid(grid) ? grid : null;
  },

  sceneToLonLat(x, z) {
    return localToLonLat(this.originLonLat, x, z);
  },

  // Ground height (scene y) under a scene point: the DEM grid when QGIS sent
  // one, otherwise the approximate surface fitted through the route.
  groundY(x, z) {
    const g = this.terrainGrid;
    if (!g || !this.originLonLat) return this.getTerrainElevationAt(x, z);
    const { lon, lat } = this.sceneToLonLat(x, z);
    const elevation = sampleTerrainGrid(g, lon, lat);
    return (elevation - this.baseElevation) * this.elevationExaggeration;
  },

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
  },

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
  },

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
  },

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

    // Least-squares plane through the route for the regional slope. The
    // normal equations below hold for coordinates centred on the point mean;
    // centring on the bounding-box middle (as before) tilted the plane.
    const n = Math.max(1, this.scenePoints.length);
    const meanX = this.scenePoints.reduce((a, p) => a + p.x, 0) / n;
    const meanZ = this.scenePoints.reduce((a, p) => a + p.z, 0) / n;
    const meanY = this.scenePoints.reduce((a, p) => a + p.y, 0) / n;
    let sumXX = 0, sumZZ = 0, sumXZ = 0, sumXY = 0, sumZY = 0;
    for (const p of this.scenePoints) {
      const x = p.x - meanX;
      const z = p.z - meanZ;
      const y = p.y - meanY;
      sumXX += x * x; sumZZ += z * z; sumXZ += x * z;
      sumXY += x * y; sumZY += z * y;
    }
    const denom = (sumXX * sumZZ - sumXZ * sumXZ);
    const gradX = Math.abs(denom) > 1e-4 ? (sumXY * sumZZ - sumZY * sumXZ) / denom : 0;
    const gradZ = Math.abs(denom) > 1e-4 ? (sumZY * sumXX - sumXY * sumXZ) / denom : 0;

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
      const regionalEle = meanY + gradX * (vx - meanX) + gradZ * (vz - meanZ);
      const blend = Math.max(0.0, Math.min(1.0, (minDist - 20.0) / 60.0));
      posAttr.setY(i, (1.0 - blend) * localRouteEle + blend * regionalEle - 0.35);
    }
    return { geo, width, depth, center, nx: segments, ny: segments };
  },

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
  },
};
