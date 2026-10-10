import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from '../../web/js/three.module.js';
import { mergeGeometries, ribbonGeometry, RoutePath } from '../../web/js/scene_geometry.js';

const points = [new THREE.Vector3(0, 0, 0), new THREE.Vector3(100, 10, 0), new THREE.Vector3(100, 30, -100)];

test('route path is parameterised by horizontal distance', () => {
  const path = new RoutePath(points);
  assert.equal(path.total, 200);
  assert.deepEqual(path.cumulative, [0, 100, 200]);
  assert.ok(Math.abs(path.paramAt(0.5) - 0.5) < 1e-12);
  assert.equal(path.paramAt(-1), 0);
  assert.equal(path.paramAt(2), 1);
  const mid = path.at(0.5).point;
  assert.ok(Math.abs(mid.x - 100) < 1e-6 && Math.abs(mid.z) < 1e-6);
});

test('ribbon follows the route heights at the given width', () => {
  const path = new RoutePath(points);
  const geo = ribbonGeometry(path, 4, 0.5);
  const pos = geo.getAttribute('position');
  // The first pair straddles the start, 4 m apart, raised by the offset.
  const a = new THREE.Vector3().fromBufferAttribute(pos, 0);
  const b = new THREE.Vector3().fromBufferAttribute(pos, 1);
  assert.ok(Math.abs(a.distanceTo(b) - 4) < 1e-6);
  assert.ok(Math.abs(a.y - 0.5) < 1e-6);
  // The last pair sits at the end height (30 m): heights are not squashed.
  const last = new THREE.Vector3().fromBufferAttribute(pos, pos.count - 1);
  assert.ok(Math.abs(last.y - 30.5) < 1e-6, String(last.y));
  assert.equal(geo.index.count, (pos.count / 2 - 1) * 6);
});

test('merging keeps every vertex of every part', () => {
  const box = new THREE.BoxGeometry(1, 1, 1);
  const plane = new THREE.PlaneGeometry(2, 2);
  const merged = mergeGeometries([box, plane]);
  assert.equal(merged.getAttribute('position').count, box.index.count + plane.index.count);
  assert.ok(merged.getAttribute('normal') && merged.getAttribute('uv'));
});
