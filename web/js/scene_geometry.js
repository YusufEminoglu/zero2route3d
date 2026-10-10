// Geometry helpers shared by the viewer: disposal, merging, route ribbons
// and the distance-parameterised route path.
import * as THREE from './three.module.js';

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

export function disposeHierarchy(obj) {
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
export function mergeGeometries(geometries) {
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

// A flat ribbon of the given width that follows a route path at its true
// heights. (The old ribbons were tubes squashed with geometry.scale(1, 0.12, 1),
// which also squashed the route's heights to 12 %: on hills the ribbon sank
// under the terrain.)
export function ribbonGeometry(path, width, yOffset = 0) {
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
export class RoutePath {
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
