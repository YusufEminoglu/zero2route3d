import { test } from 'node:test';
import assert from 'node:assert/strict';
import { niceTicks, wormPath } from '../../web/js/chart_math.js';

test('nice ticks cover the range in 1-2-5 steps', () => {
  assert.deepEqual(niceTicks(0, 10, 5), [0, 2, 4, 6, 8, 10]);
  assert.deepEqual(niceTicks(12, 87, 3), [0, 50, 100]);
  assert.deepEqual(niceTicks(0, 1.43, 5), [0, 0.5, 1, 1.5]);
  assert.deepEqual(niceTicks(5, 5, 4), [5]);
  assert.deepEqual(niceTicks(NaN, 3, 4), [0]);
});

test('worm path is a closed outline that ignores missing values', () => {
  const d = wormPath([1, 2, null, 4], 100, 20);
  assert.ok(d.startsWith('M 0.0,') && d.endsWith('Z'));
  assert.equal((d.match(/L /g) || []).length, 7); // 3 more top points + 4 bottom points
  assert.equal(wormPath([1], 100, 20), '');
  assert.equal(wormPath([null, NaN], 100, 20), '');
});
