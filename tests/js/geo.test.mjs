import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  basemapTileRange, isValidTerrainGrid, lat2tile, localToLonLat, lon2tile, lonLatToLocal, sampleTerrainGrid,
  tile2lat, tile2lon,
} from '../../web/js/geo.js';

test('local metre frame round-trips and points the right way', () => {
  const origin = { lon: 27.14, lat: 38.42 };
  const p = lonLatToLocal(origin, 27.15, 38.43);
  assert.ok(p.x > 860 && p.x < 880, `east ${p.x}`); // ~872 m east
  assert.ok(p.z < -1100 && p.z > -1110, `north is -z ${p.z}`);
  const back = localToLonLat(origin, p.x, p.z);
  assert.ok(Math.abs(back.lon - 27.15) < 1e-12 && Math.abs(back.lat - 38.43) < 1e-12);
});

test('tile math matches the slippy-map scheme', () => {
  // Izmir at zoom 12: x 2356, y 1573 (the OSM formula, checked in Python).
  assert.equal(lon2tile(27.14, 12), 2356);
  assert.equal(lat2tile(38.42, 12), 1573);
  assert.ok(tile2lon(2356, 12) <= 27.14 && tile2lon(2357, 12) > 27.14);
  assert.ok(tile2lat(1573, 12) >= 38.42 && tile2lat(1574, 12) < 38.42);
  // Clamped at the poles and the antimeridian instead of returning NaN.
  assert.equal(lat2tile(90, 3), 0);
  assert.equal(lat2tile(-90, 3), 7);
  assert.equal(lon2tile(180, 3), 7);
});

test('basemap zoom keeps the extent within the tile budget', () => {
  const r = basemapTileRange(27.10, 38.40, 27.20, 38.46, 8);
  assert.ok(r.maxX - r.minX + 1 <= 8 && r.maxY - r.minY + 1 <= 8);
  // One zoom deeper would need more than 8 tiles on some axis.
  if (r.zoom < 19) {
    const nx = lon2tile(27.20, r.zoom + 1) - lon2tile(27.10, r.zoom + 1) + 1;
    const ny = lat2tile(38.40, r.zoom + 1) - lat2tile(38.46, r.zoom + 1) + 1;
    assert.ok(nx > 8 || ny > 8);
  }
});

const GRID = { bbox: [0, 0, 2, 1], cols: 3, rows: 2, heights: [10, 20, 30, 0, 10, 20] }; // north row first

test('terrain grid validation rejects partial grids', () => {
  assert.equal(isValidTerrainGrid(GRID), true);
  assert.equal(isValidTerrainGrid({ ...GRID, heights: [1, 2, 3] }), false);
  assert.equal(isValidTerrainGrid({ ...GRID, heights: [10, 20, NaN, 0, 10, 20] }), false);
  assert.equal(isValidTerrainGrid({ ...GRID, bbox: [0, 0, 2] }), false);
  assert.equal(isValidTerrainGrid(null), false);
});

test('terrain grid sampling is bilinear with north row first', () => {
  assert.equal(sampleTerrainGrid(GRID, 0, 1), 10); // north-west corner
  assert.equal(sampleTerrainGrid(GRID, 0, 0), 0); // south-west corner
  assert.equal(sampleTerrainGrid(GRID, 1, 0.5), 15); // centre
  assert.equal(sampleTerrainGrid(GRID, 1.5, 1), 25);
  assert.equal(sampleTerrainGrid(GRID, 5, 5), 30); // clamped to the edge
});
